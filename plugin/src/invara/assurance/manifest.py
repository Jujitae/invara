"""The Equivalence Manifest — what "the same behaviour" means, written down.

Everything an assurance result depends on that is not an observation lives
here: the two systems, how inputs reach them, which probes observe them,
which policies relax exact comparison and why, which claims are mandatory,
the budgets and timeouts, what is explicitly out of scope, and what a
person must look at regardless.

Two properties carry the product:

* **Content-addressed.** The digest is the SHA-256 of the canonical JSON of
  the validated manifest, defaults filled in. Every claim result records the
  digest it was computed under, so a result cannot be quietly re-read under
  a different definition of equivalence.
* **Fail closed on validation.** An unknown field, an unknown policy kind, a
  blanket ignore, a tolerance with no bound — each is a refusal with a
  stable reason slug, the way :func:`invara.contract.seal` refuses. A policy
  that was mistyped must not silently become no policy.

Amendments are explicit records, never edits: they name who asked, why, the
old and the new digest, and which policies were added or changed, so a
later reader can ask whether a policy arrived after a divergence did.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..chain import canonical_json
from . import paths
from .redaction import looks_secret
from .http_boundary import validate_requests

__all__ = [
    "ADAPTERS",
    "AMENDABLE_SECTIONS",
    "AmendmentResult",
    "Budgets",
    "CLAIM_KINDS",
    "ClaimRequirement",
    "CorpusItem",
    "DEFAULT_BUDGETS",
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_ENV_ALLOW",
    "DEFAULT_TIMEOUTS",
    "DELIVERIES",
    "Exclusion",
    "FiniteDomain",
    "HumanReview",
    "InputDomain",
    "Manifest",
    "ManifestError",
    "POLICY_KINDS",
    "Performance",
    "Policy",
    "Probe",
    "SCHEMA_VERSION",
    "SYSTEM_KINDS",
    "System",
    "Timeouts",
    "amend",
    "content_digest",
    "exclusions_covering",
    "policies_covering",
    "ref_safe_id",
]

SCHEMA_VERSION = "invara.assurance.manifest/1"

SYSTEM_KINDS = ("process", "service")
DELIVERIES = ("stdin_json", "argv_json", "file_json", "http")
ADAPTERS = ("process", "json", "filesystem", "sqlite", "http")
POLICY_KINDS = (
    "exact",
    "canonical_json",
    "ordered_sequence",
    "unordered_set",
    "unordered_multiset",
    "numeric_abs_tolerance",
    "numeric_rel_tolerance",
    "float_edges",
    "timestamp",
    "generated_id",
    "path_canonical",
    "redact",
    "ignore",
    "line_endings",
    "stable_map",
)
CLAIM_KINDS = (
    "corpus_equivalence",
    "counterexample_search",
    "finite_domain_proof",
    "performance_envelope",
    "baseline_stability",
)
ORIGINS = ("declared", "inferred", "amendment")
#: Kinds that replace or remove values; a wildcard-only selector on one of
#: these would erase evidence wholesale.
ERASING_KINDS = ("generated_id", "redact", "timestamp", "stable_map")
#: Claim kinds that compare the target; a session needs one of them.
COMPARISON_CLAIM_KINDS = ("corpus_equivalence", "counterexample_search", "finite_domain_proof")
MAX_TIMESTAMP_TOLERANCE_S = 366 * 86400
ID_PATTERNS = ("uuid", "int", "hex", "any")

DEFAULT_BUDGETS: dict[str, int] = {
    "search_runs": 200,
    "search_seconds": 120,
    "finite_max_members": 10_000,
    "stability_runs": 2,
    "shrink_steps": 500,
    # work is estimated before it starts: executions a phase may plan, and
    # divergences one comparison (and one claim) may record
    "max_planned_runs": 2_000,
    "max_divergences": 200,
    # observed values the blind-spot scan mutates (per session, in path order)
    "sensitivity_max_leaves": 500,
    # leaf visits the scan may spend (sites x mutations x tree size): a large record shrinks the site budget
    "sensitivity_max_work": 2_000_000,
}
#: Budgets that authorise work; zero would authorise nothing and hide that.
_POSITIVE_BUDGETS = ("max_planned_runs", "max_divergences", "finite_max_members", "sensitivity_max_leaves", "sensitivity_max_work")
DEFAULT_TIMEOUTS: dict[str, float] = {"run_seconds": 60.0, "service_ready_seconds": 20.0}
DEFAULT_PERFORMANCE: dict[str, Any] = {
    "kind": "wall_clock",
    "rel_tolerance": 0.5,
    "abs_tolerance_s": 0.25,
    "runs": 1,
}
DEFAULT_CAPTURE_LIMIT = 1_048_576
#: What a child process may inherit unless the manifest says otherwise. The
#: minimum that lets an interpreter start on both platforms; secrets do not
#: travel by default because nothing not on this list does.
DEFAULT_ENV_ALLOW = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
)
AMENDABLE_SECTIONS = (
    "policies",
    "exclusions",
    "budgets",
    "timeouts",
    "performance",
    "human_review",
    "claims",
)

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def ref_safe_id(ident: str) -> bool:
    """A slug that is also a valid git ref component: session and unit ids name refs."""

    return bool(_SLUG.fullmatch(ident)) and ".." not in ident and "@{" not in ident and not ident.endswith(".lock")

_PROBE_FIELDS: dict[str, tuple[str, ...]] = {
    "process": ("capture",),
    "json": ("source", "path"),
    "filesystem": ("root", "include", "content", "max_text_bytes", "max_entries", "max_bytes"),
    "sqlite": ("path", "tables"),
    "http": ("requests", "headers"),
}
_PROBE_REQUIRED: dict[str, tuple[str, ...]] = {
    "process": (),
    "json": ("source",),
    "filesystem": ("root",),
    "sqlite": ("path", "tables"),
    "http": ("requests",),
}
_POLICY_PARAMS: dict[str, tuple[str, ...]] = {
    "exact": (),
    "canonical_json": (),
    "ordered_sequence": (),
    "unordered_set": (),
    "unordered_multiset": (),
    "numeric_abs_tolerance": ("abs",),
    "numeric_rel_tolerance": ("rel",),
    "float_edges": ("nan_equal", "negative_zero_equal"),
    "timestamp": ("tolerance_s", "epoch"),
    "generated_id": ("pattern", "group"),
    "path_canonical": ("roots",),
    "redact": ("replacement",),
    "ignore": (),
    "line_endings": ("to",),
    "stable_map": ("map", "in_text"),
}
_CLAIM_PARAMS: dict[str, tuple[str, ...]] = {
    "corpus_equivalence": (),
    "counterexample_search": ("runs", "seconds", "seed"),
    "finite_domain_proof": (),
    "performance_envelope": ("rel_tolerance", "abs_tolerance_s", "runs"),
    "baseline_stability": ("runs",),
}


class ManifestError(ValueError):
    """The manifest could not be accepted. ``reason`` is a stable slug."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def content_digest(value: Any) -> str:
    """The one digest rule for every JSON-shaped record in this package."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _reject_unknown(data: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ManifestError("unknown_field", f"{where}: {', '.join(unknown)}")


def _text(value: Any, reason: str, where: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(reason, f"{where} must be a string")
    return value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value: Any) -> bool:
    return _number(value) and value > 0


# --------------------------------------------------------------------------
# systems


@dataclass(frozen=True)
class System:
    """One executable thing to observe: a process per run, or a service."""

    id: str
    kind: str
    command: tuple[str, ...]
    root: str
    env_allow: tuple[str, ...] = DEFAULT_ENV_ALLOW
    env_set: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None
    capture_limit_bytes: int = DEFAULT_CAPTURE_LIMIT
    service: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "command": list(self.command),
            "root": self.root,
            "env": {"allow": list(self.env_allow), "set": dict(self.env_set)},
            "capture_limit_bytes": self.capture_limit_bytes,
        }
        if self.timeout_s is not None:
            out["timeout_s"] = self.timeout_s
        if self.service is not None:
            out["service"] = dict(self.service)
        return out

    @classmethod
    def from_dict(cls, data: Any, *, where: str, default_id: str, default_root: str) -> "System":
        if not isinstance(data, dict):
            raise ManifestError("bad_system", f"{where} must be an object")
        _reject_unknown(
            data,
            ("id", "kind", "command", "root", "env", "timeout_s", "capture_limit_bytes", "service"),
            where,
        )
        ident = data.get("id", default_id)
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_system", f"{where}.id must be a slug")
        kind = data.get("kind", "process")
        if kind not in SYSTEM_KINDS:
            raise ManifestError("bad_system_kind", f"{where}.kind {kind!r}")
        command = data.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ManifestError("bad_command", f"{where}.command must be a non-empty argv list, never a shell string")
        root = data.get("root", default_root)
        _text(root, "bad_system", f"{where}.root")
        env = data.get("env", {})
        if not isinstance(env, dict):
            raise ManifestError("bad_system", f"{where}.env must be an object")
        _reject_unknown(env, ("allow", "set"), f"{where}.env")
        allow = env.get("allow", list(DEFAULT_ENV_ALLOW))
        if not isinstance(allow, list) or not all(isinstance(name, str) and name for name in allow):
            raise ManifestError("bad_system", f"{where}.env.allow must be a list of names")
        env_set = env.get("set", {})
        if not isinstance(env_set, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env_set.items()
        ):
            raise ManifestError("bad_system", f"{where}.env.set must map names to strings")
        # The manifest is evidence and is stored verbatim, so a secret in it
        # would be a secret on disk. Put secrets in the environment and
        # allow-list their names instead.
        for name, value in env_set.items():
            if looks_secret(f"{name}={value}") or looks_secret(value):
                raise ManifestError("secret_in_manifest", f"{where}.env.set.{name} looks like a credential; the manifest is stored as evidence, so put the secret in the environment and allow-list its name")
        for part in command:
            if looks_secret(part):
                raise ManifestError("secret_in_manifest", f"{where}.command carries what looks like a credential; the manifest is stored as evidence")
        timeout_s = data.get("timeout_s")
        if timeout_s is not None and not _positive(timeout_s):
            raise ManifestError("bad_timeout", f"{where}.timeout_s must be positive")
        limit = data.get("capture_limit_bytes", DEFAULT_CAPTURE_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ManifestError("bad_system", f"{where}.capture_limit_bytes must be a positive integer")
        service = data.get("service")
        if kind == "service":
            ready = service.get("ready") if isinstance(service, dict) else None
            if not isinstance(ready, dict) or not (
                isinstance(ready.get("http"), str) or ready.get("tcp") is True
            ):
                raise ManifestError(
                    "service_without_ready",
                    f"{where}: a service must declare service.ready as {{'http': path}} or {{'tcp': true}}",
                )
            _reject_unknown(service, ("ready", "port_env"), f"{where}.service")
            if "http" in ready:
                try:
                    validate_requests([{"path": ready["http"]}])
                except ValueError as error:
                    raise ManifestError("bad_http_request", f"{where}.service.ready: {error}") from error
            service = {"ready": dict(ready), "port_env": service.get("port_env", "PORT")}
        elif service is not None:
            raise ManifestError("bad_system", f"{where}.service is only meaningful for kind 'service'")
        return cls(
            id=ident,
            kind=kind,
            command=tuple(command),
            root=root,
            env_allow=tuple(allow),
            env_set=dict(env_set),
            timeout_s=float(timeout_s) if timeout_s is not None else None,
            capture_limit_bytes=limit,
            service=service,
        )


# --------------------------------------------------------------------------
# inputs


@dataclass(frozen=True)
class CorpusItem:
    id: str
    input: Any
    initial_state: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "input": self.input}
        if self.initial_state:
            out["initial_state"] = dict(self.initial_state)
        return out

    def identity(self) -> str:
        """The digest of the exact input and initial state: what an execution of this item is an execution of.

        Two items with different ids and the same identity are the same
        execution; two items with the same id and different identities are
        not. Evidence is bound by identity, never by the display id.
        """

        return content_digest({"input": self.input, "initial_state": dict(self.initial_state)})

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "CorpusItem":
        if not isinstance(data, dict):
            raise ManifestError("bad_input", f"{where} must be an object")
        _reject_unknown(data, ("id", "input", "initial_state"), where)
        ident = data.get("id")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_input", f"{where}.id must be a slug")
        if "input" not in data:
            raise ManifestError("bad_input", f"{where} has no input")
        state = data.get("initial_state", {})
        if not isinstance(state, dict):
            raise ManifestError("bad_initial_state", f"{where}.initial_state must be an object")
        _reject_unknown(state, ("files", "sqlite"), f"{where}.initial_state")
        files = state.get("files", {})
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and (isinstance(content, str) or (isinstance(content, dict) and isinstance(content.get("base64"), str)))
            for path, content in files.items()
        ):
            raise ManifestError("bad_initial_state", f"{where}.initial_state.files must map paths to text or {{'base64': ...}}")
        sqlite = state.get("sqlite")
        if sqlite is not None and not (
            isinstance(sqlite, dict)
            and isinstance(sqlite.get("path"), str)
            and isinstance(sqlite.get("sql"), list)
            and all(isinstance(statement, str) for statement in sqlite["sql"])
        ):
            raise ManifestError("bad_initial_state", f"{where}.initial_state.sqlite must be {{'path': ..., 'sql': [...]}}")
        return cls(id=ident, input=data["input"], initial_state=dict(state))


def _product(columns: list[list[Any]]) -> Iterator[tuple[Any, ...]]:
    if not columns:
        yield ()
        return
    for head in columns[0]:
        for rest in _product(columns[1:]):
            yield (head, *rest)


@dataclass(frozen=True)
class FiniteDomain:
    """A cartesian product of declared parameter values. Enumerable, by construction."""

    parameters: dict[str, Any]

    @property
    def cardinality(self) -> int:
        """How many members the domain has: counted, never materialised."""

        total = 1
        for values in self.parameters.values():
            total *= len(self.values_of(values))  # a range knows its length without allocating
        return total

    @staticmethod
    def values_of(spec: Any) -> Sequence[Any]:
        if isinstance(spec, dict):
            low, high = spec["range"]
            return range(low, high + 1)
        return list(spec)

    def members(self) -> list["CorpusItem"]:
        """Every member, in declared parameter order — the enumeration a proof walks.

        Ids are positional (``f-0001``) so the same domain declaration always
        yields the same ids; the domain digest is what ties them to values.
        """

        names = list(self.parameters)
        columns = [self.values_of(self.parameters[name]) for name in names]
        width = max(4, len(str(self.cardinality)))
        out: list[CorpusItem] = []
        for index, combination in enumerate(_product(columns), start=1):
            out.append(CorpusItem(id=f"f-{index:0{width}d}", input=dict(zip(names, combination))))
        return out

    def member_index(self, ident: str) -> int:
        """The position (from 1) of the member ``ident`` names, or 0 when it names none: computed, never enumerated."""

        digits = ident[2:] if ident.startswith("f-") else ""
        if not digits.isascii() or not digits.isdigit():
            return 0
        index = int(digits)
        width = max(4, len(str(self.cardinality)))
        return index if 1 <= index <= self.cardinality and f"f-{index:0{width}d}" == ident else 0

    def digest(self) -> str:
        return content_digest(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {"parameters": {name: spec for name, spec in self.parameters.items()}}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "FiniteDomain":
        if not isinstance(data, dict):
            raise ManifestError("bad_finite_domain", f"{where} must be an object")
        _reject_unknown(data, ("parameters",), where)
        parameters = data.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            raise ManifestError("bad_finite_domain", f"{where}.parameters must name at least one parameter")
        for name, spec in parameters.items():
            if not isinstance(name, str) or not name:
                raise ManifestError("bad_finite_domain", f"{where}: parameter names must be strings")
            if isinstance(spec, dict):
                _reject_unknown(spec, ("range",), f"{where}.parameters.{name}")
                bounds = spec.get("range")
                if not (
                    isinstance(bounds, list)
                    and len(bounds) == 2
                    and all(isinstance(b, int) and not isinstance(b, bool) for b in bounds)
                    and bounds[0] <= bounds[1]
                ):
                    raise ManifestError("bad_finite_domain", f"{where}.parameters.{name}.range must be [low, high]")
            elif isinstance(spec, list):
                if not spec or not all(isinstance(v, (str, int, float, bool)) or v is None for v in spec):
                    raise ManifestError("bad_finite_domain", f"{where}.parameters.{name} must list scalar values")
                # a finite parameter names each value once: a repeated value would be two members with one identity,
                # a cardinality the domain does not have, and evidence the proof could not tell apart (the same rule
                # of identity the proof binds by: canonical JSON, so 1 and 1.0, true and 1 stay distinct)
                seen_values: set[str] = set()
                for value in spec:
                    key = content_digest(value)
                    if key in seen_values:
                        raise ManifestError("bad_finite_domain", f"{where}.parameters.{name} lists the value {value!r} more than once; a finite parameter names each value once")
                    seen_values.add(key)
            else:
                raise ManifestError("bad_finite_domain", f"{where}.parameters.{name} must be a list or a range")
        return cls(parameters={name: spec for name, spec in parameters.items()})


@dataclass(frozen=True)
class InputDomain:
    kind: str
    delivery: str
    corpus: tuple[CorpusItem, ...] = ()
    finite: FiniteDomain | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "delivery": self.delivery, "corpus": [item.as_dict() for item in self.corpus]}
        if self.finite is not None:
            out["finite"] = self.finite.as_dict()
        return out

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "InputDomain":
        if not isinstance(data, dict):
            raise ManifestError("bad_input_domain", f"{where} must be an object")
        _reject_unknown(data, ("kind", "delivery", "corpus", "finite"), where)
        kind = data.get("kind")
        if kind not in ("corpus", "finite"):
            raise ManifestError("bad_input_domain", f"{where}.kind must be 'corpus' or 'finite'")
        delivery = data.get("delivery")
        if delivery not in DELIVERIES:
            raise ManifestError("bad_delivery", f"{where}.delivery must be one of {', '.join(DELIVERIES)}")
        raw_corpus = data.get("corpus", [])
        if not isinstance(raw_corpus, list):
            raise ManifestError("bad_input_domain", f"{where}.corpus must be a list")
        corpus = tuple(CorpusItem.from_dict(item, where=f"{where}.corpus[{index}]") for index, item in enumerate(raw_corpus))
        seen: set[str] = set()
        for item in corpus:
            if item.id in seen:
                raise ManifestError("duplicate_input", item.id)
            seen.add(item.id)
            if delivery == "http":
                try:
                    validate_requests(item.input.get("requests") if isinstance(item.input, dict) else None)
                except ValueError as error:
                    raise ManifestError("bad_http_request", f"{where}: {error}") from error
        finite = None
        if kind == "finite":
            finite = FiniteDomain.from_dict(data.get("finite"), where=f"{where}.finite")
            # a corpus item may sit beside the members, never in a member's place: an id that names a member
            # would put a corpus execution under the member's key and the member's baseline would be that
            for index, item in enumerate(corpus):
                position = finite.member_index(item.id)
                if position:
                    raise ManifestError(
                        "duplicate_input",
                        f"{where}.corpus[{index}].id {item.id!r} is also the id of member {position} of the finite domain; a corpus item cannot stand in for a member",
                    )
        elif "finite" in data:
            raise ManifestError("bad_input_domain", f"{where}.finite is only meaningful for kind 'finite'")
        if kind == "corpus" and not corpus:
            raise ManifestError("empty_corpus", "a corpus domain needs at least one input")
        return cls(kind=kind, delivery=delivery, corpus=corpus, finite=finite)


# --------------------------------------------------------------------------
# probes, policies, claims


@dataclass(frozen=True)
class Probe:
    id: str
    adapter: str
    mandatory: bool
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "adapter": self.adapter, "mandatory": self.mandatory, **self.params}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Probe":
        if not isinstance(data, dict):
            raise ManifestError("bad_probe", f"{where} must be an object")
        ident = data.get("id")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_probe", f"{where}.id must be a slug")
        adapter = data.get("adapter")
        if adapter not in ADAPTERS:
            raise ManifestError("unknown_adapter", f"{where}.adapter {adapter!r}")
        _reject_unknown(data, ("id", "adapter", "mandatory") + _PROBE_FIELDS[adapter], where)
        mandatory = data.get("mandatory", True)
        if not isinstance(mandatory, bool):
            raise ManifestError("bad_probe", f"{where}.mandatory must be a boolean")
        for name in _PROBE_REQUIRED[adapter]:
            if name not in data:
                raise ManifestError("probe_missing_field", f"{where} ({adapter}) needs {name}")
        params = {name: data[name] for name in _PROBE_FIELDS[adapter] if name in data}
        cls._check_params(adapter, params, where)
        return cls(id=ident, adapter=adapter, mandatory=mandatory, params=params)

    @staticmethod
    def _check_params(adapter: str, params: dict[str, Any], where: str) -> None:
        if adapter == "json":
            if params["source"] not in ("stdout", "stderr", "file"):
                raise ManifestError("bad_probe", f"{where}.source must be stdout, stderr or file")
            if params["source"] == "file" and not isinstance(params.get("path"), str):
                raise ManifestError("probe_missing_field", f"{where} (json from file) needs path")
        elif adapter == "process":
            capture = params.get("capture", ["stdout", "stderr"])
            if not isinstance(capture, list) or not set(capture) <= {"stdout", "stderr"}:
                raise ManifestError("bad_probe", f"{where}.capture may name stdout and stderr only")
        elif adapter == "filesystem":
            if not isinstance(params["root"], str):
                raise ManifestError("bad_probe", f"{where}.root must be a path")
            include = params.get("include", ["**"])
            if not isinstance(include, list) or not all(isinstance(p, str) for p in include):
                raise ManifestError("bad_probe", f"{where}.include must be a list of globs")
            if params.get("content", "digest") not in ("digest", "text"):
                raise ManifestError("bad_probe", f"{where}.content must be digest or text")
            for name in ("max_text_bytes", "max_entries", "max_bytes"):
                if name in params and (not isinstance(params[name], int) or isinstance(params[name], bool) or params[name] <= 0):
                    raise ManifestError("bad_probe", f"{where}.{name} must be a positive integer")
        elif adapter == "sqlite":
            tables = params["tables"]
            if not isinstance(params["path"], str):
                raise ManifestError("bad_probe", f"{where}.path must be a path")
            if not isinstance(tables, list) or not tables:
                raise ManifestError("bad_probe", f"{where}.tables must list at least one table")
            for index, table in enumerate(tables):
                if not isinstance(table, dict) or not isinstance(table.get("name"), str):
                    raise ManifestError("bad_probe", f"{where}.tables[{index}] needs a name")
                _reject_unknown(table, ("name", "order", "key", "max_rows", "max_bytes"), f"{where}.tables[{index}]")
                for bound in ("max_rows", "max_bytes"):
                    if bound in table and (not isinstance(table[bound], int) or isinstance(table[bound], bool) or table[bound] <= 0):
                        raise ManifestError("bad_probe", f"{where}.tables[{index}].{bound} must be a positive integer")
                if table.get("order", "ordered") not in ("ordered", "unordered"):
                    raise ManifestError("bad_probe", f"{where}.tables[{index}].order must be ordered or unordered")
                key = table.get("key", [])
                if not isinstance(key, list) or not all(isinstance(k, str) for k in key):
                    raise ManifestError("bad_probe", f"{where}.tables[{index}].key must list columns")
        elif adapter == "http":
            requests = params["requests"]
            if requests != "$INPUT" and not isinstance(requests, list):
                raise ManifestError("bad_probe", f"{where}.requests must be '$INPUT' or a list")
            if isinstance(requests, list):
                try:
                    validate_requests(requests)
                except ValueError as error:
                    raise ManifestError("bad_http_request", f"{where}: {error}") from error
            headers = params.get("headers", ["content-type"])
            if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
                raise ManifestError("bad_probe", f"{where}.headers must be a list of header names")


@dataclass(frozen=True)
class Policy:
    """One declared relaxation of exact comparison, with its reason."""

    id: str
    kind: str
    path: str
    paths: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    origin: str = "declared"
    accepted: bool = True

    def selectors(self) -> tuple[str, ...]:
        return (self.path, *self.paths)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "reason": self.reason,
            "origin": self.origin,
            "accepted": self.accepted,
        }
        if self.paths:
            out["paths"] = list(self.paths)
        if self.params:
            out["params"] = dict(self.params)
        return out

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Policy":
        if not isinstance(data, dict):
            raise ManifestError("bad_policy", f"{where} must be an object")
        _reject_unknown(data, ("id", "kind", "path", "paths", "params", "reason", "origin", "accepted"), where)
        ident = data.get("id")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_policy", f"{where}.id must be a slug")
        kind = data.get("kind")
        if kind not in POLICY_KINDS:
            raise ManifestError("unknown_policy_kind", f"{where}.kind {kind!r}")
        selectors = [data.get("path")] + list(data.get("paths", []))
        for selector in selectors:
            if not isinstance(selector, str):
                raise ManifestError("bad_selector", f"{where}: selectors must be strings")
            try:
                paths.parse_selector(selector)
            except paths.PathError as error:
                raise ManifestError("bad_selector", f"{where}: {error}") from None
        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise ManifestError("bad_policy", f"{where}.reason must be a string")
        if kind != "exact" and not reason.strip():
            raise ManifestError("policy_without_reason", f"{where} ({kind}) must say why exact comparison is relaxed")
        origin = data.get("origin", "declared")
        if origin not in ORIGINS:
            raise ManifestError("bad_policy_origin", f"{where}.origin {origin!r}")
        accepted = data.get("accepted", origin != "inferred")
        if not isinstance(accepted, bool):
            raise ManifestError("bad_policy", f"{where}.accepted must be a boolean")
        if origin == "inferred" and accepted:
            raise ManifestError(
                "inferred_policy_accepted",
                f"{where}: an inferred policy becomes authoritative only through an explicit amendment",
            )
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise ManifestError("bad_policy_params", f"{where}.params must be an object")
        unknown = sorted(set(params) - set(_POLICY_PARAMS[kind]))
        if unknown:
            raise ManifestError("bad_policy_params", f"{where} ({kind}): unknown params {', '.join(unknown)}")
        cls._check_params(kind, params, where)
        if kind == "ignore":
            for selector in selectors:
                parsed = paths.parse_selector(selector)
                if parsed.is_root:
                    raise ManifestError("root_ignore", f"{where}: ignoring the whole observation leaves nothing to compare")
                if parsed.is_blanket:
                    raise ManifestError("blanket_ignore", f"{where}: an ignore must name at least one concrete segment ({selector})")
        elif kind in ERASING_KINDS:
            for selector in selectors:
                parsed = paths.parse_selector(selector)
                if parsed.is_root or parsed.is_blanket:
                    raise ManifestError("blanket_policy", f"{where}: a {kind} policy must name at least one concrete segment ({selector})")
        return cls(
            id=ident,
            kind=kind,
            path=selectors[0],
            paths=tuple(selectors[1:]),
            params=dict(params),
            reason=reason,
            origin=origin,
            accepted=accepted,
        )

    @staticmethod
    def _check_params(kind: str, params: dict[str, Any], where: str) -> None:
        if kind == "numeric_abs_tolerance":
            if not _positive(params.get("abs")):
                raise ManifestError("unbounded_tolerance", f"{where}: abs must be a finite positive number")
        elif kind == "numeric_rel_tolerance":
            rel = params.get("rel")
            if not _positive(rel):
                raise ManifestError("unbounded_tolerance", f"{where}: rel must be a finite positive number")
            if rel >= 1:
                raise ManifestError("overbroad_tolerance", f"{where}: a relative tolerance of {rel} accepts almost any value")
        elif kind == "timestamp":
            tolerance = params.get("tolerance_s")
            if tolerance is not None and not (_number(tolerance) and tolerance >= 0):
                raise ManifestError("unbounded_tolerance", f"{where}: tolerance_s must be a finite non-negative number")
            if tolerance is not None and tolerance > MAX_TIMESTAMP_TOLERANCE_S:
                raise ManifestError("overbroad_tolerance", f"{where}: a timestamp tolerance above one year is not clock skew")
            if "epoch" in params and not isinstance(params["epoch"], bool):
                raise ManifestError("bad_policy_params", f"{where}: epoch must be a boolean")
        elif kind == "generated_id":
            pattern = params.get("pattern", "any")
            if isinstance(pattern, dict):
                if set(pattern) != {"regex"} or not isinstance(pattern["regex"], str):
                    raise ManifestError("bad_policy_params", f"{where}: pattern must be one of {ID_PATTERNS} or {{'regex': ...}}")
                try:
                    re.compile(pattern["regex"])
                except re.error as error:
                    raise ManifestError("bad_policy_params", f"{where}: bad regex: {error}") from None
            elif pattern not in ID_PATTERNS:
                raise ManifestError("bad_policy_params", f"{where}: pattern must be one of {ID_PATTERNS} or {{'regex': ...}}")
            group = params.get("group")
            if group is not None and not isinstance(group, str):
                raise ManifestError("bad_policy_params", f"{where}: group must be a string")
        elif kind == "stable_map":
            mapping = params.get("map")
            if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                raise ManifestError("bad_policy_params", f"{where}: map must map strings to strings")
        elif kind == "path_canonical":
            roots = params.get("roots", [])
            if not isinstance(roots, list) or not all(
                isinstance(r, dict) and isinstance(r.get("token"), str) and isinstance(r.get("path"), str) for r in roots
            ):
                raise ManifestError("bad_policy_params", f"{where}: roots must list {{'token': ..., 'path': ...}}")
        elif kind == "redact":
            if "replacement" in params and not isinstance(params["replacement"], str):
                raise ManifestError("bad_policy_params", f"{where}: replacement must be a string")
        elif kind == "line_endings":
            if params.get("to", "lf") != "lf":
                raise ManifestError("bad_policy_params", f"{where}: only 'lf' is supported")
        elif kind == "float_edges":
            for name in ("nan_equal", "negative_zero_equal"):
                if name in params and not isinstance(params[name], bool):
                    raise ManifestError("bad_policy_params", f"{where}: {name} must be a boolean")


@dataclass(frozen=True)
class ClaimRequirement:
    id: str
    kind: str
    mandatory: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "mandatory": self.mandatory}
        if self.params:
            out["params"] = dict(self.params)
        return out

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "ClaimRequirement":
        if not isinstance(data, dict):
            raise ManifestError("bad_claim", f"{where} must be an object")
        _reject_unknown(data, ("id", "kind", "mandatory", "params"), where)
        ident = data.get("id")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_claim", f"{where}.id must be a slug")
        kind = data.get("kind")
        if kind not in CLAIM_KINDS:
            raise ManifestError("unknown_claim_kind", f"{where}.kind {kind!r}")
        mandatory = data.get("mandatory", True)
        if not isinstance(mandatory, bool):
            raise ManifestError("bad_claim", f"{where}.mandatory must be a boolean")
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise ManifestError("bad_claim_params", f"{where}.params must be an object")
        unknown = sorted(set(params) - set(_CLAIM_PARAMS[kind]))
        if unknown:
            raise ManifestError("bad_claim_params", f"{where} ({kind}): unknown params {', '.join(unknown)}")
        for name, value in params.items():
            if name in ("runs", "seconds", "seed") and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ManifestError("bad_claim_params", f"{where}.{name} must be a non-negative integer")
            if name in ("rel_tolerance", "abs_tolerance_s") and not (_number(value) and value >= 0):
                raise ManifestError("bad_claim_params", f"{where}.{name} must be a finite non-negative number")
        return cls(id=ident, kind=kind, mandatory=mandatory, params=dict(params))


# --------------------------------------------------------------------------
# budgets, timeouts, performance, exclusions, human review


@dataclass(frozen=True)
class Budgets:
    search_runs: int = DEFAULT_BUDGETS["search_runs"]
    search_seconds: int = DEFAULT_BUDGETS["search_seconds"]
    finite_max_members: int = DEFAULT_BUDGETS["finite_max_members"]
    stability_runs: int = DEFAULT_BUDGETS["stability_runs"]
    shrink_steps: int = DEFAULT_BUDGETS["shrink_steps"]
    max_planned_runs: int = DEFAULT_BUDGETS["max_planned_runs"]
    max_divergences: int = DEFAULT_BUDGETS["max_divergences"]
    sensitivity_max_leaves: int = DEFAULT_BUDGETS["sensitivity_max_leaves"]
    sensitivity_max_work: int = DEFAULT_BUDGETS["sensitivity_max_work"]

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in DEFAULT_BUDGETS}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Budgets":
        if not isinstance(data, dict):
            raise ManifestError("bad_budget", f"{where} must be an object")
        _reject_unknown(data, tuple(DEFAULT_BUDGETS), where)
        values = dict(DEFAULT_BUDGETS)
        for name, value in data.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ManifestError("bad_budget", f"{where}.{name} must be a non-negative integer")
            if name in _POSITIVE_BUDGETS and value == 0:
                raise ManifestError("bad_budget", f"{where}.{name} must be at least 1; a budget of zero authorises nothing")
            values[name] = value
        if values["finite_max_members"] < 1 or values["stability_runs"] < 1:
            raise ManifestError("bad_budget", f"{where}: finite_max_members and stability_runs must be at least 1")
        return cls(**values)


@dataclass(frozen=True)
class Timeouts:
    run_seconds: float = DEFAULT_TIMEOUTS["run_seconds"]
    service_ready_seconds: float = DEFAULT_TIMEOUTS["service_ready_seconds"]

    def as_dict(self) -> dict[str, Any]:
        return {"run_seconds": self.run_seconds, "service_ready_seconds": self.service_ready_seconds}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Timeouts":
        if not isinstance(data, dict):
            raise ManifestError("bad_timeout", f"{where} must be an object")
        _reject_unknown(data, tuple(DEFAULT_TIMEOUTS), where)
        values = dict(DEFAULT_TIMEOUTS)
        for name, value in data.items():
            if not _positive(value):
                raise ManifestError("bad_timeout", f"{where}.{name} must be a positive number")
            values[name] = float(value)
        return cls(**values)


@dataclass(frozen=True)
class Performance:
    kind: str = "wall_clock"
    rel_tolerance: float = DEFAULT_PERFORMANCE["rel_tolerance"]
    abs_tolerance_s: float = DEFAULT_PERFORMANCE["abs_tolerance_s"]
    runs: int = DEFAULT_PERFORMANCE["runs"]

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "rel_tolerance": self.rel_tolerance, "abs_tolerance_s": self.abs_tolerance_s, "runs": self.runs}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Performance":
        if not isinstance(data, dict):
            raise ManifestError("bad_performance", f"{where} must be an object")
        _reject_unknown(data, tuple(DEFAULT_PERFORMANCE), where)
        if data.get("kind", "wall_clock") != "wall_clock":
            raise ManifestError("bad_performance", f"{where}.kind: only wall_clock is measured")
        rel = data.get("rel_tolerance", DEFAULT_PERFORMANCE["rel_tolerance"])
        abs_s = data.get("abs_tolerance_s", DEFAULT_PERFORMANCE["abs_tolerance_s"])
        runs = data.get("runs", DEFAULT_PERFORMANCE["runs"])
        if not (_number(rel) and rel >= 0 and _number(abs_s) and abs_s >= 0):
            raise ManifestError("bad_performance", f"{where}: tolerances must be finite and non-negative")
        if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
            raise ManifestError("bad_performance", f"{where}.runs must be a positive integer")
        return cls(kind="wall_clock", rel_tolerance=float(rel), abs_tolerance_s=float(abs_s), runs=runs)


@dataclass(frozen=True)
class Exclusion:
    id: str
    path: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "path": self.path, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Exclusion":
        if not isinstance(data, dict):
            raise ManifestError("bad_exclusion", f"{where} must be an object")
        _reject_unknown(data, ("id", "path", "reason"), where)
        ident, path, reason = data.get("id"), data.get("path"), data.get("reason", "")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_exclusion", f"{where}.id must be a slug")
        if not isinstance(path, str):
            raise ManifestError("bad_exclusion", f"{where}.path must be a selector")
        try:
            parsed = paths.parse_selector(path)
        except paths.PathError as error:
            raise ManifestError("bad_selector", f"{where}: {error}") from None
        if parsed.is_root:
            raise ManifestError("root_exclusion", f"{where}: excluding the whole observation leaves nothing mandatory")
        if parsed.is_blanket:
            raise ManifestError("blanket_exclusion", f"{where}: an exclusion must name at least one concrete segment ({path})")
        if not isinstance(reason, str) or not reason.strip():
            raise ManifestError("bad_exclusion", f"{where}: an exclusion must say why the behaviour is out of scope")
        return cls(id=ident, path=path, reason=reason)


@dataclass(frozen=True)
class HumanReview:
    id: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "HumanReview":
        if not isinstance(data, dict):
            raise ManifestError("bad_human_review", f"{where} must be an object")
        _reject_unknown(data, ("id", "reason"), where)
        ident, reason = data.get("id"), data.get("reason", "")
        if not isinstance(ident, str) or not _SLUG.fullmatch(ident):
            raise ManifestError("bad_human_review", f"{where}.id must be a slug")
        if not isinstance(reason, str) or not reason.strip():
            raise ManifestError("bad_human_review", f"{where}: say what a person must look at")
        return cls(id=ident, reason=reason)


# --------------------------------------------------------------------------
# the manifest

_TOP_LEVEL = (
    "schema_version",
    "session_id",
    "title",
    "source_system",
    "target_system",
    "provenance",
    "input_domain",
    "probes",
    "policies",
    "claims",
    "budgets",
    "timeouts",
    "performance",
    "exclusions",
    "human_review",
)


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    session_id: str
    title: str
    source_system: System
    target_system: System
    provenance: dict[str, Any]
    input_domain: InputDomain
    probes: tuple[Probe, ...]
    policies: tuple[Policy, ...]
    claims: tuple[ClaimRequirement, ...]
    budgets: Budgets
    timeouts: Timeouts
    performance: Performance
    exclusions: tuple[Exclusion, ...]
    human_review: tuple[HumanReview, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "title": self.title,
            "source_system": self.source_system.as_dict(),
            "target_system": self.target_system.as_dict(),
            "provenance": dict(self.provenance),
            "input_domain": self.input_domain.as_dict(),
            "probes": [probe.as_dict() for probe in self.probes],
            "policies": [policy.as_dict() for policy in self.policies],
            "claims": [claim.as_dict() for claim in self.claims],
            "budgets": self.budgets.as_dict(),
            "timeouts": self.timeouts.as_dict(),
            "performance": self.performance.as_dict(),
            "exclusions": [exclusion.as_dict() for exclusion in self.exclusions],
            "human_review": [item.as_dict() for item in self.human_review],
        }

    def digest(self) -> str:
        return content_digest(self.as_dict())

    def probe(self, ident: str) -> Probe | None:
        for probe in self.probes:
            if probe.id == ident:
                return probe
        return None

    def mandatory_probes(self) -> tuple[Probe, ...]:
        return tuple(probe for probe in self.probes if probe.mandatory)

    def accepted_policies(self) -> tuple[Policy, ...]:
        return tuple(policy for policy in self.policies if policy.accepted)

    def claim(self, ident: str) -> ClaimRequirement | None:
        for claim in self.claims:
            if claim.id == ident:
                return claim
        return None

    @classmethod
    def from_dict(cls, data: Any) -> "Manifest":
        if not isinstance(data, dict):
            raise ManifestError("bad_manifest", "a manifest is a JSON object")
        _reject_unknown(data, _TOP_LEVEL, "manifest")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError("unsupported_schema", f"expected {SCHEMA_VERSION}, got {data.get('schema_version')!r}")
        session_id = data.get("session_id", "")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ManifestError("no_session_id")
        if not ref_safe_id(session_id):
            raise ManifestError("bad_session_id", f"{session_id!r} must match {_SLUG.pattern}, contain no '..', and not end in '.lock' (it names a git ref)")
        title = data.get("title", "")
        _text(title, "bad_manifest", "title")
        if "source_system" not in data:
            raise ManifestError("no_source_system")
        if "target_system" not in data:
            raise ManifestError("no_target_system")
        source = System.from_dict(data["source_system"], where="source_system", default_id="before", default_root="$SOURCE_ROOT")
        raw_target = data["target_system"]
        if isinstance(raw_target, dict) and raw_target.get("same_as_source") is True:
            _reject_unknown(raw_target, ("same_as_source", "id", "root"), "target_system")
            merged = source.as_dict()
            merged["id"] = raw_target.get("id", "after")
            merged["root"] = raw_target.get("root", "$TARGET_ROOT")
            target = System.from_dict(merged, where="target_system", default_id="after", default_root="$TARGET_ROOT")
        else:
            target = System.from_dict(raw_target, where="target_system", default_id="after", default_root="$TARGET_ROOT")
        if source.id == target.id:
            raise ManifestError("same_system_id", f"source and target systems share the id {source.id!r}; their observations would collide in the evidence store")
        provenance = data.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ManifestError("bad_provenance", "provenance must be an object")
        if "input_domain" not in data:
            raise ManifestError("bad_input_domain", "input_domain is required")
        domain = InputDomain.from_dict(data["input_domain"], where="input_domain")
        raw_probes = data.get("probes", [])
        if not isinstance(raw_probes, list) or not raw_probes:
            raise ManifestError("no_probes", "at least one probe must observe the systems")
        probes = tuple(Probe.from_dict(item, where=f"probes[{index}]") for index, item in enumerate(raw_probes))
        seen: set[str] = set()
        for probe in probes:
            if probe.id in seen:
                raise ManifestError("duplicate_probe", probe.id)
            seen.add(probe.id)
        if not any(probe.mandatory for probe in probes):
            raise ManifestError("no_mandatory_probe", "no probe is mandatory, so nothing could ever block")
        needs_service = domain.delivery == "http" or any(probe.adapter == "http" for probe in probes)
        if needs_service and (source.kind != "service" or target.kind != "service"):
            raise ManifestError("http_requires_service", "HTTP observation needs both systems declared as kind 'service'")
        raw_policies = data.get("policies", [])
        if not isinstance(raw_policies, list):
            raise ManifestError("bad_policy", "policies must be a list")
        policies = tuple(Policy.from_dict(item, where=f"policies[{index}]") for index, item in enumerate(raw_policies))
        seen = set()
        mandatory_ids = {probe.id for probe in probes if probe.mandatory}
        for policy in policies:
            if policy.id in seen:
                raise ManifestError("duplicate_policy", policy.id)
            seen.add(policy.id)
            if policy.kind == "ignore":
                for selector in policy.selectors():
                    segments = paths.parse_selector(selector).segments
                    if segments and segments[0] in mandatory_ids and (len(segments) == 1 or segments[1:] == ("**",)):
                        raise ManifestError("probe_ignore", f"{policy.id}: ignoring all of mandatory probe {segments[0]!r} erases its evidence")
        raw_claims = data.get("claims", [])
        if not isinstance(raw_claims, list):
            raise ManifestError("bad_claim", "claims must be a list")
        claims = tuple(ClaimRequirement.from_dict(item, where=f"claims[{index}]") for index, item in enumerate(raw_claims))
        seen = set()
        for claim in claims:
            if claim.id in seen:
                raise ManifestError("duplicate_claim", claim.id)
            seen.add(claim.id)
            if claim.kind == "finite_domain_proof" and domain.kind != "finite":
                raise ManifestError("proof_without_finite_domain", f"{claim.id}: a proof needs input_domain.kind 'finite'")
            if claim.kind == "corpus_equivalence" and not domain.corpus:
                raise ManifestError("claim_without_corpus", f"{claim.id}: corpus equivalence needs corpus inputs")
        if not any(claim.mandatory for claim in claims):
            raise ManifestError("no_mandatory_claim", "no mandatory claim, so nothing here could refuse the transformation")
        if not any(claim.mandatory and claim.kind in COMPARISON_CLAIM_KINDS for claim in claims):
            raise ManifestError("no_comparison_claim", f"no mandatory claim compares the target; one of {', '.join(COMPARISON_CLAIM_KINDS)} is required")
        kinds_seen: set[str] = set()
        for claim in claims:
            if claim.kind in kinds_seen:
                raise ManifestError("duplicate_claim_kind", f"{claim.id}: a second {claim.kind} claim; one per kind is evaluated")
            kinds_seen.add(claim.kind)
        budgets = Budgets.from_dict(data.get("budgets", {}), where="budgets")
        timeouts = Timeouts.from_dict(data.get("timeouts", {}), where="timeouts")
        performance = Performance.from_dict(data.get("performance", {}), where="performance")
        raw_exclusions = data.get("exclusions", [])
        if not isinstance(raw_exclusions, list):
            raise ManifestError("bad_exclusion", "exclusions must be a list")
        exclusions = tuple(Exclusion.from_dict(item, where=f"exclusions[{index}]") for index, item in enumerate(raw_exclusions))
        seen = set()
        for exclusion in exclusions:
            if exclusion.id in seen:
                raise ManifestError("bad_exclusion", f"duplicate exclusion {exclusion.id}")
            seen.add(exclusion.id)
            segments = paths.parse_selector(exclusion.path).segments
            if segments and segments[0] in mandatory_ids and (len(segments) == 1 or segments[1:] == ("**",)):
                raise ManifestError("probe_exclusion", f"{exclusion.id}: excluding all of mandatory probe {segments[0]!r} would make nothing mandatory")
        _check_index_selectors(policies, exclusions)
        raw_review = data.get("human_review", [])
        if not isinstance(raw_review, list):
            raise ManifestError("bad_human_review", "human_review must be a list")
        review = tuple(HumanReview.from_dict(item, where=f"human_review[{index}]") for index, item in enumerate(raw_review))
        return cls(
            schema_version=SCHEMA_VERSION,
            session_id=session_id,
            title=title,
            source_system=source,
            target_system=target,
            provenance=dict(provenance),
            input_domain=domain,
            probes=probes,
            policies=policies,
            claims=claims,
            budgets=budgets,
            timeouts=timeouts,
            performance=performance,
            exclusions=exclusions,
            human_review=review,
        )


def _check_index_selectors(policies: Sequence[Policy], exclusions: Sequence["Exclusion"] = ()) -> None:
    """A numeric index in a selector under an unordered list has no stable meaning.

    Applies to policies and exclusions alike: both would otherwise address
    whichever element the sort happened to put at that index.
    """

    unordered = [paths.parse_selector(s) for p in policies if p.kind in ("unordered_set", "unordered_multiset") for s in p.selectors()]
    if not unordered:
        return
    named: list[tuple[str, str]] = [(p.id, s) for p in policies if p.kind not in ("unordered_set", "unordered_multiset") for s in p.selectors()]
    named += [(e.id, e.path) for e in exclusions]
    for ident, selector in named:
        segments = paths.parse_selector(selector).segments
        for index, segment in enumerate(segments):
            if not segment.isdigit():
                continue
            prefix = segments[:index]
            if any(part in paths.WILDCARDS for part in prefix):
                continue
            if any(u.matches(paths.join(*prefix)) for u in unordered):
                raise ManifestError(
                    "unstable_index_selector",
                    f"{ident}: {selector} names element {segment} of a list that is compared unordered; use * instead",
                )


def _ancestors(path: str) -> list[str]:
    segments = paths.split(path)
    return [paths.join(*segments[:count]) for count in range(len(segments), -1, -1)]


# --------------------------------------------------------------------------
# amendments


@dataclass(frozen=True)
class AmendmentResult:
    manifest: Manifest
    record: dict[str, Any]


def amend(manifest: Manifest, amendment: Mapping[str, Any]) -> AmendmentResult:
    """Apply an explicit amendment. Sections are replaced, never merged.

    The result is a new manifest with a new digest and a record that says who
    asked, why, and exactly which policies arrived or changed. Whether any of
    them covers a path that already diverged is a question the caller asks
    with :func:`policies_covering`; this function only makes it answerable.
    """

    if not isinstance(amendment, Mapping):
        raise ManifestError("amendment_incomplete", "an amendment is an object")
    requested_by = amendment.get("requested_by")
    reason = amendment.get("reason")
    changes = amendment.get("changes")
    if not (isinstance(requested_by, str) and requested_by.strip()):
        raise ManifestError("amendment_incomplete", "requested_by must say who or what asked")
    if not (isinstance(reason, str) and reason.strip()):
        raise ManifestError("amendment_incomplete", "reason must say why")
    if not isinstance(changes, Mapping) or not changes:
        raise ManifestError("amendment_incomplete", "changes must name at least one section")
    for section in changes:
        if section not in AMENDABLE_SECTIONS:
            raise ManifestError("section_not_amendable", f"{section}: only {', '.join(AMENDABLE_SECTIONS)} may be amended; anything else is a new session")
    data = manifest.as_dict()
    for section, value in changes.items():
        data[section] = value
    amended = Manifest.from_dict(data)
    if amended.digest() == manifest.digest():
        raise ManifestError("amendment_no_change", "the amendment changes nothing")
    before = {policy.id: policy.as_dict() for policy in manifest.policies}
    after = {policy.id: policy.as_dict() for policy in amended.policies}
    exclusions_before = {e.id: e.as_dict() for e in manifest.exclusions}
    exclusions_after = {e.id: e.as_dict() for e in amended.exclusions}
    claims_before = {c.id: c for c in manifest.claims}
    claims_after = {c.id: c for c in amended.claims}
    review_before = {h.id for h in manifest.human_review}
    review_after = {h.id for h in amended.human_review}
    record = {
        "old_digest": manifest.digest(),
        "new_digest": amended.digest(),
        "requested_by": requested_by.strip(),
        "reason": reason.strip(),
        "changed_sections": sorted(changes),
        "added_policies": [ident for ident in after if ident not in before],
        "removed_policies": [ident for ident in before if ident not in after],
        "changed_policies": [ident for ident in after if ident in before and after[ident] != before[ident]],
        "added_exclusions": [ident for ident in exclusions_after if ident not in exclusions_before],
        "changed_exclusions": [ident for ident in exclusions_after if ident in exclusions_before and exclusions_after[ident] != exclusions_before[ident]],
        "removed_claims": [ident for ident, claim in claims_before.items() if claim.mandatory and ident not in claims_after],
        "demoted_claims": [ident for ident, claim in claims_before.items() if claim.mandatory and ident in claims_after and not claims_after[ident].mandatory],
        "removed_human_review": sorted(review_before - review_after),
    }
    return AmendmentResult(manifest=amended, record=record)


def policies_covering(manifest: Manifest, policy_ids: Sequence[str], observed_paths: Sequence[str]) -> list[str]:
    """Which of ``policy_ids`` match at least one of ``observed_paths`` or an ancestor of one.

    A policy on a parent path hides everything below it, so an ancestor
    match is a match.
    """

    wanted = set(policy_ids)
    candidates = [ancestor for path in observed_paths for ancestor in _ancestors(path)]
    covering: list[str] = []
    for policy in manifest.policies:
        if policy.id not in wanted:
            continue
        selectors = [paths.parse_selector(selector) for selector in policy.selectors()]
        if any(selector.matches(path) for selector in selectors for path in candidates):
            covering.append(policy.id)
    return covering


def exclusions_covering(manifest: Manifest, exclusion_ids: Sequence[str], observed_paths: Sequence[str]) -> list[str]:
    """Which of ``exclusion_ids`` match at least one of ``observed_paths`` or an ancestor of one."""

    wanted = set(exclusion_ids)
    candidates = [ancestor for path in observed_paths for ancestor in _ancestors(path)]
    return [e.id for e in manifest.exclusions if e.id in wanted and any(paths.parse_selector(e.path).matches(path) for path in candidates)]
