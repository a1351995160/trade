"""固定输入的独立降级预检；不调用原严格输入验收或任何绩效入口。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
BASE = Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3')
OWNER = Path('E:/llmwiki/owner-execution-export-v1')
ROOT = BASE.parent / 'degraded-train-v1'
APPROVAL = Path('C:/Users/84219/.codex/attachments/13ebc2d8-af8b-4c9b-80ab-97924132d8d7/pasted-text.txt')
BUDGET = Path('E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/governance/search_budget_registry.json')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(name, value):
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(ROOT / name, json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)))


def freeze():
    from chanlun_trader.research_factory.degraded_train_v1 import CONTRACT
    if datetime.now(timezone.utc) >= datetime.fromisoformat(CONTRACT['expires_at']):
        raise PermissionError('DEGRADED_APPROVAL_EXPIRED')
    parent = read(BUDGET.parent / 'confirmation.json')
    if datetime.now(timezone.utc) >= datetime.fromisoformat(parent['plan']['expires_at']):
        raise PermissionError('EARLIER_PARENT_APPROVAL_EXPIRED')
    if (BUDGET.parent / 'revocation.json').exists():
        raise PermissionError('PARENT_APPROVAL_REVOKED')
    paths = [BASE / n for n in ['MANIFEST.json', 'CALENDAR.json', 'DAILY.parquet',
        'HISTORICAL_POOL_AND_STATE.parquet', 'RETURN_5D_VALUES.parquet', 'FACTOR_BINDING.json']]
    paths += [BASE.parent / 'INPUT_READ_PLAN_V2.json', OWNER / 'run-v1/WARMUP_GAP_RESOLUTION.json',
        OWNER / 'run-v2/staging/gbbq_window.jsonl', OWNER / 'run-v2/GBBQ_READ_AUDIT.json',
        OWNER / 'run-v2/staging/units.json', OWNER / 'run-v2/staging/unit_source_evidence.json']
    paths += [SOURCE / 'src/chanlun_trader/tdx_data.py',
        Path('C:/Users/84219/.codex/skills/tdx-tq-local/SKILL.md'),
        Path('C:/Users/84219/.codex/skills/tdx-quant/SKILL.md')]
    # 清单先固定；只是当前明确文件，不沿引用寻找原件。
    save('READ_ALLOWLIST.json', {'paths': [str(p) for p in paths],
        'purpose': 'DEGRADED_EXECUTION_FEASIBILITY_V1_NO_OUTCOME',
        'reader': os.environ.get('CODEX_THREAD_ID', 'CODEX_CURRENT_LOCAL_TASK'),
        'recipient': 'REQUESTING_USER', 'start': 20220722, 'end': 20240731})
    save('FROZEN_CONTRACT.json', {'contract': CONTRACT, 'approval_sha256': sha(APPROVAL),
        'budget_before_sha256': sha(BUDGET), 'inputs': {str(p): sha(p) for p in paths},
        'code': {str(p.relative_to(SOURCE)): sha(p) for p in [Path(__file__),
            SOURCE / 'src/chanlun_trader/research_factory/degraded_train_v1.py']}})


def scan():
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    from chanlun_trader.research_factory.degraded_train_v1 import capacity, hazard_overlap, feasibility_verdict
    frozen = read(ROOT / 'FROZEN_CONTRACT.json')
    for path, expected in frozen['inputs'].items():
        if sha(path) != expected:
            raise ValueError('DEGRADED_INPUT_IDENTITY_CONFLICT:' + path)
    manifest = read(BASE / 'MANIFEST.json')
    for name in ['CALENDAR.json', 'DAILY.parquet', 'HISTORICAL_POOL_AND_STATE.parquet', 'RETURN_5D_VALUES.parquet', 'FACTOR_BINDING.json']:
        if sha(BASE / name) != manifest['files'][name]['sha256']:
            raise ValueError('ORIGINAL_MANIFEST_CONFLICT:' + name)
    if sha(OWNER / 'run-v2/staging/gbbq_window.jsonl') != 'f2bc6064f9ab495fc854efdc6a4942dd4a87dc7be263de6e13da9be65e8a1927':
        raise ValueError('HAZARD_IDENTITY_CONFLICT')
    calendar = read(BASE / 'CALENDAR.json')
    days = calendar['sessions']; train = calendar['train_sessions']
    plan = read(BASE.parent / 'INPUT_READ_PLAN_V2.json')
    symbols = sorted([r[0] for r in plan['warmup_sources']] + plan['missing_symbols_carried_forward'])
    if len(symbols) != 5182 or len(set(symbols)) != 5182:
        raise ValueError('FROZEN_UNIVERSE_CONFLICT')
    accesses = []

    def frame(name, date_col, columns):
        path = BASE / name
        metadata = pq.ParquetFile(path)
        index = metadata.schema.names.index(date_col)
        for i in range(metadata.metadata.num_row_groups):
            stats = metadata.metadata.row_group(i).column(index).statistics
            if stats is None or not stats.has_min_max:
                raise PermissionError('DATE_WINDOW_NOT_PROVEN')
            if not 20220722 <= int(str(stats.min).replace('-', '')) <= int(str(stats.max).replace('-', '')) <= 20240731:
                raise PermissionError('DATE_WINDOW_OUTSIDE_APPROVAL')
        data = pq.read_table(path, columns=columns, use_threads=False).to_pandas(use_threads=False)
        data[date_col] = data[date_col].astype(str).str.replace('-', '').astype(int)
        if data.duplicated(['symbol', date_col]).any():
            raise ValueError('DUPLICATE_INPUT_IDENTITY')
        accesses.append({'path': str(path), 'sha256': sha(path), 'columns': columns,
            'rows': len(data), 'reader_pid': os.getpid(), 'purpose': 'NO_OUTCOME_FEASIBILITY'})
        return data

    daily = frame('DAILY.parquet', 'date', ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume_encoded', 'source_published_at'])
    factors = frame('RETURN_5D_VALUES.parquet', 'timestamp', ['symbol', 'timestamp', 'value'])
    states = frame('HISTORICAL_POOL_AND_STATE.parquet', 'trade_date', ['symbol', 'trade_date',
        'listed', 'delisted', 'universe_member', 'eligibility_status', 'st_status', 'suspension_status', 'board'])

    def matrix(data, date_col, value):
        return data.pivot(index=date_col, columns='symbol', values=value).reindex(index=days, columns=symbols)

    prices = {k: matrix(daily, 'date', k).to_numpy(dtype=float) for k in ['open', 'high', 'low', 'close', 'volume_encoded']}
    values = matrix(factors, 'timestamp', 'value').to_numpy(dtype=float)
    valid = np.logical_and.reduce([np.isfinite(prices[k]) & (prices[k] > 0) for k in ['open', 'high', 'low', 'close']])
    states['known'] = states.st_status.isin(['NORMAL', 'ST']) & states.suspension_status.isin(['TRADING', 'SUSPENDED']) & (states.eligibility_status != 'CONFLICT')
    states['eligible'] = states.known & states.listed & ~states.delisted & states.universe_member & (states.eligibility_status == 'ELIGIBLE') & (states.st_status == 'NORMAL') & (states.suspension_status == 'TRADING')
    known = matrix(states, 'trade_date', 'known').fillna(False).to_numpy(dtype=bool)
    eligible = matrix(states, 'trade_date', 'eligible').fillna(False).to_numpy(dtype=bool)
    # 没有warmup完整状态证据就延后六session依赖资格，不猜NORMAL/TRADING。
    dependency = pd.DataFrame(valid & known).rolling(6, min_periods=6).sum().eq(6).to_numpy()
    observed = matrix(daily, 'date', 'source_published_at')
    later = np.zeros_like(valid)
    for i in range(len(days) - 1):
        ts = pd.Timestamp(str(days[i + 1]) + ' 09:30', tz='Asia/Shanghai')
        supplied = observed.iloc[i].dropna()
        for symbol, value in supplied.items():
            if value != 'UNKNOWN' and pd.Timestamp(value) > ts:
                later[i, symbols.index(symbol)] = True
    hazards = {}
    hazard_count = 0
    with (OWNER / 'run-v2/staging/gbbq_window.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line); day = int(row['datetime']); symbol = row['symbol']
            if symbol not in symbols or not 20220722 <= day <= 20240731:
                raise PermissionError('HAZARD_WINDOW_OR_MEMBER_CONFLICT')
            hazards.setdefault(symbol, []).append(day); hazard_count += 1
    if hazard_count != 29990:
        raise ValueError('HAZARD_RECORD_COUNT_CONFLICT')
    gaps = read(OWNER / 'run-v1/WARMUP_GAP_RESOLUTION.json')
    warmup_counts = {key: sum(r['evidence_status'] == key for r in gaps) for key in ['UNKNOWN', 'NOT_YET_LISTED']}
    if warmup_counts != {'UNKNOWN': 27, 'NOT_YET_LISTED': 2673}:
        raise ValueError('WARMUP_EVIDENCE_CONFLICT')
    # uint32只证明数量表示；TQ单位文档不能自动证明原DAY到TQ的单位映射。
    unit_proof = {'nonnegative_quantity_format': 'TDX_UINT32_AT_BYTES_24_27',
        'raw_day_unit_domain_proven': False, 'status': 'DEGRADED_VOLUME_MODEL_UNUSABLE',
        'reason': 'TQ_SHARES_LOTS_DOCUMENTATION_DOES_NOT_PROVE_RAW_DAY_UNIT_DOMAIN',
        'strict_unit_status': read(OWNER / 'run-v2/staging/units.json')['status'],
        'no_new_source_search': True}
    save('VOLUME_MODEL_EVIDENCE.json', unit_proof)
    daily_counts = []; paths = []; first = {}
    for i, day in enumerate(days):
        if day not in train:
            continue
        finite = np.isfinite(values[i])
        ready = dependency[i] & eligible[i] & finite & ~later[i]
        for j in np.flatnonzero(ready):
            first.setdefault(symbols[j], day)
        ranked = sorted(np.flatnonzero(ready & (values[i] < 0)), key=lambda j: (values[i, j], symbols[j]))
        daily_counts.append({'date': day, 'frozen_universe_count': len(symbols),
            'observable_count': int(valid[i].sum()), 'unavailable_count': int((~valid[i]).sum()),
            'return_5d_computable_count': int(finite.sum()), 'eligible_count': int(ready.sum()),
            'ranked_count': len(ranked), 'top3_count': min(3, len(ranked)),
            'HISTORY_OR_STATE_NOT_READY': int((finite & ~dependency[i]).sum()),
            'LATER_OBSERVED_AVAILABILITY': int(later[i].sum()),
            'DATA_UNAVAILABLE_FOR_DEGRADED_BACKTEST': int((~valid[i]).sum())})
        for rank, j in enumerate(ranked[:3], 1):
            symbol = symbols[j]
            path = {'symbol': symbol, 'signal_session': day, 'rank': rank,
                'entry_date': days[i + 1] if i + 1 < len(days) else None,
                'exit_date': days[i + 5] if i + 5 < len(days) else None}
            if path['exit_date'] is None:
                path['reason'] = 'END_OF_TRAIN_NO_COMPLETE_CLOSURE_PATH'
            else:
                hits = hazard_overlap(hazards.get(symbol, []), day, path['exit_date'])
                path['hazard_dates'] = hits
                entry_open = prices['open'][i + 1, j]
                requested = int((10000 / 3) / (entry_open * 1.001) / 100) * 100 if np.isfinite(entry_open) and entry_open > 0 else 0
                path['capacity'] = capacity(prices['volume_encoded'][i, j], requested,
                    schema_proven=unit_proof['raw_day_unit_domain_proven'])
                # 并列记录hazard与容量，不能顺序短路后隐藏另一类失败。
                path['hazard_decision'] = 'ENTRY_REJECT_CORPORATE_ACTION_UNSUPPORTED' if hits else 'NO_LOCAL_HAZARD_IN_PROPOSED_PATH'
                path['reason'] = path['capacity']['reason']
            paths.append(path)
    verdict = feasibility_verdict(paths)
    save('DAILY_COVERAGE.json', daily_counts)
    save('CANDIDATE_PATHS.json', paths)
    save('FIRST_ELIGIBLE_SESSION.json', {s: first.get(s) for s in symbols})
    save('FROZEN_UNIVERSE.json', {'symbols': symbols, 'missing_source_symbols': plan['missing_symbols_carried_forward'],
        'missing_label': 'DATA_UNAVAILABLE_FOR_DEGRADED_BACKTEST'})
    from collections import Counter
    summary = {**verdict, 'type': 'DEGRADED_EXECUTION_FEASIBILITY_V1',
        'status': 'DEGRADED_ACCOUNT_BACKTEST_NOT_INFORMATIVE', 'no_outcome': True,
        'candidate_paths': len(paths), 'reasons': dict(Counter(p['reason'] for p in paths)),
        'hazard_overlapping_paths': sum(bool(p.get('hazard_dates')) for p in paths),
        'capacity_numbers_are_hypothetical_not_unit_certification': True,
        'closure_checks_short_circuited_by_unusable_volume_model': True,
        'hazard_records': hazard_count, 'warmup_evidence': warmup_counts,
        'no_local_hazard_records_symbols': len(set(symbols) - set(hazards)),
        'first_eligible_symbols': len(first), 'daily_sessions': len(daily_counts),
        'volume_model': unit_proof, 'budget_unchanged': sha(BUDGET) == frozen['budget_before_sha256'],
        'STRICT_TRAIN_INPUT_READY': False, 'TRAIN_EXECUTION_INPUT_READY': False,
        'DEGRADED_TRAIN_EXECUTION_INPUT_READY': False, 'DEGRADED_FEASIBILITY_COMPLETED': True,
        'DEGRADED_FEASIBILITY_PASSED': False, 'DEGRADED_ACCOUNT_BACKTEST_STARTED': False,
        'DEGRADED_ACCOUNT_BACKTEST_COMPLETED': False, 'DEGRADED_MAIN_EXPOSURES_USED': 0,
        'DEGRADED_REPAIR_EXPOSURES_USED': 0, 'CANDIDATE_RESEARCH_PRIORITY': 'INSUFFICIENT_EXECUTABLE_EVIDENCE',
        'AUTONOMOUS_STRATEGY_GOAL_COMPLETED': False, 'READY_FOR_REAL_TRIAL': False, 'R1_FULLY_CLOSED': False,
        'UNIVERSE_COVERAGE_BIAS_PRESENT': True, 'PIT_AVAILABILITY_NOT_HISTORICALLY_PROVEN': True}
    save('ACCESS_RECORD.json', {'parquet_accesses': accesses, 'frozen_files_verified': frozen['inputs'],
        'reader_pid': os.getpid(), 'recipient': 'REQUESTING_USER', 'outcome_fields_computed': []})
    save('FEASIBILITY.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    if '--worker' in sys.argv:
        from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
        worker_resource_handshake()
        scan()
    else:
        if (ROOT / 'FEASIBILITY.json').exists():
            raise PermissionError('EXISTING_FEASIBILITY_REUSE_ONLY_NO_AUTOMATIC_REPEAT')
        freeze()
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        started = time.monotonic()
        env = {**os.environ, 'PYTHONPATH': str(SOURCE / 'src'), 'PYTHONIOENCODING': 'utf-8',
            **{k: '1' for k in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']}}
        result = run_bounded_worker([sys.executable, str(Path(__file__)), '--worker'], root=SOURCE,
            memory_mib=2048, wall_seconds=900, environment=env,
            on_started=lambda pid: save('WORKER_STARTED.json', {'pid': pid, 'memory_mib': 2048,
                'wall_seconds': 900, 'numeric_threads': 1, 'concurrency': 1}),
            execution={'purpose': 'NO_OUTCOME_DEGRADED_FEASIBILITY'})
        save('WORKER_RESULT.json', {'returncode': result['returncode'], 'timed_out': result['timed_out'],
            'elapsed_seconds': time.monotonic() - started,
            'stdout': result['stdout'].decode('utf-8', errors='replace'),
            'stderr': result['stderr'].decode('utf-8', errors='replace')})
        print(result['stdout'].decode('utf-8', errors='replace'))
        print(result['stderr'].decode('utf-8', errors='replace'))
        sys.exit(result['returncode'])
