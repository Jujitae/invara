# Counterexample search and finite-domain proof

Two ways to look beyond the recorded corpus, with different words for
what they find. Implementation: `src/invara/assurance/search.py` and
`proof.py`; tests: `tests/assurance/test_search.py`, `test_proof.py`, and
the fixture proofs in `test_fixtures.py`.

## Search

Given the seed inputs (the corpus, plus every member of a finite domain
that fits the budget) and a comparator that runs one input on both systems,
the search evaluates candidates in a fixed order and stops at the first
mandatory divergence or when the budget is spent.

**Order of candidates.**

1. Every seed itself.
2. Boundary tier 1 — the neighbours of every numeric leaf (`v-1`, `v+1`,
   and for floats `v±1e-9`), interleaved across seeds so the first runs
   cover the neighbourhood of every recorded input.
3. Tier 2 — zero, one, minus one, the negation.
4. Tier 3 — twice the value and the type extremes (`2^31-1`, `2^31`,
   `2^63-1`, `-2^63`; `±1e308`, `5e-324`).
5. Tier 4 — for every site: `null`, the empty value of its type, and the
   site removed.
6. Random mutations from the catalogue, drawn with the PRNG: 30% from a
   recently generated input, otherwise from a seed.

**The catalogue** (`mutate`): `null`, `empty`, `missing`, `numeric-zero`,
`numeric-sign`, `numeric-min`, `numeric-max`, `numeric-step`,
`numeric-type`, `string-length`, `string-unicode` (Korean, emoji, a NUL,
combining marks, right-to-left override, ligatures, whitespace),
`string-numeric`, `string-whitespace`, `bool-flip`, `list-reorder`,
`list-duplicate`, `list-remove`, `list-cardinality`, `object-nested`,
`object-unknown-key`. With `sequence_of_operations` (used for HTTP
delivery), the root list is mutated half the time so operation sequences
are reordered, duplicated and dropped.

**Reproducibility.** The PRNG is splitmix64, written out in `search.py`
rather than taken from `random`, so the sequence a stored search names is
regenerable by any interpreter; `Prng(42).next_u64()` is pinned in the
tests. The seed, the budget and the corpus determine the candidate
sequence; the result's `trace_digest` is the content digest of every input
evaluated, in order, and two searches with the same seed produce the same
digest. Every candidate's raw observations and comparison are stored in
the evidence store under `search:` keys.

**Budgets.** `runs` and `seconds` from the claim's params or the manifest
budgets; an evaluation that is `unverifiable` (a timeout on a monstrous
input, say) counts against the budget and is reported in coverage. If no
candidate at all could be compared the claim is `UNVERIFIABLE`; otherwise
finding nothing is `NO_DIVERGENCE_FOUND` and the coverage says how many
inputs were compared. It is never `PROVED`.

## Shrinking

When a candidate diverges, the search minimizes it: a greedy,
restart-on-success reduction over local shrinks — a list to empty, to
either half, minus one element; an object minus one key; a string to
empty, to either half, minus one character; a number to zero, to a float's
integer, then toward zero by halving deltas. Only candidates that are
strictly smaller by canonical JSON size, or numbers strictly closer to
zero, are tried, so the process terminates; only candidates the comparator
still rejects are kept, so the minimized input reproduces the divergence
by construction. The step budget is `budgets.shrink_steps`. Both the
original failing input (with the mutation that produced it and the seed it
came from) and the minimized one are recorded in the claim's
`counterexample`.

## Finite-domain proof

When `input_domain.kind` is `finite`, every member of the cartesian
product is enumerated in declared parameter order and compared against
its own frozen baseline record: the record captured for that member at
characterize time, believed by the digest of its exact input and initial
state. A member the frozen baseline holds no record for is compared
against nothing: the source is never run again after the freeze, because
a run of `SOURCE_ROOT` at proof time would be evidence about whatever the
directory holds then, not about the baseline the claim is stamped with.
Every member is in the baseline when `finite_max_members` was at least
the cardinality when `characterize` ran; a budget raised afterwards (by
amendment, before or after the freeze) adds members to the enumeration
but never to the frozen baseline, and the proof over them is
`UNVERIFIABLE` until a new session captures the domain under the budget
in force. Before `PROVED` is recorded, and again when a package is
inspected, every member's comparison is held to a source record that is
the member's frozen record (`proof.binding_problems`).

- cardinality above `budgets.finite_max_members` → `UNVERIFIABLE`, nothing
  is sampled;
- a member without a frozen baseline record → `UNVERIFIABLE`, the member
  named, nothing run for it;
- a member that cannot be compared → `UNVERIFIABLE`, even if every other
  member matched;
- an enumeration cut short by a time budget → `UNVERIFIABLE`;
- any diverging member → `DIVERGED`, with the diverging count and the
  minimal member — smallest by canonical size, earliest in declared order
  among equals — as the counterexample;
- every member equivalent → `PROVED_WITHIN_DECLARED_DOMAIN`, with the
  domain digest, the cardinality, and a content-addressed summary of every
  member's comparison digest in coverage.

There is no partial proof, by construction: the `ClaimResult` refuses a
`PROVED` status whose coverage is not exhaustive.

## The proof-backend protocol

`proof.ProofBackend` (`invara.assurance.proof-backend/1`) is the seam for
future formal methods. A backend has a `name` and a `version` and
implements `prove(finite, evaluate, *, max_members, max_seconds, clock)`
returning a `ProofResult` with the same five fields the exhaustive backend
fills. It is registered with `proof.register_backend` and selected by name.
An SMT, symbolic-execution, model-checking or translation-validation
backend would plug in here and speak the same claim model; none is bundled,
and no solver dependency exists. See `DOMAINS.md` for the extension rules.
