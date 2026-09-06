"""Independent verification of work an agent claims to have done.

The question, and only this question:

    Given a task, the constraints it had to respect, and the state of the
    repository afterwards, did the work actually happen?

The shape comes from a sibling engine in the repository this was built in,
which tests claims about the world. That engine seals a contract before the
evidence exists, refuses to create anything it cannot kill, scores only
observed records, blocks self-reference at construction, and chains the
result. Change "world claim" to "agent's work" and the same machine applies:

===========================  =========================================
sealed anchor                sealed acceptance contract
base rate (counter evidence) the digests of the protected paths at seal
trial                        running the checks
observed evidence only       exit codes and file bytes
``seal()`` refuses           a task with no checkable done-condition
``starved``                  ``UNVERIFIABLE`` — unchecked is not passed
hash chain                   tamper-evident verdict history
===========================  =========================================

**The agent's own report is not an input.** There is no field in a contract
where anything can assert that the work is finished; the verdict is computed
from command exit codes and file digests. That is structural rather than
policed, which is the only way it stays true: the cheapest way to obey
"never treat an agent's self-report as evidence" is to leave out the field
that would carry it.

**The kill path was written first.** ``BLOCK`` and ``UNVERIFIABLE`` work
before ``PASS`` does, for the same reason the Divergence Engine's killer was
built before its generator: a verifier that can only approve is a rubber
stamp, and it is easier to notice a missing approval than a false one.

**Boundary.** Verdicts live in ``.runtime/verify.db`` and nowhere else. This
package writes no other store, and it keeps its own entry point so that a
syntax error here cannot take down an unrelated command in the same install.
"""

from __future__ import annotations

__version__ = "0.2.0"

import sys as _sys

#: The runtime floor is a product decision (ADR-0018): Python 3.12+, nothing
#: below, nothing else required. pip enforces ``requires-python`` at install
#: time, but the plugin ships this package as bundled source on ``PYTHONPATH``
#: and pip is never consulted -- so an old interpreter walks straight in and
#: dies mid-import with a traceback about dataclasses or ``datetime.UTC``,
#: which reads as INVARA being broken. INV-011 is about the failure before
#: this one (no Python at all, which no line of ours can catch), but this is
#: the nearest failure a line of ours *can* catch, so it speaks.
_RUNTIME_FLOOR = (3, 12)


def _version_refusal(version_info) -> str | None:
    """The message an interpreter below the floor must be told, or ``None``.

    Kept as a function of its argument so a test can hold the message to its
    three obligations -- name what is wrong, say how to fix it, and say whose
    fault it is not -- without needing an old interpreter to run under.
    Written without f-strings on purpose: this line must still *parse* on the
    interpreters it exists to refuse (3.7+ for the ``__future__`` import
    above; anything older fails at the parser, which we cannot reach).
    """

    if tuple(version_info[:2]) >= _RUNTIME_FLOOR:
        return None
    found = "%d.%d" % (version_info[0], version_info[1])
    return (
        "INVARA requires Python 3.12 or newer; this interpreter is Python "
        + found
        + ".\nINVARA 는 Python 3.12 이상이 필요합니다. 지금 인터프리터는 Python "
        + found
        + " 입니다.\n"
        "Install / 설치: https://www.python.org/downloads/"
        "  (Windows: winget install Python.Python.3.12)\n"
        "This is the environment, not an INVARA defect."
        " / INVARA 고장이 아니라 실행 환경 문제입니다."
    )


_refusal = _version_refusal(_sys.version_info)
if _refusal is not None:
    # Both channels on purpose. stderr is what an MCP client shows a person
    # when the server dies; ImportError is what an importing program can
    # catch. Raising alone buries the message under a traceback, printing
    # alone exits with a lie of an ImportError about some later module.
    print(_refusal, file=_sys.stderr)
    raise ImportError(_refusal)
del _refusal

from .contract import (
    BLOCK,
    HUMAN_REVIEW,
    PASS,
    UNVERIFIABLE,
    Constraint,
    NotVerifiable,
    Predicate,
    VerificationContract,
    Verdict,
    seal,
)
from .runner import digest_paths, observe
from .verdict import Observation, judge

__all__ = [
    "BLOCK",
    "HUMAN_REVIEW",
    "PASS",
    "UNVERIFIABLE",
    "Constraint",
    "NotVerifiable",
    "Observation",
    "Predicate",
    "Verdict",
    "VerificationContract",
    "digest_paths",
    "judge",
    "observe",
    "seal",
]
