# Repair session lifecycle

A repair session governs a transformation the host agent performs one unit
at a time. Its state is never held only in memory: every step is an event
in the evidence store and the current state is what the events reduce to.
Implementation: `src/invara/assurance/session.py` (the pure state machine),
`governor.py` (git), `workflow.py` (the steps); tests:
`tests/assurance/test_session.py`, `test_governor.py`, `test_workflow.py`.

## States

```
CREATED -> BASELINE_CAPTURING -> BASELINE_FROZEN -> ANALYZING -> PLANNED
                                                                   |
        +----------------------------------------------------------+
        v
   UNIT_PREPARING -> UNIT_IN_PROGRESS -> UNIT_VERIFYING -> UNIT_ACCEPTED -> PLANNED | COMPLETED
                           |                  |
                           |                  +--> UNIT_IN_PROGRESS   (keep editing)
                           v                  v
                        UNIT_REJECTED <-------+
                           |
                           v
                     UNIT_ROLLED_BACK -> PLANNED | COMPLETED

any non-terminal state -> BLOCKED
```

The table in `session.TRANSITIONS` is the authority. Points that carry the
guarantees: a unit can be accepted from `UNIT_VERIFYING` only; a rejected
unit can only be rolled back; planning needs an analysis and a frozen
baseline first; `COMPLETED` and `BLOCKED` accept nothing but the trace of
an export, which moves nothing. A stored history
that claims an impossible move, or lies about the state it leaves, is a
`SessionCorrupt` and nothing is inferred from it.

## Events

| event | to | payload |
|---|---|---|
| `created` | CREATED | kind, manifest digest, roots, repository, base commit, workspace |
| `baseline_capture` | BASELINE_CAPTURING | the capture (inputs, runs, raw digests, volatile paths, proposals, uncovered) |
| `claim_recorded` | (same) | a claim result (stability at capture time; corpus/search/proof in an assure session) |
| `baseline_frozen` | BASELINE_FROZEN | the freeze (baseline digest, record digests, policy-set digest) |
| `analysis_recorded` | ANALYZING | measured metrics of the baseline tree, declared findings |
| `plan_recorded` | PLANNED | the units |
| `unit_started` / `unit_in_progress` | UNIT_PREPARING / UNIT_IN_PROGRESS | unit id, worktree, the accepted commit it starts from |
| `unit_verifying` | UNIT_VERIFYING | unit id, tree hash, changed paths |
| `unit_verified` | (same) | verdict, claim results, metrics after, metrics delta |
| `unit_accepted` | UNIT_ACCEPTED | commit, tree, reviewer if any |
| `unit_rejected` / `unit_rolled_back` | UNIT_REJECTED / UNIT_ROLLED_BACK | reason, patch path and digest, changed paths / removed worktree |
| `continued` | PLANNED | — |
| `manifest_amended` | (same) | the amendment record, invalidated claims (each with `before`, the number of claim results recorded so far: only those are dead), post-divergence notes |
| `resumed` | (same) | checks performed |
| `exported` | (same; the one event the terminal states accept, and it moves nothing) | package version, the verdict at export, the store-wide problems it counted, the report digest |
| `blocked` | BLOCKED | reason |
| `completed` | COMPLETED | final verdict, report digest, branch |

## What a session records

Session: id, kind, manifest digest and history, repository, base commit,
accepted commit, workspace, roots, captures, the freeze and baseline
digest, metrics before, findings, the plan, every unit, accepted and
rejected units, rollbacks, every claim result and which were invalidated,
amendments, post-divergence notes, the blocked reason, the final verdict
and report digest.

Unit: id, objective, reason, risk, owned paths, expected behaviour impact,
verification notes; then as it proceeds: worktree, base commit, tree hash,
changed paths, verdict, claim results, metrics after and delta, commit,
reviewer, rejection reason, patch path and digest.

## The git layout

```
<repo>                              the user's checkout: never touched
refs/invara/repair/<sid>/accepted   the accepted state; starts at the base commit
<workspace>/<sid>/baseline          detached worktree at the base commit (the SOURCE root)
<workspace>/<sid>/baseline.invara.json
<workspace>/<sid>/units/<uid>       detached worktree at the accepted commit (the TARGET root)
<workspace>/<sid>/units/<uid>.invara.json
<workspace>/<sid>/evidence/<sid>-<uid>.patch   a rejected unit's diff
refs/heads/invara/repair/<sid>      created by finish, at the accepted commit
```

`<workspace>` defaults to a sibling of the repository named
`<repo>.invara-repair`. Every worktree the governor creates has a marker
beside it naming the repository, the path, the session, the unit and the
commit it started from; a directory without a matching marker, or one git
does not list as a worktree, is never removed.

## Acceptance and rejection

`unit-verify` checks that the unit worktree is where the session recorded
it, that the baseline worktree is still pristine at the base commit,
stages everything in the unit worktree, writes the tree object, lists the
paths changed against the accepted commit the session recorded (never
against the marker file), checks them against the unit's `owned_paths`,
runs every declared claim with the unit worktree as the target root,
measures the tree, then reads the tree and the baseline again: a tree
that changed while it was being verified, or a baseline that was touched,
is an integrity problem and the verdict is `BLOCK`. The verdict is
recorded for that exact tree hash as `verified_tree` under the manifest
digest in force; a new `unit_verifying` event voids it, and so does an
amendment of the manifest while the unit is open.

`unit-accept` reads that verdict. It refuses unless the verdict is `PASS`
(or `HUMAN_REVIEW` with a named reviewer, which the report labels as
attested by that person), refuses if the tree hash now differs from the
verified one (`stale_verification` / `unit_not_verified`), refuses if the
accepted ref moved since the unit started (`accepted_moved`), then creates
a commit from the verified tree with the accepted commit as parent,
advances the ref with a compare-and-swap, and removes the unit worktree.

`unit-reject` writes a binary-safe `git diff <base> <tree>` (bytes as
they are, renames as a deletion and an addition) to the evidence
directory, records its digest and the changed paths, anchors the rejected
tree under `refs/invara/repair/<session>/rejected/<unit>` so it stays
reachable, records the rejection, and only then removes the worktree: an
interruption between the two leaves a state `resume` completes, and a
repeated rejection of the same tree reuses the patch and the anchor. The
accepted ref is not read, let alone written.

`finish` publishes the accepted state as a branch only when the accepted
ref is at the commit the session recorded (`accepted_moved` otherwise),
validates the branch name, and never moves a branch that already exists.

## Resume

`resume` re-reads the history, then asks the governor: does the accepted
ref exist and match the recorded accepted commit; if a unit is open, is
its worktree present and recognised. Three interruptions are completed
rather than reported: a unit started but not yet marked in progress
(`UNIT_PREPARING`) whose worktree is in place moves to `UNIT_IN_PROGRESS`;
a rejected unit whose rollback did not finish (`UNIT_REJECTED`) has its
worktree removed if it is still there and moves to `UNIT_ROLLED_BACK`;
and an acceptance that crashed after the ref moved but before the event
was written is reconciled only when the unit's recorded verdict is `PASS`
on its current tree and the ref is exactly one non-merge commit ahead
holding exactly that tree. Any other discrepancy (a vanished worktree, a
ref that moved to something unverified, a ref that moved behind a unit
that verified `BLOCK`) blocks the session with the reason; nothing is
recreated or guessed. When git itself cannot be started the discrepancy
is reported as `transient`, nothing is recorded, and the session stays
where it was.

A worktree the governor recognises has no symlink or junction in its
path, lies inside the session workspace and outside the checkout, carries
a marker naming this repository and path, and is in git's worktree list.
A registered worktree that lost its marker is adopted, not deleted; a
marker without a worktree is cleared; a directory that is neither is a
`worktree_collision` and is left alone. Every fresh checkout must write
back to its commit's tree (`checkout_not_faithful` otherwise), so a unit
verifies the repository's bytes and not a platform's line endings.

## Invariants held by tests

- a dirty tree is refused and nothing is created;
- the user's branch and HEAD are unchanged after every operation;
- acceptance promotes the exact verified tree, and only that;
- a rejected unit's changes never reach the accepted state or a later unit;
- the accepted tree hash is byte-identical before and after a rejection;
- the second unit starts from the first accepted commit;
- an unrecognised directory, and a worktree the user created, are never
  removed;
- a symlinked path, a path outside the session workspace, and the user's
  checkout are never recognised as a unit, marker or not;
- a rewritten marker cannot move the ownership base;
- a changed path is recorded as it is named (a Korean filename matches
  its glob), and a move out of ownership is a deletion the check sees;
- ownership globs are segment-aware: `*` stays within one path segment,
  `**` crosses segments;
- a unit or session id is always a valid ref component;
- a unit whose verdict was not `PASS` is never reconciled into acceptance;
- a tree that changed during verification is not accepted;
- `finish` leaves no branch behind when the ref moved;
- INVARA's own `.invara/` and `.runtime/` never count as a dirty tree;
- no command in the governor is `git clean`, a hard reset, or a shell.
