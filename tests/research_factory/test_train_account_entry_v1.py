"""入口只用临时合成文件核验物理窗口与读取清单。"""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


def entry():
    path=Path(__file__).resolve().parents[2]/'scripts/run_train_account_v1.py'
    spec=importlib.util.spec_from_file_location('train_entry_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_footer_rejects_sealed_rows_before_frame_read(tmp_path,monkeypatch):
    m=entry();path=tmp_path/'mixed.parquet'
    pd.DataFrame({'date':[20240731,20240801],'close':[1,2]}).to_parquet(path,index=False)
    import pyarrow.parquet as pq
    monkeypatch.setattr(pq,'read_table',lambda *a,**k:pytest.fail('must not read rows'))
    with pytest.raises(PermissionError,match='WINDOW_CONFLICT'):m.bounded_frame(path,'date')


def test_unlisted_owner_role_is_rejected_before_file_reads(tmp_path,monkeypatch):
    m=entry();manifest=tmp_path/'OWNER_DELIVERY_MANIFEST.json'
    manifest.write_text(json.dumps({'version':'OWNER_EXECUTION_EXPORT_V1','start':20220722,'end':20240731,
        'physical_window_attestation':{'owner':'SYNTHETIC','window_enforced_before_export':True,'start':20220722,'end':20240731},
        'files':{'unapproved':{'file':'arbitrary.json','sha256':'a'*64}}}),encoding='utf-8')
    monkeypatch.setattr(m,'OWNER',manifest)
    monkeypatch.setattr(m,'sha',lambda *a:pytest.fail('must not read file bytes'))
    with pytest.raises(ValueError,match='FIELDS_MISSING_OR_EXTRA'):m.owner_files()
