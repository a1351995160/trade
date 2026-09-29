"""公共数据入口的合成原件测试；不读取本机行情或执行账户。"""
import hashlib
import json
import shutil

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_data_provider_v1 import ResearchDataProviderV1
from chanlun_trader.research.guard import FinalTestAccessViolation


def dataset(root, symbols=('000003.SZ', '600004.SH')):
    root.mkdir(exist_ok=True)
    dates = pd.bdate_range('2023-01-02', periods=64)
    first, last = dates[0].strftime('%Y-%m-%d'), dates[-1].strftime('%Y-%m-%d')
    files = {}
    def put(name, api, request, fields, rows):
        raw = json.dumps({'provider': 'BaoStock', 'api': api, 'request': request,
            'error_code': '0', 'mode': 'HISTORICAL_MODELED',
            'historical_available_at_verified': False, 'fields': fields, 'raw_rows': rows,
            'raw_rows_sha256': hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()}).encode()
        (root / name).write_bytes(raw)
        files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'start': first, 'end': last}
    window = {'start_date': first, 'end_date': last}
    put('TRADE_DATES.json', 'query_trade_dates', window,
        ['calendar_date', 'is_trading_day'], [[d.strftime('%Y-%m-%d'), '1' if d in dates else '0']
        for d in pd.date_range(first, last)])
    for symbol in symbols:
        code = ('sz.' if symbol.endswith('SZ') else 'sh.') + symbol[:6]
        fields = ['date','code','open','high','low','close','preclose','volume','amount','turn','adjustflag','tradestatus','isST']
        rows = [[d.strftime('%Y-%m-%d'),code,'10','11','9','10','10','1000','10000','1','3','1','0'] for d in dates]
        put(f'DAILY_{symbol}.json', 'query_history_k_data_plus',
            {**window, 'code': code, 'frequency': 'd', 'adjustflag': '3'}, fields, rows)
        put(f'DIVIDEND_{symbol}_2023.json', 'query_dividend_data',
            {'code': code, 'year': '2023', 'yearType': 'report'}, ['code'], [])
        put(f'ADJUST_{symbol}.json', 'query_adjust_factor',
            {**window, 'code': code}, ['code','dividOperateDate'], [])
    manifest = {'adapter': 'BAOSTOCK_RESPONSE_JSON_V1', 'symbols': list(symbols),
                'start': first, 'end': last, 'files': files}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    args = {'symbols': list(symbols), 'feature_start': first,
            'account_start': dates[60].strftime('%Y-%m-%d'), 'account_end': last,
            'authorization': {'authorization_id': 'authorized', 'purpose': 'EXPLORATORY',
                'dataset_ids': ['sample'], 'start': first, 'end': last}}
    return args


def provider(root, events):
    value = ResearchDataProviderV1({'root': root}, events.append)
    value.register('sample', 'root', 'manifest.json')
    return value


def test_metadata_does_not_read_market_or_prove_independence(tmp_path):
    dataset(tmp_path)
    events = []
    service = provider(tmp_path, events)
    assert service.catalog()['content_read'] is False
    assert service.catalog()['datasets'][0]['historical_independence'] == 'UNKNOWN'
    assert events == []


def test_arbitrary_pool_and_window_have_content_identity(tmp_path):
    args = dataset(tmp_path / 'one')
    events = []
    result = provider(tmp_path / 'one', events).prepare('sample', **args)
    assert result['window']['symbols'] == ['000003.SZ', '600004.SH']
    assert len(result['bundle']['daily']) == 128
    assert result['qualification']['historical_availability'] == 'MODELED'
    assert not result['qualification']['independent_confirmation_eligible']
    shutil.copytree(tmp_path / 'one', tmp_path / 'two')
    other = provider(tmp_path / 'two', []).prepare('sample', **args)
    assert result['input_identity'] == other['input_identity']
    assert events[0]['event'] == 'DATA_CONTENT_READ_ATTEMPT'
    assert events[-1]['event'] == 'DATA_BUNDLE_PREPARED'


def test_same_path_changed_content_rejected(tmp_path):
    args = dataset(tmp_path)
    service = provider(tmp_path, [])
    with (tmp_path / 'TRADE_DATES.json').open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='DATA_SOURCE_CONTENT_CHANGED'):
        service.prepare('sample', **args)


@pytest.mark.parametrize('purpose', ['FORMAL', 'INDEPENDENT', 'PAPER'])
def test_unqualified_purpose_refused_before_content(tmp_path, purpose):
    args = dataset(tmp_path)
    events = []
    with pytest.raises(ValueError, match='DATA_PURPOSE_NOT_QUALIFIED'):
        provider(tmp_path, events).prepare('sample', purpose=purpose, **args)
    assert not events


def test_whole_file_access_not_just_slice_requires_authority(tmp_path):
    args = dataset(tmp_path)
    args['feature_start'] = '2023-01-03'
    args['authorization']['start'] = '2023-01-03'
    events = []
    with pytest.raises(ValueError, match='DATA_WHOLE_SOURCE_NOT_AUTHORIZED'):
        provider(tmp_path, events).prepare('sample', **args)
    assert not events


def test_sealed_request_rejected_before_read(tmp_path):
    args = dataset(tmp_path)
    args['account_end'] = '2025-08-01'
    events = []
    with pytest.raises(FinalTestAccessViolation):
        provider(tmp_path, events).prepare('sample', **args)
    assert not events


def test_missing_security_never_silently_shrinks_pool(tmp_path):
    args = dataset(tmp_path)
    args['symbols'].append('000005.SZ')
    with pytest.raises(ValueError, match='DATA_UNIVERSE_NOT_COVERED'):
        provider(tmp_path, []).prepare('sample', **args)


def test_path_escape_and_non_digest_rejected(tmp_path):
    dataset(tmp_path)
    path = tmp_path / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files']['TRADE_DATES.json']['sha256'] = 'E:/data/calendar.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='DATA_SOURCE_IDENTITY_INVALID'):
        provider(tmp_path, [])
    with pytest.raises(ValueError, match='DATA_PATH_OUTSIDE_ROOT'):
        ResearchDataProviderV1({'root': tmp_path}, list().append).register('bad', 'root', '../x')


def test_recorder_failure_prevents_content_access(tmp_path):
    args = dataset(tmp_path)
    def fail(event):
        raise RuntimeError('ledger unavailable')
    service = ResearchDataProviderV1({'root': tmp_path}, fail)
    service.register('sample', 'root', 'manifest.json')
    with pytest.raises(RuntimeError, match='ledger unavailable'):
        service.prepare('sample', **args)


def rewrite_response(root, name, mutate):
    path = root / name
    value = json.loads(path.read_text())
    mutate(value)
    value['raw_rows_sha256'] = hashlib.sha256(json.dumps(value['raw_rows'],
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][name]['sha256'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


@pytest.mark.parametrize('corruption', ['missing_day', 'duplicate_day', 'suspended', 'bad_price', 'missing_turn'])
def test_invalid_vendor_payload_is_not_promoted(tmp_path, corruption):
    args = dataset(tmp_path)
    def mutate(value):
        if corruption == 'missing_day':
            value['raw_rows'].pop()
        elif corruption == 'duplicate_day':
            value['raw_rows'].append(value['raw_rows'][-1])
        elif corruption == 'suspended':
            value['raw_rows'][0][value['fields'].index('tradestatus')] = '0'
        elif corruption == 'bad_price':
            value['raw_rows'][0][value['fields'].index('high')] = '1'
        else:
            index = value['fields'].index('turn')
            value['fields'].pop(index)
            for row in value['raw_rows']:
                row.pop(index)
    rewrite_response(tmp_path, 'DAILY_000003.SZ.json', mutate)
    with pytest.raises(ValueError):
        provider(tmp_path, []).prepare('sample', **args)


def test_non_cash_action_cannot_claim_complete_coverage(tmp_path):
    args = dataset(tmp_path)
    def mutate(value):
        value['fields'] = ['code','dividOperateDate','dividRegistDate','dividStocksPs',
            'dividReserveToStockPs','dividStockMarketDate','dividPayDate','dividPlanDate','dividCashPsBeforeTax']
        value['raw_rows'] = [['sz.000003','2023-03-29','2023-03-28','0.1','0','',
                             '2023-03-29','2023-03-20','0']]
    rewrite_response(tmp_path, 'DIVIDEND_000003.SZ_2023.json', mutate)
    with pytest.raises(ValueError, match='NONCASH_OR_UNSUPPORTED_ACTION'):
        provider(tmp_path, []).prepare('sample', **args)


def test_external_manifest_keeps_raw_directory_unchanged(tmp_path):
    root = tmp_path / "raw"
    args = dataset(root)
    original = (root / "manifest.json").read_bytes()
    external = tmp_path / "deployment-manifest.json"
    external.write_bytes(original)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    service = ResearchDataProviderV1({"root": root}, lambda event: None)
    service.register_manifest("sample", "root", external)
    assert service.prepare("sample", **args)["qualification"]["manifest_hash"] == hashlib.sha256(original).hexdigest()
    assert before == {path.name: path.read_bytes() for path in root.iterdir()}
    unsafe = json.loads(original)
    unsafe["files"]["../outside.json"] = next(iter(unsafe["files"].values()))
    external.write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(ValueError, match="OUTSIDE_ROOT"):
        ResearchDataProviderV1({"root": root}, lambda event: None).register_manifest("other", "root", external)
