"""Observation path selectors: the language every policy speaks."""

from __future__ import annotations

import unittest

from invara.assurance import paths


class Splitting(unittest.TestCase):
    def test_root_is_the_empty_tuple(self) -> None:
        self.assertEqual(paths.split("/"), ())

    def test_segments_round_trip_through_join(self) -> None:
        self.assertEqual(paths.split("/a/b/0"), ("a", "b", "0"))
        self.assertEqual(paths.join("a", "b", "0"), "/a/b/0")
        self.assertEqual(paths.join(), "/")

    def test_slashes_and_tildes_inside_a_segment_are_escaped(self) -> None:
        joined = paths.join("files", "out/report.txt", "a~b")
        self.assertEqual(joined, "/files/out~1report.txt/a~0b")
        self.assertEqual(paths.split(joined), ("files", "out/report.txt", "a~b"))

    def test_a_path_must_start_at_the_root(self) -> None:
        with self.assertRaises(paths.PathError):
            paths.split("a/b")


class Selectors(unittest.TestCase):
    def test_a_concrete_selector_matches_only_itself(self) -> None:
        self.assertTrue(paths.matches("/out/value/id", "/out/value/id"))
        self.assertFalse(paths.matches("/out/value/id", "/out/value/ids"))
        self.assertFalse(paths.matches("/out/value/id", "/out/value"))

    def test_star_matches_exactly_one_segment(self) -> None:
        self.assertTrue(paths.matches("/out/value/*/id", "/out/value/3/id"))
        self.assertFalse(paths.matches("/out/value/*/id", "/out/value/id"))
        self.assertFalse(paths.matches("/out/value/*/id", "/out/value/a/b/id"))

    def test_double_star_matches_any_depth_including_none(self) -> None:
        self.assertTrue(paths.matches("/out/**/id", "/out/id"))
        self.assertTrue(paths.matches("/out/**/id", "/out/value/rows/7/id"))
        self.assertFalse(paths.matches("/out/**/id", "/db/value/id"))

    def test_the_root_selector_matches_the_root_only(self) -> None:
        self.assertTrue(paths.matches("/", "/"))
        self.assertFalse(paths.matches("/", "/out"))

    def test_a_selector_is_parsed_once_and_reused(self) -> None:
        selector = paths.parse_selector("/db/tables/*/rows/**")
        self.assertEqual(selector.segments, ("db", "tables", "*", "rows", "**"))
        self.assertTrue(selector.matches("/db/tables/orders/rows/0/id"))
        self.assertTrue(selector.matches("/db/tables/orders/rows"))

    def test_a_malformed_selector_is_refused(self) -> None:
        for bad in ("out/value", "", "/out//value", "/a/***"):
            with self.assertRaises(paths.PathError, msg=bad):
                paths.parse_selector(bad)


class Breadth(unittest.TestCase):
    """What a selector says about how much it covers, for the ignore refusals."""

    def test_the_root_is_blanket(self) -> None:
        self.assertTrue(paths.parse_selector("/").is_root)
        self.assertTrue(paths.parse_selector("/").is_blanket)

    def test_wildcard_only_selectors_are_blanket(self) -> None:
        for selector in ("/*", "/**", "/*/*", "/**/*"):
            self.assertTrue(paths.parse_selector(selector).is_blanket, selector)

    def test_one_concrete_segment_is_enough_to_stop_being_blanket(self) -> None:
        self.assertFalse(paths.parse_selector("/out/**").is_blanket)
        self.assertFalse(paths.parse_selector("/*/created_at").is_blanket)

    def test_concrete_depth_counts_named_segments_only(self) -> None:
        self.assertEqual(paths.parse_selector("/out/*/id").concrete_segments, 2)
        self.assertEqual(paths.parse_selector("/**").concrete_segments, 0)


if __name__ == "__main__":
    unittest.main()
