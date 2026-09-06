"""Finite-domain proof: enumerate every member, or issue nothing.

When the manifest declares its input domain as a finite cartesian product,
equivalence over that domain is decidable by exhaustion, and this module
decides it: every member is run on both systems and compared; only when
every member is equivalent does it issue
:data:`~invara.assurance.claims.PROVED_WITHIN_DECLARED_DOMAIN`. A domain
larger than the budget, a member that could not be compared, or an
enumeration cut short by time is ``UNVERIFIABLE`` — never a partial proof.
A divergence is reported with the minimal diverging member: smallest by
canonical size, earliest in declared order among equals.

The backend interface is versioned so that an SMT, symbolic-execution,
model-checking or translation-validation backend can be attached later:
it receives the domain and the comparator and returns the same
:class:`ProofResult`. None of those is bundled; the claim model does not
change when one is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from .claims import DIVERGED, PROVED_WITHIN_DECLARED_DOMAIN, UNVERIFIABLE
from .manifest import CorpusItem, FiniteDomain, content_digest
from .records import COMPARISON_VERSION, OBSERVATION_VERSION

__all__ = [
    "BACKEND_PROTOCOL_VERSION",
    "ExhaustiveEnumeration",
    "ProofBackend",
    "ProofError",
    "ProofResult",
    "backend",
    "backends",
    "binding_problems",
    "prove",
    "register_backend",
    "unregister_backend",
]

#: The interface a backend implements. Bumped when ``prove``'s signature or
#: :class:`ProofResult` changes shape.
BACKEND_PROTOCOL_VERSION = "invara.assurance.proof-backend/1"


class ProofError(ValueError):
    """A proof could not even be attempted as asked."""


@dataclass(frozen=True)
class ProofResult:
    status: str
    backend: str
    domain_digest: str
    cardinality: int
    members_compared: int
    member_digests: dict[str, str]
    diverging_count: int
    counterexample: dict[str, Any] | None
    reason: str
    elapsed_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "backend": self.backend,
            "domain_digest": self.domain_digest,
            "cardinality": self.cardinality,
            "members_compared": self.members_compared,
            "member_digests": dict(self.member_digests),
            "diverging_count": self.diverging_count,
            "counterexample": dict(self.counterexample) if self.counterexample is not None else None,
            "reason": self.reason,
            "elapsed_s": self.elapsed_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProofResult":
        return cls(
            status=data["status"],
            backend=data["backend"],
            domain_digest=data["domain_digest"],
            cardinality=int(data["cardinality"]),
            members_compared=int(data["members_compared"]),
            member_digests=dict(data.get("member_digests", {})),
            diverging_count=int(data.get("diverging_count", 0)),
            counterexample=dict(data["counterexample"]) if data.get("counterexample") is not None else None,
            reason=data.get("reason", ""),
            elapsed_s=float(data.get("elapsed_s", 0.0)),
        )


class ProofBackend(Protocol):
    name: str
    version: str

    def prove(
        self,
        finite: FiniteDomain,
        evaluate: Callable[[CorpusItem], Any],
        *,
        max_members: int,
        max_seconds: float | None,
        clock: Callable[[], float],
    ) -> ProofResult: ...


def _size(value: Any) -> int:
    return len(content_digest(value)) if False else len(_canonical(value))


def _canonical(value: Any) -> str:
    import json  # local: only for measuring size, never for evidence

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


class ExhaustiveEnumeration:
    name = "exhaustive-enumeration"
    version = "1"

    def prove(
        self,
        finite: FiniteDomain,
        evaluate: Callable[[CorpusItem], Any],
        *,
        max_members: int,
        max_seconds: float | None,
        clock: Callable[[], float],
    ) -> ProofResult:
        label = f"{self.name}/{self.version}"
        digest = finite.digest()
        cardinality = finite.cardinality
        if cardinality > max_members:
            return ProofResult(
                status=UNVERIFIABLE,
                backend=label,
                domain_digest=digest,
                cardinality=cardinality,
                members_compared=0,
                member_digests={},
                diverging_count=0,
                counterexample=None,
                reason=f"cardinality {cardinality} exceeds the budget of {max_members} members; no partial proof is issued",
                elapsed_s=0.0,
            )
        started = clock()
        compared = 0
        diverging: list[tuple[int, int, CorpusItem, Any]] = []
        unverifiable: list[str] = []
        digests: dict[str, str] = {}
        incomplete: str | None = None
        for order, member in enumerate(finite.members()):
            if max_seconds is not None and clock() - started > max_seconds:
                incomplete = f"time budget of {max_seconds:g}s exhausted after {compared} member(s)"
                break
            outcome = evaluate(member)
            digest_of = getattr(outcome, "digest", None)
            digests[member.id] = (
                digest_of()
                if callable(digest_of)
                else content_digest({"input_id": member.id, "status": outcome.status, "equivalent": bool(outcome.mandatory_equivalent)})
            )
            if outcome.status != "compared":
                unverifiable.append(member.id)
                continue
            compared += 1
            if not outcome.mandatory_equivalent:
                diverging.append((len(_canonical(member.input)), order, member, outcome))
        elapsed = clock() - started
        if diverging:
            diverging.sort(key=lambda entry: (entry[0], entry[1]))
            size, _, member, outcome = diverging[0]
            counterexample: dict[str, Any] = {"id": member.id, "input": member.input, "size": size}
            first = getattr(outcome, "divergences", ())
            if first:
                counterexample["divergence"] = dict(first[0]) if isinstance(first[0], dict) else first[0].as_dict()
            return ProofResult(
                status=DIVERGED,
                backend=label,
                domain_digest=digest,
                cardinality=cardinality,
                members_compared=compared,
                member_digests=digests,
                diverging_count=len(diverging),
                counterexample=counterexample,
                reason=f"{len(diverging)} of {compared} compared member(s) diverged; minimal counterexample {member.id}",
                elapsed_s=elapsed,
            )
        if incomplete or unverifiable:
            reason = incomplete or f"{len(unverifiable)} member(s) could not be compared: " + ", ".join(unverifiable[:5])
            return ProofResult(
                status=UNVERIFIABLE,
                backend=label,
                domain_digest=digest,
                cardinality=cardinality,
                members_compared=compared,
                member_digests=digests,
                diverging_count=0,
                counterexample=None,
                reason=reason + "; no partial proof is issued",
                elapsed_s=elapsed,
            )
        return ProofResult(
            status=PROVED_WITHIN_DECLARED_DOMAIN,
            backend=label,
            domain_digest=digest,
            cardinality=cardinality,
            members_compared=compared,
            member_digests=digests,
            diverging_count=0,
            counterexample=None,
            reason=f"all {cardinality} member(s) of the declared domain compared equivalent",
            elapsed_s=elapsed,
        )


_BACKENDS: dict[str, Any] = {}


def register_backend(implementation: Any) -> None:
    _BACKENDS[implementation.name] = implementation


def unregister_backend(name: str) -> None:
    _BACKENDS.pop(name, None)


def backend(name: str) -> Any:
    try:
        return _BACKENDS[name]
    except KeyError:
        raise ProofError(f"no proof backend named {name!r}; available: {', '.join(backends())}") from None


def backends() -> list[str]:
    return sorted(_BACKENDS)


register_backend(ExhaustiveEnumeration())


def prove(
    finite: FiniteDomain,
    evaluate: Callable[[CorpusItem], Any],
    *,
    max_members: int,
    max_seconds: float | None = None,
    backend: str = "exhaustive-enumeration",
    clock: Callable[[], float] = time.monotonic,
) -> ProofResult:
    """Prove equivalence over ``finite`` with the named backend, or say why not."""

    implementation = globals()["backend"](backend)
    return implementation.prove(finite, evaluate, max_members=max_members, max_seconds=max_seconds, clock=clock)


def binding_problems(
    finite: FiniteDomain,
    digests: Sequence[str],
    lookup: Callable[[str], Mapping[str, Any] | None],
    *,
    source_id: str,
    target_id: str,
    label: str,
    max_members: int,
    frozen_record_digests: Mapping[str, str] | None = None,
) -> list[str]:
    """What keeps the evidence named for a proof over ``finite`` from binding every member to its own executions.

    Empty when, and only when, ``digests`` names one stored comparison record
    per member, each made from a source record and a target record that are
    observed executions of that member's exact input and initial state (their
    ``input_digest`` is the member's identity) by the declared systems, and
    compared without a mandatory divergence. With ``frozen_record_digests``
    (member id to the digest of its frozen baseline record) the source record
    must moreover be that frozen record: an execution of the member's input by
    the source system made after the freeze is bound to the input, not to the
    baseline the claim is stamped with, and a member the freeze never captured
    has nothing a proof can rest on. The same rule runs before a proof is
    recorded and when a package is inspected, the store and the package being
    the two lookups; a display id decides nothing here. The members are
    enumerated only when the evidence is at least as large as the domain, so a
    claim over a domain it cannot have enumerated is refused by its count, not
    by an allocation.
    """

    problems: list[str] = []
    counts: dict[str, int] = {}
    for digest in digests:
        counts[digest] = counts.get(digest, 0) + 1
    for digest, count in counts.items():
        if count > 1:
            problems.append(f"claim {label}: evidence {digest[:12]} is named {count} times")
    cardinality = finite.cardinality
    if cardinality > max_members:
        problems.append(f"claim {label}: the domain ({cardinality} members) exceeds the member budget ({max_members}); no proof can have enumerated it")
        return problems
    if len(digests) != cardinality:
        problems.append(f"claim {label}: names {len(digests)} evidence record(s) for a domain of {cardinality} member(s)")
        if len(digests) < cardinality:
            return problems
    identities = {member.identity(): member for member in finite.members()}
    covered: dict[str, str] = {}
    for digest in counts:
        record = lookup(digest)
        if record is None:
            problems.append(f"claim {label}: evidence {digest[:12]} is not in the evidence")
            continue
        if record.get("record_version") != COMPARISON_VERSION:
            problems.append(f"claim {label}: evidence {digest[:12]} is not a comparison record")
            continue
        if record.get("status") != "compared":
            problems.append(f"claim {label}: evidence {digest[:12]} is an incomplete comparison ({record.get('status')})")
            continue
        if not record.get("mandatory_equivalent"):
            problems.append(f"claim {label}: evidence {digest[:12]} records a mandatory divergence")
            continue
        sides: list[str] = []
        for role, system_id, key in (("source", source_id, "source_raw_digest"), ("target", target_id, "target_raw_digest")):
            raw = lookup(str(record.get(key, "")))
            if raw is None or raw.get("record_version") != OBSERVATION_VERSION:
                problems.append(f"claim {label}: evidence {digest[:12]}: its {role} record is not in the evidence")
                break
            if raw.get("system_id") != system_id:
                problems.append(f"claim {label}: evidence {digest[:12]}: its {role} record ran {raw.get('system_id')}, not {system_id}")
                break
            if raw.get("status") != "observed":
                problems.append(f"claim {label}: evidence {digest[:12]}: its {role} record did not observe ({raw.get('status')})")
                break
            identity = raw.get("input_digest")
            if not isinstance(identity, str) or not identity:
                problems.append(f"claim {label}: evidence {digest[:12]}: its {role} record does not name the input it ran on")
                break
            sides.append(identity)
        if len(sides) != 2:
            continue
        if sides[0] != sides[1]:
            problems.append(f"claim {label}: evidence {digest[:12]}: its source and target ran different inputs")
            continue
        member = identities.get(sides[0])
        if member is None:
            problems.append(f"claim {label}: evidence {digest[:12]} is an execution of an input outside the declared domain")
            continue
        if member.id in covered:
            problems.append(f"claim {label}: member {member.id} has two evidence records ({covered[member.id][:12]}, {digest[:12]})")
            continue
        covered[member.id] = digest
        if frozen_record_digests is not None:
            frozen = frozen_record_digests.get(member.id)
            if frozen is None:
                problems.append(f"claim {label}: member {member.id} has no frozen baseline record; a proof cannot rest on a source run made after the freeze")
            elif str(record.get("source_raw_digest", "")) != str(frozen):
                problems.append(f"claim {label}: evidence {digest[:12]}: its source record is not the frozen baseline record of member {member.id} (an execution outside the frozen baseline)")
    missing = [member.id for member in identities.values() if member.id not in covered]
    for member_id in missing[:5]:
        problems.append(f"claim {label}: member {member_id} has no comparison evidence")
    if len(missing) > 5:
        problems.append(f"claim {label}: {len(missing) - 5} more member(s) have no comparison evidence")
    return problems
