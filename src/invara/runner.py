"""Running the checks, and forming the verdict from what actually happened.

Nothing here decides anything on its own. Every judgement comes from a
contract that was sealed before the work was looked at, and every input is a
file digest or an exit code. There is no path by which a claim becomes a
verdict.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .contract import (
    BLOCK,
    HUMAN_REVIEW,
    PASS,
    UNVERIFIABLE,
    Predicate,
    VerificationContract,
    Verdict,
)

__all__ = [
    "Observation",
    "digest_paths",
    "judge",
    "observe",
    "resolve_program",
    "run_predicate",
]

#: A check that has not finished in this long is treated as unrunnable rather
#: than left to hang a gate forever. Generous: the full suite here takes ~200s.
DEFAULT_TIMEOUT_S = 900


def digest_paths(paths: Iterable[str], root: Path) -> dict[str, str]:
    """sha256 per path, for the paths that exist.

    A path that is absent is simply missing from the result, and
    :func:`~invara.contract.seal` refuses a constraint whose paths have no
    baseline. Absence is never recorded as a digest of nothing.
    """

    out: dict[str, str] = {}
    for name in paths:
        target = root / name
        if target.is_file():
            out[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    return out


@dataclass(frozen=True)
class Observation:
    """What one predicate did. Raw, before any judgement."""

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


def resolve_program(command: Sequence[str], root: Path) -> list[str]:
    """Make a repo-relative executable actually runnable.

    ``subprocess`` does not search ``cwd`` for the program — the standard
    library says so plainly: *"this directory is not considered when searching
    the executable, so you cannot specify the program's path relative to
    cwd"*. A contract that says ``.venv/Scripts/python.exe`` means this
    repository's interpreter, and without this it means nothing at all.

    Found by running the verifier on the task that built it. All three checks
    came back ``command not found`` and the verdict was ``UNVERIFIABLE`` —
    correct, and useless. A bare name like ``git`` is left alone for PATH.
    """

    if not command:
        return []
    program, *rest = command
    if "/" in program or "\\" in program:
        candidate = Path(program)
        if not candidate.is_absolute():
            local = (root / candidate).resolve()
            if local.exists():
                return [str(local), *rest]
    return list(command)


def run_predicate(
    predicate: Predicate, root: Path, timeout_s: int = DEFAULT_TIMEOUT_S
) -> Observation:
    """Run one check. A check that will not run is reported as not run.

    The distinction matters more than it looks. A command that is missing, or
    that times out, has told us nothing — and "nothing" must not arrive at the
    verdict wearing the same clothes as "failed" or "passed".
    """

    if predicate.human:
        return Observation(predicate.id, None, False, "defers to a person")
    try:
        completed = subprocess.run(  # noqa: S603 — operator-authored contract
            resolve_program(predicate.command, root),
            cwd=root,
            capture_output=True,
            text=True,
            # Decode as UTF-8 whatever the console codepage is. Without this
            # the reader thread dies on a Windows cp949 console the moment a
            # check prints anything non-ASCII — the first real PASS came with
            # a UnicodeDecodeError traceback attached and an empty detail
            # line. The verdict was still correct, because it rests on exit
            # codes, but a verifier that prints a stack trace while approving
            # is a verifier nobody believes.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as error:
        return Observation(predicate.id, None, False, f"command not found: {error}")
    except OSError as error:
        return Observation(predicate.id, None, False, f"could not run: {error}")
    except subprocess.TimeoutExpired:
        return Observation(
            predicate.id, None, False, f"timed out after {timeout_s}s"
        )
    tail = (completed.stdout or "").strip().splitlines()
    stderr = (completed.stderr or "").strip().splitlines()
    detail = " | ".join((tail[-1:] or [""]) + (stderr[-1:] or []))
    return Observation(predicate.id, completed.returncode, True, detail.strip())


def observe(
    contract: VerificationContract,
    root: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[dict[str, dict[str, str]], list[Observation]]:
    """Everything the verdict is allowed to look at.

    Returns the current digests of every protected path, and one observation
    per predicate. Separated from :func:`judge` so a dry run and a recorded
    run see identical inputs, and so the judgement can be re-derived from
    stored observations without running anything again.
    """

    current: dict[str, dict[str, str]] = {}
    for index, constraint in enumerate(contract.constraints):
        current[str(index)] = digest_paths(constraint.paths, root)
    observations = [
        run_predicate(predicate, root, timeout_s) for predicate in contract.done_when
    ]
    return current, observations


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
