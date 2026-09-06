"""Orchestration: the steps of a session, each recorded before the next.

Everything the CLI and the plugin commands do goes through here, and
nothing here trusts its caller. A step checks the session's recorded state
before it runs, records what it did as an event, and returns what the
evidence now says. There is no argument anywhere by which a caller asserts
an outcome: ``unit_accept`` reads the verdict ``unit_verify`` stored for
the exact tree it verified, and the governor refuses if the tree has moved.

Two kinds of session share the machinery:

* **assure** — an existing before and after; the caller names both roots.
* **repair** — a git repository; the governor keeps the accepted state and
  every unit's disposable worktree, and the target of each verification is
  that unit's worktree.
"""

from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import analysis, claims, session, identity
from . import proof as proof_module
from . import report as report_module
from . import search as search_module
from .compare import Comparison
from . import coverage as coverage_module
from .engine import BaselineCapture, Engine, EngineError, Frozen, StoredComparison, declared_inputs, preflight
from .execute import matches_include
from .evidence import Evidence, EvidenceError
from .governor import Governor, GovernorError
from .manifest import AmendmentResult, CorpusItem, Manifest, ManifestError, content_digest, exclusions_covering, policies_covering, ref_safe_id
from .manifest import amend as amend_manifest

__all__ = ["NEXT_STEPS", "Workflow", "WorkflowError"]

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

NEXT_STEPS: dict[str, dict[str, str]] = {
    "CREATED": {"assure": "characterize", "repair": "characterize"},
    "BASELINE_CAPTURING": {"assure": "freeze", "repair": "freeze"},
    "BASELINE_FROZEN": {"assure": "compare, then search and/or prove, then verify", "repair": "analyze"},
    "ANALYZING": {"assure": "-", "repair": "plan"},
    "PLANNED": {"assure": "-", "repair": "unit-start <unit id>, or finish"},
    "UNIT_PREPARING": {"assure": "-", "repair": "edit the unit worktree, then unit-verify"},
    "UNIT_IN_PROGRESS": {"assure": "-", "repair": "edit the unit worktree, then unit-verify"},
    "UNIT_VERIFYING": {"assure": "-", "repair": "unit-accept (only on PASS) or unit-reject"},
    "UNIT_ACCEPTED": {"assure": "-", "repair": "continue (next unit) or finish"},
    "UNIT_REJECTED": {"assure": "-", "repair": "the unit is being rolled back; run resume"},
    "UNIT_ROLLED_BACK": {"assure": "-", "repair": "continue (next unit) or finish"},
    "COMPLETED": {"assure": "report", "repair": "report"},
    "BLOCKED": {"assure": "read the blocked reason; start a new session", "repair": "read the blocked reason; start a new session"},
}


class WorkflowError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _tool_versions() -> dict[str, str]:
    from .. import __version__ as invara_version
    return {"python": platform.python_version(), "invara": invara_version, "git": "see provenance.git"}


class Workflow:
    def __init__(
        self,
        evidence: Evidence,
        *,
        workspace_parent: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        git: str = "git",
        seed: int = 0,
    ) -> None:
        self.evidence = evidence
        self.clock = clock
        self.git = git
        self._default_workspace = Path(workspace_parent) if workspace_parent is not None else None
        self.engine = Engine(evidence, workspace_parent=workspace_parent, clock=clock, seed=seed)
        self.governor: Governor | None = None
        self._governors: dict[str, Governor] = {}

    # ---------------------------------------------------------------- state

    def snapshot(self, session_id: str) -> session.Snapshot:
        try:
            events = self.evidence.events(session_id)
        except EvidenceError as error:
            raise WorkflowError("store_corrupt", str(error)) from None
        if not events:
            raise WorkflowError("no_session", f"no session named {session_id!r} in {self.evidence.path}")
        try:
            snap = session.reduce(events, session_id=session_id)
            identity.assert_current(snap.producer_identity)
            return snap
        except identity.IdentityError as error:
            raise WorkflowError(error.reason, str(error)) from None
        except session.SessionCorrupt as error:
            raise WorkflowError("session_corrupt", str(error)) from None

    def manifest(self, session_id: str, snapshot: session.Snapshot | None = None) -> Manifest:
        snap = snapshot or self.snapshot(session_id)
        try:
            return self.evidence.load_manifest(snap.manifest_digest)
        except KeyError:
            raise WorkflowError("store_corrupt", f"manifest {snap.manifest_digest} is missing from the evidence store") from None

    def _check(self, snap: session.Snapshot, event: str) -> str:
        destination = session.EVENT_STATES[event] or snap.state
        if event == "exported":
            # an export is a read that leaves a trace: allowed in every state, terminal ones included; it moves nothing
            return destination
        if event == "resumed":
            if snap.state in session.TERMINAL:
                raise WorkflowError("invalid_transition", f"session is {snap.state}")
            return destination
        try:
            session.check_transition(snap.state, destination)
        except session.InvalidTransition as error:
            raise WorkflowError("invalid_transition", f"{event} from {snap.state}: {error}") from None
        return destination

    def _record(self, session_id: str, event: str, payload: Mapping[str, Any], *, snap: session.Snapshot | None = None) -> session.Snapshot:
        """Append an event from the state the caller saw, after checking it is still the state on record.

        A step captures its snapshot, works (a search can take a minute),
        then records. If another process moved the session meanwhile, the
        event would carry a from-state the history contradicts and the
        reducer would refuse the session for good; so the store is read
        again first and a moved session is refused without appending. The
        window between that read and the append is not closed; two
        processes driving one session are not supported (THREAT_MODEL.md).
        """

        snap = snap or self.snapshot(session_id)
        destination = self._check(snap, event)
        current = self.snapshot(session_id)
        if (current.state, current.current_unit, current.manifest_digest) != (snap.state, snap.current_unit, snap.manifest_digest):
            raise WorkflowError(
                "stale_session",
                f"session {session_id!r} moved from {snap.state} to {current.state} while this step ran; nothing was recorded, read the session again",
            )
        self.evidence.append_event(session_id, event, snap.state, destination, dict(payload), at=self.clock())
        return self.snapshot(session_id)

    def sessions(self) -> list[dict[str, Any]]:
        out = []
        for session_id in self.evidence.sessions():
            try:
                snap = self.snapshot(session_id)
                out.append({"session_id": session_id, "kind": snap.kind, "state": snap.state, "manifest_digest": snap.manifest_digest})
            except WorkflowError as error:
                out.append({"session_id": session_id, "kind": "?", "state": "CORRUPT", "error": str(error)})
        return out

    def _engine_for(self, snap: session.Snapshot) -> Engine:
        """The engine, running in the workspace this session recorded at creation.

        One engine serves every session of a workflow, so its workspace is
        set for the session at hand on every call: the session's own, or
        the workflow's default when the session recorded none.
        """

        self.engine.workspace_parent = Path(snap.run_workspace) if snap.run_workspace else self._default_workspace
        return self.engine

    def status(self, session_id: str) -> dict[str, Any]:
        snap = self.snapshot(session_id)
        data = snap.as_dict()
        data["next"] = NEXT_STEPS.get(snap.state, {}).get(snap.kind or "assure", "-")
        return data

    # --------------------------------------------------------------- create

    @staticmethod
    def _check_roots(roots: Mapping[str, str]) -> dict[str, str]:
        """Both roots named and directories; returns them resolved, the form the session records and compares."""

        resolved: dict[str, str] = {}
        for name in ("SOURCE_ROOT", "TARGET_ROOT"):
            if name not in roots:
                raise WorkflowError("root_missing", f"{name} must be given")
            if not Path(roots[name]).is_dir():
                raise WorkflowError("root_missing", f"{name} {roots[name]} is not a directory")
            resolved[name] = str(Path(roots[name]).resolve())
        return resolved

    def create(self, manifest: Manifest, roots: Mapping[str, str], *, kind: str = "assure", extra: Mapping[str, Any] | None = None) -> session.Snapshot:
        session_id = manifest.session_id
        if self.evidence.events(session_id):
            raise WorkflowError("session_exists", f"session {session_id!r} already has a history; resume it or pick another id")
        self._check_roots(roots)
        try:
            producer = identity.capture()
        except identity.IdentityError as error:
            raise WorkflowError(error.reason, str(error)) from None
        self.evidence.record_manifest(manifest, at=self.clock())
        payload: dict[str, Any] = {"kind": kind, "manifest_digest": manifest.digest(), "roots": {k: str(Path(v).resolve()) for k, v in roots.items()}}
        payload.update(dict(extra or {}))
        payload["producer_identity"] = producer
        self.evidence.append_event(session_id, "created", "", "CREATED", payload, at=self.clock())
        return self.snapshot(session_id)

    # ------------------------------------------------------------ baseline

    def characterize(self, session_id: str, *, runs: int | None = None) -> BaselineCapture:
        snap = self.snapshot(session_id)
        self._check(snap, "baseline_capture")
        if snap.captures:
            raise WorkflowError("already_characterized", f"session {session_id!r} already holds a baseline capture; freeze it, or start a new session to capture again")
        manifest = self.manifest(session_id, snap)
        engine = self._engine_for(snap)
        requirement = next((claim for claim in manifest.claims if claim.kind == "baseline_stability"), None)
        needed = self._characterize_needed(manifest)
        if runs is not None and runs < needed:
            raise WorkflowError("insufficient_runs", f"the {requirement.id} claim needs {needed} run(s) per input and {runs} were requested; characterize runs once per session")
        wanted = self._characterize_runs(manifest, runs)
        self._preflight(manifest, "characterize", runs=wanted)
        try:
            capture = engine.characterize(session_id, manifest, snap.roots, runs=wanted)
        except EngineError as error:
            raise WorkflowError(error.reason, error.detail) from None
        snap = self._record(session_id, "baseline_capture", {"capture": capture.as_dict()}, snap=snap)
        if requirement is not None:
            result = self._stability_result(requirement, manifest, capture)
            self._record(session_id, "claim_recorded", {"result": result.as_dict()}, snap=snap)
        return capture

    @staticmethod
    def _characterize_needed(manifest: Manifest) -> int:
        requirement = next((claim for claim in manifest.claims if claim.kind == "baseline_stability"), None)
        return max(2, int(requirement.params.get("runs", 2))) if requirement is not None else 1

    def _characterize_runs(self, manifest: Manifest, runs: int | None) -> int:
        """The runs per input a characterize will start: the caller's number, else the budget and the stability claim decide."""

        return runs if runs is not None else max(int(manifest.budgets.stability_runs), self._characterize_needed(manifest))

    @staticmethod
    def _stability_result(requirement: Any, manifest: Manifest, capture: BaselineCapture) -> claims.ClaimResult:
        """What repeated runs of the source say, as a claim nobody can accept for it."""

        common = dict(
            claim_id=requirement.id,
            kind=requirement.kind,
            mandatory=requirement.mandatory,
            manifest_digest=manifest.digest(),
            baseline_digest="",
            coverage={
                "kind": "stability",
                "runs": capture.runs,
                "inputs": len(capture.inputs),
                "volatile_paths": list(capture.volatile_paths),
                "uncovered_volatile": list(capture.uncovered_volatile),
                "proposals": len(capture.proposals),
            },
        )
        wanted = int(requirement.params.get("runs", 2))
        if capture.runs < max(2, wanted):
            return claims.ClaimResult(status=claims.UNVERIFIABLE, unverified=(f"stability needs at least {max(2, wanted)} run(s) per input; the baseline was captured with {capture.runs}",), **common)
        if capture.problems:
            return claims.ClaimResult(status=claims.UNVERIFIABLE, unverified=tuple(capture.problems[:5]), **common)
        if capture.uncovered_volatile:
            return claims.ClaimResult(
                status=claims.HUMAN_REVIEW,
                detail=f"{len(capture.uncovered_volatile)} path(s) vary between runs and no accepted policy covers them: " + ", ".join(capture.uncovered_volatile[:5]),
                **common,
            )
        return claims.ClaimResult(
            status=claims.NO_DIVERGENCE_FOUND,
            detail=f"{len(capture.volatile_paths)} volatile path(s) over {capture.runs} run(s), every one covered by an accepted policy" if capture.volatile_paths else f"no path varied over {capture.runs} run(s)",
            **common,
        )

    def freeze(self, session_id: str) -> Frozen:
        snap = self.snapshot(session_id)
        self._check(snap, "baseline_frozen")
        if snap.frozen:
            raise WorkflowError("already_frozen", f"session {session_id!r} is frozen at baseline {str(snap.baseline_digest)[:12]}; a baseline is frozen once")
        manifest = self.manifest(session_id, snap)
        engine = self._engine_for(snap)
        try:
            frozen = engine.freeze(session_id, manifest)
        except EngineError as error:
            raise WorkflowError(error.reason, error.detail) from None
        # the equivalence definition is measured as soon as it is fixed
        engine.sensitivity(session_id, manifest)
        self._record(session_id, "baseline_frozen", {"frozen": frozen.as_dict()}, snap=snap)
        return frozen

    def _frozen(self, session_id: str, snap: session.Snapshot) -> Frozen:
        frozen = self.engine.frozen(session_id)
        if frozen is None or snap.frozen is None:
            raise WorkflowError("not_frozen", f"session {session_id!r} has no frozen baseline; run characterize and freeze first")
        if frozen.baseline_digest != snap.baseline_digest:
            raise WorkflowError("store_corrupt", "the stored freeze does not match the session's recorded baseline digest")
        return frozen

    # -------------------------------------------------------------- claims

    def _requirement(self, manifest: Manifest, kind: str, reason: str) -> Any:
        requirement = next((claim for claim in manifest.claims if claim.kind == kind), None)
        if requirement is None:
            raise WorkflowError(reason, f"the manifest declares no {kind} claim")
        return requirement

    @staticmethod
    def _preflight(manifest: Manifest, phase: str, *, runs: int | None = None) -> dict[str, Any]:
        """Refuse work the manifest has not authorised, before the first execution."""

        estimate = preflight(manifest, phase=phase, runs=runs)
        if not estimate["within"]:
            raise WorkflowError("budget_exceeded", estimate["detail"] + "; raise budgets.max_planned_runs in the manifest to authorise this work")
        return estimate

    def _evaluate_corpus(self, session_id: str, manifest: Manifest, frozen: Frozen, roots: Mapping[str, str]) -> claims.ClaimResult:
        self._preflight(manifest, "compare")
        try:
            return self.engine.compare_corpus(session_id, manifest, frozen, dict(roots))
        except EngineError as error:
            raise WorkflowError(error.reason, error.detail) from None

    def _evaluate_search(
        self, session_id: str, manifest: Manifest, frozen: Frozen, roots: Mapping[str, str], *, seed: int | None, runs: int | None, seconds: float | None
    ) -> claims.ClaimResult:
        requirement = self._requirement(manifest, "counterexample_search", "no_search_claim")
        params = requirement.params
        max_runs = runs if runs is not None else int(params.get("runs", manifest.budgets.search_runs))
        # the estimate must count the runs this search will really start, override included
        self._preflight(manifest, "search", runs=max_runs)
        max_seconds = seconds if seconds is not None else float(params.get("seconds", manifest.budgets.search_seconds))
        the_seed = seed if seed is not None else int(params.get("seed", self.engine.seed))
        found: dict[str, Comparison] = {}
        # every comparison the search makes, by the address the store gave it: the claim names them all
        addresses: list[str] = []

        def evaluate(item: CorpusItem) -> StoredComparison:
            stored = self.engine.differential(session_id, manifest, item, dict(roots), phase="search")
            addresses.append(stored.address)
            if stored.status == "compared" and not stored.mandatory_equivalent:
                found[item.id] = stored.comparison
            return stored

        result = search_module.search(
            declared_inputs(manifest),
            evaluate,
            seed=the_seed,
            max_runs=max_runs,
            max_seconds=max_seconds,
            shrink_steps=manifest.budgets.shrink_steps,
            sequence_of_operations=manifest.input_domain.delivery == "http",
        )
        coverage = {
            "kind": "search",
            "runs": result.runs,
            "compared_runs": result.compared_runs,
            "unverifiable_runs": result.unverifiable_runs,
            "shrink_runs": result.shrink_runs,
            "seed": the_seed,
            "corpus_size": result.corpus_size,
            "trace_digest": result.trace_digest,
            "elapsed_s": result.elapsed_s,
        }
        common = dict(
            claim_id=requirement.id,
            kind=requirement.kind,
            mandatory=requirement.mandatory,
            manifest_digest=manifest.digest(),
            baseline_digest=frozen.baseline_digest,
            coverage=coverage,
            detail=result.reason,
            evidence_digests=tuple(addresses),
        )
        if result.status == claims.DIVERGED and result.original is not None:
            comparison = found.get(result.original["id"])
            divergences = tuple(dict(d.as_dict(), input_id=result.original["id"]) for d in comparison.divergences) if comparison else ({"path": "?", "input_id": result.original["id"]},)
            counterexample = {"original": result.original, "minimized": result.minimized, "seed": the_seed, "trace_digest": result.trace_digest, "runs": result.runs, "shrink_runs": result.shrink_runs}
            return claims.ClaimResult(status=claims.DIVERGED, divergences=divergences, counterexample=counterexample, **common)
        if result.status == claims.UNVERIFIABLE:
            return claims.ClaimResult(status=claims.UNVERIFIABLE, unverified=(result.reason,), **common)
        return claims.ClaimResult(status=claims.NO_DIVERGENCE_FOUND, **common)

    def _evaluate_proof(self, session_id: str, manifest: Manifest, frozen: Frozen, roots: Mapping[str, str]) -> claims.ClaimResult:
        requirement = self._requirement(manifest, "finite_domain_proof", "no_proof_claim")
        self._preflight(manifest, "prove")
        finite = manifest.input_domain.finite
        if finite is None:
            raise WorkflowError("no_finite_domain", "the manifest's input domain is not finite")
        found: dict[str, Comparison] = {}
        not_compared: dict[str, tuple[str, ...]] = {}

        def evaluate(item: CorpusItem) -> StoredComparison:
            # the outcome's digest is the address of the stored comparison record: what the proof keeps per member;
            # a member the frozen baseline holds no record for is compared against nothing and stores nothing
            stored = self.engine.compare_against_baseline(session_id, manifest, frozen, item, dict(roots), phase="proof")
            if stored.status == "compared" and not stored.mandatory_equivalent:
                found[item.id] = stored.comparison
            if stored.status != "compared":
                not_compared[item.id] = tuple(stored.problems)
            return stored

        result = proof_module.prove(finite, evaluate, max_members=manifest.budgets.finite_max_members)
        # every member's comparison record, in member order, none cut: the evidence is the proof (a member compared
        # against nothing has no record to name)
        evidence = tuple(digest for member_id in sorted(result.member_digests) if (digest := result.member_digests[member_id]))
        coverage = {
            "kind": "exhaustive" if result.status == claims.PROVED_WITHIN_DECLARED_DOMAIN else "partial",
            "members": result.members_compared,
            "cardinality": result.cardinality,
            "domain_digest": result.domain_digest,
            "backend": result.backend,
            "member_digests_digest": content_digest(result.member_digests),
            "elapsed_s": result.elapsed_s,
        }
        common = dict(
            claim_id=requirement.id,
            kind=requirement.kind,
            mandatory=requirement.mandatory,
            manifest_digest=manifest.digest(),
            baseline_digest=frozen.baseline_digest,
            coverage=coverage,
            detail=result.reason,
            evidence_digests=evidence,
        )
        if result.status == claims.DIVERGED and result.counterexample is not None:
            comparison = found.get(str(result.counterexample["id"]))
            divergences = tuple(dict(d.as_dict(), input_id=result.counterexample["id"]) for d in comparison.divergences) if comparison else ({"path": "?", "input_id": result.counterexample["id"]},)
            return claims.ClaimResult(status=claims.DIVERGED, divergences=divergences, counterexample={"minimized": {"id": result.counterexample["id"], "input": result.counterexample["input"]}, "diverging_count": result.diverging_count}, **common)
        if result.status == claims.UNVERIFIABLE:
            reasons = [f"{member_id}: {'; '.join(problems) or 'could not be compared'}" for member_id, problems in sorted(not_compared.items())]
            return claims.ClaimResult(status=claims.UNVERIFIABLE, unverified=(result.reason, *reasons[:4]), **common)
        # before the word "proved" is recorded, the evidence on record is held to it: every member bound, by the
        # digest of its exact input, to the source and target executions its comparison was made from, and its
        # source execution is the member's frozen baseline record, never a run made after the freeze
        unbound = proof_module.binding_problems(
            finite,
            evidence,
            self.engine.records_by_digest(session_id).get,
            source_id=manifest.source_system.id,
            target_id=manifest.target_system.id,
            label=requirement.id,
            max_members=manifest.budgets.finite_max_members,
            frozen_record_digests=frozen.record_digests,
        )
        if unbound:
            common.update(coverage=dict(coverage, kind="partial"), detail="the evidence on record does not bind every member to its own executions; no proof is issued")
            return claims.ClaimResult(status=claims.UNVERIFIABLE, unverified=tuple(unbound[:5]), **common)
        return claims.ClaimResult(status=claims.PROVED_WITHIN_DECLARED_DOMAIN, **common)

    def _evaluate_performance(self, session_id: str, manifest: Manifest, frozen: Frozen, roots: Mapping[str, str]) -> claims.ClaimResult:
        requirement = self._requirement(manifest, "performance_envelope", "no_performance_claim")
        params = requirement.params
        # the estimate must count the runs this measurement will really start, on both systems, for every input
        self._preflight(manifest, "performance", runs=int(params.get("runs", manifest.performance.runs)))
        try:
            return self.engine.measure_performance(
                session_id,
                manifest,
                frozen,
                dict(roots),
                runs=int(params.get("runs", manifest.performance.runs)),
                rel_tolerance=float(params.get("rel_tolerance", manifest.performance.rel_tolerance)),
                abs_tolerance_s=float(params.get("abs_tolerance_s", manifest.performance.abs_tolerance_s)),
            )
        except EngineError as error:
            raise WorkflowError(error.reason, error.detail) from None

    def _assure_ready(self, session_id: str) -> tuple[session.Snapshot, Manifest, Frozen]:
        snap = self.snapshot(session_id)
        if snap.kind == "repair":
            raise WorkflowError("repair_session", "claims of a repair session are evaluated by unit-verify")
        if snap.state != "BASELINE_FROZEN":
            raise WorkflowError("not_frozen", f"session {session_id!r} is {snap.state}; claims are evaluated on a frozen baseline")
        manifest = self.manifest(session_id, snap)
        self._engine_for(snap)
        return snap, manifest, self._frozen(session_id, snap)

    def compare(self, session_id: str) -> claims.ClaimResult:
        snap, manifest, frozen = self._assure_ready(session_id)
        result = self._evaluate_corpus(session_id, manifest, frozen, snap.roots)
        self._record(session_id, "claim_recorded", {"result": result.as_dict()}, snap=snap)
        return result

    def search(self, session_id: str, *, seed: int | None = None, runs: int | None = None, seconds: float | None = None) -> claims.ClaimResult:
        snap, manifest, frozen = self._assure_ready(session_id)
        result = self._evaluate_search(session_id, manifest, frozen, snap.roots, seed=seed, runs=runs, seconds=seconds)
        self._record(session_id, "claim_recorded", {"result": result.as_dict()}, snap=snap)
        return result

    def prove(self, session_id: str) -> claims.ClaimResult:
        snap, manifest, frozen = self._assure_ready(session_id)
        result = self._evaluate_proof(session_id, manifest, frozen, snap.roots)
        self._record(session_id, "claim_recorded", {"result": result.as_dict()}, snap=snap)
        return result

    def performance(self, session_id: str) -> claims.ClaimResult:
        snap, manifest, frozen = self._assure_ready(session_id)
        result = self._evaluate_performance(session_id, manifest, frozen, snap.roots)
        self._record(session_id, "claim_recorded", {"result": result.as_dict()}, snap=snap)
        return result

    # ------------------------------------------------------------ one shot

    def _preflight_run(self, session_id: str, snap: session.Snapshot, runs: int | None) -> None:
        """Estimate every phase this run will start, together, before its first execution.

        Each phase also preflights itself; a one-shot that fits step by step
        but not as a whole would otherwise start several budgets' worth of
        executions from one command.
        """

        manifest = self.manifest(session_id, snap)
        phases: list[tuple[str, int | None]] = []
        if snap.state == "CREATED":
            phases.append(("characterize", self._characterize_runs(manifest, runs)))
        if snap.state in ("CREATED", "BASELINE_CAPTURING", "BASELINE_FROZEN"):
            active = {
                str(r.get("claim_id"))
                for r in snap.active_claim_results()
                if r.get("baseline_digest") in ("", snap.baseline_digest) and r.get("status") != claims.UNVERIFIABLE
            }
            by_kind = {"corpus_equivalence": "compare", "counterexample_search": "search", "finite_domain_proof": "prove", "performance_envelope": "performance"}
            for claim in manifest.claims:
                if claim.id not in active and claim.kind in by_kind:
                    phases.append((by_kind[claim.kind], None))
        estimates = [preflight(manifest, phase=phase, runs=phase_runs) for phase, phase_runs in phases]
        planned = sum(int(e["planned_runs"]) for e in estimates)
        budget = int(manifest.budgets.max_planned_runs)
        if planned > budget:
            parts = ", ".join(f"{e['phase']} {e['planned_runs']}" for e in estimates)
            raise WorkflowError("budget_exceeded", f"run would start {planned} execution(s) together ({parts}); budgets.max_planned_runs is {budget}; raise it knowingly or run the steps one at a time")

    def run(self, manifest: Manifest, roots: Mapping[str, str], *, extra: Mapping[str, Any] | None = None, runs: int | None = None) -> dict[str, Any]:
        """Take an assure session from wherever it stands to a verdict and a report, in one call.

        The same steps in the same order the protocol requires: create if
        absent, characterize, freeze, every declared claim that has no result
        under the manifest in force, verify, report. Nothing is completed,
        accepted or decided here; a person still amends, and runs again.
        """

        steps: list[str] = []
        session_id = manifest.session_id
        if not self.evidence.events(session_id):
            self.create(manifest, roots, extra=extra)
            steps.append("init")
        snap = self.snapshot(session_id)
        if snap.kind == "repair":
            raise WorkflowError("repair_session", f"session {session_id!r} is a repair session; drive it with the repair commands")
        if manifest.digest() not in snap.manifest_history:
            raise WorkflowError("manifest_mismatch", f"session {session_id!r} was not created from this manifest (digest {manifest.digest()[:12]} is not in its history); use the session's manifest or a new session id")
        # an existing session is bound to the roots it was created with: a run that names other directories (or
        # directories that do not exist) is refused before anything is read or appended, never answered with the
        # verdict of the directories the session did examine
        requested = self._check_roots(roots)
        differing = [name for name in ("SOURCE_ROOT", "TARGET_ROOT") if os.path.normcase(requested[name]) != os.path.normcase(str(snap.roots.get(name, "")))]
        if differing:
            bound = "; ".join(f"{name}={snap.roots.get(name, '')}" for name in differing)
            named = "; ".join(f"{name}={requested[name]}" for name in differing)
            raise WorkflowError("roots_mismatch", f"session {session_id!r} is bound to {bound}, this run named {named}; run the session with its own roots, or start a new session id for the other directory")
        self._preflight_run(session_id, snap, runs)
        if snap.state == "CREATED":
            self.characterize(session_id, runs=runs)
            steps.append("characterize")
            snap = self.snapshot(session_id)
        if snap.state == "BASELINE_CAPTURING":
            self.freeze(session_id)
            steps.append("freeze")
            snap = self.snapshot(session_id)
        if snap.state != "BASELINE_FROZEN":
            raise WorkflowError("not_runnable", f"session {session_id!r} is {snap.state}; run continues only a session that is frozen or earlier")
        current = self.manifest(session_id, snap)
        active = {
            str(r.get("claim_id"))
            for r in snap.active_claim_results()
            if r.get("baseline_digest") in ("", snap.baseline_digest) and r.get("status") != claims.UNVERIFIABLE
        }
        requirement = next((claim for claim in current.claims if claim.kind == "baseline_stability"), None)
        if requirement is not None and requirement.id not in active and snap.captures:
            # a characterize interrupted between its two appends left the capture without its claim: derive it now
            engine = self._engine_for(snap)
            latest = snap.captures[-1]
            capture = BaselineCapture(
                session_id=session_id,
                manifest_digest=current.digest(),
                inputs=list(latest.get("inputs", [])),
                runs=int(latest.get("runs", 1)),
                raw_digests={k: list(v) for k, v in latest.get("raw_digests", {}).items()},
                volatile_paths=list(latest.get("volatile_paths", [])),
                proposals=[dict(p) for p in latest.get("proposals", [])],
                uncovered_volatile=engine.uncovered_volatile(session_id, current),
                problems=list(latest.get("problems", [])),
            )
            stability = self._stability_result(requirement, current, capture)
            snap = self._record(session_id, "claim_recorded", {"result": stability.as_dict(), "derived_by": "run"}, snap=snap)
            steps.append("stability")
            active.add(requirement.id)
        evaluators = {
            "corpus_equivalence": (self.compare, "compare"),
            "counterexample_search": (lambda sid: self.search(sid), "search"),
            "finite_domain_proof": (self.prove, "prove"),
            "performance_envelope": (self.performance, "performance"),
        }
        for claim in current.claims:
            if claim.id in active or claim.kind not in evaluators:
                continue
            evaluate, name = evaluators[claim.kind]
            evaluate(session_id)
            steps.append(name)
        verdict = self.verdict(session_id)
        steps.append("verify")
        report = self.report(session_id)
        steps.append("report")
        snap = self.snapshot(session_id)
        return {"session_id": session_id, "state": snap.state, "verdict": verdict.as_dict(), "report": report, "steps": steps, "roots": dict(snap.roots)}

    def unit_finish(self, session_id: str, unit_id: str) -> dict[str, Any]:
        """Verify a unit and, only on ``PASS``, accept it; anything else is left open with the next step named.

        The repairer still cannot decide: acceptance reads the verdict the
        verifier just stored, and ``HUMAN_REVIEW`` still needs a named
        person through ``unit-accept --reviewed-by``.
        """

        verdict = self.unit_verify(session_id, unit_id)
        if verdict.status == claims.PASS:
            commit = self.unit_accept(session_id, unit_id)
            return {"unit_id": unit_id, "verdict": verdict.as_dict(), "accepted": True, "commit": commit, "next": "continue, then start the next unit, or finish"}
        following = {
            claims.BLOCK: "fix the unit in its worktree and run unit-finish again, or unit-reject it with the reason",
            claims.UNVERIFIABLE: "something could not run or be compared; fix the environment or the manifest and run unit-finish again, or unit-reject",
            claims.HUMAN_REVIEW: "a person decides: unit-accept --reviewed-by <their name> after they looked, or unit-reject",
        }
        return {"unit_id": unit_id, "verdict": verdict.as_dict(), "accepted": False, "commit": None, "next": following.get(verdict.status, "read the verdict")}

    # ----------------------------------------------------------- amendment

    def amend(self, session_id: str, amendment: Mapping[str, Any]) -> AmendmentResult:
        snap = self.snapshot(session_id)
        self._check(snap, "manifest_amended")
        manifest = self.manifest(session_id, snap)
        try:
            result = amend_manifest(manifest, amendment)
        except ManifestError as error:
            raise WorkflowError(error.reason, error.detail) from None
        engine = self._engine_for(snap)
        if snap.captures:
            # the new policy set is held to the same bar as at freeze
            try:
                engine.check_policies(session_id, result.manifest)
            except EngineError as error:
                raise WorkflowError(error.reason, error.detail) from None
        self.evidence.record_manifest(result.manifest, at=self.clock())
        if snap.frozen:
            # new policies, new blind spots: the scan is redone under the amended manifest
            engine.sensitivity(session_id, result.manifest)
        invalidated = sorted({str(r["claim_id"]) for r in snap.active_claim_results()})
        touched = list(result.record["added_policies"]) + list(result.record["changed_policies"])
        diverged = snap.all_divergence_paths
        post_divergence = [f"policy {policy_id} covers {', '.join(diverged)} after a divergence was recorded there" for policy_id in policies_covering(result.manifest, touched, diverged)]
        touched_exclusions = list(result.record["added_exclusions"]) + list(result.record["changed_exclusions"])
        post_divergence += [f"exclusion {exclusion_id} covers {', '.join(diverged)} after a divergence was recorded there" for exclusion_id in exclusions_covering(result.manifest, touched_exclusions, diverged)]
        weakening = [f"mandatory claim {claim_id} removed by amendment" for claim_id in result.record["removed_claims"]]
        weakening += [f"mandatory claim {claim_id} demoted to informational by amendment" for claim_id in result.record["demoted_claims"]]
        weakening += [f"human review item {item_id} removed by amendment" for item_id in result.record["removed_human_review"]]
        snap = self._record(
            session_id,
            "manifest_amended",
            {"record": result.record, "new_manifest_digest": result.manifest.digest(), "invalidated": invalidated, "post_divergence": post_divergence, "weakening": weakening},
            snap=snap,
        )
        # Stability is a function of the stored capture and the policies in
        # force, so it is re-derived under the new manifest from evidence
        # already on record rather than left "never evaluated".
        requirement = next((claim for claim in result.manifest.claims if claim.kind == "baseline_stability"), None)
        if requirement is not None and snap.captures:
            latest = snap.captures[-1]
            capture = BaselineCapture(
                session_id=session_id,
                manifest_digest=result.manifest.digest(),
                inputs=list(latest.get("inputs", [])),
                runs=int(latest.get("runs", 1)),
                raw_digests={k: list(v) for k, v in latest.get("raw_digests", {}).items()},
                volatile_paths=list(latest.get("volatile_paths", [])),
                proposals=[dict(p) for p in latest.get("proposals", [])],
                uncovered_volatile=engine.uncovered_volatile(session_id, result.manifest),
                problems=list(latest.get("problems", [])),
            )
            stability = self._stability_result(requirement, result.manifest, capture)
            self._record(session_id, "claim_recorded", {"result": stability.as_dict(), "re_derived_after": result.record["old_digest"]}, snap=snap)
        return result

    # ------------------------------------------------------------- verdict

    @staticmethod
    def _verdict_results(snap: session.Snapshot) -> list[dict[str, Any]]:
        """The results the verdict rests on: the snapshot owns the rule (``Snapshot.verdict_results``)."""

        return snap.verdict_results()

    def _verdict(
        self,
        session_id: str,
        snap: session.Snapshot,
        manifest: Manifest,
        results: Sequence[Mapping[str, Any]],
        *,
        constraint_breaks: Sequence[str] = (),
        extra_integrity: Sequence[str] = (),
        extra_human: Sequence[str] = (),
    ) -> claims.FinalVerdict:
        frozen = self.engine.frozen(session_id)
        if frozen is not None:
            integrity = self.engine.integrity_problems(session_id, frozen)
            if snap.baseline_digest and frozen.baseline_digest != snap.baseline_digest:
                integrity.append("the stored freeze does not match the session's recorded baseline digest")
        else:
            integrity = list(self.evidence.verify()["problems"])
        integrity.extend(extra_integrity)
        volatile = self.engine.uncovered_volatile(session_id, manifest) if snap.captures else []
        parsed = [claims.ClaimResult.from_dict(r) for r in results]
        return claims.final_verdict(
            [claim.as_dict() for claim in manifest.claims],
            parsed,
            integrity_problems=integrity,
            constraint_breaks=list(constraint_breaks),
            human_review=[f"{item.id}: {item.reason}" for item in manifest.human_review] + list(extra_human),
            post_divergence_amendments=list(snap.post_divergence),
            weakening_amendments=list(snap.weakening),
            volatile_unaccepted=volatile,
            manifest_digest=snap.manifest_digest,
            baseline_digest=snap.baseline_digest,
        )

    def coverage(self, session_id: str) -> dict[str, Any]:
        """The assurance coverage map: what each observed value's state is and why."""

        snap = self.snapshot(session_id)
        manifest = self.manifest(session_id, snap)
        engine = self._engine_for(snap)
        raw_records = [(input_id, row["record"]["probes"]) for input_id, row in engine._baseline_rows(session_id, manifest).items() if row["record"].get("status") == "observed"]
        uncovered = engine.uncovered_volatile(session_id, manifest) if snap.captures else []
        scan = engine.sensitivity_record(session_id, manifest) or {}
        return coverage_module.coverage_map(
            manifest,
            self._verdict_results(snap),
            raw_records=raw_records,
            uncovered_volatile=uncovered,
            manifest_digest=snap.manifest_digest,
            baseline_digest=snap.baseline_digest,
            units=[dict(record, id=unit_id) for unit_id, record in snap.units.items()],
            blind_spots=[entry["path"] for entry in scan.get("blind", [])] + [entry["path"] for entry in scan.get("placeholder", [])],
        )

    def verdict(self, session_id: str) -> claims.FinalVerdict:
        snap = self.snapshot(session_id)
        manifest = self.manifest(session_id, snap)
        return self._verdict(session_id, snap, manifest, self._verdict_results(snap))

    def report(self, session_id: str) -> dict[str, Any]:
        snap = self.snapshot(session_id)
        manifest = self.manifest(session_id, snap)
        verdict = self._verdict(session_id, snap, manifest, self._verdict_results(snap))
        provenance = {
            "producer_identity": snap.producer_identity,
            "tool_versions": _tool_versions(),
            "platform": platform.platform(),
            "evidence_store": str(self.evidence.path),
            "evidence_chain_heads": self.evidence.verify()["heads"],
        }
        counts = {
            kind: len(self.evidence.observations(session_id, kind=kind))
            for kind in ("raw", "normalized", "comparison", "freeze")
        }
        uncovered = self.engine.uncovered_volatile(session_id, manifest) if snap.captures else []
        return report_module.build(
            snap,
            manifest,
            verdict,
            provenance=provenance,
            extras={"observation_counts": counts},
            results=self._verdict_results(snap),
            uncovered_volatile=uncovered,
            coverage_map=self.coverage(session_id),
            sensitivity=self.engine.sensitivity_record(session_id, manifest),
        )

    def _store_report(self, session_id: str, data: Mapping[str, Any]) -> str:
        number = len(self.evidence.observations(session_id, kind="report", prefix="report:")) + 1
        return self.evidence.record_observation(session_id, "report", f"report:{number}", dict(data), at=self.clock())

    def complete(self, session_id: str) -> session.Snapshot:
        snap = self.snapshot(session_id)
        if snap.kind == "repair":
            return self.finish(session_id)
        self._check(snap, "completed")
        manifest = self.manifest(session_id, snap)
        verdict = self._verdict(session_id, snap, manifest, self._verdict_results(snap))
        data = self.report(session_id)
        digest = self._store_report(session_id, data)
        return self._record(session_id, "completed", {"final_verdict": verdict.as_dict(), "report_digest": digest}, snap=snap)

    # --------------------------------------------------------------- repair

    def _governor(self, snap: session.Snapshot) -> Governor:
        if snap.kind != "repair":
            raise WorkflowError("not_a_repair_session", f"session {snap.session_id!r} is an assure session")
        governor = self._governors.get(snap.session_id)
        if governor is None:
            if not snap.repository or not snap.workspace:
                raise WorkflowError("session_corrupt", "repair session has no repository or workspace recorded")
            governor = Governor(snap.repository, workspace=Path(snap.workspace).parent, git=self.git, clock=self.clock)
            self._governors[snap.session_id] = governor
        self.governor = governor
        return governor

    def repair_init(self, repo_root: str | Path, manifest: Manifest, *, workspace: str | Path | None = None) -> session.Snapshot:
        session_id = manifest.session_id
        if self.evidence.events(session_id):
            raise WorkflowError("session_exists", f"session {session_id!r} already has a history; resume it or pick another id")
        governor = Governor(repo_root, workspace=workspace, git=self.git, clock=self.clock)
        try:
            info = governor.init_session(session_id)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        self._governors[session_id] = governor
        self.governor = governor
        roots = {"SOURCE_ROOT": info["baseline_root"], "TARGET_ROOT": info["baseline_root"]}
        return self.create(
            manifest,
            roots,
            kind="repair",
            extra={"repository": info["repository"], "base_commit": info["base_commit"], "accepted_commit": info["base_commit"], "workspace": info["workspace"], "accepted_ref": info["accepted_ref"]},
        )

    def analyze(self, session_id: str, findings: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        snap = self.snapshot(session_id)
        self._governor(snap)
        self._check(snap, "analysis_recorded")
        try:
            validated = analysis.validate_findings([dict(f) for f in (findings or [])])
        except analysis.AnalysisError as error:
            raise WorkflowError("bad_findings", str(error)) from None
        metrics = analysis.measure(snap.roots["SOURCE_ROOT"])
        self._record(session_id, "analysis_recorded", {"metrics": metrics.as_dict(), "findings": validated}, snap=snap)
        return {"metrics": metrics.as_dict(), "findings": validated}

    def plan(self, session_id: str, units: Sequence[Mapping[str, Any]]) -> session.Snapshot:
        snap = self.snapshot(session_id)
        self._governor(snap)
        self._check(snap, "plan_recorded")
        if not isinstance(units, Sequence) or not units:
            raise WorkflowError("bad_plan", "a plan lists at least one unit")
        seen: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for index, unit in enumerate(units):
            where = f"units[{index}]"
            if not isinstance(unit, Mapping):
                raise WorkflowError("bad_plan", f"{where} must be an object")
            unknown = sorted(set(unit) - {"id", "objective", "reason", "risk", "owned_paths", "expected_behavior_impact", "verification"})
            if unknown:
                raise WorkflowError("bad_plan", f"{where}: unknown field(s) {', '.join(unknown)}")
            ident = unit.get("id")
            if not isinstance(ident, str) or not ref_safe_id(ident):
                raise WorkflowError("bad_plan", f"{where}.id must be a slug with no '..' and no '.lock' ending (it names a git ref)")
            if ident in seen or ident in snap.units and snap.units[ident].get("status") not in (None, "planned"):
                raise WorkflowError("bad_plan", f"{where}: unit {ident!r} is duplicated or already worked on")
            seen.add(ident)
            objective = unit.get("objective")
            if not isinstance(objective, str) or not objective.strip():
                raise WorkflowError("bad_plan", f"{where}.objective must say what the unit changes")
            owned = unit.get("owned_paths", [])
            if not isinstance(owned, list) or not all(isinstance(p, str) and p for p in owned):
                raise WorkflowError("bad_plan", f"{where}.owned_paths must be a list of path globs")
            verification = unit.get("verification", [])
            if not isinstance(verification, list) or not all(isinstance(v, str) for v in verification):
                raise WorkflowError("bad_plan", f"{where}.verification must be a list of claim ids or notes")
            cleaned.append(
                {
                    "id": ident,
                    "objective": objective.strip(),
                    "reason": str(unit.get("reason", "")),
                    "risk": str(unit.get("risk", "unknown")),
                    "owned_paths": list(owned),
                    "expected_behavior_impact": str(unit.get("expected_behavior_impact", "none")),
                    "verification": list(verification),
                }
            )
        for ident, record in snap.units.items():
            if record.get("status") not in (None, "planned") and ident not in seen:
                cleaned.append({k: v for k, v in record.items() if k in ("id", "objective", "reason", "risk", "owned_paths", "expected_behavior_impact", "verification")})
        return self._record(session_id, "plan_recorded", {"units": cleaned}, snap=snap)

    def unit_start(self, session_id: str, unit_id: str) -> Path:
        snap = self.snapshot(session_id)
        governor = self._governor(snap)
        self._check(snap, "unit_started")
        unit = snap.units.get(unit_id)
        if unit is None or unit_id not in {u["id"] for u in snap.plan}:
            raise WorkflowError("unknown_unit", f"unit {unit_id!r} is not in the recorded plan")
        if unit.get("status") not in (None, "planned"):
            raise WorkflowError("unit_done", f"unit {unit_id!r} is already {unit.get('status')}")
        try:
            root = governor.start_unit(session_id, unit_id)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        snap = self._record(session_id, "unit_started", {"unit_id": unit_id, "worktree": str(root), "base_commit": snap.accepted_commit}, snap=snap)
        self._record(session_id, "unit_in_progress", {"unit_id": unit_id}, snap=snap)
        return root

    def _current_unit(self, snap: session.Snapshot, unit_id: str, *states: str, reason: str) -> dict[str, Any]:
        if snap.state not in states:
            raise WorkflowError(reason, f"session is {snap.state}; needs one of {', '.join(states)}")
        if snap.current_unit != unit_id:
            raise WorkflowError("wrong_unit", f"the unit in progress is {snap.current_unit!r}, not {unit_id!r}")
        return snap.units[unit_id]

    def unit_verify(self, session_id: str, unit_id: str) -> claims.FinalVerdict:
        snap = self.snapshot(session_id)
        governor = self._governor(snap)
        unit = self._current_unit(snap, unit_id, "UNIT_IN_PROGRESS", "UNIT_VERIFYING", reason="unit_not_in_progress")
        manifest = self.manifest(session_id, snap)
        self._engine_for(snap)
        frozen = self._frozen(session_id, snap)
        self._preflight(manifest, "unit_verify")
        base = snap.accepted_commit
        try:
            unit_root = governor.unit_root(session_id, unit_id)
            recorded = unit.get("worktree")
            if recorded and Path(recorded).resolve() != Path(unit_root).resolve():
                raise WorkflowError("worktree_mismatch", f"unit {unit_id!r} was started at {recorded}, but the governor now places it at {unit_root}")
            tree = governor.unit_tree(session_id, unit_id, base=base)
            changed = governor.unit_changed_paths(session_id, unit_id, base=base)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        owned = list(unit.get("owned_paths", []))
        breaks = [
            f"unit {unit_id} changed {path} outside its declared ownership ({', '.join(owned)})"
            for path in changed
            if owned and not matches_include(path, owned)
        ]
        # a git-ignored file in the worktree is seen by the verification but will not be in the commit:
        # the behaviour verified may depend on it, so a person decides (never PASS on its own)
        try:
            ignored = governor.ignored_entries(unit_root)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        human = (
            [f"unit {unit_id}: {len(ignored)} git-ignored file(s) present in the verified worktree are not part of the tree to be committed: {', '.join(ignored[:8])}"]
            if ignored
            else []
        )
        integrity: list[str] = []
        baseline_root = snap.roots.get("SOURCE_ROOT", "")

        def baseline_intact(moment: str) -> None:
            try:
                governor.assert_pristine(baseline_root, str(snap.base_commit))
            except GovernorError as error:
                integrity.append(f"baseline worktree {moment} verification: {error.reason}: {error.detail}")

        baseline_intact("before")
        snap = self._record(session_id, "unit_verifying", {"unit_id": unit_id, "tree": tree, "changed_paths": changed}, snap=snap)
        roots = dict(snap.roots)
        roots["TARGET_ROOT"] = str(unit_root)
        results = []
        if any(claim.kind == "corpus_equivalence" for claim in manifest.claims):
            results.append(self._evaluate_corpus(session_id, manifest, frozen, roots))
        if any(claim.kind == "counterexample_search" for claim in manifest.claims):
            results.append(self._evaluate_search(session_id, manifest, frozen, roots, seed=None, runs=None, seconds=None))
        if any(claim.kind == "finite_domain_proof" for claim in manifest.claims):
            results.append(self._evaluate_proof(session_id, manifest, frozen, roots))
        if any(claim.kind == "performance_envelope" for claim in manifest.claims):
            results.append(self._evaluate_performance(session_id, manifest, frozen, roots))
        metrics_after = analysis.measure(unit_root)
        delta = analysis.delta(analysis.Metrics.from_dict(snap.metrics_before), metrics_after) if snap.metrics_before else None
        # the tree that was verified must be the tree that is still there
        try:
            after = governor.unit_tree(session_id, unit_id, base=base)
        except GovernorError as error:
            after = None
            integrity.append(f"unit worktree after verification: {error.reason}: {error.detail}")
        if after is not None and after != tree:
            integrity.append(f"unit {unit_id!r} changed while it was being verified (tree {tree[:12]} became {after[:12]}); nothing verified applies to it")
        baseline_intact("after")
        session_level = [r for r in snap.active_claim_results() if r.get("kind") == "baseline_stability"]
        verdict = self._verdict(session_id, snap, manifest, session_level + [r.as_dict() for r in results], constraint_breaks=breaks, extra_integrity=integrity, extra_human=human)
        self._record(
            session_id,
            "unit_verified",
            {
                "unit_id": unit_id,
                "tree": tree,
                "changed_paths": changed,
                "ignored_paths": ignored,
                "manifest_digest": snap.manifest_digest,
                "verdict": verdict.as_dict(),
                "claim_results": [r.as_dict() for r in results],
                "metrics_after": metrics_after.as_dict(),
                "metrics_delta": delta,
            },
            snap=snap,
        )
        return verdict

    def unit_accept(self, session_id: str, unit_id: str, *, reviewed_by: str | None = None) -> str:
        snap = self.snapshot(session_id)
        governor = self._governor(snap)
        unit = snap.units.get(unit_id, {})
        if (
            snap.state != "UNIT_VERIFYING"
            or snap.current_unit != unit_id
            or "verdict" not in unit
            or not unit.get("verified_tree")
            or unit.get("verified_tree") != unit.get("tree")
            or unit.get("manifest_digest") != snap.manifest_digest
        ):
            raise WorkflowError("unit_not_verified", f"unit {unit_id!r} has no recorded verification of its current tree under the current manifest in this state ({snap.state}); run unit-verify first")
        verdict = unit["verdict"]
        status = verdict.get("status")
        if status == claims.PASS:
            pass
        elif status == claims.HUMAN_REVIEW and reviewed_by:
            pass
        else:
            raise WorkflowError(
                "unit_not_accepted",
                f"unit {unit_id!r} verified {status} (decided by {verdict.get('decided_by')}); only PASS is accepted, or HUMAN_REVIEW with --reviewed-by naming the person",
            )
        objective = unit.get("objective", unit_id)
        try:
            commit = governor.accept_unit(session_id, unit_id, message=f"{unit_id}: {objective}", expected_tree=str(unit["tree"]), reviewed_by=reviewed_by, base=snap.accepted_commit)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        self._record(session_id, "unit_accepted", {"unit_id": unit_id, "commit": commit, "tree": unit["tree"], "reviewed_by": reviewed_by}, snap=snap)
        return commit

    def unit_reject(self, session_id: str, unit_id: str, *, reason: str) -> dict[str, Any]:
        snap = self.snapshot(session_id)
        governor = self._governor(snap)
        self._current_unit(snap, unit_id, "UNIT_IN_PROGRESS", "UNIT_VERIFYING", reason="unit_not_in_progress")
        try:
            record = governor.reject_unit(session_id, unit_id, evidence_dir=governor.evidence_root(session_id), reason=reason, base=snap.accepted_commit, remove=False)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        # the rejection is on record before the worktree goes: an
        # interruption between the two is completed by resume
        snap = self._record(session_id, "unit_rejected", {"unit_id": unit_id, "reason": reason, "patch_digest": record["patch_digest"], "patch_path": record["patch_path"], "changed_paths": record["changed_paths"], "tree": record["tree"]}, snap=snap)
        root = governor.unit_root(session_id, unit_id)
        try:
            if governor.recognized_worktree(root):
                governor.remove_worktree(root)
        except GovernorError as error:
            raise WorkflowError("rollback_incomplete", f"the rejection of unit {unit_id!r} is recorded but its worktree could not be removed ({error.detail}); run resume to finish the rollback") from None
        self._record(session_id, "unit_rolled_back", {"unit_id": unit_id, "removed_worktree": str(root)}, snap=snap)
        return record

    def continue_(self, session_id: str) -> session.Snapshot:
        snap = self.snapshot(session_id)
        self._governor(snap)
        return self._record(session_id, "continued", {}, snap=snap)

    def finish(self, session_id: str) -> session.Snapshot:
        snap = self.snapshot(session_id)
        governor = self._governor(snap)
        if snap.state in ("UNIT_PREPARING", "UNIT_IN_PROGRESS", "UNIT_VERIFYING", "UNIT_REJECTED"):
            raise WorkflowError("unit_open", f"session is {snap.state}; accept or reject the open unit first")
        if snap.state not in ("PLANNED", "UNIT_ACCEPTED", "UNIT_ROLLED_BACK", "BASELINE_FROZEN"):
            raise WorkflowError("not_finishable", f"session is {snap.state}; a repair session finishes from a frozen baseline, a plan, or after a unit is accepted or rolled back")
        self._check(snap, "completed")
        manifest = self.manifest(session_id, snap)
        verdict = self._verdict(session_id, snap, manifest, self._verdict_results(snap))
        try:
            branch = governor.finish(session_id, expected_commit=snap.accepted_commit)
        except GovernorError as error:
            raise WorkflowError(error.reason, error.detail) from None
        data = self.report(session_id)
        digest = self._store_report(session_id, data)
        return self._record(session_id, "completed", {"final_verdict": verdict.as_dict(), "report_digest": digest, "branch": branch}, snap=snap)

    def resume(self, session_id: str) -> dict[str, Any]:
        """Re-read the session, finish an interrupted transition, and say what is next.

        Three outcomes: the evidence and the repository agree (``resumed``);
        they disagree in a way no automatic step can mend (``blocked``); or
        git could not be consulted at all (``transient``: nothing is
        recorded, the session stays where it was, and the caller retries).
        """

        snap = self.snapshot(session_id)
        problems: list[str] = []
        notes: list[str] = []
        transient: list[str] = []
        if snap.kind == "repair" and snap.state not in session.TERMINAL:
            open_states = ("UNIT_PREPARING", "UNIT_IN_PROGRESS", "UNIT_VERIFYING")
            open_unit = snap.current_unit if snap.state in open_states else None
            try:
                governor = self._governor(snap)
                state = governor.resume_state(session_id, accepted_commit=snap.accepted_commit, unit_id=open_unit)
                problems = governor.resume_problems(state)
                unit = snap.units.get(open_unit or "", {})
                if snap.state == "UNIT_VERIFYING" and state["ref_drift"] and unit.get("tree"):
                    verdict = unit.get("verdict") or {}
                    status = verdict.get("status")
                    reconciled = None
                    reviewer = None
                    if status in (claims.PASS, claims.HUMAN_REVIEW) and unit.get("verified_tree") == unit.get("tree"):
                        reconciled = governor.reconcile_acceptance(session_id, accepted_commit=str(snap.accepted_commit), verified_tree=str(unit["tree"]))
                        if reconciled and status == claims.HUMAN_REVIEW:
                            # the same rule as unit-accept: HUMAN_REVIEW is
                            # accepted only with a named reviewer, which the
                            # acceptance commit itself carries
                            reviewer = governor.reviewed_by_of(reconciled)
                            if not reviewer:
                                reconciled = None
                                problems.append(f"the accepted ref moved to the tree of unit {open_unit!r}, which verified HUMAN_REVIEW, and the commit names no reviewer; not reconciled")
                    if reconciled is None and not any("not reconciled" in p for p in problems):
                        problems.append(f"the accepted ref moved while unit {open_unit!r} stood at {status or 'no verdict'}; only a PASS verdict, or a HUMAN_REVIEW verdict with a named reviewer, on the current tree can be reconciled")
                    if reconciled:
                        snap = self._record(session_id, "unit_accepted", {"unit_id": open_unit, "commit": reconciled, "tree": unit["tree"], "reconciled": True, "reviewed_by": reviewer}, snap=snap)
                        notes.append(f"reconciled an interrupted acceptance of {open_unit}: the accepted ref already held the verified tree as {reconciled[:12]}")
                        root = governor.unit_root(session_id, str(open_unit))
                        try:
                            if governor.recognized_worktree(root):
                                governor.remove_worktree(root)
                                notes.append("removed the unit worktree left behind by the interrupted acceptance")
                        except GovernorError as error:
                            transient.append(f"{error.reason}: the worktree of accepted unit {open_unit} could not be removed ({error.detail}); remove it when it is free")
                        # the drift is explained and the worktree is gone, or going, by design
                        state = dict(state, ref_drift=False, unit_worktree_missing=False)
                        problems = governor.resume_problems(state)
                if not problems and snap.state == "UNIT_PREPARING" and open_unit:
                    snap = self._record(session_id, "unit_in_progress", {"unit_id": open_unit, "resumed": True}, snap=snap)
                    notes.append(f"completed the interrupted start of unit {open_unit}; its worktree is in place")
                if not problems and snap.state == "UNIT_REJECTED":
                    rejected = snap.current_unit or next((uid for uid, rec in snap.units.items() if rec.get("status") == "rejected"), None)
                    if rejected:
                        root = governor.unit_root(session_id, rejected)
                        removed = True
                        try:
                            if governor.recognized_worktree(root):
                                governor.remove_worktree(root)
                                notes.append(f"removed the worktree of rejected unit {rejected}")
                        except GovernorError as error:
                            removed = False
                            transient.append(f"{error.reason}: the worktree of rejected unit {rejected} could not be removed ({error.detail}); run resume again when it is free")
                        if removed:
                            snap = self._record(session_id, "unit_rolled_back", {"unit_id": rejected, "removed_worktree": str(root), "resumed": True}, snap=snap)
                            notes.append(f"completed the interrupted rollback of unit {rejected}")
            except GovernorError as error:
                if error.reason in ("git_unavailable", "git_timeout"):
                    transient.append(f"{error.reason}: {error.detail}")
                else:
                    problems.append(f"{error.reason}: {error.detail}")
        if problems:
            snap = self._record(session_id, "blocked", {"reason": "; ".join(problems), "resumed": True}, snap=snap)
        elif transient:
            notes.append("git could not be consulted; nothing was recorded, run resume again when it is available")
        elif snap.state not in session.TERMINAL:
            snap = self._record(session_id, "resumed", {"problems": [], "notes": notes}, snap=snap)
        return {
            "session_id": session_id,
            "kind": snap.kind,
            "state": snap.state,
            "current_unit": snap.current_unit,
            "accepted_commit": snap.accepted_commit,
            "problems": problems,
            "transient": transient,
            "notes": notes,
            "next": NEXT_STEPS.get(snap.state, {}).get(snap.kind or "assure", "-"),
        }
