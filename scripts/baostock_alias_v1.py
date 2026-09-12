"""仅处理已批准的300114/302132训练期单对身份映射。"""
import os
from datetime import datetime, timezone

import run_baostock_account_v1 as driver


def evidence(root):
    path = root/'SECURITY_ALIAS_V1.json'
    if not path.exists():
        return None
    from chanlun_trader.research_factory.common import stable_hash
    item = driver.read(path)
    if item['identity'] != stable_hash({k:v for k,v in item.items() if k != 'identity'}):
        raise PermissionError('ALIAS_IDENTITY_CHANGED')
    if item['aliases'] != {'302132.SZ':'300114.SZ'}:
        raise PermissionError('ALIAS_SCOPE_CHANGED')
    for name, expected in item['files'].items():
        if driver.sha(name) != expected:
            raise PermissionError('ALIAS_EVIDENCE_CHANGED')
    return item


def execution_symbols(symbols, item):
    if item is None:
        return list(symbols)
    if symbols.count('300114.SZ') != 1 or symbols.count('302132.SZ') != 1:
        raise ValueError('ALIAS_MEMBERSHIP_CHANGED')
    return [s for s in symbols if s != '302132.SZ']


def canonical_hazards(hazards, item):
    if item is None:
        return hazards
    # 原29990条先核对，再合并两个来源的hazard，保留重复与全部事件。
    result = {s:list(days) for s,days in hazards.items()}
    result.setdefault('300114.SZ',[]).extend(result.pop('302132.SZ',[]))
    return result


def verify_states(states):
    columns = ['trade_date','listed','delisted','universe_member','eligibility_status',
               'st_status','suspension_status','board']
    a = states.loc[states.symbol == '300114.SZ',columns].sort_values('trade_date').reset_index(drop=True)
    b = states.loc[states.symbol == '302132.SZ',columns].sort_values('trade_date').reset_index(drop=True)
    if a.empty or a.trade_date.duplicated().any() or not a.equals(b):
        raise ValueError('ALIAS_HISTORICAL_STATE_CONFLICT')
    return len(a)


def register():
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    from chanlun_trader.research_factory.common import stable_hash
    import pyarrow.parquet as pq
    worker_resource_handshake()
    driver.active()
    root = driver.ROOT
    if (root/'SECURITY_ALIAS_V1.json').exists():
        raise PermissionError('ALIAS_ALREADY_REGISTERED_RECONCILE')
    thread = os.environ.get('CODEX_THREAD_ID')
    if not thread:
        raise PermissionError('ACTUAL_THREAD_ID_REQUIRED')
    triage_path = root/'BATCH14_SECURITY_IDENTITY_TRIAGE_V1.json'
    triage = driver.read(triage_path)
    files = dict(triage['files'])
    files[str(triage_path)] = driver.sha(triage_path)
    state_path = driver.BASE/'materialized-v3/HISTORICAL_POOL_AND_STATE.parquet'
    plan = driver.read(root/'READ_PLAN.json')
    expected = plan['files'][str(state_path)]
    files[str(state_path)] = expected
    for path, expected in files.items():
        if driver.sha(path) != expected:
            raise PermissionError('ALIAS_SOURCE_HASH_CHANGED')
    meta = pq.ParquetFile(state_path)
    col = meta.schema.names.index('trade_date')
    for i in range(meta.metadata.num_row_groups):
        stats = meta.metadata.row_group(i).column(col).statistics
        if not stats or not stats.has_min_max or not 20220722 <= int(str(stats.min).replace('-','')) <= int(str(stats.max).replace('-','')) <= 20240731:
            raise PermissionError('STATE_PHYSICAL_WINDOW_UNPROVEN')
    states = pq.read_table(state_path,filters=[('symbol','in',['300114.SZ','302132.SZ'])],use_threads=False).to_pandas(use_threads=False)
    state_count = verify_states(states)
    old_batch = plan['symbols'].index('300114.SZ')//200
    old_quality = driver.quality_path(old_batch)
    old_result = next(r for r in driver.read(old_quality)['results'] if r['symbol']=='300114.SZ')
    if not old_result['passed']:
        raise PermissionError('CANONICAL_PRICE_QUALITY_FAILED')
    files[str(old_quality)] = driver.sha(old_quality)
    decision = driver.SOURCE/'docs/baostock-batch14-identity-resolution-v1.md'
    files[str(decision)] = driver.sha(decision)
    item = {'aliases':{'302132.SZ':'300114.SZ'},'window':[20220722,20240731],
        'origin':'USER_EXPLICIT_SINGLE_PAIR_MAPPING_APPROVAL','approval_statement':'批准',
        'reader':thread,'recipient':'REQUESTING_USER','created_at':datetime.now(timezone.utc).isoformat(),
        'state_rows_equal':state_count,'files':files,'source_member_count':len(plan['symbols']),
        'execution_member_count':len(plan['symbols'])-1,'original_failure_preserved':True}
    item['identity'] = stable_hash(item)
    execution_symbols(plan['symbols'],item)
    driver.save(root/'SECURITY_ALIAS_V1.json',item)
    original = driver.read(root/'quality-ipo-v1/batch-14.json')
    results = [r if r['symbol'] != '302132.SZ' else {
        'symbol':'302132.SZ','canonical_symbol':'300114.SZ','passed':True,
        'status':'ALIAS_NOT_AN_INDEPENDENT_EXECUTION_MEMBER','original_response_passed':False,
        'canonical_quality_passed':True,'no_signal_or_outcome':True} for r in original['results']]
    driver.save(root/'quality-alias-v1/batch-14.json',{'results':results,
        'passed':all(r['passed'] for r in results),'alias_identity':item['identity'],
        'original_quality_sha256':driver.sha(root/'quality-ipo-v1/batch-14.json')})


if __name__ == '__main__':
    register()
