"""Forming the verdict. Nothing in here touches the world.

This module exists because of where the line had to be drawn, not because
:func:`judge` needed a new home. ``ADR-0016`` seals one invariant — *core
verification semantics stay deterministic and pure; network, clock, storage,
billing, auth and execution adapters live outside core* — and cites this
function as evidence that the invariant was already true.

The function was. The module was not. ``judge`` sat in :mod:`invara.runner`
next to :func:`~invara.runner.run_predicate`, and that module imports
``subprocess`` and ``pathlib`` at the top. So a gate written the only way a
gate can be written — *the module holding seal and judge does not import
io, os, time, socket or subprocess* — came back red on the first run, before
anything was deliberately broken to test it.

That is not a contradiction of the ADR; it is the ADR's own invalidation
condition arriving, and it prescribes what to do: recover the separation
rather than add a feature. Purity that holds only per-function cannot be
checked without reading every function, and a rule nobody can check is a rule
that has already started drifting. At module granularity a test can hold it.

What this buys is the thing ``ADR-0016`` says the business model rests on::

    Local agent -> INVARA core -> same contract, same evidence, same verdict
                -> Local CLI | MCP | Plugin | Hosted API | CI | Agent runtime

Surfaces are swappable only while the core underneath them refuses to reach
for the world. :mod:`invara.runner` is the execution adapter and is expected
to be impure; this module and :mod:`invara.contract` are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .contract import (
    BLOCK,
    HUMAN_REVIEW,
    PASS,
    UNVERIFIABLE,
    VerificationContract,
    Verdict,
)

__all__ = ["Observation", "judge"]


@dataclass(frozen=True)
class Observation:
    """What one predicate did. Raw, before any judgement.

    Lives here rather than with the runner that produces it because it is the
    only shape :func:`judge` is allowed to read. Putting it beside the code
    that fills it would tie the pure half to the impure half for the sake of
    one dataclass.
    """

    predicate_id: str
    exit_code: int | None
    ran: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "exit_code": self.exit_code,
            "ran": self.ran,
            "detail": self.detail[:500],
        }


def judge(
    contract: VerificationContract,
    current: dict[str, dict[str, str]],
    observations: Sequence[Observation],
) -> Verdict:
    """Turn observations into a verdict, kill path first.

    Five exits, and each one names itself. ``decided_by`` is the name of the
    evidence field that carried the decision, so the ladder below is readable
    from the verdict alone instead of having to be reconstructed by a reader
    who would then own a second copy of it.

    Every input is already a digest or an exit code. This function does not
    read a file, run a command or look at a clock — which is what lets the
    same judgement be re-derived from stored observations years later and
    come out identical.
    """

    breaks: list[str] = []
    for index, constraint in enumerate(contract.constraints):
        now = current.get(str(index), {})
        for path, sealed_digest in constraint.baseline.items():
            actual = now.get(path)
            if actual is None:
                breaks.append(f"{path}: gone ({constraint.reason})")
            elif actual != sealed_digest:
                breaks.append(f"{path}: changed ({constraint.reason})")

    by_id = {observation.predicate_id: observation for observation in observations}
    failed: list[str] = []
    unrunnable: list[str] = []
    passed: list[str] = []
    needs_human: list[str] = []

    for predicate in contract.done_when:
        observation = by_id.get(predicate.id)
        if observation is None:
            unrunnable.append(f"{predicate.id}: never run")
            continue
        if predicate.human:
            needs_human.append(f"{predicate.id}: {predicate.reason or 'review'}")
            continue
        if not observation.ran:
            unrunnable.append(f"{predicate.id}: {observation.detail}")
        elif observation.exit_code != predicate.expect_exit:
            failed.append(
                f"{predicate.id}: exit {observation.exit_code} "
                f"(wanted {predicate.expect_exit}) {observation.detail}"[:200]
            )
        else:
            passed.append(predicate.id)

    common = dict(
        constraint_breaks=tuple(breaks),
        failed=tuple(failed),
        unrunnable=tuple(unrunnable),
        passed=tuple(passed),
        needs_human=tuple(needs_human),
    )

    # --- kill path first, and constraint breaks outrank everything. A run
    # that touched what it promised not to touch is not partially fine.
    if breaks:
        return Verdict(
            BLOCK,
            f"{len(breaks)} protected path(s) changed: " + "; ".join(breaks[:3]),
            decided_by="constraint_breaks",
            **common,
        )
    if failed:
        return Verdict(
            BLOCK,
            f"{len(failed)} completion check(s) failed: " + "; ".join(failed[:3]),
            decided_by="failed",
            **common,
        )
    if unrunnable:
        return Verdict(
            UNVERIFIABLE,
            f"{len(unrunnable)} check(s) could not be run, so the work is "
            "unverified rather than accepted: " + "; ".join(unrunnable[:3]),
            decided_by="unrunnable",
            **common,
        )
    if needs_human:
        return Verdict(
            HUMAN_REVIEW,
            f"machine checks passed; {len(needs_human)} item(s) need a person: "
            + "; ".join(needs_human[:3]),
            decided_by="needs_human",
            **common,
        )
    return Verdict(
        PASS,
        f"{len(passed)} check(s) passed and "
        f"{sum(len(c.paths) for c in contract.constraints)} protected path(s) "
        "are unchanged",
        decided_by="passed",
        **common,
    )
