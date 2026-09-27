"""统一生命周期服务：部署绑定固定业务入口，沿用其原授权和证据。"""
from copy import deepcopy
from pathlib import Path

from .bounded_research_v1 import _put, _read
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .research_lifecycle_jobs_v1 import KINDS, LifecycleJobsV1, _identity


_BINDINGS = {
    'RESEARCH': {'kind', 'root', 'implementation'},
    'PAPER': {'kind', 'root', 'snapshot_root', 'snapshots'},
    'DAILY_PLAN': {'kind', 'root'},
    'VALIDATION': {'kind', 'archive_root', 'batch_id', 'inputs'},
    'PORTFOLIO': {'kind', 'root'},
    'CAPTURE_OPEN': {'kind', 'snapshot_root', 'symbols', 'authority'},
    'CAPTURE_CLOSE': {'kind', 'snapshot_root', 'symbols', 'authority'},
    'DATA_QUALIFICATION': {'kind', 'manifests', 'exposure_roots'},
    'VALIDATION_PROTOCOL': {'kind', 'path'},
    'ARCHIVE': {'kind', 'archive_root', 'strategy_ids'},
}


class LifecycleServiceV2:
    def __init__(self, root, bindings, *, research_loader=None, synthetic_clock=None):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root or '..' in self.root.parts:
            raise ValueError('LIFECYCLE_ROOT_REDIRECTED')
        if not isinstance(bindings, dict):
            raise ValueError('LIFECYCLE_BINDINGS_REQUIRED')
        self.bindings = deepcopy(bindings)
        self.research_loader = research_loader  # 仅部署注入；JSON、HTTP 均无法指定代码。
        self.synthetic_clock = synthetic_clock
        for key, binding in self.bindings.items():
            _identity(key)
            paper_capture = isinstance(binding, dict) and binding.get('kind') == 'PAPER' and set(binding) == {'kind', 'root', 'snapshot_root', 'capture_jobs'}
            if not isinstance(binding, dict) or (set(binding) != _BINDINGS.get(binding.get('kind'), set()) and not paper_capture):
                raise ValueError('LIFECYCLE_BINDING_FIELDS')
            self._paths(binding)
        self.jobs = LifecycleJobsV1(self.root / 'lifecycle_jobs',
                                   dispatchers={kind: self for kind in KINDS}, synthetic_clock=synthetic_clock)

    def _path(self, value):
        path = Path(value).absolute()
        if path.resolve() != path or '..' in path.parts or not path.is_relative_to(self.root):
            raise ValueError('LIFECYCLE_BINDING_OUTSIDE_WORKSPACE')
        return path

    def _paths(self, binding):
        for key, value in binding.items():
            if key.endswith('_root') or key in {'root', 'path'}:
                self._path(value)
            elif key == 'exposure_roots':
                for root in value:
                    self._path(root)
            elif key in {'authority', 'inputs'}:
                if not isinstance(value, dict):
                    raise ValueError('LIFECYCLE_BINDING_OBJECT_REQUIRED')
                self._paths(value)

    def _paper(self, binding):
        from .forward_paper_v1 import ForwardPaperSessionV1
        return ForwardPaperSessionV1(binding['root'], clock=self.synthetic_clock)

    def _research(self, binding):
        if binding['implementation'] == 'DIAGNOSIS_V1':
            from .diagnosis_research_v1 import DiagnosisResearchV1
            return DiagnosisResearchV1(binding['root'])
        if binding['implementation'] == 'DIAGNOSIS_V2':
            from .diagnosis_research_v2 import DiagnosisResearchV2
            return DiagnosisResearchV2(binding['root'])
        if binding['implementation'] == 'BOUNDED_V2':
            from .bounded_research_v2 import BoundedResearchSessionV2
            return BoundedResearchSessionV2(binding['root'])
        raise ValueError('LIFECYCLE_RESEARCH_IMPLEMENTATION_UNSUPPORTED')

    def _scope(self, binding):
        kind = binding['kind']
        if kind.startswith('CAPTURE_'):
            authority = binding['authority']
            if authority.get('kind') not in {'PAPER', 'VALIDATION'}:
                raise ValueError('LIFECYCLE_CAPTURE_EXISTING_AUTHORITY_REQUIRED')
            return self._scope(authority)
        if kind == 'RESEARCH':
            research = self._research(binding)
            session = research.session if binding['implementation'] in {'DIAGNOSIS_V1', 'DIAGNOSIS_V2'} else research
            frozen = session.scope()
            objective, identity = frozen['objective_id'], frozen['scope_id']
            profile = frozen['input_manifest']['profile']
        elif kind in {'PAPER', 'DAILY_PLAN'}:
            frozen = self._paper(binding).header()
            identity, objective, profile = frozen['header_id'], 'PAPER', frozen['profile']
        elif kind == 'VALIDATION':
            from .formal_assessment_v1 import FormalAssessmentServiceV1
            frozen = FormalAssessmentServiceV1(binding['archive_root']).plan(binding['batch_id'])
            identity, objective, profile = binding['batch_id'], 'VALIDATION', frozen['profile']
        elif kind == 'PORTFOLIO':
            from .portfolio_qualification_v1 import PortfolioQualificationV1
            frozen = PortfolioQualificationV1(binding['root']).frozen()
            identity, objective, profile = frozen['portfolio_id'], 'PORTFOLIO', frozen['profile']
        else:
            raise ValueError('LIFECYCLE_READ_MODEL_NOT_EXECUTABLE')
        return {'objective_id': objective, 'session_id': identity, 'scope_hash': stable_hash(frozen)}, (
            'SYNTHETIC' if profile == 'SYNTHETIC' else 'REAL')

    def _binding(self, config):
        matching = [binding for binding in self.bindings.values()
                    if binding['kind'] == config['kind'] and stable_hash(binding) == config['config_hash']]
        if len(matching) != 1:
            raise ValueError('LIFECYCLE_FROZEN_BINDING_CHANGED')
        binding = matching[0]
        self._paths(binding)
        scope, profile = self._scope(binding)
        if config['scope_ref'] != scope or config['profile'] != profile:
            raise ValueError('LIFECYCLE_EXISTING_SCOPE_CHANGED')
        return binding

    def create_job(self, job_id, *, binding_id, expires_at, max_calls, stages, trading_calendar=()):
        binding = self.bindings[binding_id]
        scope, profile = self._scope(binding)
        return self.jobs.create(job_id, scope_ref=scope, kind=binding['kind'], config_hash=stable_hash(binding),
                                expires_at=expires_at, max_calls=max_calls, stages=stages,
                                trading_calendar=trading_calendar, profile=profile)

    def inspect_binding(self, binding_id):
        binding = self.bindings[binding_id]
        self._paths(binding)
        kind = binding['kind']
        if kind == 'RESEARCH':
            return self._research(binding).status()
        if kind in {'PAPER', 'DAILY_PLAN'}:
            paper = self._paper(binding)
            paper.lock().probe()
            header = paper.header()
            return paper._status(header, paper._records(header))
        if kind == 'VALIDATION':
            from .formal_assessment_v1 import FormalAssessmentServiceV1
            return FormalAssessmentServiceV1(binding['archive_root']).status(binding['batch_id'])
        if kind == 'PORTFOLIO':
            from .portfolio_qualification_v1 import PortfolioQualificationV1
            service = PortfolioQualificationV1(binding['root'])
            service.lock().probe()
            return {**service.readiness(), 'status': 'PORTFOLIO_READINESS', 'portfolio_qualified': False,
                    'qualification_note': '这里只展示准入准备；完整账户评审须显式执行。'}
        if kind == 'VALIDATION_PROTOCOL':
            from .validation_protocol_v2 import load_protocol
            return load_protocol(binding['path'])
        if kind == 'DATA_QUALIFICATION':
            from .research_data_qualification_v1 import audit_data_qualification, read_governance_exposures
            exposures = [record for root in binding['exposure_roots'] for record in read_governance_exposures(root)]
            return audit_data_qualification(binding['manifests'], exposures)
        if kind == 'ARCHIVE':
            from .strategy_qualification_v1 import BoundedStrategyArchiveV1
            archive = BoundedStrategyArchiveV1(binding['archive_root'])
            return {'status': 'STRATEGY_ADMISSIONS', 'strategies': {
                key: archive.admission(key, purpose='FORMAL_OBSERVATION') for key in binding['strategy_ids']}}
        if kind.startswith('CAPTURE_'):
            scope, profile = self._scope(binding)
            return {'status': 'CAPTURE_CONFIGURED', 'scope_ref': scope, 'profile': profile,
                    'symbols': binding['symbols'], 'background_enabled': False}
        raise ValueError('LIFECYCLE_UNKNOWN_BINDING')

    def inspect(self):
        views = {}
        for key in self.bindings:
            try:
                views[key] = self.inspect_binding(key)
            except (ValueError, OSError, KeyError, PermissionError) as exc:
                views[key] = {'status': 'BLOCKED', 'reason': str(exc)}
        jobs = {path.parent.name: self.jobs.status(path.parent.name)
                for path in sorted(self.jobs.root.glob('*/CONFIG.json'))}
        return {'version': 'LIFECYCLE_SERVICE_V2', 'bindings': views, 'jobs': jobs,
                'binding_catalog': {key: {'kind': item['kind']} for key, item in self.bindings.items()},
                'background_enabled': False, 'real_execution_authorized': False,
                'completion': {'engineering': 'AVAILABLE', 'tests': 'SEE_DELIVERY_REPORT',
                               'real_data': 'PER_BINDING_EVIDENCE', 'strategy_effectiveness': 'PER_CANONICAL_ADMISSION'}}

    def _snapshot(self, binding, stage):
        from .forward_snapshot_v1 import SnapshotStoreV1
        snapshot_id = binding.get('snapshots', {}).get(stage['key'])
        if 'capture_jobs' in binding:
            reference = binding['capture_jobs'].get(stage['key'])
            if not isinstance(reference, dict) or set(reference) != {'job_id', 'stage_key'}:
                raise ValueError('LIFECYCLE_CAPTURE_JOB_REFERENCE_REQUIRED')
            if not (self.jobs._root(reference['job_id']) / 'CONFIG.json').exists():
                return None
            capture_config, capture_state, pending = self.jobs._load(reference['job_id'])
            capture = self._binding(capture_config)
            if (not capture['kind'].startswith('CAPTURE_') or capture['snapshot_root'] != binding['snapshot_root']
                    or sorted(capture['symbols']) != sorted(self._paper(binding).header()['policy']['symbols'])):
                raise ValueError('LIFECYCLE_CAPTURE_JOB_SCOPE_CONFLICT')
            record = capture_state['stages'].get(reference['stage_key'])
            if pending or not record or record['status'] != 'COMPLETED':
                return None
            receipt_ref = record['receipt_ref']
            receipt = _read(self._path(receipt_ref['path']))
            if (stable_hash(receipt) != receipt_ref['hash'] or receipt['config_identity'] != stable_hash(capture_config)
                    or receipt['operation_id'] != record['operation_id']):
                raise ValueError('LIFECYCLE_CAPTURE_JOB_RECEIPT_CONFLICT')
            snapshot_id = receipt['reference']['snapshot_id']
        if snapshot_id is None:
            return None
        value = SnapshotStoreV1(binding['snapshot_root']).load(snapshot_id)
        if value['market_date'] != stage['trade_date']:
            raise ValueError('LIFECYCLE_SNAPSHOT_STAGE_DATE_CONFLICT')
        return value

    def readiness(self, config, stage):
        binding = self._binding(config)
        kind = binding['kind']
        status, reason = 'READY', 'EXISTING_SERVICE_RECHECK_REQUIRED'
        if kind == 'RESEARCH':
            research = self._research(binding)
            session = research.session if binding['implementation'] in {'DIAGNOSIS_V1', 'DIAGNOSIS_V2'} else research
            session.scope(active=True)
            current = research.status()
            if binding['implementation'] in {'BOUNDED_V2', 'DIAGNOSIS_V2'} and self.research_loader is None:
                status, reason = 'WAITING_DATA', 'V2_QUALIFIED_LOADER_NOT_BOUND'
            elif current['status'] in {'ATTEMPT_BUDGET_EXHAUSTED', 'BUDGET_EXHAUSTED'}:
                status, reason = 'BUDGET_EXHAUSTED', current['status']
            elif current['status'] not in {'IN_PROGRESS', 'READY'}:
                status, reason = 'WAITING_QUALIFICATION', current.get('reason', current['status'])
        elif kind == 'PAPER':
            paper = self._paper(binding)
            if paper.path('REVOKED.json').exists():
                status, reason = 'WAITING_QUALIFICATION', 'PAPER_REVOKED'
            elif self._snapshot(binding, stage) is None:
                status, reason = 'WAITING_DATA', 'FROZEN_STAGE_SNAPSHOT_REQUIRED'
        elif kind == 'DAILY_PLAN':
            paper = self._paper(binding)
            header = paper.header()
            current = paper._status(header, paper._records(header))
            plan = current.get('next_plan')
            if not plan or plan.get('next_session') != stage['trade_date']:
                status, reason = 'WAITING_DATA', 'DAILY_PLAN_FOR_REQUESTED_SESSION_REQUIRED'
            elif paper.path('REVOKED.json').exists():
                status, reason = 'WAITING_QUALIFICATION', 'PAPER_REVOKED'
        elif kind == 'VALIDATION':
            inputs = binding['inputs']
            if not inputs or len(inputs.get('snapshot_ids', [])) < 564 or len(inputs.get('open_snapshot_ids', [])) < 504:
                status, reason = 'WAITING_DATA', 'INDEPENDENT_WINDOW_INCOMPLETE'
        elif kind.startswith('CAPTURE_'):
            if config['profile'] != 'REAL':
                status, reason = 'WAITING_DATA', 'REAL_CAPTURE_NOT_ALLOWED_FOR_SYNTHETIC_SCOPE'
            authority = binding['authority']
            if authority['kind'] == 'PAPER':
                paper = self._paper(authority)
                if paper.path('REVOKED.json').exists():
                    status, reason = 'WAITING_QUALIFICATION', 'PAPER_REVOKED'
                if sorted(binding['symbols']) != sorted(paper.header()['policy']['symbols']):
                    raise ValueError('LIFECYCLE_CAPTURE_SYMBOL_SCOPE_CONFLICT')
                if stage['trade_date'] not in paper.header()['calendar']:
                    raise ValueError('LIFECYCLE_CAPTURE_OUTSIDE_PAPER_CALENDAR')
            else:
                from .formal_assessment_v1 import FormalAssessmentServiceV1
                formal = FormalAssessmentServiceV1(authority['archive_root'])
                plan = formal.plan(authority['batch_id'])
                if (sorted(binding['symbols']) != sorted(plan['symbols']) or
                        self._path(binding['snapshot_root']) != formal.path(authority['batch_id'], 'snapshots')):
                    raise ValueError('LIFECYCLE_CAPTURE_FORMAL_SCOPE_CONFLICT')
                if stage['trade_date'] < plan['not_before']:
                    raise ValueError('LIFECYCLE_CAPTURE_BEFORE_FORMAL_START')
            # 原采集入口仍核验实际交易日、时段、提供者和数据完整性。
        elif kind == 'PORTFOLIO':
            from .portfolio_qualification_v1 import PortfolioQualificationV1
            ready = PortfolioQualificationV1(binding['root']).readiness()
            if not ready['observation_bound']:
                status, reason = 'WAITING_DATA', 'PORTFOLIO_OBSERVATION_NOT_BOUND'
        return {'status': status, 'reason': reason}

    def _receipt_path(self, operation_id):
        if len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id):
            raise ValueError('LIFECYCLE_OPERATION_ID_INVALID')
        return self._path(self.root / 'lifecycle_receipts' / (operation_id + '.json'))

    def _result(self, config, operation_id, reference):
        receipt = {'operation_id': operation_id, 'config_identity': stable_hash(config),
                   'reference': reference, 'grants_qualification': False}
        _put(self._receipt_path(operation_id), receipt)
        return {'status': 'COMPLETED', 'receipt_ref': {'path': str(self._receipt_path(operation_id)),
                'hash': stable_hash(receipt)}, 'reason': 'CANONICAL_SERVICE_RESULT_RECORDED'}

    def execute(self, config, stage, operation_id):
        binding = self._binding(config)
        readiness = self.readiness(config, stage)
        if readiness['status'] != 'READY':
            return {'status': 'UNRESOLVED', 'receipt_ref': None, 'reason': readiness['reason']}
        kind = binding['kind']
        if kind.startswith('CAPTURE_'):
            from .forward_snapshot_v1 import SnapshotStoreV1
            result = SnapshotStoreV1(binding['snapshot_root']).capture_tdx(phase=kind.removeprefix('CAPTURE_'), symbols=binding['symbols'])
            if result['market_date'] != stage['trade_date']:
                raise ValueError('LIFECYCLE_CAPTURE_STAGE_DATE_CONFLICT')
            reference = {'snapshot_id': result['snapshot_id'], 'snapshot_hash': result['snapshot_hash']}
        elif kind == 'PAPER':
            snapshot = self._snapshot(binding, stage)
            result = self._paper(binding).ingest(binding['snapshot_root'], snapshot['snapshot_id'])
            reference = {'header_id': result['session_id'], 'snapshot_id': snapshot['snapshot_id']}
        elif kind == 'RESEARCH':
            from .bounded_model_v1 import BoundedCodexInvokerV1
            from scripts.run_bounded_research_v1 import load_s1
            research = self._research(binding)
            result = research.tick(loader=self.research_loader or load_s1, invoker=BoundedCodexInvokerV1())
            reference = {'status_hash': stable_hash(result), 'scope_ref': config['scope_ref'], 'status': result['status']}
        elif kind == 'VALIDATION':
            from .formal_assessment_v1 import FormalAssessmentServiceV1
            result = FormalAssessmentServiceV1(binding['archive_root']).run(binding['batch_id'], **binding['inputs'])
            reference = {'batch_id': binding['batch_id'], 'result_hash': stable_hash(result)}
        elif kind == 'PORTFOLIO':
            from .portfolio_qualification_v1 import PortfolioQualificationV1
            result = PortfolioQualificationV1(binding['root']).review()
            reference = {'portfolio_id': result['portfolio_id'], 'result_hash': stable_hash(result), 'status': result['status']}
        elif kind == 'DAILY_PLAN':
            paper = self._paper(binding)
            paper.lock().probe()
            header = paper.header()
            result = paper._status(header, paper._records(header))
            reference = {'header_id': header['header_id'], 'plan_hash': stable_hash(result['next_plan']),
                         'last_snapshot': result['last_snapshot']}
        else:
            raise ValueError('LIFECYCLE_ACTION_UNSUPPORTED')
        return self._result(config, operation_id, reference)

    def reconcile(self, config, stage, operation_id):
        binding = self._binding(config)
        path = self._receipt_path(operation_id)
        if path.exists():
            receipt = _read(path)
            if receipt['config_identity'] != stable_hash(config) or receipt['operation_id'] != operation_id:
                raise ValueError('LIFECYCLE_RECEIPT_CONFLICT')
            # 原回执只能结算调度阶段，不升级任何策略资格。
            return {'status': 'COMPLETED', 'receipt_ref': {'path': str(path), 'hash': stable_hash(receipt)},
                    'reason': 'EXISTING_SERVICE_RECEIPT'}
        if binding['kind'] == 'PAPER':
            snapshot = self._snapshot(binding, stage)
            paper = self._paper(binding)
            with paper.lock():
                header = paper.header()
                records = paper._records(header, recover=True)
                if snapshot and any(row['snapshot']['snapshot_id'] == snapshot['snapshot_id'] for row in records):
                    return self._result(config, operation_id, {'header_id': header['header_id'], 'snapshot_id': snapshot['snapshot_id']})
        elif binding['kind'] == 'VALIDATION':
            from .formal_assessment_v1 import FormalAssessmentServiceV1
            formal = FormalAssessmentServiceV1(binding['archive_root'])
            if formal.path(binding['batch_id'], 'REPORT.json').exists() or formal.path(binding['batch_id'], 'FAILED.json').exists():
                result = formal.status(binding['batch_id'])
                return self._result(config, operation_id, {'batch_id': binding['batch_id'], 'result_hash': stable_hash(result)})
        return {'status': 'UNRESOLVED', 'receipt_ref': None, 'reason': 'CANONICAL_COMMIT_NOT_PROVEN_NO_AUTOMATIC_RETRY'}

    def action_preview(self, action, payload):
        if action not in {'create', 'start', 'pause', 'resume', 'tick'} or not isinstance(payload, dict):
            raise ValueError('LIFECYCLE_ACTION_INVALID')
        if action != 'create' and set(payload) != {'job_id'}:
            raise ValueError('LIFECYCLE_ACTION_FIELDS')
        state = self.inspect() if action == 'create' else self.jobs.status(payload['job_id'])
        value = {'action': action, 'payload': deepcopy(payload), 'state': state,
                 'bindings_hash': stable_hash(self.bindings)}
        identity = deepcopy(value)
        def stable_state(item):
            if isinstance(item, dict):
                # Budget.snapshot 的读取时间不是预算变更；其余事实仍参与确认。
                if item.get('schema_version') == 'search-budget-registry-v1':
                    item.pop('updated_at', None)
                for child in item.values():
                    stable_state(child)
            elif isinstance(item, list):
                for child in item:
                    stable_state(child)
        stable_state(identity['state'])
        return {**value, 'preview_hash': stable_hash(identity)}

    def perform(self, policy, *, action, payload, preview_hash, confirmed):
        if not policy.governance_allowed or confirmed is not True:
            raise PermissionError('LIFECYCLE_EXPLICIT_GOVERNED_CONFIRMATION_REQUIRED')
        with ObjectiveMutationLock.for_resource(self.root / 'lifecycle-control'):
            preview = self.action_preview(action, payload)
            if preview['preview_hash'] != preview_hash:
                raise ValueError('LIFECYCLE_PREVIEW_CHANGED')
            # Web 现有权限只允许合成治理，不借新入口扩大真实运行权限。
            if action == 'create':
                _, profile = self._scope(self.bindings[payload['binding_id']])
            else:
                config, _, _ = self.jobs._load(payload['job_id'])
                profile = config['profile']
            if profile != 'SYNTHETIC':
                raise PermissionError('LIFECYCLE_REAL_WEB_MUTATION_NOT_AUTHORIZED')
            return self.create_job(**payload) if action == 'create' else getattr(self.jobs, action)(payload['job_id'])
