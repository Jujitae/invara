---
description: Diagnose why the INVARA MCP server will not start (missing Python / Windows Store python alias)
---

The INVARA MCP server is spawned as `python -m invara.mcp`. When it fails to
connect, the cause is almost always the machine's `python`, not INVARA — and on
Windows that failure can be silent. Diagnose it for the user and tell them, in
their language, exactly what to install. Do not assume Python exists; none of
the steps below require it.

## Steps

1. Find what `python` resolves to.
   - Windows (PowerShell): `Get-Command python | Select-Object Source`
   - macOS/Linux: `command -v python || command -v python3`

2. Interpret:
   - **No `python` at all** → Python is not installed. Go to step 4.
   - **Windows: the source is under `...\Microsoft\WindowsApps\python.exe`**
     → this is the Microsoft Store app-execution alias, not Python. Confirm
     with step 3. This is the known silent case (INV-011).
   - A real installation path → run `python --version`; if it prints < 3.12,
     the interpreter is below INVARA's floor. Go to step 4.

3. Confirm the Store alias (Windows). Run:
   `python --version; $LASTEXITCODE`
   Measured signature of the alias when Python is not installed: **exit code
   9009** (or 49 through an 8-bit truncating layer), **stderr of exactly
   7 bytes: `Python `**, nothing on stdout. If you see this, Python is not
   actually installed on this machine — the alias only pretends.

4. Tell the user, bilingually (English + the user's language), all three of:
   - **What is missing**: Python 3.12+ is not installed (or the Microsoft
     Store alias is shadowing it).
   - **How to fix it**: install from https://www.python.org/downloads/ (on
     Windows also: `winget install Python.Python.3.12`), or disable the alias
     under Settings > Apps > Advanced app settings > App execution aliases.
     During python.org installation, check "Add python.exe to PATH".
   - **Whose fault it is not**: this failure happens before any INVARA code
     runs; it is the environment, not an INVARA defect.

5. After the fix, ask the user to restart Claude Code so the plugin respawns
   the server, then verify with `/mcp` that `invara` is connected.
