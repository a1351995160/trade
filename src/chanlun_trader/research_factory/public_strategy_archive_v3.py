"""公共 V3 作业的持久策略档案；不继承旧探索资格或合成准入。"""
from copy import deepcopy
import json
from pathlib import Path
import re

from .bounded_research_v1 import _put, _read, source_identity
from .common import stable_hash
from .research_evidence_v1 import reconstruct_account, verify_job_evidence
from .research_rule_strategy_v3 import CAPABILITY, ResearchRuleStrategyV3
from .strategy_qualification_v1 import BoundedStrategyArchiveV1, _require, _without


class PublicStrategyArchiveV3(BoundedStrategyArchiveV1):
    """同一原件只冻结一次；旧 BS_ 档案仍由原版本解释。"""

    def _path(self, strategy_id, name):
        _require(isinstance(strategy_id, str) and re.fullmatch(r'PS_[0-9a-f]{64}', strategy_id), 'ID_INVALID')
        path = self.root / strategy_id / name
        _require(path.resolve() == path and path.is_relative_to(self.root), 'PATH_REDIRECTED')
        return path

    @staticmethod
    def _audit(value):
        from .strategy_submission_v1 import _restore_frame
        from .rule_account_backend_v2 import rule_input_identity
        frozen, result, plan = value['frozen_input'], value['result'], value['plan']
        bundle = deepcopy(frozen['bundle'])
        for key in ('daily', 'turn', 'states'):
            bundle[key] = _restore_frame(bundle[key], frozen['frame_schemas'][key])
        _require(rule_input_identity(bundle, frozen['window']) == result['input_identity'] == frozen['input_identity'],
                 'PUBLIC_INPUT_CONFLICT')
        _require(result['strategy_plan'] == plan and plan['backend']['window'] == frozen['window'], 'PUBLIC_PLAN_CONFLICT')
        return reconstruct_account(bundle, frozen['window'], result,
            initial_cash=plan['backend']['initial_cash'], costs=plan['backend']['costs'],
            strategy_id=plan['strategy']['strategy_id'], rule=value['proposal'])

    def freeze(self, job_path, name):
        path = Path(job_path).absolute()
        _require(path.resolve() == path and path.name == 'JOB.json', 'PUBLIC_JOB_PATH_INVALID')
        verification = verify_job_evidence(path, name=name)
        _require(verification['status'] == 'PASS', 'PUBLIC_EVIDENCE_NOT_VERIFIED')
        job = json.loads(path.read_text(encoding='utf-8'))
        plan = job['plans'][name]
        proposal = plan['strategy']['parameters'].get('candidate_payload')
        _require(isinstance(proposal, dict) and proposal.get('version') == CAPABILITY, 'PUBLIC_V3_REQUIRED')
        frozen = json.loads(Path(job['items'][name]['loader_kwargs']['path']).read_text(encoding='utf-8'))
        result = json.loads((path.parent/(name+'_RESULT.json')).read_text(encoding='utf-8'))
        origin = {'job_path': str(path), 'candidate_id': name, 'plan_id': plan['plan_id'],
                  'result_sha256': verification['result_sha256'], 'input_identity': job['input_identity']}
        key = 'PS_' + stable_hash(origin)
        qualification = frozen.get('qualification') or {}
        synthetic = (qualification.get('test_fixture') is True or any('SYNTHETIC' in str(v).upper()
                      for v in frozen['bundle'].get('source_hashes', {}).values()))
        real_modeled = qualification.get('account_data_ready') is True and 'field_status' in qualification
        profile = 'SYNTHETIC' if synthetic else ('HISTORICAL_MODELED' if real_modeled else 'UNVERIFIED')
        evidence = {suffix: json.loads((path.parent/suffix).read_text(encoding='utf-8')) for suffix in
            ['JOB.json', 'CONFIRMATION.json', name+'_START.json', name+'_SETTLEMENT.json',
             name+'_WORKER.json', name+'_RESOURCE.json', name+'_INPUT_ACCESS.json', 'RESULTS_INDEX.json']}
        evidence['BUDGET.json'] = json.loads(Path(job['budget_path']).read_text(encoding='utf-8'))
        value = {'schema_version': 'PUBLIC_STRATEGY_ARCHIVE_V3', 'strategy_id': key, 'origin': origin,
            'proposal': proposal, 'plan': plan, 'result': result, 'frozen_input': frozen,
            'rule_identity': plan['strategy']['parameters']['rule_identity'],
            'source_profile': profile, 'source_identity': source_identity(),
            'qualification': 'NOT_ASSESSED', 'exposure': 'EXPLORATION_EXPOSED',
            'verification': verification, 'source_evidence': evidence,
            'source_evidence_hashes': {k: stable_hash(v) for k,v in evidence.items()}}
        _require(self._audit(value) == verification['account_audit'], 'PUBLIC_AUDIT_CONFLICT')
        with self._lock(key):
            target = self._path(key, 'ARCHIVE.json')
            if target.exists():
                existing = self.load(key)
                _require(all(existing[k] == v for k,v in value.items() if k not in ('source_identity', 'source_evidence', 'source_evidence_hashes')), 'PUBLIC_REFREEZE_CONFLICT')
                # 原事实已落盘而HEAD尚未提交，只补同一份已核验档案，不改资格。
                if not self._path(key, 'HEAD.json').exists():
                    _require(not self._path(key, 'REVOKED.json').exists(), 'COMMITTED_HEAD_CONFLICT')
                    self._commit_head(key, existing['archive_hash'])
                return existing
            value['archive_hash'] = stable_hash(value)
            _put(target, value)
            self._commit_head(key, value['archive_hash'])
            return value

    def load(self, strategy_id):
        value = _read(self._path(strategy_id, 'ARCHIVE.json'))
        _require(value['schema_version'] == 'PUBLIC_STRATEGY_ARCHIVE_V3'
                 and value['archive_hash'] == stable_hash(_without(value, 'archive_hash'))
                 and value['strategy_id'] == strategy_id == 'PS_' + stable_hash(value['origin']), 'PUBLIC_HASH_CONFLICT')
        _require(value['plan']['plan_id'] == stable_hash(_without(value['plan'], 'plan_id'))
                 and value['proposal'] == value['plan']['strategy']['parameters']['candidate_payload']
                 and value['proposal']['version'] == CAPABILITY
                 and value['rule_identity'] == value['plan']['strategy']['parameters']['rule_identity']
                 and value['qualification'] == 'NOT_ASSESSED'
                 and value['verification']['status'] == 'PASS'
                 and value['verification']['plan_id'] == value['origin']['plan_id'] == value['plan']['plan_id']
                 and value['verification']['input_identity'] == value['origin']['input_identity'] == value['result']['input_identity']
                 and value['result']['strategy_plan'] == value['plan']
                 and value['source_evidence']['JOB.json']['plans'][value['origin']['candidate_id']] == value['plan']
                 and value['source_evidence_hashes'] == {k: stable_hash(v) for k,v in value['source_evidence'].items()},
                 'PUBLIC_PROJECTION_CONFLICT')
        return value

    def review(self, strategy_id):
        value = self.load(strategy_id)
        try:
            strategy = ResearchRuleStrategyV3(value['proposal'], strategy_id=value['origin']['candidate_id'])
            executable = strategy.rule_identity == value['rule_identity'] and strategy.parameters == value['plan']['strategy']['parameters']
        except (ValueError, KeyError, TypeError):
            executable = False
        from .formal_rule_adapter_v3 import method_resolver
        backend = value['plan']['backend']
        scope = {key: backend[key] for key in ('initial_cash', 'max_positions', 'max_symbol_exposure_bps')}
        scope.update(symbols=backend['window']['symbols'], capability=CAPABILITY,
                     sessions=len(value['result']['daily_accounts']), rule_identity=value['rule_identity'])
        support = method_resolver(scope)
        report = {'strategy_id': strategy_id, 'archive_hash': value['archive_hash'],
            'source_profile': value['source_profile'], 'current_rule_executable': executable,
            'historical_account_state': 'COMPLETE', 'metrics': value['verification']['account_audit']['metrics'],
            'strategy_qualified': False, 'qualification': 'NOT_ASSESSED', 'method_support': support,
            'reason_codes': ['INDEPENDENT_CONFIRMATION_REQUIRED', *support['reason_codes']]}
        report['review_hash'] = stable_hash(report)
        return report

    def admission(self, strategy_id, *, purpose):
        result = super().admission(strategy_id, purpose=purpose)
        if result['source_profile'] == 'UNVERIFIED':
            result.update(allowed=False, strategy_qualified=False,
                          reason_codes=[*result['reason_codes'], 'PUBLIC_SOURCE_PROFILE_UNVERIFIED'])
        return result


def archive_for_ids(root, strategy_ids):
    if strategy_ids and all(isinstance(key, str) and key.startswith('PS_') for key in strategy_ids):
        return PublicStrategyArchiveV3(root)
    if any(isinstance(key, str) and key.startswith('PS_') for key in strategy_ids):
        raise ValueError('PUBLIC_ARCHIVE_MIXED_VERSIONS_UNSUPPORTED')
    return BoundedStrategyArchiveV1(root)
