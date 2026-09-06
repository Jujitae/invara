"""Engineering analysis: what can be measured is measured, the rest is declared.

There is no universal code-quality score here and there will not be one.
What there is:

* **Measured, reproducibly.** File and line counts, a size distribution,
  duplicated line windows with their locations, and — for Python, through
  ``ast`` — local dependency edges, dependency cycles and the public
  surface of each module. Two measurements of the same tree agree byte for
  byte, and the tree digest says which tree was measured. Symlinks and
  junctions are never followed.
* **Declared, and labelled as such.** Findings are the host agent's
  reasoning about ownership, responsibilities, validation, error
  boundaries and the rest. INVARA records them with who declared them,
  validates their shape, and refuses a score field on principle.

A before/after delta of the measured part is how "the structure improved"
becomes a claim that can be checked rather than a sentence.
"""

from __future__ import annotations

import ast
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .execute import _is_link, matches_include
from .manifest import content_digest

__all__ = ["AnalysisError", "FINDING_KINDS", "Metrics", "delta", "measure", "validate_findings"]

FINDING_KINDS: tuple[str, ...] = (
    "duplicate_implementation",
    "dead_code",
    "circular_dependency",
    "ownership_ambiguity",
    "oversized_module",
    "dependency_direction",
    "mixed_responsibilities",
    "fragmented_state",
    "inconsistent_validation",
    "hidden_side_effect",
    "weak_error_boundary",
    "untested_critical_behavior",
)

EXCLUDED_DIRS = frozenset({".git", "__pycache__", ".invara", ".runtime", ".venv", "venv", "node_modules", ".pytest_cache", "build", "dist"})
SIZE_BUCKETS = (("<=50", 50), ("51-200", 200), ("201-500", 500), ("501-1000", 1000), (">1000", None))
_FINDING_FIELDS = ("id", "kind", "paths", "summary", "declared_by", "evidence", "unit_id")


class AnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class Metrics:
    files: int
    lines: int
    per_file: dict[str, int]
    size_distribution: dict[str, int]
    largest: list[dict[str, Any]]
    duplicate_blocks: dict[str, Any]
    python: dict[str, Any]
    unparsed: list[str]
    window: int
    tree_digest: str
    include: tuple[str, ...] = ("**/*.py",)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "lines": self.lines,
            "per_file": dict(self.per_file),
            "size_distribution": dict(self.size_distribution),
            "largest": [dict(item) for item in self.largest],
            "duplicate_blocks": dict(self.duplicate_blocks),
            "python": dict(self.python),
            "unparsed": list(self.unparsed),
            "window": self.window,
            "tree_digest": self.tree_digest,
            "include": list(self.include),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metrics":
        return cls(
            files=int(data["files"]),
            lines=int(data["lines"]),
            per_file=dict(data["per_file"]),
            size_distribution=dict(data["size_distribution"]),
            largest=[dict(item) for item in data["largest"]],
            duplicate_blocks=dict(data["duplicate_blocks"]),
            python=dict(data["python"]),
            unparsed=list(data["unparsed"]),
            window=int(data["window"]),
            tree_digest=data["tree_digest"],
            include=tuple(data.get("include", ["**/*.py"])),
        )


def _sources(root: Path, include: Sequence[str]) -> list[Path]:
    """Source files under ``root`` in sorted order, never through a link."""

    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS and not _is_link(os.path.join(directory, name)))
        for name in sorted(filenames):
            full = Path(directory) / name
            if _is_link(str(full)) or not full.is_file():
                continue
            relative = full.relative_to(root).as_posix()
            if matches_include(relative, list(include)):
                found.append(full)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def _module_name(relative: str) -> str:
    parts = relative[:-3].split("/") if relative.endswith(".py") else relative.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _normalized_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((number, " ".join(stripped.split())))
    return out


def _resolve_import(module: str, is_package: bool, node: ast.AST, local: set[str]) -> list[str]:
    targets: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            while name:
                if name in local:
                    targets.append(name)
                    break
                name = name.rpartition(".")[0]
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            package = module if is_package else module.rpartition(".")[0]
            for _ in range(node.level - 1):
                package = package.rpartition(".")[0]
            base = f"{package}.{node.module}" if node.module else package
        else:
            base = node.module or ""
        candidates = [base] + [f"{base}.{alias.name}" for alias in node.names]
        for candidate in candidates:
            name = candidate
            while name:
                if name in local and name != module:
                    targets.append(name)
                    break
                name = name.rpartition(".")[0]
    return targets


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components of size > 1 (or self-loops), Tarjan, sorted."""

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    out: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(edges.get(node, ())):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in edges.get(node, ()):
                out.append(sorted(component))

    for node in sorted(edges):
        if node not in indices:
            visit(node)
    return sorted(out)


def measure(root: str | Path, *, include: Sequence[str] = ("**/*.py",), window: int = 6) -> Metrics:
    """Measure one tree. Same tree, same numbers."""

    base = Path(root).resolve()
    per_file: dict[str, int] = {}
    hasher = hashlib.sha256()
    windows: dict[str, list[dict[str, Any]]] = {}
    modules: dict[str, tuple[str, ast.Module | None, bool]] = {}
    unparsed: list[str] = []
    for path in _sources(base, include):
        relative = path.relative_to(base).as_posix()
        data = path.read_bytes()
        hasher.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        per_file[relative] = len(lines)
        normalized = _normalized_lines(text)
        for start in range(0, max(0, len(normalized) - window + 1)):
            chunk = normalized[start : start + window]
            digest = hashlib.sha256("\n".join(line for _, line in chunk).encode("utf-8")).hexdigest()
            windows.setdefault(digest, []).append({"file": relative, "line": chunk[0][0]})
        if relative.endswith(".py"):
            module = _module_name(relative)
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                unparsed.append(relative)
                modules[module] = (relative, None, relative.endswith("__init__.py"))
            else:
                modules[module] = (relative, tree, relative.endswith("__init__.py"))

    duplicates = {digest: places for digest, places in windows.items() if len(places) > 1}
    ranked = sorted(duplicates.items(), key=lambda item: (-len(item[1]), item[0]))
    blocks = [{"digest": digest, "occurrences": places} for digest, places in ranked[:20]]

    local = set(modules)
    edges: dict[str, set[str]] = {name: set() for name in modules}
    public: dict[str, list[str]] = {}
    for module, (relative, tree, is_package) in modules.items():
        if tree is None:
            continue
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        public[module] = sorted(name for name in names if not name.startswith("_"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _resolve_import(module, is_package, node, local):
                    if target != module:
                        edges[module].add(target)

    distribution = {label: 0 for label, _ in SIZE_BUCKETS}
    for count in per_file.values():
        for label, limit in SIZE_BUCKETS:
            if limit is None or count <= limit:
                distribution[label] += 1
                break
    largest = [{"file": name, "lines": per_file[name]} for name in sorted(per_file, key=lambda f: (-per_file[f], f))[:5]]
    return Metrics(
        files=len(per_file),
        lines=sum(per_file.values()),
        per_file=per_file,
        size_distribution=distribution,
        largest=largest,
        duplicate_blocks={"window": window, "count": len(duplicates), "blocks": blocks},
        python={
            "modules": sorted(modules),
            "edges": sorted([source, target] for source, targets in edges.items() for target in targets),
            "cycles": _cycles(edges),
            "public_names": {module: public.get(module, []) for module in sorted(modules)},
        },
        unparsed=sorted(unparsed),
        window=window,
        tree_digest=hasher.hexdigest(),
        include=tuple(include),
    )


def _surface(metrics: Metrics) -> set[str]:
    return {f"{module}.{name}" for module, names in metrics.python.get("public_names", {}).items() for name in names}


def delta(before: Metrics, after: Metrics) -> dict[str, Any]:
    """What changed between two measurements, as numbers with both sides shown."""

    def pair(a: Any, b: Any) -> dict[str, Any]:
        return {"before": a, "after": b, "delta": b - a}

    surface_before, surface_after = _surface(before), _surface(after)
    return {
        "files": pair(before.files, after.files),
        "lines": pair(before.lines, after.lines),
        "duplicate_blocks": pair(before.duplicate_blocks["count"], after.duplicate_blocks["count"]),
        "cycles": pair(len(before.python.get("cycles", [])), len(after.python.get("cycles", []))),
        "dependency_edges": pair(len(before.python.get("edges", [])), len(after.python.get("edges", []))),
        "largest_module_lines": pair(before.largest[0]["lines"] if before.largest else 0, after.largest[0]["lines"] if after.largest else 0),
        "public_surface": {"added": sorted(surface_after - surface_before), "removed": sorted(surface_before - surface_after)},
        "size_distribution": {"before": dict(before.size_distribution), "after": dict(after.size_distribution)},
        "unparsed": {"before": list(before.unparsed), "after": list(after.unparsed)},
        "tree_digest": {"before": before.tree_digest, "after": after.tree_digest},
        "digest": content_digest({"before": before.tree_digest, "after": after.tree_digest}),
    }


def validate_findings(findings: Sequence[Any]) -> list[dict[str, Any]]:
    """Findings are declared, shaped, attributed — and never scored."""

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        where = f"findings[{index}]"
        if not isinstance(finding, dict):
            raise AnalysisError(f"{where} must be an object")
        unknown = sorted(set(finding) - set(_FINDING_FIELDS))
        if unknown:
            raise AnalysisError(f"{where}: unknown field(s) {', '.join(unknown)}; findings are recorded, not scored")
        for name in ("id", "kind", "paths", "summary", "declared_by"):
            if name not in finding:
                raise AnalysisError(f"{where} needs {name}")
        if not isinstance(finding["id"], str) or not finding["id"].strip():
            raise AnalysisError(f"{where}.id must be a non-empty string")
        if finding["id"] in seen:
            raise AnalysisError(f"{where}: duplicate finding id {finding['id']!r}")
        seen.add(finding["id"])
        if finding["kind"] not in FINDING_KINDS:
            raise AnalysisError(f"{where}.kind {finding['kind']!r} is not one of {', '.join(FINDING_KINDS)}")
        if not isinstance(finding["paths"], list) or not all(isinstance(p, str) for p in finding["paths"]):
            raise AnalysisError(f"{where}.paths must be a list of paths")
        if not isinstance(finding["summary"], str) or not finding["summary"].strip():
            raise AnalysisError(f"{where}.summary must say what was found")
        if not isinstance(finding["declared_by"], str) or not finding["declared_by"].strip():
            raise AnalysisError(f"{where}.declared_by must name who declared it")
        record = {name: finding[name] for name in _FINDING_FIELDS if name in finding}
        out.append(record)
    return out
