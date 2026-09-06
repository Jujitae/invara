"""Characterize, freeze, compare — the engine over a declared input domain.

``characterize`` runs the source over every declared input (the corpus,
and every member of a finite domain) and records the raw observations;
run it more than once and it also says which paths vary between runs and
what kind of policy would address each, as proposals it does not accept.

``freeze`` takes the first run of every input, normalizes it under the
manifest's accepted policies, refuses if any mandatory probe is left with
nothing to compare, and derives the baseline digest: one number that
binds the manifest digest to the digest of every raw baseline record. The
freeze itself is stored as evidence, and :meth:`Engine.integrity_problems`
recomputes it later so a baseline edited after the fact is a ``BLOCK``.

``compare_corpus`` runs the target over the corpus and compares each run
against the stored baseline record for the same input — never against a
fresh source run, because the baseline is the envelope that was frozen.
``differential`` is for inputs outside the envelope (a search, a proof):
it runs both systems fresh and compares.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import compare as comparison
from . import execute
from .claims import DIVERGED, PRESERVED_WITHIN_ENVELOPE, UNVERIFIABLE, ClaimResult
from .compare import Comparison, Divergence
from .evidence import Evidence
from .http_boundary import observation_problems
from .manifest import CorpusItem, Manifest, content_digest
from . import sensitivity as sensitivity_module
from .normalize import erases_signal, normalize, overbroad_tolerances, policy_set_digest, propose_policies, uncovered_volatile_empirical, unobtained_observable, volatile_paths
from .records import COMPARISON_VERSION, FREEZE_VERSION, NORMALIZED_VERSION

__all__ = [
    "preflight","BaselineCapture", "Engine", "EngineError", "Frozen", "StoredComparison"]


@dataclass(frozen=True)
class StoredComparison:
    """A comparison together with the content address the store gave its record.

    Evidence names the address, never ``Comparison.digest()``: the stored
    record adds its version and the digest of the manifest it was made under,
    so its address is not the bare comparison's. The comparison's outcome is
    readable here, so one object both says what was found and names the
    record that holds it; ``digest()`` is the address, which is what a proof
    or a search keeps per member and per run.
    """

    comparison: Comparison
    address: str | None

    @property
    def status(self) -> str:
        return self.comparison.status

    @property
    def equivalent(self) -> bool:
        return self.comparison.equivalent

    @property
    def mandatory_equivalent(self) -> bool:
        return self.comparison.mandatory_equivalent

    @property
    def divergences(self) -> tuple[Divergence, ...]:
        return self.comparison.divergences

    @property
    def problems(self) -> tuple[str, ...]:
        return self.comparison.problems

    @property
    def input_id(self) -> str:
        return self.comparison.input_id

    @property
    def extra(self) -> dict[str, Any]:
        return self.comparison.extra

    def digest(self) -> str | None:
        return self.address


class EngineError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class BaselineCapture:
    session_id: str
    manifest_digest: str
    inputs: list[str]
    runs: int
    raw_digests: dict[str, list[str]]
    volatile_paths: list[str]
    proposals: list[dict[str, Any]]
    uncovered_volatile: list[str]
    problems: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "manifest_digest": self.manifest_digest,
            "inputs": list(self.inputs),
            "runs": self.runs,
            "raw_digests": {k: list(v) for k, v in self.raw_digests.items()},
            "volatile_paths": list(self.volatile_paths),
            "proposals": [dict(p) for p in self.proposals],
            "uncovered_volatile": list(self.uncovered_volatile),
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class Frozen:
    session_id: str
    manifest_digest: str
    baseline_digest: str
    source_system_id: str
    inputs: list[str]
    record_digests: dict[str, str]
    policy_set_digest: str
    frozen_at: float
    ambiguities: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_version": FREEZE_VERSION,
            "session_id": self.session_id,
            "manifest_digest": self.manifest_digest,
            "baseline_digest": self.baseline_digest,
            "source_system_id": self.source_system_id,
            "inputs": list(self.inputs),
            "record_digests": dict(self.record_digests),
            "policy_set_digest": self.policy_set_digest,
            "frozen_at": self.frozen_at,
            "ambiguities": list(self.ambiguities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Frozen":
        return cls(
            session_id=data["session_id"],
            manifest_digest=data["manifest_digest"],
            baseline_digest=data["baseline_digest"],
            source_system_id=data["source_system_id"],
            inputs=list(data["inputs"]),
            record_digests=dict(data["record_digests"]),
            policy_set_digest=data["policy_set_digest"],
            frozen_at=float(data["frozen_at"]),
            ambiguities=list(data.get("ambiguities", [])),
        )


def _context(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"workspace": record.get("workspace"), "root": record.get("root")}


def _id_maps_record(id_maps: Mapping[str, Mapping[Any, str]]) -> dict[str, list[list[Any]]]:
    """Identity maps as evidence: ``[raw, placeholder]`` pairs, so a raw ``1`` and a raw ``"1"`` stay two entries."""

    return {group: [[raw, placeholder] for raw, placeholder in sorted(mapping.items(), key=lambda pair: pair[1])] for group, mapping in id_maps.items()}


def _observed_nothing(value: Any) -> bool:
    return isinstance(value, dict) and any(value.get(flag) is True for flag in ("missing", "root_missing", "root_link"))


def baseline_digest_of(manifest_digest: str, record_digests: dict[str, str]) -> str:
    return content_digest({"manifest": manifest_digest, "records": dict(sorted(record_digests.items()))})


def _claim_runs(manifest: Manifest, kind: str, default: int) -> int:
    """The runs a claim of ``kind`` declares in its params, or ``default``: what the phase will actually use."""

    claim = next((c for c in manifest.claims if c.kind == kind), None)
    if claim is None:
        return int(default)
    return int(claim.params.get("runs", default))


def preflight(manifest: Manifest, *, phase: str, runs: int | None = None) -> dict[str, Any]:
    """How many executions a phase would start, judged against ``budgets.max_planned_runs``, before any of them starts.

    Counted, never materialised: a finite domain contributes its cardinality
    as a number. Each search run and each shrink step executes both systems
    (a differential), and a performance measurement runs both systems on
    every corpus input for every run; ``runs`` is the caller's explicit
    override where a phase takes one, otherwise the claim's own params and
    then the budgets decide, exactly as the phase itself does. ``within`` is
    the only thing callers act on; the rest is the explanation they print.
    """

    budgets = manifest.budgets
    corpus = len(manifest.input_domain.corpus)
    finite = manifest.input_domain.finite
    members = finite.cardinality if finite is not None else 0
    members_in_budget = members if finite is not None and members <= budgets.finite_max_members else 0
    kinds = {claim.kind for claim in manifest.claims}
    search_runs = runs if runs is not None else _claim_runs(manifest, "counterexample_search", budgets.search_runs)
    performance_runs = runs if runs is not None else _claim_runs(manifest, "performance_envelope", manifest.performance.runs)
    parts: dict[str, int] = {}
    if phase == "characterize":
        parts["baseline"] = (corpus + members_in_budget) * max(1, int(runs or 1))
    elif phase == "compare":
        parts["compare"] = corpus
    elif phase == "search":
        parts["search"] = (max(0, int(search_runs)) + int(budgets.shrink_steps)) * 2
    elif phase == "prove":
        parts["prove"] = members_in_budget
    elif phase == "performance":
        parts["performance"] = corpus * 2 * max(1, int(performance_runs))
    elif phase == "unit_verify":
        if "corpus_equivalence" in kinds:
            parts["compare"] = corpus
        if "counterexample_search" in kinds:
            parts["search"] = (max(0, int(_claim_runs(manifest, "counterexample_search", budgets.search_runs))) + int(budgets.shrink_steps)) * 2
        if "finite_domain_proof" in kinds:
            parts["prove"] = members_in_budget
        if "performance_envelope" in kinds:
            parts["performance"] = corpus * 2 * max(1, int(_claim_runs(manifest, "performance_envelope", manifest.performance.runs)))
    else:
        raise ValueError(f"unknown phase {phase!r}")
    planned = sum(parts.values())
    budget = int(budgets.max_planned_runs)
    note = ""
    if "prove" in parts and finite is not None and members > budgets.finite_max_members:
        note = f"; the finite domain ({members} members) exceeds budgets.finite_max_members ({budgets.finite_max_members}): the proof executes nothing and is unverifiable"
    return {
        "phase": phase,
        "planned_runs": planned,
        "parts": parts,
        "budget": budget,
        "within": planned <= budget,
        "detail": f"{phase} plans {planned} execution(s) ({', '.join(f'{k} {v}' for k, v in parts.items()) or 'nothing'}); budgets.max_planned_runs is {budget}{note}",
    }


def declared_inputs(manifest: Manifest) -> list[CorpusItem]:
    """The corpus plus, for a finite domain within budget, every member."""

    items = list(manifest.input_domain.corpus)
    finite = manifest.input_domain.finite
    if finite is not None and finite.cardinality <= manifest.budgets.finite_max_members:
        seen = {item.id for item in items}
        items.extend(member for member in finite.members() if member.id not in seen)
    return items


class Engine:
    def __init__(
        self,
        evidence: Evidence,
        *,
        workspace_parent: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        seed: int = 0,
    ) -> None:
        self.evidence = evidence
        self.workspace_parent = Path(workspace_parent) if workspace_parent is not None else None
        self.clock = clock
        self.seed = seed

    # ------------------------------------------------------------------ helpers

    def _run(self, system: Any, item: CorpusItem, manifest: Manifest, roots: dict[str, str]) -> dict[str, Any]:
        return execute.run(
            execute.RunSpec(system=system, item=item, manifest=manifest, roots=roots),
            workspace_parent=self.workspace_parent,
            seed=self.seed,
        )

    def _store_raw(self, session_id: str, key: str, record: dict[str, Any]) -> str:
        return self.evidence.record_observation(session_id, "raw", key, record, at=self.clock())

    def _next_round(self, session_id: str, kind: str, prefix: str) -> int:
        return len(self.evidence.observations(session_id, kind=kind, prefix=prefix)) + 1

    # ------------------------------------------------------------- characterize

    def characterize(self, session_id: str, manifest: Manifest, roots: dict[str, str], *, runs: int = 1) -> BaselineCapture:
        if runs < 1:
            raise EngineError("bad_runs", "at least one run is needed to capture a baseline")
        source = manifest.source_system
        inputs = declared_inputs(manifest)
        raw_digests: dict[str, list[str]] = {}
        problems: list[str] = []
        observed_runs: dict[str, list[dict[str, Any]]] = {}
        for item in inputs:
            raw_digests[item.id] = []
            observed_runs[item.id] = []
            for run in range(1, runs + 1):
                key = f"baseline:{source.id}:{item.id}:{run}"
                stored = self.evidence.observation(session_id, "raw", key)
                if stored is not None:
                    # an interrupted capture left this run behind; evidence is
                    # write-once, so the stored run is the run
                    record = stored["record"]
                    raw_digests[item.id].append(stored["digest"])
                else:
                    record = self._run(source, item, manifest, roots)
                    raw_digests[item.id].append(self._store_raw(session_id, key, record))
                if record["status"] == "observed":
                    observed_runs[item.id].append(record)
                elif run == 1:
                    problems.append(f"{item.id}: {record['status']}")
        volatile: set[str] = set()
        uncovered: set[str] = set()
        proposals: dict[str, dict[str, Any]] = {}
        if runs > 1:
            for item_id, records in observed_runs.items():
                if len(records) < 2:
                    continue
                probes = [record["probes"] for record in records]
                volatile.update(volatile_paths(probes))
                uncovered.update(uncovered_volatile_empirical(probes, manifest.policies, [_context(record) for record in records]))
                for proposal in propose_policies(probes):
                    proposals.setdefault(proposal["path"], proposal)
        paths = sorted(volatile)
        numbered = []
        for number, path in enumerate(paths, start=1):
            proposal = dict(proposals[path])
            proposal["id"] = f"inferred-{number}"
            numbered.append(proposal)
        return BaselineCapture(
            session_id=session_id,
            manifest_digest=manifest.digest(),
            inputs=[item.id for item in inputs],
            runs=runs,
            raw_digests=raw_digests,
            volatile_paths=paths,
            proposals=numbered,
            uncovered_volatile=sorted(uncovered),
            problems=problems,
        )

    def _baseline_runs(self, session_id: str, manifest: Manifest) -> dict[str, list[dict[str, Any]]]:
        """Every observed baseline run per input, in run order."""

        prefix = f"baseline:{manifest.source_system.id}:"
        by_input: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for row in self.evidence.observations(session_id, kind="raw", prefix=prefix):
            head, _, run = row["run_key"][len(prefix):].rpartition(":")
            if not run.isdigit() or row["record"].get("status") != "observed":
                continue
            by_input.setdefault(head, []).append((int(run), row["record"]))
        return {input_id: [record for _, record in sorted(runs, key=lambda pair: pair[0])] for input_id, runs in by_input.items()}

    def uncovered_volatile(self, session_id: str, manifest: Manifest) -> list[str]:
        """Volatile baseline paths that the manifest's accepted policies do not actually absorb."""

        uncovered: set[str] = set()
        for records in self._baseline_runs(session_id, manifest).values():
            if len(records) < 2:
                continue
            uncovered.update(uncovered_volatile_empirical([r["probes"] for r in records], manifest.policies, [_context(r) for r in records]))
        return sorted(uncovered)

    def sensitivity(self, session_id: str, manifest: Manifest) -> dict[str, Any]:
        """Scan the frozen baseline under ``manifest`` and store the result once per manifest digest."""

        existing = self.sensitivity_record(session_id, manifest)
        if existing is not None:
            # the scan is a function of the frozen baseline and the manifest: an interrupted freeze does not pay for it twice
            return existing
        records = [row["record"] for row in self._baseline_rows(session_id, manifest).values() if row["record"].get("status") == "observed"]
        result = sensitivity_module.scan(manifest, records)
        self.evidence.record_observation(session_id, "sensitivity", f"sensitivity:{manifest.digest()[:16]}", result, at=self.clock())
        return result

    def sensitivity_record(self, session_id: str, manifest: Manifest) -> dict[str, Any] | None:
        """The stored scan for this manifest, or None when none was run."""

        row = self.evidence.observation(session_id, "sensitivity", f"sensitivity:{manifest.digest()[:16]}")
        return dict(row["record"]) if row is not None else None

    def check_probes(self, session_id: str, manifest: Manifest) -> None:
        """A mandatory probe that observed nothing usable for any baseline input is aimed at nothing.

        Per input, both sides writing nothing is agreement and compares as
        such; a probe that never observes anything across the whole corpus
        would make "missing equals missing" the entire claim, which is a
        misconfigured path, not preserved behaviour. A probe whose declared
        observable was never obtained on any input (a JSON probe that only
        ever recorded a parse failure) is aimed at a program that never
        produced it: the same refusal, before anything is compared.
        """

        rows = [row["record"] for row in self._baseline_rows(session_id, manifest).values() if row["record"].get("status") == "observed"]
        if not rows:
            return
        for probe in manifest.probes:
            if not probe.mandatory:
                continue
            observed = [record["probes"].get(probe.id) for record in rows]
            if all(_observed_nothing(record) for record in observed):
                raise EngineError(
                    "probe_unobserved",
                    f"mandatory probe {probe.id!r} observed nothing for any of the {len(rows)} baseline input(s); check its path before freezing",
                )
            if all(_observed_nothing(record) or unobtained_observable(probe, record) for record in observed):
                raise EngineError(
                    "probe_unobserved",
                    f"mandatory probe {probe.id!r} never obtained its declared value on any of the {len(rows)} baseline input(s) (the program produced no parseable output where the probe looks); check the command and the probe before freezing",
                )

    def check_policies(self, session_id: str, manifest: Manifest) -> None:
        """Refuse a policy set that would erase the baseline's signal or drown its values."""

        policies = manifest.accepted_policies()
        raw_runs: list[Any] = []
        for input_id, row in self._baseline_rows(session_id, manifest).items():
            record = row["record"]
            if record.get("status") != "observed":
                continue
            raw_runs.append(record["probes"])
            normalized = normalize(record["probes"], policies, _context(record))
            problems = erases_signal(normalized.value, manifest.probes, raw=record["probes"])
            if problems:
                raise EngineError("normalization_erases_signal", f"{input_id}: " + "; ".join(problems))
        problems = overbroad_tolerances(raw_runs, policies)
        if problems:
            raise EngineError("overbroad_tolerance", "; ".join(problems))

    # ------------------------------------------------------------------- freeze

    def _baseline_rows(self, session_id: str, manifest: Manifest) -> dict[str, dict[str, Any]]:
        prefix = f"baseline:{manifest.source_system.id}:"
        rows = self.evidence.observations(session_id, kind="raw", prefix=prefix)
        by_input: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["run_key"].endswith(":1"):
                input_id = row["run_key"][len(prefix):-2]
                by_input[input_id] = row
        return by_input

    def freeze(self, session_id: str, manifest: Manifest) -> Frozen:
        rows = self._baseline_rows(session_id, manifest)
        if not rows:
            raise EngineError("no_baseline", f"no baseline observations recorded for {session_id}")
        unobserved = [f"{input_id}: {row['record']['status']}" for input_id, row in rows.items() if row["record"]["status"] != "observed"]
        if unobserved:
            raise EngineError("baseline_unobserved", "; ".join(unobserved[:5]))
        endpoint_problems = [problem for row in rows.values() for problem in observation_problems(row["record"], manifest)]
        if endpoint_problems:
            raise EngineError("http_endpoint_identity", "; ".join(endpoint_problems[:5]))
        policies = manifest.accepted_policies()
        self.check_probes(session_id, manifest)
        self.check_policies(session_id, manifest)
        digest_of_policies = policy_set_digest(policies)
        record_digests: dict[str, str] = {}
        ambiguities: list[str] = []
        for input_id, row in rows.items():
            record = row["record"]
            normalized = normalize(record["probes"], policies, _context(record))
            ambiguities.extend(f"{input_id}: {note}" for note in normalized.ambiguities)
            self.evidence.record_observation(
                session_id,
                "normalized",
                f"{row['run_key']}:{digest_of_policies[:16]}",
                {
                    "record_version": NORMALIZED_VERSION,
                    "raw_digest": row["digest"],
                    "policy_set_digest": digest_of_policies,
                    "probes": normalized.value,
                    "actions": [action.as_dict() for action in normalized.actions[: int(manifest.budgets.max_divergences)]],
                    "actions_omitted": max(0, len(normalized.actions) - int(manifest.budgets.max_divergences)),
                    "id_maps": _id_maps_record(normalized.id_maps),
                    "ambiguities": list(normalized.ambiguities),
                },
                at=self.clock(),
            )
            record_digests[input_id] = row["digest"]
        frozen = Frozen(
            session_id=session_id,
            manifest_digest=manifest.digest(),
            baseline_digest=baseline_digest_of(manifest.digest(), record_digests),
            source_system_id=manifest.source_system.id,
            inputs=sorted(record_digests),
            record_digests=record_digests,
            policy_set_digest=digest_of_policies,
            frozen_at=self.clock(),
            ambiguities=ambiguities,
        )
        round_number = self._next_round(session_id, "freeze", "freeze:")
        self.evidence.record_observation(session_id, "freeze", f"freeze:{round_number}", frozen.as_dict(), at=self.clock())
        return frozen

    def frozen(self, session_id: str) -> Frozen | None:
        rows = self.evidence.observations(session_id, kind="freeze", prefix="freeze:")
        if not rows:
            return None
        return Frozen.from_dict(rows[-1]["record"])

    def baseline_digest(self, session_id: str) -> str | None:
        frozen = self.frozen(session_id)
        return frozen.baseline_digest if frozen else None

    def integrity_problems(self, session_id: str, frozen: Frozen) -> list[str]:
        """Everything that no longer matches what was frozen."""

        problems: list[str] = []
        recomputed: dict[str, str] = {}
        for input_id, expected in frozen.record_digests.items():
            row = self.evidence.observation(session_id, "raw", f"baseline:{frozen.source_system_id}:{input_id}:1")
            if row is None:
                problems.append(f"baseline record missing: {input_id}")
                continue
            actual = content_digest(row["record"])
            if actual != row["digest"]:
                problems.append(f"baseline record tampered: {input_id} (content no longer matches its address)")
            if actual != expected:
                problems.append(f"baseline record differs from the frozen digest: {input_id}")
            recomputed[input_id] = actual
        if recomputed and baseline_digest_of(frozen.manifest_digest, recomputed) != frozen.baseline_digest:
            problems.append("baseline digest mismatch: the frozen envelope no longer hashes to what was recorded")
        problems.extend(self.evidence.verify()["problems"])
        return problems

    # ------------------------------------------------------------------ compare

    def _store_comparison(self, session_id: str, key: str, result: comparison.Comparison, *, manifest_digest: str) -> str:
        """Store a comparison with the digest of the manifest it was made under: exclusions and budgets shape it, not only the policy set."""

        record = result.as_dict()
        record["record_version"] = COMPARISON_VERSION
        record["manifest_digest"] = manifest_digest
        return self.evidence.record_observation(session_id, "comparison", key, record, at=self.clock())

    def _frozen_baseline(self, session_id: str, frozen: Frozen, item: CorpusItem) -> dict[str, Any] | None:
        """The frozen baseline record for ``item``, only when it is an execution of this exact item.

        The record is found by id and believed by identity: its digest must be
        the frozen one and its ``input_digest`` must be the item's own, so a
        record captured from another input under the same id is never reused
        for this one. Anything else is no baseline, and the caller runs the
        source or says the input could not be compared.
        """

        expected = frozen.record_digests.get(item.id)
        if expected is None:
            return None
        row = self.evidence.observation(session_id, "raw", f"baseline:{frozen.source_system_id}:{item.id}:1")
        if row is None or row["digest"] != expected or row["record"].get("input_digest") != item.identity():
            return None
        return row["record"]

    def records_by_digest(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Every record of the session by its content address: what a proof is checked against before it is recorded."""

        return {row["digest"]: row["record"] for row in self.evidence.observations(session_id)}

    def compare_corpus(self, session_id: str, manifest: Manifest, frozen: Frozen, roots: dict[str, str]) -> ClaimResult:
        requirement = next((claim for claim in manifest.claims if claim.kind == "corpus_equivalence"), None)
        if requirement is None:
            raise EngineError("no_corpus_claim", "the manifest declares no corpus_equivalence claim")
        target = manifest.target_system
        divergences: list[dict[str, Any]] = []
        informational: list[dict[str, Any]] = []
        unverified: list[str] = []
        evidence_digests: list[str] = []
        source_wall: list[float] = []
        target_wall: list[float] = []
        compared = 0
        per_comparison_omitted = 0
        for item in manifest.input_domain.corpus:
            baseline = self._frozen_baseline(session_id, frozen, item)
            if baseline is None:
                unverified.append(f"{item.id}: frozen baseline record missing, altered, or not an execution of this input")
                continue
            round_number = self._next_round(session_id, "raw", f"compare:{target.id}:{item.id}:")
            record = self._run(target, item, manifest, roots)
            self._store_raw(session_id, f"compare:{target.id}:{item.id}:{round_number}", record)
            result = comparison.compare(baseline, record, manifest)
            evidence_digests.append(self._store_comparison(session_id, f"compare:{item.id}:{round_number}", result, manifest_digest=manifest.digest()))
            if result.status != "compared":
                unverified.append(f"{item.id}: " + "; ".join(result.problems))
                continue
            compared += 1
            per_comparison_omitted += int(result.extra.get("divergences_omitted", 0))
            source_wall.append(float(baseline.get("timing", {}).get("wall_s", 0.0)))
            target_wall.append(float(record.get("timing", {}).get("wall_s", 0.0)))
            for divergence in result.divergences:
                entry = dict(divergence.as_dict(), input_id=item.id)
                (divergences if divergence.mandatory else informational).append(entry)
        # the claim keeps a bounded number of divergences too; the count of
        # what it does not keep is part of the evidence
        limit = int(manifest.budgets.max_divergences)
        omitted = per_comparison_omitted + max(0, len(divergences) - limit) + max(0, len(informational) - limit)
        divergences = divergences[:limit]
        informational = informational[:limit]
        coverage: dict[str, Any] = {
            "kind": "corpus",
            "members": len(manifest.input_domain.corpus),
            "compared": compared,
            "divergences_omitted": omitted,
            "timing": {
                "source_wall_s": sum(source_wall),
                "target_wall_s": sum(target_wall),
                "runs": compared,
            },
        }
        if informational:
            coverage["informational_divergences"] = informational
        common = dict(
            claim_id=requirement.id,
            kind=requirement.kind,
            mandatory=requirement.mandatory,
            manifest_digest=manifest.digest(),
            baseline_digest=frozen.baseline_digest,
            coverage=coverage,
            evidence_digests=tuple(evidence_digests),
        )
        if divergences:
            return ClaimResult(status=DIVERGED, divergences=tuple(divergences), unverified=tuple(unverified), detail=f"{len(divergences)} mandatory divergence(s) over {compared} compared input(s)", **common)
        if unverified:
            return ClaimResult(status=UNVERIFIABLE, unverified=tuple(unverified), detail=f"{len(unverified)} input(s) could not be compared", **common)
        return ClaimResult(status=PRESERVED_WITHIN_ENVELOPE, detail=f"{compared} corpus input(s) compared equivalent under the declared policies", **common)

    def measure_performance(
        self,
        session_id: str,
        manifest: Manifest,
        frozen: Frozen,
        roots: dict[str, str],
        *,
        runs: int,
        rel_tolerance: float,
        abs_tolerance_s: float,
    ) -> ClaimResult:
        """Wall-clock over the corpus under a declared protocol: both systems, fresh, interleaved, medians.

        The baseline's stored timings are not reused: a performance claim
        needs both sides measured on the same machine in the same minute.
        """

        import platform
        import statistics
        import sys

        requirement = next((claim for claim in manifest.claims if claim.kind == "performance_envelope"), None)
        if requirement is None:
            raise EngineError("no_performance_claim", "the manifest declares no performance_envelope claim")
        if runs < 1:
            raise EngineError("bad_runs", "a performance protocol needs at least one run")
        source, target = manifest.source_system, manifest.target_system
        per_input: dict[str, dict[str, Any]] = {}
        unverified: list[str] = []
        for item in manifest.input_domain.corpus:
            source_times: list[float] = []
            target_times: list[float] = []
            for run in range(1, runs + 1):
                for system, times in ((source, source_times), (target, target_times)):
                    number = self._next_round(session_id, "raw", f"perf:{system.id}:{item.id}:")
                    record = self._run(system, item, manifest, roots)
                    self._store_raw(session_id, f"perf:{system.id}:{item.id}:{number}", record)
                    if record["status"] != "observed":
                        unverified.append(f"{item.id}: {system.id} run {run} {record['status']}")
                    else:
                        times.append(float(record.get("timing", {}).get("wall_s", 0.0)))
            if source_times and target_times:
                per_input[item.id] = {
                    "runs": runs,
                    "source_wall_s": statistics.median(source_times),
                    "target_wall_s": statistics.median(target_times),
                    "source_samples": source_times,
                    "target_samples": target_times,
                }
        source_total = sum(entry["source_wall_s"] for entry in per_input.values())
        target_total = sum(entry["target_wall_s"] for entry in per_input.values())
        bound = source_total * (1.0 + rel_tolerance) + abs_tolerance_s
        coverage = {
            "kind": "performance",
            "protocol": {
                "runs": runs,
                "statistic": "median",
                "rel_tolerance": rel_tolerance,
                "abs_tolerance_s": abs_tolerance_s,
                "interleaved": True,
                "platform": platform.platform(),
                "python": sys.version.split()[0],
            },
            "inputs": len(manifest.input_domain.corpus),
            "per_input": per_input,
            "source_wall_s": source_total,
            "target_wall_s": target_total,
            "bound_s": bound,
        }
        common = dict(
            claim_id=requirement.id,
            kind=requirement.kind,
            mandatory=requirement.mandatory,
            manifest_digest=manifest.digest(),
            baseline_digest=frozen.baseline_digest,
            coverage=coverage,
        )
        if unverified:
            return ClaimResult(status=UNVERIFIABLE, unverified=tuple(unverified), detail="a timed run did not observe; the protocol was not completed", **common)
        if target_total > bound:
            divergence = {
                "path": "/timing/wall_s",
                "raw_source": source_total,
                "raw_target": target_total,
                "normalized_source": source_total,
                "normalized_target": target_total,
                "policy_id": "performance",
                "policy_kind": "performance_envelope",
                "why": f"exceeds tolerance rel {rel_tolerance} / abs {abs_tolerance_s}s (bound {bound:.6g}s)",
                "mandatory": requirement.mandatory,
            }
            return ClaimResult(status=DIVERGED, divergences=(divergence,), detail=f"target {target_total:.4g}s exceeds bound {bound:.4g}s derived from source {source_total:.4g}s", **common)
        return ClaimResult(status=PRESERVED_WITHIN_ENVELOPE, detail=f"target {target_total:.4g}s within bound {bound:.4g}s derived from source {source_total:.4g}s under {runs} run(s) per input", **common)

    def compare_against_baseline(
        self, session_id: str, manifest: Manifest, frozen: Frozen, item: CorpusItem, roots: dict[str, str], *, phase: str
    ) -> StoredComparison:
        """One input against its own frozen baseline record, or against nothing: the source is never run again after the freeze.

        The baseline is used only when it is an execution of this exact item
        (input and initial state, by digest): a record that merely carries
        the item's id is not its baseline. An item the frozen baseline holds
        no record for cannot be compared at all: a fresh run of SOURCE_ROOT
        would be an execution of whatever the directory holds at proof time,
        not evidence from the baseline the claim is stamped with, so nothing
        runs and the outcome is unverifiable (nothing is stored either: there
        is no comparison to keep).
        """

        source, target = manifest.source_system, manifest.target_system
        baseline = self._frozen_baseline(session_id, frozen, item)
        if baseline is None:
            unverifiable = comparison.Comparison(
                status="unverifiable",
                equivalent=False,
                mandatory_equivalent=False,
                divergences=(),
                compared_leaves=0,
                tolerance_applications=(),
                problems=(
                    "no frozen baseline record for this input: the frozen baseline does not cover it (captured under a smaller "
                    "finite_max_members, or the record is missing or altered); the source is not run again after the freeze, so a "
                    "proof over it needs a session whose capture covered the domain",
                ),
                source_raw_digest="",
                target_raw_digest="",
                source_normalized_digest="",
                target_normalized_digest="",
                policy_set_digest=policy_set_digest(manifest.accepted_policies()),
                input_id=item.id,
            )
            return StoredComparison(unverifiable, None)
        round_number = self._next_round(session_id, "raw", f"{phase}:{target.id}:{item.id}:")
        source_record = baseline
        target_record = self._run(target, item, manifest, roots)
        self._store_raw(session_id, f"{phase}:{target.id}:{item.id}:{round_number}", target_record)
        result = comparison.compare(source_record, target_record, manifest)
        address = self._store_comparison(session_id, f"{phase}:{item.id}:{round_number}", result, manifest_digest=manifest.digest())
        return StoredComparison(result, address)

    # ------------------------------------------------------------- differential

    def differential(self, session_id: str, manifest: Manifest, item: CorpusItem, roots: dict[str, str], *, phase: str) -> StoredComparison:
        """Run both systems on one input outside the frozen envelope and compare."""

        source, target = manifest.source_system, manifest.target_system
        round_number = self._next_round(session_id, "raw", f"{phase}:{source.id}:{item.id}:")
        source_record = self._run(source, item, manifest, roots)
        self._store_raw(session_id, f"{phase}:{source.id}:{item.id}:{round_number}", source_record)
        target_record = self._run(target, item, manifest, roots)
        self._store_raw(session_id, f"{phase}:{target.id}:{item.id}:{round_number}", target_record)
        result = comparison.compare(source_record, target_record, manifest)
        address = self._store_comparison(session_id, f"{phase}:{item.id}:{round_number}", result, manifest_digest=manifest.digest())
        return StoredComparison(result, address)
