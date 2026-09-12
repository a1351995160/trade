"""单对证券别名不会增加候选、持仓身份，也不能丢弃hazard。"""
import importlib
from pathlib import Path

import pandas as pd
import pytest


def test_alias_preserves_other_members_and_all_hazards(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    module = importlib.import_module('baostock_alias_v1')
    item = {'aliases':{'302132.SZ':'300114.SZ'}}
    assert module.execution_symbols(['X','300114.SZ','302132.SZ'],item) == ['X','300114.SZ']
    hazards = {'X':[1],'300114.SZ':[2],'302132.SZ':[2,3]}
    mapped = module.canonical_hazards(hazards,item)
    assert mapped == {'X':[1],'300114.SZ':[2,2,3]}
    assert hazards['302132.SZ'] == [2,3]
    with pytest.raises(ValueError):
        module.execution_symbols(['X','302132.SZ'],item)


def test_alias_state_conflict_is_not_silently_merged(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    module = importlib.import_module('baostock_alias_v1')
    row = dict(trade_date=20220801,listed=True,delisted=False,universe_member=True,
               eligibility_status='ELIGIBLE',st_status='NORMAL',suspension_status='TRADING',board='CHINEXT')
    frame = pd.DataFrame([dict(row,symbol=s) for s in ['300114.SZ','302132.SZ']])
    assert module.verify_states(frame) == 1
    frame.loc[1,'st_status'] = 'ST'
    with pytest.raises(ValueError,match='ALIAS_HISTORICAL_STATE_CONFLICT'):
        module.verify_states(frame)
