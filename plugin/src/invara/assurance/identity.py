"""Local, unsigned identity of the Python implementation that executes assurance.

Every shipped Python module is trusted, including the CLI/MCP and kernel. The
manifest binds source bytes, while loaded origins and function code are checked
against that source. This detects deployment/import drift; it is not attestation
against a process owner able to replace this check or the Python runtime itself.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
from functools import lru_cache
import importlib
import importlib.metadata
import importlib.machinery
import json
from pathlib import Path
import subprocess
import sys
import types
from typing import Any

SCHEMA = "invara.verifier.identity/1"
_REQUIRED = frozenset("__init__ __main__ chain contract exact_json intent intent_cli intent_view mcp runner store verdict assurance.__init__ assurance.analysis assurance.claims assurance.cli assurance.compare assurance.coverage assurance.engine assurance.evidence assurance.execute assurance.governor assurance.http_boundary assurance.identity assurance.manifest assurance.normalize assurance.package assurance.paths assurance.proof assurance.records assurance.redaction assurance.report assurance.search assurance.sensitivity assurance.session assurance.workflow".split())
_ENTRYPOINTS = {"invara": "invara.__main__:main", "invara-mcp": "invara.mcp:main"}


class IdentityError(RuntimeError):
    reason = "verifier_identity_conflict"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _file(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@lru_cache(maxsize=8192)
def _code(code: types.CodeType) -> str:
    def constant(value: Any) -> Any:
        if isinstance(value, types.CodeType):
            return {"code": _code(value)}
        if isinstance(value, tuple):
            return [constant(v) for v in value]
        if isinstance(value, frozenset):
            return sorted((constant(v) for v in value), key=repr)
        return (type(value).__name__, repr(value))
    return _digest([code.co_code.hex(), code.co_exceptiontable.hex(), code.co_names,
                    code.co_varnames, code.co_freevars, code.co_cellvars, code.co_flags,
                    code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
                    [constant(c) for c in code.co_consts]])


@lru_cache(maxsize=256)
def _compiled_codes(source: bytes, filename: str) -> set[str]:
    codes: set[str] = set()
    def visit(code: types.CodeType) -> None:
        codes.add(_code(code))
        for value in code.co_consts:
            if isinstance(value, types.CodeType):
                visit(value)
    visit(compile(source, filename, "exec", dont_inherit=True, optimize=sys.flags.optimize))
    return codes


@lru_cache(maxsize=256)
def _declared_functions(source: bytes) -> tuple[str, ...]:
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            names.extend(node.name + "." + member.name for member in node.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return tuple(names)


def _functions(module: types.ModuleType):
    for value in vars(module).values():
        if isinstance(value, types.FunctionType):
            yield value
        elif isinstance(value, type) and value.__module__ == module.__name__:
            for member in vars(value).values():
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                if isinstance(member, property):
                    for func in (member.fget, member.fset, member.fdel):
                        if func is not None:
                            yield func
                elif isinstance(member, types.FunctionType):
                    yield member


def _git(root: Path) -> dict[str, Any] | None:
    # Only report an enclosing checkout which contains this implementation.
    try:
        def run(*args: str) -> str:
            result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=5)
            if result.returncode:
                raise ValueError("not a readable checkout")
            return result.stdout.strip()
        top = Path(run("rev-parse", "--show-toplevel")).resolve()
        root.relative_to(top)
        if not run("ls-files", "--", "."):
            return None
        return {"root": str(top), "commit": run("rev-parse", "HEAD"),
                "tree": run("rev-parse", "HEAD^{tree}"),
                "clean": not bool(run("status", "--porcelain", "--untracked-files=all"))}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _distribution(root: Path, version: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    owned, unrelated = [], []
    seen: set[str] = set()
    for dist in importlib.metadata.distributions(name="invara"):
        location = str(Path(dist.locate_file("")).resolve())
        metadata_path = str(getattr(dist, "_path", location))
        if metadata_path in seen:
            continue
        seen.add(metadata_path)
        direct_text = dist.read_text("direct_url.json")
        direct = json.loads(direct_text) if direct_text else None
        paths = [Path(dist.locate_file(f)).resolve() for f in (dist.files or []) if str(f).replace("\\", "/").endswith("invara/__init__.py")]
        belongs = root / "__init__.py" in paths
        if not belongs and direct and direct.get("dir_info", {}).get("editable"):
            from urllib.parse import unquote, urlparse
            from urllib.request import url2pathname
            parsed = urlparse(direct.get("url", ""))
            if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                checkout = Path(url2pathname(unquote(parsed.path))).resolve()
                belongs = root in (checkout / "src" / "invara", checkout / "invara")
        entries = {ep.name: ep.value for ep in dist.entry_points if ep.group == "console_scripts"}
        data = {"version": dist.version, "location": location, "metadata_path": metadata_path,
                "entry_points": entries, "direct_url": direct,
                "metadata_sha256": hashlib.sha256((dist.read_text("METADATA") or "").encode()).hexdigest()}
        if belongs:
            if dist.version != version:
                raise IdentityError("loaded source version conflicts with its distribution")
            if any(entries.get(k) != v for k, v in _ENTRYPOINTS.items()):
                raise IdentityError("distribution entrypoint conflicts with verifier entrypoint")
            owned.append(data)
        else:
            unrelated.append(data)
    if len(owned) > 1:
        raise IdentityError("multiple distributions claim the loaded implementation")
    return (owned[0] if owned else None), sorted(unrelated, key=lambda d: d["metadata_path"])


def _launcher() -> dict[str, Any]:
    argv0 = sys.argv[0] if sys.argv else ""
    wrapper = Path(argv0)
    has_wrapper = argv0 not in ("", "-", "-c") and wrapper.is_file()
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return {"python": _file(Path(sys.executable)), "module": getattr(spec, "name", None),
            "wrapper": _file(wrapper) if has_wrapper else None,
            "invocation": str(wrapper.resolve()) if has_wrapper else argv0}


def capture(*, include_git: bool = True) -> dict[str, Any]:
    """Capture actual loaded source and reject mixed, unsupported or stale code."""
    try:
        return _capture(include_git=include_git)
    except IdentityError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, ImportError) as error:
        raise IdentityError(f"cannot establish verifier identity: {error}") from None


def _capture(*, include_git: bool = True) -> dict[str, Any]:
    package = importlib.import_module("invara")
    root = Path(package.__file__).resolve().parent
    files = sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix())
    if not files or len(files) > 256:
        raise IdentityError("trusted module surface missing or too large")
    names = {p.relative_to(root).with_suffix("").as_posix().replace("/", ".") for p in files}
    if _REQUIRED != names:
        raise IdentityError("trusted verifier module surface is incomplete or contains undeclared modules")
    manifest, modules, code_by_file = [], {}, {}
    # Resolve each repeated function filename once per check, never across checks:
    # Windows path resolution otherwise makes a guard cost thousands of syscalls.
    resolved: dict[str, str] = {}
    def resolve(filename: str) -> str:
        if filename not in resolved:
            resolved[filename] = str(Path(filename).resolve())
        return resolved[filename]
    dataclass_filename = resolve(dataclasses.__file__)
    dataclass_codes = _compiled_codes(Path(dataclass_filename).read_bytes(), dataclass_filename)
    for path in files:
        full_path = resolve(str(path))
        if path.is_symlink() or not Path(full_path).is_relative_to(root):
            raise IdentityError("trusted module is a link or escapes the import root")
        relative = path.relative_to(root).as_posix()
        source = path.read_bytes()
        if len(source) > 4 * 1024 * 1024:
            raise IdentityError("trusted module exceeds identity size bound")
        manifest.append({"path": relative, "sha256": hashlib.sha256(source).hexdigest()})
        name = "invara." + relative[:-3].replace("/", ".")
        if name.endswith(".__init__"):
            name = name[:-9]
        module = importlib.import_module(name)
        if resolve(module.__file__) != full_path or resolve(module.__spec__.origin) != full_path:
            raise IdentityError(f"loaded module origin conflicts: {name}")
        if not isinstance(module.__loader__, importlib.machinery.SourceFileLoader):
            raise IdentityError(f"unsupported module loader: {name}")
        if hasattr(module, "__path__") and [str(Path(p).resolve()) for p in module.__path__] != [str(path.parent.resolve())]:
            raise IdentityError(f"mixed package import paths: {name}")
        modules[name] = full_path
        code_by_file[full_path] = _compiled_codes(source, full_path)
        for binding in _declared_functions(source):
            func = module
            for part in binding.split("."):
                func = vars(func).get(part)
            if isinstance(func, (staticmethod, classmethod)):
                func = func.__func__
            if isinstance(func, property):
                func = func.fget
            if hasattr(func, "__wrapped__"):
                func = func.__wrapped__
            if not isinstance(func, types.FunctionType) or func.__module__ != name or func.__qualname__ != binding or _code(func.__code__) not in code_by_file[full_path]:
                raise IdentityError(f"declared verifier function replaced: {name}.{binding}")
    runtime_functions = []
    for name, module in list(sys.modules.items()):
        if name == "invara" or name.startswith("invara."):
            if name not in modules or module is None:
                raise IdentityError(f"unmanifested loaded verifier module: {name}")
            for func in _functions(module):
                if not str(func.__module__).startswith("invara"):
                    continue
                filename = resolve(func.__code__.co_filename)
                runtime_functions.append([name, func.__qualname__, _code(func.__code__)])
                # dataclasses synthesize methods with no source file; their generator
                # belongs to Python, whose executable identity is recorded separately.
                if func.__code__.co_filename == "<string>" and func.__name__ in ("__init__", "__repr__", "__eq__", "__hash__", "__setattr__", "__delattr__"):
                    continue
                if filename == dataclass_filename and _code(func.__code__) in dataclass_codes:
                    continue
                if filename not in code_by_file or _code(func.__code__) not in code_by_file[filename]:
                    raise IdentityError(f"loaded function differs from verifier source: {name}.{func.__name__}")
    init = ast.parse((root / "__init__.py").read_bytes())
    versions = [ast.literal_eval(n.value) for n in init.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets)]
    if len(versions) != 1 or not isinstance(versions[0], str) or getattr(package, "__version__", None) != versions[0]:
        raise IdentityError("loaded/source version identity is missing or conflicting")
    distribution, unrelated = _distribution(root, versions[0])
    result = {"schema": SCHEMA, "source_version": versions[0], "root": str(root),
              "manifest": manifest, "implementation_sha256": _digest(manifest),
              "runtime_functions_sha256": _digest(sorted(runtime_functions)),
              "modules": modules, "distribution": distribution, "unrelated_distributions": unrelated,
              "launcher": _launcher(), "git": _git(root) if include_git else None}
    result["identity_sha256"] = _digest(result)
    return result


def validate(identity: Any) -> list[str]:
    """Validate an embedded claim without reading paths supplied by the package."""
    try:
        if not isinstance(identity, dict) or identity.get("schema") != SCHEMA:
            return ["producer verifier identity missing or unsupported"]
        body = {k: v for k, v in identity.items() if k != "identity_sha256"}
        if _digest(body) != identity.get("identity_sha256"):
            return ["producer verifier identity digest mismatch"]
        manifest = identity["manifest"]
        if not isinstance(manifest, list) or not 1 <= len(manifest) <= 256:
            return ["producer verifier manifest invalid"]
        paths = [row["path"] for row in manifest]
        if paths != sorted(set(paths)) or any(not isinstance(p, str) or p.startswith("/") or "\\" in p or ":" in p or ".." in p.split("/") or not p.endswith(".py") for p in paths):
            return ["producer verifier manifest paths invalid"]
        if not _REQUIRED <= {p[:-3].replace("/", ".") for p in paths}:
            return ["producer verifier manifest incomplete"]
        if any(set(row) != {"path", "sha256"} or len(row["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in row["sha256"]) for row in manifest):
            return ["producer verifier module digest invalid"]
        if _digest(manifest) != identity.get("implementation_sha256"):
            return ["producer verifier implementation digest mismatch"]
        if not isinstance(identity.get("source_version"), str) or not identity["source_version"] or not isinstance(identity.get("modules"), dict) or not isinstance(identity.get("root"), str):
            return ["producer verifier context incomplete"]
        root = identity["root"].replace("\\", "/").rstrip("/")
        if not root.startswith("/") and not (len(root) >= 3 and root[1:3] == ":/"):
            return ["producer verifier root is not absolute"]
        expected_modules = {}
        for path in paths:
            name = "invara." + path[:-3].replace("/", ".")
            if name.endswith(".__init__"):
                name = name[:-9]
            expected_modules[name] = root + "/" + path
        if {k: v.replace("\\", "/") for k, v in identity["modules"].items()} != expected_modules:
            return ["producer loaded module paths conflict with the manifest"]
        def valid_hash(value: Any) -> bool:
            return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
        if not valid_hash(identity.get("runtime_functions_sha256")):
            return ["producer runtime function digest invalid"]
        launcher = identity.get("launcher")
        if not isinstance(launcher, dict) or set(launcher) != {"python", "module", "wrapper", "invocation"}:
            return ["producer launcher identity malformed"]
        for name in ("python", "wrapper"):
            member = launcher[name]
            if member is None and name == "wrapper":
                continue
            if not isinstance(member, dict) or set(member) != {"path", "sha256"} or not isinstance(member["path"], str) or not valid_hash(member["sha256"]):
                return ["producer executable/wrapper identity malformed"]
        distribution = identity.get("distribution")
        if distribution is not None and (not isinstance(distribution, dict) or distribution.get("version") != identity["source_version"] or any(distribution.get("entry_points", {}).get(k) != v for k, v in _ENTRYPOINTS.items())):
            return ["producer distribution version/entrypoint conflicts with source"]
        return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["producer verifier identity malformed"]


def assert_current(expected: Any) -> dict[str, Any]:
    problems = validate(expected)
    if problems:
        raise IdentityError("; ".join(problems))
    current = capture(include_git=False)
    for key in ("source_version", "implementation_sha256", "runtime_functions_sha256", "root", "modules", "distribution", "launcher"):
        if current[key] != expected[key]:
            raise IdentityError(f"verifier identity changed after session creation: {key}")
    return current
