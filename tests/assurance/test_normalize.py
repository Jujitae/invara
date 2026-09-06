"""Semantic normalization: symmetric, audited, and unable to erase evidence."""

from __future__ import annotations

import copy
import unittest

from _support import manifest_dict, observation, policy
from invara.assurance import manifest as m
from invara.assurance import normalize as n

UUID_A = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
UUID_B = "9b2c1d20-6e1f-4b6a-8f2a-1d2e3f4a5b6c"
UUID_C = "00000000-0000-4000-8000-000000000001"


def policies(*items: dict) -> tuple:
    loaded = m.Manifest.from_dict(manifest_dict(policies=list(items)))
    return loaded.policies


def probes(**probes_dict) -> dict:
    return observation(probes_dict)["probes"]


class TheAudit(unittest.TestCase):
    def test_exact_is_the_default_and_touches_nothing(self) -> None:
        raw = probes(cli={"exit_code": 0, "stdout": "x"}, out={"value": {"a": 1}})
        done = n.normalize(raw, ())
        self.assertEqual(done.value, raw)
        self.assertEqual(done.actions, ())

    def test_the_raw_input_is_never_mutated(self) -> None:
        raw = probes(out={"value": {"id": UUID_A, "at": "2026-09-03T01:02:03Z"}})
        frozen = copy.deepcopy(raw)
        n.normalize(raw, policies(policy("generated_id", "/out/value/id", pattern="uuid"), policy("timestamp", "/out/value/at")))
        self.assertEqual(raw, frozen)

    def test_every_action_names_its_policy_and_path(self) -> None:
        raw = probes(out={"value": {"id": UUID_A}})
        done = n.normalize(raw, policies(policy("generated_id", "/out/value/id", pattern="uuid", id="ids")))
        self.assertEqual([(a.policy_id, a.kind, a.path) for a in done.actions], [("ids", "generated_id", "/out/value/id")])

    def test_the_policy_set_digest_is_stable(self) -> None:
        one = n.policy_set_digest(policies(policy("ignore", "/cli/stderr", id="a")))
        two = n.policy_set_digest(policies(policy("ignore", "/cli/stderr", id="a")))
        other = n.policy_set_digest(policies(policy("ignore", "/cli/stdout", id="a")))
        self.assertEqual(one, two)
        self.assertNotEqual(one, other)
        self.assertEqual(len(one), 64)


class EachPolicy(unittest.TestCase):
    def test_canonical_json_parses_a_string_leaf(self) -> None:
        raw = probes(cli={"stdout": '{"b": 1, "a": [1, 2]}'})
        done = n.normalize(raw, policies(policy("canonical_json", "/cli/stdout")))
        self.assertEqual(done.value["cli"]["stdout"], {"a": [1, 2], "b": 1})

    def test_canonical_json_keeps_a_parse_failure_as_evidence(self) -> None:
        raw = probes(cli={"stdout": "not json"})
        done = n.normalize(raw, policies(policy("canonical_json", "/cli/stdout")))
        self.assertEqual(done.value["cli"]["stdout"], "not json")
        self.assertIn("unparseable", done.actions[0].note)

    def test_ordered_sequence_is_documentary_and_keeps_order(self) -> None:
        raw = probes(out={"value": [3, 1, 2]})
        done = n.normalize(raw, policies(policy("ordered_sequence", "/out/value")))
        self.assertEqual(done.value["out"]["value"], [3, 1, 2])

    def test_unordered_set_sorts_and_deduplicates(self) -> None:
        raw = probes(out={"value": ["b", "a", "b"]})
        done = n.normalize(raw, policies(policy("unordered_set", "/out/value")))
        self.assertEqual(done.value["out"]["value"], ["a", "b"])

    def test_unordered_multiset_sorts_and_keeps_duplicates(self) -> None:
        raw = probes(out={"value": [{"k": 2}, {"k": 1}, {"k": 2}]})
        done = n.normalize(raw, policies(policy("unordered_multiset", "/out/value")))
        self.assertEqual(done.value["out"]["value"], [{"k": 1}, {"k": 2}, {"k": 2}])

    def test_unordered_sort_remembers_where_each_element_came_from(self) -> None:
        raw = probes(out={"value": ["b", "a"]})
        done = n.normalize(raw, policies(policy("unordered_multiset", "/out/value")))
        self.assertEqual(done.path_map["/out/value/0"], "/out/value/1")
        self.assertEqual(done.path_map["/out/value/1"], "/out/value/0")

    def test_float_edges_make_nan_and_negative_zero_comparable(self) -> None:
        raw = probes(out={"value": {"a": float("nan"), "b": -0.0, "c": float("inf")}})
        done = n.normalize(raw, policies(policy("float_edges", "/out/value/*")))
        self.assertEqual(done.value["out"]["value"], {"a": "<nan>", "b": 0.0, "c": "<inf>"})

    def test_timestamp_becomes_a_placeholder_when_no_tolerance_is_declared(self) -> None:
        raw = probes(out={"value": {"at": "2026-09-03T01:02:03.123Z"}})
        done = n.normalize(raw, policies(policy("timestamp", "/out/value/at")))
        self.assertEqual(done.value["out"]["value"]["at"], "<timestamp>")

    def test_timestamp_inside_text_is_replaced_inline(self) -> None:
        raw = probes(files={"entries": {"report.txt": {"text": "generated 2026-09-03 01:02:03 by app"}}})
        done = n.normalize(raw, policies(policy("timestamp", "/files/entries/*/text")))
        self.assertEqual(done.value["files"]["entries"]["report.txt"]["text"], "generated <timestamp> by app")

    def test_a_value_that_is_not_a_timestamp_is_left_alone_and_noted(self) -> None:
        raw = probes(out={"value": {"at": "yesterday"}})
        done = n.normalize(raw, policies(policy("timestamp", "/out/value/at")))
        self.assertEqual(done.value["out"]["value"]["at"], "yesterday")
        self.assertIn("not a timestamp", done.actions[0].note)

    def test_timestamp_with_tolerance_keeps_the_value_for_the_comparator(self) -> None:
        raw = probes(out={"value": {"at": "2026-09-03T01:02:03Z"}})
        done = n.normalize(raw, policies(policy("timestamp", "/out/value/at", tolerance_s=5)))
        self.assertEqual(done.value["out"]["value"]["at"], "2026-09-03T01:02:03Z")

    def test_path_canonical_replaces_the_workspace_and_separators(self) -> None:
        raw = probes(cli={"stdout": "wrote C:\\ws\\run1\\out\\a.txt"})
        done = n.normalize(raw, policies(policy("path_canonical", "/cli/stdout")), context={"workspace": "C:\\ws\\run1"})
        self.assertEqual(done.value["cli"]["stdout"], "wrote $WORKSPACE/out/a.txt")

    def test_the_workspace_is_canonicalised_everywhere_by_the_builtin_policy(self) -> None:
        raw = probes(cli={"stdout": "/tmp/ws-77/out.txt"})
        done = n.normalize(raw, (), context={"workspace": "/tmp/ws-77"})
        self.assertEqual(done.value["cli"]["stdout"], "$WORKSPACE/out.txt")
        self.assertEqual(done.actions[0].policy_id, n.BUILTIN_WORKSPACE_POLICY)

    def test_the_system_root_is_canonicalised_by_the_builtin_policy(self) -> None:
        """A traceback names the file it came from; the root INVARA chose is not behaviour."""

        before = probes(cli={"stderr": 'File "C:\\work\\before\\app.py", line 4'})
        after = probes(cli={"stderr": 'File "C:\\work\\after\\app.py", line 4'})
        left = n.normalize(before, (), context={"root": "C:\\work\\before"})
        right = n.normalize(after, (), context={"root": "C:\\work\\after"})
        self.assertEqual(left.value, right.value)
        self.assertEqual(left.value["cli"]["stderr"], 'File "$ROOT/app.py", line 4')
        self.assertEqual(left.actions[0].policy_id, n.BUILTIN_WORKSPACE_POLICY)

    def test_redact_replaces_the_value_on_both_sides(self) -> None:
        raw = probes(out={"value": {"token": "abc"}})
        done = n.normalize(raw, policies(policy("redact", "/out/value/token")))
        self.assertEqual(done.value["out"]["value"]["token"], "<redacted>")

    def test_ignore_removes_the_field(self) -> None:
        raw = probes(cli={"exit_code": 0, "stderr": "noise"})
        done = n.normalize(raw, policies(policy("ignore", "/cli/stderr")))
        self.assertEqual(done.value["cli"], {"exit_code": 0})

    def test_line_endings_are_normalised_only_where_declared(self) -> None:
        raw = probes(cli={"stdout": "a\r\nb\rc", "stderr": "x\r\n"})
        done = n.normalize(raw, policies(policy("line_endings", "/cli/stdout")))
        self.assertEqual(done.value["cli"]["stdout"], "a\nb\nc")
        self.assertEqual(done.value["cli"]["stderr"], "x\r\n")

    def test_stable_map_translates_environment_specific_values(self) -> None:
        raw = probes(out={"value": {"host": "build-07"}})
        done = n.normalize(raw, policies(policy("stable_map", "/out/value/host", map={"build-07": "builder", "build-08": "builder"})))
        self.assertEqual(done.value["out"]["value"]["host"], "builder")

    def test_numeric_tolerances_do_not_change_values(self) -> None:
        raw = probes(out={"value": {"x": 1.0001}})
        done = n.normalize(raw, policies(policy("numeric_abs_tolerance", "/out/value/x", abs=0.01)))
        self.assertEqual(done.value["out"]["value"]["x"], 1.0001)
        self.assertEqual(done.actions, ())


class GeneratedIdentity(unittest.TestCase):
    def test_the_same_raw_id_maps_to_the_same_placeholder_everywhere(self) -> None:
        raw = probes(
            out={"value": {"order": {"id": UUID_A}, "lines": [{"order_id": UUID_A}, {"order_id": UUID_A}]}},
            files={"entries": {"r.txt": {"text": f"order {UUID_A} done"}}},
        )
        done = n.normalize(
            raw,
            policies(policy("generated_id", "/out/value/order/id", pattern="uuid", id="ids", paths=["/out/value/lines/*/order_id", "/files/entries/*/text"])),
        )
        self.assertEqual(done.value["out"]["value"]["order"]["id"], "<id:1>")
        self.assertEqual([line["order_id"] for line in done.value["out"]["value"]["lines"]], ["<id:1>", "<id:1>"])
        self.assertEqual(done.value["files"]["entries"]["r.txt"]["text"], "order <id:1> done")
        self.assertEqual(done.id_maps["ids"], {UUID_A: "<id:1>"})

    def test_relationships_are_preserved_not_wildcarded(self) -> None:
        source = probes(out={"value": {"orders": [{"id": UUID_A}, {"id": UUID_B}], "lines": [{"o": UUID_A}, {"o": UUID_A}, {"o": UUID_B}]}})
        target = probes(out={"value": {"orders": [{"id": UUID_C}, {"id": UUID_B}], "lines": [{"o": UUID_C}, {"o": UUID_B}, {"o": UUID_B}]}})
        pol = policies(policy("generated_id", "/out/value/orders/*/id", pattern="uuid", paths=["/out/value/lines/*/o"]))
        self.assertNotEqual(n.normalize(source, pol).value, n.normalize(target, pol).value)
        consistent = probes(out={"value": {"orders": [{"id": UUID_C}, {"id": UUID_B}], "lines": [{"o": UUID_C}, {"o": UUID_C}, {"o": UUID_B}]}})
        self.assertEqual(n.normalize(source, pol).value, n.normalize(consistent, pol).value)

    def test_integer_keys_map_by_first_appearance(self) -> None:
        raw = probes(db={"tables": {"orders": {"rows": [{"id": 7, "name": "a"}, {"id": 9, "name": "b"}]}, "lines": {"rows": [{"order_row": 9}, {"order_row": 7}]}}})
        done = n.normalize(raw, policies(policy("generated_id", "/db/tables/orders/rows/*/id", pattern="int", paths=["/db/tables/lines/rows/*/order_row"])))
        self.assertEqual([r["id"] for r in done.value["db"]["tables"]["orders"]["rows"]], ["<id:1>", "<id:2>"])
        self.assertEqual([r["order_row"] for r in done.value["db"]["tables"]["lines"]["rows"]], ["<id:2>", "<id:1>"])

    def test_ids_are_masked_when_sorting_an_unordered_list(self) -> None:
        first = probes(out={"value": [{"id": UUID_B, "sku": "x"}, {"id": UUID_A, "sku": "y"}]})
        second = probes(out={"value": [{"id": UUID_C, "sku": "y"}, {"id": UUID_A, "sku": "x"}]})
        pol = policies(policy("unordered_multiset", "/out/value"), policy("generated_id", "/out/value/*/id", pattern="uuid"))
        self.assertEqual(n.normalize(first, pol).value, n.normalize(second, pol).value)

    def test_indistinguishable_elements_with_ids_are_reported_as_ambiguous(self) -> None:
        raw = probes(out={"value": [{"id": UUID_A, "sku": "x"}, {"id": UUID_B, "sku": "x"}]})
        done = n.normalize(raw, policies(policy("unordered_multiset", "/out/value"), policy("generated_id", "/out/value/*/id", pattern="uuid")))
        self.assertTrue(done.ambiguities)
        self.assertIn("/out/value", done.ambiguities[0])

    def test_a_value_that_does_not_match_the_pattern_is_left_and_noted(self) -> None:
        raw = probes(out={"value": {"id": "not-a-uuid"}})
        done = n.normalize(raw, policies(policy("generated_id", "/out/value/id", pattern="uuid")))
        self.assertEqual(done.value["out"]["value"]["id"], "not-a-uuid")
        self.assertIn("no match", done.actions[0].note)


class Refusals(unittest.TestCase):
    """Validation refuses the blanket cases; freeze refuses what erases signal."""

    def test_a_policy_set_that_leaves_a_mandatory_probe_empty_is_refused(self) -> None:
        raw = probes(cli={"exit_code": 0}, out={"value": {"token": "x", "at": "2026-01-01T00:00:00Z"}})
        pol = policies(policy("redact", "/out/value/token"), policy("timestamp", "/out/value/at"))
        loaded = m.Manifest.from_dict(manifest_dict(policies=[p.as_dict() for p in pol]))
        problems = n.erases_signal(n.normalize(raw, pol).value, loaded.probes)
        self.assertEqual(problems, ["out: no comparable observation remains after normalization"])

    def test_a_probe_with_one_comparable_leaf_is_enough(self) -> None:
        raw = probes(cli={"exit_code": 0}, out={"value": {"token": "x", "total": 3}})
        pol = policies(policy("redact", "/out/value/token"))
        loaded = m.Manifest.from_dict(manifest_dict(policies=[p.as_dict() for p in pol]))
        self.assertEqual(n.erases_signal(n.normalize(raw, pol).value, loaded.probes), [])

    def test_placeholders_do_not_count_as_signal(self) -> None:
        raw = probes(cli={"exit_code": 0}, out={"value": {"at": "2026-01-01T00:00:00Z"}})
        pol = policies(policy("timestamp", "/out/value/at"))
        loaded = m.Manifest.from_dict(manifest_dict(policies=[p.as_dict() for p in pol]))
        self.assertTrue(n.erases_signal(n.normalize(raw, pol).value, loaded.probes))

    def test_a_missing_mandatory_probe_is_a_problem(self) -> None:
        raw = probes(cli={"exit_code": 0})
        loaded = m.Manifest.from_dict(manifest_dict())
        self.assertEqual(n.erases_signal(raw, loaded.probes), ["out: mandatory probe not observed"])


class Stability(unittest.TestCase):
    def test_volatile_paths_are_the_paths_that_differ_between_runs(self) -> None:
        runs = [
            probes(out={"value": {"id": UUID_A, "total": 3, "at": "2026-01-01T00:00:00Z"}}),
            probes(out={"value": {"id": UUID_B, "total": 3, "at": "2026-01-01T00:00:07Z"}}),
        ]
        self.assertEqual(n.volatile_paths(runs), ["/out/value/at", "/out/value/id"])

    def test_proposals_are_inferred_and_unaccepted(self) -> None:
        runs = [
            probes(out={"value": {"id": UUID_A, "at": "2026-01-01T00:00:00Z", "tags": ["a", "b"], "n": 1}}),
            probes(out={"value": {"id": UUID_B, "at": "2026-01-01T00:00:07Z", "tags": ["b", "a"], "n": 2}}),
        ]
        proposals = n.propose_policies(runs)
        by_path = {p["path"]: p for p in proposals}
        self.assertEqual(by_path["/out/value/id"]["kind"], "generated_id")
        self.assertEqual(by_path["/out/value/at"]["kind"], "timestamp")
        self.assertEqual(by_path["/out/value/tags"]["kind"], "unordered_multiset")
        self.assertEqual(by_path["/out/value/n"]["kind"], "human_decision")
        for proposal in proposals:
            self.assertEqual(proposal["origin"], "inferred")
            self.assertFalse(proposal["accepted"])

    def test_a_single_run_proposes_nothing(self) -> None:
        self.assertEqual(n.propose_policies([probes(out={"value": 1})]), [])

    def test_covered_reports_which_volatile_paths_an_accepted_policy_handles(self) -> None:
        pol = policies(policy("generated_id", "/out/value/id", pattern="uuid"))
        self.assertEqual(n.uncovered_volatile(["/out/value/id", "/out/value/at"], pol), ["/out/value/at"])


if __name__ == "__main__":
    unittest.main()
