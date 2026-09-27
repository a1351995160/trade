"""根据成本与阶段诊断生成候选、初筛，并交接独立评审。"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from chanlun_trader.research_factory.diagnosis_research_v2 import DiagnosisResearchV2
from chanlun_trader.research_factory.bounded_model_v1 import BoundedCodexInvokerV1
from chanlun_trader.presentation import ZhCNPresentation
from scripts.run_historical_process_research_v1 import build_bundle
from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity
from chanlun_trader.research_factory.research_rule_strategy_v2 import CAPABILITY


def freeze_manifest(data_root):
    window, bundle, _ = build_bundle(data_root)
    return {"profile": "HISTORICAL_MODELED", "candidate_capability": CAPABILITY,
            "window": window, "input_identity": rule_input_identity(bundle, window),
            "data_root": str(data_root.absolute()), "initial_cash": 1_000_000,
            "max_positions": 2, "max_symbol_exposure_bps": 5000}


def load_rules(manifest, strategy):
    window, bundle, _ = build_bundle(Path(manifest["data_root"]))
    if window != manifest["window"] or rule_input_identity(bundle, window) != manifest["input_identity"]:
        raise ValueError("DIAGNOSIS_V2_INPUT_CHANGED")
    bundle['input_identity'] = manifest['input_identity']
    return bundle, bundle["events"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['create', 'run', 'status', 'handoff', 'revoke'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--seed-root', type=Path)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--approval-statement')
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--calibration-path', type=Path)
    parser.add_argument('--not-before', type=int)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.operation == 'create':
        if not all((args.seed_root, args.data_root, args.approval_statement)):
            parser.error('create需要 --seed-root、--data-root 和 --approval-statement')
        service = DiagnosisResearchV2.create(args.root, seed_root=args.seed_root, data_root=args.data_root,
            input_manifest=freeze_manifest(args.data_root), approval_statement=args.approval_statement, attempts=args.attempts)
        result = service.status()
    else:
        service = DiagnosisResearchV2(args.root)
        if args.operation == 'run':
            result = service.status()
            while 'screening' not in result and result['status'] != 'BLOCKED':
                result = service.tick(loader=load_rules, invoker=BoundedCodexInvokerV1())
            if args.calibration_path and result['status'] == 'READY_FOR_INDEPENDENT_REVIEW':
                result = service.handoff(calibration_path=args.calibration_path,
                    not_before=args.not_before or int((datetime.now() + timedelta(days=1)).strftime('%Y%m%d')))
        elif args.operation == 'handoff':
            if not args.calibration_path or not args.not_before:
                parser.error('handoff需要 --calibration-path 和 --not-before')
            result = service.handoff(calibration_path=args.calibration_path, not_before=args.not_before)
        elif args.operation == 'revoke':
            service.session.revoke('OPERATOR_STOP')
            result = service.status()
        else:
            result = service.status()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('研究状态：' + ZhCNPresentation.status_name(result['status'], include_code=True))
        print('策略资格：尚未取得；独立评审须使用冻结后采集的新数据。')
        if 'screening' in result:
            print('初筛通过候选：' + ('、'.join(result['screening']['selected']) or '无'))
    return 1 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
