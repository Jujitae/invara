"""Reviewed intent mappings above the unchanged deterministic verifier.

A matching confirmation is a local submission, not authenticated human identity.
This adapter does not infer whether a proposal exhausts the original request.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import uuid

from . import store
from .contract import Constraint, Predicate, VerificationContract, seal as core_seal
from .runner import DEFAULT_TIMEOUT_S, digest_paths, observe
from .verdict import Observation, judge as core_judge

PROPOSAL_SCHEMA = "invara.intent-proposal/1"
REVIEW_SCHEMA = "invara.intent-review/1"
CONFIRMATION_SCHEMA = "invara.intent-confirmation/1"
POLICY_FILES = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
                "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")


class IntentError(ValueError):
    """An adapter refusal; never relabelled as a core verdict."""


def _canonical(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IntentError("invalid JSON value") from exc


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntentError(f"cannot read adapter evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise IntentError("expected JSON object")
    return value


def _write_new(path: Path, value: dict) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical(value) + "\n")
    except FileExistsError as exc:
        raise IntentError(f"evidence already exists: {path.name}") from exc


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentError(f"{label} must be nonempty text")
    return value


def _strings(value, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise IntentError(f"{label} must be a list of strings")
    if len(set(value)) != len(value):
        raise IntentError(f"duplicate {label}")
    return value


def _root(root: Path) -> Path:
    value = Path(root).resolve()
    if not value.is_dir():
        raise IntentError("root is not an existing directory")
    return value


def _root_identity(root: Path) -> dict:
    st = root.stat()
    return {"path": os.path.normcase(str(root)), "device": st.st_dev, "inode": st.st_ino}


def _path(root: Path, name: str) -> Path:
    _text(name, "path")
    pure = PurePosixPath(name)
    if (pure.is_absolute() or PureWindowsPath(name).drive or "\\" in name
            or any(p in (".", "..") for p in name.split("/")) or pure.as_posix() != name):
        raise IntentError(f"path must be exact root-relative spelling: {name}")
    target = root / name
    try:
        target.resolve().relative_to(root)
    except ValueError as exc:
        raise IntentError(f"path leaves root: {name}") from exc
    if target.is_symlink():
        raise IntentError(f"guarded symlink unsupported: {name}")
    return target


def _snapshot(root: Path, check_files: list[str], *, require: bool = False) -> dict:
    files, directories = {}, {}
    for name in check_files:
        target = _path(root, name)
        if target.is_dir():
            names = []
            for child in sorted(target.rglob("*")):
                relative = child.relative_to(root).as_posix()
                _path(root, relative)
                if child.is_file():
                    names.append(relative)
                    files[relative] = hashlib.sha256(child.read_bytes()).hexdigest()
            directories[name] = names
        elif target.is_file():
            files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        elif require:
            raise IntentError(f"check path does not exist: {name}")
        else:
            files[name] = None
    for name in POLICY_FILES:
        target = _path(root, name)
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return {"files": files, "directories": directories}


def _drift(expected: dict, actual: dict) -> list[dict]:
    changes = []
    for group in ("files", "directories"):
        before, after = expected[group], actual[group]
        for path in sorted(set(before) | set(after)):
            if before.get(path) != after.get(path):
                changes.append({"path": path, "kind": group, "expected": before.get(path), "actual": after.get(path)})
    return changes


def _contract(spec: dict, root: Path, *, sealed_at: float) -> VerificationContract:
    try:
        constraints = []
        for raw in spec["constraints"]:
            paths = _strings(raw["paths"], "protected paths")
            for name in paths:
                if not _path(root, name).is_file():
                    raise IntentError(f"protected file missing: {name}")
            constraints.append(Constraint(raw["kind"], tuple(paths), raw.get("reason", ""), digest_paths(paths, root)))
        predicates = [Predicate.from_dict(raw) for raw in spec["done_when"]]
        return core_seal(task_id=spec["task_id"], intent=spec["intent"], constraints=constraints,
                         done_when=predicates, sealed_at=sealed_at)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, IntentError):
            raise
        raise IntentError(f"invalid underlying contract: {exc}") from exc


def _validate(proposal: dict, root: Path) -> tuple[dict, dict]:
    if not isinstance(proposal, dict) or proposal.get("schema") != PROPOSAL_SCHEMA:
        raise IntentError("unknown proposal schema")
    allowed = {"schema", "task_id", "original_request", "promises", "suggestions", "contract", "check_files"}
    if set(proposal) != allowed:
        raise IntentError("proposal fields differ from schema")
    _text(proposal["original_request"], "original_request")
    _text(proposal["task_id"], "task_id")
    spec = copy.deepcopy(proposal["contract"])
    if not isinstance(spec, dict) or spec.get("task_id") != proposal["task_id"]:
        raise IntentError("proposal and contract task_id mismatch")
    if set(spec) != {"task_id", "intent", "constraints", "done_when"}:
        raise IntentError("contract must be an unsealed task specification")
    contract = _contract(spec, root, sealed_at=0)
    predicates = {p.id: p for p in contract.done_when}
    protected = {path for c in contract.constraints for path in c.paths}
    checks = _strings(proposal["check_files"], "check_files")
    if not checks:
        raise IntentError("explicit check_files scope is required")
    snapshot = _snapshot(root, checks, require=True)
    existing_guards = sorted(k for k, v in snapshot["files"].items() if v is not None)
    if not existing_guards:
        raise IntentError("check scope contains no files")
    # Local script arguments must be frozen; indirect dependencies remain an
    # explicit scope limit, not an inferred comprehensive dependency graph.
    for predicate in contract.done_when:
        for argument in predicate.command:
            candidate = Path(argument)
            if candidate.is_absolute():
                try:
                    name = candidate.resolve().relative_to(root).as_posix()
                except ValueError:
                    continue
            else:
                name = argument
            if (root / name).is_file() and name not in existing_guards:
                raise IntentError(f"local command file is outside check_files: {name}")
    promises = proposal["promises"]
    if not isinstance(promises, list) or not promises:
        raise IntentError("at least one explicit promise is required")
    ids = []
    for promise in promises:
        if not isinstance(promise, dict) or set(promise) != {"id", "text", "kind", "origin", "mapping"}:
            raise IntentError("invalid promise fields")
        ids.append(_text(promise["id"], "promise id"))
        _text(promise["text"], "promise text")
        if promise["kind"] not in ("change", "keep") or promise["origin"] not in ("user", "agent"):
            raise IntentError("invalid promise kind or origin")
        mapping = promise["mapping"]
        if not isinstance(mapping, dict) or set(mapping) - {"kind", "predicate_ids", "protected_paths", "reason"}:
            raise IntentError("invalid mapping fields")
        kind = mapping.get("kind")
        pids = _strings(mapping.get("predicate_ids"), "predicate_ids")
        paths = _strings(mapping.get("protected_paths"), "protected_paths")
        if kind == "machine":
            if not pids and not paths:
                raise IntentError("machine mapping has no evidence")
            if any(pid not in predicates or predicates[pid].human for pid in pids):
                raise IntentError("machine mapping must reference actual machine predicates")
            if not set(paths) <= protected:
                raise IntentError("mapped path is not protected by the contract")
        elif kind in ("human", "unmapped"):
            _text(mapping.get("reason"), "unmapped or human reason")
            if pids or paths:
                raise IntentError("human/unmapped evidence is not machine-checked")
        else:
            raise IntentError("unknown mapping kind")
    if len(set(ids)) != len(ids):
        raise IntentError("duplicate promise id")
    suggestions = proposal["suggestions"]
    if not isinstance(suggestions, list):
        raise IntentError("suggestions must be separate list")
    suggestion_ids = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict) or set(suggestion) != {"id", "text"}:
            raise IntentError("invalid optional suggestion")
        suggestion_ids.append(_text(suggestion["id"], "suggestion id"))
        _text(suggestion["text"], "suggestion text")
    if len(set(suggestion_ids)) != len(suggestion_ids) or set(ids) & set(suggestion_ids):
        raise IntentError("duplicate suggestion/promise id")
    spec["constraints"].append({"kind": "paths_unchanged", "paths": existing_guards,
                                "reason": "Reviewed check and policy bytes must remain unchanged"})
    _contract(spec, root, sealed_at=0)
    return spec, snapshot


def _confirmation_template(review: dict) -> dict:
    return {"schema": CONFIRMATION_SCHEMA, **{k: review[k] for k in (
        "review_id", "proposal_digest", "contract_digest", "root_digest", "review_digest")},
        "decision": None, "accepted_promise_ids": [], "reviewed_at": None}


def prepare(proposal: dict, *, root: Path, output: Path) -> dict:
    """Freeze a reviewable proposal and real file baselines; never execute checks."""
    root = _root(root)
    proposal = json.loads(_canonical(proposal))
    spec, snapshot = _validate(proposal, root)
    target = Path(output).resolve()
    if target.exists() and any(target.iterdir()):
        raise IntentError("review output is not empty; revise into a new review directory")
    for name in proposal["check_files"]:
        scope = _path(root, name)
        if scope.is_dir() and (target == scope or scope in target.parents):
            raise IntentError("review output cannot be inside guarded check scope")
    original = _contract(spec, root, sealed_at=0)
    review = {"schema": REVIEW_SCHEMA, "review_id": str(uuid.uuid4()),
        "task_id": proposal["task_id"], "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "root": _root_identity(root), "root_digest": _digest(_root_identity(root)),
        "proposal_digest": _digest(proposal), "contract_digest": _digest(spec),
        "original_request": proposal["original_request"], "promises": proposal["promises"],
        "suggestions": proposal["suggestions"], "contract": spec, "check_files": proposal["check_files"],
        "check_snapshot": snapshot,
        "protected_snapshot": {str(i): c.baseline for i,c in enumerate(original.constraints)},
        "limitations": ["This list is proposed coverage, not proof that the entire request was captured.",
            "Shared checks do not become independent evidence when mapped to several promises.",
            "New paths outside declared check directories are not automatically covered.",
            "Indirect test dependencies, installed runtimes and environment are not fully pinned.",
            "A matching local confirmation does not authenticate a human; same-user processes can create files.",
            "Commands run with user permissions; this adapter is not a sandbox."]}
    review["review_digest"] = _digest(review)
    review["confirmation_template"] = _confirmation_template(review)
    target.mkdir(parents=True, exist_ok=True)
    _write_new(target / "proposal.json", proposal)
    _write_new(target / "review.json", review)
    return review


def load_review(output: Path) -> dict:
    target = Path(output).resolve()
    proposal, review = _read(target / "proposal.json"), _read(target / "review.json")
    body = {k:v for k,v in review.items() if k not in ("review_digest", "confirmation_template")}
    if (review.get("schema") != REVIEW_SCHEMA or review.get("review_digest") != _digest(body)
            or review.get("proposal_digest") != _digest(proposal)
            or review.get("contract_digest") != _digest(review.get("contract"))
            or review.get("root_digest") != _digest(review.get("root"))):
        raise IntentError("frozen review digest mismatch")
    for field in ("original_request", "promises", "suggestions", "check_files", "task_id"):
        if review.get(field) != proposal.get(field):
            raise IntentError(f"frozen proposal mismatch: {field}")
    if review.get("confirmation_template") != _confirmation_template(review):
        raise IntentError("edited confirmation template")
    return review


def _check_root(review: dict, root: Path) -> Path:
    root = _root(root)
    if _root_identity(root) != review["root"]:
        raise IntentError("root identity mismatch")
    return root


def _confirm(review: dict, confirmation) -> dict:
    value = _read(Path(confirmation)) if isinstance(confirmation, (Path, str)) else copy.deepcopy(confirmation)
    expected = _confirmation_template(review)
    if not isinstance(value, dict) or set(value) != set(expected):
        raise IntentError("confirmation fields mismatch")
    for key in expected:
        if key not in ("decision", "accepted_promise_ids", "reviewed_at") and value[key] != expected[key]:
            raise IntentError(f"confirmation identity mismatch: {key}")
    if value["decision"] != "confirm":
        raise IntentError("explicit confirmation is missing")
    ids = _strings(value["accepted_promise_ids"], "accepted_promise_ids")
    if set(ids) != {p["id"] for p in review["promises"]}:
        raise IntentError("confirmation must include exactly every listed promise")
    try:
        at = datetime.datetime.fromisoformat(value["reviewed_at"].replace("Z", "+00:00"))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise IntentError("confirmation reviewed_at must include timezone") from exc
    return value


def _database_identity(path: Path) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise IntentError("bound database is missing")
    stat = path.stat()
    return {"path": os.path.normcase(str(path)), "device": stat.st_dev, "inode": stat.st_ino}


def seal_review(output: Path, confirmation, *, root: Path, db: Path) -> dict:
    """Require matching local review submission, then call the existing seal/store."""
    target = Path(output).resolve()
    review = load_review(target)
    root = _check_root(review, root)
    submission = _confirm(review, confirmation)
    if (target / "sealed.json").exists():
        raise IntentError("review is already sealed")
    if _drift(review["check_snapshot"], _snapshot(root, review["check_files"])):
        raise IntentError("check drift since review; prepare a new review")
    contract = _contract(review["contract"], root, sealed_at=datetime.datetime.now(datetime.UTC).timestamp())
    if {str(i):c.baseline for i,c in enumerate(contract.constraints)} != review["protected_snapshot"]:
        raise IntentError("protected file drift since review; prepare a new review")
    try:
        with closing(store.connect(db)) as connection:
            if not store.verify(connection)["ok"]:
                raise IntentError("database chain verification failed")
            store.record_contract(connection, contract)
            row = connection.execute("SELECT record_hash FROM contract WHERE task_id=?", (contract.task_id,)).fetchone()
            receipt = {"schema": "invara.intent-seal/1", "review_id": review["review_id"],
                "review_digest": review["review_digest"], "contract_digest": review["contract_digest"],
                "root_digest": review["root_digest"], "database": _database_identity(Path(db)),
                "sealed_contract": contract.as_dict(), "sealed_contract_digest": _digest(contract.as_dict()),
                "contract_record_hash": row[0], "confirmation": submission,
                "confirmation_evidence": "MATCHING_LOCAL_SUBMISSION_NOT_AUTHENTICATED_HUMAN"}
        _write_new(target / "sealed.json", receipt)
        return receipt
    except (sqlite3.Error, store.DuplicateTask) as exc:
        raise IntentError(f"cannot seal reviewed task: {exc}") from exc


def _bound(output: Path, root: Path, db: Path):
    review = load_review(output)
    root = _check_root(review, root)
    receipt = _read(Path(output) / "sealed.json")
    if receipt.get("database") != _database_identity(Path(db)):
        raise IntentError("database identity mismatch")
    if any(receipt.get(k) != review[k] for k in ("review_id", "review_digest", "contract_digest", "root_digest")):
        raise IntentError("sealed review binding mismatch")
    _confirm(review, receipt.get("confirmation"))
    connection = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        if not store.verify(connection)["ok"]:
            raise IntentError("database chain verification failed")
        contract = store.load_contract(connection, review["task_id"])
        row = connection.execute("SELECT record_hash FROM contract WHERE task_id=?", (review["task_id"],)).fetchone()
        if (receipt.get("sealed_contract") != contract.as_dict()
                or receipt.get("sealed_contract_digest") != _digest(contract.as_dict())
                or receipt.get("contract_record_hash") != row[0]):
            raise IntentError("sealed contract identity mismatch")
        history = store.history(connection, review["task_id"])
        return review, receipt, contract, history
    except (KeyError, sqlite3.Error) as exc:
        raise IntentError("cannot read bound stored evidence") from exc
    finally:
        connection.close()


def judge_review(output: Path, *, root: Path, db: Path, timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Execute the exact sealed contract and bind the stored row to this root."""
    root = _root(root)
    review, receipt, contract, history = _bound(output, root, db)
    before = _snapshot(root, review["check_files"])
    current, observations = observe(contract, root, timeout_s=timeout_s)
    verdict = core_judge(contract, current, observations)
    after = _snapshot(root, review["check_files"])
    with closing(store.connect(db)) as connection:
        if not store.verify(connection)["ok"]:
            raise IntentError("database changed during execution")
        actual = store.load_contract(connection, review["task_id"])
        if actual.as_dict() != contract.as_dict() or store.history(connection, review["task_id"]) != history:
            raise IntentError("bound evidence changed during execution")
        store.record_verdict(connection, contract.task_id, verdict, observations,
                             observed_at=datetime.datetime.now(datetime.UTC).timestamp(), current=current)
        stored = store.history(connection, contract.task_id)[-1]
    binding = {"schema": "invara.intent-run/1", "review_id": review["review_id"],
        "review_digest": review["review_digest"], "root_digest": review["root_digest"],
        "database": receipt["database"], "contract_record_hash": receipt["contract_record_hash"],
        "verdict_seq": stored["seq"], "verdict_record_hash": stored["record_hash"],
        "check_before": before, "check_after": after}
    _write_new(Path(output) / f"run-{stored['seq']}.json", binding)
    report = read_report(output, root=root, db=db)
    report["execution_performed"] = True
    return report


def read_report(output: Path, *, root: Path, db: Path) -> dict:
    """Read only real stored observations; never accept a caller's verdict JSON."""
    root = _root(root)
    review, receipt, contract, history = _bound(output, root, db)
    stored = history[-1] if history else None
    current, observations, raw = {}, [], None
    changes = _drift(review["check_snapshot"], _snapshot(root, review["check_files"]))
    if stored:
        binding_path = Path(output) / f"run-{stored['seq']}.json"
        if not binding_path.is_file():
            raise IntentError("latest verdict has no bound adapter execution")
        binding = _read(binding_path)
        expected = {"review_id": review["review_id"], "review_digest": review["review_digest"],
                    "root_digest": review["root_digest"], "database": receipt["database"],
                    "contract_record_hash": receipt["contract_record_hash"],
                    "verdict_seq": stored["seq"], "verdict_record_hash": stored["record_hash"]}
        if any(binding.get(k) != v for k,v in expected.items()):
            raise IntentError("verdict execution binding mismatch")
        for field in ("check_before", "check_after"):
            changes += _drift(review["check_snapshot"], binding[field])
        data = json.loads(stored["observations_json"])
        current = data.get("current")
        if current is None:
            raise IntentError("stored verdict lacks replay observations")
        observations = [Observation(**item) for item in data["items"]]
        raw = json.loads(stored["detail_json"])
        recomputed = core_judge(contract, current, observations).as_dict()
        if raw != recomputed or raw["status"] != stored["status"] or raw["reason"] != stored["reason"]:
            raise IntentError("stored verdict does not match replay")
    unique_changes = {_canonical(c): c for c in changes}
    changes = list(unique_changes.values())
    predicates = {p.id:p for p in contract.done_when}
    obs = {o.predicate_id:o for o in observations}
    promises = []
    for original in review["promises"]:
        promise = copy.deepcopy(original)
        mapping, evidence, states = promise["mapping"], [], []
        kind = mapping["kind"]
        if kind == "human":
            status, reason = "PENDING_HUMAN", mapping["reason"]
        elif kind == "unmapped":
            status, reason = "UNMAPPED", mapping["reason"]
        elif stored is None:
            status, reason = "PENDING", "No bound command execution has been recorded."
        else:
            for pid in mapping["predicate_ids"]:
                p, o = predicates[pid], obs.get(pid)
                state = "UNVERIFIABLE" if o is None or not o.ran else ("MET" if o.exit_code == p.expect_exit else "NOT_MET")
                states.append(state)
                evidence.append({"kind": "predicate", "id": pid, "ran": bool(o and o.ran),
                    "exit_code": o.exit_code if o else None, "expect_exit": p.expect_exit,
                    "detail": o.detail if o else "No recorded observation"})
            for path in mapping["protected_paths"]:
                matches = [(str(i), c.baseline[path]) for i,c in enumerate(contract.constraints) if path in c.baseline]
                for index, baseline in matches:
                    actual = current.get(index, {}).get(path)
                    states.append("MET" if actual == baseline else "NOT_MET")
                    evidence.append({"kind": "protected_path", "path": path, "baseline_sha256": baseline, "observed_sha256": actual})
            if changes and mapping["predicate_ids"]:
                status, reason = "UNVERIFIABLE", "Check/policy scope drifted; command success cannot establish this mapping."
            else:
                status = next((s for s in ("NOT_MET", "UNVERIFIABLE") if s in states), "MET")
                reason = "All mapped recorded observations match." if status == "MET" else "A mapped observation failed or could not be checked."
        promise.update(status=status, evidence=evidence, reason=reason)
        promises.append(promise)
    counts = Counter(p["status"] for p in promises)
    mappings = [p["mapping"] for p in promises]
    all_predicates = [pid for m in mappings for pid in m["predicate_ids"]]
    all_paths = [path for m in mappings for path in m["protected_paths"]]
    coverage = {"total": len(promises), "machine_mapped": sum(m["kind"] == "machine" for m in mappings),
        "human": sum(m["kind"] == "human" for m in mappings), "unmapped": counts["UNMAPPED"],
        "checked": counts["MET"] + counts["NOT_MET"], "met": counts["MET"], "not_met": counts["NOT_MET"],
        "unverifiable": counts["UNVERIFIABLE"], "pending": counts["PENDING"] + counts["PENDING_HUMAN"],
        "unique_predicate_count": len(set(all_predicates)), "unique_protected_path_count": len(set(all_paths)),
        "shared_evidence": len(all_predicates) != len(set(all_predicates)) or len(all_paths) != len(set(all_paths))}
    intent_status = "CHECKED_CONDITIONS_MET" if counts["MET"] == len(promises) and raw and raw["status"] == "PASS" and not changes else (
        "NOT_MET" if counts["NOT_MET"] or (raw and raw["status"] == "BLOCK") else "INCOMPLETE" if stored else "PENDING")
    return {"schema": "invara.intent-report/1", "task_id": review["task_id"], "review_id": review["review_id"],
        "original_request": review["original_request"], "promises": promises, "suggestions": review["suggestions"],
        "coverage": coverage, "intent_status": intent_status, "raw_verdict": raw,
        "observed_at": datetime.datetime.fromtimestamp(stored["observed_at"], datetime.UTC).isoformat() if stored else None,
        "historical": True, "execution_performed": False, "current_project_verified": False,
        "check_drift": changes, "root_digest": review["root_digest"], "contract_digest": review["contract_digest"],
        "sealed_contract_digest": receipt["sealed_contract_digest"],
        "application_status": "UNKNOWN", "publication_status": "UNKNOWN",
        "limitations": review["limitations"] + ["This report reads stored observations; it does not establish the present project state.",
            "User understanding and human identity have not been authenticated."]}
