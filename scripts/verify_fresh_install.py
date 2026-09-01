#!/usr/bin/env python3
"""Prove a built INVARA wheel works as the six-tool MCP product surface.

This script must run outside the candidate package's import path.  It builds
its own temporary work directory and virtual environment, installs exactly one
provided wheel with ``--no-index``, then exercises the installed MCP process
over stdio.  It is deliberately standard-library-only so that it can run on
the same Windows and Unix-family CI runners it measures.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


EXPECTED_TOOLS = [
    "invara_chain",
    "invara_judge",
    "invara_list",
    "invara_log",
    "invara_replay",
    "invara_seal",
]


def _wheel_from(directory: Path) -> Path:
    wheels = sorted(directory.glob("invara-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one INVARA wheel in {directory}, found {wheels}")
    return wheels[0]


def _wheel_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        for line in archive.read(metadata).decode("utf-8").splitlines():
            if line.startswith("Version: "):
                return line.removeprefix("Version: ")
    raise RuntimeError(f"wheel metadata has no Version field: {wheel}")


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _run(
    command: list[str], *, input_text: str | None = None, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, input=input_text, text=True, capture_output=True, check=False, env=environment
    )


def _require(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode:
        raise RuntimeError(
            f"{label} failed with exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _payload(response: dict) -> dict:
    result = response["result"]
    if result.get("isError"):
        raise RuntimeError(f"MCP tool refused: {result}")
    return json.loads(result["content"][0]["text"])


def _smoke(
    python: Path, *, label: str, expected_version: str | None, environment: dict[str, str] | None = None
) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"invara-{label}-") as temporary:
        root = Path(temporary)
        work = root / "work"
        work.mkdir()
        (work / "guard.txt").write_text("unchanged\n", encoding="utf-8")
        database = work / "verify.db"
        task = work / "task.json"
        task.write_text(json.dumps({
            "task_id": "INV-003R-fresh-smoke",
            "intent": "Prove the installed MCP package preserves its six-tool surface",
            "constraints": [{
                "kind": "paths_unchanged", "paths": ["guard.txt"],
                "reason": "the fresh-install smoke must leave its protected file intact",
            }],
            "done_when": [{
                "id": "fresh-python-runs", "command": [str(python), "-c", "raise SystemExit(0)"],
                "expect_exit": 0, "reason": "the fresh environment can run a completion check",
            }],
        }), encoding="utf-8")

        base = {"root": str(work), "db": str(database)}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "invara_seal", "arguments": base | {"task_file": str(task)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "invara_judge", "arguments": base | {"task_id": "INV-003R-fresh-smoke", "commit": True}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "invara_list", "arguments": base}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "invara_log", "arguments": base | {"task_id": "INV-003R-fresh-smoke"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "invara_chain", "arguments": base}},
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "invara_replay", "arguments": base | {"task_id": "INV-003R-fresh-smoke"}}},
        ]
        server = _run(
            [str(python), "-m", "invara.mcp"],
            input_text="\n".join(json.dumps(request) for request in requests) + "\n",
            environment=environment,
        )
        _require(server, f"{label} MCP server")
        responses = {item["id"]: item for item in map(json.loads, server.stdout.splitlines())}
        if sorted(responses) != list(range(1, 9)):
            raise RuntimeError(f"incomplete MCP response sequence: {responses}")
        server_version = responses[1]["result"]["serverInfo"]["version"]
        if expected_version is not None and server_version != expected_version:
            raise RuntimeError(f"MCP version does not match candidate: {responses[1]}")
        tools = sorted(tool["name"] for tool in responses[2]["result"]["tools"])
        if tools != EXPECTED_TOOLS:
            raise RuntimeError(f"MCP tools differ: {tools}")

        sealed = _payload(responses[3])
        judged = _payload(responses[4])
        listed = _payload(responses[5])
        logged = _payload(responses[6])
        chained = _payload(responses[7])
        replayed = _payload(responses[8])
        if sealed.get("sealed") != "INV-003R-fresh-smoke":
            raise RuntimeError(f"seal did not record task: {sealed}")
        if judged.get("status") != "PASS" or judged.get("decided_by") != "passed":
            raise RuntimeError(f"judge did not pass: {judged}")
        if listed.get("tasks", [{}])[0].get("status") != "PASS":
            raise RuntimeError(f"list did not show PASS: {listed}")
        if len(logged.get("verdicts", [])) != 1 or logged["verdicts"][0].get("status") != "PASS":
            raise RuntimeError(f"log did not record PASS: {logged}")
        if not chained.get("ok"):
            raise RuntimeError(f"chain did not verify: {chained}")
        if not replayed.get("matches") or replayed.get("status") != "PASS":
            raise RuntimeError(f"replay did not match PASS: {replayed}")

        return {
            "surface": label,
            "platform": platform.platform(),
            "interpreter": str(python),
            "mcp_version": server_version,
            "tools": tools,
            "sequence": ["seal", "judge", "list", "log", "chain", "replay"],
            "verdict": judged["status"],
            "chain_ok": chained["ok"],
            "replay_matches": replayed["matches"],
        }


def verify(wheel: Path) -> dict:
    version = _wheel_version(wheel)
    with tempfile.TemporaryDirectory(prefix="invara-fresh-wheel-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)

        installed = _run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
        _require(installed, "fresh wheel install")
        reported = _run([str(python), "-c", "import importlib.metadata; print(importlib.metadata.version('invara'))"])
        _require(reported, "installed distribution version")
        if reported.stdout.strip() != version:
            raise RuntimeError(f"installed {reported.stdout.strip()!r}, wheel says {version!r}")

        result = _smoke(python, label="fresh-wheel", expected_version=version)
        result["wheel"] = wheel.name
        result["distribution_version"] = version
        return result


def verify_plugin(plugin_root: Path) -> dict:
    source = plugin_root / "src"
    if not (source / "invara" / "mcp.py").is_file():
        raise RuntimeError(f"plugin source not found under {source}")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source)
    return _smoke(Path(sys.executable), label="bundled-plugin", expected_version=None, environment=environment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path)
    arguments = parser.parse_args()
    result = {"wheel": verify(_wheel_from(arguments.wheel_dir))}
    if arguments.plugin_root is not None:
        result["plugin"] = verify_plugin(arguments.plugin_root)
        if result["wheel"]["tools"] != result["plugin"]["tools"]:
            raise RuntimeError("fresh wheel MCP tools differ from bundled plugin MCP tools")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
