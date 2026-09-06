# Assurance claims and the final verdict

Six claim statuses, five claim kinds, four verdicts. Implementation:
`src/invara/assurance/claims.py`; the ladder is tested in
`tests/assurance/test_claims.py`.

## Claim statuses

| status | what the evidence supports |
|---|---|
| `PROVED_WITHIN_DECLARED_DOMAIN` | every member of an explicitly finite domain was compared and found equivalent; coverage records the cardinality and every member digest. The only status allowed to use the word *proved*. |
| `PRESERVED_WITHIN_ENVELOPE` | every recorded input compared equivalent under the accepted policies (or the measured performance stayed within the declared tolerance). Says nothing about inputs outside the corpus. |
| `NO_DIVERGENCE_FOUND` | a bounded search, or a stability assessment, ran and found no diverging input. Explicitly not a proof. |
| `DIVERGED` | at least one input on which source and target differ is on record, with its located divergence and, for a search, the minimized reproducer. |
| `UNVERIFIABLE` | the claim could not be evaluated: a run did not observe, no candidate could be compared, the enumeration did not complete, the budget allowed nothing. Unchecked is not preserved. |
| `HUMAN_REVIEW` | the machine has nothing more to say and a person must decide: an uncovered volatile dimension, a declared review item. |

Which statuses each kind may take:

| kind | statuses |
|---|---|
| `corpus_equivalence` | PRESERVED, DIVERGED, UNVERIFIABLE |
| `counterexample_search` | NO_DIVERGENCE_FOUND, DIVERGED, UNVERIFIABLE |
| `finite_domain_proof` | PROVED, DIVERGED, UNVERIFIABLE |
| `performance_envelope` | PRESERVED, DIVERGED, UNVERIFIABLE |
| `baseline_stability` | NO_DIVERGENCE_FOUND, HUMAN_REVIEW, UNVERIFIABLE |

A `ClaimResult` carries the claim id and kind, whether it was mandatory, the
manifest digest and baseline digest it was computed under, a coverage
record, its divergences, its counterexample, what could not be verified,
and the digests of the comparison records that are its evidence. It refuses
to be built in an impossible state: a PROVED result without exhaustive
coverage, a DIVERGED result without a divergence, an UNVERIFIABLE result
without a reason.

Evidence digests are the content addresses of stored comparison records,
never a bare comparison's digest, so every one of them resolves in the
store and in an exported package. A proof names one record per member,
none cut; before the word *proved* is recorded, and again whenever a
package is inspected, every member is bound to its own source and target
execution by the digest of its exact input and initial state (each raw
record carries `input_digest`), by the declared systems, compared without a
mandatory divergence; duplicate, missing or substituted evidence makes the
claim UNVERIFIABLE, never PROVED. A search names every comparison it made
(its runs plus its shrink steps), so a found divergence is always among the
records it names.

## The verdict ladder

`final_verdict(requirements, results, ...)` is pure. Only the latest result
per claim id counts, and only if it was computed under the current
manifest digest, under the current baseline digest, and for the claim's
declared kind; a result of another kind or another baseline does not
satisfy a requirement. Kill path first:

1. evidence integrity problems (a baseline record missing, tampered, or
   the chain broken) → `BLOCK`, decided by `integrity`
2. a repair unit changed a path outside its declared ownership → `BLOCK`,
   decided by `constraint_breaks`
3. any mandatory claim `DIVERGED` → `BLOCK`, decided by `diverged`
4. any mandatory claim without a result under this manifest (never
   evaluated, or invalidated by an amendment) → `UNVERIFIABLE`, decided by
   `never_evaluated`
5. any mandatory claim `UNVERIFIABLE` → `UNVERIFIABLE`, decided by
   `unverifiable`
6. any `HUMAN_REVIEW` claim, declared human-review item, post-divergence
   amendment (a policy or exclusion covering a recorded divergence, a
   mandatory claim removed or demoted, a review item removed), or
   uncovered volatile dimension → `HUMAN_REVIEW`, decided by `needs_human`
7. otherwise `PASS`, decided by `preserved` when any mandatory claim is
   `PRESERVED_WITHIN_ENVELOPE`, else by `proved` when any is
   `PROVED_WITHIN_DECLARED_DOMAIN`, else by `no_divergence_found` (every
   mandatory claim is a search or a repeated run that found no divergence:
   evidence, not a proof, and the reason says so). The three buckets
   (`preserved`, `proved`, `no_divergence_found`) stay apart in the
   verdict; a search is never listed as preserved.

Informational (non-mandatory) claims never change the verdict; their ids
are listed under `informational` and the status each reached under
`informational_statuses` (`never evaluated` when none is on record). What
an informational claim found is never hidden behind the mandatory verdict:
a `PASS` beside an informational `DIVERGED` carries the qualifier
"informational claim(s) DIVERGED: …" in its reason, the report's headline
says that an informational check found a difference (instead of the plain
"behaviour preserved" label), the first question answers "no" with the
divergence and the second lists it, and `verify` prints
`informational: <id>: <status>` beside the mandatory verdict while keeping
its exit code (`--require PASS` still gates on the mandatory verdict alone).
A verdict names the field that decided it, and that field is never empty,
so every verdict can be argued with at the point it was made.

## What counts for a session

- An **assure** session's verdict rests on the latest result of every
  claim under the current manifest.
- A **repair** session's verdict rests on the results recorded for the
  last *accepted* unit — those were computed against the tree that became
  the accepted state — plus the session-level stability claim. A rejected
  unit's results describe a tree that no longer exists and do not count. A
  repair session with no accepted unit has never evaluated its claims
  against anything, and says `UNVERIFIABLE`.
- A unit's own verdict, the one `unit-accept` reads, is computed the same
  way at `unit-verify` time for the exact tree it verified, with the
  ownership check on top.

## Performance, measured or not verified

`performance_envelope` is the only claim that may say performance was
preserved, and it says so only after `Engine.measure_performance` ran its
declared protocol: fresh runs of source and target interleaved, the
declared number of `runs` per input, the median wall time per side, and
the declared `rel_tolerance` / `abs_tolerance_s`; the protocol and every
measurement are in the result's coverage. Without that claim the reports
say, in both layers, that performance was **NOT VERIFIED**; incidental
wall-clock timings in observation records are provenance, not evidence.
`BUILD_COMPLETE` of this subsystem never implies performance preservation.

## The coverage map

`coverage.coverage_map` derives, from the manifest, the results that count
(the same `latest_per_claim` rule the verdict uses), the baseline records
and the volatility check, one state per observed value: `PROVED`,
`TESTED`, `SEARCHED`, `OBSERVED` (an informational probe), `EXCLUDED`,
`UNOBSERVED` (removed or replaced by a policy, or never compared),
`UNVERIFIABLE`, `HUMAN_REVIEW`, `DIVERGED`, each with its reason and its
cause (`policy`, `excluded`, `informational`, `volatile`, `blind`,
`truncated`, `never`); then by probe, policy, exclusion, claim and repair
unit. A claim counts for a value only on the inputs it compared (a corpus
claim its corpus items, a proof its domain members); a value seen on
several inputs takes the weakest of its states and says so, so a proof
never reaches a value it did not run. A divergence is keyed to the raw
value it came from. When a comparison recorded only part of its
divergences (`max_divergences`), the values it did not list are
`UNOBSERVED` with the cause `truncated`, not `TESTED`. A leaf the
blind-spot scan found insensitive is `UNOBSERVED` with the cause `blind`
(undeclared) rather than compared. The summary sentence counts every
state; it says "all compared" only when every observed value was. A probe
captured partially (rows or entries omitted by a capture limit, a truncated
stream, a byte budget reached) is named in the map (`partial`), the summary
and the third question: what lies beyond the capture was not observed,
however the captured part compared. The map never changes a verdict; it
says what the verdict rested on.

## The blind-spot scan

`sensitivity.scan` mutates every observed value of the frozen baseline
(boundary, null, empty, order, relationship, tolerance edge, type flip,
and for a value a policy replaces with a placeholder a *same-shape*
value: another valid id, another instant, relabelled at every occurrence),
deterministically and within `budgets.sensitivity_max_leaves` (sites: a
value on one input, or a list the order pass reverses; the count is said
in sites and in value paths, never mixed), and runs the same comparator
under the manifest in force. The tolerance edge is a value just outside
the accepted envelope by the comparator's own rule (for a relative
tolerance `v / (1 - rel)`); where no such value can be built the scan says
so as a limitation instead of calling the value dulled. A value where no tested
change is a mandatory divergence is a *blind spot*; a value where some
changes pass is *dulled* by a tolerance; a value a `generated_id`,
`stable_map` or whole-value `timestamp` policy replaces is a *placeholder*
(another value of the same shape passes by design, only relationships
between occurrences are compared) and the map shows it as replaced, not
compared. A text a `canonical_json` policy parses is a subtree for the
scan and the map alike, so a declaration inside it covers real values. The scan runs at freeze and again
after every amendment, is stored as evidence (`sensitivity`), and is
reported in the section on what could not be checked. A blind spot the
declaration already owns (an excluded path, a value removed by a policy)
is annotated on its own map line; a blind spot on a value the declaration
claims to compare is listed separately as undeclared, because nobody
chose it. It measures the equivalence definition, not the transformation:
detecting a synthetic change is not detecting every real regression.

## The six kinds of knowledge

A report keeps apart:

- exhaustively proved (`PROVED_WITHIN_DECLARED_DOMAIN` claims),
- tested over a finite corpus (`PRESERVED_WITHIN_ENVELOPE`),
- searched without finding divergence (`NO_DIVERGENCE_FOUND`),
- explicitly excluded (the manifest's `exclusions`),
- not observed (volatile dimensions no policy covers),
- diverged.

`claims.coverage_summary` produces that partition; the report prints it.
