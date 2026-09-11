"""OWNER读取/限窗/唯一样本规则的确定型验证。"""
import struct
import pytest
from chanlun_trader.data.tdx.owner_export_v1 import read_day_window,compare_sources,OwnerDailyProviderV1


def test_day_window_never_returns_sealed_prices(tmp_path):
    path=tmp_path/'sh600000.day'
    path.write_bytes(b''.join(struct.pack('<IIIIIfII',d,1000,1000,1000,1000,1.,100,0) for d in [20220722,20240731,20240801]))
    rows,identity=read_day_window(path,[20220722,20240731])
    assert [r['date'] for r in rows]==[20220722,20240731]
    assert len(identity['window_sha256'])==64


def fixture():
    return ([dict(date=20220801,open=10,high=11,low=9,close=10,volume_encoded=10000,amount_encoded=100000)],
        [dict(date=20220801,open=10,high=11,low=9,close=10,volume=100,amount=10)])


def test_unique_multipliers_do_not_erase_absolute_unit_question():
    a,b=fixture();r=compare_sources(a,b)
    assert r['price_semantics_compatible']
    assert r['scales']['volume']['unique_ratio']==100
    assert r['scales']['amount']['unique_ratio']==10000


@pytest.mark.parametrize('mode',['price','date','volume','zero'])
def test_every_frozen_row_counts_even_conflict_or_zero(mode):
    a,b=fixture()
    if mode=='price':b[0]['close']=10.02
    if mode=='date':b[0]['date']=20220802
    if mode=='volume':b[0]['volume']=99
    if mode=='zero':a[0]['volume_encoded']=0;b[0]['volume']=0
    r=compare_sources(a,b)
    assert not r['price_semantics_compatible'] or r['scales']['volume']['unique_ratio'] is None


def test_tq_outside_response_is_never_returned_to_writer():
    class Client:
        def request(self,method,params,**kwargs):
            assert params['dividend_type']=='none' and params['fill_data'] is False and params['count']==0
            return {'600000.SH':{'Date':['20240801'],**{k:[1] for k in ['Open','High','Low','Close','Volume','Amount']}}}
    with pytest.raises(PermissionError,match='WINDOW_VIOLATION'):OwnerDailyProviderV1(Client()).get_daily('600000.SH')


def test_gbbq_other_market_is_not_a_parse_failure_and_window_is_filtered(tmp_path,monkeypatch):
    import pandas as pd
    from pytdx.reader import GbbqReader
    from chanlun_trader.data.tdx.owner_export_v1 import parse_gbbq_window
    path=tmp_path/'gbbq';path.write_bytes(struct.pack('<I',3)+bytes(29*3))
    monkeypatch.setattr(GbbqReader,'get_df',lambda self,name:pd.DataFrame({'market':[1,2,1],
        'code':['600000','430001','600000'],'datetime':[20220722,20220722,20240801]}))
    window,audit=parse_gbbq_window(path)
    assert list(window.symbol)==['600000.SH'] and list(window.datetime)==[20220722]
    assert audit['input_records']==3 and audit['outside_discarded']==1


def load_script(name):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'scripts'/name
    spec=importlib.util.spec_from_file_location('owner_test_'+name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_absolute_units_anchor_and_single_conflict_rejects():
    module=load_script('prove_owner_units_v1.py');a,b=fixture()
    sample={'symbol':'600000.SH','tdx':a,'tq':b}
    result=module.prove([sample])
    assert result['status']=='VERIFIED_DERIVED_UNIT_EVIDENCE' and result['volume_unit']=='SHARES'
    a[0]['amount_encoded']=500000;b[0]['amount']=50
    assert module.prove([sample])['status']=='UNKNOWN'


def test_validator_never_calls_a_missing_manifest_pass(tmp_path):
    module=load_script('validate_owner_execution_package.py')
    assert module.validate_package(tmp_path,{'required_members':['600000.SH']},'a'*64)['status']=='NOT_READY'


def test_state_prefix_stops_before_mixed_rows(tmp_path):
    module=load_script('audit_owner_state_metadata_v1.py');path=tmp_path/'history.json'
    prefix=b'{"fields":["date","isST"],"rows":'
    path.write_bytes(prefix+b'[{"date":"2025-01-01","isST":"1"}]}')
    metadata,count=module.metadata_prefix(path)
    assert metadata=={'fields':['date','isST']} and count==len(prefix)


def test_complete_synthetic_package_passes_independent_validation_and_atomic_delivery(tmp_path):
    import json
    import pandas as pd
    from chanlun_trader.data.tdx.owner_export_v1 import sha
    stage=tmp_path/'stage';stage.mkdir();symbol='600000.SH';warm=[20220722,20220725,20220726,20220727,20220728,20220729]
    pd.DataFrame([dict(symbol=symbol,date=20220801,open=10,high=10,low=10,close=10,volume_encoded=100,amount_encoded=1000)]).to_parquet(stage/'daily_supplement.parquet',index=False)
    pd.DataFrame([dict(symbol=symbol,trade_date=day,universe_member=True,listed=True,delisted=False,board='MAIN',
        st_status='NORMAL',suspension_status='TRADING',eligibility_status='ELIGIBLE',
        observed_available_at=str(pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=8)),observed_time_source='SYNTHETIC') for day in warm]).to_parquet(stage/'state_supplement.parquet',index=False)
    pd.DataFrame([dict(symbol=symbol,effective_date=20220722,listed=True,delisted=False,source='SYNTHETIC')]).to_parquet(stage/'security_master.parquet',index=False)
    (stage/'unit_source_evidence.json').write_text('{"source":"SYNTHETIC"}')
    (stage/'units.json').write_text(json.dumps({'volume_unit':'SHARES','amount_unit':'CNY','price_mode':'RAW','note':'合成单位证明',
        'evidence_sha256':sha(stage/'unit_source_evidence.json')},ensure_ascii=False),encoding='utf-8')
    (stage/'events.jsonl').write_text('')
    (stage/'actions_manifest.json').write_text(json.dumps({'dataset_id':'SYNTHETIC','version':'WindowedCorporateActionDatasetV1',
        'start':20220722,'end':20240731,'source_identity':'SYNTHETIC','events_file':'events.jsonl','events_sha256':sha(stage/'events.jsonl'),
        'coverage':{'complete':True,'symbols':[symbol]},'physical_window_attestation':{'owner':'USER_AUTHORIZED_LOCAL_DATA_OWNER',
            'window_enforced_before_export':True,'not_derived_from_current_incident':True,'start':20220722,'end':20240731}}))
    auth=tmp_path/'authorization.txt';auth.write_text('SYNTHETIC_ONLY')
    destination=tmp_path/'delivered'
    result=load_script('finalize_owner_execution_package.py').finalize(stage,{'required_members':[symbol],
        'missing_source_symbols':[symbol],'warmup_dates':warm},auth,destination)
    assert result['status']=='DELIVERED'
    assert sha(destination/'OWNER_DELIVERY_MANIFEST.json')==sha(stage/'OWNER_DELIVERY_MANIFEST.json')
    assert not destination.with_name('delivered.pending').exists()
