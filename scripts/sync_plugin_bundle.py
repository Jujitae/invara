#!/usr/bin/env python3
"""Keep ``plugin/src/invara/`` a byte-identical copy of ``src/invara/``.

The plugin ships the package as bundled source, and a copy drifts. The
suite fails when it does; this script is how a maintainer makes it stop.

    python scripts/sync_plugin_bundle.py            # copy, deleting stale modules
    python scripts/sync_plugin_bundle.py --check    # report only; exit 1 on drift
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "invara"
BUNDLED = ROOT / "plugin" / "src" / "invara"
SIDE_FILES = ("LICENSE", "NOTICE")


def tree(base: Path) -> dict[str, Path]:
    return {
        path.relative_to(base).as_posix(): path
        for path in base.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def drift() -> list[str]:
    source, bundled = tree(SOURCE), tree(BUNDLED)
    problems = [f"missing: {name}" for name in sorted(set(source) - set(bundled))]
    problems += [f"stale: {name}" for name in sorted(set(bundled) - set(source))]
    problems += [
        f"differs: {name}" for name in sorted(set(source) & set(bundled)) if source[name].read_bytes() != bundled[name].read_bytes()
    ]
    for name in SIDE_FILES:
        if (ROOT / name).read_bytes() != (ROOT / "plugin" / name).read_bytes():
            problems.append(f"differs: plugin/{name}")
    return problems


def sync() -> None:
    source, bundled = tree(SOURCE), tree(BUNDLED)
    for name, path in source.items():
        target = BUNDLED / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    for name in set(bundled) - set(source):
        bundled[name].unlink()
    for name in SIDE_FILES:
        (ROOT / "plugin" / name).write_bytes((ROOT / name).read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    arguments = parser.parse_args()
    if not arguments.check:
        sync()
    problems = drift()
    if problems:
        print("plugin bundle drift:")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"plugin bundle aligned: {len(tree(SOURCE))} module(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
