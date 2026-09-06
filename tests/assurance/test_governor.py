"""The repair governor: the user's tree is never in the loop."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from invara.assurance import governor as g


def git(*args: str, cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True, env=env)
    return result.stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.repo = self.base / "project"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.name", "Tester", cwd=self.repo)
        git("config", "user.email", "tester@example.com", cwd=self.repo)
        (self.repo / "app.py").write_bytes(b"def total(items):\n    return sum(items)\n")
        (self.repo / "README.md").write_bytes(b"# project\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "initial", cwd=self.repo)
        self.base_commit = git("rev-parse", "HEAD", cwd=self.repo)
        self.workspace = self.base / "workspace"
        self.governor = g.Governor(self.repo, workspace=self.workspace)

    def tearDown(self) -> None:
        for worktree in list(self.workspace.rglob("*")) if self.workspace.exists() else []:
            pass
        try:
            subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)
        except OSError:
            pass
        self._tmp.cleanup()

    def user_state(self) -> tuple[str, str, str]:
        return (
            git("branch", "--show-current", cwd=self.repo),
            git("rev-parse", "HEAD", cwd=self.repo),
            git("status", "--porcelain", "--untracked-files=all", cwd=self.repo),
        )


class Initialising(Repo):
    def test_a_dirty_tree_is_refused_and_nothing_is_created(self) -> None:
        (self.repo / "scratch.txt").write_bytes(b"uncommitted")
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.init_session("s1")
        self.assertEqual(caught.exception.reason, "dirty_tree")
        self.assertIn("scratch.txt", caught.exception.detail)
        self.assertFalse(self.workspace.exists())
        self.assertEqual(git("for-each-ref", "refs/invara", cwd=self.repo), "")

    def test_init_records_the_base_and_creates_an_isolated_baseline(self) -> None:
        before = self.user_state()
        info = self.governor.init_session("s1")
        self.assertEqual(info["base_commit"], self.base_commit)
        self.assertEqual(info["accepted_ref"], "refs/invara/repair/s1/accepted")
        self.assertEqual(git("rev-parse", "refs/invara/repair/s1/accepted", cwd=self.repo), self.base_commit)
        baseline = Path(info["baseline_root"])
        self.assertTrue((baseline / "app.py").is_file())
        self.assertEqual(git("rev-parse", "HEAD", cwd=baseline), self.base_commit)
        self.assertTrue(self.governor.recognized_worktree(baseline))
        self.assertEqual(self.user_state(), before)

    def test_a_session_cannot_be_initialised_twice(self) -> None:
        self.governor.init_session("s1")
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.init_session("s1")
        self.assertEqual(caught.exception.reason, "session_exists")

    def test_a_missing_git_is_unavailable_not_a_crash(self) -> None:
        broken = g.Governor(self.repo, workspace=self.workspace, git="invara-no-such-git")
        with self.assertRaises(g.GovernorError) as caught:
            broken.init_session("s1")
        self.assertEqual(caught.exception.reason, "git_unavailable")

    def test_a_directory_that_is_not_a_repository_is_refused(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        with self.assertRaises(g.GovernorError) as caught:
            g.Governor(plain, workspace=self.workspace).init_session("s1")
        self.assertEqual(caught.exception.reason, "not_a_repository")


class Units(Repo):
    def setUp(self) -> None:
        super().setUp()
        self.governor.init_session("s1")

    def test_a_unit_gets_a_disposable_worktree_at_the_accepted_commit(self) -> None:
        before = self.user_state()
        root = self.governor.start_unit("s1", "u1")
        self.assertTrue((root / "app.py").is_file())
        self.assertEqual(git("rev-parse", "HEAD", cwd=root), self.base_commit)
        self.assertTrue(self.governor.recognized_worktree(root))
        self.assertEqual(self.user_state(), before)

    def test_a_colliding_directory_is_refused_and_left_alone(self) -> None:
        foreign = self.governor.unit_root("s1", "u1")
        foreign.mkdir(parents=True)
        (foreign / "precious.txt").write_bytes(b"do not delete")
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.start_unit("s1", "u1")
        self.assertEqual(caught.exception.reason, "worktree_collision")
        self.assertTrue((foreign / "precious.txt").is_file())

    def test_the_tree_and_changed_paths_follow_the_edits(self) -> None:
        root = self.governor.start_unit("s1", "u1")
        untouched = self.governor.unit_tree("s1", "u1")
        (root / "app.py").write_bytes(b"def total(items):\n    return sum(items)  # cleaned\n")
        (root / "lib" ).mkdir()
        (root / "lib" / "util.py").write_bytes(b"X = 1\n")
        edited = self.governor.unit_tree("s1", "u1")
        self.assertNotEqual(untouched, edited)
        self.assertEqual(self.governor.unit_changed_paths("s1", "u1"), ["app.py", "lib/util.py"])

    def test_accepting_promotes_the_exact_verified_tree_and_moves_only_the_session_ref(self) -> None:
        before = self.user_state()
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"def total(items):\n    return sum(items)  # cleaned\n")
        tree = self.governor.unit_tree("s1", "u1")
        commit = self.governor.accept_unit("s1", "u1", message="u1: clean total", expected_tree=tree)
        self.assertEqual(git("rev-parse", "refs/invara/repair/s1/accepted", cwd=self.repo), commit)
        self.assertEqual(git("rev-parse", f"{commit}^", cwd=self.repo), self.base_commit)
        self.assertEqual(git("rev-parse", f"{commit}^{{tree}}", cwd=self.repo), tree)
        self.assertIn("cleaned", git("show", f"{commit}:app.py", cwd=self.repo))
        self.assertFalse(root.exists(), "the unit worktree is disposed of after acceptance")
        self.assertEqual(self.user_state(), before)
        self.assertEqual(self.governor.accepted_commit("s1"), commit)

    def test_accepting_a_tree_that_changed_since_verification_is_refused(self) -> None:
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"v1\n")
        verified = self.governor.unit_tree("s1", "u1")
        (root / "app.py").write_bytes(b"v2 after verification\n")
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.accept_unit("s1", "u1", message="u1", expected_tree=verified)
        self.assertEqual(caught.exception.reason, "stale_verification")
        self.assertEqual(self.governor.accepted_commit("s1"), self.base_commit)
        self.assertTrue(root.exists())

    def test_rejecting_keeps_the_patch_and_the_accepted_state_exactly(self) -> None:
        accepted_tree = git("rev-parse", f"{self.base_commit}^{{tree}}", cwd=self.repo)
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"def total(items):\n    return sum(items) + 1\n")
        (root / "new.py").write_bytes(b"NEW = True\n")
        evidence = self.base / "evidence"
        record = self.governor.reject_unit("s1", "u1", evidence_dir=evidence)
        patch = Path(record["patch_path"])
        self.assertTrue(patch.is_file())
        content = patch.read_bytes()
        self.assertIn(b"+    return sum(items) + 1", content)
        self.assertIn(b"new.py", content)
        self.assertEqual(record["patch_digest"], hashlib.sha256(content).hexdigest())
        self.assertEqual(sorted(record["changed_paths"]), ["app.py", "new.py"])
        self.assertFalse(root.exists())
        self.assertEqual(self.governor.accepted_commit("s1"), self.base_commit)
        self.assertEqual(git("rev-parse", f"{self.base_commit}^{{tree}}", cwd=self.repo), accepted_tree)

    def test_the_second_unit_starts_from_the_first_accepted_commit(self) -> None:
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"first\n")
        commit = self.governor.accept_unit("s1", "u1", message="u1", expected_tree=self.governor.unit_tree("s1", "u1"))
        second = self.governor.start_unit("s1", "u2")
        self.assertEqual(git("rev-parse", "HEAD", cwd=second), commit)
        self.assertEqual((second / "app.py").read_bytes(), b"first\n")

    def test_a_rejected_unit_does_not_contaminate_a_later_accepted_one(self) -> None:
        bad = self.governor.start_unit("s1", "u1")
        (bad / "app.py").write_bytes(b"bad\n")
        self.governor.reject_unit("s1", "u1", evidence_dir=self.base / "evidence")
        good = self.governor.start_unit("s1", "u2")
        self.assertEqual((good / "app.py").read_bytes(), b"def total(items):\n    return sum(items)\n")
        (good / "README.md").write_bytes(b"# project cleaned\n")
        commit = self.governor.accept_unit("s1", "u2", message="u2", expected_tree=self.governor.unit_tree("s1", "u2"))
        self.assertEqual(git("show", f"{commit}:app.py", cwd=self.repo), "def total(items):\n    return sum(items)")

    def test_commits_carry_an_identity_even_when_the_repository_has_none(self) -> None:
        empty = self.base / "empty-gitconfig"
        empty.write_bytes(b"")
        git("config", "--unset", "user.name", cwd=self.repo)
        git("config", "--unset", "user.email", cwd=self.repo)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1")
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
            env.pop(name, None)
        governor = g.Governor(self.repo, workspace=self.workspace, env=env)
        root = governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"identity\n")
        commit = governor.accept_unit("s1", "u1", message="u1", expected_tree=governor.unit_tree("s1", "u1"))
        author = git("log", "-1", "--format=%an <%ae>", commit, cwd=self.repo, env=env)
        self.assertIn("INVARA", author)


class Safety(Repo):
    def test_an_unrecognised_directory_is_never_removed(self) -> None:
        self.governor.init_session("s1")
        stray = self.workspace / "s1" / "units" / "stray"
        stray.mkdir(parents=True)
        (stray / "keep.txt").write_bytes(b"keep")
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.remove_worktree(stray)
        self.assertEqual(caught.exception.reason, "unrecognized_worktree")
        self.assertTrue((stray / "keep.txt").is_file())

    def test_a_worktree_the_user_made_is_not_recognised(self) -> None:
        self.governor.init_session("s1")
        theirs = self.base / "their-worktree"
        git("worktree", "add", "-q", "--detach", str(theirs), self.base_commit, cwd=self.repo)
        self.assertFalse(self.governor.recognized_worktree(theirs))
        with self.assertRaises(g.GovernorError):
            self.governor.remove_worktree(theirs)
        self.assertTrue((theirs / "app.py").is_file())

    def test_nothing_ever_runs_git_clean_or_a_hard_reset(self) -> None:
        source = Path(g.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"clean"', source)
        self.assertNotIn("--hard", source)
        self.assertNotIn("shell=True", source)


class Finishing(Repo):
    def test_finish_publishes_a_branch_and_disposes_of_the_baseline(self) -> None:
        before = self.user_state()
        info = self.governor.init_session("s1")
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"done\n")
        commit = self.governor.accept_unit("s1", "u1", message="u1", expected_tree=self.governor.unit_tree("s1", "u1"))
        branch = self.governor.finish("s1")
        self.assertEqual(branch, "invara/repair/s1")
        self.assertEqual(git("rev-parse", "refs/heads/invara/repair/s1", cwd=self.repo), commit)
        self.assertFalse(Path(info["baseline_root"]).exists())
        self.assertEqual(self.user_state(), before)

    def test_finish_refuses_to_move_an_existing_branch(self) -> None:
        self.governor.init_session("s1")
        git("branch", "invara/repair/s1", self.base_commit, cwd=self.repo)
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"x\n")
        self.governor.accept_unit("s1", "u1", message="u1", expected_tree=self.governor.unit_tree("s1", "u1"))
        with self.assertRaises(g.GovernorError) as caught:
            self.governor.finish("s1")
        self.assertEqual(caught.exception.reason, "branch_exists")


class Resuming(Repo):
    def test_a_fresh_governor_finds_the_session_where_it_was_left(self) -> None:
        self.governor.init_session("s1")
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"half done\n")
        again = g.Governor(self.repo, workspace=self.workspace)
        self.assertEqual(again.resume_checks("s1", accepted_commit=self.base_commit, unit_id="u1"), [])
        self.assertEqual((again.unit_root("s1", "u1") / "app.py").read_bytes(), b"half done\n")
        self.assertEqual(again.unit_changed_paths("s1", "u1"), ["app.py"])

    def test_a_vanished_unit_worktree_is_reported_not_recreated(self) -> None:
        self.governor.init_session("s1")
        root = self.governor.start_unit("s1", "u1")
        self.governor.remove_worktree(root)
        problems = g.Governor(self.repo, workspace=self.workspace).resume_checks("s1", accepted_commit=self.base_commit, unit_id="u1")
        self.assertTrue(any("worktree" in p for p in problems), problems)

    def test_an_accepted_ref_that_moved_behind_our_back_is_reported(self) -> None:
        self.governor.init_session("s1")
        git("update-ref", "refs/invara/repair/s1/accepted", self.base_commit + "", cwd=self.repo)
        other = git("commit-tree", f"{self.base_commit}^{{tree}}", "-m", "elsewhere", cwd=self.repo)
        git("update-ref", "refs/invara/repair/s1/accepted", other, cwd=self.repo)
        problems = g.Governor(self.repo, workspace=self.workspace).resume_checks("s1", accepted_commit=self.base_commit, unit_id=None)
        self.assertTrue(any("accepted ref" in p for p in problems), problems)

    def test_an_interrupted_acceptance_is_reconciled_from_the_verified_tree(self) -> None:
        """Crash between the ref update and the event write: the ref is ahead by exactly the verified tree."""

        self.governor.init_session("s1")
        root = self.governor.start_unit("s1", "u1")
        (root / "app.py").write_bytes(b"accepted but unrecorded\n")
        tree = self.governor.unit_tree("s1", "u1")
        commit = git("commit-tree", tree, "-p", self.base_commit, "-m", "u1", cwd=self.repo)
        git("update-ref", "refs/invara/repair/s1/accepted", commit, self.base_commit, cwd=self.repo)
        again = g.Governor(self.repo, workspace=self.workspace)
        reconciled = again.reconcile_acceptance("s1", accepted_commit=self.base_commit, verified_tree=tree)
        self.assertEqual(reconciled, commit)
        self.assertIsNone(again.reconcile_acceptance("s1", accepted_commit=self.base_commit, verified_tree="0" * 40))


if __name__ == "__main__":
    unittest.main()
