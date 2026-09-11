"""只读已知身份的原始状态头部和六日分区；在rows之前停止物理读取。"""
import json
from pathlib import Path
from chanlun_trader.data.tdx.owner_export_v1 import write_json


def metadata_prefix(path):
    # buffering=0防止Python缓冲器预取混合文件的数据行。
    with Path(path).open('rb',buffering=0) as stream:
        prefix=bytearray()
        while len(prefix)<16384:
            value=stream.read(1)
            if not value:raise ValueError('RAW_METADATA_PREFIX_NOT_TERMINATED')
            prefix.extend(value)
            if prefix.endswith(b'"rows":'):
                header=bytes(prefix[:-7]).rstrip().rstrip(b',')+b'}'
                return json.loads(header),len(prefix)
    raise ValueError('RAW_METADATA_PREFIX_TOO_LARGE_NO_ROW_READ')


if __name__=='__main__':
    root=Path('E:/llmwiki/owner-execution-export-v1/run-v2')
    state=Path('E:/llmwiki/chanlun-trading-system/data/research/security_state')
    request=json.loads(Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/accounting-v1/OWNER_EXPORT_REQUEST_V1_1.json').read_text(encoding='utf-8'))
    records=[]
    for symbol in request['required_members']:
        path=state/'raw/history'/('symbol='+symbol.replace('.','_')+'.json')
        if not path.is_file():records.append({'symbol':symbol,'path':str(path),'status':'FILE_NOT_FOUND'});continue
        try:
            metadata,count=metadata_prefix(path)
            records.append({'symbol':symbol,'path':str(path),'status':'METADATA_ONLY_ROWS_NOT_READ','physical_prefix_bytes':count,
                'query_date_range':metadata.get('query_date_range'),'fields':metadata.get('fields'),
                'fetched_at':metadata.get('fetched_at'),'provider':metadata.get('provider')})
        except ValueError as exc:records.append({'symbol':symbol,'path':str(path),'status':str(exc)})
    dates=[]
    for day in request['warmup_dates']:
        text=str(day);path=state/'raw/all_stock'/('trade_date='+text[:4]+'-'+text[4:6]+'-'+text[6:]+'.json')
        dates.append({'date':day,'path':str(path),'exists':path.is_file()})
    write_json(root/'EXACT_STATE_METADATA_AUDIT.json',{'history':records,'warmup_all_stock':dates,'historical_rows_read':0,
        'tq_historical_state_capability':'get_stock_info has no as-of parameter; current ST/suspension not used as history'})
    from collections import Counter
    print(json.dumps({'source_headers':dict(Counter(x['status'] for x in records)),
        'warmup_raw_partitions_present':sum(x['exists'] for x in dates),'historical_rows_read':0}))
