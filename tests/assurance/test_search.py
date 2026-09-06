"""The counterexample engine: seeded, reproducible, and minimizing."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any, Callable

from invara.assurance import claims as c
from invara.assurance import search
from invara.assurance.manifest import CorpusItem


@dataclass(frozen=True)
class Outcome:
    status: str
    mandatory_equivalent: bool
    divergences: tuple = ()


def oracle(diverges: Callable[[Any], bool], unverifiable: Callable[[Any], bool] = lambda _: False):
    def evaluate(item: CorpusItem) -> Outcome:
        if unverifiable(item.input):
            return Outcome("unverifiable", False)
        return Outcome("compared", not diverges(item.input), ({"path": "/out/value"},) if diverges(item.input) else ())
    return evaluate


def seeds(*inputs: Any) -> list[CorpusItem]:
    return [CorpusItem(id=f"s{i}", input=value) for i, value in enumerate(inputs, start=1)]


class ThePrng(unittest.TestCase):
    def test_the_same_seed_gives_the_same_sequence(self) -> None:
        a = search.Prng(7)
        b = search.Prng(7)
        self.assertEqual([a.randint(0, 1000) for _ in range(20)], [b.randint(0, 1000) for _ in range(20)])

    def test_different_seeds_differ(self) -> None:
        self.assertNotEqual([search.Prng(1).randint(0, 1000) for _ in range(10)], [search.Prng(2).randint(0, 1000) for _ in range(10)])

    def test_the_sequence_is_pinned_so_evidence_can_be_replayed_across_versions(self) -> None:
        self.assertEqual(search.Prng(42).next_u64(), 13679457532755275413)

    def test_randint_stays_in_range_and_choice_picks_members(self) -> None:
        prng = search.Prng(3)
        for _ in range(500):
            self.assertIn(prng.randint(-2, 2), (-2, -1, 0, 1, 2))
        self.assertIn(prng.choice(["a", "b"]), ("a", "b"))


class BoundaryValues(unittest.TestCase):
    def test_numeric_leaves_get_their_neighbours_and_extremes(self) -> None:
        candidates = search.boundary_candidates({"qty": 9, "name": "x"})
        values = [candidate["qty"] for candidate, description in candidates if isinstance(candidate, dict) and isinstance(candidate.get("qty"), int)]
        for expected in (8, 10, 0, 1, -1, -9, 18, 2**31 - 1, 2**63 - 1, -(2**63)):
            self.assertIn(expected, values)

    def test_every_leaf_gets_null_empty_and_missing(self) -> None:
        candidates = search.boundary_candidates({"a": {"b": [1, "s"]}})
        descriptions = [description for _, description in candidates]
        self.assertTrue(any("null" in d for d in descriptions))
        self.assertTrue(any("empty" in d for d in descriptions))
        self.assertTrue(any("missing" in d for d in descriptions))

    def test_candidates_are_deterministic(self) -> None:
        one = json.dumps([c for c, _ in search.boundary_candidates({"qty": 3, "tags": ["a"]})], sort_keys=True)
        two = json.dumps([c for c, _ in search.boundary_candidates({"qty": 3, "tags": ["a"]})], sort_keys=True)
        self.assertEqual(one, two)


class Mutations(unittest.TestCase):
    def test_the_catalogue_covers_every_required_kind(self) -> None:
        prng = search.Prng(11)
        base = {"name": "order", "items": [{"sku": "a", "qty": 2}, {"sku": "b", "qty": 3}], "note": None, "total": 12.5, "ops": ["add", "ship"]}
        seen: set[str] = set()
        for _ in range(400):
            _, description = search.mutate(base, prng)
            seen.add(description.split(":")[0])
        for kind in (
            "null", "empty", "missing", "numeric-zero", "numeric-sign", "numeric-min", "numeric-max", "numeric-step",
            "string-length", "string-unicode", "list-reorder", "list-duplicate", "list-remove", "list-cardinality",
            "object-nested", "object-unknown-key",
        ):
            self.assertIn(kind, seen, f"{kind} never produced; seen {sorted(seen)}")

    def test_a_mutation_never_changes_the_original(self) -> None:
        prng = search.Prng(5)
        base = {"a": [1, 2, {"b": "c"}]}
        frozen = json.dumps(base, sort_keys=True)
        for _ in range(50):
            search.mutate(base, prng)
        self.assertEqual(json.dumps(base, sort_keys=True), frozen)

    def test_operation_sequences_are_reordered_duplicated_and_dropped(self) -> None:
        prng = search.Prng(9)
        base = [{"op": "deposit", "amount": 5}, {"op": "withdraw", "amount": 3}, {"op": "close"}]
        seen = set()
        for _ in range(100):
            _, description = search.mutate(base, prng, sequence_of_operations=True)
            seen.add(description.split(":")[0])
        self.assertTrue({"list-reorder", "list-duplicate", "list-remove"} <= seen, seen)


class Shrinking(unittest.TestCase):
    def test_a_list_shrinks_to_the_offending_element(self) -> None:
        minimized, steps = search.shrink(["a", "bad", "c", "d", "e"], lambda v: isinstance(v, list) and "bad" in v, max_steps=200)
        self.assertEqual(minimized, ["bad"])
        self.assertGreater(steps, 0)

    def test_a_number_shrinks_toward_the_boundary(self) -> None:
        minimized, _ = search.shrink({"x": 1000, "y": "noise"}, lambda v: isinstance(v, dict) and v.get("x", 0) >= 100, max_steps=200)
        self.assertEqual(minimized, {"x": 100})

    def test_a_string_shrinks_to_the_offending_character(self) -> None:
        minimized, _ = search.shrink("héllo wörld", lambda v: isinstance(v, str) and "é" in v, max_steps=200)
        self.assertEqual(minimized, "é")

    def test_shrinking_is_bounded_and_never_returns_a_non_reproducing_input(self) -> None:
        calls = []

        def reproduces(value: Any) -> bool:
            calls.append(value)
            return isinstance(value, list) and len(value) >= 3

        minimized, steps = search.shrink(list(range(50)), reproduces, max_steps=10)
        self.assertTrue(reproduces(minimized))
        self.assertLessEqual(steps, 10)

    def test_the_minimized_input_is_never_larger_than_the_original(self) -> None:
        original = {"k": [1, 2, 3], "s": "abcdef"}
        minimized, _ = search.shrink(original, lambda v: True, max_steps=100)
        self.assertLessEqual(len(json.dumps(minimized)), len(json.dumps(original)))


class TheSearch(unittest.TestCase):
    def test_a_boundary_divergence_is_found_and_minimized(self) -> None:
        result = search.search(
            seeds({"qty": 9, "name": "abc"}),
            oracle(lambda v: isinstance(v, dict) and v.get("qty") == 10),
            seed=1, max_runs=100, max_seconds=60, shrink_steps=200,
        )
        self.assertEqual(result.status, c.DIVERGED)
        self.assertEqual(result.original["input"]["qty"], 10)
        self.assertEqual(result.original["derived_from"], "s1")
        self.assertEqual(result.minimized["input"], {"qty": 10})
        self.assertGreater(result.runs, 0)

    def test_no_divergence_found_is_not_a_proof(self) -> None:
        result = search.search(seeds({"qty": 1}), oracle(lambda v: False), seed=1, max_runs=30, max_seconds=60, shrink_steps=10)
        self.assertEqual(result.status, c.NO_DIVERGENCE_FOUND)
        self.assertEqual(result.runs, 30)
        self.assertIsNone(result.original)
        self.assertNotIn("PROVED", result.as_dict()["status"])

    def test_the_same_seed_replays_the_same_search(self) -> None:
        kwargs = dict(seed=77, max_runs=40, max_seconds=60, shrink_steps=10)
        one = search.search(seeds({"a": [1, 2], "b": "x"}), oracle(lambda v: False), **kwargs)
        two = search.search(seeds({"a": [1, 2], "b": "x"}), oracle(lambda v: False), **kwargs)
        self.assertEqual(one.trace_digest, two.trace_digest)
        self.assertNotEqual(one.trace_digest, search.search(seeds({"a": [1, 2], "b": "x"}), oracle(lambda v: False), **dict(kwargs, seed=78)).trace_digest)

    def test_the_run_budget_is_respected(self) -> None:
        evaluated = []

        def evaluate(item: CorpusItem) -> Outcome:
            evaluated.append(item.input)
            return Outcome("compared", True)

        result = search.search(seeds({"n": 1}), evaluate, seed=1, max_runs=7, max_seconds=60, shrink_steps=0)
        self.assertEqual(len(evaluated), 7)
        self.assertEqual(result.runs, 7)

    def test_the_time_budget_is_respected(self) -> None:
        ticks = iter(range(0, 10_000))
        result = search.search(
            seeds({"n": 1}), oracle(lambda v: False), seed=1, max_runs=10_000, max_seconds=5, shrink_steps=0, clock=lambda: float(next(ticks))
        )
        self.assertLess(result.runs, 10)
        self.assertEqual(result.status, c.NO_DIVERGENCE_FOUND)

    def test_a_search_that_could_compare_nothing_is_unverifiable(self) -> None:
        result = search.search(seeds({"n": 1}), oracle(lambda v: False, unverifiable=lambda v: True), seed=1, max_runs=5, max_seconds=60, shrink_steps=0)
        self.assertEqual(result.status, c.UNVERIFIABLE)
        self.assertEqual(result.unverifiable_runs, 5)

    def test_partially_unverifiable_runs_are_counted_not_hidden(self) -> None:
        result = search.search(
            seeds({"n": 1}),
            oracle(lambda v: False, unverifiable=lambda v: isinstance(v, dict) and v.get("n") is None),
            seed=1, max_runs=40, max_seconds=60, shrink_steps=0,
        )
        self.assertEqual(result.status, c.NO_DIVERGENCE_FOUND)
        self.assertGreater(result.unverifiable_runs, 0)
        self.assertEqual(result.runs, result.compared_runs + result.unverifiable_runs)

    def test_a_zero_budget_is_unverifiable(self) -> None:
        result = search.search(seeds({"n": 1}), oracle(lambda v: True), seed=1, max_runs=0, max_seconds=60, shrink_steps=0)
        self.assertEqual(result.status, c.UNVERIFIABLE)

    def test_the_result_serialises_with_both_inputs_and_the_seed(self) -> None:
        result = search.search(seeds({"qty": 9}), oracle(lambda v: isinstance(v, dict) and v.get("qty") == 10), seed=3, max_runs=50, max_seconds=60, shrink_steps=50)
        data = result.as_dict()
        self.assertEqual(data["seed"], 3)
        self.assertEqual(data["original"]["input"]["qty"], 10)
        self.assertEqual(data["minimized"]["input"], {"qty": 10})
        self.assertIn("mutation", data["original"])
        self.assertEqual(search.SearchResult.from_dict(data), result)

    def test_operation_sequences_are_searched_when_declared(self) -> None:
        def diverges(value: Any) -> bool:
            ops = value if isinstance(value, list) else []
            return sum(1 for op in ops if op == "withdraw") >= 2

        result = search.search(seeds(["deposit", "withdraw", "close"]), oracle(diverges), seed=5, max_runs=200, max_seconds=60, shrink_steps=100, sequence_of_operations=True)
        self.assertEqual(result.status, c.DIVERGED)
        self.assertEqual(result.minimized["input"], ["withdraw", "withdraw"])


if __name__ == "__main__":
    unittest.main()
