# Observation adapter protocol

An adapter turns one run of one input against one system into a
deterministic, versioned observation record. INVARA never reads the
source language; it reads what the program does. Implementation:
`src/invara/assurance/execute.py`; tests: `tests/assurance/test_execute.py`.

JSON numeric values are captured without binary-float loss; see
[exact JSON numbers](EXACT_JSON_NUMBERS.md) for limits, type semantics and
historical-record compatibility.

## The record — `invara.assurance.observation/1`

```json
{
  "record_version": "invara.assurance.observation/1",
  "system_id": "before", "input_id": "c1",
  "status": "observed",
  "problems": [],
  "workspace": "C:/…/invara-run-x1", "root": "C:/…/before",
  "command": ["python", "app.py"], "delivery": "stdin_json",
  "program": {"path": "C:/…/python.exe", "digest": "…", "resolved": true},
  "environment": {"controlled": {"TZ": "UTC", "PYTHONHASHSEED": "0", "INVARA_INPUT": "…", "...": "..."}, "inherited": ["PATH", "SYSTEMROOT"], "allowlist": ["..."],
                  "inert": ["TZ", "LC_ALL", "LANG"],
                  "uncontrollable": ["wall_clock", "thread_scheduling", "process_scheduling", "external_services", "hardware_randomness", "child_network_and_filesystem_access"],
                  "platform": "Windows-11-…", "invara_python": "3.12.10"},
  "timing": {"wall_s": 0.21},
  "probes": {"cli": {...}, "out": {...}, "files": {...}, "db": {...}, "api": {...}},
  "redactions": 0
}
```

`status` is one of:

| status | meaning | comparison |
|---|---|---|
| `observed` | the run completed and every probe reported | compared |
| `unrunnable` | the command was not found, the root does not exist, the service never became ready, or the Windows Store `python` alias answered instead of an interpreter | unverifiable |
| `timeout` | the process exceeded `timeout_s` and was stopped; for a service, the declared requests did not all complete within it | unverifiable |
| `unverifiable` | a request left the loopback boundary | unverifiable |
| `malformed` | INVARA itself failed to observe (a probe root escaped, an adapter raised) | unverifiable |

The comparator treats every non-`observed` record as `unverifiable`; it
never infers behaviour from one. Every record, whatever its status, is
redacted before it is stored: a timeout or an adapter failure carries what
was captured before it, redacted like an observation.

## One run

1. A fresh workspace directory is created; `initial_state` files and SQL
   are seeded into it (paths that escape it are `malformed`).
2. Placeholders in the root, the command and probe paths are resolved:
   `$SOURCE_ROOT`, `$TARGET_ROOT`, `$WORKSPACE`, `$PORT`. An unresolved
   placeholder is a refusal.
3. The environment is built: allow-listed variables from the parent plus
   the controlled set (`PYTHONHASHSEED=0`, `TZ=UTC`, `LC_ALL`/`LANG=C.UTF-8`,
   `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`,
   `INVARA_WORKSPACE`, `INVARA_SEED`, `INVARA_INPUT` for file delivery, the
   port variable for a service) plus the manifest's `env.set`. The record
   lists what was controlled, what was inherited by name, what is set but
   `inert` on this platform (`TZ`, `LC_ALL`, `LANG` do not reach a Windows
   C runtime), and what cannot be controlled at all.
4. The input is delivered (stdin bytes, an extra argv, `input.json`, or
   HTTP requests) and the command runs with argv only, never a shell, in
   its own process group. A relative program with a path separator
   (`./bin/app`) is the system root's on every platform. The resolved
   executable is recorded as `program`; the command and environment are on
   record before delivery, so a delivery failure still says what would
   have run. Stdin is fed on a thread with the run's deadline, so a child
   that never reads it still times out. Output pipes are drained on
   threads; at most `capture_limit_bytes` of each is kept, the full stream
   is digested, and a truncated record says so with the total byte count.
5. A process kind is waited for up to the timeout. On Windows it starts
   suspended, is assigned to a kill-on-close job, and only then resumes;
   timeout terminates that job and verifies that its active-process count is
   zero before returning. A failure to establish that postcondition is
   `unverifiable`. Other platforms stop the process group with `killpg`.
   Partial stdout and stderr remain bounded evidence on timeout. A service
   kind is waited for readiness
   (HTTP GET or TCP connect on the free loopback port, reading a bounded
   prefix under the remaining deadline) for `service_ready_seconds`,
   probed, and stopped the same way. Cleanup runs in a `finally` that
   cannot destroy the record.
6. Each probe reports; the whole `probes` tree is passed through the secret
   redactor; the workspace is removed unless the caller keeps it.

## Adapters

**process** — `exit_code`, the captured streams named in `capture`, a
`truncated` flag per stream and, when truncated, `<stream>_digest` and
`<stream>_bytes`. For a service kind the streams are the service's.

**json** — parses `stdout`, `stderr` or a file inside the workspace or root:
`{"value": …}`. A parse failure is recorded as `{"parse_error", "text_digest",
"text_head"}`: evidence that the attempt happened and of what was written,
not the value the probe declares. A probe that obtained no value on either
side is left out of that input's comparison (its diagnostic is never
compared as behaviour): the input is compared through the other declared
observables, exit status and streams, files, tables, and is `unverifiable`
when no mandatory one remains; a failure on one side alone is a divergence;
a mandatory probe that obtained no value on any baseline input is refused
at freeze (`probe_unobserved`); the diagnostic fields are never coverage,
and the report names the inputs on which the declared value was not
obtained.
An absent file is `{"missing": true}`, an observation of absence compared
as such per input.

**filesystem** — walks the probe root (must lie inside the workspace or the
system root; never follows symlinks or junctions) in sorted order:
`entries[relpath] = {"size", "digest"}` plus `"text"` under `content:
"text"` up to `max_text_bytes`; a symlink or a Windows directory junction
is `{"symlink": true}` and is not opened; `created` and `removed` are
relative to the files present before the run; a probe whose root is
itself a link observes nothing (`root_link`); at most `max_entries`
entries are recorded and `entries_omitted` says how many were not.
`include` globs match `*` within one path segment and `**` across
segments. Relative paths use `/` on every platform.

**sqlite** — copies the database file with its `-wal` and `-shm`
companions into scratch (a service stopped mid-write leaves a WAL that a
read-only open cannot see), opens the copy read-only, reads every declared
table inside one transaction: `schema` (the `CREATE` statement),
`columns`, `rows` as column→value objects (blobs as digest and length),
`row_order` — `rowid` for `ordered`, `key:<cols>` for `unordered` with a
key, `columns` without. A table may declare `max_rows`; `rows_omitted`
records the rest. Identifiers are quoted everywhere. An absent database or
table is `{"missing": true}`. Seeded SQL (`initial_state.sqlite.sql`) is
limited to data statements by a keyword allowlist and an authorizer that
denies `ATTACH`, `DETACH` and `PRAGMA`.

**http** — for a service system, sends the requests (`$INPUT` or a declared
list) to `127.0.0.1:$PORT`: method, path, headers, a JSON or string body.
Records status, the headers named in `headers`, the bounded body text and
its parsed `json`; a malformed response is recorded as an error for that
request, not as a failure of the record. A path is sent percent-encoded
(an existing escape is kept). The run's one deadline spans every request:
when it passes, the run is `timeout`. Absolute URLs support only the exact
lowercase `http://` scheme with literal `127.0.0.1` or `[::1]`; an omitted
URL port means 80, and must match the allocated service port. Relative
origin-form paths use that allocated port. Unsupported protocols, userinfo,
fragments, ambiguous targets and routing header overrides are refused before
service launch. Redirects are never followed and proxies are never used.
The raw record retains canonical executed endpoint identity separately from
the response fields; freeze, comparison and package inspection check it
against the declaration. This records plaintext HTTP only, never TLS.
None of this isolates the child: what the service itself does with the
network is its behaviour, and it is not observed.

## Determinism control

Controlled by construction: the environment allowlist, the fixed values
above, a fresh workspace per run, sorted filesystem walks, rowid or
key-ordered SQLite snapshots, canonical JSON everywhere, no retries.
Reported but not controlled: the wall clock, thread and process
scheduling, hardware randomness, external services. A manifest handles
what is reported through declared policies; a stability run says which
paths need one. Concurrency and thread-scheduling equivalence are not
supported; a claim that depends on them is `UNVERIFIABLE` by construction
because nothing observes them.

## Adding an adapter

An adapter is a function from the run context to a JSON-shaped record for
its probe id, registered in `execute.run`'s dispatch and validated in
`manifest.Probe._check_params`. It must: produce the same record for the
same behaviour; read only inside the workspace or the system root; never
follow symlinks; bound what it captures; put failures into the record (as
`missing`, `parse_error`, `error`) rather than raising; and be covered by a
test in `tests/assurance/test_execute.py` that exercises the failure path.
