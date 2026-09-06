"""Assurance claims, and the final verdict formed from them.

A claim is what the evidence supports about one requirement of the
manifest, in one of six words. They are deliberately not a confidence
score and deliberately not the four kernel verdicts: a corpus that compared
equal is *preserved within the envelope*, a search that found nothing is
*no divergence found*, and only an exhaustively enumerated domain is
*proved*. Every result names the manifest digest and the baseline digest it
was computed under, so a result cannot be re-read under a different
definition of equivalence.

The final verdict keeps the kernel's four statuses and its habit of naming
the rule that decided, kill path first. It is a pure function of the
requirements, the results and a few flags the engine raises; nothing in
this module can look at the world, which is what lets a verdict be
recomputed from stored records and come out identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contract import BLOCK, HUMAN_REVIEW, PASS, UNVERIFIABLE
from .manifest import CLAIM_KINDS, content_digest

__all__ = [
    "latest_per_claim",
    "CLAIM_STATUSES",
    "ClaimResult",
    "DIVERGED",
    "FinalVerdict",
    "HUMAN_REVIEW",
    "NO_DIVERGENCE_FOUND",
    "PRESERVED_WITHIN_ENVELOPE",
    "PROVED_WITHIN_DECLARED_DOMAIN",
    "UNVERIFIABLE",
    "coverage_summary",
    "final_verdict",
]

#: Every member of an explicitly finite domain was compared and found
#: equivalent. The only claim that may use the word "proved".
PROVED_WITHIN_DECLARED_DOMAIN = "PROVED_WITHIN_DECLARED_DOMAIN"
#: Every input of a finite corpus compared equivalent under the declared
#: policies. Says nothing about inputs outside the corpus.
PRESERVED_WITHIN_ENVELOPE = "PRESERVED_WITHIN_ENVELOPE"
#: A bounded search ran and found no diverging input. Not a proof.
NO_DIVERGENCE_FOUND = "NO_DIVERGENCE_FOUND"
#: At least one input diverged, and the divergence is on record.
DIVERGED = "DIVERGED"
#: The claim could not be evaluated: a run did not observe, a budget was
#: zero, an enumeration did not complete. Unchecked is not preserved.
#: (``UNVERIFIABLE`` and ``HUMAN_REVIEW`` are the kernel's words on purpose.)

CLAIM_STATUSES = (
    PROVED_WITHIN_DECLARED_DOMAIN,
    PRESERVED_WITHIN_ENVELOPE,
    NO_DIVERGENCE_FOUND,
    DIVERGED,
    UNVERIFIABLE,
    HUMAN_REVIEW,
)

_ALLOWED: dict[str, tuple[str, ...]] = {
    "corpus_equivalence": (PRESERVED_WITHIN_ENVELOPE, DIVERGED, UNVERIFIABLE),
    "counterexample_search": (NO_DIVERGENCE_FOUND, DIVERGED, UNVERIFIABLE),
    "finite_domain_proof": (PROVED_WITHIN_DECLARED_DOMAIN, DIVERGED, UNVERIFIABLE),
    "performance_envelope": (PRESERVED_WITHIN_ENVELOPE, DIVERGED, UNVERIFIABLE),
    "baseline_stability": (NO_DIVERGENCE_FOUND, HUMAN_REVIEW, UNVERIFIABLE),
}


COMPARING_KINDS = ("corpus_equivalence", "counterexample_search", "finite_domain_proof")


@dataclass(frozen=True)
class ClaimResult:
    claim_id: str
    kind: str
    status: str
    mandatory: bool
    manifest_digest: str
    baseline_digest: str
    coverage: dict[str, Any] = field(default_factory=dict)
    divergences: tuple[dict[str, Any], ...] = ()
    counterexample: dict[str, Any] | None = None
    unverified: tuple[str, ...] = ()
    evidence_digests: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in CLAIM_KINDS:
            raise ValueError(f"unknown claim kind {self.kind!r}")
        if self.status not in CLAIM_STATUSES:
            raise ValueError(f"unknown claim status {self.status!r}")
        if self.status not in _ALLOWED[self.kind]:
            raise ValueError(f"a {self.kind} claim cannot be {self.status}")
        if self.status == PROVED_WITHIN_DECLARED_DOMAIN:
            if self.coverage.get("kind") != "exhaustive" or self.coverage.get("members") != self.coverage.get("cardinality"):
                raise ValueError("PROVED_WITHIN_DECLARED_DOMAIN needs exhaustive coverage of every member")
        if self.status == DIVERGED and not self.divergences:
            raise ValueError("DIVERGED must carry at least one divergence")
        if self.status == UNVERIFIABLE and not self.unverified:
            raise ValueError("UNVERIFIABLE must say what could not be verified")

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "status": self.status,
            "mandatory": self.mandatory,
            "manifest_digest": self.manifest_digest,
            "baseline_digest": self.baseline_digest,
            "coverage": dict(self.coverage),
            "divergences": [dict(d) for d in self.divergences],
            "counterexample": dict(self.counterexample) if self.counterexample is not None else None,
            "unverified": list(self.unverified),
            "evidence_digests": list(self.evidence_digests),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClaimResult":
        return cls(
            claim_id=data["claim_id"],
            kind=data["kind"],
            status=data["status"],
            mandatory=bool(data["mandatory"]),
            manifest_digest=data["manifest_digest"],
            baseline_digest=data["baseline_digest"],
            coverage=dict(data.get("coverage", {})),
            divergences=tuple(dict(d) for d in data.get("divergences", [])),
            counterexample=dict(data["counterexample"]) if data.get("counterexample") is not None else None,
            unverified=tuple(data.get("unverified", [])),
            evidence_digests=tuple(data.get("evidence_digests", [])),
            detail=data.get("detail", ""),
        )

    def digest(self) -> str:
        return content_digest(self.as_dict())


@dataclass(frozen=True)
class FinalVerdict:
    status: str
    reason: str
    #: The evidence field that carried the decision, named as a field here.
    decided_by: str
    integrity: tuple[str, ...] = ()
    constraint_breaks: tuple[str, ...] = ()
    diverged: tuple[str, ...] = ()
    never_evaluated: tuple[str, ...] = ()
    unverifiable: tuple[str, ...] = ()
    needs_human: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    proved: tuple[str, ...] = ()
    #: Mandatory claims whose bounded search (or repeated run) found no divergence: evidence, not a proof, and
    #: never listed as preserved.
    no_divergence_found: tuple[str, ...] = ()
    informational: tuple[str, ...] = ()
    #: The status each informational claim reached ("never evaluated" when none is on record): an optional
    #: claim never decides the verdict, but what it found is never dropped from the result.
    informational_statuses: dict[str, str] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "integrity": list(self.integrity),
            "constraint_breaks": list(self.constraint_breaks),
            "diverged": list(self.diverged),
            "never_evaluated": list(self.never_evaluated),
            "unverifiable": list(self.unverifiable),
            "needs_human": list(self.needs_human),
            "preserved": list(self.preserved),
            "proved": list(self.proved),
            "no_divergence_found": list(self.no_divergence_found),
            "informational": list(self.informational),
            "informational_statuses": dict(self.informational_statuses),
        }


def latest_per_claim(
    results: Sequence[Mapping[str, Any]],
    manifest_digest: str | None,
    baseline_digest: str | None = None,
    kinds: Mapping[str, str] | None = None,
) -> dict[str, Mapping[str, Any]]:
    """The result dict that counts for each claim id: latest, under this manifest and baseline, of the declared kind.

    One rule for the verdict and for the coverage map, so the two can never
    disagree about which result stands.
    """

    latest: dict[str, Mapping[str, Any]] = {}
    for result in results:
        if manifest_digest is not None and result.get("manifest_digest") != manifest_digest:
            continue
        if baseline_digest is not None and result.get("baseline_digest", "") not in ("", baseline_digest):
            continue
        claim_id = str(result.get("claim_id"))
        if kinds is not None and claim_id in kinds and kinds[claim_id] != result.get("kind"):
            continue
        latest[claim_id] = result
    return latest


def _latest_per_claim(
    results: Sequence[ClaimResult],
    manifest_digest: str | None,
    baseline_digest: str | None = None,
    kinds: Mapping[str, str] | None = None,
) -> dict[str, ClaimResult]:
    by_id = {id(result.as_dict()): result for result in results}
    chosen = latest_per_claim([result.as_dict() for result in results], manifest_digest, baseline_digest, kinds)
    out: dict[str, ClaimResult] = {}
    for claim_id, data in chosen.items():
        out[claim_id] = ClaimResult.from_dict(data)
    return out


def final_verdict(
    requirements: Sequence[Mapping[str, Any]],
    results: Sequence[ClaimResult],
    *,
    integrity_problems: Sequence[str] = (),
    constraint_breaks: Sequence[str] = (),
    human_review: Sequence[str] = (),
    post_divergence_amendments: Sequence[str] = (),
    weakening_amendments: Sequence[str] = (),
    volatile_unaccepted: Sequence[str] = (),
    manifest_digest: str | None = None,
    baseline_digest: str | None = None,
) -> FinalVerdict:
    """The ladder, kill path first. Seven exits, each naming its evidence.

    ``requirements`` are the manifest's claim requirements as dicts
    (``id``, ``kind``, ``mandatory``). Only results computed under
    ``manifest_digest`` count when it is given; a result from an earlier
    definition of equivalence is not evidence about this one.
    ``constraint_breaks`` is a repair unit that changed paths outside its
    declared ownership — the kernel's protected-path rule, one level up.
    """

    kinds = {str(req["id"]): str(req.get("kind", "")) for req in requirements}
    latest = _latest_per_claim(results, manifest_digest, baseline_digest, kinds)
    mandatory = [dict(req) for req in requirements if req.get("mandatory", True)]
    optional = [dict(req) for req in requirements if not req.get("mandatory", True)]

    diverged: list[str] = []
    never: list[str] = []
    unverifiable: list[str] = []
    needs_human: list[str] = list(human_review)
    preserved: list[str] = []
    proved: list[str] = []
    no_divergence: list[str] = []
    informational: list[str] = []
    informational_statuses: dict[str, str] = {}

    if not mandatory:
        never.append("no mandatory claim was declared, so nothing could be verified")
    for req in mandatory:
        result = latest.get(req["id"])
        if result is None:
            never.append(f"{req['id']}: never evaluated" + (f" under manifest {manifest_digest[:12]}" if manifest_digest else ""))
            continue
        if result.status == DIVERGED:
            first = result.divergences[0].get("path", "?") if result.divergences else "?"
            omitted = int((result.coverage or {}).get("divergences_omitted", 0) or 0)
            cut = f", {omitted} more omitted by budget" if omitted else ""
            diverged.append(f"{req['id']}: {len(result.divergences)} divergence(s) recorded{cut}, first at {first}")
        elif result.status == UNVERIFIABLE:
            unverifiable.append(f"{req['id']}: " + "; ".join(result.unverified[:3]))
        elif result.status == HUMAN_REVIEW:
            needs_human.append(f"{req['id']}: {result.detail or 'needs a person'}")
        elif result.status == PROVED_WITHIN_DECLARED_DOMAIN:
            proved.append(req["id"])
        elif result.status == NO_DIVERGENCE_FOUND:
            # a search that found nothing, or a source that repeated itself: evidence, kept apart from "preserved"
            no_divergence.append(req["id"])
        else:
            preserved.append(req["id"])
    for req in optional:
        result = latest.get(req["id"])
        informational.append(req["id"])
        # what an informational claim found is carried with the verdict it did not decide
        informational_statuses[req["id"]] = result.status if result is not None else "never evaluated"
        if result is not None and result.status == HUMAN_REVIEW:
            needs_human.append(f"{req['id']}: {result.detail or 'needs a person'}")
    for note in post_divergence_amendments:
        needs_human.append(f"post-divergence amendment: {note}")
    for note in weakening_amendments:
        needs_human.append(f"weakening amendment: {note}")
    for path in volatile_unaccepted:
        needs_human.append(f"volatile dimension not accepted: {path}")

    common = dict(
        integrity=tuple(integrity_problems),
        constraint_breaks=tuple(constraint_breaks),
        diverged=tuple(diverged),
        never_evaluated=tuple(never),
        unverifiable=tuple(unverifiable),
        needs_human=tuple(needs_human),
        preserved=tuple(preserved),
        proved=tuple(proved),
        no_divergence_found=tuple(no_divergence),
        informational=tuple(informational),
        informational_statuses=dict(informational_statuses),
    )
    if integrity_problems:
        return FinalVerdict(BLOCK, "evidence integrity failed: " + "; ".join(integrity_problems[:3]), decided_by="integrity", **common)
    if constraint_breaks:
        return FinalVerdict(BLOCK, f"{len(constraint_breaks)} ownership constraint(s) broken: " + "; ".join(constraint_breaks[:3]), decided_by="constraint_breaks", **common)
    if diverged:
        return FinalVerdict(BLOCK, f"{len(diverged)} mandatory claim(s) diverged: " + "; ".join(diverged[:3]), decided_by="diverged", **common)
    if never:
        return FinalVerdict(UNVERIFIABLE, "not every mandatory claim was evaluated: " + "; ".join(never[:3]), decided_by="never_evaluated", **common)
    if unverifiable:
        return FinalVerdict(
            UNVERIFIABLE,
            f"{len(unverifiable)} mandatory claim(s) could not be verified, so the transformation is unverified rather than accepted: " + "; ".join(unverifiable[:3]),
            decided_by="unverifiable",
            **common,
        )
    if needs_human:
        return FinalVerdict(HUMAN_REVIEW, f"machine claims hold; {len(needs_human)} item(s) need a person: " + "; ".join(needs_human[:3]), decided_by="needs_human", **common)
    # a PASS names every kind of evidence it rests on, and never hides what an informational claim found
    searched = f"{len(no_divergence)} with no divergence found" if no_divergence else ""
    informational_diverged = sorted(claim_id for claim_id, status in informational_statuses.items() if status == DIVERGED)
    qualifier = f"; informational claim(s) DIVERGED: {', '.join(informational_diverged)} (a difference is on record outside the mandatory claims)" if informational_diverged else ""
    if preserved:
        reason = f"{len(preserved)} mandatory claim(s) preserved" + (f" and {len(proved)} proved" if proved else "") + (f" and {searched}" if searched else "") + " under the declared envelope"
        return FinalVerdict(PASS, reason + qualifier, decided_by="preserved", **common)
    if proved:
        reason = f"{len(proved)} mandatory claim(s) proved within the declared domain" + (f", {searched} (not a proof)" if searched else "")
        return FinalVerdict(PASS, reason + qualifier, decided_by="proved", **common)
    return FinalVerdict(PASS, f"{len(no_divergence)} mandatory claim(s) with no divergence found; this is evidence, not a proof" + qualifier, decided_by="no_divergence_found", **common)


def coverage_summary(
    results: Sequence[ClaimResult],
    *,
    exclusions: Sequence[Mapping[str, Any]] = (),
    not_observed: Sequence[str] = (),
) -> dict[str, list[str]]:
    """The six kinds of knowledge a report must keep apart."""

    by_status: dict[str, list[str]] = {status: [] for status in CLAIM_STATUSES}
    for result in results:
        by_status[result.status].append(result.claim_id)
    return {
        "exhaustively_proved": by_status[PROVED_WITHIN_DECLARED_DOMAIN],
        "tested_over_finite_corpus": by_status[PRESERVED_WITHIN_ENVELOPE],
        "searched_without_divergence": by_status[NO_DIVERGENCE_FOUND],
        "diverged": by_status[DIVERGED],
        "not_verified": by_status[UNVERIFIABLE],
        "needs_human": by_status[HUMAN_REVIEW],
        "explicitly_excluded": [str(item.get("id")) for item in exclusions],
        "not_observed": list(not_observed),
    }
