# Privacy

INVARA runs on your machine. It has no network code.

## What it collects

Nothing. There is no telemetry, no analytics, no crash reporting, and no
account. `dependencies = []` in `pyproject.toml` is a product promise, and the
package contains no HTTP client, no socket use, and no outbound call of any
kind — verified 2026-08-24 by searching the package for `socket`, `urllib`,
`requests`, and any URL of an API.

## What it writes

One local SQLite file, `.runtime/verify.db`, relative to the directory you run
it in (`store.py`, `DEFAULT_PATH`). It holds the contract you sealed, the
digests of the paths you named, the exit codes of the commands you named, and
the resulting verdicts.

That file stays where it is written. Nothing uploads it, because the package
contains nothing that could.

## What it runs

The commands you wrote in your own task file, on your own machine, with your own
permissions. INVARA does not choose them and does not add to them. It records
their exit codes and the digests of the paths you listed — not their contents,
and not your source.

## Third parties

None. INVARA is distributed through PyPI and the MCP registry, and those
services see a package download the way they would for any package. INVARA
itself is never told about it.

## If this ever changes

Any hosted or networked feature would be a separate, opt-in surface, and it
would be documented here before it ships. As of 2026-08-24 no such surface
exists.

---

Apache-2.0 · https://github.com/Jujitae/invara · https://migaryos.com/invara/
