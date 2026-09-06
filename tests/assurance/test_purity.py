"""The pure half of the assurance core reaches for nothing.

Same gate as ``TheCoreIsPure`` in the historical suite, aimed at the
modules that hold the assurance semantics. The verdict of a comparison, the
claim ladder, the normalization of an observation and the state machine of
a session must be re-derivable from stored records years later; a module
that can look at a clock or a socket while doing so cannot promise that.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "invara" / "assurance"

#: The modules held pure. ``chain`` is allowed as a sibling because the two
#: names taken from it (``canonical_json``, ``chain_hash``) are pure and
#: duplicating the serialisation rule would be the worse defect;
#: ``contract`` is the kernel's own pure core.
PURE_MODULES = ("records", "paths", "manifest", "normalize", "compare", "claims", "redaction", "session", "report", "coverage", "sensitivity", "http_boundary")
PURE_KERNEL_MODULES = ("exact_json",)
ALLOWED_SIBLINGS = frozenset(PURE_MODULES + PURE_KERNEL_MODULES) | {"chain", "contract"}

IMPURE_ROOTS = frozenset(
    {
        "subprocess", "multiprocessing", "signal", "ctypes", "runpy", "importlib",
        "io", "os", "sys", "pathlib", "shutil", "tempfile", "glob", "fileinput",
        "sqlite3", "dbm", "shelve", "pickle",
        "time", "datetime", "calendar", "zoneinfo",
        "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "imaplib", "poplib",
        "xmlrpc", "asyncio", "selectors", "select", "webbrowser",
        "random", "secrets", "uuid", "threading",
    }
)


def imports_of(source: str) -> tuple[set[str], set[str]]:
    absolute: set[str] = set()
    siblings: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            absolute |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    siblings.add(node.module.split(".")[0])
                else:
                    siblings |= {alias.name for alias in node.names}
            elif node.module:
                # These two parsing/escaping functions perform no I/O. Keep
                # every other urllib import forbidden, including request.
                if node.module == "urllib.parse" and {alias.name for alias in node.names} <= {"quote", "urlsplit"}:
                    continue
                absolute.add(node.module.split(".")[0])
    return absolute, siblings


class TheAssuranceCoreIsPure(unittest.TestCase):
    def test_every_pure_module_exists(self) -> None:
        for name in PURE_MODULES:
            self.assertTrue((PACKAGE / f"{name}.py").is_file(), name)
        for name in PURE_KERNEL_MODULES:
            self.assertTrue((PACKAGE.parent / f"{name}.py").is_file(), name)

    def test_the_pure_modules_reach_for_nothing(self) -> None:
        problems = []
        for name in PURE_MODULES + PURE_KERNEL_MODULES:
            base = PACKAGE.parent if name in PURE_KERNEL_MODULES else PACKAGE
            absolute, siblings = imports_of((base / f"{name}.py").read_text(encoding="utf-8"))
            for root in sorted(absolute & IMPURE_ROOTS):
                problems.append(f"{name} imports {root}")
            for sibling in sorted(siblings):
                if sibling not in ALLOWED_SIBLINGS:
                    problems.append(f"{name} imports {sibling}, which is not pure")
        self.assertEqual(problems, [])

    def test_the_gate_reports_a_planted_import(self) -> None:
        absolute, siblings = imports_of("import time\nfrom .engine import Engine\nfrom urllib.request import urlopen\n")
        self.assertEqual(absolute & IMPURE_ROOTS, {"time", "urllib"})
        self.assertEqual(siblings - ALLOWED_SIBLINGS, {"engine"})


if __name__ == "__main__":
    unittest.main()
