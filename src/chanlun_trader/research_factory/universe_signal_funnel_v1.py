"""逐个机会连接冻结意图、订单和成交；各层使用自身唯一身份计数。"""
from collections import Counter
from collections.abc import Mapping

import pandas as pd

from .common import stable_hash
from .universe_report_state_v1 import OwnReportState, at_boundary, deadline, source_identity


VERSION = 'UNIVERSE_SIGNAL_FUNNEL_V1'


def signal_key(rule_identity, strategy_id, symbol, decision_session):
    if (not all(isinstance(value, str) and value for value in (rule_identity, strategy_id, symbol))
            or type(decision_session) is not int):
        raise ValueError('FUNNEL_SIGNAL_IDENTITY_INVALID')
    return 'UNIVERSE_SIGNAL_' + stable_hash([rule_identity, strategy_id, symbol, decision_session])


def is_signal_opportunity(row):
    """评分、持仓、冷却、现金和实际成交均不能缩短信号条件分母。"""
    truth = row['conditions']
    qualification = row.get('qualification', {})
    source_ready = qualification.get('condition_qualification_ready')
    if source_ready is None:
        # V4 总预热可能包含仅评分依赖；已知条件不能因评分预热而消失。
        source_ready = (qualification.get('status') in ('TRADING', 'WARMUP_INSUFFICIENT')
                        and qualification.get('state_known') is True
                        and qualification.get('bar_present') is True
                        and not qualification.get('gap_reasons')) if 'status' in qualification else qualification.get('signal_ready', True)
    return (source_ready is True
            and truth.get('condition_ready', truth.get('ready')) is True
            and truth.get('buy') is True and truth.get('market_filter') is True
            and truth.get('sell') is False)


def _day(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('FUNNEL_AWARE_SESSION_REQUIRED')
    return int(stamp.tz_convert('Asia/Shanghai').strftime('%Y%m%d'))


def _quantity(value):
    if type(value) is not int or value < 0:
        raise ValueError('FUNNEL_QUANTITY_INVALID')
    return value


def _side(value):
    return getattr(value, 'value', value)


def _reason(value):
    # 旧记录没有数量依据，不能把一个笼统拒绝补写成持仓名额已满。
    return 'NO_PERMITTED_QUANTITY_UNCLASSIFIED' if value == 'NO_PERMITTED_QUANTITY' else value


def build_signal_funnel_v1(*, rule_identity, strategy_id, calendar, scan_days, plans,
                           orders, fills, skipped_intents=(), allocations=(), strict=True):
    """输入必须是核账服务使用的原件；返回描述性漏斗，不授予策略资格。"""
    days = list(calendar)
    if (not days or any(type(day) is not int for day in days)
            or days != sorted(set(days)) or type(strict) is not bool):
        raise ValueError('FUNNEL_CALENDAR_INVALID')
    next_days = dict(zip(days, days[1:]))
    by_signal, scans = {}, []
    for scan in scan_days:
        day = scan['date']
        if day not in days:
            raise ValueError('FUNNEL_SCAN_SESSION_CONFLICT')
        symbols = set()
        for row in scan['rows']:
            symbol = row['symbol']
            key = signal_key(rule_identity, strategy_id, symbol, day)
            if symbol in symbols or key in by_signal:
                raise ValueError('FUNNEL_DUPLICATE_SIGNAL')
            symbols.add(symbol)
            item = {'signal_key': key, 'strategy_id': strategy_id, 'symbol': symbol,
                    'decision_session': day, 'opportunity': is_signal_opportunity(row),
                    'decision_side': row['side'], 'decision_reason': _reason(row['reason']),
                    'intent_ids': []}
            by_signal[key] = item
            scans.append(item)
        if scan.get('processed', len(symbols)) != len(symbols):
            raise ValueError('FUNNEL_SCAN_COUNT_CONFLICT')
        if scan.get('target', len(symbols)) != len(symbols):
            raise ValueError('FUNNEL_SCAN_SCOPE_INCOMPLETE')
    intents, excluded = {}, {}
    scan_sessions = {scan['decision_session'] for scan in scans}
    for entry in plans:
        plan = entry.get('plan', entry)
        day = _day(plan['decision_at'])
        if next_days.get(day) != plan['next_session']:
            raise ValueError('FUNNEL_NEXT_SESSION_CONFLICT')
        for item in plan.get('excluded', []):
            key = signal_key(rule_identity, item['strategy_id'], item['symbol'], day)
            if key in excluded:
                raise ValueError('FUNNEL_DUPLICATE_EXCLUSION')
            excluded[key] = _reason(item['reason'])
        for item in plan['intents']:
            iid = item['intent_id']
            key = signal_key(rule_identity, item['strategy_id'], item['symbol'], day)
            if iid in intents or (item.get('signal_key') is not None and item['signal_key'] != key):
                raise ValueError('FUNNEL_INTENT_IDENTITY_CONFLICT')
            if day in scan_sessions and key not in by_signal:
                raise ValueError('FUNNEL_INTENT_SIGNAL_MISSING')
            intents[iid] = {**item, 'signal_key': key, 'decision_session': day,
                            'execution_session': plan['next_session'], 'order_ids': [], 'skip_reasons': []}
            if key in by_signal:
                by_signal[key]['intent_ids'].append(iid)
    # SELL 的分笔子意图沿 parent_intent_id 连接，不能当成额外买入机会。
    def parent(iid, pid=None):
        chosen = pid or iid
        if chosen not in intents:
            matches = [key for key in intents if iid.startswith(key + ':')]
            if len(matches) != 1:
                raise ValueError('FUNNEL_INTENT_LINK_MISSING')
            chosen = matches[0]
        if iid != chosen and not (intents[chosen]['side'] == 'SELL' and iid.startswith(chosen + ':')):
            raise ValueError('FUNNEL_INTENT_LINK_CONFLICT')
        return chosen
    allocation_rows, seen_allocations, allocation_totals = [], set(), {}
    for allocation in allocations:
        iid = allocation['intent_id']
        if iid in seen_allocations:
            raise ValueError('FUNNEL_DUPLICATE_ALLOCATION')
        seen_allocations.add(iid)
        pid = parent(iid, allocation.get('parent_intent_id'))
        if allocation.get('execution_session') != intents[pid]['execution_session']:
            raise ValueError('FUNNEL_ALLOCATION_SESSION_CONFLICT')
        requested = _quantity(allocation['requested_quantity'])
        allocated = _quantity(allocation['allocated_quantity'])
        if allocated > requested:
            raise ValueError('FUNNEL_ALLOCATION_QUANTITY_CONFLICT')
        allocation_rows.append(dict(allocation))
        totals = allocation_totals.setdefault(pid, [0, 0])
        totals[0] += requested
        totals[1] += allocated
    skip_ids = set()
    for skip in skipped_intents:
        iid = skip['intent_id']
        if iid in skip_ids:
            raise ValueError('FUNNEL_DUPLICATE_SKIP')
        skip_ids.add(iid)
        pid = parent(iid, skip.get('parent_intent_id'))
        if skip.get('execution_session', intents[pid]['execution_session']) != intents[pid]['execution_session']:
            raise ValueError('FUNNEL_SKIP_SESSION_CONFLICT')
        intents[pid]['skip_reasons'].append(_reason(skip['reason']))
    order_rows = list(orders.values()) if isinstance(orders, Mapping) else list(orders)
    by_order = {}
    for order in order_rows:
        oid = order['order_id']
        metadata = order.get('metadata', {})
        pid = parent(order['intent_id'], metadata.get('parent_intent_id'))
        intent = intents[pid]
        if (oid in by_order or _day(order['created_at']) != intent['execution_session']
                or (order['strategy_id'], order['symbol'], _side(order['side'])) !=
                   (intent['strategy_id'], intent['symbol'], intent['side'])):
            raise ValueError('FUNNEL_ORDER_LINK_CONFLICT')
        native = metadata.get('funnel_version') == VERSION
        if strict and not native:
            raise ValueError('FUNNEL_ORIGINAL_QUANTITY_EVIDENCE_REQUIRED')
        if native and (metadata.get('signal_key') != intent['signal_key']
                       or metadata.get('decision_session') != intent['decision_session']
                       or metadata.get('execution_session') != intent['execution_session']):
            raise ValueError('FUNNEL_ORDER_SIGNAL_CONFLICT')
        requested = _quantity(metadata['requested_quantity'] if native else order['quantity'])
        allocated = _quantity(metadata['allocated_quantity'] if native else order['quantity'])
        if allocated > requested or order['intent_id'] in skip_ids:
            raise ValueError('FUNNEL_ORDER_QUANTITY_OR_SKIP_CONFLICT')
        by_order[oid] = {**order, 'parent_intent_id': pid, 'requested_quantity': requested,
                         'allocated_quantity': allocated, 'actual_quantity': 0, 'fill_ids': [],
                         'quantity_evidence': 'NATIVE' if native else 'LEGACY_UNVERIFIED'}
        intent['order_ids'].append(oid)
    seen_fills = set()
    for fill in fills:
        fid = fill.get('fill_id', fill.get('trade_id'))
        oid = fill['order_id']
        if not fid or fid in seen_fills or oid not in by_order:
            raise ValueError('FUNNEL_FILL_LINK_CONFLICT')
        seen_fills.add(fid)
        order = by_order[oid]
        if (_day(fill['fill_time']) != _day(order['created_at'])
                or (fill['strategy_id'], fill['symbol'], _side(fill['side'])) !=
                   (order['strategy_id'], order['symbol'], _side(order['side']))):
            raise ValueError('FUNNEL_FILL_SESSION_OR_OWNER_CONFLICT')
        quantity = _quantity(fill['quantity'])
        if not quantity:
            raise ValueError('FUNNEL_FILL_QUANTITY_INVALID')
        order['actual_quantity'] += quantity
        order['fill_ids'].append(fid)
    for order in by_order.values():
        actual, allocated = order['actual_quantity'], order['allocated_quantity']
        if actual != order['filled_quantity'] or actual > allocated:
            raise ValueError('FUNNEL_FILL_QUANTITY_CONFLICT')
        order['unrealized_quantity'] = allocated - actual
        order['execution_realization'] = actual / allocated if allocated else 0.
        order['requested_realization'] = actual / order['requested_quantity'] if order['requested_quantity'] else 0.
        order['disposition'] = ('REALIZED' if actual == allocated and actual else
                                'PARTIALLY_REALIZED' if actual else str(order['status']))
    for intent in intents.values():
        linked = [by_order[oid] for oid in intent['order_ids']]
        quantities = [sum(order[key] for order in linked) for key in ('requested_quantity', 'allocated_quantity')]
        native = allocation_totals.get(intent['intent_id'])
        if native is not None and intent['side'] == 'BUY':
            if linked and quantities != native:
                raise ValueError('FUNNEL_ALLOCATION_ORDER_QUANTITY_CONFLICT')
            quantities = native
        intent['requested_quantity'], intent['allocated_quantity'] = quantities
        intent['actual_quantity'] = sum(order['actual_quantity'] for order in linked)
    for row in scans:
        if not row['opportunity']:
            row['disposition'] = row['decision_reason']
            continue
        if row['decision_session'] == days[-1]:
            row['disposition'] = 'END_OF_OBSERVATION_NO_NEXT_SESSION'
        elif row['signal_key'] in excluded:
            row['disposition'] = excluded[row['signal_key']]
        elif not row['intent_ids']:
            row['disposition'] = row['decision_reason']
        else:
            linked = [intents[iid] for iid in row['intent_ids'] if intents[iid]['side'] == 'BUY']
            if not linked:
                # 账户退出可能覆盖仍成立的原始买入条件；卖出不能算作信号买入实现。
                row['disposition'] = row['decision_reason']
                row['blocked'] = True
                continue
            quantities = sum(item['actual_quantity'] for item in linked)
            allocated = sum(item['allocated_quantity'] for item in linked)
            if quantities:
                row['disposition'] = 'REALIZED' if quantities == allocated else 'PARTIALLY_REALIZED'
            else:
                reasons = [reason for item in linked for reason in item['skip_reasons']]
                statuses = [by_order[oid]['disposition'] for item in linked for oid in item['order_ids']]
                if not reasons and not statuses:
                    raise ValueError('FUNNEL_INTENT_DISPOSITION_MISSING')
                row['disposition'] = reasons[0] if reasons else statuses[0]
        row['blocked'] = row['disposition'] not in {'REALIZED', 'PARTIALLY_REALIZED', 'END_OF_OBSERVATION_NO_NEXT_SESSION'}
    opportunities = [row for row in scans if row['opportunity']]
    side_counts = {side: {'intents': sum(item['side'] == side for item in intents.values()),
        'orders': sum(_side(item['side']) == side for item in by_order.values()),
        'fills': sum(len(item['fill_ids']) for item in by_order.values() if _side(item['side']) == side)}
        for side in ('BUY', 'SELL')}
    body = {'version': VERSION, 'rule_identity': rule_identity, 'strategy_id': strategy_id,
            'calendar_identity': stable_hash(days), 'signals': scans,
            'intents': list(intents.values()), 'orders': list(by_order.values()),
            'allocations': allocation_rows,
            'layer_counts_by_side': side_counts,
            'counts': {'scan_rows': len(scans), 'opportunity_signals': len(opportunities),
                       'intents': len(intents), 'orders': len(by_order), 'fills': len(seen_fills),
                       'realized_signals': sum(row['disposition'] in ('REALIZED', 'PARTIALLY_REALIZED') for row in opportunities),
                       'tail_signals': sum(row['disposition'] == 'END_OF_OBSERVATION_NO_NEXT_SESSION' for row in opportunities)},
            'opportunity_dispositions': dict(sorted(Counter(row['disposition'] for row in opportunities).items()))}
    return {**body, 'identity': stable_hash(body)}


def build_signal_funnel_stream_v1(*, rule_identity, strategy_id, calendar, day_packets, event_sink=None,
                                 expected_decision_sessions=None, report_checkpoint_path=None,
                                 segment_seconds=None):
    """按收盘日独立核对后立即写分片，内存只保留当天明细和累计计数。"""
    limit = deadline(segment_seconds)
    if report_checkpoint_path is None and not callable(event_sink):
        raise ValueError('FUNNEL_STREAM_SINK_REQUIRED')
    if report_checkpoint_path is not None and event_sink is not None:
        raise ValueError('REPORT_CONTINUATION_OWNS_DETAIL_SINK')
    if report_checkpoint_path is None and segment_seconds is not None:
        raise ValueError('REPORT_SEGMENT_CHECKPOINT_REQUIRED')
    days = list(calendar)
    counts, dispositions, packets = Counter(), Counter(), 0
    side_counts = {side: Counter() for side in ('BUY', 'SELL')}
    order_ids, fill_ids, scan_sessions = set(), set(), []
    previous = None
    chain = stable_hash([VERSION, rule_identity, strategy_id, days])
    store = None
    if report_checkpoint_path is not None:
        frozen = getattr(day_packets, 'binding', None)
        if frozen is None and isinstance(day_packets, (list, tuple)):
            frozen = stable_hash(day_packets)
        if frozen is None:
            raise ValueError('FUNNEL_CONTINUATION_FROZEN_PACKETS_REQUIRED')
        binding = {'rule_identity': rule_identity, 'strategy_id': strategy_id, 'calendar': days,
            'expected_decision_sessions': expected_decision_sessions, 'frozen_packets': frozen,
            'source': source_identity(('universe_signal_funnel_v1.py', 'universe_report_state_v1.py',
                'universe_execution_artifacts_v1.py', 'common.py'))}
        initial = {'counts': {}, 'dispositions': {}, 'packets': 0,
            'side_counts': {side: {} for side in side_counts}, 'order_ids': [], 'fill_ids': [],
            'scan_sessions': [], 'previous': previous, 'chain': chain}
        store = OwnReportState(report_checkpoint_path, kind=VERSION, binding=binding, initial=initial,
                               source_artifacts=getattr(day_packets, 'source_artifacts', None))
        if store.complete:
            return store.output
        saved = store.state
        counts, dispositions, packets = Counter(saved['counts']), Counter(saved['dispositions']), saved['packets']
        side_counts = {side: Counter(values) for side, values in saved['side_counts'].items()}
        order_ids, fill_ids = set(saved['order_ids']), set(saved['fill_ids'])
        scan_sessions, previous, chain = saved['scan_sessions'], saved['previous'], saved['chain']
        if packets != len(store.details.days):
            raise ValueError('FUNNEL_CHECKPOINT_CURSOR_CONFLICT')
        at_boundary(limit, 'UNIVERSE_FUNNEL_NEXT_SEGMENT')
        day_packets = (day_packets.iter_from(packets) if hasattr(day_packets, 'iter_from')
                       else iter(day_packets[packets:]))
    for packet in day_packets:
        day = packet['decision_session']
        if day not in days or (previous is not None and day <= previous):
            raise ValueError('FUNNEL_STREAM_DAY_ORDER_CONFLICT')
        scans, plans = packet.get('scan_days', []), packet.get('plans', [])
        if (any(scan['date'] != day for scan in scans)
                or any(_day(entry.get('plan', entry)['decision_at']) != day for entry in plans)):
            raise ValueError('FUNNEL_STREAM_PACKET_SESSION_CONFLICT')
        if scans:
            scan_sessions.append(day)
        value = build_signal_funnel_v1(rule_identity=rule_identity, strategy_id=strategy_id,
            calendar=days, scan_days=scans, plans=plans, orders=packet.get('orders', {}),
            fills=packet.get('fills', []), skipped_intents=packet.get('skipped_intents', []),
            allocations=packet.get('allocations', []), strict=True)
        new_orders = {order['order_id'] for order in value['orders']}
        new_fills = {fid for order in value['orders'] for fid in order['fill_ids']}
        if order_ids & new_orders or fill_ids & new_fills:
            raise ValueError('FUNNEL_STREAM_DUPLICATE_EXECUTION_ID')
        order_ids.update(new_orders)
        fill_ids.update(new_fills)
        counts.update(value['counts'])
        for side in side_counts:
            side_counts[side].update(value['layer_counts_by_side'][side])
        dispositions.update(value['opportunity_dispositions'])
        chain = stable_hash([chain, day, value['identity']])
        detail = {'decision_session': day, 'funnel': value, 'chain_identity': chain}
        previous, packets = day, packets + 1
        if store is not None:
            store.state.update(counts=dict(counts), dispositions=dict(dispositions), packets=packets,
                side_counts={side: dict(values) for side, values in side_counts.items()},
                order_ids=sorted(order_ids), fill_ids=sorted(fill_ids), scan_sessions=scan_sessions,
                previous=previous, chain=chain)
            store.commit(day, [detail])
            at_boundary(limit, 'UNIVERSE_FUNNEL_NEXT_SEGMENT')
        else:
            event_sink(detail)
    if expected_decision_sessions is not None and scan_sessions != list(expected_decision_sessions):
        raise ValueError('FUNNEL_STREAM_SCAN_SCOPE_INCOMPLETE')
    body = {'version': VERSION, 'mode': 'DAY_STREAM', 'rule_identity': rule_identity,
            'strategy_id': strategy_id, 'calendar_identity': stable_hash(days),
            'packets': packets, 'counts': dict(counts),
            'layer_counts_by_side': {side: dict(values) for side, values in side_counts.items()},
            'opportunity_dispositions': dict(sorted(dispositions.items())),
            'detail_chain_identity': chain}
    if store is not None:
        body['details_manifest'] = store.details.manifest()
    output = {**body, 'identity': stable_hash(body)}
    return store.finish(output) if store is not None else output


def funnel_day_packets_v1(result, calendar):
    """把当前收盘扫描与下一开盘的冻结原件连接，仅保留相邻两天扫描。"""
    return _FunnelDayPackets(result, calendar)


class _FunnelDayPackets:
    """冻结输入可从自己的报告 cursor 读取，无须重扫已经提交的日期。"""
    def __init__(self, result, calendar):
        from .universe_execution_artifacts_v1 import ArtifactSequence
        if result.get('result_schema') != 'UNIVERSE_SHARDED_RESULT_V2':
            raise ValueError('FUNNEL_SHARDED_RESULT_REQUIRED')
        if list(calendar) != result['execution_description']['window']['calendar']:
            raise ValueError('FUNNEL_CALENDAR_IDENTITY_CONFLICT')
        self.reader = ArtifactSequence(result['artifacts'], 'scan')
        if not len(self.reader):
            raise ValueError('FUNNEL_EMPTY_ACCOUNT_ARTIFACTS')
        self.source_artifacts = result['artifacts']
        self.binding = stable_hash({key: value for key, value in result.items()
                                    if key not in {'daily_accounts', 'scan_days', 'decisions'}})
        self.orders_by_day, self.fills_by_day = {}, {}
        for oid, order in result['final_account_checkpoint']['economic']['orders'].items():
            if oid != order['order_id']:
                raise ValueError('FUNNEL_ORDER_IDENTITY_CONFLICT')
            self.orders_by_day.setdefault(_day(order['created_at']), {})[oid] = order
        for fill in result['fills']:
            self.fills_by_day.setdefault(_day(fill['fill_time']), []).append(fill)

    def _packet(self, scan, execution):
        if execution is None:
            return {'decision_session': scan['date'], 'scan_days': [scan], 'plans': []}
        entry = execution['decision']
        day = _day(entry['plan']['decision_at'])
        execution_day = entry['plan']['next_session']
        return {'decision_session': day, 'scan_days': [] if scan is None else [scan],
                'plans': [entry], 'orders': self.orders_by_day.get(execution_day, {}),
                'fills': self.fills_by_day.get(execution_day, []),
                'allocations': execution['allocation_records'],
                'skipped_intents': execution['skipped_intents']}

    def __iter__(self):
        return self.iter_from(0)

    def iter_from(self, cursor):
        if type(cursor) is not int or cursor < 0 or cursor > len(self.reader) + 1:
            raise ValueError('FUNNEL_CHECKPOINT_CURSOR_CONFLICT')
        if cursor == len(self.reader) + 1:
            return
        current = self.reader.read_day(0 if cursor == 0 else cursor - 1)
        if cursor == 0:
            yield self._packet(None, current)
        for index in range(max(0, cursor - 1), len(self.reader)):
            following = self.reader.read_day(index + 1) if index + 1 < len(self.reader) else None
            yield self._packet(current['scan'], following)
            current = following
