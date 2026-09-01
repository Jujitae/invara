"""Running the checks. This is the half that touches the world.

Nothing here decides anything. Every judgement comes from a contract that was
sealed before the work was looked at, and every input is a file digest or an
exit code. There is no path by which a claim becomes a verdict.

**This module is deliberately impure and the verdict is deliberately not in
it.** ``subprocess`` and the filesystem live here; :mod:`invara.verdict` and
:mod:`invara.contract` are held to importing neither, and a test enforces
that. ``ADR-0016`` calls that line the seam the whole product rests on — the
core has to mean the same thing under a CLI, an MCP server, a plugin, CI or a
hosted API, and it can only do that if it never reaches for the world itself.
Which side a name is on is the question this file's boundary answers.

:class:`~invara.verdict.Observation` and :func:`~invara.verdict.judge` are
re-exported here, because that is where every caller has always found them
and moving a name is not the same as renaming it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Iterable, Sequence

from .contract import Predicate, VerificationContract
from .verdict import Observation, judge

__all__ = [
    "Observation",
    "digest_paths",
    "judge",
    "observe",
    "resolve_program",
    "run_predicate",
]

#: A check that has not finished in this long is treated as unrunnable rather
#: than left to hang a gate forever. Generous: the full suite here takes ~200s.
DEFAULT_TIMEOUT_S = 900


def digest_paths(paths: Iterable[str], root: Path) -> dict[str, str]:
    """sha256 per path, for the paths that exist.

    A path that is absent is simply missing from the result, and
    :func:`~invara.contract.seal` refuses a constraint whose paths have no
    baseline. Absence is never recorded as a digest of nothing.
    """

    out: dict[str, str] = {}
    for name in paths:
        target = root / name
        if target.is_file():
            out[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    return out


def resolve_program(command: Sequence[str], root: Path) -> list[str]:
    """Make a repo-relative executable actually runnable.

    ``subprocess`` does not search ``cwd`` for the program — the standard
    library says so plainly: *"this directory is not considered when searching
    the executable, so you cannot specify the program's path relative to
    cwd"*. A contract that says ``.venv/Scripts/python.exe`` means this
    repository's interpreter, and without this it means nothing at all.

    Found by running the verifier on the task that built it. All three checks
    came back ``command not found`` and the verdict was ``UNVERIFIABLE`` —
    correct, and useless. A bare name like ``git`` is left alone for PATH.
    """

    if not command:
        return []
    program, *rest = command
    if "/" in program or "\\" in program:
        candidate = Path(program)
        if not candidate.is_absolute():
            local = (root / candidate).resolve()
            if local.exists():
                return [str(local), *rest]
    return list(command)


#: What the Microsoft Store's app-execution alias for ``python`` actually does
#: when Python is not installed, measured on Windows (INV-011, 2026-08-29):
#: it exits 9009 (0x2331) with exactly seven bytes of stderr,
#: ``"Python "``, and nothing on stdout. Through a layer that truncates exit
#: codes to eight bits the same death reads as 49 (9009 % 256). Both are held
#: here because both have been observed for the one event.
_STORE_ALIAS_EXITS = frozenset({9009, 49})


def _store_alias_detail(
    command: Sequence[str],
    returncode: int,
    stdout: str,
    stderr: str,
    platform: str | None = None,
) -> str | None:
    """The readable account of a Store-alias interception, or ``None``.

    Windows without Python is not Windows without a ``python`` command: the
    Store puts an alias on PATH that exists, spawns, and dies with seven
    bytes of stderr. To everything upstream that is indistinguishable from a
    check that ran and failed -- which is exactly the wrong clothes for it
    (see :func:`run_predicate`). A product that forbids silence on the
    verdict path does not get to pass this particular silence through.

    The gate is deliberately narrow -- a python-named program, one of the two
    measured exit codes, and output that is empty or the measured seven-byte
    fragment -- so an honest check that legitimately exits 49 with real
    output keeps its own report. ``platform`` is an argument so a test can
    exercise both sides without pretending about the machine it runs on.
    """

    if (platform or os.name) != "nt":
        return None
    if returncode not in _STORE_ALIAS_EXITS:
        return None
    if not command:
        return None
    # A unit test on Unix may supply a measured Windows path.  ``Path`` on
    # Unix treats its backslashes as ordinary characters, so select Windows
    # path rules from the observed platform rather than the host running the
    # test.
    path_type = PureWindowsPath if (platform or os.name) == "nt" else Path
    program = path_type(command[0]).name.lower().removesuffix(".exe")
    if not program.startswith("python"):
        return None
    chatter = (stdout or "").strip() + (stderr or "").strip()
    if chatter not in ("", "Python"):
        return None
    # Held under Observation.as_dict's 500-character detail cap, so the
    # Korean half is not the half that gets truncated away in storage.
    return (
        f"python did not run: the Microsoft Store alias answered instead "
        f"(exit {returncode}, stderr 'Python '). Python is not actually "
        f"installed, or not on PATH. Fix: install Python 3.12+ "
        f"(https://www.python.org/downloads/ or winget install "
        f"Python.Python.3.12), or disable the alias in Settings > Apps > "
        f"App execution aliases. Not an INVARA defect. | "
        f"python 이 실행되지 않았습니다. Microsoft Store 별칭이 가로챘습니다 "
        f"(Python 미설치). Python 3.12+ 를 설치하거나(python.org / winget) "
        f"앱 실행 별칭에서 python 을 끄세요. INVARA 고장이 아닙니다."
    )


def run_predicate(
    predicate: Predicate, root: Path, timeout_s: int = DEFAULT_TIMEOUT_S
) -> Observation:
    """Run one check. A check that will not run is reported as not run.

    The distinction matters more than it looks. A command that is missing, or
    that times out, has told us nothing — and "nothing" must not arrive at the
    verdict wearing the same clothes as "failed" or "passed".
    """

    if predicate.human:
        return Observation(predicate.id, None, False, "defers to a person")
    try:
        completed = subprocess.run(  # noqa: S603 — operator-authored contract
            resolve_program(predicate.command, root),
            cwd=root,
            capture_output=True,
            text=True,
            # Decode as UTF-8 whatever the console codepage is. Without this
            # the reader thread dies on a Windows cp949 console the moment a
            # check prints anything non-ASCII — the first real PASS came with
            # a UnicodeDecodeError traceback attached and an empty detail
            # line. The verdict was still correct, because it rests on exit
            # codes, but a verifier that prints a stack trace while approving
            # is a verifier nobody believes.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as error:
        return Observation(predicate.id, None, False, f"command not found: {error}")
    except OSError as error:
        return Observation(predicate.id, None, False, f"could not run: {error}")
    except subprocess.TimeoutExpired:
        return Observation(
            predicate.id, None, False, f"timed out after {timeout_s}s"
        )
    intercepted = _store_alias_detail(
        predicate.command, completed.returncode, completed.stdout, completed.stderr
    )
    if intercepted is not None:
        # The Store alias ran; python did not. Reporting this as "ran and
        # failed" would let a missing interpreter arrive at the verdict
        # wearing a broken promise's clothes, so it goes down the same road
        # as FileNotFoundError above: not run, and saying why in words the
        # buyer can act on (INV-011).
        return Observation(predicate.id, None, False, intercepted)
    tail = (completed.stdout or "").strip().splitlines()
    stderr = (completed.stderr or "").strip().splitlines()
    detail = " | ".join((tail[-1:] or [""]) + (stderr[-1:] or []))
    return Observation(predicate.id, completed.returncode, True, detail.strip())


def observe(
    contract: VerificationContract,
    root: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[dict[str, dict[str, str]], list[Observation]]:
    """Everything the verdict is allowed to look at.

    Returns the current digests of every protected path, and one observation
    per predicate. Separated from :func:`judge` so a dry run and a recorded
    run see identical inputs, and so the judgement can be re-derived from
    stored observations without running anything again.
    """

    current: dict[str, dict[str, str]] = {}
    for index, constraint in enumerate(contract.constraints):
        current[str(index)] = digest_paths(constraint.paths, root)
    observations = [
        run_predicate(predicate, root, timeout_s) for predicate in contract.done_when
    ]
    return current, observations
