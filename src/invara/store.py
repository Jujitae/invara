"""Sealed contracts and their verdicts, in their own database.

``.runtime/verify.db``. Append-only, hash-chained through :mod:`invara.chain`
rather than a sixth private copy of that rule.

Two tables and the reason they are two: a contract is written once, before
the work is judged, and a verdict is written after. Keeping them apart is
what makes "sealed first" checkable — the contract's row cannot have been
touched by the run that it graded.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any, Sequence

from . import chain
from .contract import VerificationContract, Verdict
from .runner import Observation

__all__ = [
    "DEFAULT_PATH",
    "DuplicateTask",
    "connect",
    "history",
    "list_contracts",
    "load_contract",
    "record_contract",
    "record_verdict",
    "verify",
]

DEFAULT_PATH = pathlib.Path(".runtime/verify.db")

SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS contract (
        seq INTEGER PRIMARY KEY,
        task_id TEXT NOT NULL UNIQUE,
        intent TEXT NOT NULL,
        contract_json TEXT NOT NULL,
        falsifier TEXT NOT NULL,
        sealed_at REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS verdict (
        seq INTEGER PRIMARY KEY,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        observations_json TEXT NOT NULL,
        observed_at REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS verdict_by_task ON verdict(task_id, seq)",
)


class DuplicateTask(ValueError):
    """This task already has a sealed contract. Seal once, judge many."""


def connect(path: str | pathlib.Path = DEFAULT_PATH) -> sqlite3.Connection:
    location = pathlib.Path(path)
    if str(location.parent) not in ("", "."):
        location.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(location)
    connection.row_factory = sqlite3.Row
    for statement in SCHEMA:
        connection.execute(statement)
    connection.commit()
    return connection


def _contract_payload(row: Any) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "intent": row["intent"],
        "contract": json.loads(row["contract_json"]),
        "falsifier": row["falsifier"],
        "sealed_at": float(row["sealed_at"]),
    }


def _verdict_payload(row: Any) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "status": row["status"],
        "reason": row["reason"],
        "detail": json.loads(row["detail_json"]),
        "observations": json.loads(row["observations_json"]),
        "observed_at": float(row["observed_at"]),
    }


def record_contract(
    connection: sqlite3.Connection, contract: VerificationContract
) -> None:
    if connection.execute(
        "SELECT 1 FROM contract WHERE task_id = ?", (contract.task_id,)
    ).fetchone():
        raise DuplicateTask(contract.task_id)
    chain.append(
        connection,
        "contract",
        {
            "task_id": contract.task_id,
            "intent": contract.intent,
            "contract_json": chain.canonical_json(contract.as_dict()),
            "falsifier": contract.falsifier(),
            "sealed_at": float(contract.sealed_at),
        },
        _contract_payload,
    )


def record_verdict(
    connection: sqlite3.Connection,
    task_id: str,
    verdict: Verdict,
    observations: Sequence[Observation],
    *,
    observed_at: float,
) -> None:
    """Append a verdict. Never replaces one.

    A task can be judged repeatedly — that is the point of a gate — and every
    judgement stays. A verifier whose history can be tidied is a verifier
    whose history means nothing.
    """

    chain.append(
        connection,
        "verdict",
        {
            "task_id": task_id,
            "status": verdict.status,
            "reason": verdict.reason,
            "detail_json": chain.canonical_json(verdict.as_dict()),
            "observations_json": chain.canonical_json(
                {"items": [o.as_dict() for o in observations]}
            ),
            "observed_at": float(observed_at),
        },
        _verdict_payload,
    )


def load_contract(
    connection: sqlite3.Connection, task_id: str
) -> VerificationContract:
    row = connection.execute(
        "SELECT contract_json FROM contract WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise KeyError(task_id)
    return VerificationContract.from_dict(json.loads(row["contract_json"]))


def list_contracts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for row in connection.execute("SELECT * FROM contract ORDER BY seq"):
        record = dict(row)
        latest = connection.execute(
            "SELECT status, observed_at FROM verdict WHERE task_id = ? "
            "ORDER BY seq DESC LIMIT 1",
            (row["task_id"],),
        ).fetchone()
        record["status"] = latest["status"] if latest else "unjudged"
        record["judged_at"] = latest["observed_at"] if latest else None
        out.append(record)
    return out


def history(connection: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM verdict WHERE task_id = ? ORDER BY seq", (task_id,)
        )
    ]


def verify(connection: sqlite3.Connection) -> dict[str, Any]:
    """Both chains, rebuilt through the shared primitive."""

    contracts = chain.verify_chain(connection, "contract", _contract_payload)
    verdicts = chain.verify_chain(connection, "verdict", _verdict_payload)
    return {
        "ok": contracts["ok"] and verdicts["ok"],
        "counts": {"contract": contracts["rows"], "verdict": verdicts["rows"]},
        "heads": {"contract": contracts["head"], "verdict": verdicts["head"]},
        "problems": contracts["problems"] + verdicts["problems"],
    }
