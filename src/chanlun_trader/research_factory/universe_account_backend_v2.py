"""有界逐日证据与完整收盘续跑；V1任务不进入此执行语义。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import pandas as pd

from .common import canonical_json, stable_hash
from .universe_account_backend_v1 import UniverseAccountBackendV1, UniversePaperEngineV1
from .universe_execution_artifacts_v1 import ArtifactSequence, DayArtifacts, hydrated_result
from .universe_execution_state_v2 import capture, restore, write_snapshot
from .universe_signal_scan_v2 import UniverseSignalScanV2
from .universe_signal_scan_v1 import decision_from_conditions
from .rule_account_backend_v2 import _snapshot, _stamp
from .portfolio_execution_v1 import build_portfolio_plan

VERSION = 'UNIVERSE_ACCOUNT_BACKEND_V2'


class SegmentBoundary(RuntimeError):
    """已提交的正常换段，不能写成终态FAILURE或消耗第二次试验。"""
    def __init__(self, message, *, phase='ACCOUNT', checkpoint_path=None):
        super().__init__(message)
        self.phase, self.checkpoint_path = phase, checkpoint_path


class UniversePaperEngineV2(UniversePaperEngineV1):
    def decisions(self, states):
        day = int(states[0]['date'])
        calendar = tuple(self.inputs.window['calendar'])
        index = self.inputs.session_index(day)
        strategy_id, strategy = next(iter(self.strategies.items()))
        exits = {}
        if strategy_id in self.rule_exits:
            for item in self.rule_exits[strategy_id].evaluate(self.engine.ledger, self.store, calendar, day):
                exits.setdefault(item.symbol, []).append(item)
        result, scan = [], []
        indices = {d: i for i, d in enumerate(calendar)}
        quantities, entries, last_exits = {}, {}, {}
        for lot in self.engine.ledger.lots.values():
            if lot.strategy_id == strategy_id and lot.remaining_quantity:
                quantities[lot.symbol] = quantities.get(lot.symbol, 0) + lot.remaining_quantity
                bought = indices[int(lot.buy_time.strftime('%Y%m%d'))]
                entries[lot.symbol] = min(entries.get(lot.symbol, bought), bought)
        if getattr(self.engine.ledger, 'version', '') == 'UniverseCorporateAccountingV2':
            last_exits = {key.split(':', 1)[1]: indices[d] for key, d in self.engine.ledger.last_exit_dates.items()
                          if key.startswith(strategy_id + ':')}
        else:
            totals = {}
            for trade in self.engine.ledger.trades:
                if trade.strategy_id == strategy_id:
                    totals[trade.symbol] = totals.get(trade.symbol, 0) + (trade.quantity if trade.side.value == 'BUY' else -trade.quantity)
                    if totals[trade.symbol] == 0 and trade.side.value == 'SELL':
                        last_exits[trade.symbol] = indices[int(trade.fill_time.strftime('%Y%m%d'))]
        for security in states:
            symbol = security['symbol']
            truth = self.scanner.at(symbol, day)
            qualification = self.inputs.scan_status(symbol, day)
            account = {'quantity': quantities.get(symbol, 0), 'entry_session_index': entries.get(symbol),
                       'last_exit_session_index': last_exits.get(symbol), 'sellable_quantity': 0}
            key = strategy_id, symbol
            decision = decision_from_conditions(strategy, truth, account=account,
                state=self.rule_states.get(key, {}), index=index)
            self.rule_states[key] = decision.state
            side = ('BUY' if decision.intent.weight > 0 else 'SELL') if decision.intent else 'HOLD'
            reason = decision.reason
            if side == 'BUY' and hasattr(strategy, 'selection') and not truth['score_ready']:
                side, reason = 'HOLD', 'SCORE_UNKNOWN'
            if side == 'BUY' and not qualification['entry_eligible']:
                side, reason = 'HOLD', 'SECURITY_NOT_ELIGIBLE'
            record = {'strategy_id': strategy_id, 'symbol': symbol, 'side': side, 'reason': reason,
                'target_weight': decision.intent.weight if decision.intent else 0.}
            from .universe_signal_funnel_v1 import signal_key
            record['signal_key'] = signal_key(strategy.rule_identity, strategy_id, symbol, day)
            if exits.get(symbol) and side != 'SELL':
                record.update(side='SELL', reason='RULE_RISK_EXIT_V3', target_weight=0.,
                              exit_lot_ids=sorted(item.lot_id for item in exits[symbol]))
            result.append(record)
            scan.append({'symbol': symbol, 'conditions': truth, 'qualification': qualification,
                         'reason': record['reason'], 'side': record['side']})
        if hasattr(strategy, 'selection'):
            from .universe_selection_v1 import rank_candidates
            candidates = rank_candidates([{'symbol': r['symbol'], 'score': self.scanner.at(r['symbol'], day)['score'],
                'score_ready': self.scanner.at(r['symbol'], day)['score_ready']}
                for r in result if r['side'] == 'BUY'], rule_identity=strategy.rule_identity,
                direction=strategy.selection['direction'])
            ranks = {r['symbol']: r['selection'] for r in candidates}
            for record in result:
                if record['symbol'] in ranks:
                    record['metadata'] = {'selection': ranks[record['symbol']]}
        self.scan_days.append({'date': day, 'target': len(self.symbols), 'processed': len(scan),
                              'rows': scan, 'identity': stable_hash(scan)})
        return result


class UniverseAccountBackendV2(UniverseAccountBackendV1):
    def __init__(self, window, *, execution_profile, backend_version=VERSION, **options):
        from .universe_execution_profile_v1 import validate_execution_profile
        if backend_version != VERSION:
            raise ValueError('UNIVERSE_BACKEND_VERSION_REQUIRED')
        super().__init__(window, **options)
        self.execution_profile = validate_execution_profile(execution_profile)
        count = len(self.window['calendar']) - self.window['calendar'].index(self.window['account_start'])
        if count != self.execution_profile['account_sessions']:
            raise ValueError('UNIVERSE_EXECUTION_PROFILE_SESSION_CONFLICT')

    def validate_strategy(self, strategy):
        from .research_rule_strategy_v3 import ResearchRuleStrategyV3
        from .research_rule_strategy_v4 import ResearchRuleStrategyV4
        if type(strategy) not in (ResearchRuleStrategyV3, ResearchRuleStrategyV4):
            raise ValueError('UNIVERSE_V3_OR_V4_STRATEGY_REQUIRED')

    def check(self, requirements):
        if requirements.asset != 'A_SHARE' or requirements.capabilities not in (
            ('RESEARCH_RULE_STRATEGY_V3', 'EXECUTION_STATE', 'PERSONAL_CASH_DIVIDEND'),
            ('RESEARCH_RULE_STRATEGY_V4', 'EXECUTION_STATE', 'PERSONAL_CASH_DIVIDEND')):
            raise ValueError('UNIVERSE_REQUIREMENTS_UNSUPPORTED')

    def describe(self):
        description = super().describe()
        paths = ['universe_account_backend_v2.py', 'universe_execution_profile_v1.py',
            'universe_execution_state_v2.py', 'universe_execution_artifacts_v1.py', 'universe_signal_scan_v2.py',
            'research_rule_strategy_v4.py', 'universe_selection_v1.py', 'universe_signal_funnel_v1.py',
            'universe_evidence_v2.py', 'universe_research_report_v2.py', 'universe_report_state_v1.py', 'research_evidence_v1.py']
        for name in paths:
            path = Path(__file__).with_name(name).resolve()
            description['source_hashes'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        description.update(backend=VERSION, execution_profile=deepcopy(self.execution_profile),
            result_schema='UNIVERSE_SHARDED_RESULT_V2', state_schema='UNIVERSE_EXECUTION_STATE_V2',
            allocation='SELL_THEN_PRIORITY_STRATEGY_FROZEN_SCORE_SYMBOL')
        return description

    def run(self, strategy, bundle, actions, guard, *, stop_after_date=None, segment_seconds=None):
        from .universe_account_inputs_v1 import _prepare_owned_universe_account_inputs_v1
        from .universe_evidence_v1 import reconstruct_universe_account
        started, timings = time.monotonic(), {}
        segment_seconds = segment_seconds if segment_seconds is not None else getattr(self, 'segment_seconds', None)
        self.validate_strategy(strategy)
        self.check(strategy.requirements)
        if list(actions) != list(bundle['events']):
            raise ValueError('UNIVERSE_ACTION_INPUT_CONFLICT')
        inputs = _prepare_owned_universe_account_inputs_v1(bundle, self.window,
            required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
        identity = inputs.input_identity
        if guard().get('input_identity') != identity:
            raise PermissionError('UNIVERSE_INPUT_NOT_AUTHORIZED')
        if not self.checkpoint_path:
            raise ValueError('UNIVERSE_V2_CHECKPOINT_REQUIRED')
        checkpoint = Path(self.checkpoint_path)
        artifact_root = checkpoint.parent / (strategy.strategy_id + '_DAYS')
        scanner = UniverseSignalScanV2(strategy, inputs, root=checkpoint.parent / (strategy.strategy_id + '_FEATURES'),
            batch_size=self.batch_size,
            deadline=started + segment_seconds if segment_seconds is not None else None)
        timings['input_and_features'] = time.monotonic() - started
        days = self.window['calendar']
        first = days.index(self.window['account_start'])
        account_days = days[first:]
        if first < 1:
            raise ValueError('UNIVERSE_PRIOR_SESSION_REQUIRED')
        policy = {'initial_cash': self.initial_cash, 'symbols': self.window['symbols'], 'portfolio': {
            'policy_id': VERSION, 'members': [{'strategy_id': strategy.strategy_id,
                'rule_identity': strategy.rule_identity, 'weight_bps': 10000, 'priority': 0}],
            'purpose': 'ENGINEERING_OBSERVATION', 'max_positions': self.max_positions,
            'max_symbol_exposure_bps': self.max_symbol_exposure_bps, 'max_buy_turnover_bps': 10000,
            'valid_until': (pd.Timestamp(str(days[-1]), tz='Asia/Shanghai') + pd.Timedelta(days=2)).isoformat()}}
        warmup = bundle['daily'].loc[bundle['daily'].date < account_days[0]]
        recent = warmup.sort_values('date').groupby('symbol', sort=False).tail(2).to_dict('records')
        header = {'execution_version': VERSION, 'policy': policy, 'warmup': {'bars': recent, 'turn': [], 'corporate_actions': []},
            'calendar': days, 'strategies': {strategy.strategy_id: {'proposal': strategy.payload}},
            'source_identity': stable_hash(self.describe()), 'header_id': identity, 'costs': self.costs,
            'company_actions': ('HISTORICAL_CORPORATE_ACTION_V2'
                if any(e['event_type'] != 'CASH_DIVIDEND' and e['record_date'] >= account_days[0] for e in actions)
                else 'HISTORICAL_CASH_DIVIDEND_V2'),
            'account_events': [e for e in actions if e['record_date'] >= account_days[0]], 'feature_events': actions}
        paper = UniversePaperEngineV2(header, inputs, scanner)
        admissions = {strategy.strategy_id: {'allowed': True, 'archive_hash': stable_hash(strategy.parameters),
            'strategy_qualified': False, 'source_profile': bundle['profile']}}
        execution_identity = stable_hash({'description': self.describe(), 'rule': strategy.rule_identity, 'scanner': scanner.identity})
        old = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None
        if old and self.execution_profile['purpose'] == 'ENGINEERING_CONTINUOUS_REFERENCE':
            raise PermissionError('UNIVERSE_REFERENCE_MUST_NOT_RESTORE')
        decisions, peak, drawdown = [], self.initial_cash, 0.
        if old:
            committed = ArtifactSequence(old['artifacts'], 'account')
            for index in range(len(committed)):
                committed.verify_bytes(index)
            decisions, peak, drawdown = restore(paper, old, execution_identity=execution_identity)
        store = DayArtifacts(artifact_root, identity=execution_identity, days=old['artifacts']['days'] if old else ())
        date_groups = bundle['daily'].groupby('date', sort=False).indices
        last_day = old['last_day'] if old else None
        for offset, day in enumerate(account_days):
            if last_day is not None and day <= last_day:
                continue
            guard()
            previous = days[first + offset - 1]
            plan = build_portfolio_plan(policy=paper.portfolio, decisions=decisions, ledger=paper.engine.ledger,
                admissions=admissions, input_identity=identity, decision_at=_stamp(previous, 15, 30), next_session=day)
            consumed_decisions = {'date': previous, 'decisions': deepcopy(decisions), 'plan': plan}
            full = bundle['daily'].iloc[date_groups.get(day, [])].to_dict('records')
            def states_at(hour, minute):
                rows = []
                for symbol in self.window['symbols']:
                    s = inputs.state(symbol, day, asof=_stamp(day, hour, minute))
                    if not s['state_known']:
                        raise ValueError('UNIVERSE_STATE_NOT_AVAILABLE:' + symbol)
                    rows.append({'symbol': symbol, 'date': day, 'listed': bool(s['listed']), 'delisted': bool(s['delisted']),
                        'is_st': s['st_status'] == 'ST', 'suspended': s['suspension_status'] != 'TRADING',
                        'board': s['board'], 'universe_member': bool(s['universe_member']), 'eligibility_status': s['eligibility_status']})
                return rows
            states = states_at(9, 30)
            eligible = {s['symbol'] for s in states if not s['suspended'] and s['listed'] and not s['delisted']}
            opened = []
            for row in full:
                if row['symbol'] in eligible:
                    prior = inputs.bar(row['symbol'], previous)
                    opened.append({**row, **{k: row['open'] for k in ('high', 'low', 'close')},
                        'volume': float(prior['volume']) if prior is not None else 0.,
                        'amount': float(prior['amount']) if prior is not None else 0.})
            paper.open(_snapshot(day, 'OPEN', opened, [], states), plan, admissions)
            decisions = paper.close(_snapshot(day, 'CLOSE', full, [], states_at(15, 0)))
            ledger = paper.engine.ledger
            positions = [{'strategy_id': strategy.strategy_id, 'symbol': symbol,
                'quantity': ledger.position_qty(strategy.strategy_id, symbol)} for symbol in sorted({t.symbol for t in ledger.trades})]
            stale = [{'symbol': p.symbol, 'status': 'STALE_VERIFIED_SUSPENSION'} for p in ledger.positions.values()
                if p.quantity and inputs.state(p.symbol, day)['suspension_status'] == 'SUSPENDED']
            account = {'date': day, 'cash': ledger.cash, 'equity': ledger.current_equity(),
                       'positions': positions, 'stale_valuations': stale}
            peak = max(peak, account['equity'])
            drawdown = max(drawdown, 1 - account['equity'] / peak)
            store.commit(day, {'account': account, 'scan': paper.scan_days.pop(), 'decision': consumed_decisions,
                'allocation_records': deepcopy(paper.allocations), 'skipped_intents': deepcopy(paper.skips)})
            paper.allocations.clear()
            paper.skips.clear()
            # 拍卖日状态只用于当日撮合；来源全历史保留在独立输入，避免对象积累。
            for key, rows in paper.master._states.items():
                paper.master._states[key] = rows[-1:]
            snapshot = capture(paper, last_day=day, execution_identity=execution_identity,
                artifacts=store.manifest(), decisions=decisions, peak=peak, drawdown=drawdown)
            write_snapshot(checkpoint, snapshot)
            last_day = day
            if guard().get('execution_pause_requested'):
                raise SegmentBoundary('UNIVERSE_PAUSE_REQUESTED')
            if stop_after_date == day:
                raise SegmentBoundary('UNIVERSE_CLOSE_INTERRUPTED')
            if (segment_seconds is not None and time.monotonic() - started >= segment_seconds and day != account_days[-1]):
                raise SegmentBoundary('UNIVERSE_NEXT_SEGMENT')
        guard()
        inputs.assert_unchanged()
        state = paper.state()
        result = {'result_schema': 'UNIVERSE_SHARDED_RESULT_V2', 'status': 'HISTORICAL_MODELED_ACCOUNT_COMPLETED',
            'profile': bundle['profile'], 'execution_version': VERSION, 'input_identity': identity,
            'execution_identity': execution_identity, 'execution_description': self.describe(), 'account_policy': policy,
            'board_policy_identity': bundle['board_policy_identity'], 'exit_price_policy': (
                self.describe()['supported_exit_price_policies'][1] if header['company_actions'] == 'HISTORICAL_CORPORATE_ACTION_V2'
                else self.describe()['exit_price_policy']), 'scanner_identity': scanner.identity,
            'scan_preparation': scanner.preparation, 'artifacts': store.manifest(), 'coverage': inputs.coverage,
            'fills': state['economic']['trades'], 'final_account_checkpoint': state,
            'metrics': {'net_return': state['equity'] / self.initial_cash - 1, 'max_drawdown': drawdown,
                'total_fees': sum(t['fee'] for t in state['economic']['trades']), 'trade_count': len(state['economic']['trades'])},
            'strategy_qualified': False, 'independent_confirmation_eligible': False,
            'cost_scenario': 'FULL_ACCOUNT_COST_SCENARIO', 'stage_seconds': timings,
            'limitations': ['历史状态可见时间和成本为模型；504日工程验收不授予策略资格。']}
        before_audit = time.monotonic()
        remaining = None if segment_seconds is None else max(0., segment_seconds - (time.monotonic() - started))
        audit = reconstruct_universe_account(bundle, self.window, hydrated_result(result), initial_cash=self.initial_cash,
            costs=self.costs, strategy_id=strategy.strategy_id, rule=strategy.payload,
            audit_checkpoint_path=checkpoint.parent / (strategy.strategy_id + '_AUDIT.json'), segment_seconds=remaining)
        result['stage_seconds']['independent_audit'] = time.monotonic() - before_audit
        result['reconciliation'] = {'passed': True, 'days': len(store.days), 'audit_identity': stable_hash(audit)}
        return json.loads(canonical_json(result))
