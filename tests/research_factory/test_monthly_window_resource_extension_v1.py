"""新增时间只扩展数据资源，不改账户额度、原期限或已消费记录。"""
from datetime import datetime,timezone
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from monthly_window_resource_extension_v1 import validate
from chanlun_trader.research_factory.common import stable_hash

PARENT={'identity':'SYNTHETIC_PARENT','account_seconds':5400,'expires_at':'2026-09-14T10:05:03+08:00'}


def extension(**changes):
    value={'parent_release_identity':PARENT['identity'],'old_data_seconds':10800,
        'additional_data_seconds':10800,'total_data_seconds':21600,'account_seconds':5400,
        'expires_at':PARENT['expires_at'],'main_limit':1,'repair_limit':1,**changes}
    value['identity']=stable_hash(value)
    return value


def test_only_data_time_increases():
    assert validate(extension(),PARENT,datetime(2026,9,12,tzinfo=timezone.utc))==21600
    assert PARENT['account_seconds']==5400


@pytest.mark.parametrize('change',[{'main_limit':2},{'repair_limit':2},{'account_seconds':10800},
    {'expires_at':'2026-09-15T10:05:03+08:00'},{'parent_release_identity':'OTHER'},
    {'total_data_seconds':21601},{'old_data_seconds':0}])
def test_other_boundaries_stay_fixed(change):
    with pytest.raises(PermissionError,match='SCOPE_CHANGED'):
        validate(extension(**change),PARENT,datetime(2026,9,12,tzinfo=timezone.utc))


def test_expired_and_corrupt_revision_rejected():
    with pytest.raises(PermissionError,match='EXPIRED'):
        validate(extension(),PARENT,datetime(2026,9,15,tzinfo=timezone.utc))
    value=extension();value['total_data_seconds']=999999
    with pytest.raises(PermissionError,match='IDENTITY_CHANGED'):
        validate(value,PARENT,datetime(2026,9,12,tzinfo=timezone.utc))
