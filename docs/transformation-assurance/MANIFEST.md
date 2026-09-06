# Equivalence Manifest — schema `invara.assurance.manifest/1`

The manifest is the declaration of what "the same behaviour" means for one
transformation. It is validated on load, every default is filled in, and
the digest of the resulting canonical JSON (SHA-256, sorted keys, no
whitespace, `ensure_ascii=False`) is the identity every claim result carries.
Two manifests that differ in anything the comparison depends on have
different digests; a result computed under one does not count under the
other.

Validation fails closed. An unknown field anywhere, a mistyped policy kind,
a selector that does not parse — each is a refusal with a stable reason
slug, never a silently dropped rule. The implementation is
`src/invara/assurance/manifest.py`; the refusals are tested in
`tests/assurance/test_manifest.py`.

## Top level

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | yes | exactly `invara.assurance.manifest/1` |
| `session_id` | yes | slug `[A-Za-z0-9][A-Za-z0-9._-]*`; names the session and the git ref of a repair |
| `title` | no | free text for the report |
| `source_system` | yes | the BEFORE system (below) |
| `target_system` | yes | the AFTER system, or `{"same_as_source": true}` (optionally with `id`, `root`) |
| `provenance` | no | free JSON the author wants carried into every report (ticket, commit, tool) |
| `input_domain` | yes | how inputs are delivered and which inputs exist (below) |
| `probes` | yes | at least one; at least one `mandatory` |
| `policies` | no | relaxations of exact comparison (see `NORMALIZATION.md`) |
| `claims` | yes | at least one `mandatory` claim (see `CLAIMS.md`) |
| `budgets` | no | `search_runs` 200, `search_seconds` 120, `finite_max_members` 10000, `stability_runs` 2, `shrink_steps` 500 |
| `timeouts` | no | `run_seconds` 60, `service_ready_seconds` 20 |
| `performance` | no | `kind` `wall_clock`, `rel_tolerance` 0.5, `abs_tolerance_s` 0.25, `runs` 1 |
| `exclusions` | no | `{id, path, reason}`: divergences under `path` are informational, and the exclusion is reported as "not verified" |
| `human_review` | no | `{id, reason}`: items a person must look at; the verdict is at most `HUMAN_REVIEW` while any exists |

## System

```json
{"id": "before", "kind": "process", "command": ["python", "app.py"], "root": "$SOURCE_ROOT",
 "env": {"allow": ["PATH", "SYSTEMROOT"], "set": {"APP_MODE": "test"}},
 "timeout_s": 30, "capture_limit_bytes": 1048576,
 "service": {"ready": {"http": "/health"}, "port_env": "PORT"}}
```

- `command` is an argv list. A string is refused: nothing is ever passed to
  a shell.
- `root` is the working directory; it may use `$SOURCE_ROOT`,
  `$TARGET_ROOT`, `$WORKSPACE`, `$PORT`. The session resolves the roots.
- `env.allow` is the allowlist of variables the child may inherit. The
  default is the minimum that starts an interpreter on both platforms:
  `PATH SYSTEMROOT SYSTEMDRIVE WINDIR PATHEXT COMSPEC TEMP TMP HOME USERPROFILE`.
  Nothing else from the parent reaches the child.
- `env.set` are values imposed on the child and recorded as controlled.
- `kind: "service"` starts the command once per run, waits for `ready`
  (`{"http": path}` or `{"tcp": true}`) on a free loopback port passed as
  `$PORT` and the `port_env` variable, sends the HTTP probe's requests, and
  stops the process.

## Input domain

```json
{"kind": "corpus", "delivery": "stdin_json",
 "corpus": [{"id": "c1", "input": {...}, "initial_state": {"files": {"seed.txt": "..."}, "sqlite": {"path": "app.db", "sql": ["CREATE TABLE ..."]}}}]}
```

- `delivery`: `stdin_json` (the input as JSON on stdin), `argv_json` (as the
  last argument), `file_json` (written to `$WORKSPACE/input.json`, path in
  `INVARA_INPUT`), `http` (the input is `{"requests": [...]}` sent to the
  service).
- `initial_state` seeds the fresh workspace before the run: text or
  base64 files, and SQL executed against a database path, both inside the
  workspace only.
- `kind: "finite"` declares a cartesian product instead of, or in addition
  to, a corpus: `{"finite": {"parameters": {"zone": {"range": [1, 5]}, "express": [false, true]}}}`.
  Members are enumerated in declared parameter order with positional ids
  `f-0001…`; the domain digest ties ids to values; a parameter names each
  value once (a repeated value is refused, `bad_finite_domain`; `1` and
  `1.0`, `true` and `1` are distinct values). A corpus item may sit
  beside the members but never in a member's place: a corpus id that names
  a member of the declared domain is refused (`duplicate_input`), and the
  verifier binds every execution to the digest of its exact input and
  initial state, never to the id, so a record captured from another input
  is never reused as a member's baseline.

## Probes

| adapter | fields | observes |
|---|---|---|
| `process` | `capture` (subset of `stdout`, `stderr`; default both) | `exit_code`, captured streams, truncation flags and full-output digests |
| `json` | `source` (`stdout`, `stderr`, `file`) and `path` for `file` | `{"value": parsed}` or `{"parse_error", "text_digest", "text_head"}` (a failed observation, not the declared value: left out of that input's comparison, which proceeds on the other declared observables and is `unverifiable` when no mandatory one remains; on every baseline input refused at freeze; named in the report) or `{"missing": true}` |
| `filesystem` | `root`, `include` (globs, default `["**"]`; `*` within a segment, `**` across), `content` (`digest` or `text`), `max_text_bytes`, `max_entries` | `entries` (size, digest, optional text; symlinks and junctions recorded, never followed), `created`, `removed`, `entries_omitted` |
| `sqlite` | `path`, `tables` (`name`, `order` `ordered`/`unordered`, `key`, `max_rows`) | per table: `schema`, `columns`, `row_order`, `rows`, `rows_omitted`; `{"missing": true}` for an absent database or table |
| `http` | `requests` (`"$INPUT"` or a list), `headers` to keep | per request: status, kept headers, body, parsed `json`, or an error; only lowercase `http://` with literal `127.0.0.1` or `[::1]`, or a relative origin-form `path`, is supported |

HTTP request declarations are validated before the service starts. An absolute
URL uses its explicit port, or HTTP port 80 if omitted, and that port must match
the service port allocated for this run. Prefer `{"path": "/items"}` to address
the allocated service port. HTTPS and other schemes, mixed-case schemes,
userinfo, fragments, ambiguous authorities or targets, and routing/framing
header overrides are refused. Redirects are recorded without being followed;
proxy settings are not used. Readiness HTTP targets obey the same path rules.

The raw observation binds each declaration's digest to the executed HTTP
scheme, literal host, effective port, method, request target and URL in
`http_request_identities`; it retains `http_service_port` and
`http_request_declarations` alongside the response evidence. These fields are
validated before freeze, comparison and offline package inspection. Actual
allocated ports stay in raw provenance rather than behavioral comparison,
because each run starts a fresh service. Legacy HTTP observations lacking this
identity cannot support a new comparison. No TLS behavior is claimed.

Probe roots and paths must lie inside the workspace or the system root;
anything else is `malformed`. A probe with `mandatory: true` makes its
divergences mandatory and its absence a refusal at freeze.

## Budgets

Every budget is part of the manifest and therefore of its digest; raising
one is a recorded decision. `search_runs`, `search_seconds`,
`finite_max_members`, `stability_runs`, `shrink_steps` bound the search,
the proof and the capture. `finite_max_members` also decides, when
`characterize` runs, which finite members enter the baseline: raising it
afterwards (by amendment, before or after the freeze) enumerates more
members for the proof but adds none to the frozen baseline, so the proof
over them is `UNVERIFIABLE`; the source is never run again after the
freeze to stand in for a frozen record. A domain that is to be proved is
captured under a sufficient budget, in a new session if need be. Three
bound work before it starts and evidence
before it is written: `max_planned_runs` (default 2,000) is checked by a
preflight estimate before a characterize, compare, search, proof,
performance measurement or unit verification launches its first execution
(`budget_exceeded` otherwise, nothing run); `max_divergences` (default 200)
is how many divergences one comparison, and one claim, record in full,
the rest being counted as `divergences_omitted`; `sensitivity_max_leaves`
(default 500) bounds the blind-spot scan in sites (a value on one input, or
a list the order pass reverses), every pass charged to the same budget, and
`sensitivity_max_work` (default 2,000,000 leaf visits) bounds its work:
every comparison visits the whole record, so a large record shrinks the
site budget and the scan says so (`budget_reason`). A scan is computed once
per manifest and baseline; an interrupted freeze does not pay for it twice.
`max_divergences` also bounds the normalizer's action log kept in a
comparison or a frozen record (`actions_omitted` counts the rest). A
one-shot `run` estimates every phase it will execute together before its
first execution, so steps that fit one by one but not as a whole are
refused up front. Capture is bounded in bytes as well as in count: a
filesystem probe's texts (`max_bytes`, default 8 MiB per tree; a file
beyond the budget keeps its digest and loses its text, `text_limited`) and
a sqlite table's rows (`tables[].max_bytes`, default 8 MiB per table;
`rows_omitted` and `bytes_limited` say what was cut). Every cut is a
partial capture on the coverage map. The
preflight estimate counts what a phase really starts: a search run or a
shrink step executes both systems, a performance measurement runs both
systems on every corpus input for every run, and a claim's own `params.runs`
(or an explicit override) is what is counted. A budget of zero is refused.

## Amendments

An amendment is a JSON object `{"requested_by", "reason", "changes": {section: value}}`
applied with `invara assure amend` / `invara repair amend`. Only
`policies`, `exclusions`, `budgets`, `timeouts`, `performance`,
`human_review` and `claims` may change; systems, probes and the input
domain define a session and changing them means a new one. Sections are
replaced whole, the result is validated like any manifest, the new policy
set is checked against the stored baseline exactly as at freeze (signal
erasure, overbroad tolerances), an amendment that changes nothing is
refused, and the record names the old and new digests and every policy
and exclusion added, removed or changed, every mandatory claim removed or
demoted, and every human-review item removed. Claim results recorded so far
under the old digest are invalidated, by event order: an amendment that
brings the digest back to an earlier one does not resurrect the results it
had invalidated, and a result evaluated after the return counts; the
stability claim is re-derived from the stored
runs under the new policies; any added or changed policy or exclusion
covering a path where a divergence was already recorded (or an ancestor,
or the raw path of a sorted element), and any removed or demoted mandatory
claim or removed review item, is flagged `post_divergence`, which holds
the session's final verdict at `HUMAN_REVIEW` for good. Claims must keep
one mandatory claim that compares the target and at most one claim per
kind, so an amendment cannot demote the only comparing claim.
