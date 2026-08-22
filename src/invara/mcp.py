"""INVARA inside the editor, over MCP, on the standard library alone.

The buyer this exists for cannot read code. They will not install a Python
CLI, and telling them to open a terminal is the same as not shipping. They
are already inside Cursor or Claude Code all day, and in there the *agent*
installs things and the person types a sentence. That is the whole reason
this surface exists: it is the only one where nobody has to go and find a
developer artifact.

**No dependencies, deliberately.** Every MCP server reaches for an SDK and
this one may not: ``dependencies = []`` is a product promise, and that promise
is what makes ``uvx --from git+...`` honest. So the transport is written out
here -- MCP over stdio is newline-delimited JSON-RPC 2.0, which is ``json``
and ``sys``. That is a real cost paid on purpose, and nothing in this file may
import what the package does not already have.

**stdout belongs to the protocol.** The CLI in :mod:`invara.__main__` prints
its results and none of that can be reused here: one stray line on stdout
corrupts the stream, and the client sees a dead server rather than a bug. So
this module calls the library directly and never the command layer. Anything
diagnostic goes to stderr.

**What this does not change.** The verdict is still a function of file digests
and command exit codes. There is no model in the path and this surface does
not become one. An agent may *ask* for a verdict here and still cannot assert
one -- there is no field in a contract where anything says the work is done,
and the checks that run are the ones a sealed contract already named.

Not a sandbox, and this does not make it one. ``seal`` accepts a task file,
that file names commands, so an agent able to write a task file and call
``invara_seal`` can cause those commands to run. That is not an escalation --
an agent in an editor already has a shell -- but it is better said here than
discovered.
"""

from __future__ import annotations

import datetime
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import store
from .contract import Constraint, NotVerifiable, Predicate, seal
from .runner import DEFAULT_TIMEOUT_S, digest_paths, judge, observe

__all__ = ["main", "serve"]

#: Protocol revisions this server can speak. The client's requested version is
#: echoed back when it is one of these, which is what the spec asks for;
#: otherwise the newest known one is offered and the client decides.
SUPPORTED: tuple[str, ...] = ("2025-03-26", "2024-11-05")

SERVER_INFO = {"name": "invara", "version": "0.1.0"}

_ROOT = {"type": "string", "description": "Repository root. Defaults to the working directory."}
_DB = {"type": "string", "description": "Verdict database. Defaults to .runtime/verify.db."}


def _tools() -> list[dict[str, Any]]:
    """The tool list, written so an agent can pick without guessing.

    The descriptions say what a verdict *means*, not only what the call does.
    An agent told merely "returns a verdict" will read UNVERIFIABLE as a pass,
    and that single confusion is what this product exists to prevent.
    """

    return [
        {
            "name": "invara_judge",
            "description": (
                "Judge a sealed task: run its completion checks, compare its "
                "protected paths against the digests taken at seal time, and "
                "return a verdict. BLOCK means a promise was broken or a check "
                "failed. UNVERIFIABLE means a check could not be run at all, so "
                "the work is unverified and NOT accepted. HUMAN_REVIEW means the "
                "machine checks passed and the contract asked for a person. PASS "
                "means every check returned what it promised and every protected "
                "path is byte-identical. The verdict also names which rule decided "
                "it. Nothing you report can change any of it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The sealed task to judge."},
                    "commit": {
                        "type": "boolean",
                        "description": "Append the verdict to the hash chain. Default false (dry run).",
                    },
                    "timeout_s": {
                        "type": "integer",
                        "description": "Per-check timeout in seconds. Default 900.",
                    },
                    "root": _ROOT,
                    "db": _DB,
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "invara_seal",
            "description": (
                "Seal an acceptance contract from a task file, before the work is "
                "done. Sealing takes the digests of the protected paths now, and "
                "that ordering is the entire guarantee. Refuses any contract that "
                "cannot fail the work: no completion condition, a condition with "
                "no command to check it, no protected paths, a duplicate check id, "
                "or every condition deferring to a person. Seal once, judge many."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_file": {"type": "string", "description": "Path to the task JSON."},
                    "root": _ROOT,
                    "db": _DB,
                },
                "required": ["task_file"],
            },
        },
        {
            "name": "invara_list",
            "description": "Every sealed task with its latest verdict, or 'unjudged'.",
            "inputSchema": {"type": "object", "properties": {"db": _DB}},
        },
        {
            "name": "invara_log",
            "description": (
                "Every verdict a task has ever had, oldest first. The history is "
                "append-only: judgements are added, never replaced or tidied."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "db": _DB},
                "required": ["task_id"],
            },
        },
        {
            "name": "invara_chain",
            "description": (
                "Rebuild both hash chains from their stored rows and report whether "
                "they still verify. This is how you find out the history was edited."
            ),
            "inputSchema": {"type": "object", "properties": {"db": _DB}},
        },
    ]


def _db(args: dict[str, Any]):
    return store.connect(args.get("db") or store.DEFAULT_PATH)


def _root(args: dict[str, Any]) -> Path:
    return Path(args.get("root") or ".").resolve()


def _now() -> float:
    return datetime.datetime.now(datetime.UTC).timestamp()


def _call_judge(args: dict[str, Any]) -> dict[str, Any]:
    connection = _db(args)
    try:
        contract = store.load_contract(connection, args["task_id"])
        current, observations = observe(
            contract, _root(args), timeout_s=int(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
        )
        verdict = judge(contract, current, observations)
        recorded = False
        if args.get("commit"):
            store.record_verdict(
                connection, contract.task_id, verdict, observations, observed_at=_now()
            )
            recorded = True
        return {
            "task_id": contract.task_id,
            "intent": contract.intent,
            "status": verdict.status,
            "decided_by": verdict.decided_by,
            "reason": verdict.reason,
            "accepted": verdict.accepted,
            "evidence": {
                "constraint_breaks": list(verdict.constraint_breaks),
                "failed": list(verdict.failed),
                "unrunnable": list(verdict.unrunnable),
                "passed": list(verdict.passed),
                "needs_human": list(verdict.needs_human),
            },
            "observations": [o.as_dict() for o in observations],
            "recorded": recorded,
        }
    finally:
        connection.close()


def _call_seal(args: dict[str, Any]) -> dict[str, Any]:
    root = _root(args)
    raw = json.loads(Path(args["task_file"]).read_text(encoding="utf-8"))
    constraints = [
        Constraint(
            kind=c["kind"],
            paths=tuple(c["paths"]),
            reason=c.get("reason", ""),
            baseline=digest_paths(c["paths"], root),
        )
        for c in raw.get("constraints", [])
    ]
    predicates = [
        Predicate(
            id=p["id"],
            command=tuple(p.get("command", ())),
            expect_exit=int(p.get("expect_exit", 0)),
            reason=p.get("reason", ""),
            human=bool(p.get("human", False)),
        )
        for p in raw.get("done_when", [])
    ]
    contract = seal(
        task_id=raw.get("task_id", ""),
        intent=raw.get("intent", ""),
        constraints=constraints,
        done_when=predicates,
        sealed_at=_now(),
    )
    connection = _db(args)
    try:
        store.record_contract(connection, contract)
    finally:
        connection.close()
    return {
        "sealed": contract.task_id,
        "intent": contract.intent,
        "protected_paths": sum(len(c.paths) for c in contract.constraints),
        "checks": len(contract.done_when),
        "fails_if": contract.falsifier(),
    }


def _call_list(args: dict[str, Any]) -> dict[str, Any]:
    connection = _db(args)
    try:
        return {
            "tasks": [
                {
                    "task_id": r["task_id"],
                    "intent": r["intent"],
                    "status": r["status"],
                    "judged_at": r["judged_at"],
                }
                for r in store.list_contracts(connection)
            ]
        }
    finally:
        connection.close()


def _call_log(args: dict[str, Any]) -> dict[str, Any]:
    connection = _db(args)
    try:
        out = []
        for row in store.history(connection, args["task_id"]):
            detail = json.loads(row["detail_json"])
            out.append(
                {
                    "observed_at": row["observed_at"],
                    "status": row["status"],
                    # Absent on rows written before the field existed, and
                    # reported as null rather than derived: deriving it here
                    # would put a second copy of the resolution order in this
                    # file, and two copies of a rule do not report a
                    # disagreement, they report the ledger as corrupt.
                    "decided_by": detail.get("decided_by"),
                    "reason": row["reason"],
                }
            )
        return {"task_id": args["task_id"], "verdicts": out}
    finally:
        connection.close()


def _call_chain(args: dict[str, Any]) -> dict[str, Any]:
    connection = _db(args)
    try:
        return store.verify(connection)
    finally:
        connection.close()


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "invara_judge": _call_judge,
    "invara_seal": _call_seal,
    "invara_list": _call_list,
    "invara_log": _call_log,
    "invara_chain": _call_chain,
}


def _dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool. A refusal is a result, not a transport fault.

    ``seal`` refusing a contract that cannot fail the work is the product
    behaving correctly, and it has to reach the agent as content it can read
    rather than as a protocol error it will retry.
    """

    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": "no such tool: " + name}], "isError": True}
    try:
        payload: dict[str, Any] = handler(args or {})
        is_error = False
    except NotVerifiable as refusal:
        payload = {"refused": refusal.reason, "detail": refusal.detail}
        is_error = True
    except store.DuplicateTask as dup:
        payload = {"refused": "already_sealed", "detail": str(dup) + " (seal once, judge many)"}
        is_error = True
    except KeyError as missing:
        payload = {"refused": "no_such_task", "detail": str(missing)}
        is_error = True
    except Exception:  # noqa: BLE001 - the transport must outlive any one tool
        print(traceback.format_exc(), file=sys.stderr)
        payload = {"error": "the tool raised; see the server's stderr"}
        is_error = True
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
        "isError": is_error,
    }


def _ok(ident: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """One request in, at most one response out. A notification gets nothing."""

    method = message.get("method")
    ident = message.get("id")
    params = message.get("params") or {}

    if ident is None:
        # A notification. Replying to one is itself a protocol violation, so
        # this branch is the whole of "initialized" handling.
        return None

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _ok(
            ident,
            {
                "protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        return _ok(ident, {"tools": _tools()})
    if method == "tools/call":
        return _ok(ident, _dispatch_tool(params.get("name", ""), params.get("arguments") or {}))
    if method == "ping":
        return _ok(ident, {})
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {"code": -32601, "message": "method not found: " + str(method)},
    }


def serve(stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC until the client closes the stream.

    The parameters exist so a test can drive this without a subprocess.
    Nothing else should pass them.
    """

    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for raw in source:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # No id can be recovered from unparseable input, so this is the one
            # place a null id is correct rather than lazy.
            response: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        else:
            response = _handle(message)
        if response is None:
            continue
        sink.write(json.dumps(response, ensure_ascii=False) + "\n")
        sink.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
