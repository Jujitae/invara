"""One run of one input against one system, observed by every declared probe.

This is the impure heart of the observation engine: it makes a fresh
workspace, seeds the declared initial state, builds a controlled
environment, delivers the input, runs the system as a process or a local
service, and asks each adapter for its record. What comes back is one raw
observation record — versioned, JSON-shaped, redacted, and deterministic
in everything INVARA controls.

What it refuses to do, and says so in the record instead:

* run through a shell (``command`` is an argv list, always),
* pass the parent's environment through (an allowlist plus fixed values),
* read outside the workspace or the system root (a probe root that escapes
  is ``malformed``),
* follow symlinks or directory junctions (recorded as links, never opened,
  never deleted through),
* talk to anything but the service it started (a request to another host,
  or another port, is ``unverifiable``: outside the verification boundary),
* keep unbounded output (capture is bounded; the full digest is kept),
* wait forever (``timeout`` is a status, not a hang — on stdin, on
  readiness and on the run alike).

A missing executable — including the Microsoft Store's ``python`` alias
that spawns and dies — is ``unrunnable``; an adapter that fails internally
is ``malformed``. Neither is an observation, and the comparator treats
both as unverifiable.

**Not a sandbox.** INVARA's own process makes no network connection but
the loopback one it declared; the child runs with the user's permissions
and can reach whatever the user can. INVARA controls what it observes and
what it sends, not what the program does.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import json

from .. import exact_json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..runner import _store_alias_detail
from .manifest import CorpusItem, Manifest, Probe, System
from .records import OBSERVATION_VERSION
from .redaction import redact_value
from .http_boundary import validate_requests

__all__ = [
    "CONTROLLED_ENV",
    "ExecutionError",
    "RunSpec",
    "UNCONTROLLABLE",
    "controlled_environment",
    "matches_include",
    "resolve",
    "run",
]

#: Values imposed on every child. The record lists them as *controlled*,
#: except those the platform ignores, which it lists as *inert*.
CONTROLLED_ENV: dict[str, str] = {
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
#: Controls the platform's C runtime does not honour: set for consistency, reported honestly.
INERT_ON_WINDOWS: tuple[str, ...] = ("TZ", "LC_ALL", "LANG")

#: Dimensions no process adapter can pin. Reported, never silently assumed.
UNCONTROLLABLE: tuple[str, ...] = (
    "wall_clock",
    "thread_scheduling",
    "process_scheduling",
    "external_services",
    "hardware_randomness",
    "child_network_and_filesystem_access",
)

_PLACEHOLDER = re.compile(r"\$(SOURCE_ROOT|TARGET_ROOT|WORKSPACE|PORT)\b")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _loopback_literal(host: str) -> str:
    """The literal loopback address for a loopback name; never the resolver's answer."""

    if host == "localhost":
        return "127.0.0.1"
    if host in _LOOPBACK:
        return host
    raise ValueError(f"{host!r} is not a loopback name")
_TEXT_LIMIT = 65_536
_DEFAULT_MAX_ROWS = 10_000
_DEFAULT_MAX_ENTRIES = 5_000
#: bytes of captured content one table, or one file tree's texts, may hold (rows and entries are also capped by count)
_DEFAULT_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_ALLOWED_SEED_SQL = ("create", "insert", "update", "delete", "begin", "commit", "with")


class ExecutionError(RuntimeError):
    """INVARA itself could not observe. The record becomes ``malformed``."""


@dataclass(frozen=True)
class RunSpec:
    system: System
    item: CorpusItem
    manifest: Manifest
    roots: dict[str, str]


def resolve(text: str, roots: dict[str, str], *, workspace: str, port: int | None) -> str:
    """Substitute the four placeholders; refuse to leave one behind."""

    values = {"WORKSPACE": workspace}
    values.update({key: str(value) for key, value in roots.items()})
    if port is not None:
        values["PORT"] = str(port)

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ExecutionError(f"unresolved placeholder ${name} in {text!r}")
        return values[name]

    return _PLACEHOLDER.sub(substitute, text)


def controlled_environment(system: System, *, workspace: str, port: int | None, seed: int) -> tuple[dict[str, str], dict[str, Any]]:
    inherited = sorted(name for name in system.env_allow if name in os.environ)
    env = {name: os.environ[name] for name in inherited}
    controlled = dict(CONTROLLED_ENV)
    controlled.update(system.env_set)
    controlled["INVARA_WORKSPACE"] = workspace
    controlled["INVARA_SEED"] = str(seed)
    if port is not None and system.service is not None:
        controlled[system.service.get("port_env", "PORT")] = str(port)
    env.update(controlled)
    record = {
        "controlled": controlled,
        "inert": [name for name in INERT_ON_WINDOWS if name in controlled] if os.name == "nt" else [],
        "inherited": inherited,
        "allowlist": list(system.env_allow),
        "uncontrollable": list(UNCONTROLLABLE),
        "platform": platform.platform(),
        "invara_python": sys.version.split()[0],
    }
    return env, record


def matches_include(relative: str, patterns: list[str]) -> bool:
    """Glob matching where ``*`` stays within one path segment and ``**`` crosses them."""

    for pattern in patterns:
        if pattern == "**" or _glob_match(pattern.split("/"), relative.split("/")):
            return True
    return False


def _glob_match(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_glob_match(rest, parts[index:]) for index in range(len(parts) + 1))
    if not parts:
        return False
    return _segment_match(head, parts[0]) and _glob_match(rest, parts[1:])


def _segment_match(pattern: str, segment: str) -> bool:
    regex = "^" + "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern) + "$"
    return re.match(regex, segment) is not None


# --------------------------------------------------------------------------
# process plumbing


class _Capture:
    """Drain one pipe on a thread; keep at most ``limit`` bytes, digest everything."""

    def __init__(self, stream: Any, limit: int) -> None:
        self.limit = limit
        self.kept = bytearray()
        self.total = 0
        self.hasher = hashlib.sha256()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self.thread.start()

    def _drain(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    break
                self.total += len(chunk)
                self.hasher.update(chunk)
                room = self.limit - len(self.kept)
                if room > 0:
                    self.kept.extend(chunk[:room])
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            self.done.set()

    def finish(self) -> dict[str, Any]:
        complete = self.done.wait(timeout=5)
        text = bytes(self.kept).decode("utf-8", errors="replace")
        out: dict[str, Any] = {"text": text, "truncated": self.total > self.limit, "incomplete": not complete}
        if out["truncated"]:
            out["digest"] = self.hasher.hexdigest()
            out["bytes"] = self.total
        return out


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _popen_options() -> dict[str, Any]:
    if os.name == "nt":
        # CREATE_SUSPENDED keeps the child from racing a descendant outside the
        # job before INVARA has established its process-tree boundary.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000004}
    return {"start_new_session": True}


def _start_windows_job(process: subprocess.Popen[bytes]) -> None:
    """Put a suspended child in a kill-on-close job, then resume it."""

    from ctypes import wintypes

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        process.kill()
        process.wait(timeout=5)
        raise ExecutionError(f"could not create a Windows process job: {ctypes.WinError(ctypes.get_last_error())}")
    assigned = False
    try:
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):  # type: ignore[attr-defined]
            raise ctypes.WinError(ctypes.get_last_error())
        assigned = True

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if ctypes.cast(snapshot, ctypes.c_void_p).value == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        resumed = 0
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
            while present:
                if int(entry.th32OwnerProcessID) == process.pid:
                    thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        resumed += 1
                    finally:
                        kernel32.CloseHandle(thread)
                present = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        if resumed < 1:
            raise OSError("the suspended process had no resumable thread")
        setattr(process, "_invara_job_handle", int(job))
    except Exception as error:
        if assigned:
            kernel32.TerminateJobObject(job, 1)
        else:
            try:
                process.kill()
            except OSError:
                pass
        kernel32.CloseHandle(job)
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise ExecutionError(f"could not establish the Windows process-tree boundary: {error}") from error


def _end_windows_job(process: subprocess.Popen[bytes]) -> None:
    """Terminate one contained job and verify that no member remains active."""

    from ctypes import wintypes

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    raw_job = getattr(process, "_invara_job_handle", None)
    if raw_job is None:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise ExecutionError("the uncontained Windows child could not be stopped") from error
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = wintypes.HANDLE(raw_job)
    problem: str | None = None
    try:
        if not kernel32.TerminateJobObject(job, 1):
            problem = f"TerminateJobObject failed: {ctypes.WinError(ctypes.get_last_error())}"
        deadline = time.monotonic() + 5
        while True:
            accounting = _BasicAccounting()
            if not kernel32.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                problem = f"process-tree verification failed: {ctypes.WinError(ctypes.get_last_error())}"
                break
            if accounting.ActiveProcesses == 0:
                problem = None
                break
            if time.monotonic() >= deadline:
                problem = f"{accounting.ActiveProcesses} process(es) remained active after termination"
                break
            time.sleep(0.01)
    finally:
        kernel32.CloseHandle(job)
        setattr(process, "_invara_job_handle", None)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        problem = problem or "the root process remained active after job termination"
    if problem is not None:
        raise ExecutionError(f"Windows process-tree death could not be established: {problem}")


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Stop the child tree and return only after its death is established."""

    if os.name == "nt":
        _end_windows_job(process)
        return
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, 15)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def _attach_process_probes(
    record: dict[str, Any], probes: tuple[Probe, ...], captured: dict[str, dict[str, Any]], exit_code: int | None
) -> None:
    for probe in probes:
        if probe.adapter != "process":
            continue
        entry: dict[str, Any] = {"exit_code": exit_code, "truncated": {}}
        for stream in probe.params.get("capture", ["stdout", "stderr"]):
            entry[stream] = captured[stream]["text"]
            entry["truncated"][stream] = captured[stream]["truncated"]
            if captured[stream]["truncated"]:
                entry[f"{stream}_digest"] = captured[stream]["digest"]
                entry[f"{stream}_bytes"] = captured[stream]["bytes"]
            if captured[stream]["incomplete"]:
                entry[f"{stream}_incomplete"] = True
        record["probes"][probe.id] = entry


def _wait_ready(port: int, ready: dict[str, Any], deadline_s: float, process: subprocess.Popen[bytes]) -> bool:
    try:
        endpoint = validate_requests([{"path": ready["http"]}], port)[0] if "http" in ready else None
    except ValueError:
        return False
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        remaining = max(0.05, min(1.0, deadline - time.monotonic()))
        try:
            if "http" in ready:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=remaining)
                try:
                    connection.request("GET", endpoint["target"])
                    connection.getresponse().read(65_536)
                finally:
                    connection.close()
            else:
                with socket.create_connection(("127.0.0.1", port), timeout=remaining):
                    pass
            return True
        except (OSError, http.client.HTTPException):
            time.sleep(0.05)
    return False


def _feed_stdin(process: subprocess.Popen[bytes], data: bytes, deadline_s: float) -> bool:
    """Write stdin on a thread so a child that never reads cannot hang the run."""

    def write() -> None:
        try:
            process.stdin.write(data)  # type: ignore[union-attr]
            process.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    thread.join(timeout=deadline_s)
    return not thread.is_alive()


# --------------------------------------------------------------------------
# workspace


def _is_link(path: str | Path) -> bool:
    text = str(path)
    return os.path.islink(text) or (hasattr(os.path, "isjunction") and os.path.isjunction(text))


def _seed_state(workspace: Path, state: dict[str, Any]) -> None:
    for relative, content in state.get("files", {}).items():
        target = _inside(workspace, relative, what="initial file")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            target.write_bytes(base64.b64decode(content["base64"]))
        else:
            target.write_bytes(content.encode("utf-8"))
    sqlite_state = state.get("sqlite")
    if sqlite_state:
        target = _inside(workspace, sqlite_state["path"], what="initial database")
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target)
        try:
            connection.set_authorizer(_seed_authorizer)
            for statement in sqlite_state["sql"]:
                first = statement.strip().split(None, 1)[0].lower() if statement.strip() else ""
                if first not in _ALLOWED_SEED_SQL:
                    raise ExecutionError(f"initial database statements may only create and fill tables; refused: {statement[:60]!r}")
                try:
                    connection.execute(statement)
                except sqlite3.DatabaseError as error:
                    raise ExecutionError(f"initial database statement refused: {error}") from None
            connection.commit()
        finally:
            connection.close()


def _seed_authorizer(action: int, arg1: Any, arg2: Any, database: Any, trigger: Any) -> int:
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _inside(base: Path, relative: str, *, what: str) -> Path:
    candidate = (base / relative).resolve()
    if candidate != base.resolve() and base.resolve() not in candidate.parents:
        raise ExecutionError(f"{what} {relative!r} escapes the workspace")
    return candidate


def _contained(path: Path, allowed: list[Path]) -> bool:
    resolved = path.resolve()
    for base in allowed:
        base = base.resolve()
        if resolved == base or base in resolved.parents:
            return True
    return False


def _digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _file_names(root: Path, include: list[str], allowed: list[Path] | None = None) -> list[str]:
    """Files under ``root`` in sorted order; links are listed, never entered."""

    names: list[str] = []
    if not root.is_dir() or _is_link(root):
        return names
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        if allowed is not None and not _contained(Path(directory), allowed):
            raise ExecutionError(f"filesystem walk left the allowed roots at {directory}")
        dirnames.sort()
        linked = [name for name in dirnames if _is_link(Path(directory) / name)]
        for name in linked:
            dirnames.remove(name)
            names.append((Path(directory) / name).relative_to(root).as_posix())
        for name in sorted(filenames):
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if matches_include(relative, include):
                names.append(relative)
    return sorted(names)


# --------------------------------------------------------------------------
# adapters


def _probe_filesystem(probe: Probe, root: Path, allowed: list[Path], initial: list[str]) -> dict[str, Any]:
    if not _contained(root, allowed):
        raise ExecutionError(f"filesystem probe {probe.id!r} root {root} escapes the workspace and the system root")
    if _is_link(root):
        # a link is never entered; a probe rooted at one observed nothing
        return {"root_link": True, "entries": {}, "created": [], "removed": list(initial)}
    include = list(probe.params.get("include", ["**"]))
    content = probe.params.get("content", "digest")
    max_text = int(probe.params.get("max_text_bytes", _TEXT_LIMIT))
    max_entries = int(probe.params.get("max_entries", _DEFAULT_MAX_ENTRIES))
    max_bytes = int(probe.params.get("max_bytes", _DEFAULT_MAX_CAPTURE_BYTES))
    text_bytes = 0
    text_limited = False
    entries: dict[str, Any] = {}
    if not root.exists():
        return {"root_missing": True, "entries": entries, "created": [], "removed": list(initial)}
    names = _file_names(root, include, allowed)
    omitted = 0
    for relative in names:
        if len(entries) >= max_entries:
            omitted += 1
            continue
        full = root / relative
        if _is_link(full):
            entries[relative] = {"symlink": True}
            continue
        if not full.is_file():
            continue
        size = full.stat().st_size
        entry: dict[str, Any] = {"size": size, "digest": _digest_file(full)}
        if content == "text":
            if size > max_text:
                entry["text_omitted"] = size
            elif text_bytes + size > max_bytes:
                # the tree's texts exceed the byte budget: the digest stays, the text does not
                entry["text_omitted"] = size
                text_limited = True
            else:
                entry["text"] = full.read_bytes().decode("utf-8", errors="replace")
                text_bytes += size
        entries[relative] = entry
    present = set(names)
    out: dict[str, Any] = {
        "entries": entries,
        "created": sorted(present - set(initial)),
        "removed": sorted(set(initial) - present),
    }
    if omitted:
        out["entries_omitted"] = omitted
    if text_limited:
        out["text_limited"] = True
    return out


def _json_value(cell: Any) -> Any:
    if isinstance(cell, bytes):
        return {"blob_digest": hashlib.sha256(cell).hexdigest(), "blob_bytes": len(cell)}
    return cell


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _probe_sqlite(probe: Probe, path: Path, allowed: list[Path], scratch: Path) -> dict[str, Any]:
    if not _contained(path, allowed):
        raise ExecutionError(f"sqlite probe {probe.id!r} path {path} escapes the workspace and the system root")
    if not path.is_file() or _is_link(path):
        return {"missing": True, "tables": {}}
    # Snapshot the database file with its journal so a database the child
    # left in WAL mode is still readable; the copy is opened read-write only
    # to let SQLite recover its own journal, never the original.
    scratch.mkdir(parents=True, exist_ok=True)
    copy = scratch / f"{probe.id}.db"
    shutil.copyfile(path, copy)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_file() and not _is_link(sidecar):
            shutil.copyfile(sidecar, Path(str(copy) + suffix))
    connection = sqlite3.connect(copy)
    connection.row_factory = sqlite3.Row
    tables: dict[str, Any] = {}
    try:
        connection.execute("BEGIN")
        for table in probe.params["tables"]:
            name = table["name"]
            try:
                tables[name] = _read_table(connection, table, name)
            except sqlite3.DatabaseError as error:
                tables[name] = {"unreadable": str(error)[:200]}
        connection.rollback()
    finally:
        connection.close()
    return {"tables": tables}


def _read_table(connection: sqlite3.Connection, table: dict[str, Any], name: str) -> dict[str, Any]:
    found = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    if found is None:
        return {"missing": True}
    quoted = _quote_identifier(name)
    columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({quoted})")]
    key = list(table.get("key", []))
    max_rows = int(table.get("max_rows", _DEFAULT_MAX_ROWS))
    max_bytes = int(table.get("max_bytes", _DEFAULT_MAX_CAPTURE_BYTES))
    if table.get("order", "ordered") == "unordered":
        order_columns = key or columns
        order_by = ", ".join(_quote_identifier(column) for column in order_columns)
        row_order = "key:" + ",".join(key) if key else "columns"
    else:
        order_by = "rowid"
        row_order = "rowid"
    try:
        rows = connection.execute(f"SELECT * FROM {quoted} ORDER BY {order_by} LIMIT ?", (max_rows + 1,)).fetchall()  # noqa: S608
    except sqlite3.OperationalError:
        rows = connection.execute(f"SELECT * FROM {quoted} LIMIT ?", (max_rows + 1,)).fetchall()  # noqa: S608
        row_order = "unordered"
    kept: list[dict[str, Any]] = []
    size = 0
    bytes_limited = False
    for row in rows[:max_rows]:
        record = {column: _json_value(row[column]) for column in columns}
        size += len(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
        if kept and size > max_bytes:
            # the table exceeds the byte budget: what was kept is on record, the rest is counted
            bytes_limited = True
            break
        kept.append(record)
    out: dict[str, Any] = {
        "schema": found["sql"],
        "columns": columns,
        "row_order": row_order,
        "rows": kept,
    }
    if len(rows) > max_rows or bytes_limited:
        total = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]  # noqa: S608
        out["rows_omitted"] = int(total) - len(kept)
    if bytes_limited:
        out["bytes_limited"] = True
    return out


def _probe_http(
    probe: Probe, requests: Any, port: int, timeout_s: float, capture_limit: int, *, deadline: float | None = None
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(requests, list):
        raise ExecutionError(f"http probe {probe.id!r}: requests must be a list")
    wanted = [name.lower() for name in probe.params.get("headers", ["content-type"])]
    responses: list[dict[str, Any]] = []
    try:
        identities = validate_requests(requests, port)
    except ValueError as error:
        return {"responses": responses, "request_identities": []}, str(error)
    attempted_identities: list[dict[str, Any]] = []
    result = {"responses": responses, "request_identities": attempted_identities}
    for request, identity in zip(requests, identities):
        if not isinstance(request, dict):
            raise ExecutionError(f"http probe {probe.id!r}: each request must be an object")
        # one deadline for the whole run: every request gets what is left of it
        request_timeout = timeout_s
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return result, f"timed out after {timeout_s:g}s before request {len(responses) + 1} of {len(requests)}"
            request_timeout = min(timeout_s, remaining)
        method, host, target_port, path = identity["method"], identity["host"], identity["port"], identity["target"]
        headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        body = request.get("body")
        payload: bytes | None = None
        if body is not None:
            if isinstance(body, str):
                payload = body.encode("utf-8")
            else:
                payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
        echo = {"method": method, "path": path, "body": body}
        connection = http.client.HTTPConnection(_loopback_literal(host), target_port, timeout=request_timeout)
        try:
            attempted_identities.append(identity)
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(capture_limit + 1)
            entry: dict[str, Any] = {
                "request": echo,
                "status": response.status,
                "headers": {name: value for name, value in ((k.lower(), v) for k, v in response.getheaders()) if name in wanted},
                "body": raw[:capture_limit].decode("utf-8", errors="replace"),
                "truncated": len(raw) > capture_limit,
            }
            try:
                entry["json"] = json.loads(entry["body"])
            except (ValueError, RecursionError):
                pass
            responses.append(entry)
        except (OSError, http.client.HTTPException) as error:
            responses.append({"request": echo, "error": f"{type(error).__name__}: {error}"})
        finally:
            connection.close()
    return result, None


# --------------------------------------------------------------------------
# the run


def _deliver(delivery: str, item: CorpusItem, argv: list[str], workspace: Path, env: dict[str, str]) -> tuple[bytes, list[str], Any]:
    if delivery == "stdin_json":
        return json.dumps(item.input, sort_keys=True, ensure_ascii=False).encode("utf-8"), argv, None
    if delivery == "argv_json":
        return b"", [*argv, json.dumps(item.input, sort_keys=True, ensure_ascii=False)], None
    if delivery == "file_json":
        target = workspace / "input.json"
        target.write_text(json.dumps(item.input, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        env["INVARA_INPUT"] = str(target)
        return b"", argv, None
    if delivery == "http":
        if not isinstance(item.input, dict) or not isinstance(item.input.get("requests"), list):
            raise ExecutionError("http delivery needs input.requests as a list")
        return b"", argv, item.input["requests"]
    raise ExecutionError(f"unknown delivery {delivery!r}")


def _resolve_program(argv: list[str], root: Path) -> list[str]:
    """A relative program with a path separator is the root's, on every platform.

    POSIX resolves such a name against the child's working directory;
    Windows resolves it against INVARA's own. Making it absolute against the
    system root gives both the same program.
    """

    if not argv:
        return argv
    first = argv[0]
    if ("/" in first or "\\" in first) and not Path(first).is_absolute():
        candidate = (root / first).resolve()
        if candidate.is_file():
            return [str(candidate), *argv[1:]]
    return argv


def _program_identity(argv: list[str], root: Path, env: dict[str, str]) -> dict[str, Any]:
    """Which binary actually ran: the resolved program and its digest."""

    program = argv[0] if argv else ""
    candidate: str | None
    if os.path.isabs(program) or ("/" in program or "\\" in program):
        local = Path(program) if os.path.isabs(program) else root / program
        candidate = str(local) if local.exists() else None
    else:
        candidate = shutil.which(program, path=env.get("PATH"))
    if not candidate:
        return {"path": program, "digest": None, "resolved": False}
    path = Path(candidate)
    try:
        digest = _digest_file(path) if path.is_file() else None
    except OSError:
        digest = None
    return {"path": str(path), "digest": digest, "resolved": True}


def run(
    spec: RunSpec,
    *,
    workspace_parent: str | Path | None = None,
    keep_workspace: bool = False,
    seed: int = 0,
    before_run: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one input against one system. Never raises for the system's sake.

    Whatever the run's outcome, the record is redacted before it leaves:
    a timeout, an unrunnable command or an adapter failure carries whatever
    was captured before it, and that is evidence like any other.
    """

    record = _observe(spec, workspace_parent=workspace_parent, keep_workspace=keep_workspace, seed=seed, before_run=before_run)
    redacted, count = redact_value(record)
    redacted["redactions"] = count
    return redacted


def _observe(
    spec: RunSpec,
    *,
    workspace_parent: str | Path | None,
    keep_workspace: bool,
    seed: int,
    before_run: Callable[[str], None] | None,
) -> dict[str, Any]:
    manifest, system, item = spec.manifest, spec.system, spec.item
    if workspace_parent is not None:
        Path(workspace_parent).mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="invara-run-", dir=str(workspace_parent) if workspace_parent else None)).resolve()
    scratch = Path(tempfile.mkdtemp(prefix="invara-scratch-", dir=str(workspace_parent) if workspace_parent else None)).resolve()
    record: dict[str, Any] = {
        "record_version": OBSERVATION_VERSION,
        "system_id": system.id,
        "input_id": item.id,
        # the digest of the exact input and initial state this run is an execution of: evidence is bound by it
        "input_digest": item.identity(),
        "status": "observed",
        "problems": [],
        "workspace": str(workspace),
        "root": "",
        "command": [],
        "program": {},
        "delivery": manifest.input_domain.delivery,
        "environment": {},
        "timing": {"wall_s": 0.0},
        "probes": {},
        "redactions": 0,
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        _seed_state(workspace, item.initial_state)
        port = _free_port() if system.kind == "service" else None
        if port is not None:
            record["http_service_port"] = port
            record["http_request_identities"] = {}
            record["http_request_declarations"] = {}
        root = Path(resolve(system.root, spec.roots, workspace=str(workspace), port=port)).resolve()
        record["root"] = str(root)
        argv = _resolve_program([resolve(part, spec.roots, workspace=str(workspace), port=port) for part in system.command], root)
        env, environment = controlled_environment(system, workspace=str(workspace), port=port, seed=seed)
        # what would run is on record before delivery can fail
        record["environment"] = environment
        record["command"] = list(argv)
        stdin, argv, requests = _deliver(manifest.input_domain.delivery, item, argv, workspace, env)
        # Validate every declaration before starting the service or its readiness I/O.
        try:
            for probe in manifest.probes:
                if probe.adapter == "http":
                    validate_requests(requests if probe.params.get("requests") == "$INPUT" else probe.params.get("requests"), port)
            if system.service and "http" in system.service["ready"]:
                validate_requests([{"path": system.service["ready"]["http"]}], port)
        except ValueError as error:
            record["status"] = "unverifiable"
            record["problems"].append(str(error))
            return record
        if "INVARA_INPUT" in env:
            environment["controlled"]["INVARA_INPUT"] = env["INVARA_INPUT"]
        record["command"] = list(argv)
        allowed = [workspace, root]
        timeout_s = float(system.timeout_s or manifest.timeouts.run_seconds)
        limit = int(system.capture_limit_bytes)

        probe_roots: dict[str, Path] = {}
        initial_files: dict[str, list[str]] = {}
        for probe in manifest.probes:
            if probe.adapter == "filesystem":
                probe_roots[probe.id] = Path(resolve(probe.params["root"], spec.roots, workspace=str(workspace), port=port))
                if not _contained(probe_roots[probe.id], allowed):
                    raise ExecutionError(f"filesystem probe {probe.id!r} root {probe_roots[probe.id]} escapes the workspace and the system root")
                initial_files[probe.id] = _file_names(probe_roots[probe.id], list(probe.params.get("include", ["**"])), allowed)
        if before_run is not None:
            before_run(str(workspace))
        if not root.is_dir():
            record["status"] = "unrunnable"
            record["problems"].append(f"system root does not exist: {root}")
            return record
        record["program"] = _program_identity(argv, root, env)

        captured: dict[str, dict[str, Any]] = {}
        exit_code: int | None = None
        started = time.perf_counter()
        deadline = started + timeout_s
        try:
            process = subprocess.Popen(  # noqa: S603 - manifest-authored argv, no shell
                argv, cwd=str(root), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_options()
            )
            if os.name == "nt":
                _start_windows_job(process)
        except FileNotFoundError:
            record["status"] = "unrunnable"
            record["problems"].append(f"command not found: {argv[0]}")
            return record
        except OSError as error:
            record["status"] = "unrunnable"
            record["problems"].append(f"could not start {argv[0]}: {error}")
            return record
        out_capture = _Capture(process.stdout, limit)
        err_capture = _Capture(process.stderr, limit)
        service_problem: str | None = None
        if system.kind == "process":
            fed = _feed_stdin(process, stdin, timeout_s)
            remaining = max(0.0, deadline - time.perf_counter())
            timed_out = not fed
            if not timed_out:
                try:
                    exit_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
            if timed_out:
                record["problems"].append(f"timed out after {timeout_s:g}s" + ("" if fed else " (the child never read its input)"))
                try:
                    _terminate(process)
                    record["status"] = "timeout"
                except ExecutionError as error:
                    record["status"] = "unverifiable"
                    record["problems"].append(str(error))
                stdout = out_capture.finish()
                stderr = err_capture.finish()
                captured["stdout"], captured["stderr"] = stdout, stderr
                _attach_process_probes(record, manifest.probes, captured, None)
                record["timing"]["wall_s"] = time.perf_counter() - started
                return record
            _terminate(process)
        else:
            try:
                process.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass
            ready = _wait_ready(port or 0, system.service["ready"], manifest.timeouts.service_ready_seconds, process)  # type: ignore[index]
            if not ready:
                _terminate(process)
                out_capture.finish()
                err_capture.finish()
                record["status"] = "unrunnable"
                record["problems"].append(f"service did not become ready within {manifest.timeouts.service_ready_seconds:g}s")
                record["timing"]["wall_s"] = time.perf_counter() - started
                return record
            for probe in manifest.probes:
                if probe.adapter == "http":
                    wanted = requests if probe.params.get("requests") == "$INPUT" else probe.params.get("requests")
                    result, service_problem = _probe_http(probe, wanted, port or 0, timeout_s, limit, deadline=deadline)
                    record["http_request_identities"][probe.id] = result.pop("request_identities", [])
                    record["http_request_declarations"][probe.id] = wanted
                    record["probes"][probe.id] = result
                    if service_problem:
                        break
            _terminate(process)
            exit_code = process.returncode
        record["timing"]["wall_s"] = time.perf_counter() - started
        stdout = out_capture.finish()
        stderr = err_capture.finish()
        captured["stdout"], captured["stderr"] = stdout, stderr
        if system.kind == "service":
            record["service"] = {"exit_code": exit_code, "stdout": stdout["text"][-2000:], "stderr": stderr["text"][-2000:]}
        if service_problem:
            record["status"] = "timeout" if service_problem.startswith("timed out") else "unverifiable"
            record["problems"].append(service_problem)
            return record
        intercepted = _store_alias_detail(argv, int(exit_code or 0), stdout["text"], stderr["text"]) if exit_code is not None else None
        if intercepted is not None:
            record["status"] = "unrunnable"
            record["problems"].append(intercepted)
            return record

        _attach_process_probes(record, manifest.probes, captured, exit_code)
        for probe in manifest.probes:
            if probe.adapter == "json":
                source = probe.params["source"]
                if source == "file":
                    path = Path(resolve(probe.params["path"], spec.roots, workspace=str(workspace), port=port))
                    if not _contained(path, allowed):
                        raise ExecutionError(f"json probe {probe.id!r} path {path} escapes the workspace and the system root")
                    if not path.is_file() or _is_link(path):
                        record["probes"][probe.id] = {"missing": True}
                        continue
                    text = path.read_bytes()[: limit + 1].decode("utf-8", errors="replace")
                else:
                    text = captured[source]["text"]
                try:
                    record["probes"][probe.id] = {
                        "value": exact_json.loads(text, strict_numbers=True, reject_duplicates=True)
                    }
                except (ValueError, RecursionError) as error:
                    record["probes"][probe.id] = {
                        "parse_error": str(error)[:200],
                        "text_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "text_head": text[:200],
                    }
            elif probe.adapter == "filesystem":
                record["probes"][probe.id] = _probe_filesystem(probe, probe_roots[probe.id], allowed, initial_files[probe.id])
            elif probe.adapter == "sqlite":
                path = Path(resolve(probe.params["path"], spec.roots, workspace=str(workspace), port=port))
                record["probes"][probe.id] = _probe_sqlite(probe, path, allowed, scratch)
        return record
    except ExecutionError as error:
        record["status"] = "malformed"
        record["problems"].append(str(error))
        return record
    except Exception as error:  # noqa: BLE001 - an adapter bug must fail closed, not judge
        record["status"] = "malformed"
        record["problems"].append(f"adapter failure: {type(error).__name__}: {error}")
        return record
    finally:
        try:
            if process is not None:
                _terminate(process)
        except Exception as error:  # noqa: BLE001 - cleanup must not replace the record
            record["problems"].append(f"could not stop the child cleanly: {error}")
        _remove_tree(scratch)
        if not keep_workspace:
            _remove_tree(workspace)


def _unlink_link(path: str) -> None:
    """Remove a symlink or junction itself; what it points at is never touched."""

    for remover in (os.rmdir, os.unlink):
        try:
            remover(path)
            return
        except OSError:
            continue


def _remove_tree(path: Path) -> None:
    """Remove a tree INVARA created; links and junctions are unlinked, never entered.

    ``os.walk`` descends into directory junctions on Windows even with
    ``followlinks=False``, so the walk is done by hand and every entry is
    asked whether it is a link before it is entered.
    """

    if _is_link(path):
        _unlink_link(str(path))
        return
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    for entry in entries:
        if _is_link(entry.path):
            _unlink_link(entry.path)
        elif entry.is_dir(follow_symlinks=False):
            _remove_tree(Path(entry.path))
        else:
            try:
                os.unlink(entry.path)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
