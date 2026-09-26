"""本次档案核验的复现脚本；仅描述已暴露账户，不授予任何资格。"""
import hashlib
import json
import math
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / 'src')]
from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
from chanlun_trader.research_factory.research_diagnostics_v1 import diagnose
from chanlun_trader.research_factory.formal_statistics_v2 import METHOD_HASH, METHOD_SPEC


def audit(root):
    archive_root = root / 'strategy-archives-20260926'
    store = BoundedStrategyArchiveV1(archive_root)
    rows = []
    paths = sorted(archive_root.glob('*/ARCHIVE.json'))
    if len(paths) != 5:
        raise ValueError('EXPECTED_FROZEN_FIVE_MEMBER_FAMILY')
    for path in paths:
        archive = store.load(path.parent.name)
        result = archive['evidence']['result']
        diagnostic = diagnose(result, trial_id=archive['origin']['candidate_id'])
        if diagnostic['account_state'] != 'COMPLETE':
            raise ValueError('ACCOUNT_NOT_RECONCILED')
        ledger = result['ledger']
        if ledger != result['chain']['independent_account_checks']:
            raise ValueError('LEDGER_PROJECTION_MISMATCH')
        initial = diagnostic['metrics']['initial_cash']
        previous = initial
        returns = []
        for row in ledger:
            equity = row['equity']
            if not math.isfinite(equity) or min(equity, previous) <= 0:
                raise ValueError('INVALID_EQUITY')
            returns.append(equity / previous - 1)
            previous = equity
        compounded = math.prod(1 + value for value in returns) - 1
        if abs(compounded - diagnostic['metrics']['net_return']) > 1e-12:
            raise ValueError('DAILY_RETURN_RECONCILIATION_FAILED')
        manifest = archive['evidence']['session']['input_manifest']
        rows.append({
            'candidate_id': archive['origin']['candidate_id'],
            'strategy_id': archive['strategy_id'], 'archive_hash': archive['archive_hash'],
            'archive_file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_profile': archive['source_profile'], 'exposure': archive['exposure'],
            'prior_exposure': manifest['prior_exposure'],
            'frozen_at': archive['evidence']['candidate']['frozen_at'],
            'account_dates': [ledger[0]['date'], ledger[-1]['date']],
            'account_state': diagnostic['account_state'], 'account_days': len(ledger),
            'metrics': diagnostic['metrics'], 'daily_net_simple_returns': returns,
            'return_compounding_delta': compounded - diagnostic['metrics']['net_return'],
            'complete_63_day_groups': len(ledger) // METHOD_SPEC['group_sessions'],
            'missing_account_days': max(0, METHOD_SPEC['sessions'] - len(ledger)),
            'daily_returns_kind': 'ABSOLUTE_ACCOUNT_RETURN_NOT_BENCHMARK_EXCESS',
            'applicability': 'NOT_ESTABLISHED', 'strategy_qualified': False,
        })
    reference_path = root / 'bounded-research-20260926/REFERENCE/RESULT.json'
    reference = json.loads(reference_path.read_text(encoding='utf-8-sig'))
    rule = reference['strategy_plan']['strategy']['parameters']['rule_definition']['strategy_id']
    authority = root / 'formal-assessment-authority-v1'
    plans = list(authority.glob('FA_*/PLAN.json'))
    if authority.exists() or plans:
        raise ValueError('AUTHORITY_CHANGED_REVIEW_NEW_EVIDENCE_BEFORE_REUSING_THIS_AUDIT')
    return {
        'status': 'INSUFFICIENT_EVIDENCE_NOT_STRATEGY_REJECTION',
        'method_hash': METHOD_HASH, 'required_sessions': METHOD_SPEC['sessions'],
        'required_groups': METHOD_SPEC['groups'], 'required_group_sessions': METHOD_SPEC['group_sessions'],
        'scope': str(archive_root), 'candidates': sorted(rows, key=lambda row: row['candidate_id']),
        'existing_reference_rule': rule,
        'reference_is_formal_buy_and_hold_benchmark': False,
        'reference_file_sha256': hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        'official_authority_path': str(authority), 'official_authority_exists': False,
        'formal_p_values_computed': False, 'independent_assessment_performed': False,
        'confirmation_budget_consumed_by_audit': 0, 'strategy_qualified': False,
        'limitations': [
            'REAL_MARKET_DATA_MODELED_BACKTEST_NOT_BROKER_STATEMENT',
            '118_DAYS_CANNOT_REPLACE_FIXED_504_DAY_DESIGN',
            'EXPOSED_EXPLORATION_CANNOT_BE_RELABELED_INDEPENDENT',
            'NO_PAIRED_FORMAL_BENCHMARK_IN_REVIEWED_ARCHIVES',
            'NO_REAL_CONFIRMATION_PLAN_OR_WINDOW_AT_CANONICAL_AUTHORITY',
            'DESCRIPTIVE_RETURNS_DO_NOT_PROVE_COMMON_MEAN_OR_GROUP_INDEPENDENCE',
            'NO_CLAIM_THAT_ALL_FILES_ON_E_DRIVE_ARE_EXPOSED',
        ],
    }


if __name__ == '__main__':
    value = audit(Path(sys.argv[1]))
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
