"""The assurance coverage map: one state per observed value, and what decided it.

``PASS`` names nothing by itself. This module derives, from the manifest,
the claim results, the baseline records and the volatility check, the
state of every observed leaf of the baseline, then rolls those states up
by probe, policy, exclusion, claim and repair unit. Nothing here decides a
verdict; the map describes what the verdict rested on, so a reader never
has to infer what was compared, what was declared out of scope, what a
policy removed, and what nobody could check.

States, from strongest to weakest evidence:

``PROVED``        compared for every member of an explicitly finite domain
``TESTED``        compared on every recorded corpus input
``SEARCHED``      compared only by a bounded search for counterexamples
``OBSERVED``      captured, but not held to equality (informational probe)
``EXCLUDED``      declared out of scope by an exclusion (differences are informational)
``UNOBSERVED``    removed or replaced by a policy before comparison, or never compared
``UNVERIFIABLE``  a comparison was attempted and could not be completed
``HUMAN_REVIEW``  varies between runs of the unchanged source and no policy covers it
``DIVERGED``      a mandatory divergence was recorded here

Pure: no I/O, no clock, no store.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Mapping, Sequence

from . import paths
from .claims import (
    COMPARING_KINDS,
    DIVERGED,
    HUMAN_REVIEW,
    NO_DIVERGENCE_FOUND,
    PRESERVED_WITHIN_ENVELOPE,
    PROVED_WITHIN_DECLARED_DOMAIN,
    UNVERIFIABLE,
    latest_per_claim,
)
from .manifest import Manifest
from .normalize import expand_parsed_roots, identity_effect, timestamp_effect, unobtained_observable

__all__ = ["STATES", "coverage_map", "summary_line"]

STATES: tuple[str, ...] = ("PROVED", "TESTED", "SEARCHED", "OBSERVED", "EXCLUDED", "UNOBSERVED", "UNVERIFIABLE", "HUMAN_REVIEW", "DIVERGED")
_STRENGTH = {"PROVED": 3, "TESTED": 2, "SEARCHED": 1}


def _all_paths(value: Any, path: str = "/") -> Iterator[tuple[str, Any, bool]]:
    """Every path in a JSON tree with its value and whether it is a scalar leaf."""

    if isinstance(value, dict):
        yield path, value, False
        for key, item in value.items():
            yield from _all_paths(item, paths.child(path, key))
    elif isinstance(value, list):
        yield path, value, False
        for index, item in enumerate(value):
            yield from _all_paths(item, paths.child(path, index))
    else:
        yield path, value, True


def _claim_state(kind: str, status: str) -> str:
    if status == PROVED_WITHIN_DECLARED_DOMAIN:
        return "PROVED"
    if status == PRESERVED_WITHIN_ENVELOPE:
        return "TESTED"
    if status == NO_DIVERGENCE_FOUND:
        return "SEARCHED" if kind == "counterexample_search" else "OBSERVED"
    if status == DIVERGED:
        return "DIVERGED"
    if status == UNVERIFIABLE:
        return "UNVERIFIABLE"
    if status == HUMAN_REVIEW:
        return "HUMAN_REVIEW"
    return "UNOBSERVED"


_MEMBER_ID = re.compile(r"f-\d+")
_MERGE_ORDER = ("UNVERIFIABLE", "UNOBSERVED", "HUMAN_REVIEW", "EXCLUDED", "OBSERVED", "SEARCHED", "TESTED", "PROVED")


def _covers(manifest: Manifest, result: Mapping[str, Any], input_id: str | None) -> bool:
    """Whether a claim result compared this baseline input: a corpus claim its corpus items, a proof its domain members."""

    if input_id is None:
        return True
    kind = result.get("kind")
    corpus_ids = {item.id for item in manifest.input_domain.corpus}
    if kind == "corpus_equivalence":
        return input_id in corpus_ids
    if kind == "finite_domain_proof":
        return manifest.input_domain.finite is not None and input_id not in corpus_ids and _MEMBER_ID.fullmatch(input_id) is not None
    return False


def _merge(current: str | None, new: str) -> str:
    """One state per path across inputs: a divergence anywhere wins; otherwise the weakest, never stronger than any input allows."""

    if current is None:
        return new
    if "DIVERGED" in (current, new):
        return "DIVERGED"
    return min((current, new), key=_MERGE_ORDER.index)


def _base_state(comparing: Sequence[Mapping[str, Any]]) -> str:
    """What a compared, non-diverging, non-excluded leaf can claim: the strongest completed comparison."""

    best = ""
    for result in comparing:
        status = result.get("status")
        if status == PROVED_WITHIN_DECLARED_DOMAIN:
            candidate = "PROVED"
        elif status in (PRESERVED_WITHIN_ENVELOPE, DIVERGED):
            # a diverged corpus still compared every other leaf and found it equal
            candidate = "TESTED" if result.get("kind") != "counterexample_search" else "SEARCHED"
        elif status == NO_DIVERGENCE_FOUND and result.get("kind") == "counterexample_search":
            candidate = "SEARCHED"
        else:
            continue
        if _STRENGTH[candidate] > _STRENGTH.get(best, 0):
            best = candidate
    if best:
        return best
    if any(result.get("status") == UNVERIFIABLE for result in comparing):
        return "UNVERIFIABLE"
    return "UNOBSERVED"


def coverage_map(
    manifest: Manifest,
    results: Sequence[Mapping[str, Any]],
    *,
    raw_records: Sequence[Mapping[str, Any]],
    uncovered_volatile: Sequence[str],
    manifest_digest: str | None = None,
    baseline_digest: str | None = None,
    units: Sequence[Mapping[str, Any]] = (),
    blind_spots: Sequence[str] = (),
) -> dict[str, Any]:
    """The map. ``raw_records`` are the baseline probe trees (one per input); results are claim results as dicts.

    ``blind_spots`` are paths the sensitivity scan found insensitive to every
    tested change; a comparison state there would overclaim, so such a leaf
    is shown as ``UNOBSERVED`` with the reason. A blind spot that was already
    ``EXCLUDED`` or ``UNOBSERVED`` by declaration is expected; one on a
    compared leaf is not, and is listed in ``undeclared_blind_spots``.

    A record may be ``(input_id, tree)``: then a claim counts for a leaf only
    on the inputs it compared (a corpus claim its corpus items, a proof its
    domain members), and one path seen on several inputs takes the weakest
    of its states. A bare tree counts as compared by every claim.
    ``causes`` says why a value is not compared: ``policy``, ``excluded``,
    ``informational``, ``volatile``, ``blind``, ``truncated`` (the divergence
    list was cut at the budget), ``unobtained`` (the probe's declared
    observable was never obtained on this input; its diagnostic fields are
    not coverage) or ``never``.
    """

    requirements = [claim.as_dict() for claim in manifest.claims]
    kinds = {str(req["id"]): str(req["kind"]) for req in requirements}
    latest = latest_per_claim(results, manifest_digest, baseline_digest, kinds)
    comparing = [result for result in latest.values() if result.get("kind") in COMPARING_KINDS]
    records: list[tuple[str | None, Mapping[str, Any]]] = [
        (str(item[0]), item[1]) if isinstance(item, tuple) else (None, item) for item in raw_records
    ]

    # a divergence is keyed to the raw value it came from; the normalized position is the fallback
    divergence_keys: list[tuple[str | None, str, str]] = []
    truncated: dict[str, int] = {}
    for result in comparing:
        if result.get("status") != DIVERGED:
            continue
        omitted = int((result.get("coverage") or {}).get("divergences_omitted", 0) or 0)
        if omitted:
            truncated[str(result.get("claim_id"))] = omitted
        for divergence in result.get("divergences", []) or []:
            if isinstance(divergence, Mapping) and divergence.get("mandatory", True) and divergence.get("path"):
                divergence_keys.append((divergence.get("input_id"), str(divergence.get("raw_path_source") or divergence["path"]), str(divergence["path"])))

    mandatory_probes = {probe.id for probe in manifest.probes if probe.mandatory}
    erasing: list[tuple[Any, str, str]] = []
    for policy in manifest.policies:
        if not policy.accepted:
            continue
        if policy.kind == "ignore":
            erasing.extend((paths.parse_selector(s), policy.id, "removed by policy") for s in policy.selectors())
        elif policy.kind == "redact":
            erasing.extend((paths.parse_selector(s), policy.id, "replaced by policy") for s in policy.selectors())
        elif policy.kind == "stable_map":
            erasing.extend((paths.parse_selector(s), policy.id, "mapped to a label by policy") for s in policy.selectors())
    # a timestamp policy without tolerance replaces a whole timestamp, but only the timestamps inside a text:
    # what it did to a leaf depends on the leaf, so it is decided per value below (the normalizer's own rule)
    stamping = [
        (paths.parse_selector(s), policy)
        for policy in manifest.policies
        if policy.accepted and policy.kind == "timestamp" and policy.params.get("tolerance_s") is None
        for s in policy.selectors()
    ]
    # a generated_id policy labels a whole identifier, but only the identifiers inside a text: decided per value, like timestamps
    labelling = [(paths.parse_selector(s), policy) for policy in manifest.policies if policy.accepted and policy.kind == "generated_id" for s in policy.selectors()]
    exclusions = [(paths.parse_selector(e.path), e) for e in manifest.exclusions]
    policy_selectors = {policy.id: [paths.parse_selector(s) for s in policy.selectors()] for policy in manifest.policies}
    parsing = [(paths.parse_selector(s), policy) for policy in manifest.policies if policy.accepted and policy.kind == "canonical_json" for s in policy.selectors()]
    volatile = set(uncovered_volatile)

    leaf_state: dict[str, str] = {}
    reasons: dict[str, str] = {}
    causes: dict[str, str] = {}
    partial: dict[str, list[str]] = {}
    for input_id, record in records:
        for probe_id, note in _partial_captures(record):
            label = f"{note} (input {input_id})" if input_id is not None else note
            if label not in partial.setdefault(probe_id, []):
                partial[probe_id].append(label)
    policy_paths: dict[str, set[str]] = {policy.id: set() for policy in manifest.policies}
    exclusion_paths: dict[str, set[str]] = {e.id: set() for e in manifest.exclusions}
    probe_paths: dict[str, set[str]] = {probe.id: set() for probe in manifest.probes}
    leaves_of_input: dict[str | None, set[str]] = {}

    def settle(path: str, state: str, reason: str, cause: str, input_id: str | None) -> None:
        # one state per path across inputs; the reason follows the state that won
        previous = leaf_state.get(path)
        merged = _merge(previous, state)
        if previous is None or merged != previous:
            leaf_state[path] = merged
            if previous is not None and merged == state and input_id is not None:
                reason = f"{reason} (input {input_id}; {previous} on another input)"
            reasons[path] = reason
            causes[path] = cause

    for input_id, record in records:
        covering = [result for result in comparing if _covers(manifest, result, input_id)]
        base = _base_state(covering)
        base_reason = ""
        if base == "UNOBSERVED":
            base_reason = (
                f"no claim compared this input ({input_id}) under the current manifest and baseline"
                if input_id is not None and not covering
                else "no comparing claim has been evaluated under the current manifest and baseline"
            )
        elif base == "UNVERIFIABLE":
            base_reason = "the comparison could not be completed"
        cut = {claim_id: n for claim_id, n in truncated.items() if any(str(r.get("claim_id")) == claim_id for r in covering)}
        # a text a canonical_json policy parses is a subtree here, as it is for the comparator
        expanded, parsed_roots = expand_parsed_roots(record, manifest.accepted_policies())
        # a mandatory probe whose declared observable this input never obtained: its diagnostic fields are not coverage
        item = next((item for item in manifest.input_domain.corpus if item.id == input_id), None)
        requests = item.input.get("requests") if item and isinstance(item.input, dict) else None
        unobtained = {probe.id: unobtained_observable(probe, record.get(probe.id), requests if probe.params.get("requests") == "$INPUT" else None) for probe in manifest.probes if probe.mandatory} if isinstance(record, Mapping) else {}
        for path, value, is_leaf in _all_paths(expanded):
            segments = paths.split(path)
            for policy_id, selectors in policy_selectors.items():
                if any(paths.covers(selector, path) for selector in selectors):
                    policy_paths[policy_id].add(path)
            if not is_leaf or not segments:
                continue
            probe_id = segments[0]
            probe_paths.setdefault(probe_id, set()).add(path)
            leaves_of_input.setdefault(input_id, set()).add(path)
            erased = next(((ident, how, kind) for selector, ident, how in erasing for kind in [_kind_of(manifest, ident)] if paths.covers(selector, path)), None)
            excluded = next((e for selector, e in exclusions if paths.covers(selector, path)), None)
            stamp = next((policy for selector, policy in stamping if paths.covers(selector, path)), None)
            stamp_note = timestamp_effect(stamp.params, value) if stamp is not None else None
            label = next((policy for selector, policy in labelling if paths.covers(selector, path)), None)
            label_note = identity_effect(label.params, value) if label is not None else None
            notes: list[str] = []
            if label_note == "inside":
                notes.append(f"policy {label.id} (generated_id) mapped the identifier(s) inside the text; the rest was compared")
            elif label_note == "none":
                notes.append(f"policy {label.id} (generated_id) did not apply (no match for its pattern)")
            if stamp_note in ("inside", "none"):
                notes.append(
                    f"policy {stamp.id} (timestamp) replaced the timestamp(s) inside the text; the rest was compared"
                    if stamp_note == "inside"
                    else f"policy {stamp.id} (timestamp) did not apply (not a timestamp)"
                )
            if isinstance(value, str) and path not in parsed_roots:
                parser = next((policy for selector, policy in parsing if selector.matches(path)), None)
                if parser is not None:
                    notes.append(f"policy {parser.id} (canonical_json) could not parse this text; compared as text")
            if unobtained.get(probe_id):
                state, cause = "UNVERIFIABLE", "unobtained"
                reason = f"the declared observable was not obtained on this input ({unobtained[probe_id]}); a diagnostic of the attempt is not compared as behaviour"
            elif erased is not None:
                ident, how, kind = erased
                reason = f"{how} {ident} ({kind})"
                if kind == "stable_map":
                    reason += "; a mapped value is compared as its label"
                state, cause = "UNOBSERVED", "policy"
            elif label_note == "whole":
                state, cause = "UNOBSERVED", "policy"
                reason = f"identity replaced by policy {label.id} (generated_id); a different valid value here passes, only its relationships to other occurrences are compared"
            elif stamp_note == "whole":
                state, cause, reason = "UNOBSERVED", "policy", f"replaced by policy {stamp.id} (timestamp)"
            elif excluded is not None:
                exclusion_paths[excluded.id].add(path)
                state, cause, reason = "EXCLUDED", "excluded", f"excluded by {excluded.id}: {excluded.reason}"
            elif probe_id not in mandatory_probes:
                state, cause, reason = "OBSERVED", "informational", f"probe {probe_id} is informational; captured, not held to equality"
            elif path in volatile:
                state, cause, reason = "HUMAN_REVIEW", "volatile", "varies between runs of the unchanged source and no accepted policy covers it"
            elif cut and base in _STRENGTH:
                # this input's comparison recorded only part of its divergences: whether this value differed is not on record
                claim_id, omitted = next(iter(cut.items()))
                state, cause = "UNOBSERVED", "truncated"
                reason = f"compared, but the divergence list of claim {claim_id} was cut at the budget ({omitted} omitted): whether this value differed is not recorded"
            else:
                state, cause, reason = base, ("compared" if base in _STRENGTH else "never"), base_reason
            if notes:
                reason = f"{reason}; {'; '.join(notes)}" if reason else "; ".join(notes)
            settle(path, state, reason, cause, input_id)

    # divergences, keyed to the raw value they came from (or the normalized path when the raw one is not a leaf here)
    for input_id, raw_path, normalized in divergence_keys:
        key = raw_path if raw_path in leaf_state else normalized if normalized in leaf_state else None
        if key is None:
            continue
        leaf_state[key] = "DIVERGED"
        reasons[key] = "a mandatory divergence was recorded here" + (f" (input {input_id})" if input_id else "")
        causes[key] = "diverged"

    blind = sorted(set(blind_spots) & set(leaf_state))
    undeclared: list[str] = []
    for path in blind:
        if leaf_state[path] in _STRENGTH:
            # nobody declared this value out of scope, yet no tested change was flagged: the alarming kind
            leaf_state[path] = "UNOBSERVED"
            causes[path] = "blind"
            undeclared.append(path)
            note = "a change here would not have been noticed (blind spot: no tested change was flagged)"
        else:
            note = "as declared, a change here would not have been noticed"
        reasons[path] = reasons.get(path, "").rstrip(".") + ("; " if reasons.get(path) else "") + note

    counts = {state: 0 for state in STATES}
    for state in leaf_state.values():
        counts[state] += 1

    probes: dict[str, Any] = {}
    for probe in manifest.probes:
        states = {path: leaf_state[path] for path in sorted(probe_paths.get(probe.id, ())) if path in leaf_state}
        per_state = {state: sum(1 for s in states.values() if s == state) for state in STATES}
        compared = [s for s in states.values() if s in _STRENGTH]
        if "DIVERGED" in states.values():
            state = "DIVERGED"
        elif compared:
            state = max(compared, key=lambda s: _STRENGTH[s])
        elif states:
            state = max(states.values(), key=lambda s: STATES.index(s))
        else:
            state = "UNOBSERVED"
        probes[probe.id] = {"adapter": probe.adapter, "mandatory": probe.mandatory, "state": state, "counts": per_state, "paths": states}

    claims_view = {
        str(req["id"]): {
            "kind": req["kind"],
            "mandatory": req.get("mandatory", True),
            "state": _claim_state(str(req["kind"]), str(latest[str(req["id"])].get("status"))) if str(req["id"]) in latest else "UNOBSERVED",
            "detail": str(latest[str(req["id"])].get("detail", "")) if str(req["id"]) in latest else "never evaluated under the current manifest and baseline",
        }
        for req in requirements
    }
    policies_view = {
        policy.id: {"kind": policy.kind, "accepted": policy.accepted, "origin": policy.origin, "paths": sorted(policy_paths[policy.id])}
        for policy in manifest.policies
    }
    exclusions_view = {e.id: {"path": e.path, "reason": e.reason, "paths": sorted(exclusion_paths[e.id])} for e in manifest.exclusions}
    units_view = {str(unit.get("id")): _unit_view(unit) for unit in units if unit.get("id")}
    return {
        "states": list(STATES),
        "paths": dict(sorted(leaf_state.items())),
        "reasons": dict(sorted(reasons.items())),
        "causes": dict(sorted(causes.items())),
        "counts": counts,
        "probes": probes,
        "policies": policies_view,
        "exclusions": exclusions_view,
        "claims": claims_view,
        "units": units_view,
        "inputs": len(records),
        "partial": {probe_id: notes for probe_id, notes in sorted(partial.items())},
        "blind_spots": blind,
        "undeclared_blind_spots": undeclared,
    }


def _partial_captures(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The capture markers a record carries (rows omitted, entries omitted, a truncated stream): what was not observed."""

    out: list[tuple[str, str]] = []
    for path, value, is_leaf in _all_paths(record):
        segments = paths.split(path)
        if not segments:
            continue
        probe_id, name = segments[0], segments[-1]
        if is_leaf and name in ("rows_omitted", "entries_omitted") and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out.append((probe_id, f"{path}: {value} {name.split('_')[0]} omitted by the capture limit"))
        elif is_leaf and name == "truncated" and value is True:
            out.append((probe_id, f"{path}: truncated at the capture limit"))
        elif is_leaf and name in ("bytes_limited", "text_limited") and value is True:
            out.append((probe_id, f"{path}: the capture byte budget cut this {'table' if name == 'bytes_limited' else 'file tree'}"))
        elif not is_leaf and name == "truncated" and isinstance(value, Mapping):
            for stream, flag in value.items():
                if flag is True:
                    out.append((probe_id, f"{path}/{stream}: truncated at the capture limit"))
    return out


def _kind_of(manifest: Manifest, policy_id: str) -> str:
    return next((policy.kind for policy in manifest.policies if policy.id == policy_id), "")


def _unit_view(unit: Mapping[str, Any]) -> dict[str, Any]:
    verdict = unit.get("verdict") or {}
    status = unit.get("status")
    if status == "accepted":
        state = "PROVED" if verdict.get("decided_by") == "proved" else "TESTED"
    elif status in ("rejected", "rolled_back"):
        state = "DIVERGED" if verdict.get("decided_by") == "diverged" else "UNOBSERVED"
    elif verdict.get("status") == "HUMAN_REVIEW":
        state = "HUMAN_REVIEW"
    elif verdict.get("status") == "UNVERIFIABLE":
        state = "UNVERIFIABLE"
    elif verdict:
        state = "DIVERGED" if verdict.get("decided_by") in ("diverged", "constraint_breaks", "integrity") else "TESTED"
    else:
        state = "UNOBSERVED"
    return {"status": status, "state": state, "verdict": verdict.get("status"), "decided_by": verdict.get("decided_by")}


_CAUSE_TEXT = {
    "excluded": ("선언으로 제외", "excluded by declaration"),
    "policy": ("정책으로 제거되거나 대체", "removed or replaced by policy"),
    "blind": ("비교했지만 변화를 눈치채지 못함(선언되지 않은 사각지대)", "compared but blind to change (undeclared)"),
    "truncated": ("차이 목록이 예산에서 잘려 기록되지 않음(omitted)", "not recorded after the divergence list was cut at the budget (omitted)"),
    "informational": ("참고용 프로브라 기록만 함", "captured only (informational probe)"),
    "never": ("비교되지 않음", "never compared"),
    "unverifiable": ("비교를 끝내지 못함", "could not be compared"),
    "volatile": ("실행마다 달라지는데 규칙이 없음", "vary between runs with no accepted policy"),
    "diverged": ("달라짐", "diverged"),
}


def _cause_counts(picture: Mapping[str, Any]) -> dict[str, int]:
    """How many observed values are not compared, by cause (the state decides where a value falls)."""

    out: dict[str, int] = {}
    causes = picture.get("causes", {})
    for path, state in picture["paths"].items():
        if state in _STRENGTH:
            continue
        cause = {"EXCLUDED": "excluded", "OBSERVED": "informational", "HUMAN_REVIEW": "volatile", "DIVERGED": "diverged", "UNVERIFIABLE": "unverifiable"}.get(state)
        if cause is None:
            cause = causes.get(path, "never")
            if cause == "compared":
                cause = "never"
        out[cause] = out.get(cause, 0) + 1
    return out


def summary_line(picture: Mapping[str, Any], language: str) -> str:
    """One sentence a non-coder can read: how much of what was observed was actually compared, and why the rest was not."""

    counts = picture["counts"]
    total = sum(counts.values())
    compared = counts["PROVED"] + counts["TESTED"] + counts["SEARCHED"]
    by_cause = _cause_counts(picture)
    parts_ko = [f"{_CAUSE_TEXT[cause][0]} {n}개" for cause, n in by_cause.items()]
    parts_en = [f"{n} {_CAUSE_TEXT[cause][1]}" for cause, n in by_cause.items()]
    partial = picture.get("partial") or {}
    tail_ko = f" 일부만 기록된 프로브: {', '.join(sorted(partial))} (기록 한도 밖의 값은 관찰되지 않았습니다)." if partial else ""
    tail_en = f" Captured partially: {', '.join(sorted(partial))} (values beyond the capture limit were not observed)." if partial else ""
    if language == "ko":
        if total == 0:
            return "관찰된 값이 없습니다." + tail_ko
        if compared < total:
            return f"관찰된 값 {total}개 중 {compared}개를 비교했습니다; 비교하지 않은 값: {', '.join(parts_ko)}." + tail_ko
        return f"관찰된 값 {total}개를 모두 비교했습니다 (증명 {counts['PROVED']}개, 입력 전체 확인 {counts['TESTED']}개, 탐색만 {counts['SEARCHED']}개)." + tail_ko
    if total == 0:
        return "No value was observed." + tail_en
    if compared < total:
        return f"{compared} of {total} observed values were compared; not compared: {', '.join(parts_en)}." + tail_en
    return f"All {total} observed values were compared ({counts['PROVED']} proved, {counts['TESTED']} tested on every input, {counts['SEARCHED']} searched only)." + tail_en


def unobserved_lines(picture: Mapping[str, Any], language: str) -> list[str]:
    """One line per value that was declared out, replaced, cut, blind or never compared, in the words of its cause."""

    out: list[str] = []
    causes = picture.get("causes", {})
    reasons = picture.get("reasons", {})
    for probe_id, notes in sorted((picture.get("partial") or {}).items()):
        for note in notes:
            out.append(
                f"프로브 {probe_id}는 일부만 기록되었습니다: {note}; 기록 한도 밖의 값은 관찰되지 않았습니다"
                if language == "ko"
                else f"probe {probe_id} was captured partially: {note}; values beyond the capture limit are not observed"
            )
    for path, state in picture["paths"].items():
        if state == "EXCLUDED":
            out.append(f"선언으로 비교에서 제외한 값: {path}" if language == "ko" else f"excluded by declaration, not compared: {path} ({reasons.get(path, '')})")
        elif state == "UNVERIFIABLE" and causes.get(path) == "unobtained":
            out.append(
                f"선언한 값을 얻지 못해 비교하지 않은 곳: {path}"
                if language == "ko"
                else f"the declared value was not obtained on this input and its diagnostic was not compared: {path} ({reasons.get(path, '')})"
            )
        elif state == "UNOBSERVED":
            cause = causes.get(path, "never")
            if cause == "policy":
                out.append(f"정책으로 제거되거나 대체되어 비교하지 않은 값: {path}" if language == "ko" else f"not compared after policy: {path} ({reasons.get(path, '')})")
            elif cause == "blind":
                out.append(
                    f"비교는 했지만 어떤 변화도 눈치채지 못했을 값 (선언되지 않은 사각지대): {path}"
                    if language == "ko"
                    else f"compared, but no tested change here was noticed and nothing declared it out of scope: {path}"
                )
            elif cause == "truncated":
                out.append(f"차이 목록이 잘려 달라졌는지 기록되지 않은 값: {path}" if language == "ko" else f"not recorded, the divergence list was cut: {path} ({reasons.get(path, '')})")
            else:
                out.append(f"비교되지 않은 값: {path}" if language == "ko" else f"never compared: {path} ({reasons.get(path, '')})")
    return out
