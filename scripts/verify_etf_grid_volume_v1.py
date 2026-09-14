"""固定510300窗口的数量编码核验，不产生账户、信号或收益。"""
import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from chanlun_trader.research_factory.etf_grid_preflight_v1 import decode_day_volume, check_model_input

ROOT=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/etf-grid-train-v1/input-probe-v1')
SOURCE=Path('E:/new_tdx_mock/vipdoc/sh/lday/sh510300.day')


def save(name,value):
    with (ROOT/name).open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def main():
    frame=pd.read_parquet(ROOT/'ETF_NORMALIZED_DIAGNOSTIC.parquet')
    wanted=set(map(int,frame.date))
    assert len(wanted)==606 and min(wanted)==20220128 and max(wanted)==20240731
    save('VOLUME_DECODER_READ_RULE_V1.json',{
        'created_at':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(),
        'source':str(SOURCE),'dates':sorted(wanted),'outside_window':'DATE_KEYS_ONLY',
        'source_window_sha256':'23bcbcd5baffbf1f0998c55a1ef8ef183477c2bc1afc1e647f1de7ab5e9bacd7',
        'rule':'reserved & 0xffffff00 == 0xc3640000: raw*100+(reserved&0xff); otherwise raw',
        'acceptance':'All original 606 rows retained, all VWAP/mid in [0.5,2], no source changes',
        'public_source':'https://github.com/jing2uo/tdx2db/pull/116',
        'evidence_level':'PUBLIC_IMPLEMENTATION_CORROBORATED_NOT_VENDOR_ATTESTED',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    save('USER_SPREAD_MODEL_APPROVAL_V1.json',{
        'origin':'USER_EXPLICIT_REPLY','questionItemId':'["request_user_input_async","call_VnN6cMe6D1Rmg8tW3WIC4vZW",0]',
        'answer':'允许按上述模型假设回测','slippage_per_side':.001,
        'scope':'510300 original TRAIN, grid and buy-hold same costs',
        'historical_spread':'UNKNOWN','old_search_extension':False})
    before=SOURCE.stat();digest=hashlib.sha256();records={}
    if before.st_size%32:raise ValueError('RECORD_SIZE_CONFLICT')
    with SOURCE.open('rb') as f:
        for offset in range(0,before.st_size,32):
            f.seek(offset);day=struct.unpack('<I',f.read(4))[0]
            if day not in wanted:continue
            f.seek(offset);raw=f.read(32);digest.update(raw)
            volume,reserved=struct.unpack('<II',raw[24:32])
            if day in records:raise ValueError('DUPLICATE_DATE')
            records[day]={'raw':volume,'reserved':reserved,'decoded':decode_day_volume(volume,reserved)}
    after=SOURCE.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('SOURCE_CHANGED')
    if digest.hexdigest()!='23bcbcd5baffbf1f0998c55a1ef8ef183477c2bc1afc1e647f1de7ab5e9bacd7':raise ValueError('WINDOW_HASH_CONFLICT')
    if set(records)!=wanted:raise ValueError('MISSING_DATE')
    for row in frame.itertuples():
        if records[int(row.date)]['raw']!=row.volume_encoded:raise ValueError('RAW_VOLUME_CONFLICT')
    frame['volume_shares']=[records[int(day)]['decoded'] for day in frame.date]
    result=check_model_input(frame,sorted(wanted),spread_model_approved=True)
    result['corrected_records']={str(day):value for day,value in records.items() if value['raw']!=value['decoded']}
    result['source_window_sha256']=digest.hexdigest()
    result['reader_pid']=os.getpid();result['reader_purpose']='NO_OUTCOME_FORMAT_VERIFICATION'
    result['code_sha256']=hashlib.sha256(Path('src/chanlun_trader/research_factory/etf_grid_preflight_v1.py').read_bytes()).hexdigest()
    save('VOLUME_DECODED_PREFLIGHT_V1.json',result)
    output=ROOT/'ETF_NORMALIZED_VOLUME_DECODED_V1.parquet'
    if output.exists():raise FileExistsError(output)
    frame.to_parquet(output,index=False)
    save('VOLUME_DECODED_INPUT_IDENTITY_V1.json',{'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'not_for_qualification':True,'account_execution_ready':False,'basic_input_checks':result['status']})
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
