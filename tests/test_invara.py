"""The verifier, tested the way the Divergence Engine was: refusals first.

A verifier that can only approve is a rubber stamp, and a missing approval is
much easier to notice than a false one. So most of this file is about the
four ways a task is refused or blocked, and PASS is the short part at the end.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from invara import chain
from invara import store
from invara.__main__ import (
    EXIT_BLOCK,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNVERIFIABLE,
    _stamp,
    main,
)
from invara.contract import (
    BLOCK,
    HUMAN_REVIEW,
    PASS,
    UNVERIFIABLE,
    Constraint,
    NotVerifiable,
    Predicate,
    Verdict,
    seal,
)
from invara.runner import (
    Observation,
    digest_paths,
    judge,
    observe,
    resolve_program,
    run_predicate,
)

T0 = 1_750_000_000.0
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "invara"
OK = [sys.executable, "-c", "raise SystemExit(0)"]
BAD = [sys.executable, "-c", "raise SystemExit(1)"]
MISSING = ["wie-no-such-command-exists", "--please"]


def constraint(root: Path, *names: str, reason: str = "protected") -> Constraint:
    return Constraint(
        kind="paths_unchanged",
        paths=names,
        reason=reason,
        baseline=digest_paths(names, root),
    )


def contract_over(root: Path, *predicates: Predicate, **kwargs):
    return seal(
        task_id=kwargs.get("task_id", "T-1"),
        intent=kwargs.get("intent", "do the thing"),
        constraints=[constraint(root, "guarded.txt")],
        done_when=list(predicates) or [Predicate("checks", tuple(OK))],
        sealed_at=T0,
    )


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "guarded.txt").write_text("do not touch", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class SealRefusals(Sandbox):
    """Each of these is a task that does not get a contract."""

    def test_a_task_with_no_completion_condition(self) -> None:
        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[constraint(self.root, "guarded.txt")],
                done_when=[],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "no_done_condition")

    def test_a_task_allowed_to_change_anything(self) -> None:
        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[],
                done_when=[Predicate("c", tuple(OK))],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "no_constraint")

    def test_a_condition_with_nothing_to_run(self) -> None:
        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[constraint(self.root, "guarded.txt")],
                done_when=[Predicate("looks-fine", ())],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "unverifiable_predicate")

    def test_a_task_that_only_defers_to_a_person(self) -> None:
        """The structural form of "agent self-report is not evidence"."""

        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[constraint(self.root, "guarded.txt")],
                done_when=[Predicate("looks-good", tuple(OK), human=True)],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "self_reporting_only")

    def test_a_protected_path_with_no_baseline(self) -> None:
        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[
                    Constraint("paths_unchanged", ("ghost.txt",), "r", {})
                ],
                done_when=[Predicate("c", tuple(OK))],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "unreadable_constraint_path")

    def test_two_conditions_cannot_share_a_name(self) -> None:
        with self.assertRaises(NotVerifiable) as caught:
            seal(
                task_id="T",
                intent="i",
                constraints=[constraint(self.root, "guarded.txt")],
                done_when=[Predicate("c", tuple(OK)), Predicate("c", tuple(OK))],
                sealed_at=T0,
            )
        self.assertEqual(caught.exception.reason, "duplicate_predicate")

    def test_every_sealed_contract_says_how_it_fails(self) -> None:
        contract = contract_over(self.root)
        self.assertIn("protected path", contract.falsifier())
        self.assertIn("not a pass", contract.falsifier())


class TheKillPath(Sandbox):
    def test_touching_a_protected_path_blocks(self) -> None:
        contract = contract_over(self.root)
        (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, BLOCK)
        self.assertTrue(verdict.constraint_breaks)

    def test_deleting_a_protected_path_blocks(self) -> None:
        contract = contract_over(self.root)
        (self.root / "guarded.txt").unlink()
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, BLOCK)
        self.assertIn("gone", verdict.constraint_breaks[0])

    def test_a_failing_check_blocks(self) -> None:
        contract = contract_over(self.root, Predicate("suite", tuple(BAD)))
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, BLOCK)
        self.assertTrue(verdict.failed)

    def test_a_constraint_break_outranks_a_passing_check(self) -> None:
        """Work that broke its promise is not partially fine."""

        contract = contract_over(self.root, Predicate("suite", tuple(OK)))
        (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, BLOCK)
        self.assertIn("protected path", verdict.reason)


class UncheckedIsNotPassed(Sandbox):
    """The transposition of ``starved``, and the point of the whole design."""

    def test_a_missing_command_is_unverifiable_not_pass(self) -> None:
        contract = contract_over(self.root, Predicate("suite", tuple(MISSING)))
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertNotEqual(verdict.status, PASS)
        self.assertFalse(verdict.accepted)

    def test_a_timeout_is_unverifiable_not_pass(self) -> None:
        slow = Predicate(
            "slow", (sys.executable, "-c", "import time; time.sleep(30)")
        )
        observation = run_predicate(slow, self.root, timeout_s=1)
        self.assertFalse(observation.ran)
        self.assertIn("timed out", observation.detail)

    def test_a_check_that_never_ran_is_unverifiable(self) -> None:
        contract = contract_over(self.root, Predicate("suite", tuple(OK)))
        verdict = judge(contract, {"0": digest_paths(("guarded.txt",), self.root)}, [])
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertIn("never run", verdict.unrunnable[0])

    def test_unverifiable_and_failure_are_different_things(self) -> None:
        missing = run_predicate(Predicate("m", tuple(MISSING)), self.root)
        failing = run_predicate(Predicate("f", tuple(BAD)), self.root)
        self.assertFalse(missing.ran)
        self.assertIsNone(missing.exit_code)
        self.assertTrue(failing.ran)
        self.assertEqual(failing.exit_code, 1)


class RepoRelativeCommands(Sandbox):
    """Found by running the verifier on the task that built it.

    The first real judgement came back UNVERIFIABLE on all three checks with
    `command not found`, because subprocess does not search ``cwd`` for the
    program. The verdict was right and the tool was useless, which is the
    most flattering way for a verifier to be broken.
    """

    def test_a_repo_relative_program_is_resolved_against_root(self) -> None:
        tool = self.root / "bin" / "runner.py"
        tool.parent.mkdir()
        tool.write_text("raise SystemExit(0)", encoding="utf-8")
        resolved = resolve_program(("bin/runner.py", "--x"), self.root)
        self.assertTrue(Path(resolved[0]).is_absolute())
        self.assertEqual(resolved[1], "--x")

    def test_a_bare_name_is_left_for_PATH(self) -> None:
        self.assertEqual(resolve_program(("git", "status"), self.root), ["git", "status"])

    def test_an_absolute_program_is_untouched(self) -> None:
        self.assertEqual(resolve_program((sys.executable,), self.root), [sys.executable])

    def test_a_relative_path_that_is_not_there_stays_as_written(self) -> None:
        """So the failure is still reported as not-found, not silently altered."""

        self.assertEqual(
            resolve_program(("bin/ghost.py",), self.root), ["bin/ghost.py"]
        )

    # The child writes raw UTF-8 bytes, and this source stays ASCII-only.
    #
    # The obvious version — `print('감사 결과')` — passed here and failed on
    # CI, because every command in the session that wrote it had been
    # prefixed with PYTHONIOENCODING=utf-8 and the child inherited it. A
    # runner has no such variable, so the child died encoding its own output
    # and exited 1. The test was measuring the shell it was written in.
    #
    # Writing bytes removes the child's encoding from the question entirely,
    # which is right: what is under test is whether the *parent* survives
    # non-ASCII on the pipe, not whether a subprocess can print Korean.
    _NOISY = (
        "import sys; "
        "sys.stdout.buffer.write('\\uac10\\uc0ac \\uacb0\\uacfc \\u2014 drift 0'"
        ".encode('utf-8')); "
        "raise SystemExit(0)"
    )

    def test_non_ascii_output_does_not_break_the_reader(self) -> None:
        """The first real PASS arrived with a UnicodeDecodeError attached.

        A check that emitted Korean killed the subprocess reader thread on a
        cp949 console. The exit code still decided the verdict, so the answer
        was right and the output was a stack trace, which is how a working
        gate loses its audience.
        """

        observation = run_predicate(
            Predicate("loud", (sys.executable, "-c", self._NOISY)), self.root
        )
        self.assertTrue(observation.ran)
        self.assertEqual(observation.exit_code, 0)
        self.assertIn("drift 0", observation.detail)

    def test_the_reader_survives_bytes_that_are_not_valid_utf8(self) -> None:
        """`errors="replace"` is load-bearing, not decoration.

        A check is any program. Some of them emit bytes that are not text in
        any encoding, and the reader must come back with a verdict rather
        than an exception.
        """

        observation = run_predicate(
            Predicate(
                "garbage",
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok'); "
                    "raise SystemExit(0)",
                ),
            ),
            self.root,
        )
        self.assertTrue(observation.ran)
        self.assertEqual(observation.exit_code, 0)

    def test_it_actually_runs_now(self) -> None:
        tool = self.root / "bin" / "ok.py"
        tool.parent.mkdir()
        tool.write_text("raise SystemExit(0)", encoding="utf-8")
        observation = run_predicate(
            Predicate("t", (sys.executable, "bin/ok.py")), self.root
        )
        self.assertTrue(observation.ran)
        self.assertEqual(observation.exit_code, 0)


class ThePassPath(Sandbox):
    def test_untouched_paths_and_passing_checks(self) -> None:
        contract = contract_over(self.root, Predicate("suite", tuple(OK)))
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, PASS)
        self.assertTrue(verdict.accepted)

    def test_a_human_item_holds_the_pass(self) -> None:
        contract = contract_over(
            self.root,
            Predicate("suite", tuple(OK)),
            Predicate("read-it", tuple(OK), human=True, reason="someone must look"),
        )
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, HUMAN_REVIEW)
        self.assertFalse(verdict.accepted)

    def test_a_human_item_does_not_rescue_a_block(self) -> None:
        contract = contract_over(
            self.root,
            Predicate("suite", tuple(BAD)),
            Predicate("read-it", tuple(OK), human=True),
        )
        verdict = judge(contract, *observe(contract, self.root))
        self.assertEqual(verdict.status, BLOCK)


class EveryVerdictNamesItsDecider(Sandbox):
    """A verdict that cannot say what decided it is one nobody can argue with.

    ``status`` was never enough. :func:`judge` has five exits and there are
    four statuses, so ``BLOCK`` is two different accusations sharing a word —
    a promise that was broken, and a check that came back wrong. Recovering
    which one from the evidence tuples means every reader keeps a second copy
    of the resolution order, and two copies of a rule do not report a
    disagreement; they report the whole ledger as corrupt.

    So ``decided_by`` names the evidence field that carried the decision, and
    these hold it to that rather than to a string somebody liked.
    """

    def _five(self) -> dict[str, "Verdict"]:
        """One verdict from each exit of the ladder, cheapest first."""

        out = {}
        for name, predicates, touch in (
            ("passed", (Predicate("suite", tuple(OK)),), False),
            ("failed", (Predicate("suite", tuple(BAD)),), False),
            ("unrunnable", (Predicate("suite", tuple(MISSING)),), False),
            (
                "needs_human",
                (
                    Predicate("suite", tuple(OK)),
                    Predicate("eyes", tuple(OK), human=True, reason="look"),
                ),
                False,
            ),
            ("constraint_breaks", (Predicate("suite", tuple(OK)),), True),
        ):
            contract = contract_over(self.root, *predicates)
            if touch:
                (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
            out[name] = judge(contract, *observe(contract, self.root))
            (self.root / "guarded.txt").write_text("do not touch", encoding="utf-8")
        return out

    def test_a_verdict_cannot_be_built_without_one(self) -> None:
        """No default. The field is not optional in the sense that matters."""

        with self.assertRaises(TypeError):
            Verdict(PASS, "everything was fine")  # type: ignore[call-arg]

    def test_every_exit_in_the_runner_fills_it(self) -> None:
        """Counted in the source, so a sixth exit cannot be added silently.

        The dataclass stops a verdict with *no* decider. Only this stops a
        future branch from being written with a decider copied off its
        neighbour, which is the same failure one step later.
        """

        source = (PACKAGE / "runner.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("Verdict("), source.count("decided_by="))
        self.assertEqual(source.count("Verdict("), 5)

    def test_it_names_a_field_that_actually_exists(self) -> None:
        names = {f.name for f in dataclasses.fields(Verdict)}
        for expected, verdict in self._five().items():
            self.assertIn(verdict.decided_by, names)
            self.assertEqual(verdict.decided_by, expected)

    def test_the_named_field_is_the_one_holding_the_evidence(self) -> None:
        """The point of the whole field: it says where to go and argue."""

        for expected, verdict in self._five().items():
            self.assertTrue(
                getattr(verdict, verdict.decided_by),
                f"{verdict.status} says {verdict.decided_by} decided it, "
                f"and that field is empty",
            )

    def test_the_five_exits_do_not_share_a_decider(self) -> None:
        five = self._five()
        self.assertEqual(len(five), 5)
        self.assertEqual(len({v.decided_by for v in five.values()}), 5)

    def test_block_says_which_of_its_two_rules_decided(self) -> None:
        """The case ``status`` cannot express, and the reason this exists."""

        five = self._five()
        broke, fell = five["constraint_breaks"], five["failed"]
        self.assertEqual(broke.status, BLOCK)
        self.assertEqual(fell.status, BLOCK)
        self.assertNotEqual(broke.decided_by, fell.decided_by)

    def test_it_reaches_the_ledger_and_the_chain_still_verifies(self) -> None:
        connection = store.connect(self.root / "verify.db")
        try:
            contract = contract_over(self.root, Predicate("suite", tuple(BAD)))
            verdict = judge(contract, *observe(contract, self.root))
            store.record_contract(connection, contract)
            store.record_verdict(
                connection, contract.task_id, verdict, [], observed_at=T0
            )
            row = store.history(connection, contract.task_id)[0]
            self.assertEqual(
                json.loads(row["detail_json"])["decided_by"], "failed"
            )
            self.assertTrue(store.verify(connection)["ok"])
        finally:
            connection.close()


class NoSelfReport(unittest.TestCase):
    """There is nowhere to say "done". Checked as a property of the schema."""

    def test_the_contract_has_no_field_for_a_claim_of_completion(self) -> None:
        fields = set(
            Predicate("x", ("y",)).as_dict()
        ) | set(Constraint("paths_unchanged", ("p",), "r", {"p": "d"}).as_dict())
        for banned in ("done", "completed", "status", "report", "summary", "claim"):
            self.assertNotIn(banned, fields)

    def test_the_verdict_reads_only_exit_codes_and_digests(self) -> None:
        source = (PACKAGE / "runner.py").read_text(encoding="utf-8")
        body = re.sub(r'(?s)""".*?"""', "", source)
        self.assertIn("returncode", body)
        self.assertIn("sha256", body)
        for smell in ("openai", "anthropic", "llm", "prompt"):
            self.assertNotIn(smell, body.lower())


class TheStore(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.connection = store.connect(self.root / "verify.db")
        self.contract = contract_over(self.root, Predicate("suite", tuple(OK)))
        store.record_contract(self.connection, self.contract)

    def tearDown(self) -> None:
        self.connection.close()
        super().tearDown()

    def test_a_task_is_sealed_once(self) -> None:
        with self.assertRaises(store.DuplicateTask):
            store.record_contract(self.connection, self.contract)

    def test_the_contract_round_trips_byte_exact(self) -> None:
        loaded = store.load_contract(self.connection, self.contract.task_id)
        self.assertEqual(loaded, self.contract)

    def test_every_judgement_is_kept(self) -> None:
        for _ in range(3):
            verdict = judge(self.contract, *observe(self.contract, self.root))
            store.record_verdict(
                self.connection, self.contract.task_id, verdict, [], observed_at=T0
            )
        self.assertEqual(len(store.history(self.connection, self.contract.task_id)), 3)

    def test_the_chains_verify_and_notice_an_edit(self) -> None:
        self.assertTrue(store.verify(self.connection)["ok"])
        self.connection.execute("UPDATE contract SET intent = 'rewritten'")
        self.connection.commit()
        result = store.verify(self.connection)
        self.assertFalse(result["ok"])

    def test_it_uses_the_shared_chain_not_a_private_copy(self) -> None:
        source = (PACKAGE / "store.py").read_text(encoding="utf-8")
        self.assertIn("from . import chain", source)
        self.assertNotIn("hashlib.sha256", source)


class TheCommandLine(Sandbox):
    def _task_file(self, **over) -> Path:
        spec = {
            "task_id": "T-cli",
            "intent": "leave guarded.txt alone",
            "constraints": [
                {"kind": "paths_unchanged", "paths": ["guarded.txt"], "reason": "frozen"}
            ],
            "done_when": [{"id": "suite", "command": OK}],
        }
        spec.update(over)
        path = self.root / "task.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def _args(self, *rest: str) -> list[str]:
        return [*rest, "--db", str(self.root / "v.db"), "--root", str(self.root)]

    def test_seal_then_judge_passes(self) -> None:
        self.assertEqual(
            main(self._args("seal", str(self._task_file()))), EXIT_OK
        )
        self.assertEqual(main(self._args("judge", "T-cli", "--commit")), EXIT_OK)

    def test_judge_exits_nonzero_on_a_block(self) -> None:
        main(self._args("seal", str(self._task_file())))
        (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
        self.assertEqual(main(self._args("judge", "T-cli")), EXIT_BLOCK)

    def test_judge_exits_nonzero_when_it_could_not_check(self) -> None:
        main(
            self._args(
                "seal",
                str(self._task_file(done_when=[{"id": "gone", "command": MISSING}])),
            )
        )
        self.assertEqual(main(self._args("judge", "T-cli")), EXIT_UNVERIFIABLE)

    def test_a_task_that_cannot_fail_is_refused_at_the_command_line(self) -> None:
        self.assertEqual(
            main(self._args("seal", str(self._task_file(done_when=[])))),
            EXIT_REFUSED,
        )

    def test_a_protected_path_that_is_not_there_is_refused(self) -> None:
        spec = self._task_file(
            constraints=[
                {"kind": "paths_unchanged", "paths": ["ghost.txt"], "reason": "r"}
            ]
        )
        self.assertEqual(main(self._args("seal", str(spec))), EXIT_REFUSED)

    def test_printing_the_result_cannot_be_what_fails(self) -> None:
        """Sealing the second contract crashed the CLI after it had succeeded.

        The intent line held an em-dash, the console was cp949, and the write
        raised UnicodeEncodeError — exit 1 on a contract that was already in
        the database. An operator reading that exit code runs the command
        again, which is the one thing a seal-once tool must not invite.
        """

        spec = self._task_file(
            task_id="T-dash",
            intent="build the thing — without touching the frozen path",
        )
        stream = io.TextIOWrapper(
            io.BytesIO(), encoding="cp949", errors="strict", write_through=True
        )
        saved = sys.stdout
        sys.stdout = stream
        try:
            code = main(self._args("seal", str(spec)))
        finally:
            sys.stdout = saved
        self.assertEqual(code, EXIT_OK)

    def _output(self, *rest: str) -> str:
        stream = io.StringIO()
        saved = sys.stdout
        sys.stdout = stream
        try:
            main(self._args(*rest))
        finally:
            sys.stdout = saved
        return stream.getvalue()

    def test_judge_says_what_decided(self) -> None:
        main(self._args("seal", str(self._task_file())))
        self.assertIn("decided by: passed", self._output("judge", "T-cli"))

    def test_the_two_blocks_do_not_read_the_same(self) -> None:
        """The reason the field exists, seen from a terminal.

        Both of these are ``BLOCK`` and they are not the same accusation. If
        the two lines are indistinguishable the field is recorded and useless.
        """

        main(self._args("seal", str(self._task_file(task_id="T-broke"))))
        main(
            self._args(
                "seal",
                str(
                    self._task_file(
                        task_id="T-fell",
                        done_when=[{"id": "suite", "command": BAD}],
                    )
                ),
            )
        )
        (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
        broke = self._output("judge", "T-broke", "--commit")
        (self.root / "guarded.txt").write_text("do not touch", encoding="utf-8")
        fell = self._output("judge", "T-fell", "--commit")

        self.assertIn(BLOCK, broke)
        self.assertIn(BLOCK, fell)
        self.assertIn("decided by: constraint_breaks", broke)
        self.assertIn("decided by: failed", fell)

        self.assertIn("[constraint_breaks]", self._output("log", "T-broke"))
        self.assertIn("[failed]", self._output("log", "T-fell"))

    def test_list_still_fits_a_narrow_terminal(self) -> None:
        """Its columns were sized to land under 80. Keep them there."""

        main(self._args("seal", str(self._task_file())))
        main(self._args("judge", "T-cli", "--commit"))
        for line in self._output("list").splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_a_verdict_from_before_the_field_prints_as_it_always_did(self) -> None:
        """Every verdict in a live ledger predates this field.

        Re-deriving one in the printer would put a second copy of the
        resolution order there, and guessing would let a verdict acquire a
        decider it never had. So an old row is left alone, byte for byte.
        """

        main(self._args("seal", str(self._task_file())))
        connection = store.connect(self.root / "v.db")
        detail = {
            "status": PASS,
            "reason": "1 check(s) passed and 1 protected path(s) are unchanged",
            "constraint_breaks": [],
            "failed": [],
            "unrunnable": [],
            "passed": ["suite"],
            "needs_human": [],
        }
        try:
            chain.append(
                connection,
                "verdict",
                {
                    "task_id": "T-cli",
                    "status": PASS,
                    "reason": detail["reason"],
                    "detail_json": chain.canonical_json(detail),
                    "observations_json": chain.canonical_json({"items": []}),
                    "observed_at": T0,
                },
                store._verdict_payload,
            )
            self.assertTrue(store.verify(connection)["ok"])
        finally:
            connection.close()

        logged = self._output("log", "T-cli").splitlines()[0]
        self.assertNotIn("[", logged)
        self.assertEqual(logged, f"{_stamp(T0)}  {PASS}")

    def test_nothing_it_prints_needs_a_character_the_console_may_lack(self) -> None:
        """The safety net is for the operator's text, not for our own dashes.

        ``_survive_the_console`` turns unencodable characters into '?' so the
        CLI cannot die while reporting. That is for intent lines and check
        output, which belong to whoever wrote the contract. This module's own
        literals have no such excuse, and an em-dash in the dry-run line is
        printed inside the five-minute first verdict the README sells.
        """

        tree = ast.parse((PACKAGE / "__main__.py").read_text(encoding="utf-8"))
        offenders = [
            (piece.lineno, piece.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print"
            for piece in ast.walk(node)
            if isinstance(piece, ast.Constant)
            and isinstance(piece.value, str)
            and not piece.value.isascii()
        ]
        self.assertEqual(offenders, [])

    def test_a_dry_run_reads_correctly_on_a_cp949_console(self) -> None:
        """The console that produced failure story four."""

        main(self._args("seal", str(self._task_file())))
        stream = io.TextIOWrapper(
            io.BytesIO(), encoding="cp949", errors="strict", write_through=True
        )
        saved = sys.stdout
        sys.stdout = stream
        try:
            main(self._args("judge", "T-cli"))
        finally:
            sys.stdout = saved
        printed = stream.buffer.getvalue().decode("cp949")
        self.assertIn("(dry run; pass --commit to record)", printed)
        self.assertNotIn("?", printed)

    def test_judging_without_commit_records_nothing(self) -> None:
        main(self._args("seal", str(self._task_file())))
        main(self._args("judge", "T-cli"))
        connection = store.connect(self.root / "v.db")
        try:
            self.assertEqual(store.history(connection, "T-cli"), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
