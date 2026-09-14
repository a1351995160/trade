import pytest
NAME='WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20'


def test_frozen_account_and_own_budget(tmp_path):
    from test_residual_window_v1 import test_actual_account_external_window_keeps_raw_price_and_fixed_exit as account
    from test_residual_window_v1 import test_canonical_increment_preserves_parent_and_rejects_replay as budget
    from chanlun_trader.research_factory.residual_window_governance_v1 import ResidualWindowGovernanceV1
    from chanlun_trader.research_factory.residual_window_v1 import contract
    from chanlun_trader.research_factory.train_search_batch_v1 import contract as training
    old=training(NAME);new=contract(NAME)
    assert {k for k in old if old[k]!=new[k]}=={'version','adapter_version','purpose','result_type'}
    a=ResidualWindowGovernanceV1(tmp_path,NAME);b=ResidualWindowGovernanceV1(tmp_path,'WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20')
    assert a.receipt_path!=b.receipt_path and a.main_purpose!=b.main_purpose
    account(NAME);budget(tmp_path,NAME)


@pytest.mark.parametrize('all_st',[False,True])
def test_existing_full_input_pipeline(tmp_path,monkeypatch,all_st):
    from test_residual_window_v1 import test_reuse_source_to_new_feature_bundle_and_hash_rejection
    test_reuse_source_to_new_feature_bundle_and_hash_rejection(tmp_path,monkeypatch,all_st,NAME)
