# INVARA — Claude Code plugin

Independent verification that a change kept the constraints it declared.

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

## Install

Requires Python 3.12+.

The plugin is self-contained. Install from the Claude Code plugin marketplace:

```
/plugin marketplace add Jujitae/invara
/plugin install invara
```

Zero runtime dependencies, by design and permanently — nothing in the verdict
path is a model, a service, or a network call.

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
