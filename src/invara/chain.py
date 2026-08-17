"""An append-only hash chain, in one place.

This rule already exists five times in this repository — ``lead_time``,
``backtest``, ``tombstone``, ``interpretation`` and ``divergence.store`` each
grew their own — and on 2026-08-17 a measurement found the copies had already
drifted: ``tombstone`` omits the JSON separators the others pass, so the same
payload digests differently. Nothing is broken today, because each module
writes and verifies through its own copy and is therefore self-consistent.

That is exactly the shape of the defect this project keeps paying for, and
``lead_time._mention_payload`` says so in a docstring four copies earlier:

    Two copies of a hashing rule that drift apart do not report a
    disagreement — they report the whole ledger as corrupt.

So this module exists, and it was written **additively**. In the repository
it came from, five ledgers already had their own copy of the rule and none
was migrated to this one: re-hashing a live chain is not a refactor, it is a
rewrite of history, and each has to move separately with its own proof that
the head hash did not change.

What an additive extraction prevents in the meantime is a sixth copy.

The rule, so it is written down once:

    record_hash = sha256(prev_hash + canonical_json(payload))
    canonical_json = sort_keys, ensure_ascii=False, separators=(",", ":")

The separators are load-bearing. Without them ``json.dumps`` inserts a space
after every ``,`` and ``:``, which is a different byte string and therefore a
different chain. That is the drift that was found.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable, Iterable

__all__ = [
    "GENESIS_HASH",
    "canonical_json",
    "chain_hash",
    "head_hash",
    "verify_chain",
]

#: Where every chain starts. Sixty-four zeroes, matching every existing
#: ledger in this repository.
GENESIS_HASH = "0" * 64


def canonical_json(payload: dict[str, Any]) -> str:
    """The one serialisation a hash may be taken over."""

    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """The next link."""

    return hashlib.sha256(
        (prev_hash + canonical_json(payload)).encode("utf-8")
    ).hexdigest()


def head_hash(connection: sqlite3.Connection, table: str) -> str:
    """The current head, or genesis when the table is empty.

    ``table`` is interpolated because SQLite cannot parameterise an
    identifier. Callers pass a literal from their own schema, never user
    input; :func:`verify_chain` and this function are the only places it
    happens and both are called with constants.
    """

    row = connection.execute(
        f"SELECT record_hash FROM {table} ORDER BY seq DESC LIMIT 1"  # noqa: S608
    ).fetchone()
    if row is None:
        return GENESIS_HASH
    return row["record_hash"] if not isinstance(row, tuple) else row[0]


def verify_chain(
    connection: sqlite3.Connection,
    table: str,
    payload_of: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild a chain from its stored rows.

    ``payload_of`` must be the *same* function the writer used to build the
    payload. Writing and verifying through one function is the property that
    makes a chain mean anything; two functions that agree today are two
    functions that can stop agreeing.
    """

    problems: list[str] = []
    previous = GENESIS_HASH
    count = 0
    for row in connection.execute(
        f"SELECT * FROM {table} ORDER BY seq"  # noqa: S608
    ):
        if row["prev_hash"] != previous:
            problems.append(f"{table}: chain break at seq {row['seq']}")
            break
        if chain_hash(row["prev_hash"], payload_of(row)) != row["record_hash"]:
            problems.append(f"{table}: hash mismatch at seq {row['seq']}")
            break
        previous = row["record_hash"]
        count += 1
    return {"ok": not problems, "rows": count, "head": previous, "problems": problems}


def append(
    connection: sqlite3.Connection,
    table: str,
    fields: dict[str, Any],
    payload_of: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Insert one row, chained. Returns the row as written.

    The caller supplies the columns; this adds ``prev_hash`` and
    ``record_hash`` and does the insert, so a new store cannot get the order
    wrong or forget one.
    """

    row = dict(fields)
    prev = head_hash(connection, table)
    row["prev_hash"] = prev
    row["record_hash"] = chain_hash(prev, payload_of(row))
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608
        tuple(row.values()),
    )
    connection.commit()
    return row


def existing_implementations() -> tuple[str, ...]:
    """Other copies of this rule that still exist and have not been migrated.

    Empty here. In the repository INVARA was extracted from, five engines
    carried their own hash-chain implementation and this module was pulled out
    additively rather than migrating live ledgers under a freeze; the list
    lived here so the eventual cleanup had a checklist and a test could assert
    it had not silently grown. Those names describe that repository, not this
    package, so they do not travel.

    The mechanism is kept because the reason is general: a rule with more than
    one implementation is a rule with a second opinion, and the cheapest way
    to stop that is to make the duplicates enumerable.
    """

    return ()
