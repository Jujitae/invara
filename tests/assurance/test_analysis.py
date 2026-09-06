"""Engineering analysis: measured where it can be, declared where it cannot."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from invara.assurance import analysis

DUPLICATE = "\n".join(
    [
        "def parse(line):",
        "    parts = line.split(',')",
        "    if len(parts) != 3:",
        "        raise ValueError(line)",
        "    sku, qty, price = parts",
        "    return sku, int(qty), float(price)",
        "",
    ]
)


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_bytes(b"")
        (self.root / "pkg" / "a.py").write_bytes(("from . import b\n\n" + DUPLICATE + "\ndef only_in_a():\n    return b.helper()\n").encode("utf-8"))
        (self.root / "pkg" / "b.py").write_bytes(("from . import a\n\n" + DUPLICATE + "\ndef helper():\n    return 1\n\n_private = 2\n").encode("utf-8"))
        (self.root / "pkg" / "c.py").write_bytes(b"import json\n\ndef c():\n    return json.dumps({})\n")
        (self.root / "pkg" / "__pycache__").mkdir()
        (self.root / "pkg" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
        (self.root / "notes.txt").write_bytes(b"not code\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class Measuring(Tree):
    def test_files_and_lines_are_counted_over_python_sources_only(self) -> None:
        metrics = analysis.measure(self.root)
        self.assertEqual(sorted(metrics.per_file), ["pkg/__init__.py", "pkg/a.py", "pkg/b.py", "pkg/c.py"])
        self.assertEqual(metrics.files, 4)
        self.assertEqual(metrics.lines, sum(metrics.per_file.values()))
        self.assertNotIn("notes.txt", metrics.per_file)

    def test_duplicate_blocks_are_found_with_their_locations(self) -> None:
        metrics = analysis.measure(self.root, window=5)
        self.assertGreaterEqual(metrics.duplicate_blocks["count"], 1)
        block = metrics.duplicate_blocks["blocks"][0]
        files = sorted(location["file"] for location in block["occurrences"])
        self.assertEqual(files, ["pkg/a.py", "pkg/b.py"])
        self.assertEqual(len(block["digest"]), 64)

    def test_dependency_edges_and_cycles_are_reported(self) -> None:
        metrics = analysis.measure(self.root)
        self.assertIn(["pkg.a", "pkg.b"], metrics.python["edges"])
        self.assertIn(["pkg.b", "pkg.a"], metrics.python["edges"])
        self.assertEqual(metrics.python["cycles"], [["pkg.a", "pkg.b"]])
        self.assertNotIn(["pkg.c", "json"], metrics.python["edges"], "stdlib imports are not local edges")

    def test_the_public_surface_is_the_top_level_names_without_underscores(self) -> None:
        metrics = analysis.measure(self.root)
        self.assertEqual(metrics.python["public_names"]["pkg.b"], ["helper", "parse"])
        self.assertEqual(metrics.python["public_names"]["pkg.a"], ["only_in_a", "parse"])

    def test_the_size_distribution_and_largest_files_are_stable(self) -> None:
        metrics = analysis.measure(self.root)
        self.assertEqual(sum(metrics.size_distribution.values()), metrics.files)
        self.assertEqual(metrics.largest[0]["file"], max(metrics.per_file, key=lambda f: (metrics.per_file[f], f)))

    def test_measurement_is_reproducible_and_content_addressed(self) -> None:
        one = analysis.measure(self.root)
        two = analysis.measure(self.root)
        self.assertEqual(one.as_dict(), two.as_dict())
        self.assertEqual(one.tree_digest, two.tree_digest)
        (self.root / "pkg" / "c.py").write_bytes(b"import json\n\ndef c():\n    return json.dumps([])\n")
        self.assertNotEqual(analysis.measure(self.root).tree_digest, one.tree_digest)

    def test_a_syntax_error_is_reported_not_fatal(self) -> None:
        (self.root / "pkg" / "broken.py").write_bytes(b"def (:\n")
        metrics = analysis.measure(self.root)
        self.assertIn("pkg/broken.py", metrics.unparsed)
        self.assertEqual(metrics.files, 5)

    def test_the_metrics_round_trip(self) -> None:
        metrics = analysis.measure(self.root)
        self.assertEqual(analysis.Metrics.from_dict(metrics.as_dict()), metrics)


class Deltas(Tree):
    def test_a_delta_shows_what_changed_reproducibly(self) -> None:
        before = analysis.measure(self.root, window=5)
        (self.root / "pkg" / "a.py").write_bytes(b"from . import b\n\n\ndef only_in_a():\n    return b.parse('a,1,2')\n")
        (self.root / "pkg" / "b.py").write_bytes((DUPLICATE + "\ndef helper():\n    return 1\n\n_private = 2\n").encode("utf-8"))
        after = analysis.measure(self.root, window=5)
        delta = analysis.delta(before, after)
        self.assertLess(delta["duplicate_blocks"]["after"], delta["duplicate_blocks"]["before"])
        self.assertEqual(delta["cycles"]["after"], 0)
        self.assertEqual(delta["cycles"]["before"], 1)
        self.assertIn("pkg.a.parse", delta["public_surface"]["removed"])
        self.assertEqual(delta["public_surface"]["added"], [])
        self.assertLess(delta["lines"]["after"], delta["lines"]["before"])
        self.assertEqual(delta["tree_digest"], {"before": before.tree_digest, "after": after.tree_digest})


class Findings(unittest.TestCase):
    def test_a_finding_needs_a_known_kind_paths_and_a_declarer(self) -> None:
        good = analysis.validate_findings([{"id": "dup-1", "kind": "duplicate_implementation", "paths": ["pkg/a.py", "pkg/b.py"], "summary": "parse is duplicated", "declared_by": "host-agent"}])
        self.assertEqual(good[0]["kind"], "duplicate_implementation")
        self.assertEqual(good[0]["declared_by"], "host-agent")

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_findings([{"id": "x", "kind": "vibes", "paths": [], "summary": "s", "declared_by": "a"}])

    def test_findings_are_never_scored(self) -> None:
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_findings([{"id": "x", "kind": "dead_code", "paths": ["a.py"], "summary": "s", "declared_by": "a", "score": 7}])

    def test_the_twelve_kinds_are_the_contract_list(self) -> None:
        self.assertEqual(len(analysis.FINDING_KINDS), 12)
        self.assertIn("circular_dependency", analysis.FINDING_KINDS)
        self.assertIn("untested_critical_behavior", analysis.FINDING_KINDS)


if __name__ == "__main__":
    unittest.main()
