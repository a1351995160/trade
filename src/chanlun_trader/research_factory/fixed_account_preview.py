"""BaoStock固定探索合同的只读日内预览；不是实时行情接入或使用资格。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ..engine.ledger import PortfolioLedger
from ..engine.time_types import TradingCalendar
from .baostock_account_v1 import CONTRACT
from .common import stable_hash
from .daily_plan import account_identity
from .fixed_account_rules import FixedAccountRules


def _source_identity() -> dict[str, str]:
    root = Path(__file__).parent
    names = ['fixed_account_preview.py', 'fixed_account_rules.py', 'baostock_account_v1.py',
        'baostock_price_views_v1.py', 'degraded_execution_v2.py', 'degraded_train_v1.py',
        'train_account_runner_v1.py', 'daily_plan.py', 'common.py']
    paths = [root / name for name in names]
    paths.extend(sorted((root.parent / 'engine').glob('*.py')))
    return {path.relative_to(root.parent).as_posix(): sha256(path.read_bytes()).hexdigest() for path in paths}


def preview_fixed_account_plan(
    contract: Mapping[str, Any], calendar: TradingCalendar, ledger: PortfolioLedger,
    factor_rows: pd.DataFrame | None, hazards: Mapping[str, list[int]],
    unsupported_lots: Mapping[str, Any], *, plan_at: Any, account_stage: str,
    contract_identity: str, input_identity: str,
) -> dict[str, Any]:
    """只消费已准备的内存输入，不取数、运行broker、落盘或批准研究。

    POST_OPEN_ORDERS对应09:30且原有订单已处理、尚未选新买入的账户；
    AFTER_CLOSE对应15:30退出判断前的账户。收盘不提前生成次日买入。
    factor_rows=None表示缺数据；带完整列的空表表示已核验的空候选切片。
    输入身份由调用方绑定；内容hash不能代替数据来源与使用授权验证。
    """
    if dict(contract) != CONTRACT or contract_identity != stable_hash(CONTRACT):
        raise ValueError('FIXED_PREVIEW_CONTRACT_MISMATCH')
    if not isinstance(input_identity, str) or not input_identity.strip():
        raise ValueError('FIXED_PREVIEW_INPUT_IDENTITY_REQUIRED')
    phase_times = {'POST_OPEN_ORDERS': (9, 30), 'AFTER_CLOSE': (15, 30)}
    if account_stage not in phase_times:
        raise ValueError('FIXED_PREVIEW_ACCOUNT_STAGE_REQUIRED')
    rules = FixedAccountRules(contract)
    ts = rules.clock(plan_at, calendar, *phase_times[account_stage])
    day = int(ts.strftime('%Y%m%d'))
    if not 20220801 <= day <= 20240731:
        raise ValueError('FIXED_PREVIEW_OUTSIDE_TRAIN_WINDOW')
    if (not all(math.isfinite(value) and value >= 0 for value in (ledger.cash, ledger.reserved_cash))
            or ledger.reserved_cash > ledger.cash or ledger.check_invariants()):
        raise ValueError('FIXED_PREVIEW_ACCOUNT_INVALID')
    for lot in ledger.lots.values():
        if lot.remaining_quantity <= 0:
            continue
        entry = int(lot.buy_time.strftime('%Y%m%d'))
        if (lot.strategy_id != rules.strategy_id or lot.buy_time > ts
                or not calendar.contains(entry) or lot.entry_session != entry
                or lot.entry_session_index != calendar.date_index(entry)):
            raise ValueError('FIXED_PREVIEW_LOT_IDENTITY_INVALID')
    working = deepcopy(ledger)
    status, reason = 'NO_SIGNAL', 'NO_EXIT_DUE'
    signals, checks, exits, ranking = [], [], [], None
    if account_stage == 'POST_OPEN_ORDERS':
        if factor_rows is not None and (not factor_rows.empty) and (
                'signal_version' not in factor_rows or not factor_rows.signal_version.eq(contract['signal_version']).all()):
            raise ValueError('FIXED_PREVIEW_FACTOR_VERSION_MISMATCH')
        decision = rules.entries(factor_rows, calendar, working, hazards, unsupported_lots, ts)
        status, reason, ranking, checks = decision.status, decision.reason, decision.ranking, decision.checks
        signals = [asdict(signal) for signal in decision.signals]
    else:
        exits = [asdict(item) for item in rules.exits(calendar, working, unsupported_lots, ts)]
        if exits:
            status, reason = 'SIGNALS', 'EXIT_DUE'
        if unsupported_lots:
            status, reason = 'NOT_READY', 'ACCOUNTING_UNSUPPORTED_CORPORATE_ACTION'
    due = {item['lot_id']: item for item in exits}
    holdings = []
    for lot in sorted(working.lots.values(), key=lambda item: (item.symbol, item.buy_time, item.lot_id)):
        if lot.remaining_quantity <= 0:
            continue
        blocked = lot.lot_id in unsupported_lots
        pending = lot.exit_state in {'EXIT_DUE', 'SELL_PENDING', 'PARTIALLY_FILLED'}
        action, holding_reason = 'HOLD', 'NO_EXIT_DECISION_AT_THIS_PHASE'
        if blocked:
            action, holding_reason = 'BLOCKED', 'ACCOUNTING_UNSUPPORTED_CORPORATE_ACTION'
        elif lot.lot_id in due:
            action, holding_reason = 'EXIT', due[lot.lot_id]['reason_code']
        elif pending:
            action, holding_reason = 'SELL_PENDING', lot.exit_reason or 'EXIT_PENDING_RETRY'
        holdings.append({'lot_id': lot.lot_id, 'symbol': lot.symbol,
            'action': action, 'reason': holding_reason,
            'quantity': lot.remaining_quantity,
            'sellable_quantity': lot.remaining_quantity if lot.sellable_from <= ts else 0,
            'sellable_from': str(lot.sellable_from)})
    next_day = calendar.next_day(day)
    if exits and next_day is None:
        status, reason = 'NOT_READY', 'NEXT_SESSION_NOT_AVAILABLE'
    payload = {
        'schema_version': 'fixed-account-signal-preview-v1',
        'status': 'RESEARCH_PREVIEW' if status == 'SIGNALS' else status, 'reason': reason,
        'contract_identity': contract_identity, 'input_identity': input_identity,
        'plan_at': str(ts), 'account_stage': account_stage, 'account_identity': account_identity(ledger),
        'source_identity': _source_identity(), 'ranking': ranking, 'entry_checks': checks,
        'entry_signals': signals, 'exit_decisions': exits, 'holdings': holdings,
        'earliest_exit_execution': str(pd.Timestamp(str(next_day) + ' 09:30', tz='Asia/Shanghai'))
            if exits and next_day is not None else None,
        'execution_ready': False, 'usage_qualification': 'NOT_FOR_QUALIFICATION',
        'scope': 'TRAIN_EXPLORATORY_SIGNAL_ONLY', 'labels': list(contract['labels']),
        'decision_input_hash': stable_hash({'calendar': calendar.trading_days,
            'factor_rows': None if factor_rows is None else factor_rows.to_dict('records'),
            'hazards': hazards, 'unsupported_lots': unsupported_lots}),
    }
    return {**payload, 'plan_id': stable_hash(payload)}
