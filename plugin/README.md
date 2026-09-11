# INVARA — Claude Code plugin

Independent verification that a change kept the constraints it declared.

This plugin ships with the public INVARA core 0.3.0 generation. The MCP
Registry identity is `io.github.Jujitae/invara`; its descriptor and bundled
source are released together with the core package.

You ask an agent to do a piece of work. It says it is done. INVARA decides that
independently, from exit codes and file bytes, and writes the verdict into a
hash chain so the answer cannot be quietly revised later.

**The agent's own report is not an input.** There is no field in a contract
where anything can assert that the work is finished — including the agent that
just called this tool.

## What it adds

Six tools: `invara_seal`, `invara_judge`, `invara_list`, `invara_log`,
`invara_chain`, `invara_replay`.

The order is the whole guarantee. `invara_seal` takes the digests of the
protected paths **before** the work, and `invara_judge` compares them after.

```
BLOCK          a protected path changed, or a completion check failed
UNVERIFIABLE   a check could not be run at all — unchecked is not passed
HUMAN_REVIEW   machine checks passed; the contract asked for a person
PASS           every check returned what it promised
```

Every verdict also names the rule that decided it, so it can be argued with.

## Commands

Three slash commands, all running the bundled package locally and offline:

- `/invara:doctor` — diagnose why the MCP server will not start.
- `/invara:repair` — "기능은 그대로 두고 이 프로젝트를 제대로 정리해줘." A
  governed repair session: INVARA captures and freezes what the program
  does, the agent repairs one unit at a time in a disposable worktree, and
  every unit is verified against the frozen behaviour before it can be
  kept. The agent cannot declare its own unit accepted.
- `/invara:assure` — an existing BEFORE and AFTER, compared under a declared
  Equivalence Manifest: corpus comparison, counterexample search with a
  minimized reproducer, exhaustive proof over a declared finite domain, and
  a report that leads with plain language (기능 유지 / 성능 유지 / 정리 완료
  항목 / 되돌린 변경 / 확인하지 못한 영역 / 다음에 사람이 볼 것).

The two assurance commands drive `python -m invara assure` and
`python -m invara repair`; the six MCP tools above are unchanged.

## Install

Requires Python 3.12+.

The plugin is self-contained. Install from the Claude Code plugin marketplace:

```
/plugin marketplace add Jujitae/invara
/plugin install invara
```

Zero mandatory runtime dependencies, by design. Nothing in the verdict path
calls a model, an external service, or an outbound network endpoint. An
assurance manifest may start a local service for the session; HTTP probes can
connect only to that service over loopback (`127.0.0.1`).

## If the server will not start

Run `/invara:doctor` — it diagnoses the environment without assuming Python
exists.

The one silent case is Windows without Python: the Microsoft Store puts a
`python` alias on PATH that exists, spawns, and dies with **exit code 9009**
(49 through layers that truncate to 8 bits) and **7 bytes of stderr,
`Python `**. That failure happens before any INVARA code runs, so nothing
INVARA prints can reach it — it is the environment, not this plugin. Fix:
install Python 3.12+ from <https://www.python.org/downloads/> (or
`winget install Python.Python.3.12`), or disable the alias under Settings >
Apps > App execution aliases. A Python older than 3.12 is not silent: the
server refuses to start with a message naming the floor and the fix, in
English and Korean.

## What this is not

- It does not prove software correctness. It verifies declared constraints
  against observable evidence.
- It does not inspect your codebase or find bugs.
- **It is not a sandbox.** Completion checks are commands and they run with
  your permissions. `invara_seal` takes a task file, that file names commands,
  and judging runs them.

Source and the full README: <https://github.com/Jujitae/invara>
