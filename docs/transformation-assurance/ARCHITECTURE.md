# INVARA Transformation Assurance — architecture and trust boundaries

Status: implementation candidate on branch
`agent/invara-transformation-assurance-20260903`, built from base
`38e6a385d68f98c023a92d4f81c63304dd29c984`. Nothing in this directory is
public 0.1.3 functionality. The public package and its six MCP tools are
unchanged by this branch.

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

The same core serves five uses: vibe-coded repository cleanup, large-scale
refactoring assurance, legacy migration assurance, human-written
transformations, and transformations produced by any tool.

## 2. Trust architecture

```
HOST AGENT OR HUMAN (untrusted, replaceable)
    inspects, proposes probes and policies, plans and performs repairs,
    proposes counterexamples
              |
              v
INVARA assurance core (deterministic, local, no model, no network)
    captures -> normalizes -> compares -> searches -> proves -> reports
              |
              v
   claim results  ->  final verdict  PASS | BLOCK | UNVERIFIABLE | HUMAN_REVIEW
```

The rules that make the boundary real, each held by a test:

| An agent may                              | An agent may not                                             |
|-------------------------------------------|--------------------------------------------------------------|
| propose observation probes                | declare its own repair successful (there is no field for it) |
| propose normalization policies            | silently remove a failed check or a mandatory probe          |
| create characterization inputs            | silently weaken the equivalence definition                   |
| plan and perform repair units             | convert UNVERIFIABLE into PASS                               |
| propose counterexamples                   | add an ignore rule merely to remove a divergence             |
|                                           | modify captured baseline evidence after seeing the target    |

The implementation model of this branch (Claude Fable 5.1) is not a runtime
dependency. The runtime imports only the Python standard library.

The existing verification kernel — `seal`, `judge`, `list`, `log`, `chain`,
`replay`, the four verdicts and the six MCP tools — is untouched. The new
subsystem lives in `invara.assurance` and is reached through new CLI command
groups (`invara assure`, `invara repair`) and two plugin commands. It can be
gated by the old kernel the way anything else is: a sealed contract's
`done_when` may run `invara assure verify <session> --require PASS`, which
exits non-zero unless the session's final verdict is `PASS`.

## 3. Package layout

```
src/invara/assurance/
    __init__.py     schema version constants and the public re-exports
    paths.py        observation path selectors (pure)
    manifest.py     Equivalence Manifest model, validation, digest, amendments (pure)
    normalize.py    semantic normalization policies, symmetric and audited (pure)
    compare.py      differential comparison and divergence records (pure)
    claims.py       assurance claim model and the final-verdict ladder (pure)
    session.py      repair/assurance session state machine as an event reducer (pure)
    report.py       non-coder summary and technical assurance report (pure)
    redaction.py    secret redaction for evidence and reports (pure)
    evidence.py     content-addressed, hash-chained SQLite evidence store
    adapters.py     process / service, filesystem, JSON, SQLite, local HTTP observation
    execute.py      one run of one input against one system, under controlled environment
    engine.py       characterize, freeze, stability, compare over a corpus
    search.py       deterministic counterexample search and shrinking
    proof.py        finite-domain exhaustive proof and the proof-backend protocol
    analysis.py     reproducible structural metrics and recorded findings
    governor.py     git-worktree-isolated atomic repair lifecycle
    cli.py          `invara assure` and `invara repair` command groups
```

"Pure" has the meaning the existing suite gives it: the module imports none
of the execution, filesystem, clock, network or randomness roots, and a test
holds that list against `paths`, `manifest`, `normalize`, `compare`,
`claims`, `session`, `report` and `redaction`.

The plugin bundle at `plugin/src/invara/` carries a byte-identical copy of the
whole package tree, including the subpackage; a recursive drift test holds
it.

## 4. Data flow of one differential run

```
INPUT (corpus item: id, input, initial_state)
   |
   +--> SOURCE system  --run--> RAW SOURCE observation  --policies--> NORMALIZED SOURCE
   |                                                                       |
   +--> TARGET system  --run--> RAW TARGET observation  --policies--> NORMALIZED TARGET
                                                                           |
                                                                    COMPARISON RESULT
                                                                    DIVERGENCE DETAILS
                                                                    PROVENANCE
```

Every box is a versioned, content-addressed JSON record. Raw observations are
never mutated; normalized observations are separate records that reference
the raw digest and the policy-set digest they were derived under. A
divergence names the observation path, raw and normalized values on both
sides, the policy that governed the comparison at that path, why the values
differ, and whether it is mandatory or informational.

## 5. Equivalence Manifest

Schema `invara.assurance.manifest/1`. It declares: `session_id`,
`source_system`, `target_system`, `provenance`, `input_domain`, `probes`,
`policies`, `claims`, `budgets`, `timeouts`, `performance`, `exclusions`,
`human_review`. The full field reference is in `MANIFEST.md`.

The manifest is content-addressed: its digest is the SHA-256 of its canonical
JSON. A session records the digest it runs under. Once the baseline is frozen
the manifest and the accepted envelope are immutable. Changing anything is an
**amendment**: an explicit record naming who or what requested it, why, the
old and the new digest, and the claim results it invalidates. An amendment
that adds or widens a policy over a path where a divergence was already
recorded is flagged `post_divergence`; that flag pins the session's final
verdict to at most `HUMAN_REVIEW`, so a divergence cannot be laundered into
a `PASS` by editing the definition of equivalence after the fact. Historical
evidence is never rewritten.

## 6. Normalization and comparison

Default comparison is exact. Every non-exact rule is a policy in the
manifest with an `id`, a `kind`, a path selector, parameters and a reason, and
every policy application is logged as an action against the observation it
touched. Source and target are normalized under the same policy set.

Fifteen policy kinds are supported: `exact`, `canonical_json`,
`ordered_sequence`, `unordered_set`, `unordered_multiset`,
`numeric_abs_tolerance`, `numeric_rel_tolerance`, `float_edges`,
`timestamp`, `generated_id`, `path_canonical`, `redact`, `ignore`,
`line_endings`, `stable_map`. `NORMALIZATION.md` gives the semantics of each.

Refusals are structural: an `ignore` on the observation root, an `ignore`
whose selector names no concrete segment (a blanket ignore), a tolerance
without finite positive bounds or with a relative bound of 100% or more, and
any policy set that leaves a mandatory probe with no comparable leaf in the
frozen baseline are all refused at validation or at freeze. Inferred policies
(proposed from a stability run) are stored with `origin: inferred` and
`accepted: false`; they have no effect until an explicit amendment accepts
them.

Generated-identity mapping preserves relationships: within one observation,
every occurrence of one raw identifier maps to the same `<id:N>`, numbered by
first appearance in canonical traversal, across every path the policy
covers, including identifiers embedded in text. Two records that reference
the same identifier on the source side must reference the same identifier
on the target side, or the normalized structures differ.

## 7. Observation engine

An adapter turns one run into a deterministic, versioned observation record.
First-party adapters: `process` (argv, stdin, stdout, stderr, exit code,
timeout, environment allowlist, bounded capture, working-directory
identity), `filesystem` (files created and removed, content digests or
text, canonical paths, symlink and traversal refusal), `json` (parsed and
canonicalised structured output, parse failures kept as evidence), `sqlite`
(declared tables, schema, rows, ordered or unordered, generated-key
correspondence, snapshot read inside one transaction) and `http` (local
service: method, path, body, status, selected headers, response body,
timeout). The HTTP adapter refuses any non-loopback host; an external service
is outside the verification boundary and the probe reports `UNVERIFIABLE`
rather than calling out. `ADAPTERS.md` is the protocol reference.

## 8. Determinism control

Each run executes in a fresh workspace with a controlled environment:
only allow-listed variables pass through, a fixed set is imposed
(`PYTHONHASHSEED`, `TZ`, `LC_ALL`, `LANG`, `PYTHONIOENCODING`,
`INVARA_WORKSPACE`, `INVARA_SEED`), and the record says which dimensions were
controlled, which were only reported, and which are uncontrollable (the wall
clock, thread scheduling, external services). A stability assessment runs
the source repeatedly and reports the paths that vary as proposed volatile
dimensions; it never accepts a policy on its own.

## 9. Counterexample search and finite-domain proof

The search is seeded, sequential and reproducible: a PRNG seed, a seed corpus,
and a fixed mutation catalogue (boundary values, null/empty/missing, numeric
extremes and sign, string length and Unicode, list ordering / duplication /
removal / cardinality, nested objects, operation sequences). A divergence is
shrunk deterministically to the smallest input that still reproduces it, and
both the original and the minimized input are kept. A search that finds
nothing reports `NO_DIVERGENCE_FOUND`, never a proof.

When the manifest declares a finite domain, the proof backend enumerates
every member, compares each, records cardinality, the domain digest and a
content-addressed summary of every observation, and issues
`PROVED_WITHIN_DECLARED_DOMAIN` only if every member is equivalent. An
enumeration that cannot complete is `UNVERIFIABLE`; there is no partial
proof. The backend interface is versioned so an SMT, symbolic-execution or
translation-validation backend can be attached later without touching the
claim model. None is bundled.

## 10. Repair session and governor

A repair session is an event-sourced state machine persisted in the
evidence store. States: `CREATED`, `BASELINE_CAPTURING`, `BASELINE_FROZEN`,
`ANALYZING`, `PLANNED`, `UNIT_PREPARING`, `UNIT_IN_PROGRESS`,
`UNIT_VERIFYING`, `UNIT_ACCEPTED`, `UNIT_REJECTED`, `UNIT_ROLLED_BACK`,
`COMPLETED`, `BLOCKED`. Invalid transitions raise and change nothing.

The governor keeps the user's working tree out of the loop entirely. It
requires a clean, identified tree at initialization, records the base
commit, keeps the accepted state in its own ref
(`refs/invara/repair/<session>/accepted`), captures the baseline in a
detached worktree at the base commit, and gives every repair unit its own
disposable detached worktree at the accepted commit. Verification runs
against that worktree; acceptance requires an INVARA-computed `PASS` for the
exact tree that was verified, commits it, and advances the accepted ref with
a compare-and-swap; rejection saves the patch as evidence and removes only a
worktree it created and can recognise. Nothing ever runs `git clean` or a
hard reset against a tree it did not create. `REPAIR_SESSION.md` has the full
lifecycle.

## 11. Claims and the final verdict

Six claim statuses: `PROVED_WITHIN_DECLARED_DOMAIN`,
`PRESERVED_WITHIN_ENVELOPE`, `NO_DIVERGENCE_FOUND`, `DIVERGED`,
`UNVERIFIABLE`, `HUMAN_REVIEW`. The final verdict keeps the four kernel
statuses and, like the kernel, names the rule that decided it:

1. evidence or manifest digest mismatch -> `BLOCK` (`integrity`)
2. any mandatory claim `DIVERGED` -> `BLOCK` (`diverged`)
3. any mandatory claim never evaluated -> `UNVERIFIABLE` (`never_evaluated`)
4. any mandatory claim `UNVERIFIABLE` -> `UNVERIFIABLE` (`unverifiable`)
5. any human-review requirement, `HUMAN_REVIEW` claim, unaccepted volatile
   dimension or post-divergence amendment -> `HUMAN_REVIEW` (`needs_human`)
6. otherwise `PASS` (`preserved`)

`CLAIMS.md` defines each status and what evidence it requires.

## 12. Reports

Two layers, both as JSON and Markdown. The non-coder summary leads with
기능 유지 / 성능 유지 / 정리 완료 항목 / 되돌린 변경 / 확인하지 못한 영역 /
다음에 사람이 볼 것, in Korean and English, before any internal term. The
technical assurance report carries manifests and digests, observations,
policies and their action logs, proof and search coverage, divergences,
counterexamples, accepted and rejected units, provenance and limitations.
The section that lists what could not be verified is mandatory and a test
fails if it is absent.

## 13. Security boundary

See `THREAT_MODEL.md`. In one line: no shell interpretation of command
arrays, environment allowlists, timeouts, bounded capture, path-traversal
and symlink refusal, secret redaction, no network by default, loopback-only
HTTP, missing executable -> `UNVERIFIABLE`, malformed adapter output ->
fail closed, corrupted store -> fail closed, digest mismatch -> `BLOCK`.
