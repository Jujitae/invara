"""Finite-domain proof: every member or nothing."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from invara.assurance import claims as c
from invara.assurance import proof
from invara.assurance.manifest import CorpusItem, FiniteDomain


@dataclass(frozen=True)
class Outcome:
    status: str
    mandatory_equivalent: bool
    divergences: tuple = ()


def domain(**parameters: Any) -> FiniteDomain:
    return FiniteDomain.from_dict({"parameters": parameters}, where="test")


def oracle(bad=lambda v: False, unverifiable=lambda v: False):
    def evaluate(item: CorpusItem) -> Outcome:
        if unverifiable(item.input):
            return Outcome("unverifiable", False)
        diverges = bad(item.input)
        return Outcome("compared", not diverges, ({"path": "/out/value", "input_id": item.id},) if diverges else ())
    return evaluate


class Enumeration(unittest.TestCase):
    def test_members_enumerate_the_cartesian_product_in_declared_order(self) -> None:
        members = domain(zone=[1, 2], kg={"range": [0, 1]}).members()
        self.assertEqual([m.input for m in members], [{"zone": 1, "kg": 0}, {"zone": 1, "kg": 1}, {"zone": 2, "kg": 0}, {"zone": 2, "kg": 1}])
        self.assertEqual([m.id for m in members], ["f-0001", "f-0002", "f-0003", "f-0004"])

    def test_the_domain_digest_is_stable_and_sensitive(self) -> None:
        self.assertEqual(domain(a=[1]).digest(), domain(a=[1]).digest())
        self.assertNotEqual(domain(a=[1]).digest(), domain(a=[2]).digest())


class Proving(unittest.TestCase):
    def test_an_equivalent_target_is_proved_within_the_declared_domain(self) -> None:
        result = proof.prove(domain(zone=[1, 2, 3], kg={"range": [0, 3]}), oracle(), max_members=100)
        self.assertEqual(result.status, c.PROVED_WITHIN_DECLARED_DOMAIN)
        self.assertEqual(result.cardinality, 12)
        self.assertEqual(result.members_compared, 12)
        self.assertEqual(len(result.member_digests), 12)
        self.assertEqual(result.backend, "exhaustive-enumeration/1")
        self.assertEqual(len(result.domain_digest), 64)

    def test_a_diverging_member_is_a_minimal_counterexample(self) -> None:
        result = proof.prove(
            domain(zone=[1, 2, 3], kg={"range": [0, 9]}),
            oracle(bad=lambda v: v["zone"] == 3 and v["kg"] >= 7),
            max_members=100,
        )
        self.assertEqual(result.status, c.DIVERGED)
        self.assertEqual(result.diverging_count, 3)
        self.assertEqual(result.counterexample["input"], {"zone": 3, "kg": 7})
        self.assertEqual(result.counterexample["id"], "f-0028")
        self.assertEqual(result.members_compared, 30)

    def test_a_domain_over_budget_is_unverifiable_not_sampled(self) -> None:
        evaluated = []

        def evaluate(item: CorpusItem) -> Outcome:
            evaluated.append(item.id)
            return Outcome("compared", True)

        result = proof.prove(domain(a={"range": [1, 100]}, b={"range": [1, 100]}), evaluate, max_members=1000)
        self.assertEqual(result.status, c.UNVERIFIABLE)
        self.assertIn("cardinality", result.reason)
        self.assertEqual(evaluated, [])

    def test_an_unverifiable_member_forbids_a_proof(self) -> None:
        result = proof.prove(domain(a=[1, 2, 3]), oracle(unverifiable=lambda v: v["a"] == 2), max_members=10)
        self.assertEqual(result.status, c.UNVERIFIABLE)
        self.assertEqual(result.members_compared, 2)
        self.assertIn("f-0002", result.reason)

    def test_a_divergence_outranks_an_unverifiable_member(self) -> None:
        result = proof.prove(domain(a=[1, 2, 3]), oracle(bad=lambda v: v["a"] == 3, unverifiable=lambda v: v["a"] == 2), max_members=10)
        self.assertEqual(result.status, c.DIVERGED)

    def test_an_enumeration_cut_short_by_time_is_unverifiable(self) -> None:
        ticks = iter(range(0, 10_000))
        result = proof.prove(domain(a={"range": [1, 50]}), oracle(), max_members=100, max_seconds=3, clock=lambda: float(next(ticks)))
        self.assertEqual(result.status, c.UNVERIFIABLE)
        self.assertLess(result.members_compared, 50)
        self.assertIn("time", result.reason)

    def test_the_result_round_trips(self) -> None:
        result = proof.prove(domain(a=[1, 2]), oracle(bad=lambda v: v["a"] == 2), max_members=10)
        self.assertEqual(proof.ProofResult.from_dict(result.as_dict()), result)


class Backends(unittest.TestCase):
    def test_the_default_backend_is_registered_and_versioned(self) -> None:
        backend = proof.backend("exhaustive-enumeration")
        self.assertEqual(backend.version, "1")
        self.assertEqual(backend.name, "exhaustive-enumeration")
        self.assertIn("exhaustive-enumeration", proof.backends())

    def test_an_unknown_backend_is_refused(self) -> None:
        with self.assertRaises(proof.ProofError):
            proof.prove(domain(a=[1]), oracle(), max_members=10, backend="smt")

    def test_a_future_backend_can_be_attached_without_changing_the_claim_model(self) -> None:
        class Stub:
            name = "stub"
            version = "0"

            def prove(self, finite, evaluate, *, max_members, max_seconds, clock):
                return proof.ProofResult(
                    status=c.UNVERIFIABLE, backend="stub/0", domain_digest=finite.digest(), cardinality=finite.cardinality,
                    members_compared=0, member_digests={}, diverging_count=0, counterexample=None, reason="stub declines", elapsed_s=0.0,
                )

        proof.register_backend(Stub())
        try:
            result = proof.prove(domain(a=[1]), oracle(), max_members=10, backend="stub")
            self.assertEqual(result.backend, "stub/0")
            self.assertEqual(result.status, c.UNVERIFIABLE)
        finally:
            proof.unregister_backend("stub")


if __name__ == "__main__":
    unittest.main()
