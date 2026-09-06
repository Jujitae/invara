"""The repair session as an event-sourced state machine. Invalid moves fail closed."""

from __future__ import annotations

import unittest

from invara.assurance import session as s
from invara.contract import BLOCK, HUMAN_REVIEW, PASS

MANIFEST_A = "a" * 64
MANIFEST_B = "b" * 64


def event(name: str, from_state: str, to_state: str, payload: dict | None = None, at: float = 0.0) -> dict:
    return {"event": name, "from_state": from_state, "to_state": to_state, "payload": payload or {}, "recorded_at": at}


def created(kind: str = "repair") -> dict:
    return event("created", "", "CREATED", {"kind": kind, "manifest_digest": MANIFEST_A, "repository": "/repo", "base_commit": "c0", "workspace": "/ws"})


def claim(claim_id: str, status: str, manifest_digest: str = MANIFEST_A, **over) -> dict:
    result = {
        "claim_id": claim_id,
        "kind": "corpus_equivalence",
        "status": status,
        "mandatory": True,
        "manifest_digest": manifest_digest,
        "baseline_digest": "f" * 64,
        "coverage": {"kind": "corpus", "members": 1},
        "divergences": [{"path": "/out/value/total", "input_id": "c1"}] if status == "DIVERGED" else [],
        "counterexample": None,
        "unverified": [],
        "evidence_digests": [],
        "detail": "",
    }
    result.update(over)
    return result


class TheStateTable(unittest.TestCase):
    def test_the_thirteen_states_exist(self) -> None:
        self.assertEqual(
            s.STATES,
            (
                "CREATED", "BASELINE_CAPTURING", "BASELINE_FROZEN", "ANALYZING", "PLANNED",
                "UNIT_PREPARING", "UNIT_IN_PROGRESS", "UNIT_VERIFYING", "UNIT_ACCEPTED", "UNIT_REJECTED",
                "UNIT_ROLLED_BACK", "COMPLETED", "BLOCKED",
            ),
        )

    def test_terminal_states_allow_nothing(self) -> None:
        self.assertEqual(s.TRANSITIONS["COMPLETED"], ())
        self.assertEqual(s.TRANSITIONS["BLOCKED"], ())

    def test_every_state_can_block_except_the_terminal_ones(self) -> None:
        for state in s.STATES:
            if state not in ("COMPLETED", "BLOCKED"):
                self.assertIn("BLOCKED", s.TRANSITIONS[state], state)

    def test_a_unit_cannot_be_accepted_without_verification(self) -> None:
        self.assertNotIn("UNIT_ACCEPTED", s.TRANSITIONS["UNIT_IN_PROGRESS"])
        self.assertNotIn("UNIT_ACCEPTED", s.TRANSITIONS["UNIT_PREPARING"])
        self.assertIn("UNIT_ACCEPTED", s.TRANSITIONS["UNIT_VERIFYING"])

    def test_a_rejected_unit_must_be_rolled_back_before_anything_else(self) -> None:
        self.assertEqual(s.TRANSITIONS["UNIT_REJECTED"], ("UNIT_ROLLED_BACK", "BLOCKED"))

    def test_planning_requires_analysis_and_a_frozen_baseline(self) -> None:
        self.assertNotIn("PLANNED", s.TRANSITIONS["BASELINE_FROZEN"])
        self.assertNotIn("PLANNED", s.TRANSITIONS["BASELINE_CAPTURING"])
        self.assertIn("PLANNED", s.TRANSITIONS["ANALYZING"])


class Transitions(unittest.TestCase):
    def test_a_valid_move_is_allowed(self) -> None:
        s.check_transition("CREATED", "BASELINE_CAPTURING")

    def test_an_invalid_move_raises_and_names_both_states(self) -> None:
        with self.assertRaises(s.InvalidTransition) as caught:
            s.check_transition("CREATED", "UNIT_ACCEPTED")
        self.assertIn("CREATED", str(caught.exception))
        self.assertIn("UNIT_ACCEPTED", str(caught.exception))

    def test_an_unknown_state_is_refused(self) -> None:
        with self.assertRaises(s.InvalidTransition):
            s.check_transition("CREATED", "DONE")

    def test_events_name_their_destination(self) -> None:
        self.assertEqual(s.EVENT_STATES["baseline_frozen"], "BASELINE_FROZEN")
        self.assertEqual(s.EVENT_STATES["unit_accepted"], "UNIT_ACCEPTED")
        self.assertIsNone(s.EVENT_STATES["claim_recorded"], "recording a claim does not move the session")


class Reducing(unittest.TestCase):
    def full_repair(self) -> list[dict]:
        return [
            created(),
            event("baseline_capture", "CREATED", "BASELINE_CAPTURING", {"capture": {"inputs": ["c1"], "runs": 2, "raw_digests": {}}}),
            event("baseline_frozen", "BASELINE_CAPTURING", "BASELINE_FROZEN", {"frozen": {"baseline_digest": "f" * 64, "manifest_digest": MANIFEST_A, "inputs": ["c1"]}}),
            event("analysis_recorded", "BASELINE_FROZEN", "ANALYZING", {"metrics": {"files": 3}, "findings": [{"id": "dup-1", "kind": "duplicate_implementation", "declared_by": "host-agent"}]}),
            event("plan_recorded", "ANALYZING", "PLANNED", {"units": [{"id": "u1", "objective": "dedupe", "owned_paths": ["app/*.py"]}, {"id": "u2", "objective": "split"}]}),
            event("unit_started", "PLANNED", "UNIT_PREPARING", {"unit_id": "u1", "worktree": "/ws/units/u1", "base_commit": "c0"}),
            event("unit_in_progress", "UNIT_PREPARING", "UNIT_IN_PROGRESS", {"unit_id": "u1"}),
            event("unit_verifying", "UNIT_IN_PROGRESS", "UNIT_VERIFYING", {"unit_id": "u1", "tree": "t1"}),
            event("unit_verified", "UNIT_VERIFYING", "UNIT_VERIFYING", {"unit_id": "u1", "tree": "t1", "verdict": {"status": PASS, "decided_by": "preserved"}, "claim_results": [claim("corpus", "PRESERVED_WITHIN_ENVELOPE")]}),
            event("unit_accepted", "UNIT_VERIFYING", "UNIT_ACCEPTED", {"unit_id": "u1", "commit": "c1", "tree": "t1"}),
        ]

    def test_a_fresh_session_is_created(self) -> None:
        snapshot = s.reduce([created()])
        self.assertEqual(snapshot.state, "CREATED")
        self.assertEqual(snapshot.kind, "repair")
        self.assertEqual(snapshot.manifest_digest, MANIFEST_A)
        self.assertEqual(snapshot.base_commit, "c0")
        self.assertEqual(snapshot.accepted_commit, "c0")

    def test_the_whole_repair_path_reduces_to_an_accepted_unit(self) -> None:
        snapshot = s.reduce(self.full_repair())
        self.assertEqual(snapshot.state, "UNIT_ACCEPTED")
        self.assertEqual(snapshot.accepted_commit, "c1")
        self.assertEqual(snapshot.accepted_units, ["u1"])
        self.assertEqual(snapshot.baseline_digest, "f" * 64)
        self.assertEqual([u["id"] for u in snapshot.plan], ["u1", "u2"])
        self.assertEqual(snapshot.units["u1"]["status"], "accepted")
        self.assertEqual(snapshot.units["u1"]["verdict"]["status"], PASS)
        self.assertEqual(snapshot.units["u1"]["commit"], "c1")
        self.assertIsNone(snapshot.current_unit)
        self.assertEqual(snapshot.findings[0]["id"], "dup-1")
        self.assertEqual(snapshot.metrics_before, {"files": 3})

    def test_the_next_unit_starts_from_the_accepted_state(self) -> None:
        events = self.full_repair() + [
            event("continued", "UNIT_ACCEPTED", "PLANNED", {}),
            event("unit_started", "PLANNED", "UNIT_PREPARING", {"unit_id": "u2", "worktree": "/ws/units/u2", "base_commit": "c1"}),
        ]
        snapshot = s.reduce(events)
        self.assertEqual(snapshot.current_unit, "u2")
        self.assertEqual(snapshot.units["u2"]["base_commit"], "c1")

    def test_a_rejected_unit_leaves_the_accepted_state_untouched(self) -> None:
        events = self.full_repair()[:-2] + [
            event("unit_verified", "UNIT_VERIFYING", "UNIT_VERIFYING", {"unit_id": "u1", "tree": "t1", "verdict": {"status": BLOCK, "decided_by": "diverged"}, "claim_results": [claim("corpus", "DIVERGED")]}),
            event("unit_rejected", "UNIT_VERIFYING", "UNIT_REJECTED", {"unit_id": "u1", "reason": "diverged", "patch_digest": "p" * 64}),
            event("unit_rolled_back", "UNIT_REJECTED", "UNIT_ROLLED_BACK", {"unit_id": "u1", "removed_worktree": "/ws/units/u1"}),
        ]
        snapshot = s.reduce(events)
        self.assertEqual(snapshot.state, "UNIT_ROLLED_BACK")
        self.assertEqual(snapshot.accepted_commit, "c0")
        self.assertEqual(snapshot.rejected_units, ["u1"])
        self.assertEqual(snapshot.units["u1"]["status"], "rolled_back")
        self.assertEqual(snapshot.rollbacks[0]["unit_id"], "u1")
        self.assertIn("/out/value/total", snapshot.divergence_paths)

    def test_a_history_with_an_invalid_move_fails_closed(self) -> None:
        events = [created(), event("unit_accepted", "CREATED", "UNIT_ACCEPTED", {"unit_id": "u1", "commit": "c1", "tree": "t"})]
        with self.assertRaises(s.SessionCorrupt):
            s.reduce(events)

    def test_a_history_whose_from_state_lies_fails_closed(self) -> None:
        events = [created(), event("baseline_frozen", "BASELINE_CAPTURING", "BASELINE_FROZEN", {"frozen": {}})]
        with self.assertRaises(s.SessionCorrupt):
            s.reduce(events)

    def test_an_event_after_a_terminal_state_fails_closed(self) -> None:
        events = [created(), event("blocked", "CREATED", "BLOCKED", {"reason": "dirty tree"}), event("baseline_capture", "BLOCKED", "BASELINE_CAPTURING", {})]
        with self.assertRaises(s.SessionCorrupt):
            s.reduce(events)
        self.assertEqual(s.reduce(events[:2]).blocked_reason, "dirty tree")

    def test_an_unknown_event_fails_closed(self) -> None:
        with self.assertRaises(s.SessionCorrupt):
            s.reduce([created(), event("teleport", "CREATED", "COMPLETED", {})])

    def test_an_empty_history_is_no_session(self) -> None:
        with self.assertRaises(s.SessionCorrupt):
            s.reduce([])


class ClaimsAndAmendments(unittest.TestCase):
    def assure(self) -> list[dict]:
        return [
            created("assure"),
            event("baseline_capture", "CREATED", "BASELINE_CAPTURING", {"capture": {"inputs": ["c1"], "runs": 1, "raw_digests": {}}}),
            event("baseline_frozen", "BASELINE_CAPTURING", "BASELINE_FROZEN", {"frozen": {"baseline_digest": "f" * 64, "manifest_digest": MANIFEST_A, "inputs": ["c1"]}}),
        ]

    def test_claim_results_are_kept_latest_per_claim_under_the_current_manifest(self) -> None:
        events = self.assure() + [
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "DIVERGED")}),
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "PRESERVED_WITHIN_ENVELOPE")}),
        ]
        snapshot = s.reduce(events)
        self.assertEqual(len(snapshot.claim_results), 2)
        active = snapshot.active_claim_results()
        self.assertEqual([r["status"] for r in active], ["PRESERVED_WITHIN_ENVELOPE"])
        self.assertEqual(snapshot.divergence_paths, ["/out/value/total"])

    def test_an_amendment_invalidates_results_under_the_old_digest(self) -> None:
        events = self.assure() + [
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "DIVERGED")}),
            event(
                "manifest_amended", "BASELINE_FROZEN", "BASELINE_FROZEN",
                {"record": {"old_digest": MANIFEST_A, "new_digest": MANIFEST_B, "requested_by": "repairer", "reason": "hide", "added_policies": ["hide"]}, "invalidated": ["corpus"], "post_divergence": ["hide covers /out/value/total"]},
            ),
        ]
        snapshot = s.reduce(events)
        self.assertEqual(snapshot.manifest_digest, MANIFEST_B)
        self.assertEqual(snapshot.manifest_history, [MANIFEST_A, MANIFEST_B])
        self.assertEqual(snapshot.active_claim_results(), [])
        self.assertEqual(snapshot.invalidated_claims, [{"claim_id": "corpus", "manifest_digest": MANIFEST_A, "before": 1}])
        self.assertEqual(snapshot.post_divergence, ["hide covers /out/value/total"])
        self.assertEqual(len(snapshot.amendments), 1)

    def test_a_post_divergence_flag_survives_later_results(self) -> None:
        events = self.assure() + [
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "DIVERGED")}),
            event("manifest_amended", "BASELINE_FROZEN", "BASELINE_FROZEN", {"record": {"old_digest": MANIFEST_A, "new_digest": MANIFEST_B}, "invalidated": ["corpus"], "post_divergence": ["hide covers /out/value/total"]}),
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "PRESERVED_WITHIN_ENVELOPE", manifest_digest=MANIFEST_B)}),
        ]
        snapshot = s.reduce(events)
        self.assertEqual([r["status"] for r in snapshot.active_claim_results()], ["PRESERVED_WITHIN_ENVELOPE"])
        self.assertTrue(snapshot.post_divergence)

    def test_completion_records_the_final_verdict(self) -> None:
        events = self.assure() + [
            event("claim_recorded", "BASELINE_FROZEN", "BASELINE_FROZEN", {"result": claim("corpus", "PRESERVED_WITHIN_ENVELOPE")}),
            event("completed", "BASELINE_FROZEN", "COMPLETED", {"final_verdict": {"status": HUMAN_REVIEW, "decided_by": "needs_human"}, "report_digest": "r" * 64}),
        ]
        snapshot = s.reduce(events)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.final_verdict["status"], HUMAN_REVIEW)
        self.assertEqual(snapshot.report_digest, "r" * 64)

    def test_the_snapshot_serialises(self) -> None:
        snapshot = s.reduce(self.assure())
        data = snapshot.as_dict()
        self.assertEqual(data["state"], "BASELINE_FROZEN")
        self.assertEqual(data["kind"], "assure")
        self.assertEqual(data["events"], 3)


if __name__ == "__main__":
    unittest.main()
