"""Execution counts must never masquerade as unique or new coverage."""
import copy
import pytest

from invara.assurance import report
from invara.assurance.manifest import CorpusItem, content_digest
from invara.assurance.session import Snapshot
from invara.assurance.claims import FinalVerdict


def evidence_case(baseline, candidates, shrink=()):
    rows = []
    def add(kind, record):
        digest = content_digest(record)
        rows.append({'kind': kind, 'digest': digest, 'record': record})
        return digest
    frozen = {'manifest_digest': content_digest({'fixture': 'report counts'}), 'record_digests': {}}
    for i, value in enumerate(baseline):
        frozen['record_digests'][str(i)] = add('raw', {'input_digest': value.identity(), 'input_id': str(i)})
    frozen['baseline_digest'] = content_digest({'manifest': frozen['manifest_digest'], 'records': dict(sorted(frozen['record_digests'].items()))})
    frozen['inputs'] = sorted(frozen['record_digests'])
    addresses = []
    for prefix, values in [('search', candidates), ('shrink', shrink)]:
        for i, value in enumerate(values, 1):
            raw = add('raw', {'input_digest': value.identity(), 'input_id': f'{prefix}-{i}'})
            addresses.append(add('comparison', {'input_id': f'{prefix}-{i}', 'source_raw_digest': raw, 'target_raw_digest': raw}))
    result = {'kind': 'counterexample_search', 'baseline_digest': frozen['baseline_digest'], 'coverage': {'runs': len(candidates), 'shrink_runs': len(shrink)}, 'evidence_digests': addresses}
    return result, frozen, rows


def item(value, state=None):
    return CorpusItem(id='display-only', input=value, initial_state=state or {})


@pytest.mark.parametrize('baseline,values,expected', [
    ([0], [0, 0], (2, 1, 1, 0, 1)),  # A duplicate seeds
    ([0], [1, 1, 1], (3, 1, 0, 1, 2)),  # B repeated mutations
    ([0, 1], [0, 1, 0, 1], (4, 2, 2, 0, 2)),  # C zero-new
    ([0], [1, 2, 3], (3, 3, 0, 3, 0)),  # D genuinely new
    ([0, 1], [0, 0, 1, 2, 2, 3], (6, 4, 2, 2, 2)),  # E mixed
])
def test_accounting(baseline, values, expected):
    args = evidence_case(list(map(item, baseline)), list(map(item, values)))
    before = copy.deepcopy(args)
    counts = report.search_input_counts(*args)
    keys = ('search_executions', 'distinct_search_inputs', 'baseline_overlap', 'new_distinct_inputs', 'repeated_executions')
    assert tuple(counts[k] for k in keys) == expected
    assert counts['new_distinct_inputs'] <= counts['distinct_search_inputs'] <= counts['search_executions']
    assert counts['distinct_search_inputs'] == counts['baseline_overlap'] + counts['new_distinct_inputs']
    assert args == before


def test_initial_state_is_part_of_identity_and_shrinks_are_separate():
    args = evidence_case([item(0)], [item(0), item(0, {'files': {'a': 'b'}})], shrink=[item(5)])
    counts = report.search_input_counts(*args)
    assert counts['search_executions'] == 2
    assert counts['distinct_search_inputs'] == 2
    assert counts['new_distinct_inputs'] == 1


def test_incomplete_search_evidence_is_unknown_not_positive_coverage():
    result, frozen, rows = evidence_case([item(0)], [item(0), item(1)])
    counts = report.search_input_counts(result, frozen, rows[:-1])
    assert counts['search_executions'] == 2
    assert counts['distinct_search_inputs'] is None
    assert counts['new_distinct_inputs'] is None


def test_wrong_baseline_is_unknown_even_with_known_search_inputs():
    result, frozen, rows = evidence_case([item(0)], [item(0), item(1)])
    frozen['baseline_digest'] = 'other'
    counts = report.search_input_counts(result, frozen, rows)
    assert counts['distinct_search_inputs'] == 2
    assert counts['baseline_overlap'] is None
    assert counts['new_distinct_inputs'] is None


def test_missing_runs_is_unknown_not_compared_runs_fallback():
    result, frozen, rows = evidence_case([item(0)], [item(0)])
    result['coverage'] = {'compared_runs': 1}
    assert report.search_input_counts(result, frozen, rows)['search_executions'] is None


@pytest.mark.parametrize('change', ['missing_reference', 'all_references_missing', 'wrong_manifest', 'wrong_reference_key', 'incomplete_input_list', 'malformed_reference_key'])
def test_incomplete_or_unbound_frozen_reference_set_is_unknown(change):
    result, frozen, rows = evidence_case([item(0), item(1)], [item(0), item(1)])
    assert report.search_input_counts(result, frozen, rows)['new_distinct_inputs'] == 0
    if change == 'missing_reference':
        frozen['record_digests'].pop('1')
    elif change == 'all_references_missing':
        frozen['record_digests'].clear()
    elif change == 'wrong_manifest':
        frozen['manifest_digest'] = content_digest({'other': 'manifest'})
    elif change == 'malformed_reference_key':
        frozen['record_digests'][1] = frozen['record_digests'].pop('1')
    elif change == 'wrong_reference_key':
        frozen['record_digests']['other'] = frozen['record_digests'].pop('1')
        frozen['inputs'] = sorted(frozen['record_digests'])
        # Even a self-consistent digest cannot bind a reference key to a
        # raw record of another input ID.
        frozen['baseline_digest'] = content_digest({'manifest': frozen['manifest_digest'], 'records': dict(sorted(frozen['record_digests'].items()))})
        result['baseline_digest'] = frozen['baseline_digest']
    else:
        frozen['inputs'].pop()
    counts = report.search_input_counts(result, frozen, rows)
    assert counts['distinct_search_inputs'] == 2
    assert counts['baseline_overlap'] is None
    assert counts['new_distinct_inputs'] is None


def test_plain_report_does_not_call_executions_generated_inputs():
    result, _, _ = evidence_case([item(0)], [item(0), item(0)])
    result['status'] = 'NO_DIVERGENCE_FOUND'
    data = report._behavior(Snapshot(), FinalVerdict(status='PASS', reason='', decided_by='claims'), [result])
    assert 'additional generated input' not in ' '.join(data['lines_en'])
    assert '추가로 만들어 본 입력' not in ' '.join(data['lines_ko'])
    assert 'execution' in ' '.join(data['lines_en'])


def test_native_duplicate_seed_report_export_and_inspection(tmp_path):
    import sys
    from _support import manifest_dict
    from invara.assurance import package
    from invara.assurance.evidence import Evidence
    from invara.assurance.manifest import Manifest
    from invara.assurance.workflow import Workflow
    (tmp_path / 'app.py').write_text('import json, sys\njson.load(sys.stdin)\nprint("{}")\n', encoding='utf-8')
    manifest = Manifest.from_dict(manifest_dict(
        session_id='count-parity', source_system={'id':'before','kind':'process','command':[sys.executable,'app.py'],'root':'$SOURCE_ROOT'},
        target_system={'same_as_source':True},
        input_domain={'kind':'corpus','delivery':'stdin_json','corpus':[{'id':'first','input':0},{'id':'duplicate','input':0}]},
        probes=[{'id':'out','adapter':'json','source':'stdout','mandatory':True}], policies=[],
        claims=[{'id':'search','kind':'counterexample_search','mandatory':True,'params':{'runs':2,'seconds':30,'seed':3}}],
        budgets={'shrink_steps':0}))
    evidence = Evidence(tmp_path / 'session.db')
    try:
        flow = Workflow(evidence, workspace_parent=tmp_path / 'runs')
        data = flow.run(manifest, {'SOURCE_ROOT':str(tmp_path),'TARGET_ROOT':str(tmp_path)})['report']
        counts = data['technical']['claims'][0]['coverage']['input_counts']
        assert counts['search_executions'] == 2
        assert counts['distinct_search_inputs'] == 1
        assert counts['new_distinct_inputs'] == 0
        behavior = data['summary']['sections'][0]
        assert any('새 입력은 0개' in line for line in behavior['lines_ko'])
        assert any('0 were new' in line for line in behavior['lines_en'])
        assert 'This is evidence, not a proof.' in report.markdown(data)
        archive = tmp_path / 'export.zip'
        package.export(evidence, manifest.session_id, archive)
        inspected = package.inspect(archive)
        assert inspected['ok'], inspected['problems']
        assert inspected['checks']['report_rebuilt']
    finally:
        evidence.close()
