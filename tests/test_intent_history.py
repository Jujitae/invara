"""Historical selection uses real repeated executions, not supplied verdict JSON."""
import copy
import json

import pytest

from test_intent import api, confirmed, project, sealed


def test_historical_block_survives_later_pass_and_latest_stays_default(project):
    root, output, db, _ = project
    sealed(project)
    (root / 'page.html').write_text('<main>Empty</main>', encoding='utf-8')
    first = api().judge_review(output, root=root, db=db)
    (root / 'page.html').write_text('<button>Start</button>', encoding='utf-8')
    second = api().judge_review(output, root=root, db=db)
    from invara import store
    with store.connect(db) as connection:
        rows = store.history(connection, 'ordinary-ui-edit')
    assert first['verdict_seq'] == rows[0]['seq']
    assert second['verdict_seq'] == rows[1]['seq']
    for execution, row in zip((first, second), rows):
        historical = api().read_report(output, root=root, db=db, verdict_seq=row['seq'])
        assert historical['raw_verdict'] == json.loads(row['detail_json']) == execution['raw_verdict']
        assert historical['verdict_record_hash'] == row['record_hash']
        assert historical['historical'] is True
        assert historical['execution_performed'] is False
        assert historical['current_project_verified'] is False
        assert historical['application_status'] == historical['publication_status'] == 'UNKNOWN'
    assert first['raw_verdict']['status'] == 'BLOCK'
    assert second['raw_verdict']['status'] == 'PASS'
    assert api().read_report(output, root=root, db=db)['verdict_seq'] == second['verdict_seq']


@pytest.mark.parametrize('selection', [0, -1, True, 1.0, '1', 999])
def test_selection_requires_real_positive_integer_row(project, selection):
    root, output, db, _ = project
    sealed(project)
    api().judge_review(output, root=root, db=db)
    with pytest.raises(api().IntentError, match='verdict'):
        api().read_report(output, root=root, db=db, verdict_seq=selection)


def test_pending_report_has_no_row_identity_and_explicit_selection_refuses(project):
    root, output, db, _ = project
    sealed(project)
    report = api().read_report(output, root=root, db=db)
    assert report['verdict_seq'] is report['verdict_record_hash'] is None
    with pytest.raises(api().IntentError, match='verdict'):
        api().read_report(output, root=root, db=db, verdict_seq=1)


def test_sequence_is_database_row_identity_not_task_history_position(project):
    root, output, db, proposal = project
    sealed(project)
    first = api().judge_review(output, root=root, db=db)
    other = copy.deepcopy(proposal)
    other['task_id'] = other['contract']['task_id'] = 'second-ordinary-task'
    other_output = output.parent / 'other-review'
    review = api().prepare(other, root=root, output=other_output)
    api().seal_review(other_output, confirmed(review), root=root, db=db)
    other_run = api().judge_review(other_output, root=root, db=db)
    last = api().judge_review(output, root=root, db=db)
    assert last['verdict_seq'] > other_run['verdict_seq'] > first['verdict_seq']
    assert api().read_report(output, root=root, db=db, verdict_seq=last['verdict_seq'])['raw_verdict'] == last['raw_verdict']
    with pytest.raises(api().IntentError, match='task history'):
        api().read_report(output, root=root, db=db, verdict_seq=other_run['verdict_seq'])


def test_historical_selection_requires_its_original_run_receipt(project):
    root, output, db, _ = project
    sealed(project)
    first = api().judge_review(output, root=root, db=db)
    api().judge_review(output, root=root, db=db)
    receipt = output / f"run-{first['verdict_seq']}.json"
    receipt.rename(receipt.with_suffix('.missing'))
    with pytest.raises(api().IntentError, match='bound'):
        api().read_report(output, root=root, db=db, verdict_seq=first['verdict_seq'])
    assert api().read_report(output, root=root, db=db)['raw_verdict']['status'] == 'PASS'


def test_historical_selection_keeps_current_check_drift_visible(project):
    root, output, db, _ = project
    sealed(project)
    first = api().judge_review(output, root=root, db=db)
    api().judge_review(output, root=root, db=db)
    (root / 'checks/new_check.txt').write_text('new policy input', encoding='utf-8')
    historical = api().read_report(output, root=root, db=db, verdict_seq=first['verdict_seq'])
    assert historical['raw_verdict'] == first['raw_verdict']
    assert historical['check_drift']
    assert historical['promises'][0]['status'] == 'UNVERIFIABLE'
    assert historical['intent_status'] != 'CHECKED_CONDITIONS_MET'


def test_coverage_aliases_keep_human_unmapped_and_failed_separate(project):
    root, output, db, proposal = project
    for kind in ('human', 'unmapped'):
        proposal['promises'].append({'id':kind, 'text':kind, 'kind':'change', 'origin':'agent',
            'mapping':{'kind':kind, 'predicate_ids':[], 'protected_paths':[], 'reason':'No automatic observation'}})
    sealed(project)
    pending = api().read_report(output, root=root, db=db)
    passed = api().judge_review(output, root=root, db=db)
    (root / 'page.html').write_text('empty', encoding='utf-8')
    failed = api().judge_review(output, root=root, db=db)
    aliases = {'REQUESTED_PROMISES':'total', 'MAPPED_MACHINE_PROMISES':'machine_mapped',
        'HUMAN_ONLY_PROMISES':'human', 'UNMAPPED_PROMISES':'unmapped',
        'VERIFIED_PROMISES':'met', 'FAILED_PROMISES':'not_met'}
    for report in (pending, passed, failed):
        coverage = report['coverage']
        assert all(coverage[key] == coverage[original] for key, original in aliases.items())
        assert coverage['REQUESTED_PROMISES'] == 4
        assert coverage['HUMAN_ONLY_PROMISES'] == coverage['UNMAPPED_PROMISES'] == 1
    assert pending['coverage']['VERIFIED_PROMISES'] == 0
    assert passed['coverage']['VERIFIED_PROMISES'] == 2
    assert passed['intent_status'] == 'INCOMPLETE'
    assert failed['coverage']['VERIFIED_PROMISES'] == failed['coverage']['FAILED_PROMISES'] == 1
