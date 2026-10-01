"""受限工程规模验证；所有行情为合成夹具，没有策略研究意义。"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests' / 'research_factory')]


def peak_memory_kib():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    record = Counters()
    record.cb = ctypes.sizeof(record)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if not query(process(), ctypes.byref(record), record.cb):
        raise OSError('SCALE_PEAK_MEMORY_QUERY_FAILED')
    return record.PeakWorkingSetSize / 1024


def scale_worker(stocks, batch, reverse):
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    handshake = worker_resource_handshake()
    if handshake['execution'] != {'purpose': 'FULL_UNIVERSE_ENGINEERING_SCALE'}:
        raise PermissionError('SCALE_BOUNDED_WORKER_REQUIRED')
    from universe_test_fixture_v1 import fixture
    from test_universe_account_backend_v1 import account_result
    from chanlun_trader.research_factory.common import stable_hash
    symbols = [('00' + str(i // 3).zfill(4) + '.SZ' if i % 3 == 0 else
                '60' + str(i // 3).zfill(4) + '.SH' if i % 3 == 1 else
                '30' + str(i // 3).zfill(4) + '.SZ') for i in range(stocks)]
    begin = time.monotonic()
    window, bundle = fixture(symbols=symbols, days_count=65)
    if reverse:
        for key in ('daily', 'turn', 'states'):
            bundle[key] = bundle[key].iloc[::-1].reset_index(drop=True)
    result = account_result(bundle, window, batch_size=batch)
    return {'version': 'FULL_UNIVERSE_SCALE_EVIDENCE_V1', 'synthetic': True,
        'target': stocks, 'boards': ['SZ_MAIN', 'SH_MAIN', 'CHINEXT'], 'batch_size': batch,
        'reverse_input_order': reverse, 'account_sessions': len(result['daily_accounts']),
        'each_session_processed': [row['processed'] for row in result['scan_days']],
        'result_identity': stable_hash(result), 'account_reconciled': result['reconciliation']['passed'],
        'economic_identity': stable_hash({'daily_accounts': result['daily_accounts'],
            'fills': result['fills'], 'metrics': result['metrics']}),
        'economic_identity_scope': 'DAILY_CASH_EQUITY_POSITIONS_FILLS_AND_METRICS_NO_SOURCE_PATHS',
        'metrics': result['metrics'],
        'wall_seconds': time.monotonic() - begin, 'peak_process_working_set_kib': peak_memory_kib(),
        'resource_platform': os.name, 'resource_handshake': True, 'real_acceptance': False,
        'strategy_qualified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', required=True)
    parser.add_argument('--stocks', type=int, default=4536)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--reverse', action='store_true')
    args = parser.parse_args(argv)
    if not 3 <= args.stocks <= 30000:
        raise ValueError('SCALE_STOCK_COUNT_INVALID')
    print(json.dumps(scale_worker(args.stocks, args.batch_size, args.reverse), ensure_ascii=False))


if __name__ == '__main__':
    main()
