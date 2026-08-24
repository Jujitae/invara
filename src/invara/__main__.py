"""``invara`` — seal a task, judge it, read the history.

Its own entry point, deliberately: a verifier that shares a command with the
thing it verifies can be broken by the thing it verifies.

    invara init                 generate a task.json template from the repository
    invara seal   <task.json>   seal the contract; refuses if it cannot fail
    invara judge  <task_id>     run the checks and record what happened
    invara list                 sealed tasks and their latest verdict
    invara log    <task_id>     every verdict this task has had
    invara show   <task_id>     the sealed contract, as sealed
    invara chain                rebuild both hash chains
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import store
from .contract import BLOCK, PASS, UNVERIFIABLE, Constraint, NotVerifiable, Predicate, seal
from .runner import DEFAULT_TIMEOUT_S, digest_paths, observe
from .verdict import judge

#: Exit codes, so a shell or a CI step can act without parsing text.
EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_UNVERIFIABLE = 2
EXIT_REFUSED = 3

_OPEN: list[Any] = []


def _store(path: str):
    connection = store.connect(path)
    _OPEN.append(connection)
    return connection


def _now() -> float:
    return datetime.datetime.now(datetime.UTC).timestamp()


def _stamp(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def _decider(detail_json: str | None) -> str:
    """The rule that decided a stored verdict, bracketed — or nothing at all.

    Every verdict in a ledger older than this field has no decider to show,
    and it does not get one fitted here. Deriving one would put a second copy
    of the resolution order in the printer, and two copies of a rule do not
    report a disagreement; they report the whole ledger as corrupt. An older
    row says less because less is what it has, and it prints exactly as it
    always did.
    """

    if not detail_json:
        return ""
    name = json.loads(detail_json).get("decided_by")
    return f"  [{name}]" if name else ""


def cmd_seal(args: argparse.Namespace) -> int:
    """Seal a contract from a task file.

    The baseline digests are taken here, now, before the work is judged. That
    ordering is the whole of the guarantee: a protected path's "before" is
    recorded at a moment when nobody yet knows what the verdict will be.
    """

    root = Path(args.root).resolve()
    spec = json.loads(Path(args.task).read_text(encoding="utf-8"))

    constraints = []
    for raw in spec.get("constraints", []):
        paths = tuple(raw.get("paths", ()))
        baseline = digest_paths(paths, root)
        missing = [p for p in paths if p not in baseline]
        if missing:
            print(
                "REFUSED unreadable_constraint_path: "
                + ", ".join(missing[:5])
                + "\n  a path that is not there cannot be protected; fix the "
                "task file or the working tree"
            )
            return EXIT_REFUSED
        constraints.append(
            Constraint(
                kind=raw.get("kind", "paths_unchanged"),
                paths=paths,
                reason=raw.get("reason", ""),
                baseline=baseline,
            )
        )

    predicates = [Predicate.from_dict(p) for p in spec.get("done_when", [])]

    try:
        contract = seal(
            task_id=spec.get("task_id", ""),
            intent=spec.get("intent", ""),
            constraints=constraints,
            done_when=predicates,
            sealed_at=_now(),
        )
    except NotVerifiable as error:
        print(f"REFUSED {error.reason}: {error.detail}")
        return EXIT_REFUSED

    connection = _store(args.db)
    try:
        store.record_contract(connection, contract)
    except store.DuplicateTask:
        print(f"already sealed: {contract.task_id} (seal once, judge many)")
        return EXIT_REFUSED

    protected = sum(len(c.paths) for c in contract.constraints)
    print(f"sealed {contract.task_id}  at {_stamp(contract.sealed_at)}")
    print(f"  intent     {contract.intent}")
    print(f"  protects   {protected} path(s)")
    print(f"  checks     {len(contract.done_when)}")
    print(f"  fails if   {contract.falsifier()}")
    return EXIT_OK


def cmd_judge(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    connection = _store(args.db)
    try:
        contract = store.load_contract(connection, args.task_id)
    except KeyError:
        print(f"no sealed contract for {args.task_id}")
        return EXIT_REFUSED

    print(f"judging {contract.task_id}  (sealed {_stamp(contract.sealed_at)})")
    print(f"  {contract.intent}")
    print()
    current, observations = observe(contract, root, timeout_s=args.timeout)
    verdict = judge(contract, current, observations)

    for observation in observations:
        mark = "ok " if observation.ran and observation.exit_code == 0 else "!! "
        if not observation.ran:
            mark = ".. "
        print(f"  {mark}{observation.predicate_id:<28} {observation.detail}")
    print()
    print(f"  {verdict.status}: {verdict.reason}")
    print(f"  decided by: {verdict.decided_by}")

    if args.commit:
        store.record_verdict(
            connection,
            contract.task_id,
            verdict,
            observations,
            observed_at=_now(),
        )
        print("  recorded")
    else:
        print("  (dry run; pass --commit to record)")

    if verdict.status == BLOCK:
        return EXIT_BLOCK
    if verdict.status == UNVERIFIABLE:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    connection = _store(args.db)
    rows = store.list_contracts(connection)
    if not rows:
        print("nothing sealed yet")
        return EXIT_OK
    for row in rows:
        judged = _stamp(row["judged_at"]) if row["judged_at"] else "never"
        print(f"{row['task_id']:<34} {row['status']:<14} judged {judged}")
        print(f"    {row['intent']}")
    return EXIT_OK


def cmd_log(args: argparse.Namespace) -> int:
    connection = _store(args.db)
    rows = store.history(connection, args.task_id)
    if not rows:
        print(f"{args.task_id} has never been judged")
        return EXIT_OK
    for row in rows:
        decided = _decider(row["detail_json"])
        print(f"{_stamp(row['observed_at'])}  {row['status']}{decided}")
        print(f"    {row['reason']}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    connection = _store(args.db)
    try:
        contract = store.load_contract(connection, args.task_id)
    except KeyError:
        print(f"no sealed contract for {args.task_id}")
        return EXIT_REFUSED
    print(json.dumps(contract.as_dict(), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_chain(args: argparse.Namespace) -> int:
    connection = _store(args.db)
    result = store.verify(connection)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return EXIT_OK if result["ok"] else EXIT_BLOCK


def cmd_init(args: argparse.Namespace) -> int:
    """Generate a minimal task.json that passes seal."""
    import subprocess

    root = Path(args.root).resolve()

    # Generate task_id from git branch + timestamp
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        branch = "unknown"

    task_id = f"TASK-{branch}-{int(_now())}"

    # Find common files to protect
    protected = []
    for candidate in ["README.md", "LICENSE", "pyproject.toml", "package.json"]:
        if (root / candidate).exists():
            protected.append(candidate)

    # If nothing found, protect at least one file so seal doesn't refuse
    if not protected:
        # Find any tracked file
        try:
            tracked = subprocess.run(
                ["git", "ls-files"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip().split("\n")
            if tracked and tracked[0]:
                protected.append(tracked[0])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    # Still nothing? Use a placeholder
    if not protected:
        protected = [".gitignore"]

    task = {
        "task_id": task_id,
        "intent": f"Verify work on {branch}",
        "constraints": [
            {
                "kind": "paths_unchanged",
                "paths": protected,
                "reason": "These files define the project and should not change during the work",
            }
        ],
        "done_when": [
            {
                "id": "tests_pass",
                "command": ["echo", "TODO: replace with actual test command"],
                "expect_exit": 0,
                "reason": "All tests must pass",
            }
        ],
    }

    print(json.dumps(task, ensure_ascii=False, indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invara",
        description=(
            "Given a task, its constraints and the repository afterwards, "
            "decide independently whether the work actually happened."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=str(store.DEFAULT_PATH))
        p.add_argument("--root", default=".")

    seal_p = sub.add_parser("seal", help="seal a contract from a task file")
    common(seal_p)
    seal_p.add_argument("task")
    seal_p.set_defaults(func=cmd_seal)

    judge_p = sub.add_parser("judge", help="run the checks and form a verdict")
    common(judge_p)
    judge_p.add_argument("task_id")
    judge_p.add_argument("--commit", action="store_true")
    judge_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    judge_p.set_defaults(func=cmd_judge)

    list_p = sub.add_parser("list", help="sealed tasks and latest verdicts")
    common(list_p)
    list_p.set_defaults(func=cmd_list)

    log_p = sub.add_parser("log", help="every verdict for one task")
    common(log_p)
    log_p.add_argument("task_id")
    log_p.set_defaults(func=cmd_log)

    show_p = sub.add_parser("show", help="the contract, as sealed")
    common(show_p)
    show_p.add_argument("task_id")
    show_p.set_defaults(func=cmd_show)

    chain_p = sub.add_parser("chain", help="rebuild both hash chains")
    common(chain_p)
    chain_p.set_defaults(func=cmd_chain)

    init_p = sub.add_parser("init", help="generate a task.json template")
    common(init_p)
    init_p.set_defaults(func=cmd_init)

    return parser


def _survive_the_console() -> None:
    """Never let printing the result be what fails.

    Found by sealing the second contract: the CLI wrote an em-dash to a cp949
    console, raised UnicodeEncodeError, and exited 1 — *after* the contract
    had already been written. A tool that does the work and then reports
    failure while printing about it is worse than one that just fails, because
    the operator's next move is to run it again.

    ``errors="replace"`` rather than forcing UTF-8: the console keeps whatever
    encoding it has, so Korean still reads correctly where it can, and the
    handful of characters it cannot represent become '?' instead of an
    exception.

    This net is for the operator's own text — intent lines, check output, path
    names — which INVARA does not get to choose. It is not licence for INVARA
    to print characters it did not need. An em-dash in the dry-run line came
    out as '?' on a cp949 console, and that line is part of the first verdict
    a new user ever sees. A test holds this module's own literals to ASCII.
    """

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - exotic stream
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _survive_the_console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    finally:
        while _OPEN:
            _OPEN.pop().close()


if __name__ == "__main__":
    sys.exit(main())
