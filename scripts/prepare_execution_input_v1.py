"""按冻结清单物化TRAIN输入；不生成收益标签、订单、回测或预算。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from chanlun_trader.data.tdx.execution_input import read_warmup_day_window, classify_state
from chanlun_trader.research.guard import ResearchDataAccessGuard, ResearchDataAccessGuardConfig
from chanlun_trader.research.io_safety import GuardedResearchReader
from chanlun_trader.research.unified_factor import AsOfDataView, FactorCompiler, UnifiedFactorRegistry


ROOT = Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1')


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def prepare(plan_name='INPUT_READ_PLAN_V1.json', output_name='materialized-v1'):
    started = time.monotonic()
    plan_path = ROOT/plan_name
    plan = json.loads(plan_path.read_bytes())
    out = ROOT/output_name
    out.mkdir(exist_ok=False)
    warm, train = plan['warmup_dates'], plan['train_dates']
    archived_calendar = json.loads((ROOT/'get_trading_calendar_sample_response.json').read_bytes())
    actual = [int(d) for d in archived_calendar['Date'] if int(d) < 20220801][-6:]
    if actual != warm or train[0] != 20220801 or train[-1] != 20240731:
        raise ValueError('CALENDAR_IDENTITY_CONFLICT')
    if digest(plan['snapshot']) != plan['snapshot_expected_sha256']:
        raise ValueError('SNAPSHOT_HASH_CONFLICT')
    if digest(plan['calendar']) != plan['calendar_sha256']:
        raise ValueError('CALENDAR_HASH_CONFLICT')
    sessions = warm + train
    write_json(out/'CALENDAR.json', {'sessions':sessions,'warmup_sessions':warm,
        'train_sessions':train,'warmup_source':str(ROOT/'get_trading_calendar_sample_response.json'),
        'warmup_response_sha256':digest(ROOT/'get_trading_calendar_sample_response.json'),
        'train_source':plan['calendar'],'train_source_sha256':plan['calendar_sha256']})
    audit = []
    guard = ResearchDataAccessGuard(ResearchDataAccessGuardConfig(research_end=20240731))
    frame = GuardedResearchReader(guard, audit_sink=audit.append).read_parquet(
        plan['snapshot'], start_date=20220801, end_date=20240731)
    warm_frames, failures = [], []
    for symbol, source in plan['warmup_sources']:
        path = Path(source['path'])
        if path.resolve() != path:
            raise ValueError('SOURCE_REDIRECTED')
        try:
            stat = path.stat()
            # 客户端启动可能更新原文件；与已冻结来源身份冲突时不读取价格。
            if (stat.st_size,stat.st_mtime_ns) != (source['size'],source['mtime_ns']):
                raise ValueError('SOURCE_CHANGED_SINCE_FIXED_PLAN')
            rows, item = read_warmup_day_window(path, warm)
            item['symbol'] = symbol
            audit.append(item)
            rows['symbol'] = symbol
            warm_frames.append(rows)
        except (OSError, ValueError) as exc:
            failures.append({'symbol':symbol,'path':str(path),'error':str(exc)})
    warm_frame = pd.concat(warm_frames, ignore_index=True) if warm_frames else pd.DataFrame()
    daily = pd.concat([warm_frame,frame], ignore_index=True)
    # pandas 3合并空片段可能保留object；原始解码数值必须显式验证为数值，不填缺口。
    for column in ['open','high','low','close','amount_encoded','volume_encoded']:
        daily[column] = daily[column].astype(float)
    if daily.duplicated(['symbol','date']).any() or not set(daily.date)<=set(sessions):
        raise ValueError('DAILY_KEYS_CONFLICT')
    daily['source_published_at'] = None
    daily['historical_availability_evidence'] = 'UNKNOWN'
    daily['corporate_action_evidence'] = 'UNKNOWN'
    daily['modeled_available_at'] = pd.to_datetime(daily.date.astype(str)).dt.tz_localize('Asia/Shanghai') + pd.Timedelta(hours=15,microseconds=1)
    daily['ingested_at'] = datetime.now(timezone.utc).isoformat()
    daily['price_decode_rule'] = 'TDX_UINT32_CENTS_V1'
    daily['volume_unit_evidence'] = 'UNKNOWN'
    daily['amount_unit_evidence'] = 'UNKNOWN'
    daily['data_role'] = np.where(daily.date<20220801,'WARMUP_ONLY','TRAIN')
    daily.to_parquet(out/'DAILY.parquet',index=False)
    write_json(out/'DAILY_READ_AUDIT.json',{'reads':audit,'warmup_source_failures':failures})
    price_keys = set(zip(daily.symbol,daily.date))
    del frame, warm_frames
    print(json.dumps({'stage':'daily','rows':len(daily),'warmup_rows':len(warm_frame),'source_conflicts':len(failures)}),flush=True)
    state_counts, pit_audit, missing = Counter(), [], []
    writer = None
    symbols = set()
    try:
        for d,path_text in zip(sessions,plan['pit_partitions']):
            path = Path(path_text)
            if not path.is_file():
                missing.append(path_text)
                continue
            if path.resolve() != path:
                raise ValueError('PIT_REDIRECTED')
            records = []
            sha = hashlib.sha256()
            with path.open('rb') as f:
                for line in f:
                    sha.update(line)
                    row = json.loads(line)
                    if int(row['trade_date'].replace('-','')) != d:
                        raise ValueError('PIT_PARTITION_CONFLICT')
                    if row.get('exchange') not in {'SH','SZ'}:
                        continue
                    row['input_state_diagnostic'] = classify_state(row)
                    row['source_lineage'] = json.dumps(row['source_lineage'],sort_keys=True)
                    row['reason_codes'] = json.dumps(row['reason_codes'])
                    row['price_present'] = (row['symbol'],d) in price_keys
                    state_counts[row['input_state_diagnostic']] += 1
                    symbols.add(row['symbol'])
                    records.append(row)
            table = pa.Table.from_pylist(records)
            if writer is None:
                writer = pq.ParquetWriter(out/'HISTORICAL_POOL_AND_STATE.parquet',table.schema)
            writer.write_table(table)
            pit_audit.append({'path':path_text,'sha256':sha.hexdigest(),'rows':len(records),'date':d})
    finally:
        if writer: writer.close()
    print(json.dumps({'stage':'states','rows':sum(state_counts.values()),'states':state_counts}),flush=True)
    write_json(out/'STATE_READ_AUDIT.json',{'partitions':pit_audit,'missing':missing})
    registry = UnifiedFactorRegistry.read(Path(plan['factor_registry']))
    definition = registry.get('RETURN_5D')
    expected = {'op':'pct_change','args':[{'op':'field','field':'close'}],'window':5,'min_periods':6}
    if definition.operator_graph != expected or definition.feature_price_mode != 'RETURN_ONLY':
        raise ValueError('FACTOR_DEFINITION_CONFLICT')
    write_json(out/'FACTOR_BINDING.json',{'registry':plan['factor_registry'],
        'registry_sha256':digest(plan['factor_registry']),'factor_id':'RETURN_5D',
        'operator_graph':expected,'feature_price_mode_unchanged':'RETURN_ONLY',
        'meaning':'RAW close ratio; not corporate-action invariant',
        'calendar_mask':'require six positive finite closes on six independent sessions; no compressed gaps',
        'compiler_available_at':'date key only; not historical availability evidence'})
    values_writer, factor_counts = None, Counter()
    try:
        for symbol, group in daily.groupby('symbol',sort=True):
            panel = group.set_index('date')[['close']].reindex(sessions).rename_axis('date').reset_index()
            panel['symbol'] = symbol
            valid = np.isfinite(panel.close) & (panel.close>0)
            complete = valid.rolling(6,min_periods=6).sum().eq(6)
            result = FactorCompiler(guard=guard).execute(definition,AsOfDataView(panel,'execution-input-v1','ORIGINAL_POOL_NOT_REDEFINED',as_of=20240731))
            result = result.rename(columns={'available_at':'compiler_available_date'})
            result.loc[~complete.to_numpy(),'value'] = np.nan
            result['computability'] = np.where(complete,'COMPUTABLE','INCOMPLETE_OR_INVALID_SIX_SESSION_WINDOW')
            factor_counts.update(result.computability)
            table = pa.Table.from_pandas(result,preserve_index=False)
            if values_writer is None:
                values_writer = pq.ParquetWriter(out/'RETURN_5D_VALUES.parquet',table.schema)
            values_writer.write_table(table)
    finally:
        if values_writer: values_writer.close()
    write_json(out/'PHYSICAL_READ_AUDIT.json',{'daily':audit,'pit':pit_audit,'warmup_source_failures':failures,'missing_pit_partitions':missing})
    write_json(out/'INPUT_READINESS.json',{'status':'PARTIAL','daily_rows':len(daily),'warmup_rows':len(warm_frame),
        'calendar_sessions':len(sessions),'original_missing_symbols':plan['missing_symbols_carried_forward'],
        'warmup_source_failures':failures,'state_counts':state_counts,'state_symbols':len(symbols),
        'missing_pit_partitions':missing,'factor_counts':factor_counts,
        'components':{'daily':'SUPPORTED_WITH_COVERAGE_GAPS','calendar':'SUPPORTED','historical_pool_and_state':'SUPPORTED_WITH_UNKNOWN_STATES_AND_LATE_EVIDENCE',
        'standalone_historical_security_master':'MISSING','volume_amount_units':'UNKNOWN','corporate_actions':'UNSUPPORTED_PROVIDER_WINDOW',
        'corporate_action_accounting':'UNSUPPORTED','execution_adapter':'PARTIAL_NOT_ENGINE_BOUND'},
        'TRAIN_EXECUTION_BACKTEST_USER_APPROVAL':True,'TRAIN_EXECUTION_BACKTEST_AUTHORIZED':False,
        'TRAIN_EXECUTION_BACKTEST_STARTED':False,'TRAIN_EXECUTION_BACKTEST_COMPLETED':False,
        'NEW_EXPOSURES_USED':0,'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False,'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,
        'new_budget_receipt_issued':False,'new_user_approved_limit':2,'previous_usage_not_reset':True,
        'this_materialization_wall_seconds':time.monotonic()-started,'previous_compute_usage':'NOT_RECONSTRUCTED_NOT_ZERO'})
    manifest={p.name:{'sha256':digest(p),'bytes':p.stat().st_size} for p in out.iterdir() if p.is_file()}
    write_json(out/'MANIFEST.json',{'read_plan_sha256':digest(plan_path),'script_sha256':digest(__file__),'files':manifest})
    print(json.dumps({'stage':'complete','seconds':time.monotonic()-started,'factor_counts':factor_counts}),flush=True)


if __name__ == '__main__':
    prepare()
