"""The acceptance contract — sealed before the work is judged.

Written before the runner, deliberately, and the refusals were written before
the verdicts. A contract that cannot fail the work is not a contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = [
    "BLOCK",
    "HUMAN_REVIEW",
    "PASS",
    "TERMINAL",
    "UNVERIFIABLE",
    "Constraint",
    "NotVerifiable",
    "Predicate",
    "Verdict",
    "VerificationContract",
    "seal",
]

#: A constraint broke, or a completion condition was not met. The work is not
#: accepted.
BLOCK = "BLOCK"

#: A check could not be run at all — a missing command, a vanished path. The
#: work is **not** accepted. This is the transposition of the Divergence
#: Engine's ``starved``: unchecked is not the same as passed, and a verifier
#: that treats "I could not look" as "nothing wrong" is worse than none.
UNVERIFIABLE = "UNVERIFIABLE"

#: Everything machine-checkable passed and the contract declared something a
#: person has to look at.
HUMAN_REVIEW = "HUMAN_REVIEW"

#: All constraints intact, all completion conditions met.
PASS = "PASS"

#: Resolution order. The kill path comes first — a run that both broke a
#: constraint and failed to run a check is a BLOCK, not a shrug.
TERMINAL: tuple[str, ...] = (BLOCK, UNVERIFIABLE, HUMAN_REVIEW, PASS)


class NotVerifiable(ValueError):
    """The contract could not decide anything, so it is not sealed.

    ``reason`` is a stable slug so a run can report *why* a task was refused
    rather than only that it was.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Constraint:
    """Something that has to still be true afterwards.

    One kind for now — ``paths_unchanged`` — because it is the one this
    repository already checks by hand every session: the 2026-08-27
    resolution path must come out of every piece of work byte-identical.
    Sealing the digests turns that habit into a contract.
    """

    kind: str
    paths: tuple[str, ...]
    reason: str
    #: sha256 per path, taken at seal. The "what it looked like before".
    baseline: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "paths": list(self.paths),
            "reason": self.reason,
            "baseline": dict(self.baseline),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Constraint":
        return cls(
            kind=data["kind"],
            paths=tuple(data["paths"]),
            reason=data.get("reason", ""),
            baseline=dict(data.get("baseline", {})),
        )


@dataclass(frozen=True)
class Predicate:
    """One checkable statement about the finished work.

    ``command`` is the whole of it. There is deliberately no field for a
    description of what was done — a predicate is a thing that runs and
    yields an exit code, or it is not a predicate.
    """

    id: str
    command: tuple[str, ...]
    expect_exit: int = 0
    reason: str = ""
    #: When true, a machine pass is not enough and the verdict becomes
    #: HUMAN_REVIEW. For the checks that genuinely need eyes.
    human: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": list(self.command),
            "expect_exit": int(self.expect_exit),
            "reason": self.reason,
            "human": bool(self.human),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Predicate":
        return cls(
            id=data["id"],
            command=tuple(data["command"]),
            expect_exit=int(data.get("expect_exit", 0)),
            reason=data.get("reason", ""),
            human=bool(data.get("human", False)),
        )


@dataclass(frozen=True)
class VerificationContract:
    task_id: str
    intent: str
    constraints: tuple[Constraint, ...]
    done_when: tuple[Predicate, ...]
    sealed_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "intent": self.intent,
            "constraints": [c.as_dict() for c in self.constraints],
            "done_when": [p.as_dict() for p in self.done_when],
            "sealed_at": float(self.sealed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationContract":
        return cls(
            task_id=data["task_id"],
            intent=data["intent"],
            constraints=tuple(
                Constraint.from_dict(c) for c in data["constraints"]
            ),
            done_when=tuple(Predicate.from_dict(p) for p in data["done_when"]),
            sealed_at=float(data["sealed_at"]),
        )

    def falsifier(self) -> str:
        """What makes this task fail. Plain sentence, for a human reading a log."""

        protected = sum(len(c.paths) for c in self.constraints)
        return (
            f"{protected} protected path(s) change, or any of "
            f"{len(self.done_when)} completion check(s) does not return its "
            "expected exit code. A check that cannot be run at all is not a "
            "pass either."
        )


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    constraint_breaks: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    unrunnable: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()
    needs_human: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "constraint_breaks": list(self.constraint_breaks),
            "failed": list(self.failed),
            "unrunnable": list(self.unrunnable),
            "passed": list(self.passed),
            "needs_human": list(self.needs_human),
        }


KNOWN_CONSTRAINT_KINDS = frozenset({"paths_unchanged"})


def seal(
    *,
    task_id: str,
    intent: str,
    constraints: Sequence[Constraint],
    done_when: Sequence[Predicate],
    sealed_at: float,
) -> VerificationContract:
    """Build the contract, or refuse the task.

    Every branch is the same sentence: if this could not fail the work, it is
    not written down as if it could.
    """

    if not task_id.strip():
        raise NotVerifiable("no_task_id")
    if not intent.strip():
        raise NotVerifiable("no_intent", "a task with no stated intent")

    if not done_when:
        raise NotVerifiable(
            "no_done_condition",
            "nothing was named that would show the work happened",
        )
    if not constraints:
        raise NotVerifiable(
            "no_constraint",
            "nothing was named that must survive the work; a task allowed to "
            "change anything cannot be said to have respected anything",
        )

    seen: set[str] = set()
    for predicate in done_when:
        if not predicate.id.strip():
            raise NotVerifiable("unnamed_predicate")
        if predicate.id in seen:
            raise NotVerifiable("duplicate_predicate", predicate.id)
        seen.add(predicate.id)
        if not predicate.command:
            raise NotVerifiable(
                "unverifiable_predicate",
                f"{predicate.id} states a condition with no command to check it",
            )

    if all(predicate.human for predicate in done_when):
        raise NotVerifiable(
            "self_reporting_only",
            "every completion condition defers to a person; nothing here is "
            "independently checkable",
        )

    for constraint in constraints:
        if constraint.kind not in KNOWN_CONSTRAINT_KINDS:
            raise NotVerifiable("unknown_constraint_kind", constraint.kind)
        if not constraint.paths:
            raise NotVerifiable("empty_constraint", constraint.kind)
        missing = [p for p in constraint.paths if p not in constraint.baseline]
        if missing:
            raise NotVerifiable(
                "unreadable_constraint_path",
                "no baseline digest for " + ", ".join(sorted(missing)[:3]),
            )

    return VerificationContract(
        task_id=task_id.strip(),
        intent=intent.strip(),
        constraints=tuple(constraints),
        done_when=tuple(done_when),
        sealed_at=float(sealed_at),
    )
