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

from .http_boundary import observation_problems

from decimal import Decimal
from ..exact_json import number_equal

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import paths
from .manifest import Manifest, Policy, content_digest
from .normalize import GAP_EMPTIED, Normalized, describe_gap, normalize, policy_set_digest, signal_gaps, tolerance_verdict, unobtained_observable
from .records import OBSERVATION_VERSION

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
    if isinstance(value, int):
        return "int"
    if isinstance(value, (float, Decimal)):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _lookup(value: Any, path: str) -> Any:
    found, node = paths.lookup(value, path)
    return node if found else None


_child = paths.child


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


from .normalize import _is_number  # noqa: E402 - one definition of "a number" for the whole package


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
        verdict, detail = tolerance_verdict(policy, a, b)
        if verdict == "inapplicable":
            # the tolerance has nothing to say about these values; they
            # decide for themselves, exactly
            if _typename(a) != _typename(b):
                out.append((path, f"type differs ({_typename(a)} vs {_typename(b)}); the tolerance does not apply", ident))
            elif a != b:
                out.append((path, "values differ, and the tolerance does not apply to them", ident))
            return
        if verdict == "not_finite":
            out.append((path, "not a finite number on both sides, so the tolerance cannot apply", ident))
            return
        applied.append({"policy_id": policy.id, "path": path, **detail, "within": verdict == "within"})
        if verdict == "beyond":
            if policy.kind == "timestamp":
                why = f"exceeds timestamp tolerance {detail['tolerance_s']}s (delta {detail['delta_s']}s)"
            elif policy.kind == "numeric_abs_tolerance":
                why = f"exceeds absolute tolerance {detail['abs']} (delta {detail['delta']})"
            else:
                why = f"exceeds relative tolerance {detail['rel']} (ratio {detail['ratio']:.6g})"
            out.append((path, why, ident))
        return
    if _typename(a) != _typename(b):
        out.append((path, f"type differs ({_typename(a)} vs {_typename(b)})", _EXACT))
    elif _is_number(a) and _is_number(b):
        if not number_equal(a, b):
            out.append((path, f"values differ ({a!r} vs {b!r})", _EXACT))
    elif a != b:
        out.append((path, "values differ", _EXACT))



def _unverifiable(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    problems: list[str],
    digests: dict[str, str],
    *,
    actions: tuple[Normalized | None, Normalized | None] = (None, None),
) -> Comparison:
    left, right = actions
    return Comparison(
        status="unverifiable",
        equivalent=False,
        mandatory_equivalent=False,
        divergences=(),
        compared_leaves=0,
        tolerance_applications=(),
        problems=tuple(problems),
        source_actions=tuple(a.as_dict() for a in left.actions) if left else (),
        target_actions=tuple(a.as_dict() for a in right.actions) if right else (),
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
    for record in (source, target):
        problems.extend(observation_problems(record, manifest, require_responses=False))
    if problems:
        return _unverifiable(source, target, problems, digests)
    # A probe whose declared observable was obtained on neither side (a JSON
    # probe that only recorded a parse failure) contributes nothing to this
    # input: the diagnostic of each failed attempt says that the attempt
    # happened, not what the probe declares, so it is left out of the
    # comparison rather than compared as behaviour. The input is compared
    # through what the other probes observed (exit status, streams, files,
    # tables); when no mandatory observation remains, nothing can be held
    # equal. One side alone is a divergence, found below like any other.
    unobtained: dict[str, str] = {}
    http_losses: set[str] = set()
    source_probes = dict(source["probes"])
    target_probes = dict(target["probes"])
    for probe in manifest.probes:
        left_reason = unobtained_observable(probe, source_probes.get(probe.id), source.get("http_request_declarations", {}).get(probe.id))
        right_reason = unobtained_observable(probe, target_probes.get(probe.id), target.get("http_request_declarations", {}).get(probe.id))
        if probe.adapter == "http":
            if probe.mandatory and left_reason and right_reason:
                return _unverifiable(source, target, [f"{probe.id}: mandatory HTTP observable not obtained on either side (source: {left_reason}; target: {right_reason})"], digests)
            if bool(left_reason) != bool(right_reason):
                http_losses.add(probe.id)
                # Preserve diagnostics in raw evidence, not in semantic leaves.
                (source_probes if left_reason else target_probes).pop(probe.id, None)
        if left_reason and right_reason:
            unobtained[probe.id] = f"source: {left_reason}; target: {right_reason}"
            source_probes.pop(probe.id, None)
            target_probes.pop(probe.id, None)
    mandatory = [probe for probe in manifest.probes if probe.mandatory and probe.id not in unobtained]
    if unobtained and not mandatory:
        return _unverifiable(
            source,
            target,
            [f"{probe_id}: mandatory probe: the declared observable was not obtained on either side ({why}); no other declared observation remains to compare" for probe_id, why in unobtained.items()],
            digests,
        )

    left: Normalized = normalize(source_probes, policies, {"workspace": source.get("workspace"), "root": source.get("root")})
    right: Normalized = normalize(target_probes, policies, {"workspace": target.get("workspace"), "root": target.get("root")})
    # A mandatory probe that the policy set has emptied on either side, or
    # that observed nothing on both sides, cannot be compared at all.
    # A probe emptied on one side only is a divergence (the other side
    # still says something the empty side does not); emptied on both there
    # is nothing left to hold either side to.
    left_empty = {probe for probe, gap in signal_gaps(left.value, mandatory, raw=source_probes) if gap == GAP_EMPTIED}
    right_empty = {probe for probe, gap in signal_gaps(right.value, mandatory, raw=target_probes) if gap == GAP_EMPTIED}
    signal_problems = [describe_gap(probe, GAP_EMPTIED) for probe in sorted(left_empty & right_empty)]
    if signal_problems:
        return _unverifiable(source, target, sorted(set(signal_problems)), digests, actions=(left, right))
    comparators = _Comparators(policies)
    found: list[tuple[str, str, tuple[str, str]]] = []
    applied: list[dict[str, Any]] = []
    counter = [0]
    _diff(left.value, right.value, "/", comparators, found, applied, counter)
    for probe_id in sorted(http_losses):
        path = paths.join(probe_id)
        if not any(entry[0] == path for entry in found):
            found.append((path, "HTTP observable was obtained on only one side", _EXACT))

    mandatory_ids = {probe.id for probe in manifest.probes if probe.mandatory}
    exclusions = [(paths.parse_selector(e.path), e.id) for e in manifest.exclusions]
    # every difference is judged (mandatory or not); only a bounded number is
    # recorded in full, mandatory ones first, and the rest are counted
    judged: list[tuple[str, str, tuple[str, str], str | None, bool]] = []
    for path, why, ident in found:
        segments = paths.split(path)
        excluded = next((exclusion_id for selector, exclusion_id in exclusions if paths.covers(selector, path)), None)
        if segments and segments[0] in http_losses:
            excluded = None  # a diff policy cannot manufacture a missing HTTP response
        mandatory = bool(segments) and segments[0] in mandatory_ids and excluded is None
        judged.append((path, why, ident, excluded, mandatory))
    limit = int(manifest.budgets.max_divergences)
    ordered = [entry for entry in judged if entry[4]] + [entry for entry in judged if not entry[4]]
    omitted = max(0, len(ordered) - limit)
    divergences: list[Divergence] = []
    for path, why, (policy_id, policy_kind), excluded, mandatory in ordered[:limit]:
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
                mandatory=mandatory,
                excluded_by=excluded,
            )
        )
    limit = int(manifest.budgets.max_divergences)
    extra: dict[str, Any] = {
        "divergences_omitted": omitted,
        "mandatory_divergences": sum(1 for entry in judged if entry[4]),
        # the normalizer notes one action per matching policy per leaf: bounded like the divergences, the rest counted
        "actions_omitted": {"source": max(0, len(left.actions) - limit), "target": max(0, len(right.actions) - limit)},
    }
    if unobtained:
        # on record: on this input these probes obtained no value on either side and were not compared
        extra["unobtained"] = dict(unobtained)
    return Comparison(
        status="compared",
        equivalent=not judged,
        mandatory_equivalent=not any(entry[4] for entry in judged),
        divergences=tuple(divergences),
        compared_leaves=counter[0],
        tolerance_applications=tuple(applied),
        problems=(),
        extra=extra,
        source_raw_digest=digests["source"],
        target_raw_digest=digests["target"],
        source_normalized_digest=content_digest(left.value),
        target_normalized_digest=content_digest(right.value),
        policy_set_digest=digests["policies"],
        source_actions=tuple(a.as_dict() for a in left.actions[:limit]),
        target_actions=tuple(a.as_dict() for a in right.actions[:limit]),
        ambiguities=tuple(left.ambiguities + right.ambiguities),
        input_id=str(source.get("input_id") or ""),
    )
