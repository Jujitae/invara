# INV-003R package-surface remediation evidence

## Candidate contract

Candidate version: `0.1.3` (not published).

The release candidate is a wheel built from this commit, not a fetch from
PyPI. The proof installs it into a newly-created virtual environment with
`pip install --no-index --no-deps <wheel>` so a cached or already-installed
`invara` cannot satisfy the measurement. It drives `python -m invara.mcp` as
newline-delimited JSON-RPC and requires exactly these six MCP tools:

```text
invara_chain
invara_judge
invara_list
invara_log
invara_replay
invara_seal
```

For each measured package surface it requires, in order: `seal`, committed
`judge` (`PASS`, decided by `passed`), `list` (`PASS`), `log` (one `PASS`),
`chain` (`ok: true`), and `replay` (`matches: true`). The same smoke is also
run against the bundled plugin source. The candidate fails if the two sorted
tool lists differ.

## Reproduction commands

```bash
python -m pytest tests -q
python -m pip wheel --no-deps . --wheel-dir .runtime/invara-dist
python scripts/verify_fresh_install.py --wheel-dir .runtime/invara-dist --plugin-root plugin
```

The `fresh-package` CI job runs the last two commands independently on
`ubuntu-latest` and `windows-latest`. It is deliberately separate from the
editable-install test job: source-level tests cannot prove which source files
went into a distributable wheel.

## Boundary

This document records an internal release-candidate proof only. No package
upload, registry submission, marketplace action, or public release is part of
this task. The Founder remains the only actor who may publish `0.1.3` after
the candidate's independent Windows and Unix-family CI proofs pass.
