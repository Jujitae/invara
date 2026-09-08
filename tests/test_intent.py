"""Real local operations for the intent adapter; no fabricated verdict inputs."""
from __future__ import annotations

import copy
import datetime
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def api():
    assert importlib.util.find_spec("invara.intent") is not None, "intent adapter is not implemented"
    return importlib.import_module("invara.intent")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "checks").mkdir()
    (root / "pricing.json").write_text('{"price":9}', encoding="utf-8")
    (root / "page.html").write_text('<button>Start</button>', encoding="utf-8")
    (root / "checks/check_ui.py").write_text(
        "from pathlib import Path\nassert 'Start' in Path('page.html').read_text()\n", encoding="utf-8")
    spec = {
        "schema": "invara.intent-proposal/1", "task_id": "ordinary-ui-edit",
        "original_request": "Change the layout. Keep the Start button and price.",
        "promises": [
            {"id": "start", "text": "Keep the Start button in the edited UI", "kind": "change", "origin": "user",
             "mapping": {"kind": "machine", "predicate_ids": ["ui"], "protected_paths": []}},
            {"id": "price", "text": "Keep the price file unchanged", "kind": "keep", "origin": "user",
             "mapping": {"kind": "machine", "predicate_ids": [], "protected_paths": ["pricing.json"]}},
        ],
        "suggestions": [{"id": "optional-colors", "text": "Consider another color later"}],
        "contract": {"task_id": "ordinary-ui-edit", "intent": "Change layout while preserving Start and price",
                     "constraints": [{"kind": "paths_unchanged", "paths": ["pricing.json"], "reason": "price fixed"}],
                     "done_when": [{"id": "ui", "command": [sys.executable, "-B", "checks/check_ui.py"], "expect_exit": 0, "reason": "required UI"}]},
        "check_files": ["checks"],
    }
    return root, tmp_path / "review", tmp_path / "verify.db", spec


def confirmed(review):
    value = copy.deepcopy(review["confirmation_template"])
    value.update(decision="confirm", accepted_promise_ids=[p["id"] for p in review["promises"]],
                 reviewed_at=datetime.datetime.now(datetime.UTC).isoformat())
    return value


def prepared(project):
    root, output, db, proposal = project
    review = api().prepare(proposal, root=root, output=output)
    return review, confirmed(review)


def sealed(project):
    root, output, db, _ = project
    review, confirmation = prepared(project)
    api().seal_review(output, confirmation, root=root, db=db)
    return review


def test_prepare_preserves_request_origins_suggestions_and_no_default_confirmation(project):
    root, output, db, proposal = project
    review = api().prepare(proposal, root=root, output=output)
    assert review["original_request"] == proposal["original_request"]
    assert review["promises"] == proposal["promises"]
    assert review["suggestions"] == proposal["suggestions"]
    assert review["confirmation_template"]["decision"] is None
    assert review["confirmation_template"]["accepted_promise_ids"] == []
    assert not db.exists()
    assert api().load_review(output) == review
    assert "checks/check_ui.py" in {p for c in review["contract"]["constraints"] for p in c["paths"]}


def test_ordinary_edit_real_seal_judge_and_bound_report(project):
    root, output, db, _ = project
    review = sealed(project)
    (root / "page.html").write_text('<main><button>Start</button></main>', encoding="utf-8")
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "PASS"
    assert [p["status"] for p in report["promises"]] == ["MET", "MET"]
    assert report["coverage"]["met"] == 2
    assert report["application_status"] == report["publication_status"] == "UNKNOWN"
    assert report["review_id"] == review["review_id"]
    assert report["observed_at"]
    stored = api().read_report(output, root=root, db=db)
    assert stored["raw_verdict"] == report["raw_verdict"]
    assert stored["historical"] is True
    assert stored["execution_performed"] is False


def test_required_ui_removed_is_not_met_from_real_command(project):
    root, output, db, _ = project
    sealed(project)
    (root / "page.html").write_text('<main>No action</main>', encoding="utf-8")
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "BLOCK"
    assert report["promises"][0]["status"] == "NOT_MET"
    assert report["promises"][1]["status"] == "MET"


@pytest.mark.parametrize("kind,status", [("human", "PENDING_HUMAN"), ("unmapped", "UNMAPPED")])
def test_human_or_unmapped_promise_never_becomes_green_with_core_pass(project, kind, status):
    root, output, db, proposal = project
    proposal["promises"].append({"id": "understanding", "text": "Users understand this", "kind": "change", "origin": "agent",
                                 "mapping": {"kind": kind, "reason": "No user study exists", "predicate_ids": [], "protected_paths": []}})
    sealed(project)
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "PASS"
    assert report["promises"][-1]["status"] == status
    assert report["intent_status"] != "CHECKED_CONDITIONS_MET"
    assert report["coverage"][kind] == 1


def test_missing_command_preserves_unverifiable(project):
    root, output, db, proposal = project
    proposal["contract"]["done_when"][0]["command"] = ["invara-intent-no-such-command-8a7955"]
    sealed(project)
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "UNVERIFIABLE"
    assert report["promises"][0]["status"] == "UNVERIFIABLE"


def test_changed_test_cannot_weaken_mapping_after_freeze(project):
    root, output, db, _ = project
    sealed(project)
    (root / "checks/check_ui.py").write_text("pass\n", encoding="utf-8")
    (root / "page.html").write_text("removed", encoding="utf-8")
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "BLOCK"
    assert report["promises"][0]["status"] != "MET"
    assert report["check_drift"]


def test_new_check_file_and_later_check_drift_are_visible(project):
    root, output, db, _ = project
    sealed(project)
    report = api().judge_review(output, root=root, db=db)
    assert report["raw_verdict"]["status"] == "PASS"
    (root / "checks/extra.py").write_text("pass\n", encoding="utf-8")
    later = api().read_report(output, root=root, db=db)
    assert later["raw_verdict"]["status"] == "PASS"
    assert later["historical"] is True
    assert later["check_drift"]
    assert later["promises"][0]["status"] != "MET"


@pytest.mark.parametrize("field,value", [
    ("proposal_digest", "0"*64), ("contract_digest", "0"*64), ("review_id", "different"),
    ("root_digest", "0"*64), ("decision", None), ("accepted_promise_ids", ["start"]),
    ("accepted_promise_ids", ["start", "start"]), ("reviewed_at", None),
])
def test_mismatched_or_incomplete_confirmation_refused(project, field, value):
    root, output, db, _ = project
    review, confirmation = prepared(project)
    confirmation[field] = value
    with pytest.raises(api().IntentError):
        api().seal_review(output, confirmation, root=root, db=db)
    assert not db.exists()


def test_wrong_root_and_wrong_database_are_refused(project, tmp_path):
    root, output, db, _ = project
    review, confirmation = prepared(project)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(api().IntentError, match="root"):
        api().seal_review(output, confirmation, root=other, db=db)
    api().seal_review(output, confirmation, root=root, db=db)
    with pytest.raises(api().IntentError, match="database"):
        api().read_report(output, root=root, db=tmp_path/"other.db")


def test_pending_report_has_no_builder_verdict(project):
    root, output, db, _ = project
    sealed(project)
    report = api().read_report(output, root=root, db=db)
    assert report["raw_verdict"] is None
    assert report["coverage"]["pending"] == 2
    assert all(p["status"] == "PENDING" for p in report["promises"])


def test_check_changed_before_seal_requires_fresh_review(project):
    root, output, db, _ = project
    review, confirmation = prepared(project)
    (root/"checks/check_ui.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(api().IntentError, match="drift"):
        api().seal_review(output, confirmation, root=root, db=db)


@pytest.mark.parametrize("mapping", [
    {"kind":"machine","predicate_ids":["not-real"],"protected_paths":[]},
    {"kind":"machine","predicate_ids":[],"protected_paths":["page.html"]},
    {"kind":"human","predicate_ids":[],"protected_paths":[]},
])
def test_mapping_requires_real_evidence_or_explicit_reason(project, mapping):
    root, output, db, proposal = project
    proposal["promises"][0]["mapping"] = mapping
    with pytest.raises(api().IntentError):
        api().prepare(proposal, root=root, output=output)


def test_shared_evidence_does_not_inflate_distinct_check_coverage(project):
    root, output, db, proposal = project
    duplicate=copy.deepcopy(proposal["promises"][0]); duplicate['id']='layout'; duplicate['text']='Layout has Start'
    proposal["promises"].append(duplicate)
    sealed(project)
    report=api().judge_review(output,root=root,db=db)
    assert report['coverage']['machine_mapped']==3
    assert report['coverage']['unique_predicate_count']==1
    assert report['coverage']['shared_evidence'] is True


def test_unbound_external_judge_record_cannot_be_used_as_adapter_evidence(project):
    root, output, db, _ = project
    sealed(project)
    api().judge_review(output, root=root, db=db)
    from invara import store
    from invara.runner import observe
    from invara.verdict import judge
    with store.connect(db) as connection:
        contract=store.load_contract(connection,'ordinary-ui-edit')
        current,observations=observe(contract,root)
        store.record_verdict(connection,contract.task_id,judge(contract,current,observations),observations,
                             observed_at=datetime.datetime.now(datetime.UTC).timestamp(),current=current)
    with pytest.raises(api().IntentError,match='bound'):
        api().read_report(output,root=root,db=db)


def test_edited_frozen_review_or_proposal_is_refused(project):
    root, output, db, _ = project
    prepared(project)
    path=output/'review.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    data['original_request']='different request'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(api().IntentError):
        api().load_review(output)
