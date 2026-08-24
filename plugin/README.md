# INVARA — Claude Code plugin

Independent verification that a change kept the constraints it declared.

You ask an agent to do a piece of work. It says it is done. INVARA decides that
independently, from exit codes and file bytes, and writes the verdict into a
hash chain so the answer cannot be quietly revised later.

**The agent's own report is not an input.** There is no field in a contract
where anything can assert that the work is finished — including the agent that
just called this tool.

## What it adds

Five tools: `invara_seal`, `invara_judge`, `invara_list`, `invara_log`,
`invara_chain`.

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

Requires Python 3.12+ and uv.

The plugin declares the MCP server; the server itself comes from PyPI:

```bash
uvx --from invara invara-mcp
```

Zero runtime dependencies, by design and permanently — nothing in the verdict
path is a model, a service, or a network call.

## What this is not

- It does not prove software correctness. It verifies declared constraints
  against observable evidence.
- It does not inspect your codebase or find bugs.
- **It is not a sandbox.** Completion checks are commands and they run with
  your permissions. `invara_seal` takes a task file, that file names commands,
  and judging runs them.

Source and the full README: <https://github.com/Jujitae/invara>
