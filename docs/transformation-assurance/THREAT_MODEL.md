# Threat model — transformation assurance

The kernel's threat model (`SECURITY.md`) stands: INVARA runs commands you
declared, with your permissions; it is not a sandbox. This document covers
what the assurance subsystem adds, what it protects, what it does not, and
where each protection lives. The regression suite holds the failure cases
that shaped these protections.

## The execution boundary, stated plainly

INVARA executes only the commands the manifest declares, inside a session
it recognises (the per-run workspace, the declared system roots, the
session's own worktrees), with the environment it controls. That is the
whole of the boundary. The PROCESS and LOCAL HTTP adapters do **not**
provide an operating-system sandbox: a child program can read any file
and reach any network the user can. INVARA's own no-network rule (no
socket in the verdict path other than loopback to a service the manifest
started) says nothing about what the child does.

Consequently, where a claim would need isolation that this environment
cannot enforce, INVARA does not claim containment; it fails closed. A
request the manifest aims at a non-loopback host is not sent and the run
is `unverifiable`; a program that cannot be identified as the one declared
(the Windows Store alias) is `unrunnable`; a probe that would leave the
workspace or the system root is `malformed`. None of these can become a
`PASS`.

## Assets

- the user's working tree and branches;
- the baseline evidence (what the program did before), and every later
  observation, comparison and claim;
- the definition of equivalence (the manifest and its digest);
- the verdicts and the report.

## Adversaries

- **The transformer.** A coding agent or a person that wants its change
  accepted. It may be careless, over-eager, or hostile. It can run
  commands in the unit worktree and can write any file the CLI reads
  (manifests, amendments, plans, findings).
- **The program under observation.** Its output is untrusted text; it may
  print secrets, placeholder-looking strings, tracebacks with paths, or
  gigabytes.
- **The environment.** Crashes at any point, a concurrent session on the
  same repository, a filesystem with symlinks, a Windows checkout with
  CRLF conversion, a machine without git.
- **An editor of the evidence store.** Anything that rewrites rows in
  `verify.db` after the fact.

Out of scope, as for the kernel: a root-level attacker on the machine, and
a program that does harm when run (INVARA runs it; declaring a system is
declaring that it may run).

## Protections, by requirement

| requirement | protection | where |
|---|---|---|
| no shell for untrusted command arrays | commands are argv lists; a string is refused at manifest validation; `subprocess` is never given `shell=True` (a test greps the governor) | `manifest.System`, `execute.run`, `governor._git` |
| command and environment provenance | every record stores the resolved argv, root, controlled values, inherited names and the uncontrollable list | `execute.controlled_environment` |
| execution timeout | per-run `timeout_s`, service readiness timeout, terminate the contained tree, retain bounded partial streams; Windows verifies zero active job members or reports `unverifiable` | `execute.run`, `_terminate`, `_wait_ready` |
| stdout/stderr size limits | pipes drained on threads, `capture_limit_bytes` kept, full digest and byte count recorded | `execute._Capture` |
| path traversal rejection | seeded files and databases must resolve inside the workspace; probe roots and paths inside the workspace or the system root; anything else is `malformed` | `execute._inside`, `_contained` |
| unsafe symlink handling | filesystem walks never follow links; a symlink **or a Windows directory junction** is recorded as `{"symlink": true}` and not opened; workspace removal walks by hand (`os.walk` descends into junctions) and unlinks a link instead of entering it; the governor never recognises a worktree path with a link component | `execute._is_link`, `_file_names`, `_remove_tree`, `governor.recognized_worktree` |
| secret redaction, never plaintext evidence | the whole observation record (probes, streams, environment, command, service log) passes through the redactor before it is stored, on every exit of a run (timeout, unrunnable, malformed included); a value under a key that names a secret (`password`, `access_token`, `api_key`) is redacted whole, and a quoted JSON key in text is matched too; a manifest that carries a secret in `env.set` or a command is refused (`secret_in_manifest`) | `redaction.redact_value`, `execute.run`, `manifest.secret_in_manifest` |
| redaction is not equivalence | a redaction token is `<redacted:kind#fingerprint>` where the fingerprint is a digest prefix of the secret: two different secrets get two different tokens and diverge, the same secret matches, and no plaintext is kept. Comparison never treats "both redacted" as "equal"; it compares the fingerprints, which are the non-secret evidence sufficient for the result | `redaction.fingerprint`, `compare.compare` |
| the child is stopped, not just asked | on Windows the child starts suspended, enters a kill-on-close job, then resumes; timeout terminates the job and verifies zero active members before returning, otherwise the run is `unverifiable`. Other platforms use a process group and `killpg`; feeding stdin has the same deadline as the run; a readiness poll reads a bounded prefix under the remaining deadline | `execute._popen_options`, `_start_windows_job`, `_terminate`, `_feed_stdin`, `_wait_ready` |
| the program that ran is the program declared | the resolved executable and its identity are recorded (`program`); the Windows Store `python` alias, which exits 9009 without running anything, is detected and recorded `unrunnable` | `execute._program_identity`, `runner._store_alias_detail` |
| bounded records | filesystem `max_entries` and `max_text_bytes`, per-table `max_rows`; omissions are recorded (`entries_omitted`, `rows_omitted`), never silent | `execute._probe_filesystem`, `_read_table`, `manifest.Probe` |
| seeded SQL cannot escape | `initial_state.sqlite.sql` runs under a statement-keyword allowlist and an authorizer that denies `ATTACH`, `DETACH` and `PRAGMA`; the database file itself must resolve inside the workspace | `execute._seed_state`, `_seed_authorizer` |
| no silent upload | no network code in the verdict path; the only sockets are loopback connections to a service the manifest started | `execute._probe_http`, `_wait_ready` |
| no external network by default | a request to any host but `127.0.0.1`, `localhost`, `::1` ends the run as `unverifiable` before it is sent | `execute._probe_http` |
| no credentials in evidence | the child inherits only allow-listed variables; controlled values come from the manifest, which the user wrote | `manifest.DEFAULT_ENV_ALLOW`, `execute.controlled_environment` |
| missing executable → UNVERIFIABLE | `unrunnable` records; the comparator never compares one | `execute.run`, `compare.compare` |
| a diagnostic is not the observable | a `json` probe whose declared value was not obtained (`parse_error`) is a record of a failed observation: a probe that obtained nothing on either side is left out of that input's comparison, which rests on the other declared observables and is `unverifiable` when no mandatory one remains, so two alike diagnostics never make behaviour "preserved" or "proved" by themselves; a mandatory probe that obtained nothing on any baseline input is refused at freeze (`probe_unobserved`); the diagnostic fields are never coverage and the report names the inputs | `normalize.unobtained_observable`, `compare.compare`, `engine.check_probes`, `coverage.coverage_map` |
| timeout → UNVERIFIABLE or BLOCK | a `timeout` record is unverifiable; a mandatory claim that is unverifiable makes the verdict `UNVERIFIABLE`; it never becomes `PASS` | `claims.final_verdict` |
| malformed adapter output → fail closed | an adapter exception makes the record `malformed`; a record of another version raises `CompareError` | `execute.run`, `compare.compare` |
| corrupted session store → fail closed | an unreadable database, a wrong schema version, a dropped table raise `EvidenceError`; an impossible event history raises `SessionCorrupt` | `evidence.Evidence`, `session.reduce` |
| manifest/evidence digest mismatch → BLOCK | baseline records are re-hashed against their stored digest and the frozen digest; the chains are rebuilt; any mismatch is an integrity problem and the verdict is `BLOCK` | `engine.integrity_problems`, `evidence.verify`, `claims.final_verdict` |
| a proof rests on the frozen baseline, never on `SOURCE_ROOT` at proof time | a finite member the frozen baseline holds no record for (captured under a smaller `finite_max_members`, raised later by amendment before or after the freeze) is compared against nothing and the proof is `UNVERIFIABLE`; the source is never run again after the freeze; before `PROVED` is recorded and again at `inspect`, every member's source record must be that member's frozen record by digest, not merely an execution of its input | `engine.compare_against_baseline`, `proof.binding_problems`, `package.inspect` |
| normalization that removes all signal → refusal | root and blanket ignores refused at validation; a set that leaves a mandatory probe with no comparable leaf refused at freeze | `manifest.Policy`, `normalize.erases_signal`, `engine.freeze` |
| a repairer cannot self-declare | there is no argument to `unit-accept` that carries a verdict; it reads the verdict stored for the verified tree hash (`verified_tree` must equal the current tree), a new `unit_verifying` event voids the previous verdict, and the governor refuses a tree that moved | `workflow.unit_accept`, `session.reduce`, `governor.accept_unit` |
| what was verified is what is accepted | the unit tree is read before and after the claims run; a tree that changed during verification is an integrity problem and the verdict is `BLOCK`; the baseline worktree is checked pristine (at the base commit, clean) before and after | `workflow.unit_verify`, `governor.assert_pristine` |
| the ownership base cannot be rewritten | changed paths are computed against the accepted commit the session recorded, not against the marker file; a marker whose base disagrees is `marker_tampered` | `governor._unit` |
| a checkout is the commit | a fresh worktree must write back to the commit's own tree, or it is removed and refused (`checkout_not_faithful`): line-ending attributes, smudge filters and sparse checkouts cannot change bytes under verification; a nested repository in a unit is refused | `governor._assert_faithful`, `unit_tree` |
| resume never guesses | an interrupted acceptance is reconciled only when the unit's recorded verdict is `PASS` on its current tree and the ref is exactly one commit ahead holding that tree; anything else blocks with the reason; a git that cannot be started is reported as transient and nothing is recorded | `workflow.resume`, `governor.reconcile_acceptance` |
| finish publishes only the session's state | `finish` refuses when the accepted ref is not at the commit the session recorded (`accepted_moved`), validates the branch name, and never moves an existing branch | `governor.finish` |
| user work is never destroyed | dirty tree refused (INVARA's own `.invara/` and `.runtime/` do not count); the workspace may not lie inside the repository; detached worktrees with markers; only recognised worktrees removed; a registered worktree that lost its marker is adopted, not deleted; no `git clean`, no hard reset, no stash | `governor` |
| an internal error is never a verdict | any unexpected exception in the CLI prints `REFUSED internal_error` and exits 3; an unopenable evidence database is `store_corrupt`, also 3; exit 1 is reserved for `BLOCK` | `cli._guarded`, `evidence.Evidence` |

## Placeholder collision

A program that literally prints `<id:1>`, `<timestamp>`, `<redacted...>`,
`<nan>`, `$WORKSPACE` or `$ROOT` cannot forge equivalence. Before any
policy runs, the built-in `builtin:reserved` normalization marks raw text
that already looks like a placeholder as a literal (`<literal:<id:1>>`,
`$$WORKSPACE`) and logs that it did. A mapped identifier on one side and a
literal placeholder on the other therefore differ at that path, both raw
values are in the divergence record, and the same literal on both sides
still compares equal. The placeholders are not secrets and the design does
not depend on their unguessability; it depends on the raw record being
stored and the policy set being part of the digest.

## Measuring the definition, and carrying the evidence away

Two mechanisms make the trust boundary inspectable rather than argued.
The blind-spot scan (`sensitivity.py`) mutates the frozen baseline under
the manifest in force and reports every observed value where no tested
change would have produced a mandatory divergence; an over-broad
exclusion, ignore or tolerance shows up as a number in the report instead
of passing silently. The evidence package (`package.py`) lets a reader
recompute a session's content addresses, event history, comparisons and
verdict with nothing but the package: independence from the agent and the
machine that produced the result, for everything except re-executing the
programs, which the package does not contain. The inspection fails closed
before parsing the index or replaying records if the untrusted ZIP exceeds
any fixed resource bound: 4,096 entries, 4 MiB of central-directory bytes,
72 MiB total file size, 8 MiB compressed or 16 MiB uncompressed for one
member, 64 MiB compressed or uncompressed across all members, or a 200:1
member compression ratio. ZIP64, multi-disk archives, and non-empty members
declaring zero compressed bytes are outside this package format. Members are
expanded through a bounded reader and diagnostics retain the first 99
problems plus one omission counter. After that preflight it fails closed on
every inconsistency it can see (the archive listing as one canonical
member set: unique names, every member indexed, one index, plain relative
names with no case aliases; index, content addresses, event hashes
and chain links, referenced records, claims against their own comparisons,
a history that does not end with its own export, free files that differ
from what that export recorded, records the history does not account for,
records the freeze requires that are missing or swapped, a report that
differs from the one rebuilt from the package's own records); what it cannot
see is a package rewritten consistently from edited raw records or with
every hash recomputed, or one cut back to an earlier, internally complete
state, because the package is not signed and carries no externally held
receipt; the store-wide problems its verdict counted are carried from the
exporting store, not re-derived. A clean inspection therefore means
consistency, not authorship or freshness. Neither mechanism changes a verdict, and
both are worded as what they are: a measurement of the declaration, and a
recomputation of stored observations.

## Normalization abuse

Explicit semantic policies and exclusions are the only way to declare a
difference immaterial, and every one of them is a named, reasoned,
digested part of the manifest. They are held to these rules
(`NORMALIZATION.md` has the full list): erasing kinds and exclusions need
a concrete segment; a whole mandatory probe cannot be excluded; a policy
set that leaves a mandatory probe nothing to compare is refused at freeze
and again at every amendment; an absolute tolerance larger than every
baseline value is refused; a timestamp tolerance above a year is refused
and never applies to plain numbers; coverage of a volatile path is
decided by re-normalizing the runs, not by matching selectors; and an
amendment that covers a recorded divergence (by path, by ancestor, by raw
path, by exclusion) or weakens a claim or removes a review item after the
fact holds the verdict below `PASS`. Redaction takes part in none of this:
it changes what is shown and stored, never what is equal.

## Residual risks, stated

- **Not a sandbox.** The systems run with the user's permissions; a
  malicious program can do anything the user can, including reaching the
  network on its own. INVARA controls what it *observes* and what *it*
  sends, not what the program does. No report or verdict says otherwise.
- **`--reviewed-by` is a name, not a signature.** A `HUMAN_REVIEW` unit
  is accepted only with a reviewer named, and the name is recorded in the
  event and the commit trailer; INVARA cannot verify that the person
  actually looked. The report labels such acceptances as attested by a
  named person, not verified by INVARA.
- **The free loopback port is chosen, then bound by the child.** Another
  process could bind it in between (a time-of-check gap). The service is
  probed on that port only, the child's identity is recorded, and a
  service that is not the child would show as a divergence or a readiness
  failure, not as a silent pass; but the gap itself is not closed.
- **Redaction fingerprints are unsalted digest prefixes.** They let two
  secrets be told apart without storing either; a low-entropy secret could
  be recovered by guessing against its fingerprint. Treat `verify.db` as
  confidential, as the kernel already says of its own evidence.
- **Ignored files in a unit worktree are not part of the tree.** A
  gitignored file the transformer creates (a build artifact, a cache) is
  neither verified nor accepted; the accepted state is the committed tree.
- **Directory junctions can be created without privilege on Windows.**
  They are recorded as links and never entered or deleted through; what
  a junction points at is not observed.
- **Loopback is checked by host name and connected to by literal
  address.** A request is refused before it is sent when its host is not a
  loopback name, and a loopback name (`localhost`) is connected to as
  `127.0.0.1`, never through the resolver; a service that itself calls out
  is the program's behaviour, not INVARA's.
- **The kernel's public statements name this boundary.** `PRIVACY.md`,
  `SECURITY.md` and `AGENTS.md` state that the package makes
  no outbound network call and that its only sockets are loopback ones to a
  service the manifest starts. This subsystem is the reason: a free
  loopback port is chosen for a service the manifest starts, its readiness
  is polled there, and the HTTP adapter sends the declared requests there. Nothing else
  opens a socket, no destination is ever outside `127.0.0.1` / `::1`, and
  no telemetry exists. The exception those documents name is exactly the
  one stated in this paragraph.
- **Textual path canonicalization.** The built-in policy replaces the
  workspace and root strings; a program that prints them in an unusual
  encoding (URL-escaped, uppercased on a case-insensitive filesystem)
  would show a divergence, never hide one.
- **Evidence is unencrypted.** `verify.db` is a plain SQLite file. The
  chains and content addresses detect edits; they do not prevent them.
- **Manifest `env.set` values are stored.** They are part of the manifest
  and therefore of the evidence. Put secrets in the environment allowlist,
  not in the manifest — the same rule the kernel gives for commands.
- **Symlink creation on Windows** needs a privilege the user may not
  have; INVARA never creates one and records those it finds.
- **Resource exhaustion.** Capture, recorded divergences, planned
  executions (a preflight estimate against `budgets.max_planned_runs`) and
  the blind-spot scan are bounded, and runs time out; disk use by the
  program inside its workspace is not limited.
- **Concurrency.** Two sessions on one repository are separated by session
  id (refs, workspaces, evidence keys); two processes driving the *same*
  session concurrently are not supported and would be reported by the
  compare-and-swap on the accepted ref or by a stale tree, not prevented
  earlier.
