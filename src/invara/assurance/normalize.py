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
  comparable leaf left once the policies are applied. Freeze refuses on it.

The workspace path is the one normalization INVARA applies on its own
authority (:data:`BUILTIN_WORKSPACE_POLICY`), because INVARA chose that
path; it is still logged as an action like any other.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from . import paths
from .manifest import Policy, Probe, content_digest

__all__ = [
    "Action",
    "BUILTIN_WORKSPACE_POLICY",
    "Normalized",
    "PLACEHOLDER_REDACTED",
    "PLACEHOLDER_TIMESTAMP",
    "WORKSPACE_TOKEN",
    "erases_signal",
    "normalize",
    "parse_timestamp",
    "policy_set_digest",
    "propose_policies",
    "uncovered_volatile",
    "volatile_paths",
]

BUILTIN_WORKSPACE_POLICY = "builtin:workspace"
WORKSPACE_TOKEN = "$WORKSPACE"
PLACEHOLDER_REDACTED = "<redacted>"
PLACEHOLDER_TIMESTAMP = "<timestamp>"
_NON_SIGNAL = frozenset({PLACEHOLDER_REDACTED, PLACEHOLDER_TIMESTAMP})

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX_RE = re.compile(r"\b[0-9a-f]{16,64}\b")
TIMESTAMP_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"
)
_ID_PLACEHOLDER_RE = re.compile(r"^<id:\d+>$")
_TOKEN_PATH_RE = re.compile(r"\$[A-Z_]+[^\s\"'<>|]*")
_PATTERNS = {"uuid": UUID_RE, "hex": HEX_RE}

_LEAF_TYPES = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class Action:
    """One thing a policy did to one path. The audit log is a tuple of these."""

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

    def raw_path(self, path: str) -> str:
        return self.path_map.get(path, path)


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


def parse_timestamp(value: Any) -> float | None:
    """Epoch seconds for an ISO 8601 instant (or an epoch number), else None."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
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
    def __init__(self, policies: Sequence[Policy], workspace: str | None) -> None:
        self.compiled = [_Compiled(policy) for policy in policies if policy.accepted]
        self.workspace = workspace
        self.actions: list[Action] = []
        self.path_map: dict[str, str] = {}
        self.id_maps: dict[str, dict[Any, str]] = {}
        self.ambiguities: list[str] = []

    def matching(self, path: str, *kinds: str) -> list[Policy]:
        return [c.policy for c in self.compiled if (not kinds or c.policy.kind in kinds) and c.matches(path)]

    def note(self, policy: Policy | str, kind: str, path: str, note: str) -> None:
        ident = policy if isinstance(policy, str) else policy.id
        self.actions.append(Action(ident, kind, path, note))


def _child(path: str, key: Any) -> str:
    return paths.join(*paths.split(path), key)


def _subpaths(value: Any, path: str) -> Iterator[str]:
    yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _subpaths(item, _child(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _subpaths(item, _child(path, index))


def _workspace_variants(workspace: str) -> list[str]:
    forward = workspace.replace("\\", "/")
    backward = workspace.replace("/", "\\")
    variants = [workspace, forward, backward, "\\\\?\\" + backward]
    out: list[str] = []
    for variant in variants:
        if variant and variant not in out:
            out.append(variant)
    return sorted(out, key=len, reverse=True)


def _canonical_paths(text: str, roots: Sequence[tuple[str, str]]) -> str:
    """Replace each root path with its token, then slash the path that follows."""

    for token, root in roots:
        for variant in _workspace_variants(root):
            text = text.replace(variant, token)
    return _TOKEN_PATH_RE.sub(lambda m: m.group(0).replace("\\", "/"), text)


def _leaf(value: Any, path: str, run: _Run) -> Any:
    original = value
    if isinstance(value, str):
        for policy in run.matching(path, "line_endings"):
            fixed = value.replace("\r\n", "\n").replace("\r", "\n")
            if fixed != value:
                run.note(policy, "line_endings", path, "normalized line endings to LF")
            value = fixed
        roots: list[tuple[str, str]] = []
        if run.workspace:
            roots.append((WORKSPACE_TOKEN, run.workspace))
        canonical = run.matching(path, "path_canonical")
        for policy in canonical:
            roots.extend((r["token"], r["path"]) for r in policy.params.get("roots", []))
        if roots:
            fixed = _canonical_paths(value, roots)
            if fixed != value:
                run.note(canonical[0] if canonical else BUILTIN_WORKSPACE_POLICY, "path_canonical", path, "canonicalized paths")
            value = fixed
        for policy in run.matching(path, "stable_map"):
            mapping = policy.params["map"]
            if value in mapping:
                run.note(policy, "stable_map", path, f"{value!r} -> {mapping[value]!r}")
                value = mapping[value]
            elif policy.params.get("in_text"):
                fixed = value
                for source, target in mapping.items():
                    fixed = fixed.replace(source, target)
                if fixed != value:
                    run.note(policy, "stable_map", path, "mapped inside text")
                value = fixed
        for policy in run.matching(path, "timestamp"):
            if policy.params.get("tolerance_s") is not None:
                continue
            if parse_timestamp(value) is not None:
                run.note(policy, "timestamp", path, f"{value!r} -> {PLACEHOLDER_TIMESTAMP}")
                value = PLACEHOLDER_TIMESTAMP
            else:
                fixed, count = TIMESTAMP_RE.subn(PLACEHOLDER_TIMESTAMP, value)
                if count:
                    run.note(policy, "timestamp", path, f"replaced {count} timestamp(s) inside text")
                    value = fixed
                else:
                    run.note(policy, "timestamp", path, f"not a timestamp: {value[:40]!r}")
    elif isinstance(value, float):
        for policy in run.matching(path, "float_edges"):
            if value != value and policy.params.get("nan_equal", True):
                run.note(policy, "float_edges", path, "NaN -> <nan>")
                value = "<nan>"
            elif value in (float("inf"), float("-inf")):
                run.note(policy, "float_edges", path, "infinity -> placeholder")
                value = "<inf>" if value > 0 else "<-inf>"
            elif value == 0.0 and str(value).startswith("-") and policy.params.get("negative_zero_equal", True):
                run.note(policy, "float_edges", path, "-0.0 -> 0.0")
                value = 0.0
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        for policy in run.matching(path, "timestamp"):
            if policy.params.get("epoch") and policy.params.get("tolerance_s") is None:
                run.note(policy, "timestamp", path, f"{value!r} -> {PLACEHOLDER_TIMESTAMP}")
                value = PLACEHOLDER_TIMESTAMP
    for policy in run.matching(path, "redact"):
        replacement = policy.params.get("replacement", PLACEHOLDER_REDACTED)
        if value != replacement:
            run.note(policy, "redact", path, "redacted")
        value = replacement
    del original
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


def _walk(value: Any, path: str, run: _Run) -> Any:
    if isinstance(value, str):
        for policy in run.matching(path, "canonical_json"):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError) as error:
                run.note(policy, "canonical_json", path, f"unparseable: {str(error)[:60]}")
            else:
                run.note(policy, "canonical_json", path, "parsed JSON")
                value = parsed
                break
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            child = _child(path, key)
            ignoring = run.matching(child, "ignore")
            if ignoring:
                run.note(ignoring[0], "ignore", child, "removed")
                continue
            out[key] = _walk(value[key], child, run)
        return out
    if isinstance(value, list):
        kept: list[tuple[int, Any]] = []
        for index, item in enumerate(value):
            child = _child(path, index)
            ignoring = run.matching(child, "ignore")
            if ignoring:
                run.note(ignoring[0], "ignore", child, "removed")
                continue
            kept.append((index, _walk(item, child, run)))
        unordered = run.matching(path, "unordered_set", "unordered_multiset")
        if unordered:
            policy = unordered[0]
            keyed = [(_sort_key(_mask_for_sort(item, _child(path, index), run)), index, item) for index, item in kept]
            keyed.sort(key=lambda entry: (entry[0], entry[1]))
            if policy.kind == "unordered_set":
                deduped: list[tuple[str, int, Any]] = []
                for entry in keyed:
                    if deduped and deduped[-1][0] == entry[0]:
                        continue
                    deduped.append(entry)
                keyed = deduped
            duplicates = 0
            for left, right in zip(keyed, keyed[1:]):
                if left[0] == right[0] and _sort_key(left[2]) != _sort_key(_mask_for_sort(left[2], _child(path, left[1]), run)):
                    duplicates += 1
            if duplicates:
                run.ambiguities.append(
                    f"{path}: {duplicates + 1} elements are indistinguishable apart from generated identifiers; correspondence assumed by position"
                )
            run.note(policy, policy.kind, path, f"sorted {len(keyed)} element(s)" + (f", dropped {len(kept) - len(keyed)} duplicate(s)" if len(keyed) != len(kept) else ""))
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
        return value
    if pattern == "any" and isinstance(value, (str, int, float)):
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
    workspace path INVARA created for it. The raw input is deep-copied
    before anything happens to it.
    """

    run = _Run(policies, (context or {}).get("workspace"))
    value = _walk(copy.deepcopy(probes), "/", run)
    _assign_ids(value, run)
    return Normalized(
        value=value,
        actions=tuple(run.actions),
        path_map=dict(run.path_map),
        id_maps={group: dict(mapping) for group, mapping in run.id_maps.items()},
        ambiguities=tuple(run.ambiguities),
    )


# --------------------------------------------------------------------------
# signal, volatility


def _comparable_leaves(value: Any) -> int:
    if isinstance(value, dict):
        return 1 if not value else sum(_comparable_leaves(item) for item in value.values())
    if isinstance(value, list):
        return 1 if not value else sum(_comparable_leaves(item) for item in value)
    if isinstance(value, str) and value in _NON_SIGNAL:
        return 0
    return 1


def erases_signal(normalized_probes: Any, probes: Sequence[Probe]) -> list[str]:
    """Mandatory probes that the policy set has left with nothing to compare."""

    problems: list[str] = []
    for probe in probes:
        if not probe.mandatory:
            continue
        if not isinstance(normalized_probes, dict) or probe.id not in normalized_probes:
            problems.append(f"{probe.id}: mandatory probe not observed")
        elif _comparable_leaves(normalized_probes[probe.id]) == 0:
            problems.append(f"{probe.id}: no comparable observation remains after normalization")
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
    if a != b or type(a) is not type(b):
        out.add(path)


def volatile_paths(runs: Sequence[Any]) -> list[str]:
    """Paths whose values differ between repeated observations of one system."""

    if len(runs) < 2:
        return []
    out: set[str] = set()
    for other in runs[1:]:
        _differences(runs[0], other, "/", out)
    return sorted(out)


def _lookup(value: Any, path: str) -> tuple[bool, Any]:
    node = value
    for segment in paths.split(path):
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return False, None
    return True, node


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
    """Volatile paths no accepted policy addresses."""

    compiled = [_Compiled(policy) for policy in policies if policy.accepted and policy.kind not in ("exact", "ordered_sequence")]
    return [path for path in volatile if not any(c.matches(path) for c in compiled)]
