"""The repair governor: git worktrees as the boundary around every unit.

The host agent edits code; this module decides where it may do so and what
happens to the result. The user's working tree is never in the loop:

* a session starts only from a clean, identified tree, records the base
  commit, and keeps the accepted state in a ref of its own
  (``refs/invara/repair/<session>/accepted``);
* the baseline is captured in a detached worktree at the base commit;
* every unit gets a disposable detached worktree at the accepted commit,
  with a marker file beside it that says whose it is — and the marker is
  never the authority for anything that matters: the diff base and the
  commit parent come from the ref, recognition also requires git's own
  registry, a real (non-symlinked) path inside the session's workspace,
  and never a path inside the user's checkout;
* acceptance promotes the exact tree that was verified — the same tree
  hash, or the acceptance is refused as stale — into a commit whose parent
  is the accepted commit, and advances the ref with a compare-and-swap;
* rejection saves the unit's patch as evidence, anchors the rejected tree
  under a ref so it stays resolvable, and removes the worktree;
* nothing is ever removed that the governor did not create and cannot
  recognise, and no command here discards uncommitted changes anywhere.

Commands are argv lists run without a shell, with the ``GIT_*`` variables
that could redirect them stripped from the environment. Git being absent is
``git_unavailable``, which the caller turns into ``UNVERIFIABLE``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["Governor", "GovernorError", "MARKER_SUFFIX", "OWN_ARTIFACTS", "ZERO_OID"]

MARKER_SUFFIX = ".invara.json"
ZERO_OID = "0" * 40
FALLBACK_IDENTITY = {"name": "INVARA repair governor", "email": "invara@localhost"}
#: Untracked paths INVARA itself creates in a repository; they do not make
#: the tree dirty for the purpose of starting a session.
OWN_ARTIFACTS = (".invara", ".runtime")
_REDIRECTING_GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)


class GovernorError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _real(path: str | Path) -> Path:
    return Path(os.path.realpath(str(path)))


def _has_link_component(path: str | Path) -> bool:
    """True when resolving the path changes it: a symlink or junction is in the way."""

    absolute = Path(os.path.abspath(str(path)))
    return os.path.normcase(str(_real(absolute))) != os.path.normcase(str(absolute))


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


#: On every git call: the repository's bytes, not a platform's line endings,
#: and paths as they are, not C-quoted (a Korean filename must match its glob).
_GIT_CONFIG = ("-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "core.quotePath=false")


class Governor:
    def __init__(
        self,
        repo_root: str | Path,
        *,
        workspace: str | Path | None = None,
        git: str = "git",
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        timeout_s: float = 120.0,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.git_program = git
        self.env = dict(env) if env is not None else None
        self.clock = clock
        self.timeout_s = timeout_s
        self._workspace = Path(workspace).resolve() if workspace is not None else None
        self._toplevel: Path | None = None

    # ------------------------------------------------------------------ git

    def _environment(self, env: dict[str, str] | None) -> dict[str, str]:
        base = dict(env if env is not None else (self.env if self.env is not None else os.environ))
        for name in _REDIRECTING_GIT_VARIABLES:
            base.pop(name, None)
        return base

    def _git(self, *args: str, cwd: str | Path | None = None, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        try:
            # core.autocrlf=false and core.eol=lf on every call: a worktree the
            # governor checks out must carry the repository's bytes, not a
            # platform's line endings. In-tree attributes are checked by the
            # round-trip assertion after every checkout.
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.git_program, *_GIT_CONFIG, *args],
                cwd=str(cwd or self.repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._environment(env),
                timeout=self.timeout_s,
            )
        except FileNotFoundError:
            raise GovernorError("git_unavailable", f"{self.git_program} was not found; repair sessions need git") from None
        except OSError as error:
            raise GovernorError("git_unavailable", f"{self.git_program} could not start: {error}") from None
        except subprocess.TimeoutExpired:
            raise GovernorError("git_timeout", f"git {' '.join(args)} exceeded {self.timeout_s:g}s") from None
        if check and result.returncode != 0:
            raise GovernorError("git_failed", f"git {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
        return result

    def _git_bytes(self, *args: str, cwd: str | Path | None = None) -> bytes:
        """A git call whose output is bytes to keep as they are (a patch)."""

        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.git_program, *_GIT_CONFIG, *args],
                cwd=str(cwd or self.repo_root),
                capture_output=True,
                env=self._environment(None),
                timeout=self.timeout_s,
            )
        except FileNotFoundError:
            raise GovernorError("git_unavailable", f"{self.git_program} was not found; repair sessions need git") from None
        except OSError as error:
            raise GovernorError("git_unavailable", f"{self.git_program} could not start: {error}") from None
        except subprocess.TimeoutExpired:
            raise GovernorError("git_timeout", f"git {' '.join(args)} exceeded {self.timeout_s:g}s") from None
        if result.returncode != 0:
            raise GovernorError("git_failed", f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
        return result.stdout

    def _changed_between(self, base: str, tree: str, cwd: Path) -> list[str]:
        """Every path that differs between a commit and a tree: renames as a deletion and an addition, names verbatim."""

        out = self._git_bytes("diff", "--name-only", "--no-renames", "-z", base, tree, cwd=cwd).decode("utf-8", "replace")
        return sorted(name for name in out.split("\0") if name)

    def toplevel(self) -> Path:
        if self._toplevel is None:
            result = self._git("rev-parse", "--show-toplevel", check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                if "not a git repository" in detail.lower():
                    raise GovernorError("not_a_repository", f"{self.repo_root} is not inside a git repository")
                raise GovernorError("git_failed", f"git rev-parse --show-toplevel: {detail}")
            self._toplevel = Path(result.stdout.strip()).resolve()
        return self._toplevel

    @property
    def workspace_root(self) -> Path:
        if self._workspace is not None:
            return self._workspace
        try:
            top = self.toplevel()
        except GovernorError:
            top = self.repo_root
        return Path(str(top) + ".invara-repair")

    def head(self) -> str:
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip()

    def ignored_entries(self, root: str | Path) -> list[str]:
        """Paths present in ``root`` that git ignores: they were seen by the verification, they will not be in the commit."""

        out = self._git("status", "--porcelain", "--ignored=matching", "--untracked-files=all", "--", ".", *(f":(exclude){name}" for name in OWN_ARTIFACTS), cwd=root).stdout
        return sorted(line[3:].strip() for line in out.splitlines() if line.startswith("!!"))

    def status_entries(self) -> list[str]:
        """Untracked or modified paths, ignoring the artifacts INVARA itself writes."""

        out = self._git("status", "--porcelain", "--untracked-files=all", "--", ".", *(f":(exclude){name}" for name in OWN_ARTIFACTS)).stdout
        return [line for line in out.splitlines() if line.strip()]

    def _rev(self, name: str) -> str | None:
        result = self._git("rev-parse", "--verify", "--quiet", name, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    # ---------------------------------------------------------------- paths

    @staticmethod
    def ref_name(session_id: str) -> str:
        return f"refs/invara/repair/{session_id}/accepted"

    @staticmethod
    def rejected_ref(session_id: str, unit_id: str) -> str:
        return f"refs/invara/repair/{session_id}/rejected/{unit_id}"

    def session_dir(self, session_id: str) -> Path:
        return self.workspace_root / session_id

    def baseline_root(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "baseline"

    def unit_root(self, session_id: str, unit_id: str) -> Path:
        return self.session_dir(session_id) / "units" / unit_id

    def evidence_root(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "evidence"

    # -------------------------------------------------------------- markers

    @staticmethod
    def _marker_path(worktree: Path) -> Path:
        return Path(str(worktree) + MARKER_SUFFIX)

    def _read_marker(self, worktree: Path) -> dict[str, Any] | None:
        marker = self._marker_path(worktree)
        if not marker.is_file():
            return None
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _registered_worktrees(self) -> set[Path]:
        out = self._git("worktree", "list", "--porcelain").stdout
        found: set[Path] = set()
        for line in out.splitlines():
            if line.startswith("worktree "):
                found.add(_real(line[len("worktree "):].strip()))
        return found

    def recognized_worktree(self, path: str | Path) -> bool:
        """Ours, by every test we can make: a real path inside the session's
        workspace, outside the user's checkout, with our marker, and in git's
        own registry. A planted marker beside a symlink is not ours."""

        candidate = Path(os.path.abspath(str(path)))
        if _has_link_component(candidate):
            return False
        worktree = _real(candidate)
        marker = self._read_marker(worktree)
        if marker is None:
            return False
        # a marker that does not name its path and repository names nothing:
        # an empty string would resolve to the current directory
        if not all(isinstance(marker.get(key), str) and marker.get(key) for key in ("path", "repository", "session_id")):
            return False
        try:
            top = self.toplevel()
            same_path = os.path.normcase(str(_real(str(marker.get("path", ""))))) == os.path.normcase(str(worktree))
            same_repo = os.path.normcase(str(_real(str(marker.get("repository", ""))))) == os.path.normcase(str(top))
            session_id = str(marker.get("session_id", ""))
            in_workspace = bool(session_id) and _inside(worktree, _real(self.session_dir(session_id)))
            outside_checkout = not _inside(worktree, _real(top))
        except OSError:
            return False
        return same_path and same_repo and in_workspace and outside_checkout and worktree in self._registered_worktrees()

    def _write_marker(self, path: Path, commit: str, payload: dict[str, Any]) -> None:
        record = dict(payload)
        record.update({"repository": str(self.toplevel()), "path": str(path), "base_commit": commit, "created_at": self.clock()})
        self._marker_path(path).write_bytes(json.dumps(record, sort_keys=True, indent=1).encode("utf-8"))

    def _assert_faithful(self, path: Path, commit: str) -> None:
        """A fresh checkout must write back to the commit's own tree, or line
        endings, filters or a sparse checkout are changing bytes under us."""

        self._git("add", "-A", cwd=path)
        tree = self._git("write-tree", cwd=path).stdout.strip()
        expected = self._git("rev-parse", f"{commit}^{{tree}}").stdout.strip()
        if tree != expected:
            self._git("worktree", "remove", "--force", str(path), check=False)
            self._git("worktree", "prune", check=False)
            try:
                self._marker_path(path).unlink()
            except OSError:
                pass
            raise GovernorError(
                "checkout_not_faithful",
                f"a fresh checkout of {commit[:12]} does not write back to its own tree ({tree[:12]} != {expected[:12]}); line-ending attributes, filters or a sparse checkout would change bytes under verification",
            )

    def _add_worktree(self, path: Path, commit: str, payload: dict[str, Any]) -> Path:
        path = Path(os.path.abspath(str(path)))
        marker = self._read_marker(path)
        registered = _real(path) in self._registered_worktrees() if path.exists() else False
        if path.exists():
            if marker is not None and marker.get("base_commit") == commit and marker.get("unit_id") == payload.get("unit_id") and self.recognized_worktree(path):
                return _real(path)
            if registered and marker is None and _inside(_real(path), _real(self.session_dir(str(payload.get("session_id", ""))))):
                head = self._git("rev-parse", "--verify", "HEAD", cwd=path, check=False)
                if head.returncode == 0 and head.stdout.strip() == commit:
                    # git knows this worktree and it sits at the unit's base:
                    # a marker that was lost, or an add that was interrupted
                    # after git finished. It is adopted as it stands. The
                    # round-trip check is for fresh checkouts only: here the
                    # worktree may already hold work, and a check that fails
                    # by removing it would destroy that work. Anything that
                    # differs from the base shows up as a changed path.
                    self._write_marker(path, commit, payload)
                    return _real(path)
            raise GovernorError("worktree_collision", f"{path} already exists and is not a worktree this governor created; refusing to touch it")
        if marker is not None or self._marker_path(path).exists():
            # a marker without its worktree: a removal that lost its last step
            self._git("worktree", "prune", check=False)
            try:
                self._marker_path(path).unlink()
            except OSError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_marker(path, commit, payload)
        try:
            self._git("worktree", "add", "--detach", str(path), commit)
        except GovernorError:
            try:
                self._marker_path(path).unlink()
            except OSError:
                pass
            raise
        self._assert_faithful(path, commit)
        return _real(path)

    def remove_worktree(self, path: str | Path) -> None:
        if not self.recognized_worktree(path):
            raise GovernorError("unrecognized_worktree", f"{path} was not created by this governor; it is not removed")
        worktree = _real(path)
        self._git("worktree", "remove", "--force", str(worktree))
        try:
            self._marker_path(worktree).unlink()
        except OSError:
            pass
        self._git("worktree", "prune", check=False)

    # ------------------------------------------------------------- sessions

    def init_session(self, session_id: str) -> dict[str, Any]:
        top = self.toplevel()
        if _inside(_real(self.workspace_root), _real(top)):
            raise GovernorError("workspace_inside_repository", f"the session workspace {self.workspace_root} lies inside the repository; it would dirty the tree and a clean would delete it")
        entries = self.status_entries()
        if entries:
            raise GovernorError(
                "dirty_tree",
                "the working tree has uncommitted or untracked changes; commit or set them aside first:\n" + "\n".join(entries[:20]),
            )
        ref = self.ref_name(session_id)
        if self._rev(ref) is not None:
            raise GovernorError("session_exists", f"{ref} already exists; resume the session, or remove the ref with `git update-ref -d {ref}` if it is a leftover")
        base = self.head()
        baseline = self._add_worktree(self.baseline_root(session_id), base, {"kind": "baseline", "session_id": session_id, "unit_id": None})
        try:
            self._git("update-ref", ref, base, ZERO_OID)
        except GovernorError:
            self.remove_worktree(baseline)
            raise
        return {
            "session_id": session_id,
            "repository": str(top),
            "base_commit": base,
            "accepted_ref": ref,
            "workspace": str(_real(self.session_dir(session_id))),
            "baseline_root": str(baseline),
        }

    def accepted_commit(self, session_id: str) -> str:
        commit = self._rev(self.ref_name(session_id))
        if commit is None:
            raise GovernorError("no_session", f"{self.ref_name(session_id)} does not exist")
        return commit

    def base_commit(self, session_id: str) -> str | None:
        marker = self._read_marker(self.baseline_root(session_id))
        return str(marker["base_commit"]) if marker and marker.get("base_commit") else None

    def assert_pristine(self, path: str | Path, commit: str) -> None:
        """The worktree is still exactly the commit it was created from."""

        worktree = Path(path)
        if not self.recognized_worktree(worktree):
            raise GovernorError("worktree_moved", f"{worktree} is not a recognised worktree any more")
        head = self._git("rev-parse", "--verify", "HEAD", cwd=worktree).stdout.strip()
        if head != commit:
            raise GovernorError("worktree_moved", f"{worktree} is at {head[:12]}, not {commit[:12]}")
        dirty = [line for line in self._git("status", "--porcelain", "--untracked-files=all", cwd=worktree).stdout.splitlines() if line.strip()]
        if dirty:
            raise GovernorError("worktree_dirty", f"{worktree} was modified after it was created: " + "; ".join(dirty[:5]))

    # ---------------------------------------------------------------- units

    def start_unit(self, session_id: str, unit_id: str) -> Path:
        accepted = self.accepted_commit(session_id)
        return self._add_worktree(self.unit_root(session_id, unit_id), accepted, {"kind": "unit", "session_id": session_id, "unit_id": unit_id})

    def _unit(self, session_id: str, unit_id: str, *, base: str | None = None) -> tuple[Path, dict[str, Any], str]:
        root = self.unit_root(session_id, unit_id)
        if not self.recognized_worktree(root):
            raise GovernorError("no_unit_worktree", f"no recognised worktree for unit {unit_id!r} at {root}")
        real = _real(root)
        marker = self._read_marker(real) or {}
        expected = base or self.accepted_commit(session_id)
        if marker.get("base_commit") != expected:
            raise GovernorError("marker_tampered", f"the marker beside {real} names base {str(marker.get('base_commit'))[:12]} but the unit's base is {expected[:12]}")
        return real, marker, expected

    def _gitlinks(self, tree: str) -> set[str]:
        out = self._git("ls-tree", "-r", tree).stdout
        return {line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("160000 ")}

    def unit_tree(self, session_id: str, unit_id: str, *, base: str | None = None) -> str:
        root, _, expected = self._unit(session_id, unit_id, base=base)
        self._git("add", "-A", cwd=root)
        tree = self._git("write-tree", cwd=root).stdout.strip()
        new_links = self._gitlinks(tree) - self._gitlinks(f"{expected}^{{tree}}")
        if new_links:
            raise GovernorError("nested_repository", f"unit {unit_id!r} contains a nested git repository at {', '.join(sorted(new_links))}; it cannot be recorded as a tree")
        return tree

    def unit_changed_paths(self, session_id: str, unit_id: str, *, base: str | None = None) -> list[str]:
        root, _, expected = self._unit(session_id, unit_id, base=base)
        tree = self.unit_tree(session_id, unit_id, base=base)
        return self._changed_between(expected, tree, root)

    def _identity_env(self) -> dict[str, str] | None:
        name = self._git("config", "--get", "user.name", check=False)
        email = self._git("config", "--get", "user.email", check=False)
        if name.returncode == 0 and name.stdout.strip() and email.returncode == 0 and email.stdout.strip():
            return self.env
        env = dict(self.env if self.env is not None else os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": FALLBACK_IDENTITY["name"],
                "GIT_AUTHOR_EMAIL": FALLBACK_IDENTITY["email"],
                "GIT_COMMITTER_NAME": FALLBACK_IDENTITY["name"],
                "GIT_COMMITTER_EMAIL": FALLBACK_IDENTITY["email"],
            }
        )
        return env

    def accept_unit(self, session_id: str, unit_id: str, *, message: str, expected_tree: str, reviewed_by: str | None = None, base: str | None = None) -> str:
        root, _, expected = self._unit(session_id, unit_id, base=base)
        tree = self.unit_tree(session_id, unit_id, base=base)
        if tree != expected_tree:
            raise GovernorError("stale_verification", f"unit {unit_id!r} tree {tree[:12]} is not the verified tree {expected_tree[:12]}; verify again before accepting")
        accepted = self.accepted_commit(session_id)
        if accepted != expected:
            raise GovernorError("accepted_moved", f"the accepted state is {accepted[:12]} but unit {unit_id!r} started from {expected[:12]}")
        text = message if not reviewed_by else f"{message}\n\nReviewed-by: {reviewed_by}"
        commit = self._git("commit-tree", tree, "-p", expected, "-m", text, env=self._identity_env()).stdout.strip()
        self._git("update-ref", self.ref_name(session_id), commit, expected)
        self.remove_worktree(root)
        return commit

    def reject_unit(
        self, session_id: str, unit_id: str, *, evidence_dir: str | Path, reason: str = "", base: str | None = None, remove: bool = True
    ) -> dict[str, Any]:
        """Keep the unit's patch and anchor its tree; remove the worktree only when asked.

        The workflow records the rejection first and removes the worktree
        after, so an interruption between the two leaves a retryable state.
        Calling this twice for the same tree is harmless: the patch is the
        same bytes and the anchor is reused.
        """

        root, _, expected = self._unit(session_id, unit_id, base=base)
        self._git("add", "-A", cwd=root)
        tree = self._git("write-tree", cwd=root).stdout.strip()
        changed = self._changed_between(expected, tree, root)
        patch = self._git_bytes("diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", expected, tree, cwd=root)
        target = Path(evidence_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{session_id}-{unit_id}.patch"
        path.write_bytes(patch)
        ref = self.rejected_ref(session_id, unit_id)
        existing = self._rev(ref)
        if existing is not None and self._rev(f"{existing}^{{tree}}") == tree:
            anchor = existing
        else:
            anchor = self._git("commit-tree", tree, "-p", expected, "-m", f"rejected unit {unit_id}: {reason}", env=self._identity_env()).stdout.strip()
            self._git("update-ref", ref, anchor)
        if remove:
            self.remove_worktree(root)
        return {
            "unit_id": unit_id,
            "reason": reason,
            "tree": tree,
            "base_commit": expected,
            "anchor_commit": anchor,
            "patch_path": str(path),
            "patch_digest": hashlib.sha256(patch).hexdigest(),
            "changed_paths": changed,
        }

    # --------------------------------------------------------------- finish

    def finish(self, session_id: str, branch: str | None = None, *, expected_commit: str | None = None) -> str:
        name = branch or f"invara/repair/{session_id}"
        if name.startswith("-") or self._git("check-ref-format", "--branch", name, check=False).returncode != 0:
            raise GovernorError("bad_branch_name", f"{name!r} is not a valid branch name")
        commit = self.accepted_commit(session_id)
        if expected_commit is not None and commit != expected_commit:
            raise GovernorError("accepted_moved", f"the accepted ref is at {commit[:12]} but the session recorded {expected_commit[:12]}; nothing is published")
        existing = self._rev(f"refs/heads/{name}")
        if existing is not None and existing != commit:
            raise GovernorError("branch_exists", f"branch {name} already points at {existing[:12]}; it is not moved")
        if existing is None:
            self._git("branch", "--", name, commit)
        baseline = self.baseline_root(session_id)
        if self.recognized_worktree(baseline):
            self.remove_worktree(baseline)
        return name

    # --------------------------------------------------------------- resume

    def resume_state(self, session_id: str, *, accepted_commit: str | None, unit_id: str | None) -> dict[str, Any]:
        """What the repository says about a session, as facts the workflow decides on.

        ``ref``: the accepted ref's commit or None; ``ref_missing``;
        ``ref_drift``: the ref exists and is not the commit the session
        recorded; ``unit_worktree_missing``: the open unit's worktree is not
        a recognised worktree.
        """

        current = self._rev(self.ref_name(session_id))
        state: dict[str, Any] = {
            "session_id": session_id,
            "accepted_commit": accepted_commit,
            "unit_id": unit_id,
            "ref": current,
            "ref_missing": current is None,
            "ref_drift": current is not None and accepted_commit is not None and current != accepted_commit,
            "unit_worktree_missing": False,
        }
        if unit_id is not None:
            state["unit_worktree_missing"] = not self.recognized_worktree(self.unit_root(session_id, unit_id))
        return state

    def resume_problems(self, state: Mapping[str, Any]) -> list[str]:
        """The facts of ``resume_state`` as the sentences a person reads."""

        session_id = str(state["session_id"])
        problems: list[str] = []
        if state["ref_missing"]:
            problems.append(f"accepted ref {self.ref_name(session_id)} is missing")
        elif state["ref_drift"]:
            problems.append(f"accepted ref drift: the session recorded {str(state['accepted_commit'])[:12]} but the ref is at {str(state['ref'])[:12]}")
        if state["unit_worktree_missing"]:
            unit_id = str(state["unit_id"])
            problems.append(f"unit worktree for {unit_id!r} is missing or unrecognised at {self.unit_root(session_id, unit_id)}")
        return problems

    def resume_checks(self, session_id: str, *, accepted_commit: str | None, unit_id: str | None) -> list[str]:
        return self.resume_problems(self.resume_state(session_id, accepted_commit=accepted_commit, unit_id=unit_id))

    def reviewed_by_of(self, commit: str) -> str | None:
        """The reviewer an acceptance commit names in its ``Reviewed-by`` trailer, if any."""

        message = self._git("log", "-1", "--format=%B", commit).stdout
        for line in message.splitlines():
            if line.startswith("Reviewed-by:"):
                name = line[len("Reviewed-by:"):].strip()
                return name or None
        return None

    def reconcile_acceptance(self, session_id: str, *, accepted_commit: str, verified_tree: str) -> str | None:
        """The commit an interrupted acceptance left behind, if it is exactly the verified tree on one parent."""

        current = self.accepted_commit(session_id)
        if current == accepted_commit:
            return None
        parent = self._rev(f"{current}^")
        second = self._rev(f"{current}^2")
        tree = self._rev(f"{current}^{{tree}}")
        if second is None and parent == accepted_commit and tree == verified_tree:
            return current
        return None
