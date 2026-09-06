"""The blind-spot scan: for every observed value, would a change here have been noticed?

A manifest can make behaviour invisible: an exclusion, an ignore, a wide
tolerance, a placeholder. The verdict cannot see that, because the verdict
only ever compares the target the transformer produced. This scan compares
something else: deterministic, synthetic mutations of the frozen baseline
itself, one observed value at a time, under the manifest in force. A value
where no tested mutation produces a mandatory divergence is a *blind spot*:
a wrong transformation could change it and still pass. A value where some
mutations pass and others are caught is *dulled* (a tolerance is doing what
it was declared to do; the reader should know how much).

The scan measures the equivalence definition; it does not judge the
transformation and never changes a verdict. It uses the same ``compare``
the verifier uses, so it cannot disagree with it. It is deterministic,
bounded by ``budgets.sensitivity_max_leaves``, and has no model, network or
clock. Detection of a synthetic mutation is not detection of every real
regression; the report words it that way.

Pure: no I/O, no clock, no store.
"""

from __future__ import annotations

import copy
from typing import Any, Iterator, Mapping, Sequence

from . import paths
from .compare import compare
from .manifest import Manifest, Policy, content_digest
from .normalize import collapse_parsed_roots, expand_parsed_roots, identity_effect, is_tolerance_policy, parse_timestamp, timestamp_effect
from .records import OBSERVATION_VERSION

__all__ = ["MUTATIONS", "SENSITIVITY_VERSION", "scan"]

SENSITIVITY_VERSION = "invara.assurance.sensitivity/1"
MUTATIONS: tuple[str, ...] = ("boundary", "null", "empty", "order", "relationship", "same_shape", "tolerance_edge", "type_flip")
_HEX = "0123456789abcdef"


def _leaves(value: Any, path: str = "/") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, paths.child(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, paths.child(path, index))
    else:
        yield path, value


def _containers(value: Any, path: str = "/") -> Iterator[tuple[str, list[Any]]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _containers(item, paths.child(path, key))
    elif isinstance(value, list):
        yield path, value
        for index, item in enumerate(value):
            yield from _containers(item, paths.child(path, index))


def _get(tree: Any, path: str) -> Any:
    node = tree
    for segment in paths.split(path):
        node = node[int(segment)] if isinstance(node, list) else node[segment]
    return node


def _set(tree: Any, path: str, value: Any) -> None:
    segments = paths.split(path)
    node = tree
    for segment in segments[:-1]:
        node = node[int(segment)] if isinstance(node, list) else node[segment]
    last = segments[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _placeholder_at(manifest: Manifest, path: str, value: Any) -> Policy | None:
    """The accepted policy that replaces this value with a placeholder, if any: a different valid value would pass."""

    for policy in manifest.policies:
        if not policy.accepted or not any(paths.parse_selector(s).matches(path) for s in policy.selectors()):
            continue
        if policy.kind == "generated_id" and identity_effect(policy.params, value) == "whole":
            return policy
        if policy.kind == "stable_map":
            return policy
        if policy.kind == "timestamp" and policy.params.get("tolerance_s") is None and timestamp_effect(policy.params, value) == "whole":
            return policy
    return None


def _labelled_together(manifest: Manifest, policy: Policy) -> list[paths.Selector]:
    """The selectors of every policy that labels identifiers in the same group as ``policy``: what a consistent relabel must touch."""

    if policy.kind != "generated_id":
        return [paths.parse_selector(s) for s in policy.selectors()]
    group = policy.params.get("group") or policy.id
    return [
        paths.parse_selector(s)
        for other in manifest.policies
        if other.accepted and other.kind == "generated_id" and (other.params.get("group") or other.id) == group
        for s in other.selectors()
    ]


def _same_shape(policy: Policy, value: Any) -> tuple[bool, Any]:
    """Another value of the same declared shape: ``(True, mutant)``, or ``(False, None)`` when none can be made."""

    if policy.kind == "generated_id":
        pattern = policy.params.get("pattern", "any")
        if isinstance(pattern, dict):
            return False, None
        if isinstance(value, bool):
            return False, None
        if pattern == "uuid" and isinstance(value, str):
            return True, "".join(_HEX[(_HEX.index(ch) + 1) % 16] if ch in _HEX else ch for ch in value.lower())
        if pattern == "hex" and isinstance(value, str):
            return True, "".join(_HEX[(_HEX.index(ch) + 1) % 16] if ch in _HEX else ch for ch in value.lower())
        if pattern == "int" and isinstance(value, int):
            return True, value + 1
        if pattern == "int" and isinstance(value, str) and value.lstrip("-").isdigit():
            return True, str(int(value) + 1)
        if pattern == "any":
            if isinstance(value, str):
                return True, value[::-1] + "-other" if value else "other"
            if isinstance(value, (int, float)):
                return True, value + 12345
        return False, None
    if policy.kind == "stable_map":
        mapping = policy.params.get("map") or {}
        others = [key for key in mapping if key != value]
        return (True, others[0]) if others else (False, None)
    if policy.kind == "timestamp":
        epoch = bool(policy.params.get("epoch", False))
        instant = parse_timestamp(value, epoch=epoch)
        if instant is None:
            return False, None
        return True, (_iso_utc(instant + 86400.0) if isinstance(value, str) else value + 86400)
    return False, None


def _tolerance_at(manifest: Manifest, path: str) -> Policy | None:
    for policy in manifest.policies:
        if policy.accepted and is_tolerance_policy(policy) and any(paths.parse_selector(s).matches(path) for s in policy.selectors()):
            return policy
    return None


def _edge_value(policy: Policy, value: Any) -> Any:
    """A value just beyond the declared tolerance, so the edge itself is tested."""

    if policy.kind == "numeric_abs_tolerance" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value + 2 * float(policy.params["abs"])
    if policy.kind == "numeric_rel_tolerance" and isinstance(value, (int, float)) and not isinstance(value, bool):
        # the comparator accepts |a - b| / max(|a|, |b|) <= rel: the edge is v / (1 - rel), a hair beyond;
        # at rel >= 1 every number is accepted and no edge exists
        rel = float(policy.params["rel"])
        if rel >= 1.0:
            return None
        if value == 0:
            return 1.0
        return value / (1.0 - rel) * (1.0 + 1e-6)
    if policy.kind == "timestamp" and isinstance(value, (str, int, float)) and not isinstance(value, bool):
        epoch = bool(policy.params.get("epoch", False))
        instant = parse_timestamp(value, epoch=epoch)
        if instant is None:
            return None
        shift = 2 * float(policy.params["tolerance_s"]) + 1
        if isinstance(value, str):
            return _iso_utc(instant + shift)
        return value + shift
    return None


def _iso_utc(seconds: float) -> str:
    """An ISO 8601 UTC instant from epoch seconds, by calendar arithmetic (no clock, no datetime)."""

    whole = int(seconds // 1)
    days, rest = divmod(whole, 86400)
    hour, rest = divmod(rest, 3600)
    minute, second = divmod(rest, 60)
    # civil-from-days (Howard Hinnant), proleptic Gregorian
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    if month <= 2:
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"


def _mutants(manifest: Manifest, path: str, value: Any, repeated: bool, placeholder: Policy | None = None) -> list[tuple[str, Any]]:
    """Deterministic mutations of one scalar, in a fixed order. A ``same_shape`` entry with mutant ``_UNMAKEABLE`` could not be built."""

    out: list[tuple[str, Any]] = []
    if placeholder is not None:
        possible, mutant = _same_shape(placeholder, value)
        out.append(("same_shape", mutant if possible else _UNMAKEABLE))
    if isinstance(value, bool):
        out.append(("boundary", not value))
        out.append(("type_flip", str(value)))
    elif isinstance(value, (int, float)):
        out.append(("boundary", value + 1))
        out.append(("type_flip", str(value)))
    elif isinstance(value, str):
        out.append(("boundary", value + "!" if value else "!"))
        out.append(("empty", "" if value else "x"))
        out.append(("type_flip", 0))
    elif value is None:
        out.append(("type_flip", "x"))
    else:
        out.append(("type_flip", str(value)))
    if value is not None:
        out.append(("null", None))
    if repeated and isinstance(value, (str, int, float)) and not isinstance(value, bool):
        # one occurrence of a value that occurs elsewhere: the relationship, not the value, is what must be noticed
        out.append(("relationship", value + "?" if isinstance(value, str) else value + 7))
    tolerance = _tolerance_at(manifest, path)
    if tolerance is not None:
        edge = _edge_value(tolerance, value)
        out.append(("tolerance_edge", edge if edge is not None else _UNMAKEABLE))
    return out


_UNMAKEABLE = object()


def _classify(source: Mapping[str, Any], mutated_probes: Any, manifest: Manifest) -> str:
    target = dict(source)
    target["probes"] = mutated_probes
    target["system_id"] = manifest.target_system.id
    result = compare(source, target, manifest)
    if result.status != "compared":
        return "unverifiable"
    return "noticed" if not result.mandatory_equivalent else "missed"


def scan(manifest: Manifest, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Scan the baseline records (one per input) under ``manifest``. Never touches the records."""

    budget = int(manifest.budgets.sensitivity_max_leaves)
    policies = manifest.accepted_policies()
    # a text a canonical_json policy parses is mutated inside and serialised back before comparing
    expanded: dict[int, tuple[Any, list[str]]] = {}
    sites: list[tuple[int, str, Any]] = []
    container_sites: list[tuple[int, str, list[Any]]] = []
    for index, record in enumerate(records):
        if record.get("record_version") != OBSERVATION_VERSION or record.get("status") != "observed":
            continue
        expanded[index] = expand_parsed_roots(record["probes"], policies)
        for path, value in _leaves(expanded[index][0]):
            sites.append((index, path, value))
        for path, items in _containers(expanded[index][0]):
            container_sites.append((index, path, items))
    # one budget for every pass: the scalar sites first, then the containers the order pass reverses;
    # every comparison visits the whole tree of its record, so the work budget (leaf visits) caps the sites too
    leaves_total = len(sites)
    sites_total = len(sites) + len(container_sites)
    largest = max((len([1 for _ in _leaves(tree)]) for tree, _ in expanded.values()), default=1) or 1
    work_sites = max(1, int(manifest.budgets.sensitivity_max_work) // (len(MUTATIONS) * largest))
    budget_reason = "sensitivity_max_work" if work_sites < min(budget, sites_total) else ""
    budget = min(budget, work_sites)
    scanned = sites[:budget]
    scanned_containers = container_sites[: max(0, budget - len(scanned))]
    comparisons = 0

    by_mutation: dict[str, dict[str, int]] = {}
    blind: dict[str, dict[str, Any]] = {}
    dulled: dict[str, dict[str, Any]] = {}
    fail_closed: dict[str, dict[str, Any]] = {}
    placeholder_sites: dict[str, dict[str, Any]] = {}
    sensitive = 0

    def merge(bucket: dict[str, dict[str, Any]], entry: dict[str, Any]) -> None:
        # one entry per path across inputs; the inputs it was seen on are listed
        current = bucket.get(entry["path"])
        if current is None:
            bucket[entry["path"]] = dict(entry, inputs=[entry.pop("input")])
            return
        current["inputs"].append(entry["input"])
        for key in ("mutations", "noticed", "missed", "unverifiable"):
            current[key] = sorted(set(current[key]) | set(entry[key]))

    def note(name: str, outcome: str) -> None:
        entry = by_mutation.setdefault(name, {"tried": 0, "noticed": 0, "missed": 0, "unverifiable": 0})
        entry["tried"] += 1
        entry[outcome] += 1

    for index, path, value in scanned:
        record = records[index]
        tree, roots = expanded[index]
        same_paths = [other_path for other_path, other in _leaves(tree) if other == value and type(other) is type(value)]
        occurrences = len(same_paths)
        placeholder = _placeholder_at(manifest, path, value)
        inside_paths: list[str] = []
        if placeholder is not None:
            # a consistent relabel touches the occurrences the same declaration labels, not every equal value anywhere:
            # the whole identifiers, and the identifier inside the texts the same group labels
            together = _labelled_together(manifest, placeholder)
            same_paths = [other_path for other_path in same_paths if any(selector.matches(other_path) for selector in together)] or [path]
            if isinstance(value, str) and placeholder.kind == "generated_id":
                inside_paths = [
                    other_path
                    for other_path, other in _leaves(tree)
                    if isinstance(other, str) and other != value and value in other and any(selector.matches(other_path) for selector in together)
                ]
        outcomes: dict[str, str] = {}
        for name, mutant in _mutants(manifest, path, value, repeated=occurrences > 1, placeholder=placeholder):
            if mutant is _UNMAKEABLE:
                outcome = "unverifiable"
            else:
                probes = copy.deepcopy(tree)
                # a same-shape replacement is a consistent relabel: every occurrence changes together
                # (one occurrence alone is the relationship mutation, which must be noticed)
                for target_path in (same_paths if name == "same_shape" else [path]):
                    _set(probes, target_path, mutant)
                if name == "same_shape" and isinstance(mutant, str):
                    for target_path in inside_paths:
                        _set(probes, target_path, _get(probes, target_path).replace(value, mutant))
                outcome = _classify(record, collapse_parsed_roots(probes, roots), manifest)
                comparisons += 1
            outcomes[name] = outcome
            note(name, outcome)
        noticed = [name for name, outcome in outcomes.items() if outcome == "noticed"]
        missed = [name for name, outcome in outcomes.items() if outcome == "missed"]
        unverifiable = [name for name, outcome in outcomes.items() if outcome == "unverifiable"]
        entry = {"path": path, "input": record.get("input_id", ""), "mutations": list(outcomes), "noticed": noticed, "missed": missed, "unverifiable": unverifiable}
        if placeholder is not None and outcomes.get("same_shape") != "noticed":
            # the declaration replaces this value: another value of the same shape passes by design
            entry["policy"] = placeholder.id
            entry["kind"] = placeholder.kind
            merge(placeholder_sites, entry)
        elif "tolerance_edge" in unverifiable and "boundary" in missed:
            # no outside-envelope change could be built: the declared tolerance accepts every value of this kind
            entry["limitation"] = "the declared tolerance accepts every value of this kind; no change outside it could be built, so nothing here can be flagged"
            merge(blind, entry)
        elif noticed:
            sensitive += 1
            if missed:
                merge(dulled, entry)
        elif unverifiable and not missed:
            merge(fail_closed, entry)
        else:
            merge(blind, entry)

    containers: dict[str, dict[str, Any]] = {}
    for index, path, items in scanned_containers:
        record = records[index]
        tree, roots = expanded[index]
        if len(items) < 2 or all(item == items[0] for item in items):
            continue
        probes = copy.deepcopy(tree)
        _set(probes, path, list(reversed(items)))
        outcome = _classify(record, collapse_parsed_roots(probes, roots), manifest)
        comparisons += 1
        note("order", outcome)
        if outcome == "missed":
            containers.setdefault(path, {"path": path, "mutation": "order", "inputs": []})["inputs"].append(record.get("input_id", ""))

    # one verdict per path across inputs: blind anywhere is blind; then placeholder, dulled, fail closed; the rest is sensitive
    for stronger, weaker_buckets in ((blind, (placeholder_sites, dulled, fail_closed)), (placeholder_sites, (dulled, fail_closed)), (dulled, (fail_closed,))):
        for path in stronger:
            for bucket in weaker_buckets:
                bucket.pop(path, None)
    paths_scanned = len({path for _, path, _ in scanned})
    sensitive = paths_scanned - len(blind) - len(placeholder_sites) - len(dulled) - len(fail_closed)

    return {
        "record_version": SENSITIVITY_VERSION,
        "manifest_digest": manifest.digest(),
        # the baseline records this scan mutated, by content address: a scan of another baseline is not this one
        "record_digests": sorted(content_digest(record) for record in records),
        "inputs": len(records),
        "leaves_total": leaves_total,
        "leaves": len(scanned),
        "sites_total": sites_total,
        "sites": len(scanned) + len(scanned_containers),
        "paths_scanned": paths_scanned,
        "comparisons": comparisons,
        "budget": budget,
        "budget_reason": budget_reason if len(scanned) + len(scanned_containers) < sites_total else "",
        "truncated": len(scanned) + len(scanned_containers) < sites_total,
        "sensitive": sensitive,
        "blind": [blind[path] for path in sorted(blind)],
        "placeholder": [placeholder_sites[path] for path in sorted(placeholder_sites)],
        "dulled": [dulled[path] for path in sorted(dulled)],
        "fail_closed": [fail_closed[path] for path in sorted(fail_closed)],
        "order_insensitive": [containers[path] for path in sorted(containers)],
        "by_mutation": by_mutation,
        "note": "synthetic mutations of the frozen baseline under the manifest in force; a blind spot is a value no tested change would have flagged; a placeholder is a value the declaration replaces, where another value of the same shape passes by design; this measures the equivalence definition, not the transformation",
    }
