import pytest
from chanlun_trader.research_factory.response_confirmation_signals_v1 import NAMES
from chanlun_trader.research_factory.residual_window_v1 import contract
from chanlun_trader.research_factory.train_search_batch_v1 import contract as training
from chanlun_trader.research_factory.fixed_account_rules import FixedAccountRules


@pytest.mark.parametrize('name',NAMES)
def test_contract_isolation_and_original_account(name):
    from test_residual_window_v1 import test_actual_account_external_window_keeps_raw_price_and_fixed_exit as check
    original=training(name);current=contract(name)
    assert {k for k in original if original[k]!=current[k]}=={'version','adapter_version','purpose','result_type'}
    assert FixedAccountRules(current).signal_window==(20250801,20260731)
    bad={**current,'signal_window':[20250801,20260801]}
    with pytest.raises(PermissionError):FixedAccountRules(bad)
    bad={**current,'factor_id':'MISSING'}
    with pytest.raises(PermissionError):FixedAccountRules(bad)
    check(name)


@pytest.mark.parametrize('name',NAMES)
def test_canonical_increment_repeat_revocation_and_old_parent(name,tmp_path):
    from test_residual_window_v1 import test_canonical_increment_preserves_parent_and_rejects_replay as check
    check(tmp_path,name)


@pytest.mark.parametrize('name',NAMES)
def test_existing_source_materialization_and_hash_checks(name,tmp_path,monkeypatch):
    from test_residual_window_v1 import test_reuse_source_to_new_feature_bundle_and_hash_rejection as check
    check(tmp_path,monkeypatch,False,name)
