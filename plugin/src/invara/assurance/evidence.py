"""The evidence store: three more chained tables in the verifier's own file.

Not a second truth system. The assurance subsystem writes into the same
``.runtime/verify.db`` the kernel uses, through the same
:mod:`invara.chain` primitive, with the same rule: rows are appended,
chained by hash, and never rewritten. Three tables and why they are three:

* ``assurance_manifest`` — content-addressed manifests. The digest is the
  SHA-256 of the canonical JSON; a session refers to its manifest by digest
  and an amendment produces a new row rather than an edit.
* ``assurance_observation`` — content-addressed observation records, raw
  and normalized, keyed by session, kind and run. Raw records are the
  evidence; normalized ones reference the raw digest and the policy set
  they were derived under.
* ``assurance_event`` — the append-only history of every session: state
  transitions, claim results, amendments, units, findings. A session's
  current state is whatever the last event says, which is what makes
  resuming after a crash a read rather than a guess.

:meth:`Evidence.verify` rebuilds every chain and re-derives every content
address. A store that cannot be read, or whose tables have gone missing,
raises :class:`EvidenceError` rather than answering as if it were empty.
"""

from __future__ import annotations

from .. import exact_json as json
import pathlib
import sqlite3
from typing import Any

from .. import chain
from ..store import DEFAULT_PATH
from .manifest import Manifest, content_digest

__all__ = ["DEFAULT_PATH", "Evidence", "EvidenceError", "STORE_SCHEMA_VERSION"]

STORE_SCHEMA_VERSION = "invara.assurance.store/1"

TABLES = ("assurance_manifest", "assurance_observation", "assurance_event")

SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS assurance_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS assurance_manifest (
        seq INTEGER PRIMARY KEY,
        digest TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS assurance_observation (
        seq INTEGER PRIMARY KEY,
        digest TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        run_key TEXT NOT NULL,
        record_json TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        UNIQUE (session_id, kind, run_key)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS assurance_event (
        seq INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL,
        event TEXT NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS assurance_observation_by_session ON assurance_observation(session_id, kind, run_key)",
    "CREATE INDEX IF NOT EXISTS assurance_event_by_session ON assurance_event(session_id, seq)",
)


class EvidenceError(RuntimeError):
    """The store cannot be trusted or cannot be written. Nothing is inferred.

    ``reason`` is the slug a caller reports: ``store_corrupt`` unless the
    store is merely held by another process (``store_busy``).
    """

    def __init__(self, message: str, *, reason: str = "store_corrupt") -> None:
        super().__init__(message)
        self.reason = reason


def _manifest_payload(row: Any) -> dict[str, Any]:
    return {
        "digest": row["digest"],
        "session_id": row["session_id"],
        "schema_version": row["schema_version"],
        "manifest_json": row["manifest_json"],
        "recorded_at": float(row["recorded_at"]),
    }


def _observation_payload(row: Any) -> dict[str, Any]:
    return {
        "digest": row["digest"],
        "session_id": row["session_id"],
        "kind": row["kind"],
        "run_key": row["run_key"],
        "record_json": row["record_json"],
        "recorded_at": float(row["recorded_at"]),
    }


def _event_payload(row: Any) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "event": row["event"],
        "from_state": row["from_state"],
        "to_state": row["to_state"],
        "payload_json": row["payload_json"],
        "recorded_at": float(row["recorded_at"]),
    }


class Evidence:
    def __init__(self, path: str | pathlib.Path = DEFAULT_PATH) -> None:
        self.path = pathlib.Path(path)
        try:
            if str(self.path.parent) not in ("", "."):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_dir():
                raise EvidenceError(f"evidence store path {self.path} is a directory, not a database file")
            self.connection = sqlite3.connect(self.path, timeout=5.0)
        except OSError as error:
            raise EvidenceError(f"cannot open evidence database {self.path}: {error}") from None
        except sqlite3.DatabaseError as error:
            raise EvidenceError(f"cannot open evidence database {self.path}: {error}") from None
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout = 5000")
            fresh = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assurance_meta'"
            ).fetchone() is None
            if fresh:
                for statement in SCHEMA:
                    self.connection.execute(statement)
                self.connection.execute(
                    "INSERT INTO assurance_meta (key, value) VALUES ('schema_version', ?)", (STORE_SCHEMA_VERSION,)
                )
                self.connection.commit()
            else:
                row = self.connection.execute("SELECT value FROM assurance_meta WHERE key = 'schema_version'").fetchone()
                if row is None or row["value"] != STORE_SCHEMA_VERSION:
                    raise EvidenceError(
                        f"evidence store schema {row['value'] if row else 'missing'} is not {STORE_SCHEMA_VERSION}; refusing to read it as if it were"
                    )
        except sqlite3.DatabaseError as error:
            self.connection.close()
            raise EvidenceError(f"evidence store unreadable at {self.path}: {error}") from None
        except EvidenceError:
            self.connection.close()
            raise

    def __enter__(self) -> "Evidence":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _failure(error: sqlite3.DatabaseError) -> EvidenceError:
        """A sqlite failure as the error a caller reports: contention is busy, everything else corrupt."""

        if isinstance(error, sqlite3.OperationalError) and ("locked" in str(error) or "busy" in str(error)):
            return EvidenceError(f"another process holds the evidence database: {error}", reason="store_busy")
        return EvidenceError(f"evidence store failed: {error}")

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, params)
        except sqlite3.DatabaseError as error:
            raise self._failure(error) from None

    def _append(self, table: str, fields: dict[str, Any], payload_of: Any, *, commit: bool = True) -> dict[str, Any]:
        try:
            return chain.append(self.connection, table, fields, payload_of, commit=commit)
        except sqlite3.DatabaseError as error:
            raise self._failure(error) from None

    # ---------------------------------------------------------------- manifests

    def record_manifest(self, manifest: Manifest, *, at: float) -> str:
        digest = manifest.digest()
        if self._execute("SELECT 1 FROM assurance_manifest WHERE digest = ?", (digest,)).fetchone():
            return digest
        self._append(
            "assurance_manifest",
            {
                "digest": digest,
                "session_id": manifest.session_id,
                "schema_version": manifest.schema_version,
                "manifest_json": chain.canonical_json(manifest.as_dict()),
                "recorded_at": float(at),
            },
            _manifest_payload,
        )
        return digest

    def load_manifest(self, digest: str) -> Manifest:
        row = self._execute("SELECT manifest_json FROM assurance_manifest WHERE digest = ?", (digest,)).fetchone()
        if row is None:
            raise KeyError(digest)
        return Manifest.from_dict(json.loads(row["manifest_json"]))

    def manifests(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {"seq": row["seq"], "digest": row["digest"], "recorded_at": float(row["recorded_at"])}
            for row in self._execute(
                "SELECT seq, digest, recorded_at FROM assurance_manifest WHERE session_id = ? ORDER BY seq", (session_id,)
            )
        ]

    # ------------------------------------------------------------- observations

    def record_observation(self, session_id: str, kind: str, run_key: str, record: dict[str, Any], *, at: float) -> str:
        digest = content_digest(record)
        existing = self._execute(
            "SELECT digest FROM assurance_observation WHERE session_id = ? AND kind = ? AND run_key = ?",
            (session_id, kind, run_key),
        ).fetchone()
        if existing is not None:
            if existing["digest"] != digest:
                raise EvidenceError(f"{kind} {run_key} is already recorded with different content; evidence is not rewritten")
            return digest
        self._append(
            "assurance_observation",
            {
                "digest": digest,
                "session_id": session_id,
                "kind": kind,
                "run_key": run_key,
                "record_json": chain.canonical_json(record),
                "recorded_at": float(at),
            },
            _observation_payload,
        )
        return digest

    def load_observation(self, digest: str) -> dict[str, Any]:
        row = self._execute("SELECT record_json FROM assurance_observation WHERE digest = ? ORDER BY seq LIMIT 1", (digest,)).fetchone()
        if row is None:
            raise KeyError(digest)
        return json.loads(row["record_json"])

    def observation(self, session_id: str, kind: str, run_key: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM assurance_observation WHERE session_id = ? AND kind = ? AND run_key = ?",
            (session_id, kind, run_key),
        ).fetchone()
        return self._observation_row(row) if row is not None else None

    def observations(self, session_id: str, *, kind: str | None = None, prefix: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM assurance_observation WHERE session_id = ?"
        params: list[Any] = [session_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if prefix is not None:
            sql += " AND substr(run_key, 1, ?) = ?"
            params.extend([len(prefix), prefix])
        sql += " ORDER BY seq"
        return [self._observation_row(row) for row in self._execute(sql, tuple(params))]

    @staticmethod
    def _observation_row(row: Any) -> dict[str, Any]:
        return {
            "seq": row["seq"],
            "digest": row["digest"],
            "kind": row["kind"],
            "run_key": row["run_key"],
            "record": json.loads(row["record_json"]),
            "recorded_at": float(row["recorded_at"]),
        }

    # ------------------------------------------------------------------- events

    def append_event(
        self, session_id: str, event: str, from_state: str, to_state: str, payload: dict[str, Any], *, at: float, commit: bool = True
    ) -> dict[str, Any]:
        row = self._append(
            "assurance_event",
            {
                "session_id": session_id,
                "event": event,
                "from_state": from_state,
                "to_state": to_state,
                "payload_json": chain.canonical_json(payload),
                "recorded_at": float(at),
            },
            _event_payload,
            commit=commit,
        )
        return {**row, "payload": dict(payload)}

    def events(self, session_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self._execute("SELECT * FROM assurance_event WHERE session_id = ? ORDER BY seq", (session_id,)):
            out.append(
                {
                    "seq": row["seq"],
                    "event": row["event"],
                    "from_state": row["from_state"],
                    "to_state": row["to_state"],
                    "payload": json.loads(row["payload_json"]),
                    "recorded_at": float(row["recorded_at"]),
                    "prev_hash": row["prev_hash"],
                    "record_hash": row["record_hash"],
                }
            )
        return out

    def chain_links(self, session_id: str) -> list[dict[str, Any]]:
        """The hashes of other sessions' events that sit between this session's first and last event.

        The event table is one chain over every session, so a session's own
        events do not link to each other where another session wrote in
        between. With these link rows (hashes only, no payload) a package can
        verify the chain across the whole span.
        """

        rows = self._execute(
            "SELECT seq, prev_hash, record_hash FROM assurance_event WHERE session_id != ? "
            "AND seq > (SELECT MIN(seq) FROM assurance_event WHERE session_id = ?) "
            "AND seq < (SELECT MAX(seq) FROM assurance_event WHERE session_id = ?) ORDER BY seq",
            (session_id, session_id, session_id),
        )
        return [{"seq": row["seq"], "prev_hash": row["prev_hash"], "record_hash": row["record_hash"]} for row in rows]

    def sessions(self) -> list[str]:
        return [
            row["session_id"]
            for row in self._execute(
                "SELECT session_id, MIN(seq) AS first FROM assurance_event GROUP BY session_id ORDER BY first"
            )
        ]

    # ---------------------------------------------------------------- integrity

    def verify(self) -> dict[str, Any]:
        """Every chain rebuilt, every content address re-derived."""

        problems: list[str] = []
        counts: dict[str, int] = {}
        heads: dict[str, str] = {}
        try:
            for table, payload_of in (
                ("assurance_manifest", _manifest_payload),
                ("assurance_observation", _observation_payload),
                ("assurance_event", _event_payload),
            ):
                result = chain.verify_chain(self.connection, table, payload_of)
                counts[table] = result["rows"]
                heads[table] = result["head"]
                problems.extend(result["problems"])
            for table, column in (("assurance_manifest", "manifest_json"), ("assurance_observation", "record_json")):
                for row in self.connection.execute(f"SELECT seq, digest, {column} AS body FROM {table} ORDER BY seq"):  # noqa: S608
                    try:
                        body = json.loads(row["body"])
                    except ValueError:
                        problems.append(f"{table}: unparseable record at seq {row['seq']}")
                        continue
                    if content_digest(body) != row["digest"]:
                        problems.append(f"{table}: content address mismatch at seq {row['seq']}")
        except sqlite3.DatabaseError as error:
            raise self._failure(error) from None
        return {"ok": not problems, "problems": problems, "counts": counts, "heads": heads}
