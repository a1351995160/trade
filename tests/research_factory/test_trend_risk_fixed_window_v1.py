import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
NAME='WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20'


def test_original_crossperiod_account_and_budget(tmp_path):
    from test_residual_window_v1 import test_actual_account_external_window_keeps_raw_price_and_fixed_exit as account
    from test_residual_window_v1 import test_canonical_increment_preserves_parent_and_rejects_replay as budget
    from chanlun_trader.research_factory.residual_window_v1 import contract
    from chanlun_trader.research_factory.train_search_batch_v1 import contract as training
    old=training(NAME);new=contract(NAME)
    assert {k for k in old if old[k]!=new[k]}=={'version','adapter_version','purpose','result_type'}
    account(NAME);budget(tmp_path,NAME)


@pytest.mark.parametrize('all_st',[False,True])
def test_exact_existing_input_pipeline_no_turnover(tmp_path,monkeypatch,all_st):
    import prepare_turnover_window_input_v1 as turnover
    def forbidden(*a,**kw):raise AssertionError('UNNEEDED_TURNOVER_ACCESS')
    monkeypatch.setattr(turnover,'guard',forbidden)
    from test_residual_window_v1 import test_reuse_source_to_new_feature_bundle_and_hash_rejection as check
    check(tmp_path,monkeypatch,all_st,NAME)
