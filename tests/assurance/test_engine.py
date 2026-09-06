"""Characterize, freeze, compare: the engine over a corpus."""

from __future__ import annotations

import json
import sqlite3
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

APP = textwrap.dedent(
    '''
    import json, os, sys, uuid
    from datetime import datetime, timezone
    data = json.load(sys.stdin)
    total = sum(item["qty"] * item["price"] for item in data.get("items", []))
    if total >= 100 and BONUS:
        total = total - 10
    out = {"order_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "total": total,
           "count": len(data.get("items", []))}
    print(json.dumps(out))
    '''
)


def write_app(root: Path, bonus: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(APP.replace("BONUS", "True" if bonus else "False"), encoding="utf-8")


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "before"
        self.same = self.root / "after-same"
        self.wrong = self.root / "after-wrong"
        write_app(self.source, bonus=True)
        write_app(self.same, bonus=True)
        write_app(self.wrong, bonus=False)
        self.evidence = ev.Evidence(self.root / "verify.db")
        self.engine = eng.Engine(self.evidence, workspace_parent=self.root / "ws")

    def tearDown(self) -> None:
        self.evidence.close()
        self._tmp.cleanup()

    def manifest(self, *policies: dict, **over) -> m.Manifest:
        sections = dict(
            session_id="eng",
            source_system={"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT"},
            target_system={"same_as_source": True},
            input_domain={
                "kind": "corpus",
                "delivery": "stdin_json",
                "corpus": [
                    {"id": "small", "input": {"items": [{"qty": 1, "price": 5}]}},
                    {"id": "large", "input": {"items": [{"qty": 20, "price": 5}, {"qty": 1, "price": 30}]}},
                ],
            },
            probes=[{"id": "cli", "adapter": "process", "capture": ["stderr"], "mandatory": True}, {"id": "out", "adapter": "json", "source": "stdout", "mandatory": True}],
            policies=list(policies) or [policy("generated_id", "/out/value/order_id", pattern="uuid", id="ids"), policy("timestamp", "/out/value/created_at", id="ts")],
        )
        sections.update(over)
        return m.Manifest.from_dict(manifest_dict(**sections))

    def roots(self, target: Path) -> dict[str, str]:
        return {"SOURCE_ROOT": str(self.source), "TARGET_ROOT": str(target)}


class Characterize(Sandbox):
    def test_the_baseline_records_one_raw_observation_per_input_per_run(self) -> None:
        manifest = self.manifest()
        capture = self.engine.characterize("eng", manifest, self.roots(self.same), runs=2)
        rows = self.evidence.observations("eng", kind="raw", prefix="baseline:")
        self.assertEqual(sorted(r["run_key"] for r in rows), ["baseline:before:large:1", "baseline:before:large:2", "baseline:before:small:1", "baseline:before:small:2"])
        self.assertEqual(sorted(capture.inputs), ["large", "small"])
        self.assertEqual(capture.runs, 2)

    def test_raw_traces_differ_between_runs_and_the_volatility_is_named(self) -> None:
        capture = self.engine.characterize("eng", self.manifest(), self.roots(self.same), runs=2)
        self.assertNotEqual(capture.raw_digests["small"][0], capture.raw_digests["small"][1])
        self.assertEqual(capture.volatile_paths, ["/out/value/created_at", "/out/value/order_id"])
        kinds = {p["path"]: p["kind"] for p in capture.proposals}
        self.assertEqual(kinds["/out/value/order_id"], "generated_id")
        self.assertEqual(kinds["/out/value/created_at"], "timestamp")
        self.assertTrue(all(not p["accepted"] for p in capture.proposals))

    def test_uncovered_volatility_is_reported_against_the_accepted_policies(self) -> None:
        capture = self.engine.characterize("eng", self.manifest(policy("generated_id", "/out/value/order_id", pattern="uuid")), self.roots(self.same), runs=2)
        self.assertEqual(capture.uncovered_volatile, ["/out/value/created_at"])
        covered = self.engine.characterize("eng2", self.manifest(), self.roots(self.same), runs=2)
        self.assertEqual(covered.uncovered_volatile, [])

    def test_a_source_that_cannot_run_is_captured_as_such(self) -> None:
        manifest = self.manifest(source_system={"id": "before", "kind": "process", "command": ["invara-no-such-program"], "root": "$SOURCE_ROOT"}, target_system={"same_as_source": True})
        capture = self.engine.characterize("eng", manifest, self.roots(self.same))
        self.assertEqual(capture.problems, ["small: unrunnable", "large: unrunnable"])


class Freeze(Sandbox):
    def test_freezing_yields_a_baseline_digest_over_the_raw_records(self) -> None:
        manifest = self.manifest()
        self.engine.characterize("eng", manifest, self.roots(self.same))
        frozen = self.engine.freeze("eng", manifest)
        self.assertEqual(len(frozen.baseline_digest), 64)
        self.assertEqual(frozen.manifest_digest, manifest.digest())
        self.assertEqual(sorted(frozen.inputs), ["large", "small"])
        self.assertEqual(frozen.baseline_digest, self.engine.baseline_digest("eng"))

    def test_freeze_refuses_a_policy_set_that_erases_signal(self) -> None:
        manifest = self.manifest(policy("redact", "/out/value/**", id="all"), policy("ignore", "/cli/exit_code", id="x"), policy("ignore", "/cli/stdout", id="y"), policy("ignore", "/cli/stderr", id="z"))
        self.engine.characterize("eng", manifest, self.roots(self.same))
        with self.assertRaises(eng.EngineError) as caught:
            self.engine.freeze("eng", manifest)
        self.assertEqual(caught.exception.reason, "normalization_erases_signal")

    def test_freeze_needs_a_captured_baseline(self) -> None:
        with self.assertRaises(eng.EngineError) as caught:
            self.engine.freeze("eng", self.manifest())
        self.assertEqual(caught.exception.reason, "no_baseline")

    def test_freeze_refuses_a_baseline_with_unobserved_runs(self) -> None:
        manifest = self.manifest(source_system={"id": "before", "kind": "process", "command": ["invara-no-such-program"], "root": "$SOURCE_ROOT"}, target_system={"same_as_source": True})
        self.engine.characterize("eng", manifest, self.roots(self.same))
        with self.assertRaises(eng.EngineError) as caught:
            self.engine.freeze("eng", manifest)
        self.assertEqual(caught.exception.reason, "baseline_unobserved")


class CompareCorpus(Sandbox):
    def frozen(self, manifest: m.Manifest | None = None) -> tuple[m.Manifest, eng.Frozen]:
        manifest = manifest or self.manifest()
        self.engine.characterize("eng", manifest, self.roots(self.same))
        return manifest, self.engine.freeze("eng", manifest)

    def test_an_equivalent_target_is_preserved_within_the_envelope(self) -> None:
        manifest, frozen = self.frozen()
        result = self.engine.compare_corpus("eng", manifest, frozen, self.roots(self.same))
        self.assertEqual(result.status, c.PRESERVED_WITHIN_ENVELOPE)
        self.assertEqual((result.coverage["kind"], result.coverage["members"], result.coverage["compared"]), ("corpus", 2, 2))
        self.assertEqual(result.manifest_digest, manifest.digest())
        self.assertEqual(result.baseline_digest, frozen.baseline_digest)
        self.assertEqual(len(result.evidence_digests), 2)

    def test_a_changed_target_diverges_with_a_located_divergence(self) -> None:
        manifest, frozen = self.frozen()
        result = self.engine.compare_corpus("eng", manifest, frozen, self.roots(self.wrong))
        self.assertEqual(result.status, c.DIVERGED)
        (divergence,) = result.divergences
        self.assertEqual(divergence["input_id"], "large")
        self.assertEqual(divergence["path"], "/out/value/total")
        self.assertEqual((divergence["raw_source"], divergence["raw_target"]), (120, 130))
        self.assertTrue(divergence["mandatory"])

    def test_the_comparisons_are_stored_as_evidence(self) -> None:
        manifest, frozen = self.frozen()
        self.engine.compare_corpus("eng", manifest, frozen, self.roots(self.wrong))
        rows = self.evidence.observations("eng", kind="raw", prefix="compare:after:")
        self.assertEqual(len(rows), 2)
        comparisons = self.evidence.observations("eng", kind="comparison", prefix="compare:")
        self.assertEqual(len(comparisons), 2)

    def test_a_target_that_cannot_run_is_unverifiable(self) -> None:
        manifest, frozen = self.frozen()
        missing = self.root / "after-missing"
        result = self.engine.compare_corpus("eng", manifest, frozen, self.roots(missing))
        self.assertEqual(result.status, c.UNVERIFIABLE)
        self.assertTrue(any("small" in item for item in result.unverified))

    def test_performance_is_measured_and_reported(self) -> None:
        manifest, frozen = self.frozen()
        result = self.engine.compare_corpus("eng", manifest, frozen, self.roots(self.same))
        timing = result.coverage.get("timing") or {}
        self.assertIn("source_wall_s", timing)
        self.assertIn("target_wall_s", timing)


class Integrity(Sandbox):
    def test_tampering_with_the_frozen_baseline_is_detected(self) -> None:
        manifest = self.manifest()
        self.engine.characterize("eng", manifest, self.roots(self.same))
        frozen = self.engine.freeze("eng", manifest)
        self.assertEqual(self.engine.integrity_problems("eng", frozen), [])
        connection = sqlite3.connect(self.root / "verify.db")
        row = connection.execute("SELECT seq, record_json FROM assurance_observation WHERE run_key LIKE 'baseline:%' LIMIT 1").fetchone()
        forged = json.loads(row[1])
        forged["probes"]["out"]["value"]["total"] = 0
        connection.execute("UPDATE assurance_observation SET record_json = ? WHERE seq = ?", (json.dumps(forged), row[0]))
        connection.commit()
        connection.close()
        problems = self.engine.integrity_problems("eng", frozen)
        self.assertTrue(problems)
        self.assertTrue(any("baseline" in p for p in problems))

    def test_a_missing_baseline_record_is_an_integrity_problem(self) -> None:
        manifest = self.manifest()
        self.engine.characterize("eng", manifest, self.roots(self.same))
        frozen = self.engine.freeze("eng", manifest)
        connection = sqlite3.connect(self.root / "verify.db")
        connection.execute("DELETE FROM assurance_observation WHERE run_key = 'baseline:before:small:1'")
        connection.commit()
        connection.close()
        self.assertTrue(self.engine.integrity_problems("eng", frozen))


class Differential(Sandbox):
    def test_a_fresh_input_runs_both_systems_and_compares(self) -> None:
        manifest = self.manifest()
        item = m.CorpusItem(id="probe-1", input={"items": [{"qty": 20, "price": 5}]})
        comparison = self.engine.differential("eng", manifest, item, self.roots(self.wrong), phase="search")
        self.assertEqual(comparison.status, "compared")
        self.assertFalse(comparison.equivalent)
        self.assertEqual(comparison.divergences[0].path, "/out/value/total")
        self.assertEqual(comparison.input_id, "probe-1")
        self.assertEqual(len(self.evidence.observations("eng", kind="raw", prefix="search:")), 2)


if __name__ == "__main__":
    unittest.main()
