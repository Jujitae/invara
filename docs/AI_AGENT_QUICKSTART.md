# INVARA AI Agent Quickstart

INVARA works with Claude Code, Codex, and other coding agents that can use a
shell. Its job is narrow: it verifies declared constraints against observable
evidence. It does not prove software correctness, and an agent saying that
work is finished is not completion evidence.

The shared flow is:

```text
contract before work -> seal before work -> agent works -> judge
                                                    |
                  PASS / BLOCK / UNVERIFIABLE / HUMAN_REVIEW
```

## A. Claude Code

**Install/start path**

Use the bundled editor/plugin experience:

```text
/plugin marketplace add Jujitae/invara
/plugin install invara
```

If it does not start, run the optional troubleshooting command:

```text
/invara:doctor
```

**Copy-paste instruction**

```text
For this task, use INVARA before you change the requested work. Define a
contract with protected paths and runnable completion checks, seal it before
making changes, then do the work and judge it afterwards. Preserve the exact
verdict: PASS, BLOCK, UNVERIFIABLE, or HUMAN_REVIEW. Do not treat any non-PASS
verdict as success and do not use your own completion claim as evidence.
```

**Expected flow**

Use the INVARA tools to define and seal the contract before editing. After the
task, judge the sealed contract and report its exact verdict.

**If the verdict is not PASS**

`BLOCK` means a protected path changed or a completion check failed.
`UNVERIFIABLE` means a check could not run. `HUMAN_REVIEW` means the contract
requires a person after machine checks. Keep the verdict; do not weaken or
rename it.

## B. Codex

**Install/start path**

Use the public 0.1.3 package through a shell:

```bash
uvx invara list
```

Or install it persistently:

```bash
pip install invara
invara list
```

**Copy-paste instruction**

```text
Use the local INVARA CLI for this task. Before modifying the requested work,
define a task contract with protected paths and runnable completion checks and
seal it with `invara seal <task.json>`. Then perform the task. Afterwards run
`invara judge <task_id> --commit`. Preserve PASS, BLOCK, UNVERIFIABLE, or
HUMAN_REVIEW exactly. Never reinterpret a non-PASS result as success, and
never use your own claim that the work is finished as evidence.
```

**Expected flow**

Define the contract, seal it before work, make the requested changes, and
judge afterwards. With `uvx`, use the same prefix for each CLI call, for
example `uvx invara seal <task.json>` and
`uvx invara judge <task_id> --commit`.

**If the verdict is not PASS**

Stop calling it successful. Preserve the result and its evidence: `BLOCK` is
a failed check or protected-path break, `UNVERIFIABLE` is an unrunnable check,
and `HUMAN_REVIEW` still needs a person.

## C. Other shell-capable agent

**Install/start path**

Install the public 0.1.3 package in the shell the agent can use:

```bash
pip install invara
invara list
```

**Copy-paste instruction**

```text
Use INVARA for this task through the shell. Before work, define a contract
with protected paths and runnable completion checks, then run `invara seal
<task.json>` before changing the requested work. Perform the task, then run
`invara judge <task_id> --commit`. Report the exact verdict: PASS, BLOCK,
UNVERIFIABLE, or HUMAN_REVIEW. Do not hide, weaken, or reinterpret a non-PASS
verdict as success; the agent's own completion claim is not evidence.
```

**Expected flow**

The agent creates a contract, seals it before changes, does the work, and
judges the sealed contract after work.

**If the verdict is not PASS**

Preserve it. `BLOCK` is evidence of a failed declared constraint,
`UNVERIFIABLE` is not a pass, and `HUMAN_REVIEW` is awaiting the required
human decision.

This pre-pilot release has no native ChatGPT Web surface. It does not provide
remote MCP or backend functionality for that environment.
