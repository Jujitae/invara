"""Ordinary local runtime identity and real schema-1 history compatibility."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_intent import api, confirmed, project

ROOT = Path(__file__).resolve().parents[1]
HELPER = '''import json, sys
from pathlib import Path
from invara import intent
mode, root, output, db, proposal, confirmation = sys.argv[1:]
root, output, db = Path(root), Path(output), Path(db)
try:
 if mode == 'prepare':
  result = intent.prepare(json.loads(Path(proposal).read_text(encoding='utf-8')), root=root, output=output)
 elif mode == 'seal':
  result = intent.seal_review(output, Path(confirmation), root=root, db=db)
 elif mode == 'judge':
  result = intent.judge_review(output, root=root, db=db)
 else:
  result = intent.read_report(output, root=root, db=db)
 print(json.dumps({'result':result}))
except intent.IntentError as exc:
 print(json.dumps({'refused':str(exc)}))
'''


@pytest.fixture
def environments(project):
    root, output, db, proposal = project
    folder = root.parent
    helper = folder / 'ordinary_workflow.py'
    helper.write_text(HELPER, encoding='utf-8')
    proposal_path = folder / 'proposal.json'
    proposal_path.write_text(json.dumps(proposal), encoding='utf-8')
    confirmation_path = folder / 'confirmation.json'
    sources = []
    for name in ('environment-a', 'environment-b'):
        site = folder / name
        shutil.copytree(ROOT / 'src/invara', site / 'invara', ignore=shutil.ignore_patterns('__pycache__'))
        sources.append(site)

    def run(site, mode):
        env = dict(os.environ, PYTHONPATH=str(site), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(helper), mode, str(root), str(output), str(db),
            str(proposal_path), str(confirmation_path)], cwd=folder, env=env, capture_output=True,
            text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    def submit(review):
        # Synthetic test submission only; no authenticated person is asserted.
        confirmation_path.write_text(json.dumps(confirmed(review)), encoding='utf-8')
    return sources, run, submit


def test_prepare_confirmation_and_real_execution_bind_current_verifier(project):
    root, output, db, proposal = project
    review = api().prepare(proposal, root=root, output=output)
    assert review['schema'] == 'invara.intent-review/2'
    runtime = review['verifier_identity']
    assert review['verifier_identity_digest'] == runtime['identity_sha256']
    assert review['confirmation_template']['verifier_identity_digest'] == runtime['identity_sha256']
    from invara.assurance import identity
    assert identity.validate(runtime) == []
    receipt = api().seal_review(output, confirmed(review), root=root, db=db)
    assert receipt['verifier_identity_digest'] == runtime['identity_sha256']
    report = api().judge_review(output, root=root, db=db)
    assert report['raw_verdict']['status'] == 'PASS'
    assert report['verifier_identity_status'] == 'CURRENT_MATCH'
    assert report['current_verifier_identity_verified'] is True
    assert report['current_project_verified'] is False


def test_different_ordinary_source_environment_requires_fresh_review(environments):
    (first, second), run, submit = environments
    review = run(first, 'prepare')['result']
    submit(review)
    assert 'verifier identity' in run(second, 'seal')['refused']
    run(first, 'seal')
    execution = run(first, 'judge')['result']
    assert execution['verifier_identity_status'] == 'CURRENT_MATCH'
    assert 'verifier identity' in run(second, 'judge')['refused']
    historical = run(second, 'report')['result']
    assert historical['raw_verdict'] == execution['raw_verdict']
    assert historical['verdict_record_hash'] == execution['verdict_record_hash']
    assert historical['verifier_identity_status'] == 'CURRENT_UNVERIFIED'
    assert historical['verifier_identity_warning']
    assert historical['current_verifier_identity_verified'] is False
    assert historical['intent_status'] == 'INCOMPLETE'
    assert historical['historical'] is True


def test_actual_legacy_producer_history_remains_readable_without_runtime_claim(environments):
    (legacy, current), run, submit = environments
    # Exact adapter source from f8abfb0, before schema-2 binding, in a separate
    # ordinary source environment. Its checks and SQLite observations are real.
    shutil.copyfile(ROOT / 'tests/fixtures/intent_review_v1.py', legacy / 'invara/intent.py')
    review = run(legacy, 'prepare')['result']
    assert review['schema'] == 'invara.intent-review/1'
    submit(review)
    run(legacy, 'seal')
    execution = run(legacy, 'judge')['result']
    historical = run(current, 'report')['result']
    assert historical['raw_verdict'] == execution['raw_verdict']
    assert historical['verdict_record_hash'] == execution['verdict_record_hash']
    assert historical['verifier_identity_status'] == 'LEGACY_UNBOUND'
    assert historical['verifier_identity_digest'] is None
    assert historical['intent_status'] == 'INCOMPLETE'
    assert 'legacy review' in run(current, 'judge')['refused']
    assert 'legacy review' in run(current, 'seal')['refused']
