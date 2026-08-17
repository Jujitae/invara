# Security

## Reporting

Report anything security-relevant to **hello@migaryos.com**. Please do not
open a public issue for a vulnerability first.

This is a v0.1 alpha maintained by one person. There is no SLA. What you will
get is an acknowledgement and an honest answer about whether and when it will
be fixed.

## The threat model, stated plainly

INVARA runs commands you put in a contract, with your permissions. It is
**not a sandbox** and does not try to be one.

- **Sealing a contract you have not read is equivalent to running a script you
  have not read.** `done_when[].command` is executed as given.
- **A contract is stored verbatim, including its commands.** That is
  deliberate — a contract that could be rewritten after sealing would prove
  nothing — but it means a secret pasted into a command lands in `verify.db`.
- **The verdict is only as strong as the contract.** A weak set of checks
  earns a `PASS` that means very little, and the stored contract is how a
  reader sees that.
- **`verify.db` is local and unencrypted.** It is a plain SQLite file.

## What it does not do

No network access. No model calls. No credential reads. No telemetry. The
package declares zero runtime dependencies and imports only the standard
library, so the supply-chain surface is Python itself.
