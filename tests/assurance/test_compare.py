"""Differential comparison: never a boolean, always a located divergence."""

from __future__ import annotations

import unittest

from _support import manifest_dict, observation, policy
from invara.assurance import compare as cmp
from invara.assurance import manifest as m

UUID_A = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
UUID_B = "9b2c1d20-6e1f-4b6a-8f2a-1d2e3f4a5b6c"


def manifest(*policies: dict, **over) -> m.Manifest:
    return m.Manifest.from_dict(manifest_dict(policies=list(policies), **over))


class Equivalence(unittest.TestCase):
    def test_identical_observations_are_equivalent(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"total": 3}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"total": 3}}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        self.assertTrue(result.equivalent)
        self.assertEqual(result.status, "compared")
        self.assertEqual(result.divergences, ())
        self.assertEqual(result.compared_leaves, 2)

    def test_the_result_carries_both_digests_and_the_policy_set(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": 1}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": 1}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        self.assertEqual(len(result.source_raw_digest), 64)
        self.assertEqual(len(result.target_raw_digest), 64)
        self.assertEqual(result.source_normalized_digest, result.target_normalized_digest)
        self.assertEqual(len(result.policy_set_digest), 64)


class Divergences(unittest.TestCase):
    def test_a_changed_value_is_located_with_raw_and_normalized_values(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"total": 3}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"total": 4}}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        self.assertFalse(result.equivalent)
        (d,) = result.divergences
        self.assertEqual(d.path, "/out/value/total")
        self.assertEqual((d.raw_source, d.raw_target), (3, 4))
        self.assertEqual((d.normalized_source, d.normalized_target), (3, 4))
        self.assertEqual(d.policy_kind, "exact")
        self.assertTrue(d.mandatory)
        self.assertIn("differ", d.why)

    def test_a_missing_and_an_extra_key_are_different_accusations(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"a": 1, "b": 2}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"a": 1, "c": 3}}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        whys = {d.path: d.why for d in result.divergences}
        self.assertIn("missing in target", whys["/out/value/b"])
        self.assertIn("extra in target", whys["/out/value/c"])

    def test_a_type_change_is_a_divergence_even_when_it_prints_the_same(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"n": 1}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"n": "1"}}}, system_id="after")
        (d,) = cmp.compare(source, target, manifest()).divergences
        self.assertIn("type", d.why)

    def test_list_order_is_meaningful_unless_declared_otherwise(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": [1, 2]}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": [2, 1]}}, system_id="after")
        self.assertFalse(cmp.compare(source, target, manifest()).equivalent)
        self.assertTrue(cmp.compare(source, target, manifest(policy("unordered_multiset", "/out/value"))).equivalent)

    def test_a_divergence_under_a_sorted_list_reports_the_raw_paths(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": [{"k": "b", "v": 1}, {"k": "a", "v": 1}]}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": [{"k": "a", "v": 1}, {"k": "b", "v": 2}]}}, system_id="after")
        result = cmp.compare(source, target, manifest(policy("unordered_multiset", "/out/value")))
        (d,) = result.divergences
        self.assertEqual(d.path, "/out/value/1/v")
        self.assertEqual(d.raw_path_source, "/out/value/0/v")
        self.assertEqual(d.raw_path_target, "/out/value/1/v")

    def test_a_length_difference_is_reported_once_at_the_list(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": [1, 2, 3]}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": [1, 2]}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        self.assertEqual([d.path for d in result.divergences], ["/out/value"])
        self.assertIn("length", result.divergences[0].why)


class Tolerances(unittest.TestCase):
    def test_absolute_tolerance_accepts_within_and_rejects_beyond(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 1.000}}})
        near = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 1.004}}}, system_id="after")
        far = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 1.5}}}, system_id="after")
        spec = manifest(policy("numeric_abs_tolerance", "/out/value/x", abs=0.01, id="tol"))
        ok = cmp.compare(source, near, spec)
        self.assertTrue(ok.equivalent)
        self.assertEqual([t["policy_id"] for t in ok.tolerance_applications], ["tol"])
        (d,) = cmp.compare(source, far, spec).divergences
        self.assertEqual(d.policy_id, "tol")
        self.assertIn("0.01", d.why)

    def test_relative_tolerance(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 100}}})
        near = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 100.5}}}, system_id="after")
        far = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 103}}}, system_id="after")
        spec = manifest(policy("numeric_rel_tolerance", "/out/value/x", rel=0.01))
        self.assertTrue(cmp.compare(source, near, spec).equivalent)
        self.assertFalse(cmp.compare(source, far, spec).equivalent)

    def test_a_tolerance_over_a_non_number_is_a_divergence_not_a_pass(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": 1}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"x": "1"}}}, system_id="after")
        result = cmp.compare(source, target, manifest(policy("numeric_abs_tolerance", "/out/value/x", abs=0.5)))
        self.assertFalse(result.equivalent)
        (d,) = result.divergences
        self.assertIn("the tolerance does not apply", d.why)
        self.assertIn("int vs str", d.why)

    def test_timestamp_tolerance_compares_parsed_instants(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"at": "2026-09-03T01:02:03Z"}}})
        near = observation({"cli": {"exit_code": 0}, "out": {"value": {"at": "2026-09-03T01:02:06+00:00"}}}, system_id="after")
        far = observation({"cli": {"exit_code": 0}, "out": {"value": {"at": "2026-09-03T02:02:03Z"}}}, system_id="after")
        spec = manifest(policy("timestamp", "/out/value/at", tolerance_s=5))
        self.assertTrue(cmp.compare(source, near, spec).equivalent)
        self.assertFalse(cmp.compare(source, far, spec).equivalent)

    def test_generated_ids_compare_equal_when_relationships_agree(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"id": UUID_A, "ref": UUID_A}}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": {"id": UUID_B, "ref": UUID_B}}}, system_id="after")
        spec = manifest(policy("generated_id", "/out/value/id", pattern="uuid", paths=["/out/value/ref"]))
        self.assertTrue(cmp.compare(source, target, spec).equivalent)
        broken = observation({"cli": {"exit_code": 0}, "out": {"value": {"id": UUID_B, "ref": UUID_A}}}, system_id="after")
        (d,) = cmp.compare(source, broken, spec).divergences
        self.assertEqual(d.path, "/out/value/ref")
        self.assertEqual((d.raw_source, d.raw_target), (UUID_A, UUID_A))
        self.assertEqual((d.normalized_source, d.normalized_target), ("<id:1>", "<id:2>"))


class MandatoryOrInformational(unittest.TestCase):
    def test_a_divergence_in_a_non_mandatory_probe_is_informational(self) -> None:
        spec = manifest(probes=[{"id": "cli", "adapter": "process", "mandatory": True}, {"id": "out", "adapter": "json", "source": "stdout", "mandatory": False}])
        source = observation({"cli": {"exit_code": 0}, "out": {"value": 1}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": 2}}, system_id="after")
        result = cmp.compare(source, target, spec)
        self.assertFalse(result.equivalent)
        self.assertFalse(result.divergences[0].mandatory)
        self.assertTrue(result.mandatory_equivalent)

    def test_an_explicit_exclusion_downgrades_a_divergence_to_informational(self) -> None:
        spec = manifest(exclusions=[{"id": "noise", "path": "/cli/stderr", "reason": "warnings vary by platform"}])
        source = observation({"cli": {"exit_code": 0, "stderr": "a"}, "out": {"value": 1}})
        target = observation({"cli": {"exit_code": 0, "stderr": "b"}, "out": {"value": 1}}, system_id="after")
        result = cmp.compare(source, target, spec)
        self.assertTrue(result.mandatory_equivalent)
        self.assertEqual(result.divergences[0].excluded_by, "noise")


class Unverifiable(unittest.TestCase):
    def test_a_run_that_did_not_observe_cannot_be_compared(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": 1}})
        target = observation({}, system_id="after", status="timeout")
        result = cmp.compare(source, target, manifest())
        self.assertEqual(result.status, "unverifiable")
        self.assertFalse(result.equivalent)
        self.assertIn("timeout", result.problems[0])

    def test_a_missing_mandatory_probe_on_one_side_is_a_divergence(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": 1}})
        target = observation({"cli": {"exit_code": 0}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        self.assertEqual(result.status, "compared")
        self.assertEqual(result.divergences[0].path, "/out")
        self.assertTrue(result.divergences[0].mandatory)

    def test_a_record_of_another_version_fails_closed(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": 1}})
        target = observation({"cli": {"exit_code": 0}, "out": {"value": 1}}, system_id="after")
        target["record_version"] = "invara.assurance.observation/0"
        with self.assertRaises(cmp.CompareError):
            cmp.compare(source, target, manifest())


class Serialisation(unittest.TestCase):
    def test_the_result_round_trips(self) -> None:
        source = observation({"cli": {"exit_code": 0}, "out": {"value": {"a": 1}}})
        target = observation({"cli": {"exit_code": 1}, "out": {"value": {"a": 2}}}, system_id="after")
        result = cmp.compare(source, target, manifest())
        again = cmp.Comparison.from_dict(result.as_dict())
        self.assertEqual(again, result)
        self.assertEqual(len(result.divergences), 2)


if __name__ == "__main__":
    unittest.main()
