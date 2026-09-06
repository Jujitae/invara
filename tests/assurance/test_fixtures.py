"""The five proving fixtures, run as products rather than read as documents."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import policy
from invara.assurance import claims as c
from invara.assurance import compare as comparison
from invara.assurance import evidence as ev
from invara.assurance import manifest as m
from invara.assurance import workflow as wf
from invara.contract import BLOCK, HUMAN_REVIEW, PASS

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


def manifest_dict(name: str, **over) -> dict:
    data = json.loads((FIXTURES / "manifests" / name).read_text(encoding="utf-8"))
    for key in ("source_system", "target_system"):
        command = data[key].get("command")
        if command and command[0] == "python":
            command[0] = sys.executable
    data.update(over)
    return data


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def unittest_passes(directory: Path, module: str) -> bool:
    import os

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-m", "unittest", "-q", module], cwd=directory, capture_output=True, text=True, encoding="utf-8", env=env)
    return result.returncode == 0


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.db = self.root / "verify.db"
        self.evidence = ev.Evidence(self.db)
        self.flow = wf.Workflow(self.evidence, workspace_parent=self.root / "ws")

    def tearDown(self) -> None:
        self.evidence.close()
        self._tmp.cleanup()

    def roots(self, source: Path, target: Path) -> dict[str, str]:
        return {"SOURCE_ROOT": str(source), "TARGET_ROOT": str(target)}


class FixtureA(Sandbox):
    """The ugly but working application, characterized."""

    def test_the_existing_test_suite_passes(self) -> None:
        self.assertTrue(unittest_passes(FIXTURES / "ugly_shop", "test_app"))

    def test_raw_runs_differ_by_benign_noise_and_the_policies_remove_only_that(self) -> None:
        manifest = m.Manifest.from_dict(manifest_dict("shop.json", session_id="fixture-a"))
        self.flow.create(manifest, self.roots(FIXTURES / "ugly_shop", FIXTURES / "ugly_shop"))
        capture = self.flow.characterize("fixture-a", runs=2)
        self.assertEqual(capture.problems, [])
        for input_id, digests in capture.raw_digests.items():
            self.assertNotEqual(digests[0], digests[1], f"{input_id}: two raw runs should not be byte-identical")
        for path in ("/out/value/created_at", "/out/value/order_id", "/db/tables/orders/rows/0/created_at", "/db/tables/orders/rows/0/order_id", "/files/entries/receipt.txt/text"):
            self.assertIn(path, capture.volatile_paths)
        self.assertEqual(capture.uncovered_volatile, [], "every volatile dimension is covered by a declared, reasoned policy")
        kinds = {p["kind"] for p in capture.proposals}
        self.assertTrue({"generated_id", "timestamp"} <= kinds)
        self.assertTrue(all(not p["accepted"] for p in capture.proposals))
        for input_id in capture.inputs:
            one = self.evidence.observation("fixture-a", "raw", f"baseline:before:{input_id}:1")["record"]
            two = self.evidence.observation("fixture-a", "raw", f"baseline:before:{input_id}:2")["record"]
            result = comparison.compare(one, two, manifest)
            self.assertTrue(result.equivalent, f"{input_id}: {[d.as_dict() for d in result.divergences]}")
            if input_id != "invalid-qty":
                applied = {action["kind"] for action in result.source_actions}
                self.assertTrue({"generated_id", "timestamp", "unordered_multiset", "ignore"} <= applied, applied)
        frozen = self.flow.freeze("fixture-a")
        # normalized records are keyed by the policy set they were made under (X10 S3): find the run by prefix
        normalized = self.evidence.observations("fixture-a", kind="normalized", prefix="baseline:before:bulk-gold:1")[0]["record"]
        self.assertTrue(normalized["actions"], "the normalization audit is stored with the baseline")
        self.assertEqual(normalized["id_maps"]["order-ids"][0][1], "<id:1>")
        self.assertEqual(normalized["probes"]["out"]["value"]["order_id"], "<id:1>")
        self.assertEqual(normalized["probes"]["db"]["tables"]["orders"]["rows"][0]["order_id"], "<id:1>")
        self.assertIn("ORDER <id:1>", normalized["probes"]["files"]["entries"]["receipt.txt"]["text"])
        self.assertEqual(normalized["probes"]["out"]["value"]["created_at"], "<timestamp>")
        self.assertEqual(len(frozen.inputs), 6)
        stability = next(r for r in self.flow.snapshot("fixture-a").active_claim_results() if r["kind"] == "baseline_stability")
        self.assertEqual(stability["status"], c.NO_DIVERGENCE_FOUND)

    def test_the_valid_refactor_compares_equal_and_the_audit_is_in_the_report(self) -> None:
        manifest = m.Manifest.from_dict(manifest_dict("shop.json", session_id="fixture-ab"))
        self.flow.create(manifest, self.roots(FIXTURES / "ugly_shop", FIXTURES / "clean_shop"))
        self.flow.characterize("fixture-ab", runs=2)
        self.flow.freeze("fixture-ab")
        corpus = self.flow.compare("fixture-ab")
        self.assertEqual(corpus.status, c.PRESERVED_WITHIN_ENVELOPE, corpus.divergences)
        self.assertEqual(corpus.coverage["compared"], 6)
        search = self.flow.search("fixture-ab")
        self.assertEqual(search.status, c.NO_DIVERGENCE_FOUND, search.counterexample)
        verdict = self.flow.verdict("fixture-ab")
        self.assertEqual(verdict.status, PASS, verdict)
        data = self.flow.report("fixture-ab")
        self.assertEqual([p["id"] for p in data["technical"]["policies"]], ["order-ids", "row-ids", "timestamps", "tags-order", "receipt-digest"])
        unverified = data["summary"]["sections"][4]
        self.assertTrue(any("stderr-text" in line for line in unverified["lines_en"]), "the declared exclusion is reported as not verified")
        self.assertEqual(data["summary"]["verdict"]["status"], PASS)


class FixturesBAndC(Sandbox):
    """A governed repair: the valid refactor is accepted, the wrong one is rejected."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.root / "shop"
        self.repo.mkdir()
        for name in ("app.py", "test_app.py"):
            shutil.copyfile(FIXTURES / "ugly_shop" / name, self.repo / name)
        (self.repo / ".gitignore").write_bytes(b"__pycache__/\n")
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.name", "Tester", cwd=self.repo)
        git("config", "user.email", "t@example.com", cwd=self.repo)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "ugly but working", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo)

    def test_accept_the_valid_refactor_then_reject_the_wrong_one_without_contamination(self) -> None:
        manifest = m.Manifest.from_dict(manifest_dict("shop.json", session_id="shop-repair"))
        self.flow.repair_init(self.repo, manifest, workspace=self.root / "repair-ws")
        self.flow.characterize("shop-repair", runs=2)
        self.flow.freeze("shop-repair")
        analysis = self.flow.analyze(
            "shop-repair",
            [
                {"id": "dup-pricing", "kind": "duplicate_implementation", "paths": ["app.py"], "summary": "quote() and commit_order() carry the same pricing loop", "declared_by": "host-agent"},
                {"id": "mixed", "kind": "mixed_responsibilities", "paths": ["app.py"], "summary": "pricing, storage and formatting share one function", "declared_by": "host-agent"},
            ],
        )
        self.assertGreaterEqual(analysis["metrics"]["duplicate_blocks"]["count"], 1)
        self.flow.plan(
            "shop-repair",
            [
                {"id": "u1", "objective": "가격 계산·저장 분리 / split pricing and storage out of app.py", "reason": "dup-pricing, mixed", "risk": "low", "owned_paths": ["app.py", "pricing.py", "storage.py", "test_app.py", "test_pricing.py"], "expected_behavior_impact": "none"},
                {"id": "u2", "objective": "할인 기준 정리 / tidy the discount threshold", "reason": "dup-pricing", "risk": "medium", "owned_paths": ["pricing.py"], "expected_behavior_impact": "none"},
            ],
        )

        unit = self.flow.unit_start("shop-repair", "u1")
        for name in ("app.py", "pricing.py", "storage.py", "test_pricing.py"):
            shutil.copyfile(FIXTURES / "clean_shop" / name, unit / name)
        (unit / "test_app.py").unlink()
        self.assertTrue(unittest_passes(unit, "test_pricing"))
        verdict = self.flow.unit_verify("shop-repair", "u1")
        self.assertEqual((verdict.status, verdict.decided_by), (PASS, "preserved"), verdict)
        record = self.flow.snapshot("shop-repair").units["u1"]
        delta = record["metrics_delta"]
        self.assertLess(delta["duplicate_blocks"]["after"], delta["duplicate_blocks"]["before"])
        self.assertLess(delta["largest_module_lines"]["after"], delta["largest_module_lines"]["before"])
        self.assertGreater(delta["files"]["after"], delta["files"]["before"])
        commit = self.flow.unit_accept("shop-repair", "u1")
        accepted_tree = git("rev-parse", f"{commit}^{{tree}}", cwd=self.repo)
        self.assertEqual(git("rev-parse", "refs/invara/repair/shop-repair/accepted", cwd=self.repo), commit)

        self.flow.continue_("shop-repair")
        unit2 = self.flow.unit_start("shop-repair", "u2")
        shutil.copyfile(FIXTURES / "wrong_shop" / "pricing.py", unit2 / "pricing.py")
        self.assertTrue(unittest_passes(unit2, "test_pricing"), "the wrong refactor still passes the existing tests")
        verdict2 = self.flow.unit_verify("shop-repair", "u2")
        self.assertEqual((verdict2.status, verdict2.decided_by), (BLOCK, "diverged"), verdict2)
        results = {r["kind"]: r for r in self.flow.snapshot("shop-repair").units["u2"]["claim_results"]}
        self.assertEqual(results["corpus_equivalence"]["status"], c.PRESERVED_WITHIN_ENVELOPE, "the recorded corpus does not reach the boundary")
        self.assertEqual(results["counterexample_search"]["status"], c.DIVERGED)
        minimized = results["counterexample_search"]["counterexample"]["minimized"]["input"]
        self.assertTrue(any(line["qty"] == 10 for line in minimized["items"]), minimized)
        self.assertIn(minimized["customer"]["tier"], ("gold", "silver"))
        self.assertEqual(len(minimized["items"]), 1)
        paths = {d["path"] for d in results["counterexample_search"]["divergences"]}
        self.assertIn("/out/value/total", paths)
        with self.assertRaises(wf.WorkflowError) as caught:
            self.flow.unit_accept("shop-repair", "u2")
        self.assertEqual(caught.exception.reason, "unit_not_accepted")
        rejected = self.flow.unit_reject("shop-repair", "u2", reason="behaviour diverged at the discount boundary")
        self.assertIn(b"qty > DISCOUNT_MIN_QTY", Path(rejected["patch_path"]).read_bytes())
        self.assertEqual(git("rev-parse", "refs/invara/repair/shop-repair/accepted", cwd=self.repo), commit)
        self.assertEqual(git("rev-parse", f"{commit}^{{tree}}", cwd=self.repo), accepted_tree)
        self.assertFalse(unit2.exists())

        finished = self.flow.finish("shop-repair")
        self.assertEqual(finished.state, "COMPLETED")
        self.assertEqual(finished.final_verdict["status"], PASS)
        self.assertEqual(git("rev-parse", "refs/heads/invara/repair/shop-repair", cwd=self.repo), commit)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), self.base, "the user's checkout never moved")
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        data = self.flow.report("shop-repair")
        self.assertTrue(any("가격 계산·저장 분리" in line for line in data["summary"]["sections"][2]["lines_ko"]))
        self.assertTrue(any("할인 기준 정리" in line for line in data["summary"]["sections"][3]["lines_ko"]))
        self.assertEqual(data["technical"]["units"]["rejected"][0]["patch_digest"], rejected["patch_digest"])
        self.assertTrue(self.evidence.verify()["ok"])


class FixtureD(Sandbox):
    """Normalization abuse against a target that really diverges."""

    def setUp(self) -> None:
        super().setUp()
        manifest = m.Manifest.from_dict(manifest_dict("shop.json", session_id="abuse"))
        self.flow.create(manifest, self.roots(FIXTURES / "ugly_shop", FIXTURES / "wrong_shop"))
        self.flow.characterize("abuse")
        self.flow.freeze("abuse")
        self.assertEqual(self.flow.compare("abuse").status, c.PRESERVED_WITHIN_ENVELOPE)
        self.assertEqual(self.flow.search("abuse").status, c.DIVERGED)
        self.assertEqual(self.flow.verdict("abuse").status, BLOCK)

    def amend(self, *policies: dict) -> None:
        current = self.flow.manifest("abuse")
        self.flow.amend("abuse", {"requested_by": "repairer", "reason": "make it pass", "changes": {"policies": [p.as_dict() for p in current.policies] + list(policies)}})

    def test_blanket_ignore_is_refused(self) -> None:
        with self.assertRaises(wf.WorkflowError) as caught:
            self.amend(policy("ignore", "/**", id="blanket"))
        self.assertEqual(caught.exception.reason, "blanket_ignore")

    def test_root_ignore_is_refused(self) -> None:
        with self.assertRaises(wf.WorkflowError) as caught:
            self.amend(policy("ignore", "/", id="root"))
        self.assertEqual(caught.exception.reason, "root_ignore")

    def test_overbroad_tolerance_is_refused(self) -> None:
        with self.assertRaises(wf.WorkflowError) as caught:
            self.amend(policy("numeric_rel_tolerance", "/out/value/total", rel=1.5, id="anything"))
        self.assertEqual(caught.exception.reason, "overbroad_tolerance")

    def test_a_policy_added_after_the_divergence_cannot_produce_a_pass(self) -> None:
        self.amend(
            policy("ignore", "/out/value/total", id="hide-total"),
            policy("ignore", "/out/value/lines/*/line_total", id="hide-lines"),
            policy("ignore", "/db/tables/orders/rows/*/total", id="hide-db-total"),
            policy("ignore", "/db/tables/order_lines/rows/*/line_total", id="hide-db-lines"),
            policy("redact", "/files/entries/*/text", id="hide-receipt"),
        )
        snapshot = self.flow.snapshot("abuse")
        self.assertTrue(snapshot.post_divergence, "the amendment covers paths that already diverged")
        self.assertEqual(
            [r["kind"] for r in snapshot.active_claim_results()],
            ["baseline_stability"],
            "results under the old manifest are invalidated; only stability, re-derived from the stored capture, is active",
        )
        self.flow.compare("abuse")
        self.flow.search("abuse")
        verdict = self.flow.verdict("abuse")
        self.assertNotEqual(verdict.status, PASS)
        self.assertEqual(verdict.status, HUMAN_REVIEW)
        self.assertTrue(any("post-divergence" in note for note in verdict.needs_human))


class FixtureE(Sandbox):
    """A finite domain, exhaustively compared."""

    def run_proof(self, target: str, session_id: str) -> tuple[c.ClaimResult, c.FinalVerdict]:
        data = manifest_dict("finite.json", session_id=session_id)
        data["target_system"]["command"] = [sys.executable, target]
        manifest = m.Manifest.from_dict(data)
        self.flow.create(manifest, self.roots(FIXTURES / "finite", FIXTURES / "finite"))
        capture = self.flow.characterize(session_id)
        self.assertEqual(len(capture.inputs), 80, "every member of the domain is part of the baseline")
        self.flow.freeze(session_id)
        result = self.flow.prove(session_id)
        return result, self.flow.verdict(session_id)

    def test_an_equivalent_rewrite_is_proved_within_the_declared_domain(self) -> None:
        result, verdict = self.run_proof("target_ok.py", "shipping-ok")
        self.assertEqual(result.status, c.PROVED_WITHIN_DECLARED_DOMAIN, result.detail)
        self.assertEqual((result.coverage["kind"], result.coverage["members"], result.coverage["cardinality"]), ("exhaustive", 80, 80))
        self.assertEqual(len(result.coverage["domain_digest"]), 64)
        self.assertEqual((verdict.status, verdict.decided_by), (PASS, "proved"))
        data = self.flow.report("shipping-ok")
        self.assertEqual(data["technical"]["coverage"]["exhaustively_proved"], ["domain"])
        self.assertTrue(any("80" in line for line in data["summary"]["sections"][0]["lines_ko"]))

    def test_a_wrong_rewrite_yields_the_minimal_diverging_member(self) -> None:
        result, verdict = self.run_proof("target_bad.py", "shipping-bad")
        self.assertEqual(result.status, c.DIVERGED)
        self.assertEqual(result.counterexample["diverging_count"], 2)
        member = result.counterexample["minimized"]["input"]
        self.assertEqual((member["zone"], member["weight_class"]), (5, 8))
        self.assertEqual(result.divergences[0]["path"], "/out/value/cost")
        self.assertEqual((verdict.status, verdict.decided_by), (BLOCK, "diverged"))


if __name__ == "__main__":
    unittest.main()
