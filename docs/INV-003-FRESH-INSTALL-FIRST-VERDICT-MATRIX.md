# INV-003 fresh-install / first-verdict matrix

Measured 2026-09-02 in the dedicated fresh worktree. Virtual environments,
caches, temporary smoke roots, wheels, and verdict databases were all under
`.runtime/`, which is ignored by Git. Network use was limited to read-only
retrieval of `invara==0.1.3` from PyPI; no credentials, upload, or external
write was used.

## Contract used for the smoke

[`INV-003-mcp-smoke-task.json`](INV-003-mcp-smoke-task.json) protects
`README.md` and has one deterministic `python -c` completion check. Each MCP
smoke sealed a fresh equivalent contract before judging. Every successful
judge below kept its protected file unchanged, returned `PASS` decided by
`passed`, and recorded a matching replay.

## Environment inventory

| Item | Observed value | stderr / result |
| --- | --- | --- |
| Windows host | MCP's standard-library platform probe: `Windows-11-10.0.26200-SP0`; `cmd /c ver`: `10.0.26200.9168` | none |
| Host Python | `Python 3.12.10` | none |
| uvx | `uvx 0.12.5 (210d1f678 2026-08-14 x86_64-pc-windows-msvc)` | none |
| Unix-family environment | `wsl --list --quiet` exited `0` with empty output | no installed WSL distribution; no Unix-family smoke was available |

## Results

| Surface | Clean-environment command(s) | Version / observed result | stderr | Outcome |
| --- | --- | --- | --- | --- |
| PyPI / pip CLI | `python -m venv .runtime/inv003-win312-pypi-20260902`; `.../Scripts/python.exe -m pip install --no-cache-dir invara==0.1.3`; `... -m invara list` | Python `3.12.10`; installed `invara 0.1.3`; list returned `nothing sealed yet`. | pip's available-upgrade notice only | PASS |
| PyPI MCP | Installed venv's `python -m invara.mcp`, newline-delimited JSON-RPC | Initialized as `0.1.3`; exact six tools returned: seal, judge, list, log, chain, replay. Seal and committed judge returned `PASS`; chain was `ok: true`; replay matched. | empty | PASS |
| uvx CLI, exact README entry point | `uvx --cache-dir .runtime/inv003-uv-cache-20260902 invara list` | returned `nothing sealed yet`. | empty | PASS |
| uvx MCP, server.json runtime | `uvx --cache-dir .runtime/inv003-uv-cache-20260902 --from invara==0.1.3 invara-mcp`, newline-delimited JSON-RPC | Initialized as `0.1.3`; all six tools returned. Seal, committed judge, list, log, chain, and replay succeeded; judge was `PASS`, chain `ok: true`, replay matched. | empty | PASS |
| Bundled plugin, clean `python -m` | New Python 3.12 venv with only `PYTHONPATH=plugin/src`; `python -m invara.mcp`, newline-delimited JSON-RPC | Exact six tools returned; seal, committed judge, list, log, chain, and replay all succeeded; judge was `PASS`, chain `ok: true`, replay matched. MCP initialized as `0+unknown`. | empty | **BLOCK** — version identity mismatch |
| Built candidate wheel (supplementary release proof) | `python -m pip wheel --no-deps . --wheel-dir .runtime/invara-dist-20260902`; `python scripts/verify_fresh_install.py --wheel-dir .runtime/invara-dist-20260902 --plugin-root plugin` | Fresh no-index wheel install initialized as `0.1.3`; wheel and bundled plugin both exposed the exact six tools and completed the full PASS/chain/replay sequence. | pip's available-upgrade notice only | PASS |
| Unix-family install/start/tool smoke | Not run: no installed WSL distribution. | No independent Unix-family result exists. | WSL inventory above. | **UNVERIFIABLE** |

## Verdict and follow-up

The PyPI and uvx surfaces now agree on version `0.1.3` and the six-tool MCP
contract. The clean bundled plugin does execute the same six-tool, first-
verdict flow, but its `serverInfo.version` is `0+unknown`: the source bundle
has no installed distribution metadata for `importlib.metadata.version()` to
read. This conflicts with the `0.1.3` identity advertised by `server.json`
and PyPI, so the command/version-discrepancy count is not zero.

Do not treat this matrix as green. Create a separate remediation task to give
the bundled plugin a deterministic version identity (without weakening the
clean-environment check), and run this matrix on an independently provisioned
Unix-family runner. Neither failure was changed in this task.

## Repository verification

Ran `python -m pytest tests -q`: **78 passed in 3.58s**. Ran `git diff
--check` before documentation changes with no whitespace errors; it is rerun
after the documentation update before committing. The source-level suite and
the built-wheel proof do not override the plugin-version discrepancy or the
missing Unix-family evidence.
