"""Differential comparison of two observation records.

The result is never a boolean. It is the pair of raw digests, the pair of
normalized digests, the digest of the policy set that produced them, a
count of what was actually compared, every tolerance that was applied, and
one :class:`Divergence` per place the normalized trees disagree — each
carrying the observation path, the raw and normalized values on both sides,
the policy that governed that path, why the values differ, and whether the
disagreement is mandatory or informational.

A record whose run did not observe (a timeout, a missing executable, an
adapter that produced something malformed) cannot be compared, and the
result says ``unverifiable`` rather than pretending either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import OBSERVATION_VERSION, paths
from .manifest import Manifest, Policy, content_digest
from .normalize import Normalized, normalize, parse_timestamp, policy_set_digest

__all__ = ["CompareError", "Comparison", "Divergence", "compare"]


class CompareError(ValueError):
    """The records cannot be compared at all; nothing is inferred from them."""


@dataclass(frozen=True)
class Divergence:
    path: str
    raw_path_source: str
    raw_path_target: str
    raw_source: Any
    raw_target: Any
    normalized_source: Any
    normalized_target: Any
    policy_id: str
    policy_kind: str
    why: str
    mandatory: bool
    excluded_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "raw_path_source": self.raw_path_source,
            "raw_path_target": self.raw_path_target,
            "raw_source": self.raw_source,
            "raw_target": self.raw_target,
            "normalized_source": self.normalized_source,
            "normalized_target": self.normalized_target,
            "policy_id": self.policy_id,
            "policy_kind": self.policy_kind,
            "why": self.why,
            "mandatory": self.mandatory,
            "excluded_by": self.excluded_by,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Divergence":
        return cls(**{name: data.get(name) for name in cls.__dataclass_fields__})  # type: ignore[arg-type]


@dataclass(frozen=True)
class Comparison:
    status: str
    equivalent: bool
    mandatory_equivalent: bool
    divergences: tuple[Divergence, ...]
    compared_leaves: int
    tolerance_applications: tuple[dict[str, Any], ...]
    problems: tuple[str, ...]
    source_raw_digest: str
    target_raw_digest: str
    source_normalized_digest: str
    target_normalized_digest: str
    policy_set_digest: str
    source_actions: tuple[dict[str, str], ...] = ()
    target_actions: tuple[dict[str, str], ...] = ()
    ambiguities: tuple[str, ...] = ()
    input_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "equivalent": self.equivalent,
            "mandatory_equivalent": self.mandatory_equivalent,
            "divergences": [d.as_dict() for d in self.divergences],
            "compared_leaves": self.compared_leaves,
            "tolerance_applications": [dict(t) for t in self.tolerance_applications],
            "problems": list(self.problems),
            "source_raw_digest": self.source_raw_digest,
            "target_raw_digest": self.target_raw_digest,
            "source_normalized_digest": self.source_normalized_digest,
            "target_normalized_digest": self.target_normalized_digest,
            "policy_set_digest": self.policy_set_digest,
            "source_actions": [dict(a) for a in self.source_actions],
            "target_actions": [dict(a) for a in self.target_actions],
            "ambiguities": list(self.ambiguities),
            "input_id": self.input_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Comparison":
        return cls(
            status=data["status"],
            equivalent=bool(data["equivalent"]),
            mandatory_equivalent=bool(data["mandatory_equivalent"]),
            divergences=tuple(Divergence.from_dict(d) for d in data.get("divergences", [])),
            compared_leaves=int(data.get("compared_leaves", 0)),
            tolerance_applications=tuple(dict(t) for t in data.get("tolerance_applications", [])),
            problems=tuple(data.get("problems", [])),
            source_raw_digest=data["source_raw_digest"],
            target_raw_digest=data["target_raw_digest"],
            source_normalized_digest=data["source_normalized_digest"],
            target_normalized_digest=data["target_normalized_digest"],
            policy_set_digest=data["policy_set_digest"],
            source_actions=tuple(dict(a) for a in data.get("source_actions", [])),
            target_actions=tuple(dict(a) for a in data.get("target_actions", [])),
            ambiguities=tuple(data.get("ambiguities", [])),
            input_id=data.get("input_id", ""),
            extra=dict(data.get("extra", {})),
        )

    def digest(self) -> str:
        return content_digest(self.as_dict())


# --------------------------------------------------------------------------


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _lookup(value: Any, path: str) -> Any:
    node = value
    for segment in paths.split(path):
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return None
    return node


def _child(path: str, key: Any) -> str:
    return paths.join(*paths.split(path), key)


class _Comparators:
    def __init__(self, policies: Sequence[Policy]) -> None:
        self.items: list[tuple[tuple[paths.Selector, ...], Policy]] = []
        for policy in policies:
            if not policy.accepted:
                continue
            if policy.kind in ("numeric_abs_tolerance", "numeric_rel_tolerance") or (
                policy.kind == "timestamp" and policy.params.get("tolerance_s") is not None
            ):
                self.items.append((tuple(paths.parse_selector(s) for s in policy.selectors()), policy))

    def at(self, path: str) -> Policy | None:
        for selectors, policy in self.items:
            if any(selector.matches(path) for selector in selectors):
                return policy
        return None


_EXACT = ("exact", "exact")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _diff(
    a: Any,
    b: Any,
    path: str,
    comparators: _Comparators,
    out: list[tuple[str, str, tuple[str, str]]],
    applied: list[dict[str, Any]],
    counter: list[int],
) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            child = _child(path, key)
            if key not in b:
                out.append((child, "missing in target", _EXACT))
            elif key not in a:
                out.append((child, "extra in target", _EXACT))
            else:
                _diff(a[key], b[key], child, comparators, out, applied, counter)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path, f"length differs ({len(a)} vs {len(b)})", _EXACT))
            return
        for index, (left, right) in enumerate(zip(a, b)):
            _diff(left, right, _child(path, index), comparators, out, applied, counter)
        return
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        out.append((path, f"type differs ({_typename(a)} vs {_typename(b)})", _EXACT))
        return
    counter[0] += 1
    policy = comparators.at(path)
    if policy is not None:
        ident = (policy.id, policy.kind)
        if policy.kind == "timestamp":
            left, right = parse_timestamp(a), parse_timestamp(b)
            if left is None or right is None:
                out.append((path, "not a timestamp on both sides", ident))
                return
            tolerance = float(policy.params["tolerance_s"])
            delta = abs(left - right)
            applied.append({"policy_id": policy.id, "path": path, "delta_s": delta, "tolerance_s": tolerance, "within": delta <= tolerance})
            if delta > tolerance:
                out.append((path, f"exceeds timestamp tolerance {tolerance}s (delta {delta}s)", ident))
            return
        if not (_is_number(a) and _is_number(b)):
            out.append((path, "not numeric on both sides, so the tolerance cannot apply", ident))
            return
        delta = abs(a - b)
        if policy.kind == "numeric_abs_tolerance":
            bound = float(policy.params["abs"])
            applied.append({"policy_id": policy.id, "path": path, "delta": delta, "abs": bound, "within": delta <= bound})
            if delta > bound:
                out.append((path, f"exceeds absolute tolerance {bound} (delta {delta})", ident))
            return
        bound = float(policy.params["rel"])
        scale = max(abs(a), abs(b))
        ratio = 0.0 if scale == 0 else delta / scale
        applied.append({"policy_id": policy.id, "path": path, "ratio": ratio, "rel": bound, "within": ratio <= bound})
        if ratio > bound:
            out.append((path, f"exceeds relative tolerance {bound} (ratio {ratio:.6g})", ident))
        return
    if _typename(a) != _typename(b):
        out.append((path, f"type differs ({_typename(a)} vs {_typename(b)})", _EXACT))
    elif _is_number(a) and _is_number(b):
        if a != b:
            out.append((path, f"values differ ({a!r} vs {b!r})", _EXACT))
    elif a != b:
        out.append((path, "values differ", _EXACT))


def _unverifiable(source: Mapping[str, Any], target: Mapping[str, Any], problems: list[str], digests: dict[str, str]) -> Comparison:
    return Comparison(
        status="unverifiable",
        equivalent=False,
        mandatory_equivalent=False,
        divergences=(),
        compared_leaves=0,
        tolerance_applications=(),
        problems=tuple(problems),
        source_raw_digest=digests["source"],
        target_raw_digest=digests["target"],
        source_normalized_digest="",
        target_normalized_digest="",
        policy_set_digest=digests["policies"],
        input_id=str(source.get("input_id") or target.get("input_id") or ""),
    )


def compare(source: Mapping[str, Any], target: Mapping[str, Any], manifest: Manifest) -> Comparison:
    """Normalize both raw records under the manifest's accepted policies and diff them."""

    for name, record in (("source", source), ("target", target)):
        if not isinstance(record, Mapping) or record.get("record_version") != OBSERVATION_VERSION:
            raise CompareError(f"{name} record is not a {OBSERVATION_VERSION} record")
        if "status" not in record or "probes" not in record:
            raise CompareError(f"{name} record is missing status or probes")
    policies = manifest.accepted_policies()
    digests = {
        "source": content_digest(dict(source)),
        "target": content_digest(dict(target)),
        "policies": policy_set_digest(policies),
    }
    problems = [
        f"{record.get('system_id', name)}: {record['status']}" + (": " + "; ".join(str(p) for p in record.get("problems", [])[:3]) if record.get("problems") else "")
        for name, record in (("source", source), ("target", target))
        if record["status"] != "observed"
    ]
    if problems:
        return _unverifiable(source, target, problems, digests)

    left: Normalized = normalize(source["probes"], policies, {"workspace": source.get("workspace")})
    right: Normalized = normalize(target["probes"], policies, {"workspace": target.get("workspace")})
    comparators = _Comparators(policies)
    found: list[tuple[str, str, tuple[str, str]]] = []
    applied: list[dict[str, Any]] = []
    counter = [0]
    _diff(left.value, right.value, "/", comparators, found, applied, counter)

    mandatory_ids = {probe.id for probe in manifest.probes if probe.mandatory}
    exclusions = [(paths.parse_selector(e.path), e.id) for e in manifest.exclusions]
    divergences: list[Divergence] = []
    for path, why, (policy_id, policy_kind) in found:
        segments = paths.split(path)
        excluded = next((ident for selector, ident in exclusions if selector.matches(path)), None)
        raw_source_path = left.raw_path(path)
        raw_target_path = right.raw_path(path)
        divergences.append(
            Divergence(
                path=path,
                raw_path_source=raw_source_path,
                raw_path_target=raw_target_path,
                raw_source=_lookup(source["probes"], raw_source_path),
                raw_target=_lookup(target["probes"], raw_target_path),
                normalized_source=_lookup(left.value, path),
                normalized_target=_lookup(right.value, path),
                policy_id=policy_id,
                policy_kind=policy_kind,
                why=why,
                mandatory=bool(segments) and segments[0] in mandatory_ids and excluded is None,
                excluded_by=excluded,
            )
        )
    return Comparison(
        status="compared",
        equivalent=not divergences,
        mandatory_equivalent=not any(d.mandatory for d in divergences),
        divergences=tuple(divergences),
        compared_leaves=counter[0],
        tolerance_applications=tuple(applied),
        problems=(),
        source_raw_digest=digests["source"],
        target_raw_digest=digests["target"],
        source_normalized_digest=content_digest(left.value),
        target_normalized_digest=content_digest(right.value),
        policy_set_digest=digests["policies"],
        source_actions=tuple(a.as_dict() for a in left.actions),
        target_actions=tuple(a.as_dict() for a in right.actions),
        ambiguities=tuple(left.ambiguities + right.ambiguities),
        input_id=str(source.get("input_id") or ""),
    )
