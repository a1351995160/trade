"""只验证发布包元数据合同，不把本合成fixture当作真实验收。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities, _published_acceptance
from test_strategy_submission_v1 import service, request


def publication_fixture(root, core):
    prefix = Path('reports/trusted_workflow_acceptance')
    def save(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, sort_keys=True).encode()
        target.write_bytes(raw)
        return {'path': path.as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()}
    cases = []
    for number in range(3):
        for pool in (['000003.SZ'], ['600004.SH']):
            directory = prefix / 'published_metadata' / f'{number}_{pool[0]}'
            rule_identity = stable_hash({'rule': number})
            preview = {'capabilities': core, 'request': {'initial_cash': 50000, 'symbols': pool, 'benchmark': 'FULL_POOL_BUY_HOLD'},
                       'rule_identity': rule_identity}
            preview['preview_identity'] = stable_hash(preview)
            preview_ref = save(directory / 'PREVIEW.json', preview)
            job = {'plans': {}, 'items': {}, 'input_identity': 'frozen_input', 'objective_id': 'test',
                   'source_hashes': {'original/PREVIEW.json': preview_ref['sha256']}}
            payload = {'version': 'RESEARCH_RULE_STRATEGY_V3', 'exits': {'stop_loss_pct': .1, 'take_profit_pct': .2, 'trailing_pct': .05}}
            for scenario in ('BASE', 'STRESS', 'BENCHMARK'):
                name = 'TEST_' + scenario
                params = {'candidate_payload': {'version': 'FULL_POOL_BUY_HOLD_V1', 'symbols': pool} if scenario == 'BENCHMARK' else payload,
                          'rule_identity': 'benchmark' if scenario == 'BENCHMARK' else rule_identity}
                plan = {'strategy': {'parameters': params}, 'backend': {'initial_cash': 50000, 'window': {'symbols': pool}}, 'runtime': {}}
                plan['plan_id'] = stable_hash(plan)
                job['plans'][name] = plan
                job['items'][name] = {}
            job_ref = save(directory / 'JOB.json', job)
            confirmation = {'strategy_plans': job['plans'], 'input_identity': job['input_identity'], 'objective_id': 'test'}
            confirmation['receipt_id'] = stable_hash(confirmation)
            confirmation_ref = save(directory / 'CONFIRMATION.json', confirmation)
            accounts, proofs, index = {}, {}, {}
            for name, plan in job['plans'].items():
                result = save(directory / (name + '_RESULT.json'), {'strategy_plan': plan, 'input_identity': job['input_identity']})
                start = {'kind': name, 'receipt_id': confirmation['receipt_id'], 'counted_before_account_calculation': True}
                settled = {**start, 'completed': True, 'error': None, 'result_sha256': result['sha256']}
                accounts[name] = {'result': result, 'start': save(directory / (name + '_START.json'), start),
                    'settlement': save(directory / (name + '_SETTLEMENT.json'), settled),
                    'resource': save(directory / (name + '_RESOURCE.json'), {'returncode': 0, 'timed_out': False})}
                index[name] = {'sha256': result['sha256']}
                proofs[name] = {'status': 'PASS', 'advance_allowed': True, 'reasons': [], 'plan_id': plan['plan_id'],
                    'input_identity': job['input_identity'], 'result_sha256': result['sha256'],
                    'evidence_layers': {'account_reconciled': True, 'input_profile': 'HISTORICAL_MODELED'}}
            cases.append({'rule_identity': rule_identity, 'symbols': pool, 'pool_identity': stable_hash(pool), 'initial_cash': 50000,
                'job': job_ref, 'preview': preview_ref, 'confirmation': confirmation_ref, 'accounts': accounts,
                'index': save(directory / 'RESULTS_INDEX.json', {'items': index}),
                'verification': save(directory / 'VERIFICATION.json', {'job_sha256': job_ref['sha256'], 'advance_allowed': True, 'items': proofs})})
    receipt = {'version': 'PUBLISHED_RESEARCH_ACCEPTANCE_V1', 'source_hashes': core['source_hashes'],
               'feature_ids': ['indicator_rules', 'cost_stop', 'take_profit', 'trailing_stop', 'full_pool_benchmark'], 'cases': cases}
    receipt['self_hash'] = stable_hash(receipt)
    save(prefix / 'PUBLISHED_ACCEPTANCE.json', receipt)
    return receipt


def test_publication_does_not_change_submission_preview_identity(tmp_path):
    core = capabilities()
    submission = service(tmp_path / 'tasks')
    before = submission.preview(request())
    assert _published_acceptance(tmp_path, core)['status'] == 'NOT_ACCEPTED'
    publication_fixture(tmp_path, core)
    published = _published_acceptance(tmp_path, core)
    assert published['status'] == 'PUBLISHED_METADATA_VERIFIED'
    assert published['account_count'] == 18 and published['strategy_qualified'] is False
    assert submission.preview(request())['preview_identity'] == before['preview_identity']
    assert capabilities()['fingerprint'] == core['fingerprint']


@pytest.mark.parametrize('change', ['source', 'bytes', 'missing', 'receipt_hash', 'bool_only', 'escape'])
def test_publication_fails_closed_on_tamper_or_incomplete_evidence(tmp_path, change):
    core = capabilities()
    receipt = publication_fixture(tmp_path, core)
    path = tmp_path / 'reports/trusted_workflow_acceptance/PUBLISHED_ACCEPTANCE.json'
    if change == 'source':
        core = deepcopy(core)
        core['source_hashes']['research_capabilities_v1.py'] = 'changed'
    elif change == 'bytes':
        (tmp_path / receipt['cases'][0]['job']['path']).write_bytes(b'{}')
    elif change == 'missing':
        (tmp_path / receipt['cases'][0]['verification']['path']).unlink()
    elif change == 'receipt_hash':
        receipt['initial_cash'] = 1
        path.write_text(json.dumps(receipt))
    else:
        if change == 'bool_only':
            receipt['cases'] = [{'passed': True}]
        else:
            receipt['cases'][0]['job']['path'] = '../../market/JOB.json'
        receipt['self_hash'] = stable_hash({key: value for key, value in receipt.items() if key != 'self_hash'})
        path.write_text(json.dumps(receipt))
    assert _published_acceptance(tmp_path, core)['status'] == 'NOT_ACCEPTED'


def test_checked_in_old_publication_remains_verifiable_but_does_not_cover_new_sources():
    from chanlun_trader.research_factory.research_capabilities_v1 import published_acceptance
    repo = Path(__file__).resolve().parents[2]
    receipt = json.loads((repo / 'reports/trusted_workflow_acceptance/PUBLISHED_ACCEPTANCE.json').read_bytes())
    reference = receipt['cases'][0]['preview']['path']
    archived_core = json.loads((repo / reference).read_bytes())['capabilities']
    assert receipt['source_hashes'] == archived_core['source_hashes']
    archived = _published_acceptance(repo, archived_core)
    assert archived['status'] == 'PUBLISHED_METADATA_VERIFIED', archived
    assert archived['case_count'] == 6 and archived['account_count'] == 18
    assert archived['strategy_qualified'] is False
    assert receipt['source_hashes'] != capabilities()['source_hashes']
    evidence = published_acceptance()
    assert evidence['status'] == 'NOT_ACCEPTED', evidence
    assert evidence['reason'] == 'PUBLICATION_SOURCE_CHANGED'
    assert evidence['strategy_qualified'] is False
