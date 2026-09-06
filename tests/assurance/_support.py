"""Shared builders for the assurance suite. Not collected by pytest."""

from __future__ import annotations

import copy
import json
from typing import Any

SCHEMA = "invara.assurance.manifest/1"
OBSERVATION_VERSION = "invara.assurance.observation/1"


def manifest_dict(**over: Any) -> dict[str, Any]:
    """A minimal valid manifest; ``over`` replaces top-level sections."""

    data: dict[str, Any] = {
        "schema_version": SCHEMA,
        "session_id": "unit-test",
        "source_system": {
            "id": "before",
            "kind": "process",
            "command": ["python", "app.py"],
            "root": "$SOURCE_ROOT",
        },
        "target_system": {
            "id": "after",
            "kind": "process",
            "command": ["python", "app.py"],
            "root": "$TARGET_ROOT",
        },
        "input_domain": {
            "kind": "corpus",
            "delivery": "stdin_json",
            "corpus": [{"id": "c1", "input": {"n": 1}}],
        },
        "probes": [
            {"id": "cli", "adapter": "process", "mandatory": True},
            {"id": "out", "adapter": "json", "source": "stdout", "mandatory": True},
        ],
        "policies": [],
        "claims": [{"id": "corpus", "kind": "corpus_equivalence", "mandatory": True}],
    }
    data.update(over)
    return data


def policy(kind: str, path: str, **params: Any) -> dict[str, Any]:
    """A policy dict with a reason, because non-exact policies need one."""

    ident = params.pop("id", f"{kind}-{abs(hash(path)) % 10_000}")
    reason = params.pop("reason", f"test policy {kind} at {path}")
    extra_paths = params.pop("paths", None)
    out: dict[str, Any] = {"id": ident, "kind": kind, "path": path, "reason": reason}
    if extra_paths:
        out["paths"] = list(extra_paths)
    if params:
        out["params"] = params
    return out


def observation(probes: dict[str, Any], *, workspace: str = "/tmp/ws", status: str = "observed",
                system_id: str = "before", input_id: str = "c1") -> dict[str, Any]:
    """A raw observation record around ``probes``."""

    return {
        "record_version": OBSERVATION_VERSION,
        "system_id": system_id,
        "input_id": input_id,
        "status": status,
        "problems": [],
        "workspace": workspace,
        "environment": {"controlled": {}, "reported": {}, "uncontrollable": []},
        "timing": {"wall_s": 0.0},
        "probes": copy.deepcopy(probes),
    }


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
