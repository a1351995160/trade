"""独立OWNER工作区入口；原始gbbq仅驻留本进程，研究侧只接收验收后的包。"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import os
import sys
import time

if __name__=='__main__' and '--owner-worker' in sys.argv:
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    resource=worker_resource_handshake()
    if resource['execution']!={'purpose':'OWNER_EXPORT_V1'}:raise PermissionError('OWNER_RESOURCE_CONTEXT_CONFLICT')

from chanlun_trader.data.tdx.owner_export_v1 import START,END,OWNER,sha,write_json,read_day_window,OwnerDailyProviderV1,compare_sources,parse_gbbq_window
from chanlun_trader.data.tdx.tq_client import TQClient

WORK=Path('E:/llmwiki/owner-execution-export-v1')
RESEARCH=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1')
REQUEST=RESEARCH/'accounting-v1/OWNER_EXPORT_REQUEST_V1_1.json'
AUTH=Path('C:/Users/84219/.codex/attachments/822d4ed4-de89-465c-b177-062c4e63dd49/pasted-text.txt')
TDX=Path('E:/new_tdx_mock')
STATE=Path('E:/llmwiki/chanlun-trading-system/data/research/security_state')
REPO=Path(__file__).resolve().parents[1]


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def append_audit(path,value):
    with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')


def issue(symbol,dates,role,field,source,failure):
    return {'symbol':symbol,'date':dates,'data_role':role,'exact_missing_field':field,'attempted_source':str(source),
        'actual_failure':failure,'whether_code_can_solve':False,'whether_external_human_provider_data_is_required':True}


def main():
    if os.environ.get('CHANLUN_TEST_ISOLATION')=='1':raise PermissionError('OWNER_REAL_READ_NOT_ALLOWED_IN_TESTS')
    if Path.cwd().resolve()!=WORK.resolve():raise PermissionError('INDEPENDENT_OWNER_WORKSPACE_REQUIRED')
    started=time.monotonic();request=read(REQUEST);plan=read(RESEARCH/'INPUT_READ_PLAN_V2.json')
    out=WORK/'run-v1';out.mkdir(exist_ok=False)
    stage=out/'staging';stage.mkdir()
    sessions=read(RESEARCH/'materialized-v3/CALENDAR.json')['sessions'];members=request['required_members']
    existing=dict(plan['warmup_sources']);missing=request['missing_source_symbols']
    samples=sum([sorted(s for s in existing if s.endswith('.'+market))[:2] for market in ['SH','SZ']],[])
    freeze={'owner':OWNER,'authorization_sha256':sha(AUTH),'request_sha256':sha(REQUEST),'samples':samples,'sample_dates':sessions,
        'selection':'two lexicographically first existing members per exchange, all fixed calendar dates',
        'raw':'dividend_type=none; fill_data=false','price_atol':0.005,'volume_candidates':[1,100],
        'amount_candidates':[1,10000],'volume_atol':1,'amount_tolerance':'max(1 CNY encoded unit, abs(encoded)*1e-6)',
        'all_dates_and_rows_must_pass':True,'started_at':datetime.now(timezone.utc).isoformat()}
    write_json(out/'FROZEN_EXPORT_RULES.json',freeze)
    problems=[];audit=[];supplement=[];tq=OwnerDailyProviderV1(TQClient(use_cache=False,retries=1,timeout=20))
    # 连通性只查询批准窗口，不使用原health_check的2025日期。
    try:
        health=tq.client.request('get_trading_calendar',{'market':'SH','start_time':'20220722','end_time':'20220729'},use_cache=False)
        health_dates=health.get('Date',[]) if isinstance(health,dict) else []
        health_ok=sorted(map(int,health_dates))==request['warmup_dates']
    except Exception:health_ok=False
    write_json(out/'TQ_HEALTH.json',{'window':request['warmup_dates'],'ready':health_ok})
    comparisons=[]
    for symbol in samples:
        local,identity=read_day_window(existing[symbol]['path'],sessions);audit.append(identity)
        try:
            if not health_ok:raise ValueError('TQ_SERVICE_NOT_READY')
            remote=tq.get_daily(symbol)
            comparison=compare_sources(local,remote)
            write_json(out/(symbol+'.sample.json'),{'tdx':local,'tq':remote,'comparison':comparison})
            comparisons.append({'symbol':symbol,**comparison})
        except Exception as exc:
            comparisons.append({'symbol':symbol,'failure':type(exc).__name__,'price_semantics_compatible':False})
    compatible=bool(comparisons) and all(c['price_semantics_compatible'] for c in comparisons)
    ratios={name:sorted({c.get('scales',{}).get(name,{}).get('unique_ratio') for c in comparisons if c.get('scales',{}).get(name,{}).get('unique_ratio') is not None}) for name in ['volume','amount']}
    ratio_pass={name:len(ratios[name])==1 and all(c.get('scales',{}).get(name,{}).get('unique_ratio')==ratios[name][0] for c in comparisons) for name in ratios}
    # 已装两份接口说明互相冲突：固定倍率不能单独证明绝对单位。
    unit_evidence={'status':'UNIT_SOURCE_CONFLICT','comparison':comparisons,'unique_ratios':ratios,'all_sample_ratio_pass':ratio_pass,
        'price_compatible':compatible,'source_identity':'LOCAL_PARSER_AND_TQ_OWNER_COMPARISON',
        'conflict':{'tdx-tq-local':'Volume=shares','tdx-quant':'Volume=lots'},
        'evidence_paths':[str(REPO/'.venv/Lib/site-packages/pytdx/reader/daily_bar_reader.py'),
            'C:/Users/84219/.codex/skills/tdx-tq-local/SKILL.md','C:/Users/84219/.codex/skills/tdx-quant/SKILL.md']}
    unit_evidence['source_hashes']={p:sha(p) for p in unit_evidence['evidence_paths']}
    write_json(stage/'unit_source_evidence.json',unit_evidence)
    units={'volume_unit':'UNKNOWN','amount_unit':'CNY' if ratio_pass['amount'] and ratios['amount']==[10000] else 'UNKNOWN',
        'price_mode':'RAW','source_identity':unit_evidence['source_identity'],'evidence_sha256':sha(stage/'unit_source_evidence.json'),
        'parser_version':'PROJECT_TDX_UINT32_CENTS_V1','status':'UNIT_SOURCE_CONFLICT'}
    write_json(stage/'units.json',units)
    problems.append(issue('ALL_REQUEST_MEMBERS',sessions,'units','volume_unit',unit_evidence['evidence_paths'],'DOCUMENTED_TQ_VOLUME_UNITS_CONFLICT_ABSOLUTE_ANCHOR_MISSING'))
    counts={'TDX_supplied_count':0,'TQ_supplied_count':0,'still_missing_count':0}
    for symbol in missing:
        market=symbol.split('.')[1].lower();path=TDX/'vipdoc'/market/'lday'/(market+symbol[:6]+'.day')
        rows=[]
        if path.is_file():
            try:rows,identity=read_day_window(path,sessions);audit.append(identity)
            except Exception as exc:problems.append(issue(symbol,sessions,'daily','OHLCVA',path,type(exc).__name__))
            if rows:counts['TDX_supplied_count']+=1
        else:
            audit.append({'path':str(path),'status':'FILE_NOT_FOUND','symbol':symbol})
            try:
                if not health_ok:raise ValueError('TQ_SERVICE_NOT_READY')
                remote=tq.get_daily(symbol)
                # 保留合法限窗响应，即使最终单位/兼容未通过，不冒充可用补源。
                write_json(out/(symbol+'.tq.json'),remote)
                if remote and compatible and all(ratio_pass.values()):
                    rows=[dict(symbol=symbol,date=r['date'],**{k:r[k] for k in ['open','high','low','close']},
                        volume_encoded=r['volume']*ratios['volume'][0],amount_encoded=r['amount']*ratios['amount'][0],source='TQ_LOCAL_OWNER_EXPORT') for r in remote]
                    counts['TQ_supplied_count']+=1
                elif remote:problems.append(issue(symbol,sessions,'daily','RAW_and_units_compatibility','TQ_LOCAL','FROZEN_CROSS_SOURCE_COMPARISON_NOT_PASS'))
            except Exception as exc:problems.append(issue(symbol,sessions,'daily','OHLCVA','TQ_LOCAL',str(exc) if str(exc) in ['TQ_SERVICE_NOT_READY','TQ_DAILY_SYMBOL_SHAPE_UNKNOWN','TQ_DAILY_SHAPE_UNKNOWN'] else type(exc).__name__))
        if not rows:
            counts['still_missing_count']+=1
            problems.append(issue(symbol,sessions,'daily','OHLCVA',[str(path),'TQ_LOCAL'],'NO_COMPATIBLE_WINDOW_ROWS'))
        supplement.extend({**r,'symbol':symbol,'source':r.get('source','TDX_LOCAL_OWNER_EXPORT'),'available_at':None,'source_published_at':None} for r in rows)
    write_json(out/'MISSING_SOURCE_COUNTS.json',counts)
    print(json.dumps({'stage':'missing_sources','counts':counts}),flush=True)
    import pandas as pd
    import pyarrow.parquet as pq
    # 只读V3六日键以确定原2700格；不读取行情值，也不扫描来源目录。
    warm=pq.read_table(RESEARCH/'materialized-v3/DAILY.parquet',columns=['symbol','date'],filters=[('date','<',20220801)]).to_pandas()
    known=set(zip(warm.symbol,warm.date));gaps=[(s,d) for s in sorted(existing) for d in request['warmup_dates'] if (s,d) not in known]
    if len(gaps)!=2700:raise ValueError('ORIGINAL_WARMUP_GAP_IDENTITY_CHANGED')
    masters=read(STATE/'normalized/security_master_v2/records.json');master={r['symbol']:r for r in masters if r['symbol'] in members}
    warm_states=[];unknown=0
    # 读取精确六日分区；未找到时不加载混合全历史原始文件。
    warm_partitions={}
    for day,path in zip(sessions,plan['pit_partitions']):
        if day>=20220801:break
        p=Path(path);rows={}
        if p.is_file():
            for line in p.read_text(encoding='utf-8').splitlines():
                r=json.loads(line)
                if int(r['trade_date'].replace('-',''))!=day:raise ValueError('STATE_PARTITION_CONFLICT')
                rows[r['symbol']]=r
        warm_partitions[day]=rows
    for symbol,day in gaps:
        path=Path(existing[symbol]['path']);local,identity=read_day_window(path,[day]);audit.append(identity)
        if local:
            supplement.extend(dict(r,symbol=symbol,source='TDX_LOCAL_OWNER_EXPORT',available_at=None,source_published_at=None) for r in local)
            continue
        evidence=master.get(symbol,{});r=warm_partitions[day].get(symbol)
        listed=evidence.get('list_date');delisted=evidence.get('delist_date')
        reason='UNKNOWN'
        if listed and day<int(listed.replace('-','')):reason='NOT_YET_LISTED'
        elif delisted and day>int(delisted.replace('-','')):reason='DELISTED'
        elif r and r.get('st_status')=='ST':reason='ST'
        elif r and r.get('suspension_status')=='SUSPENDED':reason='SUSPENDED'
        elif r and r.get('universe_member') is False:reason='NOT_MEMBER'
        if reason=='UNKNOWN':unknown+=1
        warm_states.append({'symbol':symbol,'trade_date':day,'evidence_status':reason,'source':evidence.get('source','UNKNOWN'),
            'source_record_time':evidence.get('source_record_time'),'full_state_ready':False})
        problems.append(issue(symbol,day,'state','warmup_complete_state_and_observed_available_at',
            str(STATE/'normalized/security_master_v2/records.json'),'LIFECYCLE_ONLY_'+reason+'_FULL_STATE_UNPROVEN'))
    write_json(out/'WARMUP_GAP_RESOLUTION.json',warm_states)
    # 完整原成员的六日状态与训练历史实际发布时间不能由当前生命周期记录补造。
    for symbol in members:
        problems.append(issue(symbol,sessions,'state','observed_available_at; warmup_full_state',
            [str(STATE/'normalized/pit_universe_v2'),str(STATE/'raw/history'/('symbol='+symbol.replace('.','_')+'.json'))],
            'NORMALIZED_TIME_IS_MODEL; RAW_HISTORY_MIXED_THROUGH_2025_NOT_READ; CURRENT_MASTER_TIME_NOT_HISTORICAL_PUBLICATION'))
    pd.DataFrame(supplement,columns=['symbol','date','open','high','low','close','volume_encoded','amount_encoded','source','available_at','source_published_at']).to_parquet(stage/'daily_supplement.parquet',index=False)
    pd.DataFrame(warm_states).to_parquet(stage/'state_evidence_partial.parquet',index=False)
    write_json(out/'DAILY_READ_AUDIT.json',audit)
    action_status='UNRESOLVED'
    try:
        window,action_audit=parse_gbbq_window(TDX/'T0002/hq_cache/gbbq',lambda value:append_audit(out/'GBBQ_ACCESS_EVENTS.jsonl',value))
        window=window[window.symbol.isin(members)]
        # 这里只写已经限窗的原字段；未映射类型或缺款项日期时不可生成假可执行事件。
        target=stage/'gbbq_window.jsonl';window.to_json(target,orient='records',lines=True,force_ascii=False)
        reread=pd.read_json(target,lines=True)
        if len(reread) and not reread.datetime.between(START,END).all():raise ValueError('GBBQ_OUTPUT_WINDOW_FAILED')
        action_audit['output_sha256']=sha(target)
        write_json(out/'GBBQ_READ_AUDIT.json',action_audit)
        for r in window.itertuples():
            problems.append(issue(r.symbol,int(r.datetime),'corporate_action',
                'record_date/payment_date/tax_rule/share_credit_date/tradable_date/event_type_semantics',
                str(TDX/'T0002/hq_cache/gbbq'),'ACCOUNTING_UNSUPPORTED_RAW_CATEGORY_'+str(r.category)+'_TERMS_NOT_IN_GBBQ'))
        action_status={'scan_complete':True,'requested_symbols':len(members),'window_records':len(window),
            'symbols_with_records':int(window.symbol.nunique()),'zero_event_symbols_after_full_scan':sorted(set(members)-set(window.symbol)),
            'accounting_ready':False,'coverage_complete_for_execution':False}
        write_json(stage/'corporate_action_coverage.json',action_status)
    except Exception as exc:
        problems.append(issue('ALL_REQUEST_MEMBERS',[START,END],'corporate_action','reliable_gbbq_decode',
            str(TDX/'T0002/hq_cache/gbbq'),str(exc) if str(exc).startswith('GBBQ_') else 'GBBQ_EXPORT_FAILED_NO_RAW_DETAILS'))
    summary={'remaining_missing_symbols':counts['still_missing_count'],'remaining_unknown_warmup_cells':unknown,
        'unit_evidence_status':units['status'],'corporate_action_coverage':action_status,'state_evidence_status':'HISTORICAL_OBSERVATION_AND_FULL_WARMUP_UNRESOLVED',
        'OWNER_EXPORT_BUILDER_READY':True,'OWNER_DELIVERY_PACKAGE_READY':False,'TRAIN_EXECUTION_INPUT_READY':False,
        'TRAIN_ACCOUNT_BACKTEST_STARTED':False,'TRAIN_ACCOUNT_BACKTEST_COMPLETED':False,'MAIN_BACKTEST_EXPOSURES_USED':0,'REPAIR_BACKTEST_EXPOSURES_USED':0,
        'AUTONOMOUS_STRATEGY_GOAL_COMPLETED':False,'READY_FOR_REAL_TRIAL':False,'R1_FULLY_CLOSED':False,
        'elapsed_seconds':time.monotonic()-started}
    write_json(out/'OWNER_EXPORT_UNRESOLVED_ITEMS.json',{'owner':OWNER,'authorization_sha256':sha(AUTH),'summary':summary,'items':problems})
    write_json(out/'SUMMARY.json',summary)
    print(json.dumps({'stage':'complete','package_ready':False,'remaining_missing_symbols':counts['still_missing_count'],
        'remaining_unknown_warmup_cells':unknown,'unresolved_items':len(problems)}),flush=True)


def resume_sources():
    """已有证实假设的修复：保留首轮，复用缺路径/预热对账，只重做失败的来源支路。"""
    import pandas as pd
    from fractions import Fraction
    prior=WORK/'run-v1';out=WORK/'run-v2';out.mkdir(exist_ok=False);stage=out/'staging';stage.mkdir()
    freeze=read(prior/'FROZEN_EXPORT_RULES.json');request=read(REQUEST);plan=read(RESEARCH/'INPUT_READ_PLAN_V2.json')
    write_json(out/'FROZEN_EXPORT_RULES.json',{**freeze,'prior_rule_sha256':sha(prior/'FROZEN_EXPORT_RULES.json'),
        'repair':'LOCALHOST_PROXY_ROUTING_AND_NON_TARGET_GBBQ_MARKET_CHECK','sample_and_tolerances_unchanged':True})
    tq=OwnerDailyProviderV1(TQClient(use_cache=False,retries=1,timeout=10));existing=dict(plan['warmup_sources']);comparisons=[]
    for symbol in freeze['samples']:
        local,identity=read_day_window(existing[symbol]['path'],freeze['sample_dates'])
        old=next(a for a in read(prior/'DAILY_READ_AUDIT.json') if a.get('path')==identity['path'] and a.get('rows')==identity['rows'])
        if old['window_sha256']!=identity['window_sha256']:raise ValueError('FROZEN_SAMPLE_SOURCE_CHANGED')
        remote=tq.get_daily(symbol);comparison=compare_sources(local,remote)
        write_json(out/(symbol+'.sample.json'),{'tdx':local,'tq':remote,'comparison':comparison,'source':identity})
        comparisons.append({'symbol':symbol,**comparison})
    compatible=all(c['price_semantics_compatible'] for c in comparisons)
    ratios={field:{c['scales'][field]['unique_ratio'] for c in comparisons} for field in ['volume','amount']}
    stable={k:next(iter(v)) if len(v)==1 and None not in v else None for k,v in ratios.items()}
    evidence=read(prior/'staging/unit_source_evidence.json');evidence.update(comparison=comparisons,
        price_compatible=compatible,unique_ratios=stable,all_sample_ratio_pass={k:v is not None for k,v in stable.items()},
        installed_tqcenter={'path':str(TDX/'PYPlugins/user/tqcenter.py'),'sha256':sha(TDX/'PYPlugins/user/tqcenter.py'),'version':'1.0.14','absolute_units_documented':False})
    write_json(stage/'unit_source_evidence.json',evidence)
    units=read(prior/'staging/units.json');units['evidence_sha256']=sha(stage/'unit_source_evidence.json')
    units['amount_unit']='CNY' if stable['amount']==10000 else 'UNKNOWN'
    write_json(stage/'units.json',units)
    old_issues=read(prior/'OWNER_EXPORT_UNRESOLVED_ITEMS.json')['items']
    problems=[i for i in old_issues if i['data_role'] not in ['daily','corporate_action']]
    rows=[];supplied=0;still=[];outcomes=[]
    for symbol in request['missing_source_symbols']:
        try:
            remote=tq.get_daily(symbol)
            write_json(out/(symbol+'.tq.json'),remote)
            if remote and compatible and all(v is not None for v in stable.values()):
                for r in remote:rows.append(dict(symbol=symbol,date=r['date'],**{k:r[k] for k in ['open','high','low','close']},
                    volume_encoded=r['volume']*stable['volume'],amount_encoded=r['amount']*stable['amount'],
                    source='TQ_LOCAL_OWNER_EXPORT',available_at=None,source_published_at=None))
                supplied+=1;outcomes.append({'symbol':symbol,'rows':len(remote),'status':'COMPATIBLE_ENCODED_VALUES_ABSOLUTE_UNIT_GATE_REMAINS'})
            else:
                still.append(symbol);outcomes.append({'symbol':symbol,'rows':len(remote),'status':'NO_ROWS' if not remote else 'COMPATIBILITY_NOT_PASS'})
                problems.append(issue(symbol,freeze['sample_dates'],'daily','OHLCVA','TQ_LOCAL',outcomes[-1]['status']))
        except Exception as exc:
            still.append(symbol);error=str(exc) if str(exc).startswith('TQ_SYMBOL_') else type(exc).__name__
            outcomes.append({'symbol':symbol,'status':error});problems.append(issue(symbol,freeze['sample_dates'],'daily','OHLCVA','TQ_LOCAL',error))
    write_json(out/'MISSING_SOURCE_COUNTS.json',{'TDX_supplied_count':0,'TQ_supplied_count':supplied,'still_missing_count':len(still),'outcomes':outcomes})
    pd.DataFrame(rows,columns=['symbol','date','open','high','low','close','volume_encoded','amount_encoded','source','available_at','source_published_at']).to_parquet(stage/'daily_supplement.parquet',index=False)
    # 已取得的生命周期证据写入正式字段形状，但UNKNOWN不改成正常状态。
    masters={r['symbol']:r for r in read(STATE/'normalized/security_master_v2/records.json') if r['symbol'] in request['required_members']}
    state_rows=[];master_rows=[]
    for symbol in request['required_members']:
        m=masters.get(symbol,{})
        for day in request['warmup_dates']:
            listed=bool(m.get('list_date')) and day>=int(m['list_date'].replace('-',''))
            delisted=bool(m.get('delist_date')) and day>int(m['delist_date'].replace('-',''))
            state_rows.append(dict(symbol=symbol,trade_date=day,universe_member=None,listed=listed if m.get('list_date') else None,
                delisted=delisted if m.get('delist_date') else None,board=m.get('board','UNKNOWN'),st_status='UNKNOWN',suspension_status='UNKNOWN',
                eligibility_status='UNKNOWN',available_at=m.get('source_record_time'),observed_available_at=m.get('source_record_time'),
                observed_time_source=m.get('source','UNKNOWN')))
        master_rows.append(dict(symbol=symbol,effective_date=START,listed=None,delisted=None,list_date=m.get('list_date'),
            delist_date=m.get('delist_date'),source=m.get('source','UNKNOWN'),source_record_time=m.get('source_record_time')))
    pd.DataFrame(state_rows).to_parquet(stage/'state_supplement.parquet',index=False)
    pd.DataFrame(master_rows).to_parquet(stage/'security_master.parquet',index=False)
    window,audit=parse_gbbq_window(TDX/'T0002/hq_cache/gbbq',lambda value:append_audit(out/'GBBQ_ACCESS_EVENTS.jsonl',value));window=window[window.symbol.isin(request['required_members'])]
    rawfile=stage/'gbbq_window.jsonl';window.to_json(rawfile,orient='records',lines=True,force_ascii=False)
    reloaded=pd.read_json(rawfile,lines=True)
    if not reloaded.datetime.between(START,END).all():raise ValueError('GBBQ_OUTPUT_WINDOW_INVALID')
    audit['output_sha256']=sha(rawfile);write_json(out/'GBBQ_READ_AUDIT.json',audit)
    events=[]
    for r in window.itertuples():
        source_id=sha(rawfile)+':'+str(r.Index)
        if r.category!=1:
            problems.append(issue(r.symbol,int(r.datetime),'corporate_action','category_semantics',str(TDX/'T0002/hq_cache/gbbq'),'ACCOUNTING_UNSUPPORTED_CATEGORY_'+str(r.category)))
            continue
        common=dict(symbol=r.symbol,effective_date=int(r.datetime),source='TDX_GBBQ_OWNER_EXPORT:'+audit['source_sha256'],source_published_at=None,
            accounting_status='ACCOUNTING_UNSUPPORTED',record_date=None,payment_date=None,share_credit_date=None,tradable_date=None)
        for kind,amount,unit,terms in [('CASH_DIVIDEND',r.hongli_panqianliutong,'CNY_PER_SHARE',{'cash_per_share':r.hongli_panqianliutong/10,'tax_rule':None}),
                ('BONUS',r.songgu_qianzongguben,'NEW_SHARES_PER_OLD_SHARE',{'raw_shares_per_ten':r.songgu_qianzongguben}),
                ('RIGHTS',r.peigu_houzongguben,'NEW_SHARES_PER_OLD_SHARE',{'raw_rights_per_ten':r.peigu_houzongguben,'raw_subscription_price':r.peigujia_qianzongguben})]:
            if amount==0:continue
            if kind=='BONUS':
                ratio=1+Fraction(str(amount))/10;terms.update(ratio_numerator=ratio.numerator,ratio_denominator=ratio.denominator)
            events.append(dict(common,event_id=source_id+':'+kind,event_type=kind,terms=terms,units=unit))
            problems.append(issue(r.symbol,int(r.datetime),'corporate_action','record_date/payment_date/tax_rule/share_credit_date/tradable_date',
                str(TDX/'T0002/hq_cache/gbbq'),'ACCOUNTING_UNSUPPORTED_'+kind+'_TERMS_ABSENT'))
    eventfile=stage/'events.jsonl'
    with eventfile.open('x',encoding='utf-8') as f:
        for e in events:f.write(json.dumps(e,ensure_ascii=False,allow_nan=False)+'\n')
    dates=[json.loads(line)['effective_date'] for line in eventfile.read_text(encoding='utf-8').splitlines()]
    if dates and not START<=min(dates)<=max(dates)<=END:raise ValueError('EVENT_FILE_WINDOW_INVALID')
    coverage={'complete':True,'symbols':request['required_members'],'scan_source':'FULL_LEGAL_LOCAL_GBBQ_SCAN',
        'zero_event_symbols':sorted(set(request['required_members'])-set(window.symbol)),
        'unmapped_category_records':int((window.category!=1).sum()),'accounting_supported':False}
    # 未映射类别仍有精确剩余项；不能把该事件子集当完整会计事件集。
    coverage['complete']=coverage['unmapped_category_records']==0
    write_json(stage/'actions_manifest.json',{'dataset_id':'OWNER_GBBQ_'+audit['source_sha256'],'version':'WindowedCorporateActionDatasetV1',
        'start':START,'end':END,'source_identity':audit['source_sha256'],'events_file':'events.jsonl','events_sha256':sha(eventfile),
        'coverage':coverage,'physical_window_attestation':{'owner':OWNER,'authorization_sha256':sha(AUTH),'window_enforced_before_export':True,
            'not_derived_from_current_incident':True,'start':START,'end':END}})
    summary=read(prior/'SUMMARY.json');summary.update(remaining_missing_symbols=len(still),corporate_action_coverage={
        'full_source_scanned':True,'symbols_in_scope':5182,'window_raw_records':len(window),'mapped_events':len(events),
        'unmapped_category_records':coverage['unmapped_category_records'],'zero_event_symbols':len(coverage['zero_event_symbols']),
        'accounting_supported':False},prior_run_retained=True)
    write_json(out/'OWNER_EXPORT_UNRESOLVED_ITEMS.json',{'owner':OWNER,'authorization_sha256':sha(AUTH),'summary':summary,'items':problems})
    write_json(out/'SUMMARY.json',summary)
    print(json.dumps({'stage':'source_repair_complete','TQ_supplied_count':supplied,'still_missing_count':len(still),'mapped_events':len(events)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',default=str(REQUEST));parser.add_argument('--owner-worker',action='store_true');parser.add_argument('--resume-sources',action='store_true');parser.add_argument('--finalize',action='store_true');args=parser.parse_args()
    if Path(args.request).resolve()!=REQUEST.resolve():raise PermissionError('EXACT_EXISTING_EXPORT_REQUEST_REQUIRED')
    if args.finalize:
        import subprocess
        raise SystemExit(subprocess.run([sys.executable,str(Path(__file__).with_name('finalize_owner_execution_package.py'))],cwd=WORK).returncode)
    elif args.owner_worker:
        if args.resume_sources:resume_sources()
        else:main()
    else:
        from chanlun_trader.synthetic_batch_resources import run_bounded_worker
        WORK.mkdir(exist_ok=True)
        env=dict(os.environ,PYTHONPATH=str(REPO/'src'),NO_PROXY='127.0.0.1,localhost',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
        version='V2' if args.resume_sources else 'V1'
        command=[sys.executable,str(Path(__file__).resolve()),'--owner-worker']+(['--resume-sources'] if args.resume_sources else [])
        result=run_bounded_worker(command,root=WORK,memory_mib=2048,wall_seconds=900,
            environment=env,execution={'purpose':'OWNER_EXPORT_V1'},on_started=lambda pid:write_json(WORK/('WORKER_STARTED_'+version+'.json'),{'pid':pid,'memory_mib':2048,'wall_seconds':900}))
        write_json(WORK/('PROCESS_'+version+'.json'),{k:v.decode(errors='replace') if isinstance(v,bytes) else v for k,v in result.items()})
        print(json.dumps({'owner_export_returncode':result['returncode'],'timed_out':result['timed_out']}))
        sys.exit(result['returncode'])
