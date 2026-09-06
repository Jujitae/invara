"""Semantic normalization — the declared, audited part of "the same".

Two observations of the same behaviour are rarely byte-identical: the run
had a different temporary directory, the order ran under a fresh UUID, the
report file carries a timestamp. Exact comparison would call every one of
those a divergence, and a blanket "ignore the noisy fields" would hide the
real ones among them. So every relaxation is a policy with a selector and a
reason, applied to source and target alike, and every application is an
action in the audit log. Raw observations are never touched; the normalized
value is a new structure.

Three things this module refuses to be:

* **A wildcard.** Generated identifiers are mapped, not erased. The same raw
  identifier maps to the same ``<id:N>`` everywhere the policy looks, in
  order of first appearance along the policy's primary selector, so two
  records that shared an id on the source side must share one on the target
  side.
* **A guesser.** :func:`propose_policies` reads a stability run and says what
  *kind* of volatility a path shows. Its proposals are ``inferred`` and
  unaccepted, and the normalizer ignores unaccepted policies entirely.
* **A shredder.** :func:`erases_signal` reports a mandatory probe that has no
  comparable leaf left once the policies are applied. Freeze refuses on it,
  and so does every amendment.

Two normalizations are applied on INVARA's own authority and still logged
as actions: the per-run workspace and the resolved system root are replaced
by tokens (:data:`BUILTIN_WORKSPACE_POLICY`), and any raw text that already
looks like one of this module's placeholders or tokens is escaped first
(:data:`BUILTIN_RESERVED_POLICY`), so a program cannot pass by printing
``<id:1>`` where a mapped identifier would appear.
"""

from __future__ import annotations

import copy
from .. import exact_json as json
from ..exact_json import decimal_value, exact_tolerance, number_equal
from decimal import Decimal
import math
import re
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from . import paths
from .manifest import Policy, Probe, content_digest

__all__ = [
    "Action",
    "BUILTIN_RESERVED_POLICY",
    "BUILTIN_WORKSPACE_POLICY",
    "Normalized",
    "PLACEHOLDER_REDACTED",
    "PLACEHOLDER_TIMESTAMP",
    "ROOT_TOKEN",
    "WORKSPACE_TOKEN",
    "describe_gap",
    "is_tolerance_policy",
    "tolerance_verdict",
    "erases_signal",
    "normalize",
    "overbroad_tolerances",
    "parse_timestamp",
    "policy_set_digest",
    "propose_policies",
    "uncovered_volatile",
    "uncovered_volatile_empirical",
    "unobtained_observable",
    "volatile_paths",
]

#: The two policies INVARA applies on its own authority. Every application
#: is logged like any declared policy's.
BUILTIN_WORKSPACE_POLICY = "builtin:paths"
BUILTIN_RESERVED_POLICY = "builtin:reserved"
WORKSPACE_TOKEN = "$WORKSPACE"
ROOT_TOKEN = "$ROOT"
PLACEHOLDER_REDACTED = "<redacted>"
PLACEHOLDER_TIMESTAMP = "<timestamp>"
_NON_SIGNAL = frozenset({PLACEHOLDER_REDACTED, PLACEHOLDER_TIMESTAMP})

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{16,64}\b")
TIMESTAMP_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"
)
_ID_PLACEHOLDER_RE = re.compile(r"^<id:\d+>$")
# Escape both reserved text and the escape character itself, anywhere in a
# string. No left boundary: generated tokens may follow arbitrary raw text.
_RESERVED_TEXT_RE = re.compile(r"\x00|<(?:id:\d+|timestamp|nan|inf|-inf|redacted)>|\$(?:WORKSPACE|ROOT)\b")
_PATTERNS = {"uuid": UUID_RE, "hex": HEX_RE}
_PATH_CONTINUATION = r"(?![A-Za-z0-9_.\-])([\\/][^\s\"'<>|,;)\]]*)?"

_LEAF_TYPES = (str, int, float, Decimal, bool, type(None))
_MAX_TIMESTAMP_TOLERANCE_S = 366 * 86400


@dataclass(frozen=True)
class Action:
    """One thing a policy did — or looked at — at one path. The audit log is a tuple of these."""

    policy_id: str
    kind: str
    path: str
    note: str

    def as_dict(self) -> dict[str, str]:
        return {"policy_id": self.policy_id, "kind": self.kind, "path": self.path, "note": self.note}


@dataclass(frozen=True)
class Normalized:
    value: Any
    actions: tuple[Action, ...]
    #: normalized path -> raw path, for every node an unordered sort moved
    path_map: dict[str, str]
    #: policy group -> {raw identifier: placeholder}
    id_maps: dict[str, dict[Any, str]]
    ambiguities: tuple[str, ...]
    #: paths at which a string leaf was parsed as JSON; everything below
    #: them came from that one string
    parsed_roots: tuple[str, ...] = ()

    def raw_path(self, path: str) -> str:
        if path in self.path_map:
            return self.path_map[path]
        for root in self.parsed_roots:
            if path.startswith(root + "/"):
                return self.path_map.get(root, root)
        return path


def policy_set_digest(policies: Sequence[Policy]) -> str:
    """The digest of the policies that actually apply — accepted ones."""

    return content_digest([policy.as_dict() for policy in policies if policy.accepted])


# --------------------------------------------------------------------------
# timestamps without the clock module


def _days_from_civil(year: int, month: int, day: int) -> int:
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def parse_timestamp(value: Any, *, epoch: bool = False) -> float | None:
    """Epoch seconds for an ISO 8601 instant, or for an epoch number when allowed, else None."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return (value if isinstance(value, Decimal) else float(value)) if epoch else None
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_RE.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 61):
        return None
    fraction = float(match.group(7)) if match.group(7) else 0.0
    zone = match.group(8)
    offset = 0
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    days = _days_from_civil(year, month, day)
    return days * 86400 + hour * 3600 + minute * 60 + second + fraction - offset


# --------------------------------------------------------------------------
# the run


class _Compiled:
    __slots__ = ("policy", "selectors")

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.selectors = tuple(paths.parse_selector(selector) for selector in policy.selectors())

    def matches(self, path: str) -> bool:
        return any(selector.matches(path) for selector in self.selectors)


class _Run:
    def __init__(self, policies: Sequence[Policy], workspace: str | None, root: str | None = None) -> None:
        self.compiled = [_Compiled(policy) for policy in policies if policy.accepted]
        self.workspace = workspace
        self.root = root
        self.actions: list[Action] = []
        self.path_map: dict[str, str] = {}
        self.id_maps: dict[str, dict[Any, str]] = {}
        self.ambiguities: list[str] = []
        self.parsed_roots: list[str] = []

    def matching(self, path: str, *kinds: str) -> list[Policy]:
        return [c.policy for c in self.compiled if (not kinds or c.policy.kind in kinds) and c.matches(path)]

    def note(self, policy: Policy | str, kind: str, path: str, note: str) -> None:
        ident = policy if isinstance(policy, str) else policy.id
        self.actions.append(Action(ident, kind, path, note))


_child = paths.child


def _subpaths(value: Any, path: str) -> Iterator[str]:
    yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _subpaths(item, _child(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _subpaths(item, _child(path, index))


def _root_variants(root: str) -> list[str]:
    forward = root.replace("\\", "/")
    backward = root.replace("/", "\\")
    variants = [root, forward, backward, "\\\\?\\" + backward]
    out: list[str] = []
    for variant in variants:
        if len(variant) >= 4 and variant not in out and variant.rstrip("\\/") not in ("", "C:", "c:"):
            out.append(variant)
    return sorted(out, key=len, reverse=True)


def _canonical_paths(text: str, roots: Sequence[tuple[str, str, str]]) -> tuple[str, dict[str, int]]:
    """Replace each root at a path boundary with its token; slash only the path that follows.

    ``roots`` are ``(token, path, policy_id)``; the result says how many
    replacements each policy made, so each is credited for its own.
    """

    counts: dict[str, int] = {}
    for token, root, policy_id in roots:
        for variant in _root_variants(root):
            # Text has already been escaped. Match roots in the same alphabet,
            # including real directory names containing reserved text.
            pattern = re.compile(re.escape(_encode_reserved(variant.rstrip("\\/"))) + _PATH_CONTINUATION)

            def substitute(match: re.Match[str]) -> str:
                counts[policy_id] = counts.get(policy_id, 0) + 1
                continuation = match.group(1) or ""
                return token + continuation.replace("\\", "/")

            text = pattern.sub(substitute, text)
    return text, counts


def _encode_reserved(text: str) -> str:
    """Prefix raw reserved text with NUL; double every raw NUL.

    At a NUL, a decoder consumes either another NUL or one reserved token;
    elsewhere it consumes one unchanged character. Thus decoding is unique,
    including for raw strings already in the encoded range. Generated
    placeholders/tokens are inserted later without the escape prefix.
    Ordinary angle brackets and dollar signs stay available to declared
    policies; only actual reserved text and NUL need encoding.
    """

    return _RESERVED_TEXT_RE.sub(lambda match: "\x00" + match.group(0), text)


def _escape_reserved(value: Any, path: str, run: _Run) -> Any:
    """Encode raw text once, before inserting generated placeholders/tokens."""

    if not isinstance(value, str):
        return value
    fixed = _encode_reserved(value)
    if fixed != value:
        run.note(BUILTIN_RESERVED_POLICY, "reserved", path, "raw reserved text NUL-prefixed and raw NUL doubled; kept as literal")
    return fixed


def _stable_map_in_text(value: str, mapping: dict[str, str]) -> str:
    """Replace original text once, independent of mapping insertion order."""

    if not mapping:
        return value
    sources = sorted(mapping, key=lambda source: (-len(source), source))
    pattern = re.compile("|".join(re.escape(source) for source in sources))
    return pattern.sub(lambda match: mapping[match.group(0)], value)


def _leaf(value: Any, path: str, run: _Run) -> Any:
    value = _escape_reserved(value, path, run)
    if isinstance(value, str):
        for policy in run.matching(path, "line_endings"):
            fixed = value.replace("\r\n", "\n").replace("\r", "\n")
            run.note(policy, "line_endings", path, "normalized line endings to LF" if fixed != value else "matched; nothing to change")
            value = fixed
        roots: list[tuple[str, str, str]] = []
        if run.workspace:
            roots.append((WORKSPACE_TOKEN, run.workspace, BUILTIN_WORKSPACE_POLICY))
        if run.root:
            roots.append((ROOT_TOKEN, run.root, BUILTIN_WORKSPACE_POLICY))
        for policy in run.matching(path, "path_canonical"):
            roots.extend((r["token"], r["path"], policy.id) for r in policy.params.get("roots", []))
        if roots:
            value, counts = _canonical_paths(value, roots)
            for policy_id, count in counts.items():
                run.note(policy_id, "path_canonical", path, f"canonicalized {count} path(s)")
        for policy in run.matching(path, "stable_map"):
            mapping = policy.params["map"]
            if value in mapping:
                run.note(policy, "stable_map", path, f"{value!r} -> {mapping[value]!r}")
                value = mapping[value]
            elif policy.params.get("in_text"):
                fixed = _stable_map_in_text(value, mapping)
                run.note(policy, "stable_map", path, "mapped inside text" if fixed != value else "matched; no key found in text")
                value = fixed
            else:
                run.note(policy, "stable_map", path, "matched; value not in map")
        for policy in run.matching(path, "timestamp"):
            if policy.params.get("tolerance_s") is not None:
                run.note(policy, "timestamp", path, "matched; compared with tolerance")
                continue
            if parse_timestamp(value) is not None:
                run.note(policy, "timestamp", path, f"{value!r} -> {PLACEHOLDER_TIMESTAMP}")
                value = PLACEHOLDER_TIMESTAMP
            else:
                fixed, count = TIMESTAMP_RE.subn(lambda m: PLACEHOLDER_TIMESTAMP if parse_timestamp(m.group(0)) is not None else m.group(0), value)
                replaced = count if fixed != value else 0
                if replaced:
                    run.note(policy, "timestamp", path, f"replaced {replaced} timestamp(s) inside text")
                    value = fixed
                else:
                    run.note(policy, "timestamp", path, f"not a timestamp: {value[:40]!r}")
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        for policy in (run.matching(path, "float_edges") if isinstance(value, (float, Decimal)) else ()):
            if value != value and policy.params.get("nan_equal", True):
                run.note(policy, "float_edges", path, "NaN -> <nan>")
                value = "<nan>"
            elif value in (float("inf"), float("-inf")):
                run.note(policy, "float_edges", path, "infinity -> placeholder")
                value = "<inf>" if value > 0 else "<-inf>"
            elif value == 0.0 and str(value).startswith("-") and policy.params.get("negative_zero_equal", True):
                run.note(policy, "float_edges", path, "-0.0 -> 0.0")
                value = 0.0
            else:
                run.note(policy, "float_edges", path, "matched; ordinary float")
        for policy in (run.matching(path, "timestamp") if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) else ()):
            if policy.params.get("epoch") and policy.params.get("tolerance_s") is None:
                run.note(policy, "timestamp", path, f"{value!r} -> {PLACEHOLDER_TIMESTAMP}")
                value = PLACEHOLDER_TIMESTAMP
            else:
                run.note(policy, "timestamp", path, "matched; not an epoch number under this policy")
    for policy in run.matching(path, "redact"):
        replacement = policy.params.get("replacement", PLACEHOLDER_REDACTED)
        run.note(policy, "redact", path, "redacted" if value != replacement else "matched; already the replacement")
        value = replacement
    return value


def _mask_for_sort(value: Any, path: str, run: _Run) -> Any:
    """The element with every generated identifier blanked, for a stable sort key."""

    if isinstance(value, dict):
        return {key: _mask_for_sort(item, _child(path, key), run) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_for_sort(item, _child(path, index), run) for index, item in enumerate(value)]
    if run.matching(path, "generated_id"):
        return "<id>"
    return value


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_canonical_json(text: str) -> tuple[Any, str | None]:
    """Parse a text a ``canonical_json`` policy points at: ``(value, None)`` or ``(None, why it did not parse)``."""

    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys, bounded_numbers=True), None
    except (ValueError, TypeError, RecursionError) as error:
        return None, str(error)[:60]


def timestamp_effect(params: Mapping[str, Any], value: Any) -> str:
    """What a timestamp policy without tolerance does to a leaf: ``whole`` (replaced), ``inside`` (timestamps in a text replaced, the rest kept) or ``none``.

    The same branching as the walk below; the coverage map and the
    blind-spot scan ask it so that they never disagree with the comparator.
    """

    if isinstance(value, bool):
        return "none"
    if isinstance(value, (int, float, Decimal)):
        return "whole" if params.get("epoch") else "none"
    if isinstance(value, str):
        if parse_timestamp(value) is not None:
            return "whole"
        if any(parse_timestamp(match.group(0)) is not None for match in TIMESTAMP_RE.finditer(value)):
            return "inside"
    return "none"


def identity_effect(params: Mapping[str, Any], value: Any) -> str:
    """What a generated_id policy does to a leaf: ``whole`` (the value becomes a label), ``inside`` (identifiers in a text are labelled, the rest kept) or ``none``.

    The same branching as :func:`_assign_along`, so that the coverage map and
    the blind-spot scan never disagree with the comparator about it.
    """

    if isinstance(value, bool) or value is None:
        return "none"
    if isinstance(value, str) and _ID_PLACEHOLDER_RE.match(value):
        return "none"
    pattern = params.get("pattern", "any")
    if pattern == "any":
        return "whole" if isinstance(value, (str, int, float, Decimal)) else "none"
    if pattern == "int":
        return "whole" if isinstance(value, int) else "none"
    regex = re.compile(pattern["regex"]) if isinstance(pattern, dict) else _PATTERNS.get(pattern)
    if regex is None or not isinstance(value, str):
        return "none"
    if regex.fullmatch(value):
        return "whole"
    return "inside" if regex.search(value) else "none"


def expand_parsed_roots(tree: Any, policies: Sequence[Policy]) -> tuple[Any, list[str]]:
    """A deep copy of ``tree`` in which every text an accepted ``canonical_json`` policy parses is its parsed value.

    Returns the copy and the paths of the parsed roots, so that
    :func:`collapse_parsed_roots` can serialise them back. The coverage map
    walks the copy (a declaration inside the text then covers real leaves);
    the blind-spot scan mutates it and collapses before comparing.
    """

    compiled = [_Compiled(policy) for policy in policies if policy.accepted and policy.kind == "canonical_json"]
    roots: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, str) and any(c.matches(path) for c in compiled):
            parsed, error = parse_canonical_json(value)
            if error is None:
                roots.append(path)
                # a parsed text may hold another text a policy parses: keep walking, as the comparator does
                return walk(copy.deepcopy(parsed), path)
            return value
        if isinstance(value, dict):
            return {key: walk(item, _child(path, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, _child(path, index)) for index, item in enumerate(value)]
        return value

    return walk(tree, "/"), roots


def collapse_parsed_roots(tree: Any, roots: Sequence[str]) -> Any:
    """Serialise the parsed roots of an expanded tree back to text (in place; returns the tree)."""

    # deepest first, so an inner root is serialised before the outer text that contains it
    for root in sorted(roots, key=lambda r: -len(paths.split(r))):
        segments = paths.split(root)
        node = tree
        for segment in segments[:-1]:
            node = node[int(segment)] if isinstance(node, list) else node[segment]
        last = segments[-1]
        key: Any = int(last) if isinstance(node, list) else last
        node[key] = json.dumps(node[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return tree


def _walk(value: Any, path: str, run: _Run) -> Any:
    if isinstance(value, str):
        for policy in run.matching(path, "canonical_json"):
            parsed, error = parse_canonical_json(value)
            if error is not None:
                run.note(policy, "canonical_json", path, f"unparseable: {error}")
            else:
                run.note(policy, "canonical_json", path, "parsed JSON")
                run.parsed_roots.append(path)
                value = parsed
                break
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            child = _child(path, key)
            ignoring = run.matching(child, "ignore")
            if ignoring:
                for index, policy in enumerate(ignoring):
                    run.note(policy, "ignore", child, "removed" if index == 0 else "matched; already removed")
                continue
            out[key] = _walk(value[key], child, run)
        return out
    if isinstance(value, list):
        kept: list[tuple[int, Any]] = []
        for index, item in enumerate(value):
            child = _child(path, index)
            ignoring = run.matching(child, "ignore")
            if ignoring:
                for position, policy in enumerate(ignoring):
                    run.note(policy, "ignore", child, "removed" if position == 0 else "matched; already removed")
                continue
            kept.append((index, _walk(item, child, run)))
        unordered = run.matching(path, "unordered_set", "unordered_multiset")
        if unordered:
            policy = unordered[0]
            keyed = [(_sort_key(_mask_for_sort(item, _child(path, index), run)), index, item) for index, item in kept]
            keyed.sort(key=lambda entry: (entry[0], entry[1]))
            duplicates = 0
            for left, right in zip(keyed, keyed[1:]):
                if left[0] == right[0] and _sort_key(left[2]) != left[0]:
                    duplicates += 1
            if duplicates:
                run.ambiguities.append(
                    f"{path}: {duplicates + 1} elements are indistinguishable apart from generated identifiers; correspondence assumed by position"
                )
            if policy.kind == "unordered_set":
                deduped: list[tuple[str, int, Any]] = []
                seen_exact: set[str] = set()
                for entry in keyed:
                    exact = _sort_key(entry[2])
                    if exact in seen_exact:
                        continue
                    seen_exact.add(exact)
                    deduped.append(entry)
                keyed = deduped
            run.note(policy, policy.kind, path, f"sorted {len(keyed)} element(s)" + (f", dropped {len(kept) - len(keyed)} duplicate(s)" if len(keyed) != len(kept) else ""))
            for extra in unordered[1:]:
                run.note(extra, extra.kind, path, "matched; already sorted")
            kept = [(index, item) for _, index, item in keyed]
        _remap(kept, path, run)
        return [item for _, item in kept]
    return _leaf(value, path, run)


def _remap(kept: list[tuple[int, Any]], path: str, run: _Run) -> None:
    """Keep ``path_map`` truthful after elements moved or were dropped."""

    if all(new == old for new, (old, _) in enumerate(kept)):
        return
    prefix = path + ("" if path == "/" else "/")
    existing = {key: value for key, value in run.path_map.items() if key.startswith(prefix)}
    for key in existing:
        del run.path_map[key]
    for new, (old, item) in enumerate(kept):
        old_prefix = _child(path, old)
        new_prefix = _child(path, new)
        for sub in _subpaths(item, new_prefix):
            old_sub = old_prefix + sub[len(new_prefix):]
            run.path_map[sub] = existing.get(old_sub, old_sub)


def _assign_ids(value: Any, run: _Run) -> None:
    for compiled in run.compiled:
        policy = compiled.policy
        if policy.kind != "generated_id":
            continue
        group = policy.params.get("group") or policy.id
        mapping = run.id_maps.setdefault(group, {})
        pattern = policy.params.get("pattern", "any")
        regex = re.compile(pattern["regex"]) if isinstance(pattern, dict) else _PATTERNS.get(pattern)
        done: set[str] = set()
        for selector in compiled.selectors:
            _assign_along(value, "/", selector, policy, pattern, regex, mapping, done, run)


def _placeholder(mapping: dict[Any, str], raw: Any) -> str:
    if isinstance(raw, float) and math.isfinite(raw):
        raw = decimal_value(raw)
    if raw not in mapping:
        mapping[raw] = f"<id:{len(mapping) + 1}>"
    return mapping[raw]


def _assign_along(
    value: Any,
    path: str,
    selector: paths.Selector,
    policy: Policy,
    pattern: Any,
    regex: re.Pattern[str] | None,
    mapping: dict[Any, str],
    done: set[str],
    run: _Run,
) -> Any:
    if isinstance(value, dict):
        for key in list(value):
            value[key] = _assign_along(value[key], _child(path, key), selector, policy, pattern, regex, mapping, done, run)
        return value
    if isinstance(value, list):
        for index in range(len(value)):
            value[index] = _assign_along(value[index], _child(path, index), selector, policy, pattern, regex, mapping, done, run)
        return value
    if path in done or not selector.matches(path):
        return value
    done.add(path)
    if isinstance(value, bool) or value is None:
        run.note(policy, "generated_id", path, f"no match for pattern {pattern}")
        return value
    if isinstance(value, str) and _ID_PLACEHOLDER_RE.match(value):
        run.note(policy, "generated_id", path, "already a mapped identifier; left as is")
        return value
    if pattern == "any" and isinstance(value, (str, int, float, Decimal)):
        placeholder = _placeholder(mapping, value)
        run.note(policy, "generated_id", path, f"{value!r} -> {placeholder}")
        return placeholder
    if pattern == "int":
        if isinstance(value, int):
            placeholder = _placeholder(mapping, value)
            run.note(policy, "generated_id", path, f"{value!r} -> {placeholder}")
            return placeholder
        run.note(policy, "generated_id", path, "no match for pattern int")
        return value
    if regex is not None and isinstance(value, str):
        if regex.fullmatch(value):
            placeholder = _placeholder(mapping, value)
            run.note(policy, "generated_id", path, f"{value!r} -> {placeholder}")
            return placeholder
        replaced, count = regex.subn(lambda m: _placeholder(mapping, m.group(0)), value)
        if count:
            run.note(policy, "generated_id", path, f"replaced {count} identifier(s) inside text")
            return replaced
    run.note(policy, "generated_id", path, f"no match for pattern {pattern if not isinstance(pattern, dict) else 'regex'}")
    return value


def normalize(probes: Any, policies: Sequence[Policy], context: dict[str, Any] | None = None) -> Normalized:
    """Apply the accepted policies to one observation's ``probes`` tree.

    ``context`` carries what the run knew that the manifest could not: the
    workspace path INVARA created for it and the system root it ran in.
    The raw input is deep-copied before anything happens to it.
    """

    run = _Run(policies, (context or {}).get("workspace"), (context or {}).get("root"))
    value = _walk(copy.deepcopy(probes), "/", run)
    _assign_ids(value, run)
    return Normalized(
        value=value,
        actions=tuple(run.actions),
        path_map=dict(run.path_map),
        id_maps={group: dict(mapping) for group, mapping in run.id_maps.items()},
        ambiguities=tuple(run.ambiguities),
        parsed_roots=tuple(run.parsed_roots),
    )


# --------------------------------------------------------------------------
# signal, tolerances, volatility


def _scalar_leaves(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _scalar_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalar_leaves(item)
    else:
        yield value


def _comparable_leaves(value: Any) -> int:
    """Scalar leaves that carry evidence.

    Placeholders for redacted values and timestamps carry none. Identifier
    placeholders carry evidence only through relationships: a placeholder
    that repeats (``<id:1>`` at two paths) pins the target to the same
    relationship, while all-distinct placeholders beside nothing else would
    match any target that prints as many distinct values.
    """

    signal = 0
    identifiers: list[str] = []
    for leaf in _scalar_leaves(value):
        if isinstance(leaf, str):
            if leaf in _NON_SIGNAL:
                continue
            if _ID_PLACEHOLDER_RE.match(leaf):
                identifiers.append(leaf)
                continue
        signal += 1
    related = len(identifiers) != len(set(identifiers))
    return signal + (len(identifiers) if signal or related else 0)


GAP_UNOBSERVED = "unobserved"
GAP_EMPTIED = "emptied"


def signal_gaps(normalized_probes: Any, probes: Sequence[Probe], raw: Any = None) -> list[tuple[str, str]]:
    """Mandatory probes with nothing to compare, as ``(probe id, gap)``.

    ``unobserved``: the probe is not in the observation at all.
    ``emptied``: the policy set left it without a comparable leaf. An
    observation that was empty before normalization (no files were written,
    a table has no rows) is evidence of emptiness and is not a gap.
    """

    gaps: list[tuple[str, str]] = []
    for probe in probes:
        if not probe.mandatory:
            continue
        if not isinstance(normalized_probes, dict) or probe.id not in normalized_probes:
            gaps.append((probe.id, GAP_UNOBSERVED))
            continue
        if _comparable_leaves(normalized_probes[probe.id]) > 0:
            continue
        if isinstance(raw, dict) and probe.id in raw and not any(True for _ in _scalar_leaves(raw[probe.id])):
            continue
        gaps.append((probe.id, GAP_EMPTIED))
    return gaps


def describe_gap(probe_id: str, gap: str) -> str:
    if gap == GAP_UNOBSERVED:
        return f"{probe_id}: mandatory probe not observed"
    return f"{probe_id}: no comparable observation remains after normalization"


def erases_signal(normalized_probes: Any, probes: Sequence[Probe], raw: Any = None) -> list[str]:
    """``signal_gaps`` as the sentences a refusal carries."""

    return [describe_gap(probe_id, gap) for probe_id, gap in signal_gaps(normalized_probes, probes, raw)]


def unobtained_observable(probe: Probe, record: Any, requests: Any = None) -> str | None:
    """Why ``record`` is not an observation of ``probe``'s declared observable, or ``None`` when it is one.

    A ``json`` probe declares a parsed value. A record that carries no value
    but a parse failure documents the attempt (what was written, by digest
    and head) and is kept as such; it is not the value the probe declared,
    so two of them are never "the same behaviour" (the comparator refuses
    that input), and a mandatory probe that produced one on every baseline
    input observed nothing usable (the freeze refuses). An absent file
    (``missing``) is an observation of absence and is compared as such per
    input; a probe absent on every input is refused at freeze by the same
    rule (``engine.check_probes``).
    """

    if probe.adapter == "http":
        from .http_boundary import response_problem
        return response_problem(record, probe.params.get("requests") if requests is None else requests)
    if probe.adapter != "json" or not isinstance(record, dict) or "value" in record:
        return None
    if "parse_error" in record:
        return f"the declared JSON value was not obtained (parse error: {record.get('parse_error')})"
    return None


def _values_at(value: Any, path: str, compiled: _Compiled) -> Iterator[Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _values_at(item, _child(path, key), compiled)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _values_at(item, _child(path, index), compiled)
    elif compiled.matches(path):
        yield value


def overbroad_tolerances(raw_runs: Sequence[Any], policies: Sequence[Policy]) -> list[str]:
    """Absolute tolerances larger than every baseline value they apply to."""

    problems: list[str] = []
    for policy in policies:
        if not policy.accepted or policy.kind != "numeric_abs_tolerance":
            continue
        compiled = _Compiled(policy)
        magnitudes = [
            (v.copy_abs() if isinstance(v, Decimal) else abs(v))
            for run in raw_runs
            for v in _values_at(run, "/", compiled)
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
        ]
        if not magnitudes:
            continue
        if any(isinstance(value, Decimal) for value in magnitudes):
            magnitudes = [decimal_value(value) for value in magnitudes]
        largest = max(magnitudes)
        bound = float(policy.params["abs"])
        comparable_bound = decimal_value(bound) if isinstance(largest, Decimal) else bound
        if largest > 0 and comparable_bound >= largest:
            problems.append(f"{policy.id}: absolute tolerance {bound:g} exceeds every baseline value it applies to (max {largest:g})")
    return problems


def _differences(a: Any, b: Any, path: str, out: set[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(a) | set(b):
            if key in a and key in b:
                _differences(a[key], b[key], _child(path, key), out)
            else:
                out.add(_child(path, key))
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b) or sorted(map(_sort_key, a)) == sorted(map(_sort_key, b)) and a != b:
            if a != b:
                out.add(path)
            return
        for index, (left, right) in enumerate(zip(a, b)):
            _differences(left, right, _child(path, index), out)
        return
    if type(a) is not type(b) and not (isinstance(a, (float, Decimal)) and isinstance(b, (float, Decimal))):
        out.add(path)
    elif isinstance(a, float) and a != a and b != b:
        # a NaN printed every run is the same value every run
        return
    elif _is_number(a) and _is_number(b):
        if not number_equal(a, b):
            out.add(path)
    elif a != b:
        out.add(path)


def volatile_paths(runs: Sequence[Any]) -> list[str]:
    """Paths whose values differ between repeated observations of one system."""

    if len(runs) < 2:
        return []
    out: set[str] = set()
    for other in runs[1:]:
        _differences(runs[0], other, "/", out)
    return sorted(out)


_lookup = paths.lookup


def propose_policies(runs: Sequence[Any]) -> list[dict[str, Any]]:
    """What kind of volatility each unstable path shows — proposals, not rules.

    Every proposal is ``origin: inferred`` and ``accepted: false``. A path
    whose variation this cannot classify is proposed as ``human_decision``,
    which is not a policy kind and cannot be accepted by anyone; it can only
    be replaced by a person's actual decision.
    """

    proposals: list[dict[str, Any]] = []
    for number, path in enumerate(volatile_paths(runs), start=1):
        samples = [value for found, value in (_lookup(run, path) for run in runs) if found]
        kind, params, why = _classify(samples)
        proposals.append(
            {
                "id": f"inferred-{number}",
                "kind": kind,
                "path": path,
                "params": params,
                "reason": why,
                "origin": "inferred",
                "accepted": False,
                "samples": samples[:3],
            }
        )
    return proposals


def _classify(samples: list[Any]) -> tuple[str, dict[str, Any], str]:
    if samples and all(isinstance(s, str) for s in samples):
        if all(UUID_RE.fullmatch(s) for s in samples):
            return "generated_id", {"pattern": "uuid"}, "every run produced a different UUID here"
        if all(parse_timestamp(s) is not None for s in samples):
            return "timestamp", {}, "every run produced a different timestamp here"
        if all(UUID_RE.search(s) for s in samples):
            return "generated_id", {"pattern": "uuid"}, "text here embeds a UUID that changes every run"
        if all(TIMESTAMP_RE.search(s) for s in samples):
            return "timestamp", {}, "text here embeds a timestamp that changes every run"
    if samples and all(isinstance(s, list) for s in samples):
        keys = [sorted(map(_sort_key, s)) for s in samples]
        if all(k == keys[0] for k in keys):
            return "unordered_multiset", {}, "the same elements arrive in a different order every run"
    return "human_decision", {}, "values differ between runs in a way INVARA cannot classify; a person must decide whether this is behaviour"


def uncovered_volatile(volatile: Sequence[str], policies: Sequence[Policy]) -> list[str]:
    """Volatile paths no accepted policy selector addresses. A cheap, selector-only view."""

    compiled = [_Compiled(policy) for policy in policies if policy.accepted and policy.kind not in ("exact", "ordered_sequence")]
    return [path for path in volatile if not any(c.matches(path) for c in compiled)]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def is_tolerance_policy(policy: Policy) -> bool:
    """A policy that compares values within a bound instead of rewriting them."""

    return policy.kind in ("numeric_abs_tolerance", "numeric_rel_tolerance") or (policy.kind == "timestamp" and policy.params.get("tolerance_s") is not None)


def tolerance_verdict(policy: Policy, a: Any, b: Any) -> tuple[str, dict[str, Any]]:
    """How a tolerance policy sees two values.

    ``within`` / ``beyond`` with the measured detail; ``inapplicable`` when
    the values are not what the policy compares (then the values themselves
    decide, exactly); ``not_finite`` when a NaN or an infinity is involved,
    which no tolerance can absorb.
    """

    if policy.kind == "timestamp":
        epoch = bool(policy.params.get("epoch", False))
        left, right = parse_timestamp(a, epoch=epoch), parse_timestamp(b, epoch=epoch)
        if left is None or right is None:
            return "inapplicable", {}
        tolerance = float(policy.params["tolerance_s"])
        if isinstance(left, Decimal) or isinstance(right, Decimal):
            if not (decimal_value(left).is_finite() and decimal_value(right).is_finite()):
                return 'not_finite', {}
            within, detail = exact_tolerance(left, right, tolerance)
            return ('within' if within else 'beyond'), {'delta_s': detail['delta'], 'tolerance_s': tolerance}
        delta = abs(left - right)
        return ("within" if delta <= tolerance else "beyond"), {"delta_s": delta, "tolerance_s": tolerance}
    if not (_is_number(a) and _is_number(b)):
        return "inapplicable", {}
    if not ((a.is_finite() if isinstance(a, Decimal) else math.isfinite(a)) and (b.is_finite() if isinstance(b, Decimal) else math.isfinite(b))):
        return "not_finite", {}
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        relative = policy.kind == 'numeric_rel_tolerance'
        bound = policy.params['rel' if relative else 'abs']
        within, detail = exact_tolerance(a, b, bound, relative=relative)
        return ('within' if within else 'beyond'), detail
    delta = abs(a - b)
    if policy.kind == "numeric_abs_tolerance":
        bound = float(policy.params["abs"])
        return ("within" if delta <= bound else "beyond"), {"delta": delta, "abs": bound}
    bound = float(policy.params["rel"])
    scale = max(abs(a), abs(b))
    ratio = 0.0 if scale == 0 else delta / scale
    return ("within" if ratio <= bound else "beyond"), {"ratio": ratio, "rel": bound}


def uncovered_volatile_empirical(runs: Sequence[Any], policies: Sequence[Policy], contexts: Sequence[dict[str, Any] | None] | None = None) -> list[str]:
    """Volatile paths that remain after the policies are actually applied to every run.

    Rewriting policies are applied by normalizing the runs; tolerance
    policies, which rewrite nothing, cover a path when every run's value
    lies within the bound of the first run's.
    """

    normalized = [normalize(run, policies, (contexts[index] if contexts else None)).value for index, run in enumerate(runs)]
    differing = volatile_paths(normalized)
    tolerant = [(policy, _Compiled(policy)) for policy in policies if policy.accepted and is_tolerance_policy(policy)]
    if not tolerant:
        return differing

    def covered(path: str) -> bool:
        for policy, compiled in tolerant:
            if not compiled.matches(path):
                continue
            values = [paths.lookup(run, path) for run in normalized]
            if not all(found for found, _ in values):
                continue
            first = values[0][1]
            if all(tolerance_verdict(policy, first, value)[0] == "within" for _, value in values[1:]):
                return True
        return False

    return [path for path in differing if not covered(path)]
