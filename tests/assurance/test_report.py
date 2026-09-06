"""Two layers: plain language first, then everything a verifier would ask for."""

from __future__ import annotations

import json
import unittest

from _support import manifest_dict, policy
from invara.assurance import claims as c
from invara.assurance import manifest as m
from invara.assurance import report
from invara.assurance import session as s
from invara.contract import BLOCK, HUMAN_REVIEW, PASS, UNVERIFIABLE

MANIFEST = m.Manifest.from_dict(
    manifest_dict(
        session_id="rep",
        policies=[policy("generated_id", "/out/value/id", pattern="uuid", id="ids")],
        claims=[
            {"id": "corpus", "kind": "corpus_equivalence", "mandatory": True},
            {"id": "search", "kind": "counterexample_search", "mandatory": True},
            {"id": "perf", "kind": "performance_envelope", "mandatory": False},
        ],
        exclusions=[{"id": "threads", "path": "/cli/stderr", "reason": "thread scheduling is not compared"}],
        human_review=[{"id": "arch", "reason": "module boundaries need a person"}],
    )
)
DIGEST = MANIFEST.digest()


def ev(name: str, from_state: str, to_state: str, payload: dict | None = None) -> dict:
    return {"event": name, "from_state": from_state, "to_state": to_state, "payload": payload or {}, "recorded_at": 1.0}


def result(claim_id: str, status: str, kind: str = "corpus_equivalence", mandatory: bool = True, **over) -> c.ClaimResult:
    fields = dict(
        claim_id=claim_id, kind=kind, status=status, mandatory=mandatory, manifest_digest=DIGEST, baseline_digest="b" * 64,
        coverage={"kind": "corpus", "members": 2, "compared": 2, "timing": {"source_wall_s": 0.4, "target_wall_s": 0.5, "runs": 2}},
        divergences=(), counterexample=None, unverified=(), evidence_digests=("e" * 64,), detail="",
    )
    fields.update(over)
    return c.ClaimResult(**fields)


def repair_snapshot(results: list[c.ClaimResult], *, reject: bool = False) -> s.Snapshot:
    events = [
        ev("created", "", "CREATED", {"kind": "repair", "manifest_digest": DIGEST, "repository": "/repo", "base_commit": "c0", "workspace": "/ws"}),
        ev("baseline_capture", "CREATED", "BASELINE_CAPTURING", {"capture": {"inputs": ["c1"], "runs": 2, "raw_digests": {"c1": ["1" * 64, "2" * 64]}, "volatile_paths": ["/out/value/id"], "uncovered_volatile": []}}),
        ev("baseline_frozen", "BASELINE_CAPTURING", "BASELINE_FROZEN", {"frozen": {"baseline_digest": "b" * 64, "manifest_digest": DIGEST, "inputs": ["c1"], "record_digests": {"c1": "1" * 64}, "policy_set_digest": "p" * 64}}),
        ev("analysis_recorded", "BASELINE_FROZEN", "ANALYZING", {"metrics": {"files": 3, "lines": 300, "duplicate_blocks": {"count": 4}, "python": {"cycles": [["a", "b"]]}}, "findings": [{"id": "dup-1", "kind": "duplicate_implementation", "paths": ["a.py", "b.py"], "summary": "parse is written twice", "declared_by": "host-agent"}]}),
        ev("plan_recorded", "ANALYZING", "PLANNED", {"units": [{"id": "u1", "objective": "중복 제거 / remove the duplicate parser", "reason": "dup-1", "risk": "low", "owned_paths": ["a.py", "b.py"]}]}),
        ev("unit_started", "PLANNED", "UNIT_PREPARING", {"unit_id": "u1", "worktree": "/ws/units/u1", "base_commit": "c0"}),
        ev("unit_in_progress", "UNIT_PREPARING", "UNIT_IN_PROGRESS", {"unit_id": "u1"}),
        ev("unit_verifying", "UNIT_IN_PROGRESS", "UNIT_VERIFYING", {"unit_id": "u1", "tree": "t1"}),
    ]
    verdict = c.final_verdict([r.as_dict() for r in MANIFEST.claims], results, manifest_digest=DIGEST)
    events.append(ev("unit_verified", "UNIT_VERIFYING", "UNIT_VERIFYING", {"unit_id": "u1", "tree": "t1", "verdict": verdict.as_dict(), "claim_results": [r.as_dict() for r in results], "metrics_delta": {"duplicate_blocks": {"before": 4, "after": 1, "delta": -3}, "cycles": {"before": 1, "after": 0, "delta": -1}}}))
    if reject:
        events.append(ev("unit_rejected", "UNIT_VERIFYING", "UNIT_REJECTED", {"unit_id": "u1", "reason": "diverged at /out/value/total", "patch_digest": "d" * 64}))
        events.append(ev("unit_rolled_back", "UNIT_REJECTED", "UNIT_ROLLED_BACK", {"unit_id": "u1", "removed_worktree": "/ws/units/u1"}))
    else:
        events.append(ev("unit_accepted", "UNIT_VERIFYING", "UNIT_ACCEPTED", {"unit_id": "u1", "commit": "c1", "tree": "t1"}))
    return s.reduce(events, session_id="rep")


class TheNonCoderSummary(unittest.TestCase):
    def build(self, results: list[c.ClaimResult], **kwargs) -> dict:
        snapshot = repair_snapshot(results, **kwargs)
        verdict = c.final_verdict([r.as_dict() for r in MANIFEST.claims], results, manifest_digest=DIGEST, human_review=[h.reason for h in MANIFEST.human_review])
        return report.build(snapshot, MANIFEST, verdict, provenance={"tool_versions": {"python": "3.12"}})

    def test_the_six_sections_come_first_in_the_declared_order(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        self.assertEqual(list(data), ["record_version", "summary", "technical"])
        keys = [section["key"] for section in data["summary"]["sections"]]
        self.assertEqual(keys, ["behavior", "performance", "cleaned", "reverted", "unverified", "human"])
        titles = [section["title_ko"] for section in data["summary"]["sections"]]
        self.assertEqual(titles, ["기능 유지", "성능 유지", "정리 완료 항목", "되돌린 변경", "확인하지 못한 영역", "다음에 사람이 볼 것"])

    def test_plain_language_before_any_internal_term(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        text = report.markdown(data)
        first_technical = min(index for index in (text.find("manifest"), text.find("digest")) if index >= 0)
        self.assertLess(text.find("기능 유지"), first_technical)
        self.assertLess(text.find("다음에 사람이 볼 것"), first_technical)
        for section in data["summary"]["sections"]:
            for line in section["lines_ko"] + section["lines_en"]:
                self.assertNotIn("digest", line.lower())
                self.assertNotIn("manifest", line.lower())

    def test_the_verdict_is_said_in_plain_words(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        self.assertEqual(data["summary"]["verdict"]["status"], HUMAN_REVIEW)
        self.assertIn("사람", data["summary"]["verdict"]["label_ko"])
        behavior = data["summary"]["sections"][0]
        self.assertEqual(behavior["status"], "yes")
        self.assertTrue(any("2" in line for line in behavior["lines_ko"]), behavior)

    def test_accepted_and_reverted_units_are_listed_where_a_person_expects_them(self) -> None:
        accepted = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        cleaned = accepted["summary"]["sections"][2]
        self.assertTrue(any("중복 제거" in line for line in cleaned["lines_ko"]))
        self.assertTrue(any("c1" in line for line in cleaned["lines_en"]))
        self.assertEqual(accepted["summary"]["sections"][3]["lines_ko"], ["없음"])

        reverted = self.build([result("corpus", c.DIVERGED, divergences=({"path": "/out/value/total", "input_id": "c1", "raw_source": 120, "raw_target": 130, "why": "values differ"},))], reject=True)
        section = reverted["summary"]["sections"][3]
        self.assertTrue(any("중복 제거" in line for line in section["lines_ko"]))
        self.assertTrue(any("/out/value/total" in line for line in section["lines_en"]))
        self.assertEqual(reverted["summary"]["sections"][2]["lines_ko"], ["없음"])
        self.assertEqual(reverted["summary"]["sections"][0]["status"], "no")

    def test_the_unverified_section_is_always_present_and_honest(self) -> None:
        clean = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        section = clean["summary"]["sections"][4]
        self.assertEqual(section["key"], "unverified")
        self.assertTrue(any("threads" in line or "thread" in line for line in section["lines_en"]), "an explicit exclusion is something not verified")
        partial = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.UNVERIFIABLE, kind="counterexample_search", unverified=("search-3: timeout after 60s",))])
        section = partial["summary"]["sections"][4]
        self.assertTrue(any("timeout" in line for line in section["lines_en"]))
        self.assertEqual(partial["summary"]["verdict"]["status"], UNVERIFIABLE)
        self.assertIn("확인하지 못한 영역", report.markdown(partial))

    def test_performance_is_not_verified_unless_a_measurement_protocol_was_declared_and_run(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        performance = data["summary"]["sections"][1]
        self.assertEqual(performance["status"], "not_verified")
        self.assertTrue(any("NOT VERIFIED" in line for line in performance["lines_en"]), performance)
        self.assertTrue(any("검증하지 않았습니다" in line for line in performance["lines_ko"]), performance)
        self.assertTrue(any("0.4" in line and "0.5" in line and "not a performance verification" in line for line in performance["lines_en"]), performance)
        self.assertFalse(any("preserved" in line.lower() for line in performance["lines_en"]))

    def test_performance_is_verified_only_from_a_performance_claim_under_its_protocol(self) -> None:
        measured = result(
            "perf", c.PRESERVED_WITHIN_ENVELOPE, kind="performance_envelope", mandatory=False,
            coverage={"kind": "performance", "protocol": {"runs": 3, "statistic": "median", "rel_tolerance": 0.5, "abs_tolerance_s": 0.25}, "source_wall_s": 0.9, "target_wall_s": 0.8},
        )
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50}), measured])
        performance = data["summary"]["sections"][1]
        self.assertEqual(performance["status"], "verified")
        self.assertTrue(any("3" in line and "median" in line and "0.9" in line and "0.8" in line for line in performance["lines_en"]), performance)
        slow = result(
            "perf", c.DIVERGED, kind="performance_envelope", mandatory=False, divergences=({"path": "/timing/wall_s", "raw_source": 0.9, "raw_target": 2.0, "why": "exceeds tolerance"},),
            coverage={"kind": "performance", "protocol": {"runs": 3, "statistic": "median", "rel_tolerance": 0.5, "abs_tolerance_s": 0.25}, "source_wall_s": 0.9, "target_wall_s": 2.0},
        )
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50}), slow])
        self.assertEqual(data["summary"]["sections"][1]["status"], "diverged")

    def test_human_items_name_what_to_look_at(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50})])
        human = data["summary"]["sections"][5]
        self.assertTrue(any("module boundaries" in line for line in human["lines_en"]))


class TheTechnicalReport(unittest.TestCase):
    def build(self, results: list[c.ClaimResult]) -> dict:
        snapshot = repair_snapshot(results)
        verdict = c.final_verdict([r.as_dict() for r in MANIFEST.claims], results, manifest_digest=DIGEST, human_review=[h.reason for h in MANIFEST.human_review])
        return report.build(snapshot, MANIFEST, verdict, provenance={"tool_versions": {"python": "3.12.10", "invara": "0.1.3+branch"}, "platform": "test"})

    def test_it_carries_every_digest_a_verifier_needs(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 50, "seed": 7})])
        technical = data["technical"]
        self.assertEqual(technical["manifest"]["digest"], DIGEST)
        self.assertEqual(technical["manifest"]["history"], [DIGEST])
        self.assertEqual(technical["baseline"]["digest"], "b" * 64)
        self.assertEqual(technical["baseline"]["record_digests"], {"c1": "1" * 64})
        self.assertEqual([p["id"] for p in technical["policies"]], ["ids"])
        self.assertEqual(technical["coverage"]["tested_over_finite_corpus"], ["corpus"])
        self.assertEqual(technical["coverage"]["searched_without_divergence"], ["search"])
        self.assertEqual(technical["coverage"]["explicitly_excluded"], ["threads"])
        self.assertEqual(technical["final_verdict"]["status"], HUMAN_REVIEW)
        self.assertEqual(technical["provenance"]["base_commit"], "c0")
        self.assertEqual(technical["provenance"]["accepted_commit"], "c1")
        self.assertEqual(technical["provenance"]["tool_versions"]["python"], "3.12.10")
        self.assertEqual(technical["units"]["accepted"][0]["commit"], "c1")
        self.assertEqual(technical["units"]["accepted"][0]["metrics_delta"]["duplicate_blocks"]["after"], 1)
        self.assertTrue(technical["limitations"])
        self.assertEqual(technical["findings"][0]["id"], "dup-1")
        self.assertEqual(technical["stability"]["volatile_paths"], ["/out/value/id"])

    def test_divergences_and_counterexamples_are_listed_in_full(self) -> None:
        divergence = {"path": "/out/value/total", "input_id": "c1", "raw_source": 120, "raw_target": 130, "normalized_source": 120, "normalized_target": 130, "policy_id": "exact", "policy_kind": "exact", "why": "values differ", "mandatory": True}
        data = self.build([
            result("corpus", c.DIVERGED, divergences=(divergence,)),
            result("search", c.DIVERGED, kind="counterexample_search", divergences=(divergence,), counterexample={"original": {"input": {"qty": 10, "x": 1}}, "minimized": {"input": {"qty": 10}}}, coverage={"kind": "search", "runs": 12}),
        ])
        technical = data["technical"]
        self.assertEqual(technical["divergences"][0]["path"], "/out/value/total")
        self.assertEqual(technical["counterexamples"][0]["minimized"]["input"], {"qty": 10})
        self.assertEqual(technical["final_verdict"]["status"], BLOCK)
        self.assertEqual(data["summary"]["verdict"]["status"], BLOCK)

    def test_the_report_is_json_serialisable_redacted_and_content_addressed(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE, detail="token=abcdef1234 leaked into a detail"), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 5})])
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("abcdef1234", text)
        self.assertEqual(len(report.digest(data)), 64)
        self.assertEqual(report.digest(data), report.digest(json.loads(text)))
        self.assertEqual(data["record_version"], report.REPORT_VERSION)

    def test_markdown_renders_both_layers(self) -> None:
        data = self.build([result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 5})])
        text = report.markdown(data)
        self.assertIn("# ", text)
        self.assertIn("기능 유지", text)
        self.assertIn(DIGEST, text)
        self.assertIn("PRESERVED_WITHIN_ENVELOPE", text)
        self.assertIn("Limitations", text)

    def test_an_assure_session_reports_without_units(self) -> None:
        events = [
            ev("created", "", "CREATED", {"kind": "assure", "manifest_digest": DIGEST, "roots": {"SOURCE_ROOT": "/a", "TARGET_ROOT": "/b"}}),
            ev("baseline_capture", "CREATED", "BASELINE_CAPTURING", {"capture": {"inputs": ["c1"], "runs": 1, "raw_digests": {}}}),
            ev("baseline_frozen", "BASELINE_CAPTURING", "BASELINE_FROZEN", {"frozen": {"baseline_digest": "b" * 64, "manifest_digest": DIGEST, "inputs": ["c1"], "record_digests": {}}}),
            ev("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": result("corpus", c.PRESERVED_WITHIN_ENVELOPE).as_dict()}),
        ]
        snapshot = s.reduce(events, session_id="rep")
        verdict = c.final_verdict([r.as_dict() for r in MANIFEST.claims], [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)], manifest_digest=DIGEST)
        data = report.build(snapshot, MANIFEST, verdict, provenance={})
        self.assertEqual(data["summary"]["sections"][2]["lines_ko"], ["해당 없음"])
        self.assertEqual(data["technical"]["provenance"]["roots"], {"SOURCE_ROOT": "/a", "TARGET_ROOT": "/b"})
        self.assertEqual(data["summary"]["verdict"]["status"], UNVERIFIABLE)


if __name__ == "__main__":
    unittest.main()
