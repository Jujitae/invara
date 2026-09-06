"""The session state machine, as a pure reducer over stored events.

A session — an assurance run over an existing before/after pair, or a
repair session that governs one unit at a time — is never held in memory
as the truth. Its truth is the append-only event history in the evidence
store, and this module turns that history into a :class:`Snapshot`. The
same events always reduce to the same snapshot, which is what makes
"resume after a crash" a read rather than a guess, and what makes a
history that contains an impossible move a corruption rather than a
surprise.

Thirteen states, and the moves between them are a table. A unit cannot be
accepted from any state but ``UNIT_VERIFYING``; a rejected unit can only be
rolled back; planning needs an analysis and a frozen baseline first; the
two terminal states accept nothing but the trace of an export, which moves
nothing. Every event names the state it leaves
and the state it enters, and the reducer refuses an event whose claims
about either are false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "EVENT_STATES",
    "InvalidTransition",
    "STATES",
    "SessionCorrupt",
    "Snapshot",
    "TRANSITIONS",
    "check_transition",
    "reduce",
]

STATES: tuple[str, ...] = (
    "CREATED",
    "BASELINE_CAPTURING",
    "BASELINE_FROZEN",
    "ANALYZING",
    "PLANNED",
    "UNIT_PREPARING",
    "UNIT_IN_PROGRESS",
    "UNIT_VERIFYING",
    "UNIT_ACCEPTED",
    "UNIT_REJECTED",
    "UNIT_ROLLED_BACK",
    "COMPLETED",
    "BLOCKED",
)

TERMINAL: frozenset[str] = frozenset({"COMPLETED", "BLOCKED"})

#: From each state, the states it may move to. Self-moves are the states in
#: which something is recorded without progress being claimed.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "CREATED": ("BASELINE_CAPTURING", "BLOCKED"),
    "BASELINE_CAPTURING": ("BASELINE_CAPTURING", "BASELINE_FROZEN", "BLOCKED"),
    "BASELINE_FROZEN": ("BASELINE_FROZEN", "ANALYZING", "COMPLETED", "BLOCKED"),
    "ANALYZING": ("ANALYZING", "PLANNED", "BLOCKED"),
    "PLANNED": ("PLANNED", "UNIT_PREPARING", "COMPLETED", "BLOCKED"),
    "UNIT_PREPARING": ("UNIT_IN_PROGRESS", "BLOCKED"),
    "UNIT_IN_PROGRESS": ("UNIT_IN_PROGRESS", "UNIT_VERIFYING", "UNIT_REJECTED", "BLOCKED"),
    "UNIT_VERIFYING": ("UNIT_VERIFYING", "UNIT_ACCEPTED", "UNIT_REJECTED", "UNIT_IN_PROGRESS", "BLOCKED"),
    "UNIT_ACCEPTED": ("PLANNED", "COMPLETED", "BLOCKED"),
    "UNIT_REJECTED": ("UNIT_ROLLED_BACK", "BLOCKED"),
    "UNIT_ROLLED_BACK": ("PLANNED", "COMPLETED", "BLOCKED"),
    "COMPLETED": (),
    "BLOCKED": (),
}

#: Where each event leads. ``None`` means the event records something and
#: the session stays where it is.
EVENT_STATES: dict[str, str | None] = {
    "created": "CREATED",
    "baseline_capture": "BASELINE_CAPTURING",
    "baseline_frozen": "BASELINE_FROZEN",
    "analysis_recorded": "ANALYZING",
    "plan_recorded": "PLANNED",
    "unit_started": "UNIT_PREPARING",
    "unit_in_progress": "UNIT_IN_PROGRESS",
    "unit_verifying": "UNIT_VERIFYING",
    "unit_verified": None,
    "unit_accepted": "UNIT_ACCEPTED",
    "unit_rejected": "UNIT_REJECTED",
    "unit_rolled_back": "UNIT_ROLLED_BACK",
    "continued": "PLANNED",
    "claim_recorded": None,
    "manifest_amended": None,
    "resumed": None,
    # an export is a read that leaves a trace: the verdict, the store-wide problems it counted and the report digest
    # enter the hashed history; allowed in every state, the terminal ones included, and it moves nothing
    "exported": None,
    "blocked": "BLOCKED",
    "completed": "COMPLETED",
}


class InvalidTransition(ValueError):
    """A move the table does not allow. The session does not move."""


class SessionCorrupt(RuntimeError):
    """The stored history cannot be a real session. Nothing is inferred from it."""


def check_transition(current: str, target: str) -> None:
    if current not in TRANSITIONS or target not in TRANSITIONS:
        raise InvalidTransition(f"unknown state in {current} -> {target}")
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current} -> {target} is not an allowed move")


@dataclass
class Snapshot:
    session_id: str = ""
    producer_identity: dict[str, Any] | None = None
    kind: str = ""
    state: str = ""
    manifest_digest: str = ""
    manifest_history: list[str] = field(default_factory=list)
    repository: str | None = None
    base_commit: str | None = None
    accepted_commit: str | None = None
    workspace: str | None = None
    run_workspace: str | None = None
    roots: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    captures: list[dict[str, Any]] = field(default_factory=list)
    frozen: dict[str, Any] | None = None
    baseline_digest: str | None = None
    metrics_before: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    units: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_unit: str | None = None
    accepted_units: list[str] = field(default_factory=list)
    rejected_units: list[str] = field(default_factory=list)
    rollbacks: list[dict[str, Any]] = field(default_factory=list)
    claim_results: list[dict[str, Any]] = field(default_factory=list)
    invalidated_claims: list[dict[str, str]] = field(default_factory=list)
    amendments: list[dict[str, Any]] = field(default_factory=list)
    post_divergence: list[str] = field(default_factory=list)
    weakening: list[str] = field(default_factory=list)
    resumes: list[dict[str, Any]] = field(default_factory=list)
    exports: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    final_verdict: dict[str, Any] | None = None
    report_digest: str | None = None
    events: int = 0
    last_event_at: float = 0.0

    @property
    def divergence_paths(self) -> list[str]:
        """Paths of every mandatory divergence ever recorded (normalized paths)."""

        paths: set[str] = set()
        for result in self.claim_results:
            if result.get("status") == "DIVERGED":
                for divergence in result.get("divergences", []):
                    if isinstance(divergence, Mapping) and divergence.get("path") and divergence.get("mandatory", True):
                        paths.add(str(divergence["path"]))
        return sorted(paths)

    @property
    def informational_divergence_paths(self) -> list[str]:
        """Paths where the two sides differed but the difference was informational or excluded."""

        paths: set[str] = set()
        for result in self.claim_results:
            for divergence in result.get("divergences", []):
                if isinstance(divergence, Mapping) and divergence.get("path") and divergence.get("mandatory", True) is False:
                    paths.add(str(divergence["path"]))
            for divergence in (result.get("coverage", {}) or {}).get("informational_divergences", []) or []:
                if isinstance(divergence, Mapping) and divergence.get("path"):
                    paths.add(str(divergence["path"]))
        return sorted(paths)

    @property
    def all_divergence_paths(self) -> list[str]:
        """Every path a divergence touched, mandatory or not, normalized or raw."""

        paths: set[str] = set(self.divergence_paths) | set(self.informational_divergence_paths)
        for result in self.claim_results:
            entries = list(result.get("divergences", [])) + list((result.get("coverage", {}) or {}).get("informational_divergences", []) or [])
            for divergence in entries:
                if isinstance(divergence, Mapping):
                    for key in ("raw_path_source", "raw_path_target"):
                        if divergence.get(key):
                            paths.add(str(divergence[key]))
        return sorted(paths)

    def verdict_results(self) -> list[dict[str, Any]]:
        """The results the session's verdict rests on: the one rule the workflow and the evidence package share.

        An assure session: the latest result per claim under the current
        manifest. A repair session: the results recorded for the last
        *accepted* unit (a rejected unit's results are about a tree that no
        longer exists) plus the session-level stability claim, which is about
        the source and belongs to every unit alike.
        """

        if self.kind != "repair":
            return self.active_claim_results()
        session_level = [r for r in self.active_claim_results() if r.get("kind") == "baseline_stability"]
        if not self.accepted_units:
            return session_level
        unit = self.units.get(self.accepted_units[-1], {})
        return session_level + [dict(r) for r in unit.get("claim_results", [])]

    def active_claim_results(self) -> list[dict[str, Any]]:
        """Latest result per claim under the current manifest, minus invalidated ones.

        An invalidation applies by event order: it kills the results of that
        claim under that manifest digest recorded *before* the amendment that
        issued it (``before`` is their count at that moment), never a result
        evaluated later. So amending A to B and back to A leaves the old A
        results dead and lets a fresh evaluation under A count. An entry
        without ``before`` (none is written any more) kills every result of
        its claim under its digest, the safe direction.
        """

        dead: dict[tuple[str, str], int] = {}
        for item in self.invalidated_claims:
            key = (str(item["claim_id"]), str(item["manifest_digest"]))
            before = int(item.get("before", len(self.claim_results)))
            dead[key] = max(dead.get(key, -1), before)
        latest: dict[str, dict[str, Any]] = {}
        for index, result in enumerate(self.claim_results):
            if result.get("manifest_digest") != self.manifest_digest:
                continue
            if index < dead.get((str(result.get("claim_id")), str(result.get("manifest_digest"))), -1):
                continue
            latest[str(result["claim_id"])] = result
        return list(latest.values())

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self.units.setdefault(unit_id, {"id": unit_id, "status": "planned"})

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "producer_identity": dict(self.producer_identity) if self.producer_identity is not None else None,
            "kind": self.kind,
            "state": self.state,
            "manifest_digest": self.manifest_digest,
            "manifest_history": list(self.manifest_history),
            "repository": self.repository,
            "base_commit": self.base_commit,
            "accepted_commit": self.accepted_commit,
            "workspace": self.workspace,
            "run_workspace": self.run_workspace,
            "roots": dict(self.roots),
            "created_at": self.created_at,
            "captures": [dict(c) for c in self.captures],
            "frozen": dict(self.frozen) if self.frozen is not None else None,
            "baseline_digest": self.baseline_digest,
            "metrics_before": dict(self.metrics_before) if self.metrics_before is not None else None,
            "findings": [dict(f) for f in self.findings],
            "plan": [dict(u) for u in self.plan],
            "units": {k: dict(v) for k, v in self.units.items()},
            "current_unit": self.current_unit,
            "accepted_units": list(self.accepted_units),
            "rejected_units": list(self.rejected_units),
            "rollbacks": [dict(r) for r in self.rollbacks],
            "claim_results": [dict(r) for r in self.claim_results],
            "invalidated_claims": [dict(i) for i in self.invalidated_claims],
            "amendments": [dict(a) for a in self.amendments],
            "post_divergence": list(self.post_divergence),
            "weakening": list(self.weakening),
            "divergence_paths": self.divergence_paths,
            "informational_divergence_paths": self.informational_divergence_paths,
            "resumes": [dict(r) for r in self.resumes],
            "exports": [dict(e) for e in self.exports],
            "blocked_reason": self.blocked_reason,
            "final_verdict": dict(self.final_verdict) if self.final_verdict is not None else None,
            "report_digest": self.report_digest,
            "events": self.events,
            "last_event_at": self.last_event_at,
        }


def _apply(snapshot: Snapshot, name: str, payload: Mapping[str, Any], at: float) -> None:
    unit_id = str(payload.get("unit_id", "")) if "unit_id" in payload else None
    if name == "created":
        identity = payload.get("producer_identity")
        snapshot.producer_identity = dict(identity) if isinstance(identity, Mapping) else None
        snapshot.kind = str(payload.get("kind", "assure"))
        snapshot.manifest_digest = str(payload.get("manifest_digest", ""))
        snapshot.manifest_history = [snapshot.manifest_digest] if snapshot.manifest_digest else []
        snapshot.repository = payload.get("repository")
        snapshot.base_commit = payload.get("base_commit")
        snapshot.accepted_commit = payload.get("accepted_commit", payload.get("base_commit"))
        snapshot.workspace = payload.get("workspace")
        snapshot.run_workspace = payload.get("run_workspace")
        snapshot.roots = dict(payload.get("roots", {}))
        snapshot.created_at = at
    elif name == "baseline_capture":
        snapshot.captures.append(dict(payload.get("capture", {})))
    elif name == "baseline_frozen":
        snapshot.frozen = dict(payload.get("frozen", {}))
        snapshot.baseline_digest = snapshot.frozen.get("baseline_digest")
    elif name == "analysis_recorded":
        # a repeated analysis replaces the previous one: the latest is in force
        if "metrics" in payload:
            snapshot.metrics_before = dict(payload["metrics"])
        snapshot.findings = [dict(f) for f in payload.get("findings", [])]
    elif name == "plan_recorded":
        snapshot.plan = [dict(u) for u in payload.get("units", [])]
        for unit in snapshot.plan:
            record = snapshot.unit(str(unit["id"]))
            record.update({k: v for k, v in unit.items() if k != "status"})
    elif name == "unit_started":
        record = snapshot.unit(unit_id or "")
        record.update({"status": "started", "worktree": payload.get("worktree"), "base_commit": payload.get("base_commit")})
        snapshot.current_unit = unit_id
    elif name == "unit_in_progress":
        snapshot.unit(unit_id or "")["status"] = "in_progress"
    elif name == "unit_verifying":
        record = snapshot.unit(unit_id or "")
        # a new verification pass: whatever was verified before is void
        for stale in ("verdict", "claim_results", "verified_tree", "metrics_after", "metrics_delta"):
            record.pop(stale, None)
        record.update({"status": "verifying", "tree": payload.get("tree"), "changed_paths": list(payload.get("changed_paths", []))})
    elif name == "unit_verified":
        record = snapshot.unit(unit_id or "")
        record.update(
            {
                "status": "verified",
                "tree": payload.get("tree", record.get("tree")),
                "verified_tree": payload.get("tree", record.get("tree")),
                "manifest_digest": payload.get("manifest_digest"),
                "verdict": dict(payload.get("verdict", {})),
                "claim_results": [dict(r) for r in payload.get("claim_results", [])],
                "metrics_after": dict(payload["metrics_after"]) if payload.get("metrics_after") is not None else None,
                "metrics_delta": dict(payload["metrics_delta"]) if payload.get("metrics_delta") is not None else None,
            }
        )
        snapshot.claim_results.extend(dict(r) for r in payload.get("claim_results", []))
    elif name == "unit_accepted":
        record = snapshot.unit(unit_id or "")
        record.update({"status": "accepted", "commit": payload.get("commit"), "tree": payload.get("tree", record.get("tree")), "reviewed_by": payload.get("reviewed_by")})
        snapshot.accepted_commit = payload.get("commit", snapshot.accepted_commit)
        snapshot.accepted_units.append(unit_id or "")
        snapshot.current_unit = None
    elif name == "unit_rejected":
        record = snapshot.unit(unit_id or "")
        record.update(
            {
                "status": "rejected",
                "reason": payload.get("reason"),
                "patch_digest": payload.get("patch_digest"),
                "patch_path": payload.get("patch_path"),
                # the tree that was diffed, anchored and patched, not the last verified one
                "tree": payload.get("tree", record.get("tree")),
                "changed_paths": list(payload.get("changed_paths", record.get("changed_paths", []))),
            }
        )
        snapshot.rejected_units.append(unit_id or "")
    elif name == "unit_rolled_back":
        snapshot.unit(unit_id or "")["status"] = "rolled_back"
        snapshot.rollbacks.append({"unit_id": unit_id, "removed_worktree": payload.get("removed_worktree"), "at": at})
        snapshot.current_unit = None
    elif name == "claim_recorded":
        snapshot.claim_results.append(dict(payload.get("result", {})))
    elif name == "manifest_amended":
        record = dict(payload.get("record", {}))
        snapshot.amendments.append(record)
        old = str(record.get("old_digest", snapshot.manifest_digest))
        new = str(record.get("new_digest", snapshot.manifest_digest))
        snapshot.manifest_digest = new
        snapshot.manifest_history.append(new)
        # an invalidation names the results it kills by their position in the history: those recorded so far
        snapshot.invalidated_claims.extend({"claim_id": str(claim_id), "manifest_digest": old, "before": len(snapshot.claim_results)} for claim_id in payload.get("invalidated", []))
        snapshot.post_divergence.extend(str(note) for note in payload.get("post_divergence", []))
        snapshot.weakening.extend(str(note) for note in payload.get("weakening", []))
        # a verdict computed under the previous manifest no longer applies
        open_unit = snapshot.units.get(snapshot.current_unit or "")
        if open_unit is not None and open_unit.get("status") == "verified":
            for stale in ("verdict", "claim_results", "verified_tree", "manifest_digest"):
                open_unit.pop(stale, None)
            open_unit["status"] = "verifying"
    elif name == "resumed":
        snapshot.resumes.append(dict(payload))
    elif name == "exported":
        snapshot.exports.append(
            {
                "package_version": payload.get("package_version"),
                "verdict": dict(payload.get("verdict", {}) or {}),
                "store_problems": [str(p) for p in payload.get("store_problems", []) or []],
                "report_digest": payload.get("report_digest"),
                "at": at,
            }
        )
    elif name == "blocked":
        snapshot.blocked_reason = str(payload.get("reason", ""))
    elif name == "completed":
        snapshot.final_verdict = dict(payload.get("final_verdict", {}))
        snapshot.report_digest = payload.get("report_digest")


def reduce(events: Sequence[Mapping[str, Any]], *, session_id: str = "") -> Snapshot:
    """Fold a stored history into a snapshot, refusing any impossible step."""

    if not events:
        raise SessionCorrupt("no events: there is no session here")
    snapshot = Snapshot(session_id=session_id)
    current = ""
    for index, event in enumerate(events):
        name = str(event.get("event", ""))
        if name not in EVENT_STATES:
            raise SessionCorrupt(f"event {index}: unknown event {name!r}")
        from_state = str(event.get("from_state", ""))
        to_state = str(event.get("to_state", ""))
        if index == 0:
            if name != "created" or from_state != "":
                raise SessionCorrupt(f"event 0 must be 'created' from nothing, not {name!r} from {from_state!r}")
            current = "CREATED"
            if to_state != current:
                raise SessionCorrupt("event 0 does not enter CREATED")
        else:
            if name == "created":
                raise SessionCorrupt(f"event {index}: a session is created once")
            if from_state != current:
                raise SessionCorrupt(f"event {index} ({name}) claims to leave {from_state} but the session is in {current}")
            if current in TERMINAL and name != "exported":
                # the terminal states accept nothing but the trace of an export, which moves nothing
                raise SessionCorrupt(f"event {index} ({name}) after terminal state {current}")
            destination = EVENT_STATES[name] or current
            if to_state != destination:
                raise SessionCorrupt(f"event {index} ({name}) claims to enter {to_state} but leads to {destination}")
            if name not in ("resumed", "exported"):
                try:
                    check_transition(current, destination)
                except InvalidTransition as error:
                    raise SessionCorrupt(f"event {index} ({name}): {error}") from None
            current = destination
        payload = event.get("payload") or {}
        at = float(event.get("recorded_at", 0.0) or 0.0)
        _apply(snapshot, name, payload, at)
        snapshot.state = current
        snapshot.events = index + 1
        snapshot.last_event_at = at
    return snapshot
