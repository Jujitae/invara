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
