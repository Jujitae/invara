"""The Equivalence Manifest: validated, content-addressed, amended explicitly."""

from __future__ import annotations

import hashlib
import unittest

from _support import SCHEMA, dumps, manifest_dict, policy
from invara.assurance import manifest as m


def load(**over):
    return m.Manifest.from_dict(manifest_dict(**over))


def refused(testcase, reason: str, **over):
    with testcase.assertRaises(m.ManifestError) as caught:
        load(**over)
    testcase.assertEqual(caught.exception.reason, reason, caught.exception)
    return caught.exception


class Loading(unittest.TestCase):
    def test_a_minimal_manifest_loads_and_fills_defaults(self) -> None:
        loaded = load()
        self.assertEqual(loaded.session_id, "unit-test")
        self.assertEqual(loaded.source_system.id, "before")
        self.assertEqual(loaded.target_system.root, "$TARGET_ROOT")
        self.assertEqual(loaded.input_domain.kind, "corpus")
        self.assertEqual([item.id for item in loaded.input_domain.corpus], ["c1"])
        self.assertEqual(loaded.budgets.search_runs, m.DEFAULT_BUDGETS["search_runs"])
        self.assertEqual(loaded.timeouts.run_seconds, m.DEFAULT_TIMEOUTS["run_seconds"])
        self.assertEqual(loaded.exclusions, ())
        self.assertEqual(loaded.human_review, ())

    def test_a_target_may_be_declared_the_same_as_the_source(self) -> None:
        loaded = load(target_system={"same_as_source": True})
        self.assertEqual(loaded.target_system.command, loaded.source_system.command)
        self.assertEqual(loaded.target_system.root, "$TARGET_ROOT")
        self.assertEqual(loaded.target_system.id, "after")

    def test_as_dict_round_trips_through_from_dict(self) -> None:
        loaded = load(policies=[policy("generated_id", "/out/value/id", pattern="uuid")])
        again = m.Manifest.from_dict(loaded.as_dict())
        self.assertEqual(again, loaded)
        self.assertEqual(again.digest(), loaded.digest())


class ContentAddressing(unittest.TestCase):
    def test_the_digest_is_the_sha256_of_the_canonical_json(self) -> None:
        loaded = load()
        expected = hashlib.sha256(dumps(loaded.as_dict()).encode("utf-8")).hexdigest()
        self.assertEqual(loaded.digest(), expected)

    def test_key_order_in_the_input_does_not_change_the_digest(self) -> None:
        data = manifest_dict()
        shuffled = dict(reversed(list(data.items())))
        self.assertEqual(m.Manifest.from_dict(data).digest(), m.Manifest.from_dict(shuffled).digest())

    def test_any_semantic_change_changes_the_digest(self) -> None:
        one = load().digest()
        two = load(policies=[policy("ignore", "/cli/stderr")]).digest()
        self.assertNotEqual(one, two)


class StructuralRefusals(unittest.TestCase):
    def test_unsupported_schema(self) -> None:
        refused(self, "unsupported_schema", schema_version="invara.assurance.manifest/9")

    def test_unknown_top_level_field_fails_closed(self) -> None:
        refused(self, "unknown_field", polices=[])

    def test_session_id_is_required_and_a_slug(self) -> None:
        refused(self, "no_session_id", session_id="")
        refused(self, "bad_session_id", session_id="has space")

    def test_both_systems_are_required(self) -> None:
        data = manifest_dict()
        del data["target_system"]
        with self.assertRaises(m.ManifestError) as caught:
            m.Manifest.from_dict(data)
        self.assertEqual(caught.exception.reason, "no_target_system")

    def test_a_system_command_is_an_argv_list_never_a_shell_string(self) -> None:
        refused(self, "bad_command", source_system={"id": "s", "kind": "process", "command": "python app.py", "root": "."})
        refused(self, "bad_command", source_system={"id": "s", "kind": "process", "command": [], "root": "."})

    def test_a_service_system_must_say_how_it_becomes_ready(self) -> None:
        refused(
            self,
            "service_without_ready",
            source_system={"id": "s", "kind": "service", "command": ["python", "srv.py"], "root": "."},
        )

    def test_the_corpus_may_not_be_empty(self) -> None:
        refused(self, "empty_corpus", input_domain={"kind": "corpus", "delivery": "stdin_json", "corpus": []})

    def test_corpus_ids_are_unique(self) -> None:
        refused(
            self,
            "duplicate_input",
            input_domain={
                "kind": "corpus",
                "delivery": "stdin_json",
                "corpus": [{"id": "c1", "input": 1}, {"id": "c1", "input": 2}],
            },
        )

    def test_probes_are_required_and_unique_and_one_must_be_mandatory(self) -> None:
        refused(self, "no_probes", probes=[])
        refused(
            self,
            "duplicate_probe",
            probes=[{"id": "cli", "adapter": "process", "mandatory": True}] * 2,
        )
        refused(self, "no_mandatory_probe", probes=[{"id": "cli", "adapter": "process", "mandatory": False}])

    def test_an_unknown_adapter_is_refused(self) -> None:
        refused(self, "unknown_adapter", probes=[{"id": "x", "adapter": "telepathy", "mandatory": True}])

    def test_adapter_specific_requirements(self) -> None:
        refused(self, "probe_missing_field", probes=[{"id": "fs", "adapter": "filesystem", "mandatory": True}])
        refused(self, "probe_missing_field", probes=[{"id": "db", "adapter": "sqlite", "mandatory": True, "path": "$WORKSPACE/a.db"}])
        refused(
            self,
            "http_requires_service",
            probes=[{"id": "api", "adapter": "http", "mandatory": True, "requests": "$INPUT"}],
        )

    def test_at_least_one_mandatory_claim(self) -> None:
        refused(self, "no_mandatory_claim", claims=[{"id": "c", "kind": "corpus_equivalence", "mandatory": False}])
        refused(self, "no_mandatory_claim", claims=[])

    def test_a_finite_proof_needs_a_finite_domain(self) -> None:
        refused(self, "proof_without_finite_domain", claims=[{"id": "p", "kind": "finite_domain_proof", "mandatory": True}])

    def test_an_unknown_claim_kind(self) -> None:
        refused(self, "unknown_claim_kind", claims=[{"id": "p", "kind": "vibes", "mandatory": True}])

    def test_a_finite_domain_is_declared_by_parameters(self) -> None:
        loaded = load(
            input_domain={
                "kind": "finite",
                "delivery": "stdin_json",
                "finite": {"parameters": {"zone": [1, 2], "kg": {"range": [0, 3]}}},
            },
            claims=[{"id": "p", "kind": "finite_domain_proof", "mandatory": True}],
        )
        self.assertEqual(loaded.input_domain.finite.cardinality, 8)
        refused(
            self,
            "bad_finite_domain",
            input_domain={"kind": "finite", "delivery": "stdin_json", "finite": {"parameters": {}}},
            claims=[{"id": "p", "kind": "finite_domain_proof", "mandatory": True}],
        )


class PolicyRefusals(unittest.TestCase):
    def test_unknown_policy_kind(self) -> None:
        refused(self, "unknown_policy_kind", policies=[policy("fuzzy", "/out/value")])

    def test_policy_ids_are_unique(self) -> None:
        refused(
            self,
            "duplicate_policy",
            policies=[policy("ignore", "/cli/stderr", id="p"), policy("ignore", "/cli/stdout", id="p")],
        )

    def test_a_bad_selector(self) -> None:
        refused(self, "bad_selector", policies=[policy("ignore", "cli/stderr")])

    def test_a_non_exact_policy_needs_a_reason(self) -> None:
        refused(self, "policy_without_reason", policies=[policy("ignore", "/cli/stderr", reason="")])

    def test_root_ignore_is_refused(self) -> None:
        refused(self, "root_ignore", policies=[policy("ignore", "/")])

    def test_blanket_ignore_is_refused(self) -> None:
        for selector in ("/*", "/**", "/*/**"):
            refused(self, "blanket_ignore", policies=[policy("ignore", selector)])

    def test_ignoring_a_whole_mandatory_probe_is_refused(self) -> None:
        refused(self, "probe_ignore", policies=[policy("ignore", "/out")])

    def test_tolerances_must_be_bounded(self) -> None:
        refused(self, "unbounded_tolerance", policies=[policy("numeric_abs_tolerance", "/out/value/x", abs=0)])
        refused(self, "unbounded_tolerance", policies=[policy("numeric_abs_tolerance", "/out/value/x", abs=-1)])
        refused(self, "unbounded_tolerance", policies=[policy("numeric_rel_tolerance", "/out/value/x", rel=0)])
        refused(self, "overbroad_tolerance", policies=[policy("numeric_rel_tolerance", "/out/value/x", rel=1.0)])
        refused(self, "overbroad_tolerance", policies=[policy("numeric_rel_tolerance", "/out/value/x", rel=3)])
        refused(self, "unbounded_tolerance", policies=[policy("timestamp", "/out/value/t", tolerance_s=-1)])

    def test_a_bounded_tolerance_is_accepted(self) -> None:
        loaded = load(policies=[policy("numeric_rel_tolerance", "/out/value/x", rel=0.01)])
        self.assertEqual(loaded.policies[0].params["rel"], 0.01)

    def test_an_inferred_policy_cannot_arrive_accepted(self) -> None:
        bad = policy("generated_id", "/out/value/id", pattern="uuid")
        bad.update(origin="inferred", accepted=True)
        refused(self, "inferred_policy_accepted", policies=[bad])

    def test_origin_defaults_to_declared_and_accepted(self) -> None:
        loaded = load(policies=[policy("generated_id", "/out/value/id", pattern="uuid")])
        self.assertEqual(loaded.policies[0].origin, "declared")
        self.assertTrue(loaded.policies[0].accepted)

    def test_generated_id_needs_a_known_pattern(self) -> None:
        refused(self, "bad_policy_params", policies=[policy("generated_id", "/out/value/id", pattern="mystery")])

    def test_unknown_policy_field_fails_closed(self) -> None:
        bad = policy("ignore", "/cli/stderr")
        bad["reasn"] = "typo"
        refused(self, "unknown_field", policies=[bad])


class Amendments(unittest.TestCase):
    def test_an_amendment_produces_a_new_manifest_and_a_record(self) -> None:
        before = load()
        result = m.amend(
            before,
            {
                "requested_by": "host-agent",
                "reason": "ids are generated",
                "changes": {"policies": [policy("generated_id", "/out/value/id", pattern="uuid", id="ids")]},
            },
        )
        self.assertNotEqual(result.manifest.digest(), before.digest())
        self.assertEqual(result.record["old_digest"], before.digest())
        self.assertEqual(result.record["new_digest"], result.manifest.digest())
        self.assertEqual(result.record["requested_by"], "host-agent")
        self.assertEqual(result.record["reason"], "ids are generated")
        self.assertEqual(result.record["changed_sections"], ["policies"])
        self.assertEqual(result.record["added_policies"], ["ids"])

    def test_an_amendment_must_say_who_and_why(self) -> None:
        for bad in ({"reason": "r", "changes": {"policies": []}}, {"requested_by": "x", "changes": {"policies": []}}):
            with self.assertRaises(m.ManifestError) as caught:
                m.amend(load(), bad)
            self.assertEqual(caught.exception.reason, "amendment_incomplete")

    def test_an_amendment_may_only_change_amendable_sections(self) -> None:
        with self.assertRaises(m.ManifestError) as caught:
            m.amend(load(), {"requested_by": "x", "reason": "r", "changes": {"session_id": "other"}})
        self.assertEqual(caught.exception.reason, "section_not_amendable")

    def test_an_amended_manifest_is_validated_like_any_other(self) -> None:
        with self.assertRaises(m.ManifestError) as caught:
            m.amend(load(), {"requested_by": "x", "reason": "r", "changes": {"policies": [policy("ignore", "/")]}})
        self.assertEqual(caught.exception.reason, "root_ignore")

    def test_accepting_an_inferred_policy_marks_it_an_amendment(self) -> None:
        proposed = policy("timestamp", "/out/value/at", id="ts")
        proposed.update(origin="inferred", accepted=False)
        before = load(policies=[proposed])
        accepted = dict(proposed, origin="amendment", accepted=True)
        result = m.amend(before, {"requested_by": "founder", "reason": "clock", "changes": {"policies": [accepted]}})
        self.assertEqual(result.manifest.policies[0].origin, "amendment")
        self.assertTrue(result.manifest.policies[0].accepted)
        self.assertEqual(result.record["changed_policies"], ["ts"])

    def test_a_post_divergence_amendment_is_named_as_such(self) -> None:
        before = load()
        result = m.amend(
            before,
            {
                "requested_by": "repairer",
                "reason": "hide it",
                "changes": {"policies": [policy("ignore", "/out/value/total", id="hide")]},
            },
        )
        covered = m.policies_covering(result.manifest, ["hide"], ["/out/value/total"])
        self.assertEqual(covered, ["hide"])
        self.assertEqual(m.policies_covering(result.manifest, ["hide"], ["/out/value/other"]), [])


class Digestible(unittest.TestCase):
    def test_schema_constant_is_versioned(self) -> None:
        self.assertEqual(m.SCHEMA_VERSION, SCHEMA)


if __name__ == "__main__":
    unittest.main()
