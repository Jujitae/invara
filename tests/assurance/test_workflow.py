"""The orchestration layer, end to end: assure sessions, repair sessions, and the mutation proofs."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from _support import manifest_dict, policy
from invara.assurance import claims as c
from invara.assurance import engine as eng
from invara.assurance import evidence as ev
from invara.assurance import manifest as m
from invara.assurance import workflow as wf
from invara.contract import BLOCK, HUMAN_REVIEW, PASS, UNVERIFIABLE

APP = textwrap.dedent(
    '''
    import json, sys, uuid
    from datetime import datetime, timezone

    def load():
        return json.load(sys.stdin)

    def total_of(data):
        total = sum(item["qty"] * item["price"] for item in data.get("items", []))
        if total >= 100 and BONUS:
            total = total - 10
        return total

    def main():
        data = load()
        out = {"order_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
               "total": total_of(data), "count": len(data.get("items", []))}
        print(json.dumps(out))

    main()
    '''
)

REFACTORED = textwrap.dedent(
    '''
    import json, sys, uuid
    from datetime import datetime, timezone
    from pricing import total_of

    def main():
        data = json.load(sys.stdin)
        out = {"order_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
               "total": total_of(data), "count": len(data.get("items", []))}
        print(json.dumps(out))

    main()
    '''
)

PRICING = textwrap.dedent(
    '''
    BONUS_THRESHOLD = 100
    BONUS = 10

    def total_of(data):
        total = sum(item["qty"] * item["price"] for item in data.get("items", []))
        if total >= BONUS_THRESHOLD:
            total -= BONUS
        return total
    '''
)

PRICING_WRONG = PRICING.replace("total >= BONUS_THRESHOLD", "total > BONUS_THRESHOLD")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def spec(session_id: str, **over) -> m.Manifest:
    sections = dict(
        session_id=session_id,
        source_system={"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT"},
        target_system={"same_as_source": True},
        input_domain={
            "kind": "corpus",
            "delivery": "stdin_json",
            "corpus": [
                {"id": "small", "input": {"items": [{"qty": 1, "price": 5}]}},
                {"id": "large", "input": {"items": [{"qty": 20, "price": 5}, {"qty": 1, "price": 30}]}},
                {"id": "edge", "input": {"items": [{"qty": 10, "price": 10}]}},
            ],
        },
        # exit code only: a refactor legitimately changes traceback text, so
        # stderr is not part of this manifest's definition of behaviour
        probes=[{"id": "cli", "adapter": "process", "capture": [], "mandatory": True}, {"id": "out", "adapter": "json", "source": "stdout", "mandatory": True}],
        policies=[policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids"), policy("timestamp", "/out/value/created_at", id="ts")],
        claims=[
            {"id": "corpus", "kind": "corpus_equivalence", "mandatory": True},
            {"id": "search", "kind": "counterexample_search", "mandatory": True, "params": {"runs": 40, "seconds": 120, "seed": 3}},
        ],
        budgets={"shrink_steps": 60},
    )
    sections.update(over)
    return m.Manifest.from_dict(manifest_dict(**sections))


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.db = self.root / "verify.db"
        self.evidence = ev.Evidence(self.db)
        self.flow = wf.Workflow(self.evidence, workspace_parent=self.root / "ws")
        self.before = self.root / "before"
        self.same = self.root / "same"
        self.wrong = self.root / "wrong"
        write(self.before / "app.py", APP.replace("BONUS", "True"))
        write(self.same / "app.py", REFACTORED)
        write(self.same / "pricing.py", PRICING)
        write(self.wrong / "app.py", REFACTORED)
        write(self.wrong / "pricing.py", PRICING_WRONG)

    def tearDown(self) -> None:
        self.evidence.close()
        self._tmp.cleanup()

    def roots(self, target: Path) -> dict[str, str]:
        return {"SOURCE_ROOT": str(self.before), "TARGET_ROOT": str(target)}


class AssureSessions(Sandbox):
    def test_the_whole_assure_flow_reaches_a_pass_for_an_equivalent_target(self) -> None:
        manifest = spec("assure-ok")
        snapshot = self.flow.create(manifest, self.roots(self.same))
        self.assertEqual(snapshot.state, "CREATED")
        capture = self.flow.characterize("assure-ok", runs=2)
        self.assertEqual(capture.uncovered_volatile, [])
        frozen = self.flow.freeze("assure-ok")
        self.assertEqual(self.flow.snapshot("assure-ok").state, "BASELINE_FROZEN")
        corpus = self.flow.compare("assure-ok")
        self.assertEqual(corpus.status, c.PRESERVED_WITHIN_ENVELOPE)
        search = self.flow.search("assure-ok")
        self.assertEqual(search.status, c.NO_DIVERGENCE_FOUND)
        verdict = self.flow.verdict("assure-ok")
        self.assertEqual(verdict.status, PASS, verdict)
        self.assertEqual(verdict.decided_by, "preserved")
        data = self.flow.report("assure-ok")
        self.assertEqual(data["summary"]["verdict"]["status"], PASS)
        self.assertEqual(data["technical"]["baseline"]["digest"], frozen.baseline_digest)
        self.assertEqual(self.flow.complete("assure-ok").state, "COMPLETED")
        self.assertTrue(self.evidence.verify()["ok"])

    def test_a_wrong_target_diverges_and_the_search_minimizes_a_counterexample(self) -> None:
        self.flow.create(spec("assure-bad"), self.roots(self.wrong))
        self.flow.characterize("assure-bad")
        self.flow.freeze("assure-bad")
        corpus = self.flow.compare("assure-bad")
        self.assertEqual(corpus.status, c.DIVERGED)
        self.assertEqual(corpus.divergences[0]["path"], "/out/value/total")
        search = self.flow.search("assure-bad")
        self.assertEqual(search.status, c.DIVERGED)
        minimized = search.counterexample["minimized"]["input"]
        original = search.counterexample["original"]["input"]
        self.assertEqual(sum(i["qty"] * i["price"] for i in minimized["items"]), 100, minimized)
        self.assertEqual(len(minimized["items"]), 1)
        self.assertGreaterEqual(len(json.dumps(original)), len(json.dumps(minimized)))
        self.assertEqual(search.counterexample["seed"], 3)
        verdict = self.flow.verdict("assure-bad")
        self.assertEqual((verdict.status, verdict.decided_by), (BLOCK, "diverged"))
        summary = self.flow.report("assure-bad")["summary"]
        self.assertEqual(summary["sections"][0]["status"], "no")

    def test_the_search_finds_a_boundary_the_corpus_missed(self) -> None:
        """The existing inputs sit next to the boundary; the wrong refactor moves it by one."""

        manifest = spec(
            "assure-boundary",
            input_domain={
                "kind": "corpus",
                "delivery": "stdin_json",
                "corpus": [{"id": "ninety", "input": {"items": [{"qty": 9, "price": 10}]}}, {"id": "small", "input": {"items": [{"qty": 1, "price": 5}]}}],
            },
        )
        self.flow.create(manifest, self.roots(self.wrong))
        self.flow.characterize("assure-boundary")
        self.flow.freeze("assure-boundary")
        self.assertEqual(self.flow.compare("assure-boundary").status, c.PRESERVED_WITHIN_ENVELOPE, "every recorded input agrees")
        search = self.flow.search("assure-boundary")
        self.assertEqual(search.status, c.DIVERGED, "the boundary value is generated from the neighbouring seed")
        self.assertEqual(search.counterexample["original"]["mutation"], "numeric-boundary:/items/0/qty=10")
        self.assertEqual(search.counterexample["minimized"]["input"], {"items": [{"qty": 10, "price": 10}]})
        self.assertEqual(self.flow.verdict("assure-boundary").status, BLOCK)

    def test_a_missing_mandatory_claim_leaves_the_session_unverifiable(self) -> None:
        self.flow.create(spec("assure-partial"), self.roots(self.same))
        self.flow.characterize("assure-partial")
        self.flow.freeze("assure-partial")
        self.flow.compare("assure-partial")
        verdict = self.flow.verdict("assure-partial")
        self.assertEqual((verdict.status, verdict.decided_by), (UNVERIFIABLE, "never_evaluated"))

    def test_steps_out_of_order_are_refused(self) -> None:
        self.flow.create(spec("assure-order"), self.roots(self.same))
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.freeze("assure-order")
        self.assertEqual(caught.exception.reason, "invalid_transition")
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.compare("assure-order")
        self.assertEqual(caught.exception.reason, "not_frozen")

    def test_an_unknown_session_is_refused(self) -> None:
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.snapshot("ghost")
        self.assertEqual(caught.exception.reason, "no_session")


class MutationProofs(Sandbox):
    """The eight mandatory mutation proofs that do not live elsewhere."""

    def frozen_bad(self, session_id: str = "mut") -> None:
        self.flow.create(spec(session_id), self.roots(self.wrong))
        self.flow.characterize(session_id)
        self.flow.freeze(session_id)

    def test_altering_baseline_evidence_after_capture_blocks_on_integrity(self) -> None:
        self.flow.create(spec("mut-tamper"), self.roots(self.same))
        self.flow.characterize("mut-tamper")
        self.flow.freeze("mut-tamper")
        self.flow.compare("mut-tamper")
        self.flow.search("mut-tamper")
        self.assertEqual(self.flow.verdict("mut-tamper").status, PASS)
        connection = sqlite3.connect(self.db)
        row = connection.execute("SELECT seq, record_json FROM assurance_observation WHERE run_key = 'baseline:before:small:1'").fetchone()
        forged = json.loads(row[1])
        forged["probes"]["out"]["value"]["total"] = 999
        connection.execute("UPDATE assurance_observation SET record_json = ? WHERE seq = ?", (json.dumps(forged), row[0]))
        connection.commit()
        connection.close()
        verdict = self.flow.verdict("mut-tamper")
        self.assertEqual((verdict.status, verdict.decided_by), (BLOCK, "integrity"))

    def test_removing_a_mandatory_observation_blocks(self) -> None:
        self.flow.create(spec("mut-remove"), self.roots(self.same))
        self.flow.characterize("mut-remove")
        self.flow.freeze("mut-remove")
        self.flow.compare("mut-remove")
        self.flow.search("mut-remove")
        connection = sqlite3.connect(self.db)
        connection.execute("DELETE FROM assurance_observation WHERE run_key = 'baseline:before:large:1'")
        connection.commit()
        connection.close()
        self.assertEqual(self.flow.verdict("mut-remove").status, BLOCK)

    def test_broadening_a_tolerance_beyond_bounds_is_refused(self) -> None:
        self.frozen_bad("mut-tol")
        self.flow.compare("mut-tol")
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.amend("mut-tol", {"requested_by": "repairer", "reason": "close enough", "changes": {"policies": [policy("numeric_rel_tolerance", "/out/value/total", rel=1.0, id="wide")]}})
        self.assertEqual(caught.exception.reason, "overbroad_tolerance")

    def test_a_policy_added_after_a_divergence_pins_the_verdict_below_pass(self) -> None:
        self.frozen_bad("mut-post")
        self.assertEqual(self.flow.compare("mut-post").status, c.DIVERGED)
        amended = self.flow.amend(
            "mut-post",
            {"requested_by": "repairer", "reason": "totals are noisy", "changes": {"policies": [policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids"), policy("timestamp", "/out/value/created_at", id="ts"), policy("numeric_abs_tolerance", "/out/value/total", abs=20, id="loose")]}},
        )
        snapshot = self.flow.snapshot("mut-post")
        self.assertEqual(snapshot.manifest_digest, amended.manifest.digest())
        self.assertEqual(snapshot.invalidated_claims, [{"claim_id": "corpus", "manifest_digest": amended.record["old_digest"], "before": 1}])
        self.assertTrue(snapshot.post_divergence)
        self.assertEqual(self.flow.compare("mut-post").status, c.PRESERVED_WITHIN_ENVELOPE)
        self.flow.search("mut-post")
        verdict = self.flow.verdict("mut-post")
        self.assertEqual((verdict.status, verdict.decided_by), (HUMAN_REVIEW, "needs_human"))
        self.assertTrue(any("post-divergence" in note for note in verdict.needs_human))

    def test_an_amendment_before_any_divergence_is_ordinary(self) -> None:
        self.flow.create(spec("mut-early"), self.roots(self.same))
        self.flow.characterize("mut-early")
        self.flow.freeze("mut-early")
        self.flow.amend("mut-early", {"requested_by": "operator", "reason": "warnings vary", "changes": {"exclusions": [{"id": "warn", "path": "/cli/stderr", "reason": "platform warnings"}]}})
        self.assertEqual(self.flow.snapshot("mut-early").post_divergence, [])
        self.flow.compare("mut-early")
        self.flow.search("mut-early")
        self.assertEqual(self.flow.verdict("mut-early").status, PASS)


class StabilityClaim(Sandbox):
    STABILITY = {"id": "stability", "kind": "baseline_stability", "mandatory": True, "params": {"runs": 2}}
    CORPUS = {"id": "corpus", "kind": "corpus_equivalence", "mandatory": True}
    SEARCH = {"id": "search", "kind": "counterexample_search", "mandatory": True, "params": {"runs": 20, "seconds": 60, "seed": 3}}

    def test_a_mandatory_stability_claim_is_evaluated_when_the_baseline_is_captured(self) -> None:
        self.flow.create(spec("stab", claims=[self.CORPUS, self.SEARCH, self.STABILITY]), self.roots(self.same))
        self.flow.characterize("stab", runs=2)
        results = self.flow.snapshot("stab").active_claim_results()
        self.assertEqual([r["claim_id"] for r in results], ["stability"])
        self.assertEqual(results[0]["status"], c.NO_DIVERGENCE_FOUND)
        self.assertEqual(results[0]["coverage"]["volatile_paths"], ["/out/value/created_at", "/out/value/order_id"])
        self.assertEqual(results[0]["coverage"]["runs"], 2)
        self.flow.freeze("stab")
        self.flow.compare("stab")
        self.flow.search("stab")
        self.assertEqual(self.flow.verdict("stab").status, PASS)

    def test_uncovered_volatility_makes_the_stability_claim_need_a_person(self) -> None:
        manifest = spec("stab2", policies=[policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids")], claims=[self.CORPUS, self.STABILITY])
        self.flow.create(manifest, self.roots(self.same))
        self.flow.characterize("stab2", runs=2)
        (result,) = self.flow.snapshot("stab2").active_claim_results()
        self.assertEqual(result["status"], HUMAN_REVIEW)
        self.assertIn("/out/value/created_at", result["detail"])

    def test_an_amendment_re_derives_stability_from_the_stored_capture(self) -> None:
        """A policy accepted later covers the volatility; no new runs are needed to say so."""

        manifest = spec("stab4", policies=[policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids")], claims=[self.CORPUS, self.STABILITY])
        self.flow.create(manifest, self.roots(self.same))
        self.flow.characterize("stab4", runs=2)
        self.assertEqual(self.flow.snapshot("stab4").active_claim_results()[0]["status"], HUMAN_REVIEW)
        runs_before = len(self.evidence.observations("stab4", kind="raw"))
        self.flow.amend(
            "stab4",
            {"requested_by": "operator", "reason": "created_at is the clock", "changes": {"policies": [policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids"), policy("timestamp", "/out/value/created_at", id="ts")]}},
        )
        (result,) = self.flow.snapshot("stab4").active_claim_results()
        self.assertEqual(result["status"], c.NO_DIVERGENCE_FOUND)
        self.assertEqual(result["manifest_digest"], self.flow.snapshot("stab4").manifest_digest)
        self.assertEqual(len(self.evidence.observations("stab4", kind="raw")), runs_before, "re-derived from evidence, not re-run")

    def test_a_single_run_cannot_evaluate_stability(self) -> None:
        # A capture with fewer runs than the claim must fail closed.
        # demands is refused before anything is stored, because a second
        # capture is not allowed and the claim would be unsatisfiable
        manifest = spec("stab3", claims=[self.CORPUS, self.STABILITY])
        self.flow.create(manifest, self.roots(self.same))
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.characterize("stab3", runs=1)
        self.assertEqual(caught.exception.reason, "insufficient_runs")
        self.assertEqual(self.flow.snapshot("stab3").state, "CREATED")
        # and the claim itself still says a single run proves nothing
        requirement = next(claim for claim in manifest.claims if claim.kind == "baseline_stability")
        capture = eng.BaselineCapture(session_id="stab3", manifest_digest=manifest.digest(), inputs=["c1"], runs=1, raw_digests={"c1": ["a" * 64]}, volatile_paths=[], proposals=[], uncovered_volatile=[], problems=[])
        self.assertEqual(wf.Workflow._stability_result(requirement, manifest, capture).status, c.UNVERIFIABLE)


class PerformanceClaim(Sandbox):
    CORPUS = {"id": "corpus", "kind": "corpus_equivalence", "mandatory": True}

    def test_performance_is_measured_under_the_declared_protocol(self) -> None:
        manifest = spec("perf", claims=[self.CORPUS, {"id": "perf", "kind": "performance_envelope", "mandatory": True, "params": {"runs": 2, "rel_tolerance": 5.0, "abs_tolerance_s": 2.0}}])
        self.flow.create(manifest, self.roots(self.same))
        self.flow.characterize("perf")
        self.flow.freeze("perf")
        self.flow.compare("perf")
        result = self.flow.performance("perf")
        self.assertEqual(result.status, c.PRESERVED_WITHIN_ENVELOPE, result)
        protocol = result.coverage["protocol"]
        self.assertEqual((protocol["runs"], protocol["statistic"], protocol["rel_tolerance"], protocol["abs_tolerance_s"]), (2, "median", 5.0, 2.0))
        self.assertEqual(sorted(result.coverage["per_input"]), ["edge", "large", "small"])
        self.assertGreater(result.coverage["source_wall_s"], 0.0)
        self.assertEqual(len(self.evidence.observations("perf", kind="raw", prefix="perf:")), 12)
        self.assertEqual(self.flow.verdict("perf").status, PASS)

    def test_a_slower_target_diverges_on_performance(self) -> None:
        slow = self.root / "slow"
        write(slow / "app.py", "import time; time.sleep(0.6)\n" + APP.replace("BONUS", "True"))
        manifest = spec("perf-slow", claims=[self.CORPUS, {"id": "perf", "kind": "performance_envelope", "mandatory": True, "params": {"runs": 1, "rel_tolerance": 0.0, "abs_tolerance_s": 0.2}}])
        self.flow.create(manifest, self.roots(slow))
        self.flow.characterize("perf-slow")
        self.flow.freeze("perf-slow")
        result = self.flow.performance("perf-slow")
        self.assertEqual(result.status, c.DIVERGED)
        self.assertEqual(result.divergences[0]["path"], "/timing/wall_s")
        self.assertEqual(self.flow.verdict("perf-slow").status, BLOCK)

    def test_without_a_performance_claim_nothing_claims_performance(self) -> None:
        self.flow.create(spec("perf-none", claims=[self.CORPUS]), self.roots(self.same))
        self.flow.characterize("perf-none")
        self.flow.freeze("perf-none")
        self.flow.compare("perf-none")
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.performance("perf-none")
        self.assertEqual(caught.exception.reason, "no_performance_claim")
        section = self.flow.report("perf-none")["summary"]["sections"][1]
        self.assertEqual(section["status"], "not_verified")


class RepairSessions(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.name", "Tester", cwd=self.repo)
        git("config", "user.email", "t@example.com", cwd=self.repo)
        write(self.repo / "app.py", APP.replace("BONUS", "True"))
        write(self.repo / "README.md", "# ugly\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "ugly but working", cwd=self.repo)
        self.base_commit = git("rev-parse", "HEAD", cwd=self.repo)
        self.workspace = self.root / "repair-ws"

    def start(self, session_id: str = "rep") -> None:
        self.flow.repair_init(self.repo, spec(session_id), workspace=self.workspace)
        self.flow.characterize(session_id, runs=2)
        self.flow.freeze(session_id)
        self.flow.analyze(session_id, [{"id": "mix-1", "kind": "mixed_responsibilities", "paths": ["app.py"], "summary": "pricing lives inside the CLI", "declared_by": "host-agent"}])
        self.flow.plan(
            session_id,
            [
                {"id": "u1", "objective": "pricing 분리 / extract pricing", "reason": "mix-1", "risk": "low", "owned_paths": ["app.py", "pricing.py"], "expected_behavior_impact": "none"},
                {"id": "u2", "objective": "threshold cleanup", "reason": "mix-1", "risk": "medium", "owned_paths": ["pricing.py"], "expected_behavior_impact": "none"},
            ],
        )

    def test_init_refuses_a_dirty_tree(self) -> None:
        write(self.repo / "wip.txt", "uncommitted")
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.repair_init(self.repo, spec("rep-dirty"), workspace=self.workspace)
        self.assertEqual(caught.exception.reason, "dirty_tree")

    def test_a_valid_refactor_is_verified_and_accepted_then_a_wrong_one_is_rejected_without_contamination(self) -> None:
        self.start()
        snapshot = self.flow.snapshot("rep")
        self.assertEqual(snapshot.state, "PLANNED")
        self.assertEqual(snapshot.base_commit, self.base_commit)

        unit = self.flow.unit_start("rep", "u1")
        self.assertEqual(self.flow.snapshot("rep").state, "UNIT_IN_PROGRESS")
        write(unit / "app.py", REFACTORED)
        write(unit / "pricing.py", PRICING)
        verdict = self.flow.unit_verify("rep", "u1")
        self.assertEqual((verdict.status, verdict.decided_by), (PASS, "preserved"), verdict)
        record = self.flow.snapshot("rep").units["u1"]
        self.assertEqual(record["metrics_delta"]["files"]["after"], 2)
        commit = self.flow.unit_accept("rep", "u1")
        self.assertEqual(self.flow.snapshot("rep").accepted_commit, commit)
        self.assertEqual(git("rev-parse", "refs/invara/repair/rep/accepted", cwd=self.repo), commit)
        accepted_tree = git("rev-parse", f"{commit}^{{tree}}", cwd=self.repo)
        self.assertFalse(unit.exists())

        self.flow.continue_("rep")
        unit2 = self.flow.unit_start("rep", "u2")
        self.assertEqual((unit2 / "pricing.py").read_bytes(), PRICING.encode("utf-8"))
        write(unit2 / "pricing.py", PRICING_WRONG)
        verdict2 = self.flow.unit_verify("rep", "u2")
        self.assertEqual((verdict2.status, verdict2.decided_by), (BLOCK, "diverged"))
        unit_record = self.flow.snapshot("rep").units["u2"]
        search_result = next(r for r in unit_record["claim_results"] if r["kind"] == "counterexample_search")
        self.assertEqual(search_result["status"], c.DIVERGED)
        self.assertEqual(sum(i["qty"] * i["price"] for i in search_result["counterexample"]["minimized"]["input"]["items"]), 100)
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.unit_accept("rep", "u2")
        self.assertEqual(caught.exception.reason, "unit_not_accepted")
        rejected = self.flow.unit_reject("rep", "u2", reason="diverged at /out/value/total")
        self.assertTrue(Path(rejected["patch_path"]).is_file())
        self.assertEqual(self.flow.snapshot("rep").state, "UNIT_ROLLED_BACK")
        self.assertEqual(git("rev-parse", "refs/invara/repair/rep/accepted", cwd=self.repo), commit)
        self.assertEqual(git("rev-parse", f"{commit}^{{tree}}", cwd=self.repo), accepted_tree)
        self.assertFalse(unit2.exists())

        finished = self.flow.finish("rep")
        self.assertEqual(finished.state, "COMPLETED")
        self.assertEqual(git("rev-parse", "refs/heads/invara/repair/rep", cwd=self.repo), commit)
        self.assertEqual(git("branch", "--show-current", cwd=self.repo), "main")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.base_commit)
        data = self.flow.report("rep")
        cleaned = data["summary"]["sections"][2]
        reverted = data["summary"]["sections"][3]
        self.assertTrue(any("pricing 분리" in line for line in cleaned["lines_ko"]))
        self.assertTrue(any("threshold cleanup" in line for line in reverted["lines_ko"]))
        self.assertEqual(data["technical"]["units"]["accepted"][0]["commit"], commit)
        self.assertTrue(self.evidence.verify()["ok"])

    def test_a_repairer_cannot_declare_its_own_pass(self) -> None:
        self.start("rep-self")
        unit = self.flow.unit_start("rep-self", "u1")
        write(unit / "app.py", REFACTORED)
        write(unit / "pricing.py", PRICING)
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.unit_accept("rep-self", "u1")
        self.assertEqual(caught.exception.reason, "unit_not_verified")
        self.flow.unit_verify("rep-self", "u1")
        write(unit / "pricing.py", PRICING_WRONG)
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.unit_accept("rep-self", "u1")
        self.assertEqual(caught.exception.reason, "stale_verification")
        self.assertEqual(self.flow.snapshot("rep-self").accepted_commit, self.base_commit)

    def test_a_unit_that_touches_paths_it_does_not_own_is_blocked(self) -> None:
        self.start("rep-own")
        unit = self.flow.unit_start("rep-own", "u2")
        write(unit / "pricing.py", PRICING)
        write(unit / "app.py", REFACTORED)
        verdict = self.flow.unit_verify("rep-own", "u2")
        self.assertEqual((verdict.status, verdict.decided_by), (BLOCK, "constraint_breaks"))
        self.assertIn("app.py", verdict.constraint_breaks[0])

    def test_an_interrupted_unit_resumes_where_it_was(self) -> None:
        self.start("rep-resume")
        unit = self.flow.unit_start("rep-resume", "u1")
        write(unit / "app.py", REFACTORED)
        write(unit / "pricing.py", PRICING)
        fresh = wf.Workflow(ev.Evidence(self.db), workspace_parent=self.root / "ws2")
        try:
            resumed = fresh.resume("rep-resume")
            self.assertEqual(resumed["state"], "UNIT_IN_PROGRESS")
            self.assertEqual(resumed["problems"], [])
            self.assertEqual(resumed["current_unit"], "u1")
            self.assertEqual(fresh.unit_verify("rep-resume", "u1").status, PASS)
            self.assertEqual(fresh.unit_accept("rep-resume", "u1"), fresh.snapshot("rep-resume").accepted_commit)
        finally:
            fresh.evidence.close()

    def test_an_acceptance_interrupted_after_the_ref_moved_is_reconciled(self) -> None:
        self.start("rep-crash")
        unit = self.flow.unit_start("rep-crash", "u1")
        write(unit / "app.py", REFACTORED)
        write(unit / "pricing.py", PRICING)
        self.flow.unit_verify("rep-crash", "u1")
        tree = self.flow.snapshot("rep-crash").units["u1"]["tree"]
        commit = git("commit-tree", tree, "-p", self.base_commit, "-m", "u1", cwd=self.repo)
        git("update-ref", "refs/invara/repair/rep-crash/accepted", commit, self.base_commit, cwd=self.repo)
        fresh = wf.Workflow(ev.Evidence(self.db), workspace_parent=self.root / "ws3")
        try:
            resumed = fresh.resume("rep-crash")
            self.assertEqual(resumed["state"], "UNIT_ACCEPTED")
            self.assertEqual(fresh.snapshot("rep-crash").accepted_commit, commit)
            self.assertTrue(any("reconciled" in note for note in resumed["notes"]))
        finally:
            fresh.evidence.close()

    def test_a_vanished_worktree_blocks_the_session_rather_than_guessing(self) -> None:
        self.start("rep-gone")
        unit = self.flow.unit_start("rep-gone", "u1")
        self.flow.governor.remove_worktree(unit)
        resumed = self.flow.resume("rep-gone")
        self.assertEqual(resumed["state"], "BLOCKED")
        self.assertTrue(any("worktree" in p for p in resumed["problems"]))
        self.assertEqual(self.flow.snapshot("rep-gone").accepted_commit, self.base_commit)


if __name__ == "__main__":
    unittest.main()
