"""``invara assure`` and ``invara repair`` — the whole protocol from a shell.

No AI is needed to drive it and none is consulted. Every step is a
subcommand with an exit code a shell or a CI job can act on: a claim that
diverged exits 1, a claim that could not be verified exits 2, a refused
step exits 3, and ``verify --require PASS`` exits 1 unless the session's
final verdict is exactly that — which is how a sealed kernel contract can
gate on an assurance session without the kernel learning anything new.

Commands that a host agent has to read back (``status``, ``resume``,
``unit-start``) print JSON; the rest print short lines. Anything printed
here is ASCII apart from what the operator wrote and what the report says.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import store, exact_json
from ..contract import BLOCK, UNVERIFIABLE
from . import claims
from .evidence import Evidence, EvidenceError
from .governor import GovernorError
from .manifest import Manifest, ManifestError
from .report import _short as report_short, markdown
from .workflow import Workflow, WorkflowError

__all__ = ["register"]

#: Mirrors :mod:`invara.__main__`; kept here so this module never imports
#: the command layer it plugs into.
EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_UNVERIFIABLE = 2
EXIT_REFUSED = 3


def _load_json(path: str, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkflowError("file_missing", f"{what} not found: {path}") from None
    except ValueError as error:
        raise WorkflowError("bad_json", f"{what} is not valid JSON: {error}") from None


def _manifest(path: str) -> Manifest:
    return Manifest.from_dict(_load_json(path, "manifest"))


def _write_utf8_lf(path: str | Path, text: str) -> None:
    """A report file is UTF-8 with LF, on every platform; text mode would give Windows CRLF."""

    Path(path).write_bytes(text.encode("utf-8"))


def _print_json(data: Any) -> None:
    # ASCII-safe on purpose: this is what a host agent parses back, and a
    # cp949 console must not be able to turn one character into a question mark.
    print(exact_json.dumps(data, ensure_ascii=True, indent=1, default=str))


def _short(value: Any, limit: int = 200) -> str:
    """A raw value fit for a console line: the report module owns the rule, this only quotes it."""

    return repr(report_short(value, limit))


def _claim_exit(status: str) -> int:
    if status == claims.DIVERGED:
        return EXIT_BLOCK
    if status == claims.UNVERIFIABLE:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def _verdict_exit(status: str) -> int:
    if status == BLOCK:
        return EXIT_BLOCK
    if status == UNVERIFIABLE:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def _print_claim(result: claims.ClaimResult) -> None:
    print(f"{result.claim_id}: {result.status}" + (f"  ({result.detail})" if result.detail else ""))
    coverage = result.coverage or {}
    if coverage:
        shown = {k: v for k, v in coverage.items() if k in ("kind", "members", "compared", "cardinality", "runs", "compared_runs", "unverifiable_runs", "seed")}
        print(f"  coverage: {exact_json.dumps(shown, sort_keys=True)}")
    for divergence in result.divergences[:5]:
        print(
            f"  divergence: {divergence.get('path')}"
            + (f" [{divergence.get('input_id')}]" if divergence.get("input_id") else "")
            + f" raw {_short(divergence.get('raw_source'))} -> {_short(divergence.get('raw_target'))}: {divergence.get('why')}"
        )
    if len(result.divergences) > 5:
        print(f"  ... {len(result.divergences) - 5} more divergence(s)")
    if result.counterexample:
        minimized = result.counterexample.get("minimized") or {}
        if minimized:
            print(f"  counterexample minimized: {exact_json.dumps(minimized.get('input'), ensure_ascii=False, sort_keys=True)}")
        original = result.counterexample.get("original") or {}
        if original:
            print(f"  counterexample original:  {exact_json.dumps(original.get('input'), ensure_ascii=False, sort_keys=True)}")
    for item in result.unverified[:5]:
        print(f"  unverified: {item}")


def _print_verdict(verdict: claims.FinalVerdict) -> None:
    print(f"{verdict.status}: {verdict.reason}")
    print(f"decided by: {verdict.decided_by}")
    for field in ("integrity", "constraint_breaks", "diverged", "never_evaluated", "unverifiable", "needs_human"):
        for item in getattr(verdict, field)[:5]:
            print(f"  {field}: {item}")
    # an informational claim never decides, and what it found is never hidden behind the mandatory verdict
    for claim_id, status in verdict.informational_statuses.items():
        print(f"  informational: {claim_id}: {status}")


def _guarded(function: Callable[[argparse.Namespace, Workflow], int]) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        # opening the store is inside the guard too: nothing on this path may escape as a traceback
        # (the exit code of a crash is the BLOCK code, which a CI wrapper would read as a behavioural block)
        evidence: Evidence | None = None
        try:
            evidence = Evidence(args.db)
            flow = Workflow(evidence)
            return function(args, flow)
        except (WorkflowError, ManifestError, GovernorError) as error:
            reason = getattr(error, "reason", "refused")
            detail = getattr(error, "detail", "") or str(error)
            print(f"REFUSED {reason}: {detail}")
            return EXIT_REFUSED
        except EvidenceError as error:
            print(f"REFUSED {getattr(error, 'reason', 'store_corrupt')}: {error}")
            return EXIT_REFUSED
        except Exception as error:  # noqa: BLE001 - no unexpected exception may read as a verdict
            print(f"REFUSED internal_error: {type(error).__name__}: {error}")
            return EXIT_REFUSED
        finally:
            if evidence is not None:
                evidence.close()

    return run


# --------------------------------------------------------------------------
# shared steps


def _characterize(args: argparse.Namespace, flow: Workflow) -> int:
    capture = flow.characterize(args.session_id, runs=args.runs)
    snap = flow.snapshot(args.session_id)
    print(f"session {args.session_id}  {snap.state}")
    print(f"  inputs     {len(capture.inputs)}  runs {capture.runs}")
    for problem in capture.problems:
        print(f"  problem    {problem}")
    if capture.runs > 1:
        print(f"  volatile   {len(capture.volatile_paths)} path(s)")
        for proposal in capture.proposals:
            print(f"    {proposal['path']}: proposed {proposal['kind']} (inferred, not accepted)")
        for path in capture.uncovered_volatile:
            print(f"    uncovered: {path}")
    return EXIT_OK


def _freeze(args: argparse.Namespace, flow: Workflow) -> int:
    frozen = flow.freeze(args.session_id)
    print(f"session {args.session_id}  BASELINE_FROZEN")
    print(f"  baseline   {frozen.baseline_digest}")
    print(f"  manifest   {frozen.manifest_digest}")
    print(f"  inputs     {len(frozen.inputs)}")
    for note in frozen.ambiguities:
        print(f"  ambiguity  {note}")
    return EXIT_OK


def _verify(args: argparse.Namespace, flow: Workflow) -> int:
    verdict = flow.verdict(args.session_id)
    _print_verdict(verdict)
    required = getattr(args, "require", None)
    if required and verdict.status != required:
        print(f"required {required}, got {verdict.status}")
        return EXIT_BLOCK
    return _verdict_exit(verdict.status)


def _report(args: argparse.Namespace, flow: Workflow) -> int:
    data = flow.report(args.session_id)
    written = False
    if args.json:
        _write_utf8_lf(args.json, exact_json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n")
        print(f"wrote {args.json}")
        written = True
    if args.md:
        _write_utf8_lf(args.md, markdown(data))
        print(f"wrote {args.md}")
        written = True
    if not written:
        print(markdown(data))
    return EXIT_OK


def _status(args: argparse.Namespace, flow: Workflow) -> int:
    _print_json(flow.status(args.session_id))
    return EXIT_OK


def _coverage(args: argparse.Namespace, flow: Workflow) -> int:
    _print_json(flow.coverage(args.session_id))
    return EXIT_OK


def _print_summary(report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    verdict = summary["verdict"]
    print(f"{verdict['status']}  {verdict['label_en']}  (decided by {verdict['decided_by']})")
    for question in summary.get("questions", []):
        print(f"- {question['q_en']} [{question['status']}]")
        for line in question["answer_en"][:6]:
            print(f"    {line}")


def _assure_run(args: argparse.Namespace, flow: Workflow) -> int:
    manifest = _manifest(args.manifest)
    extra = {"run_workspace": str(Path(args.workspace).resolve())} if args.workspace else {}
    outcome = flow.run(manifest, {"SOURCE_ROOT": args.source_root, "TARGET_ROOT": args.target_root}, extra=extra, runs=args.runs)
    _print_summary(outcome["report"])
    # the directories this verdict is about: an existing session is bound to the roots it was created with, and a run
    # that names others is refused before this line (roots_mismatch), so what is printed is what was examined
    print(f"roots: source {outcome['roots']['SOURCE_ROOT']}; target {outcome['roots']['TARGET_ROOT']}")
    print(f"steps: {', '.join(outcome['steps'])}")
    verdict = outcome["verdict"]
    for path, text in ((args.json, lambda: exact_json.dumps(outcome["report"], ensure_ascii=False, indent=1, default=str) + "\n"), (args.md, lambda: markdown(outcome["report"]))):
        if not path:
            continue
        try:
            _write_utf8_lf(path, text())
        except OSError as error:
            # the session is on record with its verdict; the report the caller asked for is not, and that is a
            # refusal of this command, named with the verdict it did reach, never a traceback and never a verdict code
            print(f"REFUSED report_unwritable: {path}: {error}; the session is on record with verdict {verdict['status']} (decided by {verdict['decided_by']}); run again with a writable path")
            return EXIT_REFUSED
    return _verdict_exit(verdict["status"])


def _unit_finish(args: argparse.Namespace, flow: Workflow) -> int:
    outcome = flow.unit_finish(args.session_id, args.unit_id)
    verdict = outcome["verdict"]
    # the exit code is the verdict on record, decided before anything is printed: a console that fails after
    # the unit was verified (and, on PASS, accepted and committed) cannot turn that into a refusal
    code = _verdict_exit(verdict["status"])
    try:
        print(f"{verdict['status']}  (decided by {verdict['decided_by']})  accepted={'yes' if outcome['accepted'] else 'no'}")
        if outcome["accepted"]:
            print(f"commit {outcome['commit']}")
        print(f"next: {outcome['next']}")
    except Exception as error:  # noqa: BLE001 - output failed after the step completed; the state on record stands
        try:
            sys.stderr.write(
                f"output failed after unit-finish completed ({type(error).__name__}: {error}); on record: {verdict['status']} "
                f"accepted={'yes' if outcome['accepted'] else 'no'}" + (f" commit {outcome['commit']}" if outcome["accepted"] else "") + "\n"
            )
        except Exception:  # noqa: BLE001 - nothing else to say it with
            pass
    return code


def _export(args: argparse.Namespace, flow: Workflow) -> int:
    from .package import export

    summary = export(flow.evidence, args.session_id, args.out)
    _print_json(summary)
    return EXIT_OK


def _inspect(args: argparse.Namespace) -> int:
    from .package import inspect

    try:
        report = inspect(args.package)
    except Exception as error:  # noqa: BLE001 - an unreadable package is a refusal, never a verdict
        print(f"REFUSED unreadable_package: {type(error).__name__}: {error}")
        return EXIT_REFUSED
    _print_json(report)
    # what the package carries on faith is said beside what it re-derived: the store-wide problems of the exporting
    # store cannot be re-checked here (the other sessions are not in the package)
    print(f"store problem(s) carried from the exporting store: {len(report.get('store_problems') or [])}")
    if not report["ok"]:
        print("INSPECT FAILED: the package does not support its own claim")
        return EXIT_BLOCK
    verdict = report.get("verdict") or {}
    print(f"INSPECT OK: {report['checks']['comparisons_agree']} comparison(s) recomputed and agreeing; verdict {verdict.get('status')} re-derived")
    return EXIT_OK


def _resume(args: argparse.Namespace, flow: Workflow) -> int:
    _print_json(flow.resume(args.session_id))
    return EXIT_OK


def _amend(args: argparse.Namespace, flow: Workflow) -> int:
    result = flow.amend(args.session_id, _load_json(args.amendment, "amendment"))
    print(f"amended {args.session_id}")
    print(f"  old        {result.record['old_digest']}")
    print(f"  new        {result.record['new_digest']}")
    print(f"  by         {result.record['requested_by']}: {result.record['reason']}")
    snap = flow.snapshot(args.session_id)
    for note in snap.post_divergence:
        print(f"  post-divergence: {note}")
    for note in snap.weakening:
        print(f"  weakening: {note}")
    return EXIT_OK


def _list(args: argparse.Namespace, flow: Workflow) -> int:
    rows = flow.sessions()
    if not rows:
        print("no assurance sessions yet")
        return EXIT_OK
    for row in rows:
        print(f"{row['session_id']:<30} {row['kind']:<8} {row['state']}")
    return EXIT_OK


# --------------------------------------------------------------------------
# assure


def _assure_init(args: argparse.Namespace, flow: Workflow) -> int:
    manifest = _manifest(args.manifest)
    extra = {"run_workspace": str(Path(args.workspace).resolve())} if args.workspace else {}
    snap = flow.create(manifest, {"SOURCE_ROOT": args.source_root, "TARGET_ROOT": args.target_root}, extra=extra)
    print(f"session {snap.session_id}  {snap.state}")
    print(f"  manifest   {snap.manifest_digest}")
    print(f"  source     {snap.roots['SOURCE_ROOT']}")
    print(f"  target     {snap.roots['TARGET_ROOT']}")
    return EXIT_OK


def _assure_compare(args: argparse.Namespace, flow: Workflow) -> int:
    result = flow.compare(args.session_id)
    _print_claim(result)
    return _claim_exit(result.status)


def _assure_search(args: argparse.Namespace, flow: Workflow) -> int:
    result = flow.search(args.session_id, seed=args.seed, runs=args.runs, seconds=args.seconds)
    _print_claim(result)
    return _claim_exit(result.status)


def _assure_prove(args: argparse.Namespace, flow: Workflow) -> int:
    result = flow.prove(args.session_id)
    _print_claim(result)
    return _claim_exit(result.status)


def _assure_performance(args: argparse.Namespace, flow: Workflow) -> int:
    result = flow.performance(args.session_id)
    _print_claim(result)
    protocol = (result.coverage or {}).get("protocol", {})
    if protocol:
        print(f"  protocol: {exact_json.dumps(protocol, sort_keys=True)}")
    return _claim_exit(result.status)


def _assure_complete(args: argparse.Namespace, flow: Workflow) -> int:
    snap = flow.complete(args.session_id)
    print(f"session {snap.session_id}  {snap.state}")
    if snap.final_verdict:
        print(f"  verdict    {snap.final_verdict.get('status')} (decided by {snap.final_verdict.get('decided_by')})")
    print(f"  report     {snap.report_digest}")
    return EXIT_OK


# --------------------------------------------------------------------------
# repair


def _repair_init(args: argparse.Namespace, flow: Workflow) -> int:
    manifest = _manifest(args.manifest)
    snap = flow.repair_init(args.repo, manifest, workspace=args.workspace)
    print(f"session {snap.session_id}  {snap.state}")
    print(f"  repository {snap.repository}")
    print(f"  base       {snap.base_commit}")
    print(f"  baseline   {snap.roots['SOURCE_ROOT']}")
    print(f"  manifest   {snap.manifest_digest}")
    return EXIT_OK


def _repair_analyze(args: argparse.Namespace, flow: Workflow) -> int:
    findings = _load_json(args.findings, "findings") if args.findings else []
    result = flow.analyze(args.session_id, findings)
    metrics = result["metrics"]
    print(f"session {args.session_id}  ANALYZING")
    print(f"  files      {metrics['files']}  lines {metrics['lines']}")
    print(f"  duplicates {metrics['duplicate_blocks']['count']} window(s) of {metrics['window']} line(s)")
    print(f"  cycles     {len(metrics['python'].get('cycles', []))}")
    print(f"  findings   {len(result['findings'])} recorded")
    return EXIT_OK


def _repair_plan(args: argparse.Namespace, flow: Workflow) -> int:
    units = _load_json(args.plan, "plan")
    if isinstance(units, dict) and "units" in units:
        units = units["units"]
    snap = flow.plan(args.session_id, units)
    print(f"session {args.session_id}  {snap.state}")
    for unit in snap.plan:
        print(f"  unit {unit['id']:<12} {unit.get('objective', '')}")
    return EXIT_OK


def _repair_unit_start(args: argparse.Namespace, flow: Workflow) -> int:
    root = flow.unit_start(args.session_id, args.unit_id)
    snap = flow.snapshot(args.session_id)
    _print_json({"session_id": args.session_id, "unit_id": args.unit_id, "worktree": str(root), "state": snap.state, "base_commit": snap.accepted_commit})
    return EXIT_OK


def _repair_unit_verify(args: argparse.Namespace, flow: Workflow) -> int:
    verdict = flow.unit_verify(args.session_id, args.unit_id)
    snap = flow.snapshot(args.session_id)
    unit = snap.units.get(args.unit_id, {})
    print(f"unit {args.unit_id}  tree {unit.get('tree')}")
    for result in unit.get("claim_results", []):
        _print_claim(claims.ClaimResult.from_dict(result))
    _print_verdict(verdict)
    return _verdict_exit(verdict.status)


def _repair_unit_accept(args: argparse.Namespace, flow: Workflow) -> int:
    commit = flow.unit_accept(args.session_id, args.unit_id, reviewed_by=args.reviewed_by)
    print(f"accepted {args.unit_id} as {commit}")
    return EXIT_OK


def _repair_unit_reject(args: argparse.Namespace, flow: Workflow) -> int:
    record = flow.unit_reject(args.session_id, args.unit_id, reason=args.reason)
    snap = flow.snapshot(args.session_id)
    print(f"rejected {args.unit_id}  {snap.state}")
    print(f"  patch      {record['patch_path']}")
    print(f"  digest     {record['patch_digest']}")
    print(f"  accepted   {snap.accepted_commit} (unchanged)")
    return EXIT_OK


def _repair_continue(args: argparse.Namespace, flow: Workflow) -> int:
    snap = flow.continue_(args.session_id)
    print(f"session {args.session_id}  {snap.state}")
    return EXIT_OK


def _repair_finish(args: argparse.Namespace, flow: Workflow) -> int:
    snap = flow.finish(args.session_id)
    print(f"session {args.session_id}  {snap.state}")
    if snap.final_verdict:
        print(f"  verdict    {snap.final_verdict.get('status')} (decided by {snap.final_verdict.get('decided_by')})")
    print(f"  accepted   {snap.accepted_commit}")
    print(f"  branch     invara/repair/{args.session_id}")
    print(f"  report     {snap.report_digest}")
    return EXIT_OK


# --------------------------------------------------------------------------
# registration


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(store.DEFAULT_PATH), help="evidence database (shared with the verifier)")


def _session(parser: argparse.ArgumentParser) -> None:
    _common(parser)
    parser.add_argument("session_id")


def register(subparsers: Any) -> None:
    assure = subparsers.add_parser("assure", help="compare an existing BEFORE and AFTER under an equivalence manifest")
    assure_sub = assure.add_subparsers(dest="assure_command", required=True)

    p = assure_sub.add_parser("run", help="one shot: init, characterize, freeze, every declared claim, verify, report (resumable)")
    _common(p)
    p.add_argument("--manifest", required=True)
    p.add_argument("--source-root", required=True)
    p.add_argument("--target-root", required=True)
    p.add_argument("--workspace")
    p.add_argument("--runs", type=int)
    p.add_argument("--json")
    p.add_argument("--md")
    p.set_defaults(func=_guarded(_assure_run))

    p = assure_sub.add_parser("export", help="write the session's evidence as one self-contained package")
    _session(p)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_guarded(_export))

    p = assure_sub.add_parser("inspect", help="recompute a package's content addresses, comparisons and verdict with nothing but the package")
    p.add_argument("package")
    p.set_defaults(func=_inspect)

    p = assure_sub.add_parser("init", help="create an assurance session from a manifest")
    _common(p)
    p.add_argument("--manifest", required=True)
    p.add_argument("--source-root", required=True)
    p.add_argument("--target-root", required=True)
    p.add_argument("--workspace", help="where per-run workspaces are created (default: system temp)")
    p.set_defaults(func=_guarded(_assure_init))

    p = assure_sub.add_parser("characterize", help="capture the baseline from the source")
    _session(p)
    p.add_argument("--runs", type=int, default=None, help="repeat each input to assess stability")
    p.set_defaults(func=_guarded(_characterize))

    p = assure_sub.add_parser("freeze", help="freeze the baseline envelope")
    _session(p)
    p.set_defaults(func=_guarded(_freeze))

    p = assure_sub.add_parser("compare", help="run the target over the corpus against the frozen baseline")
    _session(p)
    p.set_defaults(func=_guarded(_assure_compare))

    p = assure_sub.add_parser("search", help="search for a counterexample")
    _session(p)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--runs", type=int, default=None)
    p.add_argument("--seconds", type=float, default=None)
    p.set_defaults(func=_guarded(_assure_search))

    p = assure_sub.add_parser("prove", help="exhaustively compare a declared finite domain")
    _session(p)
    p.set_defaults(func=_guarded(_assure_prove))

    p = assure_sub.add_parser("performance", help="measure wall-clock on both systems under the declared protocol")
    _session(p)
    p.set_defaults(func=_guarded(_assure_performance))

    p = assure_sub.add_parser("amend", help="apply an explicit manifest amendment")
    _session(p)
    p.add_argument("--amendment", required=True)
    p.set_defaults(func=_guarded(_amend))

    p = assure_sub.add_parser("verify", help="compute the final verdict; exit codes follow it")
    _session(p)
    p.add_argument("--require", choices=["PASS", "HUMAN_REVIEW"], default=None, help="exit 1 unless the verdict is exactly this")
    p.set_defaults(func=_guarded(_verify))

    p = assure_sub.add_parser("report", help="write the two-layer report")
    _session(p)
    p.add_argument("--json", default=None)
    p.add_argument("--md", default=None)
    p.set_defaults(func=_guarded(_report))

    p = assure_sub.add_parser("status", help="the session snapshot as JSON")
    _session(p)
    p.set_defaults(func=_guarded(_status))

    p = assure_sub.add_parser("coverage", help="the assurance coverage map: the state of every observed value and why")
    _session(p)
    p.set_defaults(func=_guarded(_coverage))

    p = assure_sub.add_parser("resume", help="re-validate a session and say what comes next")
    _session(p)
    p.set_defaults(func=_guarded(_resume))

    p = assure_sub.add_parser("complete", help="record the final verdict and report")
    _session(p)
    p.set_defaults(func=_guarded(_assure_complete))

    p = assure_sub.add_parser("list", help="every assurance session and its state")
    _common(p)
    p.set_defaults(func=_guarded(_list))

    repair = subparsers.add_parser("repair", help="govern a repair session over a git repository, one unit at a time")
    repair_sub = repair.add_subparsers(dest="repair_command", required=True)

    p = repair_sub.add_parser("init", help="start a repair session from a clean repository")
    _common(p)
    p.add_argument("--repo", default=".")
    p.add_argument("--manifest", required=True)
    p.add_argument("--workspace", default=None, help="where baseline and unit worktrees live (default: a sibling of the repository)")
    p.set_defaults(func=_guarded(_repair_init))

    p = repair_sub.add_parser("characterize", help="capture the baseline from the base commit")
    _session(p)
    p.add_argument("--runs", type=int, default=None)
    p.set_defaults(func=_guarded(_characterize))

    p = repair_sub.add_parser("freeze", help="freeze the baseline envelope")
    _session(p)
    p.set_defaults(func=_guarded(_freeze))

    p = repair_sub.add_parser("analyze", help="measure the baseline tree and record declared findings")
    _session(p)
    p.add_argument("--findings", default=None, help="JSON list of findings declared by the host agent")
    p.set_defaults(func=_guarded(_repair_analyze))

    p = repair_sub.add_parser("plan", help="record the repair plan (one unit at a time from here on)")
    _session(p)
    p.add_argument("--plan", required=True, help="JSON list of units")
    p.set_defaults(func=_guarded(_repair_plan))

    p = repair_sub.add_parser("unit-start", help="open a disposable worktree for one unit")
    _session(p)
    p.add_argument("unit_id")
    p.set_defaults(func=_guarded(_repair_unit_start))

    p = repair_sub.add_parser("unit-verify", help="verify the unit worktree against the frozen baseline")
    _session(p)
    p.add_argument("unit_id")
    p.set_defaults(func=_guarded(_repair_unit_verify))

    p = repair_sub.add_parser("unit-finish", help="verify the unit and accept it only on PASS; otherwise leave it open and say what comes next")
    _session(p)
    p.add_argument("unit_id")
    p.set_defaults(func=_guarded(_unit_finish))

    p = repair_sub.add_parser("export", help="write the session's evidence as one self-contained package")
    _session(p)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_guarded(_export))

    p = repair_sub.add_parser("unit-accept", help="promote the verified tree into the accepted state")
    _session(p)
    p.add_argument("unit_id")
    p.add_argument("--reviewed-by", default=None, help="a person's name; required to accept a HUMAN_REVIEW unit")
    p.set_defaults(func=_guarded(_repair_unit_accept))

    p = repair_sub.add_parser("unit-reject", help="keep the patch as evidence and discard the unit worktree")
    _session(p)
    p.add_argument("unit_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_guarded(_repair_unit_reject))

    p = repair_sub.add_parser("continue", help="return to the plan after a unit was accepted or rolled back")
    _session(p)
    p.set_defaults(func=_guarded(_repair_continue))

    p = repair_sub.add_parser("finish", help="publish the accepted state as a branch and record the report")
    _session(p)
    p.set_defaults(func=_guarded(_repair_finish))

    p = repair_sub.add_parser("verify", help="compute the session's final verdict")
    _session(p)
    p.add_argument("--require", choices=["PASS", "HUMAN_REVIEW"], default=None)
    p.set_defaults(func=_guarded(_verify))

    p = repair_sub.add_parser("report", help="write the two-layer report")
    _session(p)
    p.add_argument("--json", default=None)
    p.add_argument("--md", default=None)
    p.set_defaults(func=_guarded(_report))

    p = repair_sub.add_parser("status", help="the session snapshot as JSON")
    _session(p)
    p.set_defaults(func=_guarded(_status))

    p = repair_sub.add_parser("coverage", help="the assurance coverage map: the state of every observed value and why")
    _session(p)
    p.set_defaults(func=_guarded(_coverage))

    p = repair_sub.add_parser("resume", help="re-validate the session and the open unit's worktree")
    _session(p)
    p.set_defaults(func=_guarded(_resume))

    p = repair_sub.add_parser("amend", help="apply an explicit manifest amendment")
    _session(p)
    p.add_argument("--amendment", required=True)
    p.set_defaults(func=_guarded(_amend))

    p = repair_sub.add_parser("list", help="every session and its state")
    _common(p)
    p.set_defaults(func=_guarded(_list))
