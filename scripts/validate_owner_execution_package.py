"""独立验收最终OWNER包；不导入builder、不访问OWNER原始源、不运行策略。"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def validate_components(directory,request):
    """在最终manifest生成前独立查实际子件；不以缺manifest代替数据质量结论。"""
    import pandas as pd
    import pyarrow.parquet as pq
    directory=Path(directory);errors=[];hashes={};counts={}
    roles={'daily_supplement.parquet':'date','state_supplement.parquet':'trade_date','security_master.parquet':'effective_date'}
    members=set(request['required_members'])
    for name,datecol in roles.items():
        path=directory/name
        if not path.is_file():errors.append(name+':FILE_MISSING');continue
        pf=pq.ParquetFile(path);i=pf.schema.names.index(datecol)
        for n in range(pf.metadata.num_row_groups):
            if pf.metadata.row_group(n).num_rows==0:continue
            stats=pf.metadata.row_group(n).column(i).statistics
            if stats is None or not stats.has_min_max or not 20220722<=int(stats.min)<=int(stats.max)<=20240731:
                raise PermissionError('COMPONENT_WINDOW_PROOF_REQUIRED_BEFORE_ROWS')
        frame=pq.read_table(path,use_threads=False).to_pandas(use_threads=False);counts[name]=len(frame);hashes[name]=digest(path)
        if not set(frame.symbol)<=members or frame.duplicated(['symbol',datecol]).any():errors.append(name+':KEY_CONFLICT')
        if name=='daily_supplement.parquet' and not set(request['missing_source_symbols'])<=set(frame.symbol):errors.append('MISSING_SOURCE_MEMBERS_NOT_SUPPLIED')
        if name=='state_supplement.parquet':
            required=['universe_member','listed','delisted','board','st_status','suspension_status','eligibility_status','observed_available_at','observed_time_source']
            if not set(required)<=set(frame.columns) or frame[required].isna().any().any() or frame[required].astype(str).eq('UNKNOWN').any().any():
                errors.append('HISTORICAL_STATE_FIELDS_UNKNOWN')
            if 'observed_available_at' in frame and frame.observed_available_at.notna().any():
                observed=pd.to_datetime(frame.observed_available_at,utc=True,format='ISO8601')
                expected=pd.to_datetime(frame.trade_date.astype(str)).dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=9,minutes=30)
                if (observed.to_numpy()>expected.dt.tz_convert('UTC').to_numpy()).any():errors.append('STATE_OBSERVATION_LATER_THAN_HISTORICAL_DECISION')
        if name=='security_master.parquet' and (not members<=set(frame.symbol) or frame[['listed','delisted','source']].isna().any().any()):
            errors.append('SECURITY_MASTER_EXECUTION_FIELDS_UNKNOWN')
    for name in ['units.json','unit_source_evidence.json','actions_manifest.json','events.jsonl']:
        path=directory/name
        if not path.is_file():errors.append(name+':FILE_MISSING');continue
        hashes[name]=digest(path)
    if 'units.json' in hashes:
        units=json.loads((directory/'units.json').read_text(encoding='utf-8'))
        if units.get('volume_unit') not in ['SHARES','LOTS_100_SHARES']:errors.append('VOLUME_UNIT_NOT_VERIFIED')
        if units.get('amount_unit') not in ['CNY','TEN_THOUSAND_CNY']:errors.append('AMOUNT_UNIT_NOT_VERIFIED')
        if units.get('evidence_sha256')!=hashes.get('unit_source_evidence.json'):errors.append('UNITS_EVIDENCE_HASH_CONFLICT')
    if 'actions_manifest.json' in hashes:
        a=json.loads((directory/'actions_manifest.json').read_text(encoding='utf-8'))
        if a['coverage'].get('complete') is not True:errors.append('ACCOUNTING_EVENT_COVERAGE_INCOMPLETE')
        if set(a['coverage']['symbols'])!=members:errors.append('ACTION_MEMBERS_INCOMPLETE')
        if a['events_sha256']!=hashes.get('events.jsonl'):errors.append('EVENTS_HASH_CONFLICT')
    if 'events.jsonl' in hashes:
        unsupported=0
        with (directory/'events.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                e=json.loads(line)
                if not 20220722<=e['effective_date']<=20240731:raise PermissionError('EXPORTED_EVENT_WINDOW_CONFLICT')
                if e.get('accounting_status')=='ACCOUNTING_UNSUPPORTED':unsupported+=1
        counts['accounting_unsupported_events']=unsupported
        if unsupported:errors.append('CORPORATE_ACTION_REQUIRED_TERMS_MISSING')
    return {'status':'PASS' if not errors else 'NOT_READY','errors':errors,'counts':counts,'component_hashes':hashes,'validator_sha256':digest(Path(__file__))}


def validate_package(directory,request,authorization_sha256):
    import pandas as pd
    import pyarrow.parquet as pq
    from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1
    directory=Path(directory).resolve();path=directory/'OWNER_DELIVERY_MANIFEST.json'
    if not path.is_file():return {'status':'NOT_READY','errors':['FINAL_MANIFEST_NOT_GENERATED_SUBITEMS_UNRESOLVED']}
    m=json.loads(path.read_text(encoding='utf-8'));errors=[]
    roles={'daily_supplement','state_supplement','security_master','units','unit_source_evidence','actions_manifest'}
    if m.get('version')!='OWNER_EXECUTION_EXPORT_V1' or set(m.get('files',{}))!=roles:
        return {'status':'REJECTED','errors':['EXACT_SCHEMA_ROLES_REQUIRED']}
    att=m.get('physical_window_attestation',{})
    if m.get('owner')!='USER_AUTHORIZED_LOCAL_DATA_OWNER' or m.get('authorization_sha256')!=authorization_sha256:
        errors.append('OWNER_AUTHORIZATION_IDENTITY_CONFLICT')
    if (m.get('start'),m.get('end'))!=(20220722,20240731) or (att.get('start'),att.get('end'))!=(20220722,20240731) or att.get('window_enforced_before_export') is not True:
        errors.append('OWNER_WINDOW_ATTESTATION_CONFLICT')
    members=set(request['required_members']);verified={};frames={}
    for role,item in m['files'].items():
        name=item.get('file','');file=directory/name
        if not name or Path(name).name!=name or file.resolve().parent!=directory:
            raise PermissionError('OWNER_DEPENDENCY_PATH_REJECTED')
        if not file.is_file():errors.append(role+':FILE_MISSING');continue
        if set(['sha256','size','row_count','min_date','max_date','source_identity','schema_version'])-item.keys():
            errors.append(role+':MANIFEST_FIELDS_MISSING');continue
        if item['source_identity'] in ['',None,'UNKNOWN']:errors.append(role+':SOURCE_UNKNOWN')
        datecol={'daily_supplement':'date','state_supplement':'trade_date','security_master':'effective_date'}.get(role)
        if datecol:
            if file.suffix!='.parquet':errors.append(role+':PARQUET_REQUIRED');continue
            pf=pq.ParquetFile(file);index=pf.schema.names.index(datecol)
            bounds=[]
            for group in range(pf.metadata.num_row_groups):
                stats=pf.metadata.row_group(group).column(index).statistics
                if stats is None or not stats.has_min_max:raise PermissionError('DATE_STATISTICS_REQUIRED_BEFORE_ROWS')
                bounds.extend([int(stats.min),int(stats.max)])
            if bounds and not 20220722<=min(bounds)<=max(bounds)<=20240731:raise PermissionError('DATE_WINDOW_REJECTED_BEFORE_ROWS')
        if file.stat().st_size!=item['size'] or digest(file)!=item['sha256']:
            errors.append(role+':HASH_OR_SIZE_CONFLICT');continue
        if datecol:
            frame=pq.read_table(file,use_threads=False).to_pandas(use_threads=False);frames[role]=frame
            if len(frame)!=item['row_count'] or (bounds and (min(bounds),max(bounds))!=(item['min_date'],item['max_date'])):
                errors.append(role+':ROW_COUNT_OR_BOUNDS_CONFLICT')
            if not set(frame.symbol)<=members:errors.append(role+':UNAPPROVED_SYMBOL')
        verified[role]=file
    if errors:return {'status':'REJECTED','errors':errors}
    units=json.loads(verified['units'].read_text(encoding='utf-8'))
    if units.get('volume_unit') not in ['SHARES','LOTS_100_SHARES'] or units.get('amount_unit') not in ['CNY','TEN_THOUSAND_CNY'] or units.get('price_mode')!='RAW':errors.append('UNITS_NOT_CLOSED')
    if units.get('evidence_sha256')!=digest(verified['unit_source_evidence']):errors.append('UNIT_EVIDENCE_IDENTITY_CONFLICT')
    states=frames['state_supplement'];needed=['universe_member','listed','delisted','board','st_status','suspension_status','eligibility_status','observed_available_at','observed_time_source']
    if not set(needed)<=set(states.columns) or states[needed].isna().any().any():errors.append('STATE_FIELDS_UNKNOWN')
    elif (states[needed].astype(str)=='UNKNOWN').any().any():errors.append('STATE_FIELDS_UNKNOWN')
    else:
        observed=pd.to_datetime(states.observed_available_at,utc=True,format='ISO8601')
        opening=pd.to_datetime(states.trade_date.astype(str)).dt.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=9,minutes=30)
        if (observed.to_numpy()>opening.dt.tz_convert('UTC').to_numpy()).any():errors.append('STATE_TIME_AFTER_DECISION')
    try:
        actions=WindowedCorporateActionDatasetV1.load(verified['actions_manifest'],digest(verified['actions_manifest']),members)
        if any(e.get('accounting_status')=='ACCOUNTING_UNSUPPORTED' for e in actions.events):errors.append('CORPORATE_ACTION_ACCOUNTING_UNSUPPORTED')
    except (ValueError,PermissionError,KeyError):errors.append('CORPORATE_ACTION_DATASET_REJECTED')
    return {'status':'PASS' if not errors else 'REJECTED','errors':errors,'manifest_sha256':digest(path),
        'validator_sha256':digest(Path(__file__)),'verified_files':{k:digest(v) for k,v in verified.items()}}


if __name__=='__main__':
    from chanlun_trader.research_factory.evidence_paths import within_root
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--request',required=True);parser.add_argument('--authorization',required=True)
    args=parser.parse_args()
    directory=within_root(args.directory,Path('E:/llmwiki/owner-execution-export-v1'))
    request=within_root(args.request,Path('E:/llmwiki/autonomous-strategy-research-v1'))
    authorization=within_root(args.authorization,Path('C:/Users/84219/.codex/attachments'))
    r=validate_package(directory,json.loads(request.read_text(encoding='utf-8')),digest(authorization))
    print(json.dumps(r,ensure_ascii=False));raise SystemExit(0 if r['status']=='PASS' else 2)
