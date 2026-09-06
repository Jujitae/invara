"""Assurance claims and the final verdict ladder, kill path first."""

from __future__ import annotations

import dataclasses
import unittest

from invara.assurance import claims as c
from invara.contract import BLOCK, HUMAN_REVIEW, PASS, UNVERIFIABLE

MANIFEST = "m" * 64
BASELINE = "b" * 64


def requirement(ident: str, kind: str = "corpus_equivalence", mandatory: bool = True) -> dict:
    return {"id": ident, "kind": kind, "mandatory": mandatory}


def result(ident: str, status: str, kind: str = "corpus_equivalence", mandatory: bool = True, **over) -> c.ClaimResult:
    fields = dict(
        claim_id=ident,
        kind=kind,
        status=status,
        mandatory=mandatory,
        manifest_digest=MANIFEST,
        baseline_digest=BASELINE,
        coverage={"kind": "corpus", "members": 3},
        divergences=(),
        counterexample=None,
        unverified=(),
        evidence_digests=(),
        detail="",
    )
    fields.update(over)
    return c.ClaimResult(**fields)


class TheClaimModel(unittest.TestCase):
    def test_the_six_statuses_exist_and_nothing_else(self) -> None:
        self.assertEqual(
            c.CLAIM_STATUSES,
            (
                c.PROVED_WITHIN_DECLARED_DOMAIN,
                c.PRESERVED_WITHIN_ENVELOPE,
                c.NO_DIVERGENCE_FOUND,
                c.DIVERGED,
                c.UNVERIFIABLE,
                c.HUMAN_REVIEW,
            ),
        )

    def test_a_result_with_an_unknown_status_cannot_be_built(self) -> None:
        with self.assertRaises(ValueError):
            result("x", "PROBABLY_FINE")

    def test_a_result_round_trips_through_its_dict(self) -> None:
        one = result("x", c.DIVERGED, divergences=({"path": "/out/value/total"},))
        self.assertEqual(c.ClaimResult.from_dict(one.as_dict()), one)
        self.assertEqual(len(one.digest()), 64)

    def test_proved_requires_exhaustive_coverage(self) -> None:
        with self.assertRaises(ValueError):
            result("p", c.PROVED_WITHIN_DECLARED_DOMAIN, kind="finite_domain_proof", coverage={"kind": "corpus"})
        ok = result(
            "p",
            c.PROVED_WITHIN_DECLARED_DOMAIN,
            kind="finite_domain_proof",
            coverage={"kind": "exhaustive", "members": 8, "cardinality": 8},
        )
        self.assertEqual(ok.status, c.PROVED_WITHIN_DECLARED_DOMAIN)

    def test_a_search_cannot_claim_a_proof(self) -> None:
        with self.assertRaises(ValueError):
            result("s", c.PROVED_WITHIN_DECLARED_DOMAIN, kind="counterexample_search", coverage={"kind": "search"})

    def test_diverged_carries_at_least_one_divergence(self) -> None:
        with self.assertRaises(ValueError):
            result("x", c.DIVERGED, divergences=())


class TheFinalVerdict(unittest.TestCase):
    def test_every_mandatory_claim_preserved_is_a_pass(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus"), requirement("search", "counterexample_search")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE), result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 10})],
        )
        self.assertEqual(verdict.status, PASS)
        self.assertEqual(verdict.decided_by, "preserved")
        # A search that found nothing is evidence, not preservation: the buckets stay apart.
        self.assertEqual(verdict.preserved, ("corpus",))
        self.assertEqual(verdict.no_divergence_found, ("search",))

    def test_a_mandatory_divergence_blocks(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.DIVERGED, divergences=({"path": "/out/value/total"},))],
        )
        self.assertEqual(verdict.status, BLOCK)
        self.assertEqual(verdict.decided_by, "diverged")

    def test_a_mandatory_claim_never_evaluated_is_unverifiable(self) -> None:
        verdict = c.final_verdict([requirement("corpus"), requirement("search", "counterexample_search")], [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)])
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertEqual(verdict.decided_by, "never_evaluated")
        self.assertIn("search", verdict.never_evaluated[0])

    def test_an_unverifiable_mandatory_claim_is_never_a_pass(self) -> None:
        verdict = c.final_verdict([requirement("corpus")], [result("corpus", c.UNVERIFIABLE, unverified=("c1: timeout",))])
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertEqual(verdict.decided_by, "unverifiable")

    def test_divergence_outranks_unverifiable(self) -> None:
        verdict = c.final_verdict(
            [requirement("a"), requirement("b")],
            [result("a", c.UNVERIFIABLE, unverified=("x",)), result("b", c.DIVERGED, divergences=({"path": "/x"},))],
        )
        self.assertEqual(verdict.status, BLOCK)

    def test_integrity_outranks_everything(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            integrity_problems=("baseline digest mismatch",),
        )
        self.assertEqual(verdict.status, BLOCK)
        self.assertEqual(verdict.decided_by, "integrity")

    def test_a_broken_ownership_constraint_blocks_even_when_behaviour_held(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            constraint_breaks=("unit u1 changed lib/other.py outside its declared ownership app/*.py",),
        )
        self.assertEqual(verdict.status, BLOCK)
        self.assertEqual(verdict.decided_by, "constraint_breaks")
        self.assertTrue(verdict.constraint_breaks)

    def test_integrity_outranks_a_constraint_break(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            integrity_problems=("baseline digest mismatch",),
            constraint_breaks=("u1 changed other.py",),
        )
        self.assertEqual(verdict.decided_by, "integrity")

    def test_an_informational_divergence_does_not_block(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus"), requirement("perf", "performance_envelope", mandatory=False)],
            [
                result("corpus", c.PRESERVED_WITHIN_ENVELOPE),
                result("perf", c.DIVERGED, kind="performance_envelope", mandatory=False, divergences=({"path": "/timing"},)),
            ],
        )
        self.assertEqual(verdict.status, PASS)
        self.assertIn("perf", verdict.informational)

    def test_a_human_review_requirement_holds_the_pass(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            human_review=("arch: someone must look at module boundaries",),
        )
        self.assertEqual(verdict.status, HUMAN_REVIEW)
        self.assertEqual(verdict.decided_by, "needs_human")

    def test_a_post_divergence_amendment_pins_the_verdict_below_pass(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            post_divergence_amendments=("hide covers /out/value/total after divergence",),
        )
        self.assertEqual(verdict.status, HUMAN_REVIEW)
        self.assertIn("hide", verdict.needs_human[0])

    def test_an_unaccepted_volatile_dimension_needs_a_person(self) -> None:
        verdict = c.final_verdict(
            [requirement("corpus")],
            [result("corpus", c.PRESERVED_WITHIN_ENVELOPE)],
            volatile_unaccepted=("/out/value/created_at",),
        )
        self.assertEqual(verdict.status, HUMAN_REVIEW)

    def test_a_proved_claim_is_a_pass_and_is_distinguished_from_preserved(self) -> None:
        verdict = c.final_verdict(
            [requirement("finite", "finite_domain_proof")],
            [result("finite", c.PROVED_WITHIN_DECLARED_DOMAIN, kind="finite_domain_proof", coverage={"kind": "exhaustive", "members": 4, "cardinality": 4})],
        )
        self.assertEqual(verdict.status, PASS)
        self.assertEqual(verdict.proved, ("finite",))
        self.assertEqual(verdict.preserved, ())

    def test_the_decider_names_a_field_that_holds_evidence(self) -> None:
        names = {f.name for f in dataclasses.fields(c.FinalVerdict)}
        verdict = c.final_verdict([requirement("corpus")], [result("corpus", c.DIVERGED, divergences=({"path": "/x"},))])
        self.assertIn(verdict.decided_by, names)
        self.assertTrue(getattr(verdict, verdict.decided_by))

    def test_a_result_under_a_different_manifest_digest_does_not_count(self) -> None:
        stale = result("corpus", c.PRESERVED_WITHIN_ENVELOPE, manifest_digest="0" * 64)
        verdict = c.final_verdict([requirement("corpus")], [stale], manifest_digest=MANIFEST)
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertEqual(verdict.decided_by, "never_evaluated")

    def test_the_coverage_summary_separates_the_six_kinds_of_knowledge(self) -> None:
        summary = c.coverage_summary(
            [
                result("finite", c.PROVED_WITHIN_DECLARED_DOMAIN, kind="finite_domain_proof", coverage={"kind": "exhaustive", "members": 4, "cardinality": 4}),
                result("corpus", c.PRESERVED_WITHIN_ENVELOPE),
                result("search", c.NO_DIVERGENCE_FOUND, kind="counterexample_search", coverage={"kind": "search", "runs": 20}),
                result("bad", c.DIVERGED, divergences=({"path": "/x"},)),
                result("slow", c.UNVERIFIABLE, unverified=("c9: timeout",)),
            ],
            exclusions=[{"id": "threads", "path": "/cli/stderr", "reason": "scheduling"}],
            not_observed=["/db"],
        )
        self.assertEqual(summary["exhaustively_proved"], ["finite"])
        self.assertEqual(summary["tested_over_finite_corpus"], ["corpus"])
        self.assertEqual(summary["searched_without_divergence"], ["search"])
        self.assertEqual(summary["diverged"], ["bad"])
        self.assertEqual(summary["not_verified"], ["slow"])
        self.assertEqual(summary["explicitly_excluded"], ["threads"])
        self.assertEqual(summary["not_observed"], ["/db"])


if __name__ == "__main__":
    unittest.main()
