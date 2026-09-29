"""固定研究 worker：资源握手先于数据/核验领域组件导入。"""
import sys
from pathlib import Path
from chanlun_trader.synthetic_batch_resources import worker_resource_handshake


def main():
    handshake = worker_resource_handshake()
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.exploration_governance import immutable, read_json
    path = Path(sys.argv[1])
    request = read_json(path)
    if stable_hash(request) != sys.argv[2] or handshake['execution'] != {'request_hash': sys.argv[2]}:
        raise PermissionError('DIAGNOSIS_WORKER_REQUEST_CONFLICT')
    immutable(path.with_name(path.stem + '_ACCESS.json'), handshake)
    if request['kind'] == 'DATA':
        from chanlun_trader.research_factory.research_data_provider_v1 import ResearchDataProviderV1
        from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1
        def record(event):
            with path.with_name(path.stem + '_EXPOSURES.jsonl').open('a', encoding='utf-8') as stream:
                import json
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                stream.flush()
                import os
                os.fsync(stream.fileno())
        provider = ResearchDataProviderV1(request['roots'], record)
        # 此快照由维护者登记服务冻结，不从策略提案接受路径或执行入口。
        provider._datasets = {key: (Path(value[0]), value[1], value[2]) for key, value in request['datasets'].items()}
        submission = StrategySubmissionV1(provider, lambda ref: request['authority'], request['submission_root'],
                                           lambda: request['capabilities'])
        result = submission.freeze(request['request'], request['preview_identity'])
    elif request['kind'] == 'VERIFY':
        from chanlun_trader.research_factory.diagnosis_research_v3 import DiagnosisResearchV3
        import hashlib
        from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
        from scripts.run_strategy_account_v1 import report_account_job
        job_path = Path(request['task']['job_path'])
        job = read_json(job_path)
        checks = {name: verify_job_evidence(job_path, name=name) for name in job['plans']}
        evidence = {'job_sha256': hashlib.sha256(job_path.read_bytes()).hexdigest(), 'items': checks,
                    'advance_allowed': all(item.get('advance_allowed') is True for item in checks.values())}
        immutable(job_path.parent / 'VERIFICATION.json', evidence)
        if not evidence['advance_allowed']:
            raise ValueError('DIAGNOSIS_ACCOUNT_EVIDENCE_NOT_VERIFIED')
        result = DiagnosisResearchV3._screen(None, request['task'], request['name'], request['policy'])
        report_account_job(job_path)
    else:
        raise ValueError('DIAGNOSIS_WORKER_KIND_INVALID')
    immutable(path.with_name(path.stem + '_RESULT.json'), {'request_hash': sys.argv[2], 'result': result})


if __name__ == '__main__':
    main()
