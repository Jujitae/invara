# INVARA Transformation Assurance — architecture and trust boundaries

Status: Gen 1, introduced in INVARA 0.2.0. The existing four verdicts and
six MCP tools keep their previous meanings; this subsystem adds the
`invara assure` and `invara repair` command groups and their plugin flows.

Reference documents in this directory: `MANIFEST.md`, `NORMALIZATION.md`,
`ADAPTERS.md`, `CLAIMS.md`, `COUNTEREXAMPLES.md`, `REPAIR_SESSION.md`,
`THREAT_MODEL.md`, `CLI.md`, `DOMAINS.md`, and `samples/`.

## 1. What this subsystem answers

A person who cannot read the source language asks:

> Inspect this ugly but working repository. Clean up its engineering
> structure without changing what the product does.

Some transformer — Claude Code, Codex, OpenRewrite, an internal tool, a human
— changes the code. INVARA does not trust the transformer. It independently
establishes, from observable evidence only:

- what behaviour was captured before the transformation (the **baseline**),
- under which explicit definition of "the same" it is compared (the
  **Equivalence Manifest**),
- what stayed the same, what diverged, and what could not be verified,
- whether each individual repair may be kept or must be rejected,
- what structural change actually occurred, measured rather than claimed,
- what uncertainty remains.

The same core serves vibe-coded repository cleanup, large-scale
refactoring assurance, legacy migration assurance, human-written
transformations, and transformations produced by any tool.

## 2. Trust architecture

```
HOST AGENT OR HUMAN (untrusted, replaceable)
    inspects, proposes probes and policies, plans and performs repairs,
    proposes counterexamples
              |
              v
INVARA assurance core (deterministic, local, no model, no outbound network)
    captures -> normalizes -> compares -> searches -> proves -> reports
              |
              v
   claim results  ->  final verdict  PASS | BLOCK | UNVERIFIABLE | HUMAN_REVIEW
```

| An agent may                              | An agent may not                                                          |
|-------------------------------------------|---------------------------------------------------------------------------|
| propose observation probes                | declare its own repair successful (there is no argument that carries it)  |
| propose normalization policies            | silently remove a failed check or a mandatory probe (not amendable)       |
| create characterization inputs            | silently weaken the equivalence definition (amendments are records)       |
| plan and perform repair units             | convert UNVERIFIABLE into PASS (the ladder forbids it)                    |
| propose counterexamples                   | add an ignore rule merely to remove a divergence (post-divergence pin)    |
|                                           | modify captured baseline evidence after seeing the target (integrity)     |

The implementation tool is not a runtime dependency. The runtime imports
only the Python standard library; `pyproject.toml` declares
`dependencies = []`.

The existing verification kernel — `seal`, `judge`, `list`, `log`, `chain`,
`replay`, the four verdicts and the six MCP tools — keeps the same public
contract. The subsystem is reached through the `invara assure` and `invara repair`
command groups and the `/invara:assure` and `/invara:repair` plugin
commands. The kernel can gate on it the way it gates on anything: a sealed
contract's `done_when` may run `invara assure verify <session> --require PASS`.

## 3. Package layout

```
src/invara/assurance/
    records.py      record version constants                                   (pure)
    paths.py        observation path selectors                                 (pure)
    manifest.py     Equivalence Manifest: validation, digest, amendments       (pure)
    normalize.py    fifteen policy kinds, symmetric and audited                (pure)
    compare.py      differential comparison, located divergences              (pure)
    claims.py       six claim statuses, the final-verdict ladder               (pure)
    session.py      thirteen-state machine as an event reducer                 (pure)
    report.py       non-coder summary + technical assurance report            (pure)
    redaction.py    secret redaction                                           (pure)
    evidence.py     content-addressed, hash-chained tables in verify.db
    execute.py      one run: process/service, filesystem, JSON, SQLite, HTTP
    engine.py       characterize, freeze, integrity, corpus compare, differential
    search.py       seeded counterexample search and shrinking
    proof.py        exhaustive finite-domain proof behind a backend protocol
    analysis.py     reproducible structural metrics, declared findings
    governor.py     git-worktree-isolated repair lifecycle
    workflow.py     the steps of a session, each recorded before the next
    cli.py          `invara assure` / `invara repair`
```

"Pure" has the meaning the kernel's suite gives it: the module imports none
of the execution, filesystem, clock, network or randomness roots;
`tests/assurance/test_purity.py` holds the nine pure modules to that list
the way `TheCoreIsPure` holds `contract` and `verdict`. The plugin bundle
at `plugin/src/invara/` is a byte-identical copy of the whole tree,
maintained by `scripts/sync_plugin_bundle.py` and held by a recursive
drift test.

## 4. Data flow of one differential run

```
INPUT (corpus item: id, input, initial_state)
   |
   +--> SOURCE system  --run--> RAW SOURCE record  --policies--> NORMALIZED SOURCE
   |                                                                    |
   +--> TARGET system  --run--> RAW TARGET record  --policies--> NORMALIZED TARGET
                                                                        |
                                                                 COMPARISON RECORD
                                                                 (divergences, tolerances
                                                                  applied, actions, digests)
```

Every box is a versioned, content-addressed JSON record in the evidence
store. Raw records are never mutated; the normalized value is a separate
record naming the raw digest and the policy-set digest; the comparison
names both raw digests, both normalized digests and the policy-set
digest. A divergence names the observation path, the raw path on each side,
the raw and normalized values on each side, the policy that governed the
path, why the values differ, whether it is mandatory, and which exclusion
downgraded it if any.

## 5. Equivalence Manifest

Schema `invara.assurance.manifest/1` (`MANIFEST.md`). Content-addressed by
the SHA-256 of its canonical JSON; every claim result carries the digest it
was computed under. After the baseline is frozen, change is only by
amendment: an explicit record naming who asked, why, the old and new
digests, the policies added or changed, the claim results it invalidated
(those recorded before it, by event order; a later result under a digest
the session returns to is not dead), and whether any added policy covers a
path where a divergence had already
been recorded — that flag holds the session's verdict at `HUMAN_REVIEW`
for good. The stability claim is re-derived from the stored capture under
the new policies; nothing historical is rewritten.

## 6. Normalization and comparison

Exact by default (`int` and `float` included). Fifteen policy kinds
(`NORMALIZATION.md`), each declared with a selector and a reason, applied
to both sides, each match an action in the audit log. Structural refusals
at validation (root or blanket ignore, blanket erasing policies, root,
blanket or whole-probe exclusions, unstable indexes under unordered lists,
unbounded or overbroad tolerances, an inferred policy that claims
acceptance, a manifest with no mandatory comparing claim), at freeze and
again at every amendment (a policy set that leaves a mandatory probe
nothing to compare, an absolute tolerance above every baseline value).
Coverage of a volatile path is decided by re-normalizing the runs.
Generated-identity mapping is relationship-preserving. Two built-in
normalizations, logged like any other: reserved-text escaping, so a
program cannot print a placeholder into equivalence, and canonicalization
of the two directories INVARA itself chose — the per-run workspace and
the resolved system root.

## 7. Observation engine

`ADAPTERS.md`. Process/service execution under an environment allowlist
plus imposed values, in its own process group, a fresh workspace per run,
bounded capture with full digests, deadlines on stdin, readiness and the
run, the whole process tree stopped on timeout, the executable identified
(the Windows Store alias is `unrunnable`); probes for the process, JSON
output, the filesystem (symlinks and junctions recorded not followed,
roots contained, entries bounded), SQLite (WAL-safe snapshot copy read in
one transaction, declared ordering, rows bounded, seeded SQL under an
authorizer) and loopback HTTP (any other host or port is outside the
boundary and makes the run `UNVERIFIABLE`). Every record says which
dimensions were controlled, which were inherited by name, which are inert
on this platform, and which cannot be controlled. None of it is an
operating-system sandbox, and nothing claims to be.

## 8. Determinism control and stability

Controlled: environment, hash seed, timezone, locale, encoding, temporary
paths, filesystem and row ordering, no retries. Reported as uncontrollable:
the wall clock, thread and process scheduling, hardware randomness,
external services. A stability assessment (`characterize --runs N`)
reports every path that varies between runs of the unchanged source and
proposes a policy kind per path; proposals are inferred and unaccepted
until an amendment names them. A `baseline_stability` claim turns that
assessment into a claim: `NO_DIVERGENCE_FOUND` when every volatile
dimension is covered, `HUMAN_REVIEW` when one is not, `UNVERIFIABLE` with
fewer than two runs.

## 9. Counterexample search and finite-domain proof

`COUNTEREXAMPLES.md`. A seeded, sequential, reproducible search — seeds,
then boundary tiers interleaved across seeds, then a fixed mutation
catalogue — with a greedy reproducing shrinker; the result is
`NO_DIVERGENCE_FOUND` or `DIVERGED` with both the original and the
minimized input, never a proof. An exhaustive enumeration backend proves
equivalence over a declared finite domain or says `UNVERIFIABLE`; the
backend protocol is versioned so formal backends can attach without
changing the claim model.

## 10. Repair session and governor

`REPAIR_SESSION.md`. Thirteen states as a transition table, reduced from
an append-only event history; invalid moves and lying histories fail
closed. The governor requires a clean identified tree, keeps the accepted
state in the session's own ref, captures the baseline in a detached
worktree at the base commit, gives every unit a disposable detached
worktree at the accepted commit, verifies the unit's exact tree, accepts
only that tree (compare-and-swap on the ref, stale trees refused), keeps a
rejected unit's patch and removes only worktrees it created and can
recognise. It never runs `git clean`, a hard reset or a stash, and never
touches the user's checkout.

## 11. Claims and the final verdict

`CLAIMS.md`. Six claim statuses over five claim kinds. The verdict ladder,
kill path first: integrity → constraint breaks → mandatory divergence →
never evaluated → unverifiable → needs a person → `PASS` (decided by
`preserved`, or `proved` when every mandatory claim is a proof). An assure
session's verdict rests on the latest results under the current manifest;
a repair session's on the last accepted unit's results plus the stability
claim.

## 11b. Coverage map, blind-spot scan, evidence package

`coverage.py` (pure) turns the evidence into one state per observed
value and rolls it up (`CLAIMS.md`); `sensitivity.py` (pure) measures the
equivalence definition by mutating the frozen baseline under the manifest
in force and reporting the values a change would not have flagged;
`package.py` exports a session as one content-addressed zip and inspects
such a zip with nothing but its contents, recomputing addresses, replaying
the events, re-running every comparison and re-deriving the verdict.
`engine.preflight` estimates a phase's executions before the first one
starts. None of these touches a verdict; all of them make one legible or
checkable.

## 12. Reports

`report.py`. Two layers in one JSON document, redacted and
content-addressed, rendered as Markdown: the three questions a non-coder asks (전이랑 같아?
이상한 점 있어? 모르면 솔직히 말해!) answered first, then six
plain-language sections in Korean and English (기능 유지 / 성능 유지 / 정리 완료 항목 / 되돌린
변경 / 확인하지 못한 영역 / 다음에 사람이 볼 것 — the fifth is mandatory and
never empty by omission), then manifests and digests, the baseline
envelope, policies, coverage by kind of knowledge, every divergence and
counterexample, accepted and rejected units with measured deltas,
provenance, amendments and limitations. `samples/` holds reports generated
from the fixtures.

## 13. Security boundary

`THREAT_MODEL.md`: no shell, environment allowlists, timeouts that stop
the whole process tree, bounded capture and records, traversal, symlink
and junction refusal, whole-record secret redaction with fingerprinted
tokens (redaction is presentation, never equivalence; no plaintext secret
in evidence), loopback-only HTTP, missing or aliased executable →
`UNVERIFIABLE`, malformed adapter output, corrupted store and internal
errors → fail closed, digest mismatch → `BLOCK`, normalization that
removes all signal → refusal, post-divergence weakening → never `PASS`,
no argument by which a repairer declares its own `PASS`, a verified tree
that is the accepted tree, and a stated execution boundary that claims no
sandbox. The regression suite holds the failure cases that shaped these
boundaries.
