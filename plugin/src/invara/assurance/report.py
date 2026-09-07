"""The report, in two layers, plain language first.

The person who asked for the cleanup cannot read the code and should not
have to read a digest. So the first layer answers their six questions in
their words - 기능 유지, 성능 유지, 정리 완료 항목, 되돌린 변경, 확인하지
못한 영역, 다음에 사람이 볼 것 - in Korean and in English, before any
internal term appears. The section on what could not be verified is not
optional and never empty by omission: when nothing is missing it says so.

The second layer is the technical assurance report: manifests and digests,
the baseline envelope, the policies, coverage by kind of knowledge,
every divergence and counterexample, accepted and rejected units with
their measured deltas, provenance, amendments and the limitations of the
method. Both layers are one JSON document, content-addressed, redacted, and
renderable as Markdown.

This module is pure. It reads a session snapshot, a manifest and a verdict;
it does not look at the store, the tree or the clock.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contract import BLOCK, HUMAN_REVIEW, PASS, UNVERIFIABLE
from . import claims as c
from . import coverage as coverage_module
from .manifest import Manifest, content_digest
from .redaction import redact_value
from .session import Snapshot

__all__ = ["LIMITATIONS", "REPORT_VERSION", "build", "digest", "markdown"]

REPORT_VERSION = "invara.assurance.report/1"

#: What the method cannot claim, stated once and shipped with every report.
LIMITATIONS: tuple[str, ...] = (
    "Equivalence is decided only within the declared input domain and the declared probes; behaviour outside them is not observed.",
    "Only an explicitly finite domain is proved by exhaustion; corpus comparison and counterexample search are finite evidence, not proofs.",
    "Thread and process scheduling, wall-clock time and hardware randomness are not controlled; their effects are handled only through declared policies.",
    "External services are outside the verification boundary; any request to a non-loopback host makes the run unverifiable.",
    "Non-exact comparison policies relax equality where the manifest says so; each application is logged, and the policy set is part of every digest.",
    "Structural metrics are measured only for Python sources; findings for other languages are declared by the host agent and recorded as declarations.",
    "A repair unit is accepted from evidence about the verified tree; INVARA does not judge whether the engineering intent was met.",
)

_VERDICT_LABELS: dict[str, tuple[str, str]] = {
    PASS: ("선언한 범위 안에서 기능이 유지되었습니다", "Behaviour preserved within the declared envelope"),
    BLOCK: ("기능이 달라졌습니다. 이 변경은 반영할 수 없습니다", "Behaviour changed; the transformation is not accepted"),
    UNVERIFIABLE: ("확인하지 못했습니다. 아직 반영할 수 없습니다", "Could not be verified; the transformation is not accepted"),
    HUMAN_REVIEW: ("기계 확인은 통과했고, 사람의 확인이 남았습니다", "Machine checks hold; a person must review before acceptance"),
}
#: A PASS whose mandatory claims held while an informational claim found a difference: the headline says both.
_QUALIFIED_PASS: tuple[str, str] = (
    "선언한 필수 항목에서는 기능이 유지되었지만, 참고용 검사에서 차이가 발견되었습니다",
    "Behaviour preserved on every mandatory claim; an informational check found a difference",
)

_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("behavior", "기능 유지", "Behaviour preserved"),
    ("performance", "성능 유지", "Performance"),
    ("cleaned", "정리 완료 항목", "Cleanup completed"),
    ("reverted", "되돌린 변경", "Changes reverted"),
    ("unverified", "확인하지 못한 영역", "Not verified"),
    ("human", "다음에 사람이 볼 것", "For a person to look at next"),
)


def digest(data: Mapping[str, Any]) -> str:
    return content_digest(dict(data))


def _section(key: str, status: str, ko: Sequence[str], en: Sequence[str]) -> dict[str, Any]:
    title_ko, title_en = next((t_ko, t_en) for k, t_ko, t_en in _SECTIONS if k == key)
    return {"key": key, "title_ko": title_ko, "title_en": title_en, "status": status, "lines_ko": list(ko), "lines_en": list(en)}


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _divergence_phrase(divergence: Mapping[str, Any]) -> str:
    where = divergence.get("path", "?")
    input_id = divergence.get("input_id")
    before, after = divergence.get("raw_source"), divergence.get("raw_target")
    phrase = f"{where}"
    if input_id:
        phrase = f"input {input_id}: {phrase}"
    if before is not None or after is not None:
        phrase += f" ({_fmt(before)} -> {_fmt(after)})"
    return phrase


def search_input_counts(
    result: Mapping[str, Any], frozen: Mapping[str, Any], observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Report-only accounting for one claim; identities include input and initial state.

    Main candidate evaluations count once per source/target pair, including
    unverifiable attempts. Shrink evaluations are separate. Missing evidence
    is unknown (JSON null), never a guess from corpus size or display IDs.
    """
    coverage = result.get("coverage") or {}
    runs = coverage.get("runs")
    if type(runs) is not int or runs < 0:
        runs = None
    counts = dict(search_executions=runs, distinct_search_inputs=None,
                  baseline_overlap=None, new_distinct_inputs=None, repeated_executions=None,
                  identity="exact input and initial_state; display IDs excluded",
                  scope="main search evaluations of the source/target pair; includes unverifiable attempts; excludes shrink evaluations",
                  unknown_reason=None)
    rows = {row["digest"]: row for row in observations
            if row.get("digest") and content_digest(row.get("record")) == row["digest"]}

    def raw_identity(address: Any) -> str | None:
        row = rows.get(address, {})
        value = (row.get("record") or {}).get("input_digest")
        if row.get("kind") == "raw" and isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
            return value
        return None

    identities = []
    main_ids = []
    for address in result.get("evidence_digests", ()):
        row = rows.get(address, {})
        record = row.get("record") or {}
        input_id = record.get("input_id", "")
        if row.get("kind") != "comparison":
            counts["unknown_reason"] = "search comparison evidence is incomplete"
            return counts
        if input_id.startswith("shrink-"):
            continue
        left, right = raw_identity(record.get("source_raw_digest")), raw_identity(record.get("target_raw_digest"))
        if left is None or left != right:
            counts["unknown_reason"] = "search input identity evidence is incomplete or inconsistent"
            return counts
        identities.append(left)
        main_ids.append(input_id)
    if runs is None or main_ids != [f"search-{i}" for i in range(1, runs + 1)]:
        counts["unknown_reason"] = "main search execution count does not reconcile with preserved comparisons"
        return counts
    distinct = set(identities)
    counts["distinct_search_inputs"] = len(distinct)
    counts["repeated_executions"] = runs - len(distinct)
    addresses = frozen.get("record_digests")
    if not result.get("baseline_digest") or result.get("baseline_digest") != frozen.get("baseline_digest") or not isinstance(addresses, dict):
        counts["unknown_reason"] = "the claim's frozen baseline identity evidence is unavailable"
        return counts
    inputs = frozen.get("inputs", list(addresses))
    if (not addresses or not isinstance(frozen.get("manifest_digest"), str)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in addresses.items())
            or not isinstance(inputs, list) or not all(isinstance(value, str) for value in inputs)
            or sorted(inputs) != sorted(addresses)
            # The existing frozen baseline binding (engine.baseline_digest_of).
            or content_digest({"manifest": frozen["manifest_digest"], "records": dict(sorted(addresses.items()))}) != frozen["baseline_digest"]
            or any((rows.get(address, {}).get("record") or {}).get("input_id") != input_id for input_id, address in addresses.items())):
        counts["unknown_reason"] = "frozen baseline reference set is incomplete or inconsistent"
        return counts
    baseline = [raw_identity(address) for address in addresses.values()]
    if any(value is None for value in baseline):
        counts["unknown_reason"] = "frozen baseline input identity evidence is incomplete"
        return counts
    counts["baseline_overlap"] = len(distinct & set(baseline))
    counts["new_distinct_inputs"] = len(distinct - set(baseline))
    return counts


def counted_results(results: Sequence[Mapping[str, Any]], frozen: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Enrich report projections without changing historical events or verdicts."""
    out = []
    for result in results:
        value = dict(result)
        if value.get("kind") == "counterexample_search":
            value["coverage"] = dict(value.get("coverage") or {})
            value["coverage"]["input_counts"] = search_input_counts(value, frozen, observations)
            value["detail"] = str(value.get("detail", "")).replace("candidate(s) compared", "search evaluation(s) compared (including repeated inputs)")
        out.append(value)
    return out


def _behavior(snapshot: Snapshot, verdict: c.FinalVerdict, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ko: list[str] = []
    en: list[str] = []
    diverged = [r for r in results if r.get("status") == c.DIVERGED]
    for result in results:
        coverage = result.get("coverage", {}) or {}
        status = result.get("status")
        kind = result.get("kind")
        if kind == "corpus_equivalence" and status == c.PRESERVED_WITHIN_ENVELOPE:
            members = coverage.get("compared", coverage.get("members", 0))
            ko.append(f"확인한 입력 {members}건에서 비교한 값은 모두 이전과 같았습니다.")
            en.append(f"On all {members} recorded input(s), the behaviour after the change matched the behaviour before it on every compared value.")
        elif kind == "counterexample_search" and status == c.NO_DIVERGENCE_FOUND:
            counts = coverage.get("input_counts") or search_input_counts(result, {}, ())
            runs, distinct, new = (counts.get(k) for k in ("search_executions", "distinct_search_inputs", "new_distinct_inputs"))
            ko.append(f"추가 검색을 {runs if runs is not None else '확인 불가(UNKNOWN)'}회 실행했고 차이를 찾지 못했습니다. 실행 횟수에는 같은 입력을 반복한 경우도 포함됩니다. (증명은 아닙니다)")
            en.append(f"The additional search made {runs if runs is not None else 'UNKNOWN'} execution(s) without finding a difference. Executions include repeated inputs. This is evidence, not a proof.")
            if distinct is not None:
                ko.append(f"서로 다른 입력은 {distinct}개이고, 같은 입력을 반복한 실행은 {counts['repeated_executions']}회입니다.")
                en.append(f"There were {distinct} distinct search input(s) and {counts['repeated_executions']} repeated execution(s).")
            else:
                ko.append("서로 다른 입력 수와 반복 실행 수는 보존된 증거로 확인할 수 없습니다 (UNKNOWN).")
                en.append("Distinct search inputs and repeated executions are UNKNOWN from the preserved evidence.")
            if new is not None:
                ko.append(f"그중 기존 기준에 있던 입력은 {counts['baseline_overlap']}개, 기존 기준에 없던 새 입력은 {new}개입니다.")
                en.append(f"Of those distinct inputs, {counts['baseline_overlap']} were already in the frozen baseline and {new} were new to it.")
            else:
                ko.append("기존 기준과 겹치는 입력 수와 새 입력 수는 확인할 수 없습니다 (UNKNOWN).")
                en.append("Baseline overlap and new distinct inputs are UNKNOWN.")
        elif kind == "finite_domain_proof" and status == c.PROVED_WITHIN_DECLARED_DOMAIN:
            members = coverage.get("members", 0)
            ko.append(f"가능한 입력 전체 {members}건을 모두 확인했고, 모두 같았습니다.")
            en.append(f"Every one of the {members} possible inputs in the declared domain was compared and matched.")
        elif status == c.DIVERGED:
            recorded = list(result.get("divergences", []))
            for divergence in recorded[:5]:
                ko.append(f"결과가 달라진 곳: {_divergence_phrase(divergence)}")
                en.append(f"Behaviour differed: {_divergence_phrase(divergence)} ({divergence.get('why', 'values differ')})")
            if len(recorded) > 5:
                ko.append(f"결과가 달라진 곳이 {len(recorded) - 5}곳 더 기록되어 있습니다.")
                en.append(f"Behaviour differed in {len(recorded) - 5} more recorded place(s).")
            omitted = int(coverage.get("divergences_omitted", 0) or 0)
            if omitted:
                ko.append(f"결과가 달라진 곳이 {omitted}곳 더 있었지만 예산 때문에 기록하지 않았습니다 (기록은 {len(recorded)}곳까지).")
                en.append(f"Behaviour differed in {omitted} more divergence(s) omitted by budget (the list was cut at {len(recorded)}).")
            counterexample = result.get("counterexample") or {}
            minimized = (counterexample.get("minimized") or {}).get("input")
            if minimized is not None:
                ko.append(f"차이를 재현하는 가장 작은 입력: {minimized}")
                en.append(f"Smallest input that reproduces the difference: {minimized}")
    if verdict.status == BLOCK and verdict.decided_by == "integrity":
        ko.append("저장된 증거가 변조되었거나 손상되어 결과를 믿을 수 없습니다.")
        en.append("Stored evidence was altered or corrupted; the result cannot be trusted.")
    if not ko:
        ko.append("동작을 비교한 결과가 아직 없습니다.")
        en.append("No behaviour comparison has been recorded yet.")
    if verdict.status == BLOCK:
        status = "no"
    elif verdict.status == UNVERIFIABLE:
        status = "unknown"
    elif diverged:
        status = "partial"
    else:
        status = "yes"
    return _section("behavior", status, ko, en)


def _performance(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Performance is verified only by a performance_envelope claim under its protocol.

    Anything else - the wall-clock a corpus run happened to take - is
    incidental and is labelled as such. The words "verified" and
    "preserved" are not used unless a measurement protocol was declared,
    run, and passed.
    """

    measured = [r for r in results if r.get("kind") == "performance_envelope"]
    if measured:
        result = measured[-1]
        coverage = result.get("coverage", {}) or {}
        protocol = coverage.get("protocol", {}) or {}
        before, after = coverage.get("source_wall_s", 0.0), coverage.get("target_wall_s", 0.0)
        runs, statistic = protocol.get("runs", "?"), protocol.get("statistic", "?")
        rel, abs_s = protocol.get("rel_tolerance", "?"), protocol.get("abs_tolerance_s", "?")
        status = result.get("status")
        if status == c.PRESERVED_WITHIN_ENVELOPE:
            ko = [f"선언한 측정 규약대로 쟀습니다: 입력당 {runs}회, {statistic}, 허용 오차 상대 {_fmt(rel)} / 절대 {_fmt(abs_s)}초. 이전 {_fmt(before)}초, 이후 {_fmt(after)}초 - 허용 범위 안입니다."]
            en = [f"Measured under the declared protocol: {runs} run(s) per input, {statistic}, tolerance rel {_fmt(rel)} / abs {_fmt(abs_s)}s: before {_fmt(before)}s, after {_fmt(after)}s - within tolerance."]
            return _section("performance", "verified", ko, en)
        if status == c.DIVERGED:
            ko = [f"선언한 측정 규약대로 쟀습니다: 입력당 {runs}회, {statistic}. 이전 {_fmt(before)}초, 이후 {_fmt(after)}초 - 허용 오차(상대 {_fmt(rel)} / 절대 {_fmt(abs_s)}초)를 벗어났습니다."]
            en = [f"Measured under the declared protocol: {runs} run(s) per input, {statistic}: before {_fmt(before)}s, after {_fmt(after)}s - outside tolerance rel {_fmt(rel)} / abs {_fmt(abs_s)}s."]
            return _section("performance", "diverged", ko, en)
        ko = ["성능을 검증하지 못했습니다: 측정 규약이 끝까지 실행되지 않았습니다 (" + "; ".join(result.get("unverified", [])[:2]) + ")."]
        en = ["Performance was NOT VERIFIED: the measurement protocol did not complete (" + "; ".join(result.get("unverified", [])[:2]) + ")."]
        return _section("performance", "unverifiable", ko, en)
    ko = ["성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다."]
    en = ["Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance."]
    for result in results:
        timing = (result.get("coverage", {}) or {}).get("timing")
        if not timing:
            continue
        before, after, runs = timing.get("source_wall_s", 0.0), timing.get("target_wall_s", 0.0), timing.get("runs", 0)
        ko.append(f"참고: 비교 실행 {runs}회의 부수적인 실행 시간 합계는 이전 {_fmt(before)}초, 이후 {_fmt(after)}초였습니다. 성능 검증이 아닙니다.")
        en.append(f"For reference only: incidental wall-clock of the {runs} single comparison run(s) was before {_fmt(before)}s, after {_fmt(after)}s. This is not a performance verification.")
    return _section("performance", "not_verified", ko, en)


def _unit_label(unit: Mapping[str, Any]) -> str:
    return str(unit.get("objective") or unit.get("id"))


def _units(snapshot: Snapshot) -> tuple[dict[str, Any], dict[str, Any]]:
    if snapshot.kind != "repair":
        return (
            _section("cleaned", "not_applicable", ["해당 없음"], ["Not applicable: this session compares an existing before and after."]),
            _section("reverted", "not_applicable", ["해당 없음"], ["Not applicable: this session compares an existing before and after."]),
        )
    accepted_ko: list[str] = []
    accepted_en: list[str] = []
    for unit_id in snapshot.accepted_units:
        unit = snapshot.units.get(unit_id, {"id": unit_id})
        delta = unit.get("metrics_delta") or {}
        change = ""
        if delta.get("duplicate_blocks"):
            change = f" 중복 블록 {delta['duplicate_blocks'].get('before')} -> {delta['duplicate_blocks'].get('after')}"
        accepted_ko.append(f"{_unit_label(unit)} (반영됨){change}")
        accepted_en.append(f"{_unit_label(unit)} - accepted as commit {unit.get('commit')}" + (f"; duplicate blocks {delta['duplicate_blocks'].get('before')} -> {delta['duplicate_blocks'].get('after')}" if delta.get("duplicate_blocks") else ""))
    rejected_ko: list[str] = []
    rejected_en: list[str] = []
    for unit_id in snapshot.rejected_units:
        unit = snapshot.units.get(unit_id, {"id": unit_id})
        paths = sorted({d.get("path", "?") for r in unit.get("claim_results", []) for d in r.get("divergences", [])})
        rejected_ko.append(f"{_unit_label(unit)} (되돌림: {unit.get('reason', '')})")
        rejected_en.append(f"{_unit_label(unit)} - rejected: {unit.get('reason', '')}" + (f"; divergence at {', '.join(paths)}" if paths else ""))
    return (
        _section("cleaned", "listed" if accepted_ko else "none", accepted_ko or ["없음"], accepted_en or ["None."]),
        _section("reverted", "listed" if rejected_ko else "none", rejected_ko or ["없음"], rejected_en or ["None."]),
    )


def _unverified(
    snapshot: Snapshot,
    manifest: Manifest,
    verdict: c.FinalVerdict,
    results: Sequence[Mapping[str, Any]],
    uncovered: Sequence[str],
    picture: Mapping[str, Any] | None = None,
    sensitivity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ko: list[str] = []
    en: list[str] = []
    for result in results:
        if result.get("status") == c.UNVERIFIABLE:
            for item in result.get("unverified", []) or ["could not be verified"]:
                ko.append(f"{result.get('claim_id')}: 확인하지 못함 ({item})")
                en.append(f"{result.get('claim_id')}: not verified ({item})")
    for note in verdict.never_evaluated:
        ko.append(f"평가되지 않음: {note}")
        en.append(f"never evaluated: {note}")
    said: set[str] = set()
    for result in results:
        for divergence in (result.get("coverage") or {}).get("informational_divergences", []) or []:
            path = str(divergence.get("path", "?"))
            if path in said:
                continue
            said.add(path)
            by = divergence.get("excluded_by") or "declaration"
            ko.append(f"비교에서 제외했지만 실제로 달라진 값: {path} (제외: {by})")
            en.append(f"excluded from comparison, but it did differ: {path} (excluded by {by})")
    if not picture:
        # without a map, the declaration is the only record of what was left out
        for exclusion in manifest.exclusions:
            ko.append(f"검사에서 제외됨(선언): {exclusion.id} - {exclusion.reason}")
            en.append(f"excluded by declaration: {exclusion.id} ({exclusion.reason}) at {exclusion.path}")
    for path in uncovered:
        ko.append(f"실행마다 달라지는 값이 있는데 아직 규칙이 없습니다: {path}")
        en.append(f"varies between runs and no accepted policy covers it: {path}")
    if picture:
        # what the manifest itself made invisible: the reader sees it here, not in a footnote
        counts = picture.get("counts", {})
        if counts.get("EXCLUDED") or counts.get("UNOBSERVED") or counts.get("UNVERIFIABLE") or picture.get("partial"):
            ko.append(coverage_module.summary_line(picture, "ko"))
            en.append(coverage_module.summary_line(picture, "en"))
            ko.extend(coverage_module.unobserved_lines(picture, "ko"))
            en.extend(coverage_module.unobserved_lines(picture, "en"))
    if sensitivity:
        # every blind spot is on its map line above (declared ones with their declaration, undeclared ones as such)
        if not picture:
            for entry in sensitivity.get("blind", []):
                ko.append(f"이 값이 달라져도 눈치채지 못했을 것입니다: {entry['path']}")
                en.append(f"a change here would not have been noticed: {entry['path']} (tried: {', '.join(entry.get('mutations', []))})")
        for entry in sensitivity.get("dulled", []):
            by = _dulling_policy(picture, entry["path"])
            ko.append(f"작은 변화는 눈치채지 못했을 것입니다 (선언한 정책 {by} 안): {entry['path']}")
            en.append(f"a small change here would not have been noticed (within the declared policy {by}): {entry['path']} (missed: {', '.join(entry.get('missed', []))})")
        for entry in sensitivity.get("order_insensitive", []):
            ko.append(f"순서가 바뀌어도 눈치채지 못했을 것입니다: {entry['path']}")
            en.append(f"a change of order here would not have been noticed: {entry['path']}")
        if sensitivity.get("truncated"):
            ko.append(f"점검 지점 {sensitivity.get('sites_total')}곳 중 {sensitivity.get('sites')}곳만 점검했습니다 (예산).")
            en.append(f"the blind-spot scan covered {sensitivity.get('sites')} of {sensitivity.get('sites_total')} site(s) (budget).")
    elif sensitivity is None and snapshot.frozen:
        ko.append("눈치채지 못할 변화가 있는지 점검(blind-spot scan)은 실행되지 않았습니다.")
        en.append("The blind-spot scan was not run for the current declaration.")
    if not ko:
        return _section("unverified", "none", ["없음"], ["None: every declared check was evaluated."])
    return _section("unverified", "listed", ko, en)


def _dulling_policy(picture: Mapping[str, Any] | None, path: str) -> str:
    """Name the declared policy that dulls ``path``: ``id (kind)``; the map knows which policy reached which leaf."""

    for policy_id, view in (picture or {}).get("policies", {}).items():
        if path in view.get("paths", ()):
            return f"{policy_id} ({view.get('kind')})"
    return "(unnamed)"


def _human(manifest: Manifest, verdict: c.FinalVerdict) -> dict[str, Any]:
    en: list[str] = []
    for note in verdict.needs_human:
        if note not in en:
            en.append(note)
    for item in manifest.human_review:
        if item.reason not in en and not any(item.reason in note for note in en):
            en.append(f"{item.id}: {item.reason}")
    if not en:
        return _section("human", "none", ["없음"], ["None."])
    ko = [f"확인 필요: {note}" for note in en]
    return _section("human", "listed", ko, en)


_COMPARED_STATES = ("PROVED", "TESTED", "SEARCHED")


def bounded_map(picture: Mapping[str, Any]) -> dict[str, Any]:
    """The map as the report carries it: every roll-up, and the values that were not compared, by path.

    A compared value is counted, not listed (a 100k-leaf record would embed
    a 100k-entry table in every report); the full per-leaf map stays on
    demand (`assure coverage`, `Workflow.coverage`).
    """

    out = dict(picture)
    keep = {path for path, state in picture.get("paths", {}).items() if state not in _COMPARED_STATES}
    out["paths"] = {path: state for path, state in picture.get("paths", {}).items() if path in keep}
    out["reasons"] = {path: reason for path, reason in picture.get("reasons", {}).items() if path in keep}
    out["causes"] = {path: cause for path, cause in picture.get("causes", {}).items() if path in keep}
    out["paths_omitted"] = len(picture.get("paths", {})) - len(keep)
    probes = {}
    for probe_id, view in picture.get("probes", {}).items():
        probe_view = dict(view)
        probe_view["paths"] = {path: state for path, state in view.get("paths", {}).items() if path in keep}
        probes[probe_id] = probe_view
    out["probes"] = probes
    out["note"] = "compared values are counted, not listed; the full per-leaf map is available on demand (assure coverage)"
    return out


def scan_line(scan: Mapping[str, Any]) -> str:
    """The blind-spot scan in one line, in one unit: value paths, with the sites (path x input) it tried."""

    return (
        f"{scan.get('paths_scanned', 0)} value path(s) over {scan.get('inputs', 0)} input(s), "
        f"{scan.get('sites', 0)} of {scan.get('sites_total', 0)} site(s) mutated under the manifest in force; "
        f"{scan.get('sensitive', 0)} sensitive, {len(scan.get('blind', []))} blind, "
        f"{len(scan.get('placeholder', []))} replaced by a declared placeholder, "
        f"{len(scan.get('dulled', []))} dulled, {len(scan.get('fail_closed', []))} fail closed"
    )


def _questions(verdict: c.FinalVerdict, sections: Sequence[Mapping[str, Any]], picture: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The three questions a non-coder asks, answered from the six sections.

    Same as before? Anything odd? Say so when you do not know. The sections
    stay; these are the first thing a reader sees.
    """

    by_key = {section["key"]: section for section in sections}
    behavior, performance = by_key["behavior"], by_key["performance"]
    reverted, unverified, human = by_key["reverted"], by_key["unverified"], by_key["human"]
    same_status = {"yes": "yes", "partial": "no", "no": "no", "unknown": "unknown"}.get(behavior["status"], "unknown")
    if verdict.status == UNVERIFIABLE:
        same_status = "unknown"
    same = {
        "q_ko": "전이랑 같아?",
        "q_en": "Same as before?",
        "status": same_status,
        "answer_ko": list(behavior["lines_ko"]) + list(performance["lines_ko"]),
        "answer_en": list(behavior["lines_en"]) + list(performance["lines_en"]),
    }
    odd_ko = [line for line in behavior["lines_ko"] if line.startswith("결과가 달라진 곳") or line.startswith("차이를 재현")]
    odd_en = [line for line in behavior["lines_en"] if line.startswith("Behaviour differed") or line.startswith("Smallest input")]
    if reverted["status"] == "listed":
        odd_ko += list(reverted["lines_ko"])
        odd_en += list(reverted["lines_en"])
    odd = {
        "q_ko": "이상한 점 있어?",
        "q_en": "Anything odd?",
        "status": "listed" if odd_ko else "none",
        "answer_ko": odd_ko or ["없음. 비교한 범위에서는 달라진 곳을 찾지 못했습니다."],
        "answer_en": odd_en or ["None found within what was compared."],
    }
    unknown_ko: list[str] = []
    unknown_en: list[str] = []
    if unverified["status"] != "none":
        unknown_ko += list(unverified["lines_ko"])
        unknown_en += list(unverified["lines_en"])
    if human["status"] != "none":
        unknown_ko += list(human["lines_ko"])
        unknown_en += list(human["lines_en"])
    listed = unverified["status"] != "none" or human["status"] != "none"
    honest = {
        "q_ko": "모르면 솔직히 말해!",
        "q_en": "What could you not check?",
        "status": "listed" if listed else "none",
        "answer_ko": unknown_ko or ["선언된 검사는 모두 평가했습니다. 선언되지 않은 동작은 보지 못합니다."],
        "answer_en": unknown_en or ["Every declared check was evaluated. Behaviour outside the declaration is not observed."],
    }
    return [same, odd, honest]


def _short(value: Any, limit: int = 200) -> Any:
    """A raw value fit for a page: long text is cut and its digest kept."""

    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}... [{len(value)} chars, sha256 {content_digest(value)[:16]}]"
    if isinstance(value, (dict, list)):
        text = str(value)
        if len(text) > limit:
            return f"{text[:limit]}... [{len(text)} chars, sha256 {content_digest(value)[:16]}]"
    return value


def build(
    snapshot: Snapshot,
    manifest: Manifest,
    verdict: c.FinalVerdict,
    *,
    provenance: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
    results: Sequence[Mapping[str, Any]] | None = None,
    uncovered_volatile: Sequence[str] | None = None,
    coverage_map: Mapping[str, Any] | None = None,
    sensitivity: Mapping[str, Any] | None = None,
    observations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Both layers as one JSON document.

    ``results`` are the claim results the verdict rests on; when omitted the
    latest results under the current manifest are used, which is right for
    an assure session and wrong for a repair session (whose verdict rests on
    the last accepted unit), so the workflow always passes them.
    """

    results = [dict(r) for r in results] if results is not None else snapshot.active_claim_results()
    results = counted_results(results, snapshot.frozen or {}, observations)
    # volatility is judged under the manifest in force (the workflow passes
    # it); the stored capture only knows the policies of its own time
    capture_now = snapshot.captures[-1] if snapshot.captures else {}
    uncovered = list(uncovered_volatile) if uncovered_volatile is not None else list(capture_now.get("uncovered_volatile", []))
    label_ko, label_en = _VERDICT_LABELS[verdict.status]
    if verdict.status == PASS and any(status == c.DIVERGED for status in verdict.informational_statuses.values()):
        # the mandatory claims decided PASS; an informational claim found a difference, and the headline says both
        label_ko, label_en = _QUALIFIED_PASS
    cleaned, reverted = _units(snapshot)
    sections = [
        _behavior(snapshot, verdict, results),
        _performance(results),
        cleaned,
        reverted,
        _unverified(snapshot, manifest, verdict, results, uncovered, coverage_map, sensitivity),
        _human(manifest, verdict),
    ]
    summary = {
        "session_id": snapshot.session_id,
        "verdict": {"status": verdict.status, "label_ko": label_ko, "label_en": label_en, "decided_by": verdict.decided_by, "informational_statuses": dict(verdict.informational_statuses)},
        "questions": _questions(verdict, sections, coverage_map),
        "sections": sections,
    }
    frozen = snapshot.frozen or {}
    latest_capture = snapshot.captures[-1] if snapshot.captures else {}
    accepted_units = [dict(snapshot.units.get(u, {"id": u})) for u in snapshot.accepted_units]
    rejected_units = [dict(snapshot.units.get(u, {"id": u})) for u in snapshot.rejected_units]
    divergences = [dict(d, claim_id=r.get("claim_id")) for r in results for d in r.get("divergences", [])]
    divergences += [
        dict(d, claim_id=r.get("claim_id"), mandatory=False)
        for r in results
        for d in (r.get("coverage", {}) or {}).get("informational_divergences", []) or []
    ]
    for divergence in divergences:
        for key in ("raw_source", "raw_target", "normalized_source", "normalized_target"):
            if key in divergence:
                divergence[key] = _short(divergence[key])
    divergences.sort(key=lambda d: (not d.get("mandatory", False), str(d.get("path"))))
    provenance_data: dict[str, Any] = {
        "repository": snapshot.repository,
        "base_commit": snapshot.base_commit,
        "accepted_commit": snapshot.accepted_commit,
        "workspace": snapshot.workspace,
        "roots": dict(snapshot.roots),
        "manifest_provenance": dict(manifest.provenance),
    }
    provenance_data.update(dict(provenance or {}))
    provenance_data.setdefault("tool_versions", {})
    technical = {
        "session": {"id": snapshot.session_id, "kind": snapshot.kind, "state": snapshot.state, "events": snapshot.events},
        "manifest": {
            "digest": snapshot.manifest_digest,
            "history": list(snapshot.manifest_history),
            "schema_version": manifest.schema_version,
            "session_id": manifest.session_id,
            "source_system": manifest.source_system.as_dict(),
            "target_system": manifest.target_system.as_dict(),
            "input_domain": {
                "kind": manifest.input_domain.kind,
                "delivery": manifest.input_domain.delivery,
                "corpus_size": len(manifest.input_domain.corpus),
                "finite_cardinality": manifest.input_domain.finite.cardinality if manifest.input_domain.finite else None,
                "finite_digest": manifest.input_domain.finite.digest() if manifest.input_domain.finite else None,
            },
            "probes": [probe.as_dict() for probe in manifest.probes],
            "claims": [claim.as_dict() for claim in manifest.claims],
            "budgets": manifest.budgets.as_dict(),
            "timeouts": manifest.timeouts.as_dict(),
            "exclusions": [e.as_dict() for e in manifest.exclusions],
            "human_review": [h.as_dict() for h in manifest.human_review],
        },
        "baseline": {
            "digest": snapshot.baseline_digest,
            "inputs": list(frozen.get("inputs", [])),
            "record_digests": dict(frozen.get("record_digests", {})),
            "policy_set_digest": frozen.get("policy_set_digest"),
            "frozen_at": frozen.get("frozen_at"),
            "ambiguities": list(frozen.get("ambiguities", [])),
        },
        "policies": [policy.as_dict() for policy in manifest.policies],
        "stability": {
            "runs": latest_capture.get("runs"),
            "volatile_paths": list(latest_capture.get("volatile_paths", [])),
            "proposals": [dict(p) for p in latest_capture.get("proposals", [])],
            "uncovered_volatile": list(uncovered),
            "raw_digests": dict(latest_capture.get("raw_digests", {})),
        },
        "coverage": c.coverage_summary(
            [c.ClaimResult.from_dict(r) for r in results],
            exclusions=[e.as_dict() for e in manifest.exclusions],
            not_observed=list(uncovered),
        ),
        "coverage_map": bounded_map(coverage_map) if coverage_map is not None else None,
        "sensitivity": dict(sensitivity) if sensitivity is not None else None,
        "claims": [dict(r) for r in results],
        "claim_history": counted_results(snapshot.claim_results, snapshot.frozen or {}, observations),
        "invalidated_claims": [dict(i) for i in snapshot.invalidated_claims],
        "amendments": [dict(a) for a in snapshot.amendments],
        "post_divergence_amendments": list(snapshot.post_divergence),
        "weakening_amendments": list(snapshot.weakening),
        "divergences": divergences,
        "counterexamples": [dict(r["counterexample"]) for r in results if r.get("counterexample")],
        "findings": [dict(f) for f in snapshot.findings],
        "metrics_before": dict(snapshot.metrics_before) if snapshot.metrics_before else None,
        "units": {"plan": [dict(u) for u in snapshot.plan], "accepted": accepted_units, "rejected": rejected_units, "rollbacks": [dict(r) for r in snapshot.rollbacks]},
        "provenance": provenance_data,
        "final_verdict": verdict.as_dict(),
        "limitations": list(LIMITATIONS),
    }
    if extras:
        technical["evidence"] = dict(extras)
    if any(probe.adapter == "http" for probe in manifest.probes):
        technical["http_transport"] = {
            "supported_scheme": "http",
            "literal_hosts": ["127.0.0.1", "::1"],
            "absolute_url_default_port": 80,
            "relative_target_port": "allocated manifest service port",
            "endpoint_evidence": "raw observations: http_service_port, http_request_identities, http_request_declarations",
            "redirects_followed": False,
            "proxies_used": False,
            "tls_executed": False,
        }
    data = {"record_version": REPORT_VERSION, "summary": summary, "technical": technical}
    redacted, _ = redact_value(data)
    return redacted


# --------------------------------------------------------------------------
# markdown


def _bullets(lines: Sequence[Any]) -> list[str]:
    return [f"- {line}" for line in lines] or ["- (none)"]


def markdown(data: Mapping[str, Any]) -> str:
    summary = data["summary"]
    technical = data["technical"]
    out: list[str] = []
    out.append(f"# 변환 보증 보고서 / Transformation assurance report - {summary.get('session_id', '')}")
    out.append("")
    verdict = summary["verdict"]
    out.append("## 결과 / Verdict")
    out.append("")
    out.append(f"**{verdict['label_ko']}** / {verdict['label_en']} (`{verdict['status']}`, decided by `{verdict['decided_by']}`)")
    out.append("")
    for claim_id, status in (verdict.get("informational_statuses") or {}).items():
        out.append(f"- informational claim {claim_id}: {status}")
    if verdict.get("informational_statuses"):
        out.append("")
    if summary.get("questions"):
        out.append("## 세 가지 질문 / Three questions")
        out.append("")
        for question in summary["questions"]:
            out.append(f"### {question['q_ko']} / {question['q_en']} - {question['status']}")
            out.append("")
            out.extend(_bullets(question["answer_ko"]))
            out.append("")
            out.extend(_bullets(question["answer_en"]))
            out.append("")
    out.append("## 요약 / Summary")
    out.append("")
    for section in summary["sections"]:
        out.append(f"### {section['title_ko']} / {section['title_en']} - {section['status']}")
        out.append("")
        out.extend(_bullets(section["lines_ko"]))
        out.append("")
        out.extend(_bullets(section["lines_en"]))
        out.append("")
    out.append("## Technical assurance report")
    out.append("")
    out.append("### Manifest")
    out.append("")
    manifest = technical["manifest"]
    out.append(f"- digest: `{manifest['digest']}`")
    out.append(f"- history: {', '.join(f'`{d}`' for d in manifest['history'])}")
    out.append(f"- schema: {manifest['schema_version']}")
    out.append(f"- source: `{' '.join(manifest['source_system']['command'])}` at `{manifest['source_system']['root']}`")
    out.append(f"- target: `{' '.join(manifest['target_system']['command'])}` at `{manifest['target_system']['root']}`")
    domain = manifest["input_domain"]
    out.append(f"- input domain: {domain['kind']} via {domain['delivery']}, corpus {domain['corpus_size']}" + (f", finite cardinality {domain['finite_cardinality']}" if domain.get("finite_cardinality") else ""))
    out.append("")
    out.append("### Baseline")
    out.append("")
    baseline = technical["baseline"]
    out.append(f"- baseline digest: `{baseline['digest']}`")
    out.append(f"- policy set digest: `{baseline.get('policy_set_digest')}`")
    for input_id, record_digest in sorted(baseline.get("record_digests", {}).items()):
        out.append(f"- {input_id}: `{record_digest}`")
    out.append("")
    out.append("### Policies")
    out.append("")
    for policy in technical["policies"]:
        out.append(f"- `{policy['id']}` {policy['kind']} at `{policy['path']}`" + (f" (+{len(policy['paths'])} more)" if policy.get("paths") else "") + f" - {policy.get('reason', '')} [{policy.get('origin')}, {'accepted' if policy.get('accepted') else 'not accepted'}]")
    if not technical["policies"]:
        out.append("- exact comparison everywhere")
    out.append("")
    out.append("### Coverage")
    out.append("")
    for key, value in technical["coverage"].items():
        out.append(f"- {key}: {', '.join(value) if value else '(none)'}")
    out.append("")
    picture = technical.get("coverage_map")
    if picture:
        out.append("### Coverage map")
        out.append("")
        out.append(f"- {coverage_module.summary_line(picture, 'en')}")
        counts = picture.get("counts", {})
        out.append("- by state: " + ", ".join(f"{state} {counts.get(state, 0)}" for state in picture.get("states", []) if counts.get(state, 0)))
        for probe_id, entry in picture.get("probes", {}).items():
            per_state = ", ".join(f"{state} {n}" for state, n in entry.get("counts", {}).items() if n)
            out.append(f"- probe `{probe_id}` ({entry.get('adapter')}, {'mandatory' if entry.get('mandatory') else 'informational'}): {entry.get('state')} ({per_state})")
        listed = [(path, state) for path, state in picture.get("paths", {}).items() if state not in ("PROVED", "TESTED", "SEARCHED")]
        for path, state in listed[:50]:
            out.append(f"  - `{path}`: {state} ({picture.get('reasons', {}).get(path, '')})")
        if len(listed) > 50:
            out.append(f"  - ... {len(listed) - 50} more in the JSON report")
        for claim_id, entry in picture.get("claims", {}).items():
            out.append(f"- claim `{claim_id}` ({entry.get('kind')}): {entry.get('state')}")
        out.append("")
    scan = technical.get("sensitivity")
    if scan:
        out.append("### Blind-spot scan")
        out.append("")
        out.append(f"- {scan_line(scan)}")
        for name, entry in scan.get("by_mutation", {}).items():
            out.append(f"- {name}: tried {entry.get('tried')}, noticed {entry.get('noticed')}, missed {entry.get('missed')}, unverifiable {entry.get('unverifiable')}")
        for entry in scan.get("blind", [])[:50]:
            out.append(f"  - blind: `{entry['path']}` (tried {', '.join(entry.get('mutations', []))})")
        for entry in scan.get("placeholder", [])[:50]:
            out.append(f"  - placeholder: `{entry['path']}` (policy {entry.get('policy')} ({entry.get('kind')}); another value of the same shape passes)")
        for entry in scan.get("dulled", [])[:50]:
            out.append(f"  - dulled: `{entry['path']}` (missed {', '.join(entry.get('missed', []))})")
        out.append(f"- {scan.get('note', '')}")
        out.append("")
    out.append("### Claims")
    out.append("")
    for claim in technical["claims"]:
        out.append(f"- `{claim['claim_id']}` ({claim['kind']}, {'mandatory' if claim.get('mandatory') else 'informational'}): **{claim['status']}** - {claim.get('detail', '')}")
    if not technical["claims"]:
        out.append("- no claim evaluated under the current manifest")
    out.append("")
    out.append("### Divergences")
    out.append("")
    for divergence in technical["divergences"]:
        out.append(f"- `{divergence.get('path')}` [{divergence.get('claim_id')}, {'mandatory' if divergence.get('mandatory') else 'informational'}] raw {divergence.get('raw_source')!r} -> {divergence.get('raw_target')!r}; normalized {divergence.get('normalized_source')!r} -> {divergence.get('normalized_target')!r}; policy {divergence.get('policy_id')} ({divergence.get('policy_kind')}); {divergence.get('why')}")
    if not technical["divergences"]:
        out.append("- none")
    out.append("")
    out.append("### Counterexamples")
    out.append("")
    for counterexample in technical["counterexamples"]:
        out.append(f"- {counterexample}")
    if not technical["counterexamples"]:
        out.append("- none")
    out.append("")
    out.append("### Repair units")
    out.append("")
    for unit in technical["units"]["accepted"]:
        out.append(f"- accepted `{unit.get('id')}`: {unit.get('objective', '')} - commit `{unit.get('commit')}`, tree `{unit.get('tree')}`")
    for unit in technical["units"]["rejected"]:
        out.append(f"- rejected `{unit.get('id')}`: {unit.get('objective', '')} - {unit.get('reason', '')}, patch `{unit.get('patch_digest')}`")
    if not technical["units"]["accepted"] and not technical["units"]["rejected"]:
        out.append("- none")
    out.append("")
    out.append("### Findings and metrics")
    out.append("")
    for finding in technical["findings"]:
        out.append(f"- {finding.get('kind')} `{finding.get('id')}` ({finding.get('declared_by')}): {finding.get('summary')} - {', '.join(finding.get('paths', []))}")
    if technical.get("metrics_before"):
        out.append(f"- metrics before: {technical['metrics_before']}")
    if not technical["findings"] and not technical.get("metrics_before"):
        out.append("- none recorded")
    out.append("")
    out.append("### Amendments")
    out.append("")
    for amendment in technical["amendments"]:
        out.append(f"- {amendment.get('old_digest')} -> {amendment.get('new_digest')} by {amendment.get('requested_by')}: {amendment.get('reason')}")
    for note in technical["post_divergence_amendments"]:
        out.append(f"- post-divergence: {note}")
    for note in technical.get("weakening_amendments", []):
        out.append(f"- weakening: {note}")
    if not technical["amendments"]:
        out.append("- none")
    out.append("")
    out.append("### Provenance")
    out.append("")
    for key, value in technical["provenance"].items():
        out.append(f"- {key}: {value}")
    out.append("")
    out.append("### Final verdict")
    out.append("")
    final = technical["final_verdict"]
    out.append(f"- `{final['status']}` decided by `{final['decided_by']}`: {final['reason']}")
    for key in ("integrity", "diverged", "never_evaluated", "unverifiable", "needs_human", "preserved", "proved", "no_divergence_found", "informational", "informational_statuses"):
        if final.get(key):
            value = final[key]
            rendered = "; ".join(f"{k}: {v}" for k, v in value.items()) if isinstance(value, dict) else "; ".join(str(item) for item in value)
            out.append(f"- {key}: {rendered}")
    out.append("")
    out.append("### Limitations")
    out.append("")
    out.extend(_bullets(technical["limitations"]))
    out.append("")
    return "\n".join(out)
