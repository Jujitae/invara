"""The command line: every step reachable without an AI, with exit codes a shell can act on."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from _support import manifest_dict, policy
from invara.__main__ import EXIT_BLOCK, EXIT_OK, EXIT_REFUSED, EXIT_UNVERIFIABLE, build_parser, main

APP = textwrap.dedent(
    '''
    import json, sys
    data = json.load(sys.stdin)
    print(json.dumps({"doubled": [x * 2 for x in data.get("values", [])], "n": len(data.get("values", []))}))
    '''
)
APP_WRONG = APP.replace("x * 2", "x * 2 if x < 5 else x * 3")


def manifest_file(path: Path, session_id: str, **over) -> Path:
    sections = dict(
        session_id=session_id,
        source_system={"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT"},
        target_system={"same_as_source": True},
        input_domain={"kind": "corpus", "delivery": "stdin_json", "corpus": [{"id": "c1", "input": {"values": [1, 2, 8]}}, {"id": "c2", "input": {"values": []}}]},
        probes=[{"id": "cli", "adapter": "process", "capture": [], "mandatory": True}, {"id": "out", "adapter": "json", "source": "stdout", "mandatory": True}],
        policies=[],
        claims=[{"id": "corpus", "kind": "corpus_equivalence", "mandatory": True}, {"id": "search", "kind": "counterexample_search", "mandatory": True, "params": {"runs": 30, "seed": 1}}],
        budgets={"shrink_steps": 40},
    )
    sections.update(over)
    path.write_text(json.dumps(manifest_dict(**sections)), encoding="utf-8")
    return path


class Cli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.db = self.root / "verify.db"
        self.before = self.root / "before"
        self.same = self.root / "same"
        self.wrong = self.root / "wrong"
        for folder, text in ((self.before, APP), (self.same, APP), (self.wrong, APP_WRONG)):
            folder.mkdir()
            (folder / "app.py").write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str]:
        stream = io.StringIO()
        saved = sys.stdout
        sys.stdout = stream
        try:
            code = main([*args, "--db", str(self.db)])
        finally:
            sys.stdout = saved
        return code, stream.getvalue()

    def assure(self, target: Path, session_id: str = "cli") -> tuple[int, str]:
        manifest = manifest_file(self.root / f"{session_id}.json", session_id)
        return self.run_cli("assure", "init", "--manifest", str(manifest), "--source-root", str(self.before), "--target-root", str(target), "--workspace", str(self.root / "ws"))


class TheKernelIsUntouched(Cli):
    def test_the_original_commands_still_parse(self) -> None:
        parser = build_parser()
        for command in ("seal", "judge", "list", "log", "show", "chain", "init", "replay"):
            self.assertIsNotNone(parser.parse_args([command] + (["x"] if command in ("seal", "judge", "log", "show", "replay") else [])))

    def test_the_new_groups_exist(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["assure", "list"]).command, "assure")
        self.assertEqual(parser.parse_args(["repair", "status", "s"]).command, "repair")

    def test_list_is_still_the_kernel_list(self) -> None:
        code, out = self.run_cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("nothing sealed yet", out)


class AssureCommands(Cli):
    def test_init_characterize_freeze_compare_search_verify_report(self) -> None:
        code, out = self.assure(self.same)
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("cli", out)
        self.assertIn("CREATED", out)
        code, out = self.run_cli("assure", "characterize", "cli", "--runs", "2")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("BASELINE_CAPTURING", out)
        code, out = self.run_cli("assure", "freeze", "cli")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("BASELINE_FROZEN", out)
        code, out = self.run_cli("assure", "compare", "cli")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("PRESERVED_WITHIN_ENVELOPE", out)
        code, out = self.run_cli("assure", "search", "cli")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("NO_DIVERGENCE_FOUND", out)
        code, out = self.run_cli("assure", "verify", "cli")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("PASS", out)
        self.assertIn("decided by: preserved", out)
        json_path = self.root / "report.json"
        md_path = self.root / "report.md"
        code, out = self.run_cli("assure", "report", "cli", "--json", str(json_path), "--md", str(md_path))
        self.assertEqual(code, EXIT_OK, out)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["verdict"]["status"], "PASS")
        self.assertIn("기능 유지", md_path.read_text(encoding="utf-8"))
        code, out = self.run_cli("assure", "status", "cli")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out)["state"], "BASELINE_FROZEN")
        code, out = self.run_cli("assure", "complete", "cli")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("COMPLETED", out)
        code, out = self.run_cli("assure", "list")
        self.assertIn("cli", out)
        self.assertIn("COMPLETED", out)

    def test_a_divergence_exits_block_and_names_the_path(self) -> None:
        self.assure(self.wrong, "bad")
        self.run_cli("assure", "characterize", "bad")
        self.run_cli("assure", "freeze", "bad")
        code, out = self.run_cli("assure", "compare", "bad")
        self.assertEqual(code, EXIT_BLOCK, out)
        self.assertIn("/out/value/doubled", out)
        code, out = self.run_cli("assure", "search", "bad")
        self.assertEqual(code, EXIT_BLOCK)
        self.assertIn("minimized", out)
        code, out = self.run_cli("assure", "verify", "bad")
        self.assertEqual(code, EXIT_BLOCK)
        self.assertIn("BLOCK", out)

    def test_verify_before_evaluation_is_unverifiable_and_require_pass_exits_nonzero(self) -> None:
        self.assure(self.same, "early")
        self.run_cli("assure", "characterize", "early")
        self.run_cli("assure", "freeze", "early")
        code, out = self.run_cli("assure", "verify", "early")
        self.assertEqual(code, EXIT_UNVERIFIABLE)
        self.assertIn("UNVERIFIABLE", out)
        code, out = self.run_cli("assure", "verify", "early", "--require", "PASS")
        self.assertEqual(code, EXIT_BLOCK)
        self.assertIn("required PASS", out)

    def test_a_bad_manifest_is_refused_with_its_reason(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text(json.dumps(manifest_dict(session_id="x", policies=[policy("ignore", "/")])), encoding="utf-8")
        code, out = self.run_cli("assure", "init", "--manifest", str(bad), "--source-root", str(self.before), "--target-root", str(self.same))
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("root_ignore", out)

    def test_out_of_order_steps_are_refused(self) -> None:
        self.assure(self.same, "order")
        code, out = self.run_cli("assure", "compare", "order")
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("not_frozen", out)

    def test_an_amendment_is_explicit_and_recorded(self) -> None:
        self.assure(self.same, "amend")
        self.run_cli("assure", "characterize", "amend")
        self.run_cli("assure", "freeze", "amend")
        amendment = self.root / "amend.json"
        amendment.write_text(json.dumps({"requested_by": "operator", "reason": "warnings vary", "changes": {"exclusions": [{"id": "warn", "path": "/cli/stderr", "reason": "platform"}]}}), encoding="utf-8")
        code, out = self.run_cli("assure", "amend", "amend", "--amendment", str(amendment))
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("old", out)
        self.assertIn("new", out)
        code, out = self.run_cli("assure", "status", "amend")
        self.assertEqual(len(json.loads(out)["manifest_history"]), 2)


class RepairCommands(Cli):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run = lambda *a: subprocess.run(["git", *a], cwd=self.repo, capture_output=True, text=True, encoding="utf-8", check=True)  # noqa: E731
        run("init", "-q", "-b", "main")
        run("config", "user.name", "Tester")
        run("config", "user.email", "t@example.com")
        (self.repo / "app.py").write_bytes(APP.encode("utf-8"))
        run("add", "-A")
        run("commit", "-q", "-m", "initial")

    def test_the_repair_protocol_runs_end_to_end_from_the_shell(self) -> None:
        manifest = manifest_file(self.root / "rep.json", "rep")
        code, out = self.run_cli("repair", "init", "--repo", str(self.repo), "--manifest", str(manifest), "--workspace", str(self.root / "rws"))
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("CREATED", out)
        for step in (["characterize", "rep"], ["freeze", "rep"]):
            code, out = self.run_cli("repair", *step)
            self.assertEqual(code, EXIT_OK, out)
        findings = self.root / "findings.json"
        findings.write_text(json.dumps([{"id": "f1", "kind": "oversized_module", "paths": ["app.py"], "summary": "everything in one file", "declared_by": "host-agent"}]), encoding="utf-8")
        code, out = self.run_cli("repair", "analyze", "rep", "--findings", str(findings))
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("ANALYZING", out)
        plan = self.root / "plan.json"
        plan.write_text(json.dumps([{"id": "u1", "objective": "add a docstring", "reason": "f1", "risk": "low", "owned_paths": ["app.py"], "expected_behavior_impact": "none"}]), encoding="utf-8")
        code, out = self.run_cli("repair", "plan", "rep", "--plan", str(plan))
        self.assertEqual(code, EXIT_OK, out)
        code, out = self.run_cli("repair", "unit-start", "rep", "u1")
        self.assertEqual(code, EXIT_OK, out)
        worktree = Path(json.loads(out)["worktree"])
        self.assertTrue((worktree / "app.py").is_file())
        (worktree / "app.py").write_bytes(('"""Doubles values."""\n' + APP).encode("utf-8"))
        code, out = self.run_cli("repair", "unit-verify", "rep", "u1")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("PASS", out)
        code, out = self.run_cli("repair", "unit-accept", "rep", "u1")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("accepted", out)
        code, out = self.run_cli("repair", "status", "rep")
        self.assertEqual(json.loads(out)["state"], "UNIT_ACCEPTED")
        code, out = self.run_cli("repair", "finish", "rep")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("invara/repair/rep", out)
        report = self.root / "rep.md"
        code, out = self.run_cli("repair", "report", "rep", "--md", str(report))
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("정리 완료 항목", report.read_text(encoding="utf-8"))

    def test_resume_reports_the_state_and_what_to_do_next(self) -> None:
        manifest = manifest_file(self.root / "rep2.json", "rep2")
        self.run_cli("repair", "init", "--repo", str(self.repo), "--manifest", str(manifest), "--workspace", str(self.root / "rws2"))
        code, out = self.run_cli("repair", "resume", "rep2")
        self.assertEqual(code, EXIT_OK, out)
        data = json.loads(out)
        self.assertEqual(data["state"], "CREATED")
        self.assertIn("characterize", data["next"])

    def test_a_blocked_or_unverified_unit_cannot_be_accepted_from_the_shell(self) -> None:
        manifest = manifest_file(self.root / "rep3.json", "rep3")
        self.run_cli("repair", "init", "--repo", str(self.repo), "--manifest", str(manifest), "--workspace", str(self.root / "rws3"))
        self.run_cli("repair", "characterize", "rep3")
        self.run_cli("repair", "freeze", "rep3")
        self.run_cli("repair", "analyze", "rep3")
        plan = self.root / "plan3.json"
        plan.write_text(json.dumps([{"id": "u1", "objective": "break it", "reason": "test", "risk": "high", "owned_paths": ["app.py"], "expected_behavior_impact": "none"}]), encoding="utf-8")
        self.run_cli("repair", "plan", "rep3", "--plan", str(plan))
        code, out = self.run_cli("repair", "unit-start", "rep3", "u1")
        worktree = Path(json.loads(out)["worktree"])
        code, out = self.run_cli("repair", "unit-accept", "rep3", "u1")
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("unit_not_verified", out)
        (worktree / "app.py").write_bytes(APP_WRONG.encode("utf-8"))
        code, out = self.run_cli("repair", "unit-verify", "rep3", "u1")
        self.assertEqual(code, EXIT_BLOCK, out)
        code, out = self.run_cli("repair", "unit-accept", "rep3", "u1")
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("unit_not_accepted", out)
        code, out = self.run_cli("repair", "unit-reject", "rep3", "u1", "--reason", "diverged")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("UNIT_ROLLED_BACK", out)


if __name__ == "__main__":
    unittest.main()
