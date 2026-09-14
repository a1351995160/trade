"""只读固定周窗口回执，不读行情/精确绩效，不启动或重试任何任务。"""
from datetime import datetime,timezone
import ctypes
import json
import os
from pathlib import Path

ROOT=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/weekly-low-vol-window-v1')


def alive(pid):
    if os.name!='nt':return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,0,int(pid))
    if not handle:return None if ctypes.get_last_error()==5 else False
    try:
        code=ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return None
        return code.value==259
    finally:kernel.CloseHandle(handle)


def snapshot(root=ROOT,process_alive=alive):
    warnings=[]
    def read(name):
        path=root/name
        if not path.exists():return None
        try:return json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError) as exc:
            warnings.append(f'{name}: {type(exc).__name__}（可能正在写入）')
            return None
    now=datetime.now(timezone.utc)
    workers=[]
    for path in (root/'resources').glob('*.started.json'):
        start=read(str(path.relative_to(root)))
        if not start:continue
        label=path.name.removesuffix('.started.json')
        done=read('resources/'+label+'.completed.json')
        try:at=datetime.fromisoformat(start['at'])
        except (KeyError,ValueError):
            warnings.append(label+': 启动时间缺失');continue
        if at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
        lines=(done or {}).get('stderr','').strip().splitlines()
        timeout=bool((done or {}).get('timed_out'))
        error='超过单worker时间限制' if timeout else next((s.strip() for s in reversed(lines) if s.strip()),'无错误详情')
        if '_ArrayMemoryError' in (done or {}).get('stderr',''):error='内存分配失败：'+error
        workers.append({'name':label,'at':at.isoformat(),'pid':start.get('pid'),
            'finished':done is not None,'exit_code':(done or {}).get('returncode'),
            'elapsed_seconds':(done or {}).get('elapsed_seconds',max(0,(now-at).total_seconds())),
            'alive':process_alive(start['pid']) if not done and start.get('pid') else None,
            'error':error,'receipt':str(root/'resources'/f'{label}.completed.json'),
            'start_path':str(path)})
    workers.sort(key=lambda x:datetime.fromisoformat(x['at']))
    latest=workers[-1] if workers else None
    failures=[w for w in workers if w['finished'] and w['exit_code']!=0]
    ready=read('READY.json'); final=read('FINAL_STATUS.json'); settle=read('ACCOUNT_SETTLEMENT.json')
    index=read('RESULTS_INDEX.json');rows=read('STREAMED_ROW_COUNTS_V1.json')
    feasibility=None
    if ready:
        # READY的可行性哈希与实际回执绑定，禁止读取精确账户绩效。
        import hashlib
        path=root/'FEASIBILITY.json'
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==ready.get('feasibility_sha256'):
            f=read('FEASIBILITY.json'); feasibility={k:f.get(k) for k in ['counts','thresholds','passed']} if f else None
        else:warnings.append('FEASIBILITY哈希缺失或冲突，不能认定已通过')
    acquisition=read('ACQUISITION_COMPLETED.json')
    index_valid=False
    if index:
        import hashlib
        required={'ACCOUNT_RESULT.json','ACCOUNT_SETTLEMENT.json','RESULT_SUMMARY.json','RESULT_REVIEW_RULE.json','RESULT_REVIEW_ACCESS.json','ACCOUNT_REPORT.md'}
        index_valid=set(index)==required
        for name,item in index.items():
            if name not in required or not isinstance(item,dict):index_valid=False;continue
            path=root/name
            try:
                if Path(item['path']).resolve()!=path.resolve() or not path.resolve().is_relative_to(root.resolve()):
                    index_valid=False;continue
                if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:index_valid=False
            except (OSError,KeyError,TypeError):index_valid=False
        if not index_valid:warnings.append('结果索引缺文件或哈希不匹配，不能确认完整交付')
    count=len(list((root/'overlap').glob('*.json')))
    stages=[{'name':'补数及重叠核验','status':'完成' if acquisition else '等待/进行中','done':min(count,5235),'total':5235},
        {'name':'输入物化','status':'完成' if rows else '运行中' if latest and latest['name'].startswith('prepare-') and not latest['finished'] else '等待','done':None,'total':None},
        {'name':'无收益可行性','status':('通过' if feasibility['passed'] else '未通过') if feasibility else '等待','done':None,'total':None},
        {'name':'账户主回测','status':('已结算' if settle['completed'] else '执行未完成') if settle else '已启动' if (root/'ACCOUNT_ACCESS.json').exists() else '已预留/启动中' if (root/'ACCOUNT_EXECUTION.json').exists() else '等待','done':None,'total':None},
        {'name':'报告归档','status':'完成' if final and final.get('report_completed') and index_valid else '等待','done':None,'total':None}]
    complete=bool(final and final.get('account_completed') and final.get('report_completed') and settle and settle.get('completed') and index_valid)
    status='已完成（回执与索引齐全）' if complete else '可行性未通过，账户未运行' if feasibility and not feasibility['passed'] else '运行中/等待回执'
    current_error=None
    if not complete and latest and not (feasibility and not feasibility['passed']):
        if latest['finished'] and latest['exit_code']!=0:status='当前执行已失败';current_error=latest
        elif not latest['finished'] and latest['alive'] is False:status='进程已退出但尚未结算';current_error=latest
        elif latest['finished']:status='当前worker已结束，等待下一阶段/最终回执'
    return {'time':now.astimezone().isoformat(),'root':str(root),'status':status,'stages':stages,
        'current_worker':latest,'current_error':current_error,'historical_failures':failures,
        'feasibility':feasibility,'streamed_rows':rows,'warnings':warnings,
        'data_settled_seconds':sum(w['elapsed_seconds'] for w in workers if w['finished'] and w['name'] not in ['account-main','report']),
        'account_settled_seconds':sum(w['elapsed_seconds'] for w in workers if w['finished'] and w['name'] in ['account-main','report']),
        'main_exposures':settle.get('MAIN_BACKTEST_EXPOSURES_USED') if settle else None,
        'repair_exposures':settle.get('REPAIR_BACKTEST_EXPOSURES_USED') if settle else None,
        'complete':complete,'note':'未知百分比不估算；进度只来自回执。已停止的旧continuation失败不作为新worker当前故障。'}


if __name__=='__main__':
    print(json.dumps(snapshot(),ensure_ascii=True))
