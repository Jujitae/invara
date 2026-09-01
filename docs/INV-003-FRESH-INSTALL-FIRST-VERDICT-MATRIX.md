# INV-003 fresh-install / first-verdict matrix

Measured 2026-09-01 in the dedicated fresh worktree. All caches, virtual
environments, and verdict databases used below were placed under `.runtime/`,
which is ignored by Git. Network use was limited to read-only package retrieval
from PyPI; no credentials, upload, or external write was used.

## Contract used for the smoke

[`INV-003-mcp-smoke-task.json`](INV-003-mcp-smoke-task.json) protects
`README.md` and has one deterministic `python -c` completion check. It was
sealed before each independent smoke database was judged. The check exited
zero and the protected README digest remained unchanged in every successful
judge below.

## Environment inventory

| Item | Observed value | stderr / result |
| --- | --- | --- |
| Windows host | `Microsoft Windows NT 10.0.26200.0`; `cmd /c ver`: `10.0.26200.9168` | `Get-CimInstance Win32_OperatingSystem` was denied; `Get-ComputerInfo` labels the product `Windows 10 Pro`, so the requested Windows 11 edition label was not independently verified. |
| Host Python | `Python 3.12.10` | none |
| uv | `uv 0.12.5 (210d1f678 2026-08-14 x86_64-pc-windows-msvc)` | none |
| uvx | same version as uvx | none |
| Unix-family environment | WSL probe only | `wsl --list --quiet` exited `-1`: no installed Linux distributions. Docker was present but deliberately not used because obtaining/running an image would expand network and host-container state beyond this task. |

## Results

| Surface | Clean-environment command(s) | Version / observed result | stderr | Outcome |
| --- | --- | --- | --- | --- |
| PyPI / pip CLI | `python -m venv .runtime/inv003-win312-pypi`; `.runtime/inv003-win312-pypi/Scripts/python.exe -m pip install --no-cache-dir invara==0.1.2`; `... -m invara list` | Python `3.12.10`; `invara 0.1.2`; install reported `Successfully installed invara-0.1.2`; list returned `nothing sealed yet`. | none | PASS |
| uvx CLI, exact README entry point | `UV_CACHE_DIR=.runtime/inv003-uv-cache uvx invara list` | returned `nothing sealed yet`. | `Installed 1 package in 128ms` | PASS |
| uvx seal and judge | `uvx invara seal docs/INV-003-mcp-smoke-task.json --root . --db .runtime/inv003-uvx.db`; `uvx invara judge INV-003-mcp-smoke --root . --db .runtime/inv003-uvx.db --commit`; `uvx invara list --db .runtime/inv003-uvx.db` | seal succeeded; judge was `PASS`, decided by `passed`; list recorded `PASS`. | none | PASS |
| Bundled plugin, `python -m invara.mcp` | Host Python `3.12.10`, `PYTHONPATH=plugin/src`, newline-delimited JSON-RPC over stdio | Initialized as `0.1.2`; `tools/list` returned all six: `invara_seal`, `invara_judge`, `invara_list`, `invara_log`, `invara_chain`, `invara_replay`. Seal succeeded, committed judge was `PASS`, list was `PASS`, log count was `1`, chain `ok` was `true`, replay `matches` was `true`; server exit `0`. | empty | PASS |
| PyPI 0.1.2 MCP, `python -m invara.mcp` | `.runtime/inv003-win312-pypi/Scripts/python.exe -m invara.mcp` | Initialized as `0.1.2`; the five listed tools were seal, judge, list, log, and chain. Those five calls completed successfully and the committed judge was `PASS`; `invara_replay` returned `no such tool: invara_replay` with `isError: true`; server exit `0`. | empty | **BLOCK** |
| Unix-family install/start/tool smoke | Not run: no installed WSL distribution. | No independent Unix-family result exists. | WSL diagnostic above. | **UNVERIFIABLE** |

## Verdict and follow-up

This matrix is not green. `server.json` advertises the PyPI package with
`runtimeHint: "uvx"`, while the versioned PyPI artifact `invara==0.1.2`
identifies itself as `0.1.2` but does not provide the `invara_replay` MCP tool
that the bundled plugin source provides. The same version label therefore does
not identify equivalent product surfaces. Create a separate remediation task
to publish or otherwise align the PyPI artifact, package metadata, and six-tool
MCP contract; do not relabel this measurement as passing. A separate Unix
runner (or a preinstalled WSL distribution) is also required before the
two-platform success metric can be verified.

## Repository verification

Ran `python -m pytest tests -q`: **78 passed in 3.38s**. The test suite proves
the checked-out source and plugin bundle agree; it does not override the
fresh-PyPI discrepancy measured above.
