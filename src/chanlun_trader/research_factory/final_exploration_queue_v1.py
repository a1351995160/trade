"""同一初筛规则的最终探索晋级；不修改初筛原件，不调用模型。"""
from copy import deepcopy
import hashlib
from pathlib import Path

from .budget import BudgetExhaustedError
from .common import stable_hash
from .continuous_submission_v1 import submission_job_path
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock
from .research_data_provider_v1 import day
from .research_rule_strategy_v4 import ResearchRuleStrategyV4
from .universe_execution_profile_v1 import SEGMENTED_PROFILE, validate_execution_profile
from .universe_research_report_v2 import default_observation_plan

VERSION = 'FINAL_EXPLORATION_QUEUE_V1'


def _iso(value):
    value = str(day(value))
    return value[:4] + '-' + value[4:6] + '-' + value[6:]


def validate_final_exploration_template(template, initial, contract):
    """仅允许预先扩大原探索窗口及登记计算规格；模型无权改变模板。"""
    if template is None:
        return None
    from .strategy_submission_v1 import REQUEST_FIELDS
    fields = (REQUEST_FIELDS - {'symbols', 'rule', 'strategy_id'}) | {
        'version', 'universe_id', 'account_scope', 'execution_profile', 'observation_plan', 'phase'}
    variable = {'feature_start', 'account_start', 'account_end', 'execution_profile', 'observation_plan'}
    if (not isinstance(template, dict) or set(template) != fields
            or any(template[key] != initial[key] for key in fields - variable)
            or template['version'] != 'FULL_UNIVERSE_SUBMISSION_V4'
            or template['phase'] != 'EXPLORATION' or template['purpose'] != 'EXPLORATORY'):
        raise ValueError('FINAL_EXPLORATION_TEMPLATE_SCOPE_CHANGED')
    route = contract['scope']['data_routes']['EXPLORATION']
    start, account, end = (_iso(template[key]) for key in ('feature_start', 'account_start', 'account_end'))
    if (('dataset_id' in route and template['dataset_id'] != route['dataset_id'])
            or not route['start'] <= start <= account <= end <= route['end']
            or start > _iso(initial['feature_start']) or account > _iso(initial['account_start'])
            or end < _iso(initial['account_end'])):
        raise PermissionError('FINAL_EXPLORATION_TEMPLATE_WINDOW_OUTSIDE_FROZEN_SCOPE')
    profile = validate_execution_profile(template['execution_profile'])
    if (profile['profile_id'] != SEGMENTED_PROFILE or profile['purpose'] != 'RESEARCH_ACCOUNT'
            or profile['account_sessions'] < contract['final_criteria']['exploration_minimum_actual_sessions']
            or profile not in contract['scope']['execution_profiles']):
        raise PermissionError('FINAL_EXPLORATION_REGISTERED_504_PROFILE_REQUIRED')
    if template['observation_plan'] != default_observation_plan(template):
        raise ValueError('FINAL_EXPLORATION_OBSERVATION_WINDOW_CONFLICT')
    return deepcopy(template)


class FinalExplorationQueueV1:
    def __init__(self, research):
        self.research, self.submission = research, research.submission
        self.root = research.root / 'final_exploration'

    def _template(self, config):
        return validate_final_exploration_template(config.get('final_exploration_template'),
                                                   config['template'], config['contract'])

    def _eligible(self, records):
        return [row for row in records if row.get('status') == 'SCREENED'
                and (row.get('screen') or {}).get('passed') is True
                and (row.get('screen') or {}).get('final_exploration_ready') is not True]

    def _source(self, record):
        directory = self.research.root / record['candidate_id'].lower()
        screen = read_json(directory / 'SCREEN.json')
        proposal = read_json(directory / 'PROPOSAL.json')
        strategy = ResearchRuleStrategyV4(proposal, strategy_id=record['candidate_id'])
        if (screen.get('passed') is not True or stable_hash(screen) != record['screen']['identity']
                or strategy.rule_identity != record['rule_identity']):
            raise ValueError('FINAL_EXPLORATION_INITIAL_EVIDENCE_CHANGED')
        return proposal

    def evidence(self):
        """读取并核对独立晋级原件；不修复、不准备、不派发。"""
        config = self.research.config()
        template = self._template(config)
        records = {row['candidate_id']: row for row in self.research._records()}
        result = []
        for path in sorted(self.root.glob('CANDIDATE_*/EVIDENCE.json')):
            value = read_json(path)
            record = records.get(value['candidate_id'])
            if (value.get('version') != VERSION or record is None
                    or value.get('identity') != stable_hash({key: item for key, item in value.items() if key != 'identity'})
                    or value['source_record_identity'] != stable_hash(record)
                    or value['rule_identity'] != record['rule_identity']
                    or value['final_template_identity'] != stable_hash(template)
                    or value['batch_id'] != 'FINAL_' + record['candidate_id']
                    or value['screen_identity'] != stable_hash(value['screen'])
                    or value['status'] != ('FINAL_EXPLORATION_READY' if value['screen']['final_exploration_ready']
                                           else 'FINAL_EXPLORATION_FAILED')):
                raise ValueError('FINAL_EXPLORATION_EVIDENCE_IDENTITY_CONFLICT')
            self._source(record)
            for name, digest in value['artifacts'].items():
                if Path(name).name != name or hashlib.sha256((path.parent / name).read_bytes()).hexdigest() != digest:
                    raise ValueError('FINAL_EXPLORATION_ARTIFACT_CHANGED')
            request = read_json(path.parent / 'REQUEST.json')
            outcome = read_json(path.parent / 'PUBLIC_RESULT.json')
            reference = read_json(path.parent / 'TASK.json')
            task = self.submission._task(reference['task_id'])
            job_path = submission_job_path(self.submission, reference['task_id'], task)
            if (task['task_id'] != value['task_id'] or task['input_identity'] != value['input_identity']
                    or task['job_path'] != value['job_path']
                    or hashlib.sha256(job_path.read_bytes()).hexdigest() != task['job_sha256']
                    or request['rule'] != self._source(record)
                    or any(request.get(key) != item for key, item in template.items())
                    or outcome.get('input_identity') != task['input_identity']
                    or self.research._screen(path.parent, task, outcome, config) != value['screen']):
                raise ValueError('FINAL_EXPLORATION_CANONICAL_PUBLIC_EVIDENCE_CHANGED')
            result.append({**value, 'request': request, 'task': task, 'outcome': outcome,
                'evidence_path': str(path), 'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return result

    def verified_evidence(self, candidate_id):
        """返回含完整公共 request/task/outcome 的已核对晋级原件；缺失返回 None。"""
        return next((row for row in self.evidence() if row['candidate_id'] == candidate_id), None)

    def status(self):
        config = self.research.config()
        evidence = self.evidence()
        done = {row['candidate_id'] for row in evidence}
        pending = [row['candidate_id'] for row in self._eligible(self.research._records()) if row['candidate_id'] not in done]
        state = ('WAITING_FINAL_EXPLORATION_DATA' if self._template(config) is None
                 else 'READY' if pending else 'NO_FINAL_EXPLORATION_PENDING')
        return {'version': VERSION, 'status': state, 'pending': pending,
                'ready': [row['candidate_id'] for row in evidence if row['screen']['final_exploration_ready']],
                'failed': [row['candidate_id'] for row in evidence if not row['screen']['final_exploration_ready']],
                'dispatched_segments': 0, 'model_calls': 0, 'strategy_qualified': False}

    def advance(self):
        with ObjectiveMutationLock.for_resource(self.research.root / 'CONFIG.json'):
            config = self.research.config()
            template = self._template(config)
            evidence = self.evidence()
            # 原件先提交、批次结算后提交；中断恢复只补原批次结算。
            if evidence:
                with self.research.campaign._lock():
                    budget = self.research.campaign._budget()
                    for row in evidence:
                        budget.complete_batch(row['batch_id'])
            if template is None:
                return self.status()
            done = {row['candidate_id'] for row in evidence}
            record = next((row for row in self._eligible(self.research._records())
                           if row['candidate_id'] not in done), None)
            if record is None:
                return self.status()
            candidate, batch = record['candidate_id'], 'FINAL_' + record['candidate_id']
            directory = self.root / candidate
            proposal = self._source(record)
            advancing = False
            try:
                request_path = directory / 'REQUEST.json'
                if not request_path.exists():
                    request = {**template, 'rule': proposal, 'strategy_id': candidate}
                    request = self.submission.bind_research_request(request, phase='EXPLORATION',
                        candidate_identity=record['rule_identity'], batch_id=batch)
                    immutable(request_path, request)
                request = read_json(request_path)
                preview = self.submission.preview(request)
                immutable(directory / 'PREVIEW.json', preview)
                task_path = directory / 'TASK.json'
                if not task_path.exists():
                    task = self.submission.freeze(request, preview['preview_identity'])
                    immutable(task_path, {'task_id': task['task_id'], 'preview_identity': preview['preview_identity']})
                    return {'status': 'FINAL_EXPLORATION_PREPARING', 'candidate_id': candidate,
                            'dispatched_segments': 0, 'model_calls': 0}
                reference = read_json(task_path)
                advancing = True
                result = self.submission.advance(reference['task_id'])
                if result['status'] != 'ACCOUNT_VERIFIED':
                    return {**result, 'candidate_id': candidate, 'model_calls': 0}
                immutable(directory / 'PUBLIC_RESULT.json', {**result, 'dispatched_segments': 0})
                if result.get('dispatched_segments'):
                    return {'status': 'FINAL_EXPLORATION_REPORT_COMPLETED', 'candidate_id': candidate,
                            'dispatched_segments': 1, 'model_calls': 0}
                task = self.submission._task(reference['task_id'])
                screen = self.research._screen(directory, task, result, config)
                immutable(directory / 'SCREEN.json', screen)
                value = {'version': VERSION, 'candidate_id': candidate, 'batch_id': batch,
                    'rule_identity': record['rule_identity'], 'source_record_identity': stable_hash(record),
                    'final_template_identity': stable_hash(template), 'task_id': task['task_id'],
                    'job_path': task['job_path'], 'input_identity': task['input_identity'],
                    'screen': screen, 'screen_identity': stable_hash(screen),
                    'status': 'FINAL_EXPLORATION_READY' if screen['final_exploration_ready'] else 'FINAL_EXPLORATION_FAILED',
                    'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in directory.glob('*.json') if path.name != 'EVIDENCE.json'},
                    'interpretation': '同规则最终探索事实；不代表独立验证或总业务目标达成。'}
                value['identity'] = stable_hash(value)
                immutable(directory / 'EVIDENCE.json', value)
                with self.research.campaign._lock():
                    self.research.campaign._budget().complete_batch(batch)
                return {'status': value['status'], 'candidate_id': candidate,
                        'dispatched_segments': 0, 'model_calls': 0}
            except BudgetExhaustedError as exc:
                return {'status': 'WAITING_FINAL_EXPLORATION_AUTHORIZATION', 'waiting_reason': str(exc),
                        'candidate_id': candidate, 'dispatched_segments': 0, 'model_calls': 0}
            except FileNotFoundError as exc:
                if advancing:
                    raise
                return {'status': 'WAITING_FINAL_EXPLORATION_DATA', 'waiting_reason': type(exc).__name__,
                        'candidate_id': candidate, 'dispatched_segments': 0, 'model_calls': 0}
            except ValueError as exc:
                if advancing or not str(exc).startswith(('UNIVERSE_ACCOUNT_INPUT_NOT_READY:', 'UNIVERSE_WINDOW_',
                                                        'SUBMISSION_DATA_WINDOW_NOT_COVERED')):
                    raise
                return {'status': 'WAITING_FINAL_EXPLORATION_DATA', 'waiting_reason': str(exc),
                        'candidate_id': candidate, 'dispatched_segments': 0, 'model_calls': 0}
