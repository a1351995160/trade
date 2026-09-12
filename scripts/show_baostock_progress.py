"""只读查看本次固定取数进度；不导入研究执行器，不读取行情正文。"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1')


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def process_status(pid):
    """Windows只查询，不发送信号；PID存在不等于任务身份已经核实。"""
    if os.name != 'nt':
        return '未知（仅支持Windows进程查询）'
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return '已不存在' if ctypes.get_last_error() == 87 else '未知（无法查询）'
    try:
        code = wintypes.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
            return '未知（无法查询）'
        return 'PID仍存在' if code.value == 259 else '已退出'
    finally:
        api.CloseHandle(handle)


def snapshot(root):
    plan = read(root/'READ_PLAN.json')
    if not plan or not isinstance(plan.get('symbols'), list):
        raise ValueError('读取清单缺失或暂不可解析，不能把总量当作0。')
    complete = partial = errors = 0
    revision_path = root/'ACQUISITION_RESUME_V1.json'
    revision = read(revision_path)
    if revision_path.exists() and revision is None:
        raise ValueError('恢复修订暂不可解析，请稍后重查。')
    for symbol in plan['symbols']:
        receipts = [read(root/'responses'/symbol/('attempt-2' if revision and symbol == '002853.SZ'
                    and flag == '1' else '')/f'{flag}.access.json') for flag in ('3', '1')]
        successes = sum(r is not None and str(r.get('error_code')) == '0' for r in receipts)
        complete += successes == 2
        partial += successes == 1
        errors += sum(r is not None and str(r.get('error_code')) != '0' for r in receipts)
    settled_seconds = 0.0
    pending = []
    latest = None
    resources = root/'resources'
    for path in resources.glob('*.json'):
        if path.name.endswith('.started.json'):
            if not path.with_name(path.name.replace('.started.json', '.json')).exists():
                item = read(path)
                if item and 'pid' in item:
                    pending.append((path.name, item['pid']))
            continue
        item = read(path)
        if item:
            settled_seconds += item.get('elapsed_seconds', 0)
            if path.name.startswith(('fetch-hfq1-', 'resume-fetch-')):
                number = int(path.stem.rsplit('-', 1)[1])
                if path.name.startswith('resume-fetch-'):
                    number += 10000
                if latest is None or number > latest[0]:
                    latest = (number, item)
    passed = checked = 0
    for batch in range((len(plan['symbols']) + plan['batch_symbols'] - 1)//plan['batch_symbols']):
        revised = root/'quality-ipo-v1'/f'batch-{batch}.json'
        if batch == 14 and (root/'quality-alias-v1/batch-14.json').exists():
            revised = root/'quality-alias-v1/batch-14.json'
        item = read(revised if revised.exists() else root/'quality'/f'batch-{batch}.json')
        if item is not None:
            checked += 1
            passed += item.get('passed') is True
    return dict(total=len(plan['symbols']), complete=complete, partial=partial, errors=errors,
                settled_seconds=settled_seconds, limit=revision['total_seconds'] if revision else plan['total_seconds'],
                pending=pending, checked=checked, passed=passed, latest=latest)


def show_once():
    print(f'\n检查时间：{datetime.now().astimezone().isoformat(timespec="seconds")}')
    try:
        result = snapshot(ROOT)
    except ValueError as exc:
        print(exc)
        return
    print('BaoStock固定训练输入进度（只读，非回测结果）')
    print(f"双价格响应成功：{result['complete']}/{result['total']}只")
    print(f"仅一侧成功：{result['partial']}只；已记录接口错误：{result['errors']}份")
    print(f"批次质量核验：{result['passed']}/{result['checked']}个已有报告通过（优先采用修订报告）")
    print(f"已结算worker耗时：{result['settled_seconds']/60:.1f}/{result['limit']/60:.0f}分钟（不含尚未结算worker）")
    for name, pid in result['pending']:
        print(f'未结算worker：{name}，PID={pid}，{process_status(pid)}')
    if not result['pending']:
        print('没有未结算worker；这不代表全部完成，请结合数量与最后回执。')
    if result['latest']:
        batch, item = result['latest']
        label = f'恢复分片起点{batch-10000}' if batch >= 10000 else f'批次{batch}'
        print(f"最近取数回执：{label}，退出码={item.get('returncode', '未知')}，超时={item.get('timed_out', '未知')}")
        if item.get('returncode') != 0:
            print('该回执错误（可能已有后续质量修订，保留原失败）：')
            print(str(item.get('stderr', '未保存stderr'))[-1800:])
    print('成功响应不等于输入就绪；文件并发写入时可能暂时少计，请稍后再次查看。')
    print('当前取数程序不会自动启动回测。无自动重试、补额度或执行功能。')
    print(f'证据目录：{ROOT}')


def run(watch=False):
    try:
        while True:
            show_once()
            if not watch:
                return
            print('60秒后再次检查；Ctrl+C仅退出查看。', flush=True)
            time.sleep(60)
    except KeyboardInterrupt:
        print('\n已退出进度查看；未向取数进程发送停止指令。')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='只读查看BaoStock取数进度')
    parser.add_argument('--watch', action='store_true', help='每轮检查后等待60秒，循环查看')
    run(parser.parse_args().watch)


if __name__ == '__main__':
    main()
