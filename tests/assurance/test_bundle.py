"""The plugin bundle carries the whole package tree, byte for byte.

The historical drift test compares the top-level modules. A subpackage
added to ``src/invara/`` and forgotten in ``plugin/src/invara/`` would pass
it and ship a plugin whose ``invara.assurance`` does not exist, so this
one walks the tree.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "invara"
BUNDLED = ROOT / "plugin" / "src" / "invara"


def tree(base: Path) -> dict[str, Path]:
    return {
        path.relative_to(base).as_posix(): path
        for path in base.rglob("*.py")
        if "__pycache__" not in path.parts
    }


class TheBundleIsTheSource(unittest.TestCase):
    def test_every_source_module_is_bundled_byte_identical(self) -> None:
        source, bundled = tree(SOURCE), tree(BUNDLED)
        self.assertIn("assurance/__init__.py", source, "the assurance subpackage is expected under src")
        missing = sorted(set(source) - set(bundled))
        self.assertEqual(missing, [], f"missing from plugin bundle: {missing}")
        for name, path in sorted(source.items()):
            self.assertEqual(path.read_bytes(), bundled[name].read_bytes(), f"plugin/src/invara/{name} differs from src/invara/{name}")

    def test_the_bundle_carries_nothing_the_source_does_not(self) -> None:
        extra = sorted(set(tree(BUNDLED)) - set(tree(SOURCE)))
        self.assertEqual(extra, [], f"stale modules in plugin bundle: {extra}")

    def test_the_sync_script_reports_alignment(self) -> None:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sync_plugin_bundle.py"), "--check"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("aligned", result.stdout)


if __name__ == "__main__":
    unittest.main()
