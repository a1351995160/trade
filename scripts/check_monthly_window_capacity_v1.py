"""仅以独立日历证明原门槛是否可能达到；不读取行情或计算信号。"""
from datetime import datetime, timezone

from acquire_monthly_independent_v1 import ROOT, SOURCE, guard, read, save, sha
from chanlun_trader.research_factory.degraded_train_v1 import CONTRACT, feasibility_verdict


def calendar_bound(sessions, start=20250801, include_warmup_signal=False):
    """乐观假设每个合法月末有3只可成交股票，计算门槛上限。"""
    if sessions != sorted(set(sessions)):
        raise ValueError('CALENDAR_IDENTITY_REQUIRED')
    paths=[]
    for i, day in enumerate(sessions[:-1]):
        if day//100 == sessions[i+1]//100 or sessions[i+1]<start:
            continue
        if not include_warmup_signal and day<start:
            continue
        exit_day=sessions[i+22] if i+22<len(sessions) else None
        for rank in range(1,4):
            paths.append({'symbol':f'SYNTHETIC_UPPER_BOUND_{rank}', 'signal_session':day,
                'entry_date':sessions[i+1],'exit_date':exit_day,
                'reason':'COMPLETE_CLOSURE_PATH' if exit_day else 'NO_WINDOW_EXIT'})
    return {'interpretation':'OPTIMISTIC_CALENDAR_UPPER_BOUND_NOT_ACTUAL_FEASIBILITY',
        'include_warmup_signal':include_warmup_signal,
        'maximum_entry_dates':len({p['entry_date'] for p in paths}),
        'complete_entry_dates':sorted({p['entry_date'] for p in paths if p['exit_date']}),
        'original_gate_on_optimistic_paths':feasibility_verdict(paths)}


def run():
    grant=guard()
    path=ROOT/'CALENDAR_WINDOW.json'
    sessions=[int(d.replace('-','')) for d in read(path)['sessions']]
    value={'status':'FROZEN_GATE_UNREACHABLE_IN_APPROVED_WINDOW',
        'calendar_sha256':sha(path),'release_identity':grant['identity'],
        'original_thresholds':CONTRACT['thresholds'],
        'normal_signal_window':calendar_bound(sessions),
        'generous_boundary_upper_bound':calendar_bound(sessions,include_warmup_signal=True),
        'performance_computed':False,'account_started':False,'main_exposures_used':0,
        'repair_exposures_used':0,'scope_change_applied':False}
    save(ROOT/'CALENDAR_GATE_APPLICABILITY.json',value)
    save(ROOT/'CALENDAR_GATE_ACCESS.json',{'reader':grant['thread_id'],'recipient':'REQUESTING_USER',
        'purpose':'NO_PRICE_CALENDAR_GATE_APPLICABILITY','at':datetime.now(timezone.utc).isoformat(),
        'files':{str(path):sha(path),str(SOURCE/'src/chanlun_trader/research_factory/degraded_train_v1.py'):
                 sha(SOURCE/'src/chanlun_trader/research_factory/degraded_train_v1.py')},
        'output_sha256':sha(ROOT/'CALENDAR_GATE_APPLICABILITY.json')})
    print(value)


if __name__=='__main__':
    run()
