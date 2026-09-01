<!-- mcp-name: io.github.Jujitae/invara -->
# INVARA

**Engineering invariants for AI-built software.**

> **INVARA upgrades vibe-coded software into verifiable, engineering-grade
> software.**

You asked an agent to do a piece of work. It says it is done. INVARA decides
that independently, from exit codes and file bytes, and writes the verdict into
a hash chain so the answer cannot be quietly revised later.

**The agent's own report is not an input.** There is no field in a contract
where anything can assert that the work is finished.

```text
Founder intent
    ↓
Sealed specification and constraints
    ↓
Builder agent (Claude Code / Codex / another agent)
    ↓
Code, files, database changes, commands, runtime effects
    ↓
Independent INVARA verifier
    ↓
Observable evidence and provenance
    ↓
BLOCK / UNVERIFIABLE / HUMAN_REVIEW / PASS
    ↓
Human or policy-controlled authority
```

Status: **v0.1 / Alpha.** It is dogfooded daily and has not been sold. It is
not autonomous authorization and not an enterprise compliance control.

---

## What this is not

Read this part before the install line. It is short on purpose, and the word
that carries the promise above is *verifiable* — not *correct*.

> **INVARA does not prove software correctness.**
> **It verifies declared constraints against observable evidence.**

Specifically:

- **It does not inspect your codebase.** It never asks whether the code is
  good, whether the architecture holds, or whether the tests are the right
  tests. Stack a new floor on junk and, if this floor was built to the
  declared spec, the verdict is `PASS`.
- **It does not find bugs.** A completion check that runs your suite is only as
  strong as your suite. INVARA reports the exit code; it does not have an
  opinion about coverage.
- **It does not decide what should have been promised.** You write the
  contract. A weak contract earns a weak `PASS`, and the contract is stored
  verbatim so anyone can see how weak it was.
- **It is not a sandbox.** Completion checks are commands and they run with
  your permissions. Do not seal a contract you have not read.
- **It does not judge intent.** `intent` is prose; nothing checks that the
  work matched it. The checks are what bind.
- **It is not an AI code generator and not an LLM code-review bot.** There is
  no model anywhere in the verdict path.

The narrow claim, which is what the machine actually does:

> **It decides, independently, whether this change kept the promises it made.**

---

## Install and first verdict, in five minutes

Nothing to configure. No API key, no service, no account. Python 3.12+ and uv.

```bash
uvx invara list
```

or, to keep it:

```bash
pip install invara
```

The package declares **zero runtime dependencies**, so this pulls only the
standard library. `pip install invara` reports the installed INVARA version,
and `pip list` shows no additional runtime packages.

From a checkout it is the same program, but install it first — the source
lives under `src/`, so a bare `python -m invara` in the repository root
finds nothing to run:

```bash
pip install -e .
python -m invara list
```

### Release-candidate verification before publish

The bundled plugin and the distributable package must expose the same six MCP
tools. Before a maintainer publishes a new package version, build its wheel and
run the repository's clean-environment proof. It creates a fresh virtual
environment, installs only that wheel without using an index, starts the MCP
server over stdio, verifies the exact tool list, then seals, judges, lists,
logs, rebuilds the chain, and replays a verdict.

```bash
python -m pip wheel --no-index --no-deps --no-build-isolation . --wheel-dir .runtime/invara-dist
python scripts/verify_fresh_install.py --wheel-dir .runtime/invara-dist --plugin-root plugin
```

This is a release-candidate proof, not a publish command. The CI workflow runs
it independently on Windows and Ubuntu for every candidate pull request.

### 1. Write the contract *before* the work

`task.json`, next to the repository you are about to change:

```json
{
  "task_id": "2026-08-17-tidy-the-parser",
  "intent": "Speed up the CSV parser without changing what it accepts",
  "constraints": [
    {
      "kind": "paths_unchanged",
      "paths": ["tests/test_parser.py"],
      "reason": "a speedup that edits its own test is not a speedup"
    }
  ],
  "done_when": [
    {
      "id": "suite",
      "command": ["python", "-m", "pytest", "-q"],
      "expect_exit": 0,
      "reason": "the whole suite"
    }
  ]
}
```

```bash
invara seal task.json
```

Sealing takes the digests of the protected paths **now**, before anyone knows
what the verdict will be. That ordering is the entire guarantee.

### 2. Do the work.

### 3. Judge

```bash
invara judge 2026-08-17-tidy-the-parser            # dry run
invara judge 2026-08-17-tidy-the-parser --commit   # record it
```

```
  BLOCK: 1 protected path(s) changed: tests/test_parser.py: changed
         (a speedup that edits its own test is not a speedup)
  decided by: constraint_breaks
```

That is the first verdict. Everything below is detail.

---

## The four verdicts

| Verdict | Meaning | Exit |
|---|---|---|
| `BLOCK` | A protected path changed, or a completion check failed | 1 |
| `UNVERIFIABLE` | A check could not be run at all. **Unchecked is not passed** | 2 |
| `HUMAN_REVIEW` | Machine checks passed; something was declared as needing eyes | 0 |
| `PASS` | Every check returned what it promised, every protected path is byte-identical | 0 |

Constraint breaks outrank everything. A run that touched what it promised not
to touch is not partially fine.

`BLOCK` is two rules wearing one word — a protected path that changed and a
check that came back wrong are not the same accusation. So the verdict also
records which rule decided it, named after the evidence it decided on
(`constraint_breaks`, `failed`, `unrunnable`, `needs_human`, `passed`), and
`judge` and `log` print it. Verdicts recorded before this existed do not have
one, and do not get one fitted after the fact.

## Sealing refuses more than it accepts

`seal` will not write a contract that cannot fail the work. It refuses a task
with no completion condition, a condition with no command to check it, a
contract with no protected paths ("a task allowed to change anything cannot be
said to have respected anything"), a duplicate check id, a protected path that
does not exist, and — the one that matters most — a contract where **every**
condition defers to a person.

That last one is why this is not a rubber stamp: if the only evidence is
somebody saying yes, there is no contract.

## Other commands

```
invara init              print a task.json template for this repository
invara list              sealed tasks and their latest verdict
invara log    <task_id>  every verdict this task has ever had
invara show   <task_id>  the contract, exactly as sealed
invara replay <task_id>  recompute a recorded verdict and compare it
invara chain             rebuild both hash chains
```

`invara replay` is the one that answers "was this verdict reproducible?" — it
recomputes from the observations stored at the time. A verdict recorded before
replay support refuses with `no_current_stored` rather than guessing.

Verdicts live in `.runtime/verify.db` (`--db` to move it). A contract is sealed
once and judged many times; the history is append-only and chained.

---

## Inside the editor

The buyer this was built for does not open a terminal. So the same package
ships an MCP server, self-contained as a Claude Code plugin:

```bash
/plugin marketplace add Jujitae/invara
/plugin install invara
```

The plugin bundles the INVARA source and runs via `python -m`, requiring only
Python 3.12+ — no uv, no network, zero runtime dependencies. If the server
will not start, `/invara:doctor` diagnoses the environment without assuming
Python exists — the known silent case is Windows without Python, where the
Microsoft Store's `python` alias spawns and dies with exit 9009 and seven
bytes of stderr (`Python `); that death happens before any INVARA code runs.
The configuration the plugin writes looks like this:

```json
{
  "mcpServers": {
    "invara": {
      "type": "stdio",
      "command": "python",
      "env": {
        "PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/src"
      },
      "args": ["-m", "invara.mcp"]
    }
  }
}
```

Six tools — `invara_seal`, `invara_judge`, `invara_list`, `invara_log`,
`invara_chain`, `invara_replay`. They are the commands above, and they change
nothing about what a verdict is: it is still computed from file digests and
exit codes, the checks that run are the ones a sealed contract already named,
and there is still no field anywhere for an agent to assert that the work is
done. An agent can ask for a verdict here. It cannot give one.

`invara` there is a local label — call it what you like. The server's actual
identity is `io.github.Jujitae/invara`, which is how it is listed in the
[MCP registry][registry] and how a client that resolves through the registry
will find it. It cannot be used as the key above: `claude mcp add` answers
`Names can only contain letters, numbers, hyphens, and underscores`.

[registry]: https://registry.modelcontextprotocol.io/v0/servers?search=invara

Not a sandbox, and this does not make it one. `invara_seal` takes a task file,
that file names commands, and judging runs them. An agent that can write a
task file can cause those commands to run — which is no more than the shell it
already has, but better said here than discovered.

---

## Determinism

Same repository state, same contract, same environment → same verdict. The
verdict is a function of file digests and command exit codes, and nothing else;
there is no model in the path and no clock in the decision.

Measured rather than asserted, 2026-08-17: contract
`2026-08-17-route-discovery` was judged twice, four minutes apart, against an
unchanged tree — 7 checks each including a 2,021-test suite. Both runs returned
the same status and the same reason string, and both rows are in the chain:

```
$ invara log 2026-08-17-route-discovery
2026-08-17 09:17 UTC  PASS
    7 check(s) passed and 6 protected path(s) are unchanged
2026-08-17 09:21 UTC  PASS
    7 check(s) passed and 6 protected path(s) are unchanged
```

Run that yourself on your own contract before you trust it on ours.

The word doing work there is **environment**. See the first failure story
below: a contract that passed locally and failed in CI was not
non-deterministic — it was two different environments, and INVARA reported
each one correctly. If you want the verdict to be reproducible, make the
completion commands reproducible.

---

## Failure stories

These are real, from building and using this tool. They are here because a
verification tool that only shows its successes is asking to be trusted on
exactly the grounds it tells you not to trust anything.

**Environment contamination.** A contract passed on the machine that wrote it
and failed in CI. The tree was clean; the *environment* was not.
`PYTHONIOENCODING` was set in the shell, child processes inherited it, and the
completion command only worked because of it. Verification runs now use
`env -u PYTHONIOENCODING`. Clean tree is not clean environment.

**A seal that broke itself.** A fresh clone rewrote a sealed ontology file to
CRLF on checkout, so its SHA-256 no longer matched. The file whose entire job
was to prove nothing had changed failed its own seal. Fixed in
`.gitattributes`, not in the digest rule — the digest was right.

**The first verdict was `UNVERIFIABLE`, and it was correct.** A completion
command could not be found, because `subprocess` does not use `cwd` to resolve
the executable. The verdict was right and the tool was useless. Both facts are
recorded; only one of them was a bug.

**It did not pass the work that built it.** The first contract INVARA ever
sealed was the task of building it. It returned `UNVERIFIABLE`, then
`PASS`, then `PASS`. All three are still in `.runtime/verify.db`, and
`invara log` prints them.

---

## Where it came from

The shape is lifted from a sibling engine in the same private repository:
seal the contract
before the evidence exists, refuse to create anything you cannot kill, score
only observed records, and chain the result. Change "world claim" to "agent's
work" and the same machine applies.

INVARA was built and is dogfooded inside a private working repository (WIE);
see [`PROVENANCE.md`](PROVENANCE.md) for what that means for this source.
