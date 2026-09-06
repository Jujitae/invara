"""The verifier, tested the way the Divergence Engine was: refusals first.

A verifier that can only approve is a rubber stamp, and a missing approval is
much easier to notice than a false one. So most of this file is about the
four ways a task is refused or blocked, and PASS is the short part at the end.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.metadata
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from invara import _version_refusal
from invara import chain
from invara import mcp
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
    _store_alias_detail,
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

    def test_every_exit_in_the_judge_fills_it(self) -> None:
        """Counted in the source, so a sixth exit cannot be added silently.

        The dataclass stops a verdict with *no* decider. Only this stops a
        future branch from being written with a decider copied off its
        neighbour, which is the same failure one step later.

        Reads ``verdict.py``: the ladder moved there when the core purity gate
        went in, and a source-counting test has to follow the source or it
        starts counting an empty file and passing.
        """

        source = (PACKAGE / "verdict.py").read_text(encoding="utf-8")
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


#: The modules that hold the verdict semantics. ``seal`` lives in one and
#: ``judge`` in the other, and :class:`TheCoreIsPure` asserts that too — a
#: gate aimed at a module that no longer holds the thing it was written for
#: goes green by looking at nothing.
CORE_MODULES = ("contract", "verdict")

#: What "pure" means, written as module names instead of as a feeling.
#: Grouped the way ``ADR-0016`` groups them: *network, clock, storage,
#: billing, auth and execution adapters live outside core*.
IMPURE_ROOTS = frozenset(
    {
        # Execution — the whole point. A core that can start a process can
        # decide something the evidence did not.
        "subprocess", "multiprocessing", "signal", "ctypes", "runpy",
        "importlib",
        # The filesystem, and the process it is running in.
        "io", "os", "sys", "pathlib", "shutil", "tempfile", "glob",
        "fileinput", "sqlite3", "dbm", "shelve", "pickle",
        # The clock. ``sealed_at`` is injected precisely so this stays out.
        "time", "datetime", "calendar", "zoneinfo",
        # The network.
        "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "imaplib",
        "poplib", "xmlrpc", "asyncio", "selectors", "select", "webbrowser",
        # Anything that answers the same question twice with two answers.
        # Not in the ADR's list, and it belongs: replay is the claim, and a
        # verdict that cannot be re-derived is not a verdict.
        "random", "secrets", "uuid", "threading",
    }
)


def module_imports(source: str) -> tuple[set[str], set[str]]:
    """What one module pulls in: outside roots, and siblings of its own package.

    ``ast`` rather than a regex or an actual import, because the question is
    about the source as written. Importing it to look would run it, and a
    module that reaches for the clock at import time is exactly the case this
    has to catch without executing.

    ``ast.walk`` and not a top-level scan: an import inside a function is
    still an import, and moving one there is the cheapest way to walk past a
    gate that only reads the header.
    """

    absolute: set[str] = set()
    siblings: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            absolute |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    siblings.add(node.module.split(".")[0])
                else:  # from . import store
                    siblings |= {alias.name for alias in node.names}
            elif node.module:
                absolute.add(node.module.split(".")[0])
    return absolute, siblings


def core_violations(modules, read) -> list[str]:
    """Every way the core reaches for the world. Empty means it does not.

    Two rules, and the second is the one that is easy to forget: a core
    module may import stdlib names that are not on the impure list, and it
    may import *other core modules* — nothing else. Purity that only holds
    for the module you are looking at is not purity, because the import one
    level down is where it would actually leak.

    ``read`` takes a module name and returns its source, so the same checker
    can be pointed at planted sources to prove it still reports.
    """

    problems: list[str] = []
    for name in modules:
        absolute, siblings = module_imports(read(name))
        for root in sorted(absolute & IMPURE_ROOTS):
            problems.append(f"{name} imports {root}")
        for sibling in sorted(siblings):
            if sibling not in modules:
                problems.append(f"{name} imports {sibling}, which is not core")
    return problems


def core_source(name: str) -> str:
    return (PACKAGE / f"{name}.py").read_text(encoding="utf-8")


class TheCoreIsPure(unittest.TestCase):
    """``ADR-0016`` invariant 1, held by a test instead of by memory.

    *INVARA core verification semantics stay deterministic and pure; network,
    clock, storage, billing, auth and execution adapters live outside core.*

    The ADR recorded that this was already true and pointed at the two
    functions. It was true of the functions and false of the modules —
    ``judge`` sat in :mod:`invara.runner`, which imports ``subprocess`` at the
    top, so the first run of this gate was red before anything was planted in
    it. That is the ADR's own invalidation condition, and what it asks for at
    that point is separation recovery rather than a feature, so ``judge`` and
    ``Observation`` moved to :mod:`invara.verdict`.

    Why the module and not the function: a per-function rule can only be
    checked by reading every function, and a rule nobody can check has already
    started drifting. This is what ``ADR-0016`` calls the seam the business
    model rests on — the same contract meaning the same thing under a CLI, an
    MCP server, a plugin, CI or a hosted API — and it is far cheaper to hold
    now than to recover later.
    """

    def test_the_core_reaches_for_nothing(self) -> None:
        self.assertEqual(core_violations(CORE_MODULES, core_source), [])

    def test_it_is_aimed_at_the_modules_that_hold_the_semantics(self) -> None:
        """A gate pointed at the wrong file is green and means nothing.

        The list above is two strings. Without this, moving ``judge`` back
        into the runner would leave the gate passing over a module that no
        longer decides anything — the failure where the command runs, the
        number is real, and it counted something else.
        """

        self.assertEqual(seal.__module__, "invara.contract")
        self.assertEqual(judge.__module__, "invara.verdict")
        self.assertEqual(
            sorted(CORE_MODULES),
            sorted({seal.__module__.split(".")[-1], judge.__module__.split(".")[-1]}),
        )

    def test_the_gate_reports_a_planted_import(self) -> None:
        """Rule 27: prove the harness can go red before believing it green.

        A checker that silently finds nothing and a core that is genuinely
        clean produce the identical empty list. The only way to tell them
        apart is to hand it something it must object to.
        """

        planted = {
            "contract": "import time\n",
            "verdict": "from .contract import Verdict\n",
        }
        self.assertEqual(
            core_violations(CORE_MODULES, planted.__getitem__),
            ["contract imports time"],
        )

    def test_it_sees_an_import_hidden_inside_a_function(self) -> None:
        planted = {
            "contract": "def now():\n    import datetime\n    return 1\n",
            "verdict": "",
        }
        self.assertEqual(
            core_violations(CORE_MODULES, planted.__getitem__),
            ["contract imports datetime"],
        )

    def test_it_sees_the_leak_one_module_down(self) -> None:
        """The interesting case. Core stays clean by importing something dirty."""

        planted = {
            "contract": "",
            "verdict": "from .runner import run_predicate\n",
        }
        self.assertEqual(
            core_violations(CORE_MODULES, planted.__getitem__),
            ["verdict imports runner, which is not core"],
        )

    def test_it_does_not_object_to_everything(self) -> None:
        """The other half of the differential: a clean source must pass.

        Without this the three tests above are also satisfied by a checker
        that reports on any input at all.
        """

        planted = {
            "contract": "from dataclasses import dataclass\nimport hashlib\n",
            "verdict": "from .contract import Verdict\n",
        }
        self.assertEqual(core_violations(CORE_MODULES, planted.__getitem__), [])


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


class TheEditorSurface(Sandbox):
    """MCP over stdio, on the standard library alone.

    The buyer cannot read code and will not open a terminal, so the surface
    has to be where they already are. These hold the two things that make
    that surface honest rather than merely present: it adds no dependency,
    and it does not talk over its own transport.
    """

    def _rpc(self, *requests: dict) -> list[dict]:
        out = io.StringIO()
        mcp.serve(io.StringIO("\n".join(json.dumps(r) for r in requests)), out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def _call(self, name: str, **arguments) -> tuple[dict, bool]:
        arguments.setdefault("db", str(self.root / "v.db"))
        arguments.setdefault("root", str(self.root))
        result = self._rpc({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })[0]["result"]
        return json.loads(result["content"][0]["text"]), result["isError"]

    def test_it_adds_no_dependency(self) -> None:
        """`dependencies = []` is a product promise, so it is a test.

        Every MCP server reaches for an SDK. Reaching for one here would end
        `uvx --from git+...` pulling only the standard library, which is the
        claim the install line makes.
        """

        tree = ast.parse((PACKAGE / "mcp.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(sorted(imported - sys.stdlib_module_names), [])

    def test_it_does_not_keep_its_own_version_by_hand(self) -> None:
        """A version kept by hand drifts, and this one did.

        The string here said 0.1.0 while PyPI served 0.1.1, so every client
        that connected was told the wrong thing about what it had just
        installed. The number belongs to ``pyproject.toml``; any copy of it
        in this module is a second copy waiting to disagree.
        """

        source = (PACKAGE / "mcp.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'"\d+\.\d+\.\d+"', source), [])

    def test_the_version_it_reports_names_the_loaded_source(self) -> None:
        # Host metadata may belong to an entirely different checkout. The
        # executing package's source identity is the reference for this surface.
        from invara import __version__

        self.assertEqual(mcp.SERVER_INFO["version"], __version__)

    def test_it_never_speaks_over_its_own_transport(self) -> None:
        """stdout is the protocol. One stray line and the client sees a corpse.

        A server that prints a warning to stdout does not look like a server
        with a warning; it looks like a server that died, because the client
        is parsing that stream as JSON-RPC.
        """

        tree = ast.parse((PACKAGE / "mcp.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                streams = [k.value for k in node.keywords if k.arg == "file"]
                self.assertTrue(
                    streams and ast.unparse(streams[0]) == "sys.stderr",
                    f"print() at line {node.lineno} is not directed at stderr",
                )

    def test_a_notification_is_not_answered(self) -> None:
        """Replying to a notification is itself a protocol violation."""

        answered = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self.assertEqual([m["id"] for m in answered], [1])

    def test_initialize_answers_in_the_version_it_was_asked_in(self) -> None:
        for asked, expected in (("2024-11-05", "2024-11-05"), ("1999-01-01", mcp.SUPPORTED[0])):
            result = self._rpc({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": asked},
            })[0]["result"]
            self.assertEqual(result["protocolVersion"], expected)
            self.assertEqual(result["serverInfo"]["name"], "invara")

    def test_every_tool_is_listed_with_a_schema(self) -> None:
        tools = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0]["result"]["tools"]
        self.assertEqual(
            sorted(t["name"] for t in tools),
            ["invara_chain", "invara_judge", "invara_list", "invara_log", "invara_replay", "invara_seal"],
        )
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"].strip())
        self.assertEqual(sorted(mcp.HANDLERS), sorted(t["name"] for t in tools))

    def test_judge_tells_the_agent_which_rule_decided(self) -> None:
        """The verdict crosses the transport intact, decider and all."""

        spec = self.root / "task.json"
        spec.write_text(json.dumps({
            "task_id": "T-mcp", "intent": "leave guarded.txt alone",
            "constraints": [{"kind": "paths_unchanged", "paths": ["guarded.txt"],
                             "reason": "frozen"}],
            "done_when": [{"id": "suite", "command": OK}],
        }), encoding="utf-8")

        sealed, errored = self._call("invara_seal", task_file=str(spec))
        self.assertFalse(errored)
        self.assertEqual(sealed["sealed"], "T-mcp")

        clean, errored = self._call("invara_judge", task_id="T-mcp", commit=True)
        self.assertFalse(errored)
        self.assertEqual((clean["status"], clean["decided_by"]), (PASS, "passed"))

        (self.root / "guarded.txt").write_text("touched", encoding="utf-8")
        broke, errored = self._call("invara_judge", task_id="T-mcp", commit=True)
        self.assertEqual((broke["status"], broke["decided_by"]), (BLOCK, "constraint_breaks"))
        self.assertFalse(broke["accepted"])
        self.assertTrue(broke["evidence"]["constraint_breaks"])

        logged, _ = self._call("invara_log", task_id="T-mcp")
        self.assertEqual(
            [(v["status"], v["decided_by"]) for v in logged["verdicts"]],
            [(PASS, "passed"), (BLOCK, "constraint_breaks")],
        )
        chain_state, _ = self._call("invara_chain")
        self.assertTrue(chain_state["ok"])

    def test_a_refusal_arrives_as_content_not_as_a_dead_transport(self) -> None:
        """`seal` refusing is the product working. The agent must be able to read it."""

        spec = self.root / "task.json"
        spec.write_text(json.dumps({
            "task_id": "T-weak", "intent": "no way to fail this",
            "constraints": [{"kind": "paths_unchanged", "paths": ["guarded.txt"],
                             "reason": "frozen"}],
            "done_when": [],
        }), encoding="utf-8")
        refusal, errored = self._call("invara_seal", task_file=str(spec))
        self.assertTrue(errored)
        self.assertEqual(refusal["refused"], "no_done_condition")

        missing, errored = self._call("invara_judge", task_id="never-sealed")
        self.assertTrue(errored)
        self.assertEqual(missing["refused"], "no_such_task")

    def test_unparseable_input_does_not_kill_the_server(self) -> None:
        out = io.StringIO()
        mcp.serve(io.StringIO('{not json\n{"jsonrpc":"2.0","id":7,"method":"ping"}'), out)
        answered = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(answered[0]["error"]["code"], -32700)
        self.assertEqual(answered[1]["id"], 7)

    def test_replay_reproduces_a_stored_verdict(self) -> None:
        """Replay recomputes the verdict from stored observations and current state."""

        (self.root / "stable.txt").write_text("unchanged", encoding="utf-8")

        spec = self.root / "task.json"
        spec.write_text(json.dumps({
            "task_id": "T-replay",
            "intent": "test replay",
            "constraints": [{"kind": "paths_unchanged", "paths": ["stable.txt"], "reason": "frozen"}],
            "done_when": [{"id": "check", "command": OK, "expect_exit": 0, "reason": "must pass"}],
        }), encoding="utf-8")

        self._call("invara_seal", task_file=str(spec))
        verdict, _ = self._call("invara_judge", task_id="T-replay", commit=True)
        self.assertEqual(verdict["status"], "PASS")

        replayed, errored = self._call("invara_replay", task_id="T-replay")
        self.assertFalse(errored)
        self.assertEqual(replayed["status"], "PASS")
        self.assertEqual(replayed["decided_by"], "passed")
        self.assertTrue(replayed["matches"])
        self.assertEqual(replayed["stored_status"], "PASS")
        self.assertEqual(replayed["stored_decided_by"], "passed")


class TheSilentAliasSpeaks(Sandbox):
    """INV-011: the failure a Python-less Windows serves at the front door.

    Measured on Windows (2026-08-29): the Microsoft Store's app-execution
    alias for ``python`` exists, spawns, and dies with exit 9009 and exactly
    seven bytes of stderr, ``"Python "`` -- and through a layer that truncates
    exit codes to eight bits the same death reads as 49. A product that
    forbids silence on the verdict path does not get to serve that silence
    itself, so both the runner (checks that spawn ``python``) and the package
    entry (an interpreter below the floor) must say what is missing, how to
    fix it, and whose fault it is not -- in English and Korean.
    """

    #: The three obligations of INV-010 §3, as substrings the message must
    #: carry: (A) what is missing by name, (B) how to fix it, (C) that it is
    #: not INVARA's defect -- the last in both languages.
    OBLIGATIONS = ("python", "python.org", "winget", "INVARA", "설치")

    def _mimic(self, exit_code: int = 49) -> list[str]:
        """A real spawn wearing the alias's measured clothes.

        ``sys.executable`` so the program is python-named on every platform;
        the child writes the seven measured bytes and dies with the measured
        (truncated) code. POSIX cannot return 9009 from a child at all, which
        is why the truncated form is the one exercised end to end.
        """

        return [
            sys.executable,
            "-c",
            f"import sys; sys.stderr.write('Python '); sys.exit({exit_code})",
        ]

    @unittest.skipUnless(os.name == "nt", "the Store alias is a Windows fact")
    def test_store_alias_interception_is_reported_as_not_run(self) -> None:
        observation = run_predicate(
            Predicate("suite", tuple(self._mimic())), self.root, timeout_s=60
        )
        self.assertFalse(observation.ran)
        self.assertIsNone(observation.exit_code)
        for needle in self.OBLIGATIONS:
            self.assertIn(needle, observation.detail)
        # Storage truncates details at 500 characters; the Korean half must
        # not be the half that pays for that.
        self.assertLessEqual(len(observation.detail), 500)

    @unittest.skipUnless(os.name == "nt", "the Store alias is a Windows fact")
    def test_store_alias_makes_the_verdict_unverifiable_not_block(self) -> None:
        """An interpreter that never ran has told us nothing.

        "Nothing" must not arrive at the verdict wearing a broken promise's
        clothes: the check was not run, so the work is unverified rather
        than judged to have failed.
        """

        sealed = contract_over(self.root, Predicate("suite", tuple(self._mimic())))
        current, observations = observe(sealed, self.root, timeout_s=60)
        verdict = judge(sealed, current, observations)
        self.assertEqual(verdict.status, UNVERIFIABLE)
        self.assertEqual(verdict.decided_by, "unrunnable")
        self.assertIn("Microsoft Store", verdict.reason)

    def test_store_alias_detection_matches_only_the_measured_signature(self) -> None:
        """The gate is as narrow as the measurement, on purpose."""

        caught = _store_alias_detail(["python"], 9009, "", "Python ", platform="nt")
        self.assertIsNotNone(caught)
        self.assertIn("9009", caught)
        also = _store_alias_detail(
            [r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"],
            49, "", "", platform="nt",
        )
        self.assertIsNotNone(also)

        for command, code, out, err in (
            (["python"], 49, "", "AssertionError: boom"),  # real output
            (["git"], 9009, "", "Python "),  # not python-named
            (["python"], 1, "", "Python "),  # not a measured exit
            ([], 9009, "", "Python "),  # nothing was spawned
        ):
            self.assertIsNone(
                _store_alias_detail(command, code, out, err, platform="nt"),
                (command, code, out, err),
            )
        # And never off Windows: exit 49 with quiet output is an honest
        # failure a POSIX child is allowed to have.
        self.assertIsNone(
            _store_alias_detail(["python"], 49, "", "Python ", platform="posix")
        )

    def test_version_floor_refusal_names_the_problem_and_the_fix(self) -> None:
        """Below 3.12 the package must refuse in words, not in a traceback.

        pip enforces ``requires-python`` but the plugin ships bundled source
        on PYTHONPATH and never asks pip, so the package itself is the last
        gate that can speak.
        """

        refusal = _version_refusal((3, 11, 9, "final", 0))
        self.assertIsNotNone(refusal)
        for needle in ("3.12", "3.11") + self.OBLIGATIONS:
            self.assertIn(needle, refusal)
        self.assertIsNone(_version_refusal(sys.version_info))

    def test_version_floor_guard_stands_before_the_package_imports(self) -> None:
        """The guard is only worth having if it runs before what it guards.

        An old interpreter dies importing the modern modules, so the refusal
        must be raised before ``__init__`` touches any of them. Held by
        source order rather than by running an old interpreter.
        """

        tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
        guarded_at = package_import_at = None
        for index, node in enumerate(tree.body):
            if guarded_at is None and isinstance(node, ast.If):
                raises = [
                    n for n in ast.walk(node)
                    if isinstance(n, ast.Raise)
                    and isinstance(n.exc, ast.Call)
                    and getattr(n.exc.func, "id", "") == "ImportError"
                ]
                if raises:
                    guarded_at = index
            if package_import_at is None and (
                isinstance(node, ast.ImportFrom) and node.level
            ):
                package_import_at = index
        self.assertIsNotNone(guarded_at, "no version guard raising ImportError")
        self.assertIsNotNone(package_import_at)
        self.assertLess(guarded_at, package_import_at)

    def test_the_plugin_bundle_carries_the_store_alias_fix(self) -> None:
        """The plugin ships this package as a copy, and a copy can drift.

        Naming only the two INV-011 modules here was too narrow, and the
        narrowness was not theoretical: `mcp.py`, `store.py` and
        `__main__.py` had already drifted behind `src/` by a whole tool
        (`invara_replay`) while this test stayed green. A copy that is
        checked in part is a copy nobody is checking.

        So the rule is now the whole package, discovered rather than listed
        — a module added to `src/invara/` and forgotten in the bundle fails
        here instead of shipping.
        """

        bundled = PACKAGE.parents[1] / "plugin" / "src" / "invara"
        names = sorted(p.name for p in PACKAGE.glob("*.py"))
        self.assertIn("__init__.py", names, "no modules discovered to compare")
        for name in names:
            self.assertTrue(
                (bundled / name).exists(),
                f"plugin/src/invara/{name} is missing from the plugin bundle",
            )
            self.assertEqual(
                (PACKAGE / name).read_bytes(),
                (bundled / name).read_bytes(),
                f"plugin/src/invara/{name} differs from src/invara/{name}",
            )

    def test_the_plugin_bundle_carries_its_own_license(self) -> None:
        """`git-subdir` distributes `plugin/` alone, not the repository.

        The directory the marketplace fetches is the whole of what the user
        receives, so a LICENSE that sits only at the repository root is a
        licence the installed plugin does not have.
        """

        root = PACKAGE.parents[1]
        for name in ("LICENSE", "NOTICE"):
            bundled = root / "plugin" / name
            self.assertTrue(bundled.exists(), f"plugin/{name} is missing")
            self.assertEqual(
                (root / name).read_bytes(),
                bundled.read_bytes(),
                f"plugin/{name} differs from {name}",
            )

    def test_the_plugin_manifest_version_matches_the_package(self) -> None:
        """Two files state the version. They are one claim, so they must agree."""

        root = PACKAGE.parents[1]
        manifest = json.loads(
            (root / "plugin" / ".claude-plugin" / "plugin.json").read_text("utf-8")
        )
        pyproject = (root / "pyproject.toml").read_text("utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
        self.assertIsNotNone(match, "no version in pyproject.toml")
        self.assertEqual(
            manifest["version"],
            match.group(1),
            "plugin.json version disagrees with pyproject.toml",
        )

    def test_the_plugin_readme_names_every_tool_it_ships(self) -> None:
        """The bundle's README is submitted material; a stale tool list is a false claim.

        It said "Five tools" while the server advertised six.
        """

        root = PACKAGE.parents[1]
        readme = (root / "plugin" / "README.md").read_text("utf-8")
        advertised = {tool["name"] for tool in mcp._tools()}
        for name in sorted(advertised):
            self.assertIn(
                f"`{name}`", readme, f"plugin/README.md does not mention {name}"
            )
        counted = {
            5: "Five tools", 6: "Six tools", 7: "Seven tools", 8: "Eight tools",
        }.get(len(advertised))
        if counted is not None:
            self.assertIn(
                counted,
                readme,
                f"plugin/README.md miscounts: {len(advertised)} tools are served",
            )


if __name__ == "__main__":
    unittest.main()
