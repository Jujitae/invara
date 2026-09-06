"""Observation paths and selectors — the language every policy speaks.

An observation is a JSON tree keyed first by probe id. A *path* names one
node in it (``/out/value/orders/0/id``); a *selector* names a set of nodes
(``/out/value/orders/*/id``). Segments follow RFC 6901 escaping so a file
name with a slash in it is still one segment.

Two wildcards, and no others: ``*`` is exactly one segment, ``**`` is any
number including none. That is enough to write every policy the fixtures
need and small enough that "how much does this selector cover" has a
precise answer, which the ignore refusals rely on.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PathError",
    "Selector",
    "child",
    "covers",
    "escape",
    "join",
    "lookup",
    "matches",
    "parse_selector",
    "split",
    "unescape",
]

WILDCARDS = ("*", "**")


class PathError(ValueError):
    """A path or selector that does not parse. Refused, never guessed at."""


def escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def unescape(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def join(*segments: object) -> str:
    """``join("a", "b/c")`` is ``/a/b~1c``; ``join()`` is the root."""

    return "/" + "/".join(escape(str(segment)) for segment in segments)


def covers(selector: "Selector | str", path: str) -> bool:
    """Whether a selector names ``path`` or an ancestor of it.

    A declaration on a container (an exclusion on ``/out/value/tags``, an
    ignore on ``/out/value``) speaks for everything under it. Used by the
    comparator and the coverage map alike, so the two cannot disagree.
    """

    parsed = parse_selector(selector) if isinstance(selector, str) else selector
    if parsed.matches(path):
        return True
    segments = split(path)
    return any(parsed.matches(join(*segments[:count])) for count in range(1, len(segments)))


def child(path: str, key: object) -> str:
    """The path of ``key`` under ``path`` (a dict key or a list index)."""

    return join(*split(path), key)


def lookup(value: object, path: str) -> tuple[bool, object]:
    """Walk a JSON tree along ``path``: ``(True, node)`` when it exists, ``(False, None)`` when not."""

    node = value
    for segment in split(path):
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return False, None
    return True, node


def split(path: str) -> tuple[str, ...]:
    """The segments of an absolute path; the root splits to nothing."""

    if not isinstance(path, str) or not path.startswith("/"):
        raise PathError(f"a path starts at the root: {path!r}")
    if path == "/":
        return ()
    parts = path[1:].split("/")
    if any(part == "" for part in parts):
        raise PathError(f"empty segment in {path!r}")
    return tuple(unescape(part) for part in parts)


def _match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match(rest, path[index:]) for index in range(len(path) + 1))
    if not path:
        return False
    if head == "*" or head == path[0]:
        return _match(rest, path[1:])
    return False


@dataclass(frozen=True)
class Selector:
    text: str
    segments: tuple[str, ...]

    @property
    def is_root(self) -> bool:
        return not self.segments

    @property
    def is_blanket(self) -> bool:
        """True when nothing in the selector names a real segment."""

        return all(segment in WILDCARDS for segment in self.segments)

    @property
    def concrete_segments(self) -> int:
        return sum(1 for segment in self.segments if segment not in WILDCARDS)

    def matches(self, path: str) -> bool:
        return _match(self.segments, split(path))


def parse_selector(text: str) -> Selector:
    segments = split(text)
    for segment in segments:
        if segment.startswith("*") and segment not in WILDCARDS:
            raise PathError(f"unknown wildcard {segment!r} in {text!r}")
    return Selector(text, segments)


def matches(selector: str | Selector, path: str) -> bool:
    if isinstance(selector, str):
        selector = parse_selector(selector)
    return selector.matches(path)
