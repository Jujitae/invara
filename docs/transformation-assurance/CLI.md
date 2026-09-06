# Command line, plugin commands, and the non-coder flow

Everything below runs with `python -m invara` (or `invara` after
`pip install -e .`), locally, offline, on the standard library. No step
consults a model. Implementation: `src/invara/assurance/cli.py`,
registered beside the kernel's commands in `src/invara/__main__.py`.

## Exit codes

| code | meaning |
|---|---|
| 0 | done; for `compare`/`search`/`prove`: the claim did not diverge and was verifiable; for `verify`/`unit-verify`: the verdict is `PASS` or `HUMAN_REVIEW` |
| 1 | a claim `DIVERGED`, a verdict is `BLOCK`, or `--require` was not met |
| 2 | a claim or verdict is `UNVERIFIABLE` |
| 3 | the step was refused (`REFUSED <reason>: <detail>` on stdout) |

`REFUSED` reasons are the stable slugs the manifest, workflow and governor
raise (`root_ignore`, `not_frozen`, `unit_not_verified`, `stale_verification`,
`dirty_tree`, …). A store held by another process is `REFUSED store_busy`
(run the command again); a store that cannot be trusted is `store_corrupt`;
a store that cannot be opened at all is refused the same way, never as a
traceback. An unexpected exception is `REFUSED internal_error` and exit 3,
never exit 1: a crash is not a `BLOCK`. A `run` whose session reached a
verdict but whose report file could not be written is
`REFUSED report_unwritable`, naming the verdict it reached; the session is
on record and the same command run again writes the report. `unit-finish`
decides its exit code from the verdict on record before it prints: a
console that fails after the unit was verified (and, on `PASS`, accepted
and committed) does not turn that into a refusal; the state is said on
stderr instead. JSON output is ASCII-safe (`\uXXXX` escapes) so a cp949
console cannot alter it.

## `invara assure` — an existing BEFORE and AFTER

The one-shot form is what a non-coder (or the agent working for one) runs:

```
invara assure run          --manifest FILE --source-root DIR --target-root DIR [--workspace DIR] [--runs N] [--json FILE] [--md FILE] [--db FILE]
```

`run` creates the session if it does not exist, characterizes, freezes,
evaluates every declared claim that has no result under the manifest in
force, computes the verdict and writes the report; it prints the three
questions first (같아? 이상한 점? 모르는 것?). It is resumable: run it again
after an amendment and only the invalidated claims are re-evaluated. It
never completes, accepts or decides anything; it exits by the verdict like
`verify`. An existing session is bound to the roots it was created with:
a run that names other directories is `REFUSED roots_mismatch` (a directory
that does not exist, `root_missing`) before anything is read or appended,
never answered with the verdict of the directories the session did examine;
the roots examined are printed with the verdict (`roots: source …; target
…`), and another directory gets its own session id. The step-by-step
commands remain for anyone who wants them:

```
invara assure init         --manifest FILE --source-root DIR --target-root DIR [--workspace DIR] [--db FILE]
invara assure characterize SESSION [--runs N]
invara assure freeze       SESSION
invara assure compare      SESSION
invara assure search       SESSION [--seed N] [--runs N] [--seconds S]
invara assure prove        SESSION
invara assure performance  SESSION            (only with a performance_envelope claim)
invara assure amend        SESSION --amendment FILE
invara assure verify       SESSION [--require PASS|HUMAN_REVIEW]
invara assure report       SESSION [--json FILE] [--md FILE]
invara assure status       SESSION            (JSON)
invara assure coverage     SESSION            (JSON: the coverage map)
invara assure export       SESSION --out FILE (self-contained evidence package)
invara assure inspect      FILE               (recompute a package with nothing but the package)
invara assure resume       SESSION            (JSON)
invara assure complete     SESSION
invara assure list
```

`coverage` prints the assurance coverage map: one state per observed value
(`PROVED`, `TESTED`, `SEARCHED`, `OBSERVED`, `EXCLUDED`, `UNOBSERVED`,
`UNVERIFIABLE`, `HUMAN_REVIEW`, `DIVERGED`) with the reason, rolled up by
probe, policy, exclusion, claim and unit; the report carries the same map.
`export` writes every record of the session, content-addressed, into one
zip, with the hash chain of its events (and the hashes of other sessions
that wrote in between, as link rows); `inspect` reopens such a zip without
any store. Inspection first applies fixed limits to the untrusted archive:
4,096 entries, a 4 MiB central directory, a 72 MiB archive file, 8 MiB
compressed and 16 MiB uncompressed per member, 64 MiB compressed and 64 MiB
uncompressed across all members, and a 200:1 per-member compression ratio.
ZIP64, multi-disk ZIPs, and a non-empty member declaring zero compressed bytes
are refused. Member bytes are then read in bounded chunks, and at most 100
inspection problems are returned (the last item counts omitted problems).
Every resource refusal happens before index JSON parsing or semantic replay.

After those bounds, `inspect` fails closed on any inconsistency: an archive listing that is
not one canonical member set (a member name that appears twice, a member the
index does not name, more than one `index.json`, a name that is not a plain
relative path, two names that differ only in case: no duplicate is ever read,
whichever copy comes first); an index entry whose bytes,
size or digest differ; a content address that does not match its record; an
event whose payload does not match its recorded hash, or a chain that no
longer links (a record removed or rewritten); a manifest or baseline digest
the event history disagrees with; a record the evidence names (a baseline
record, a comparison a claim rests on, the frozen baseline) that is not in
the package; a comparison that does not name the manifest it was made
under, or names one whose policy set is not its own; a claim recorded as
preserved whose own comparison records show a divergence; a proof whose
evidence does not bind every member of the declared domain, exactly once,
to a source and a target execution of that member's own input (by the
`input_digest` the raw records carry), or whose source record for a member
is not that member's frozen baseline record; a search that names fewer or more
comparison records than the runs it counts; a history that does not end
with the export that produced the package; a `verdict.json`,
`integrity.json` or `report.json` that differs from what that export
recorded in the hashed history; a record the history does not account for
(a comparison no claim result names, a raw run no comparison uses, a
normalized record or blind-spot scan no freeze requires); a normalized
record or blind-spot scan the freeze requires that is missing, or a scan
made under another manifest or over other baseline records; a `report.json`
that differs, in any field but provenance, from the report rebuilt from the
package's own records with the code that wrote it (the headline and its
label, the three questions, the six sections, every claim's status and
strength, coverage, divergences, counterexamples, the final verdict block):
the report is the reader's deliverable, so it is re-derived, never merely
byte-checked against the index, and a qualified PASS beside an
informational divergence rebuilds the same way and stays legitimate. The export
itself is an event of the session (allowed in every state, moving nothing):
it carries the verdict, the store-wide problems that verdict counted and
the report digest, so none of those free files can be edited on its own.
The store-wide problems are carried, not re-derived: the other sessions
are not in the package, and `inspect` says how many it carries. Every
comparison is recomputed from the raw records and the verdict re-derived;
any problem makes that verdict BLOCK. The package is not signed: a clean
inspection shows that the package agrees with itself and its raw records,
not who wrote it; a history rewritten consistently (every hash recomputed),
or a package cut back to an earlier, internally complete state with every
later record removed, is indistinguishable from a genuine one without an
externally held receipt (the store's chain head, the package digest).
Programs are not re-executed; the package says all of this.

## `invara repair` — a governed repair of a git repository

```
invara repair init         --repo DIR --manifest FILE [--workspace DIR] [--db FILE]
invara repair characterize SESSION [--runs N]
invara repair freeze       SESSION
invara repair analyze      SESSION [--findings FILE]
invara repair plan         SESSION --plan FILE
invara repair unit-start   SESSION UNIT       (JSON: the worktree to edit)
invara repair unit-verify  SESSION UNIT
invara repair unit-finish  SESSION UNIT       (verify, then accept only on PASS; otherwise leave open and say what is next)
invara repair unit-accept  SESSION UNIT [--reviewed-by NAME]
invara repair unit-reject  SESSION UNIT --reason TEXT
invara repair continue     SESSION
invara repair finish       SESSION
invara repair verify       SESSION [--require PASS|HUMAN_REVIEW]
invara repair report       SESSION [--json FILE] [--md FILE]
invara repair status       SESSION            (JSON)
invara repair coverage     SESSION            (JSON)
invara repair export       SESSION --out FILE
invara repair resume       SESSION            (JSON)
invara repair amend        SESSION --amendment FILE
invara repair list
```

`unit-finish` is `unit-verify` followed by `unit-accept` when, and only
when, the verdict is `PASS`. A `BLOCK` or `UNVERIFIABLE` unit stays open
with the next step printed; a `HUMAN_REVIEW` unit still needs a named
person through `unit-accept --reviewed-by`. It never rejects on its own.

`--db` defaults to `.runtime/verify.db`, the verifier's own database; the
assurance tables live beside the contract and verdict tables. `--workspace`
given at `init` is recorded in the session and used by every later step.
`characterize` runs once per session; `resume` prints `problems` (the
session is blocked), `transient` (git could not be consulted; nothing was
recorded, run it again) and `notes` (an interrupted transition it
completed).

## Gating with the kernel

The six MCP tools and the `seal`/`judge` contract are unchanged. A sealed
contract can require an assurance verdict the way it requires any other
check:

```json
{"id": "assurance", "command": ["python", "-m", "invara", "assure", "verify", "shop", "--require", "PASS", "--db", ".runtime/verify.db"], "expect_exit": 0}
```

`judge` then reports `BLOCK` unless the session's final verdict is exactly
`PASS`. The kernel learns nothing about manifests; it sees an exit code.

## Plugin commands

`/invara:repair` and `/invara:assure` (`plugin/commands/`) walk a host agent
through the same commands, in order, with the JSON templates it needs and
the rules it may not break. They use the bundled package at
`${CLAUDE_PLUGIN_ROOT}/src`. A contract test checks that every command they
invoke exists and that the steps appear in protocol order. Codex or any
shell-capable agent follows `docs/transformation-assurance/CLI.md` — this
file — with the same commands.

## The non-coder flow

The person types one sentence: **"기능은 그대로 두고 이 프로젝트를 제대로
정리해줘."** What happens, in their terms:

1. The agent reads the project and writes down how the program is used
   and what it produces (the manifest). The person does not read it; the
   agent may ask them one or two plain questions — "is the order of tags
   in the output meaningful?" — when a run-to-run difference cannot be
   classified.
2. INVARA records what the program does today, several times, and freezes
   that record.
3. The agent measures the code, names what is wrong with its structure, and
   plans small repairs.
4. For each repair: the agent changes the code in a separate copy; INVARA
   runs the frozen inputs and a search for new ones against it and says
   `PASS`, `BLOCK`, `UNVERIFIABLE` or `HUMAN_REVIEW`. Only a `PASS` is kept.
   A `BLOCK` is thrown away with its evidence saved. The person's own copy
   never changes.
5. At the end the person gets a report that starts with six lines they can
   read without knowing any language the code is in:

   - **기능 유지** — did it still do the same thing, on which inputs, and
     was that tested or proved;
   - **성능 유지** — measured, not promised;
   - **정리 완료 항목** — which repairs were kept;
   - **되돌린 변경** — which were thrown away, and why;
   - **확인하지 못한 영역** — what nobody checked (never empty by omission);
   - **다음에 사람이 볼 것** — what needs a person.

   The kept repairs are on a branch named `invara/repair/<session>`; the
   person, or whoever they trust, merges it or does not.

The same report, below those six sections, carries everything an engineer
needs to check the claim: digests, policies, coverage, divergences,
counterexamples, provenance, limitations.
