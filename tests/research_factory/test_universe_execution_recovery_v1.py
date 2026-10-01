from copy import deepcopy
import json

import pandas as pd
import pytest

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.engine_replay_recovery_v1 import ReplayInterrupted
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3
from chanlun_trader.research_factory.strategy_interface_v1 import prepare
from chanlun_trader.research_factory.universe_account_backend_v1 import UniverseAccountBackendV1
from chanlun_trader.research_factory.universe_account_inputs_v1 import universe_input_identity_v1
from universe_test_fixture_v1 import fixture, proposal
from test_universe_account_backend_v1 import account_result


def test_interruption_after_daily_commit_reuses_frozen_execution_and_rebuilds_exact_result(tmp_path):
    window, bundle = fixture()
    strategy = ResearchRuleStrategyV3(proposal({'stop_loss_pct': .05}), strategy_id='REPLAY')
    path = tmp_path / 'CHECKPOINT.json'
    backend = UniverseAccountBackendV1(window, checkpoint_path=path)
    identity = universe_input_identity_v1(bundle, window)
    receipt = {'strategy_plans': {'REPLAY': prepare(strategy, backend)}, 'input_identity': identity,
               'execution_consumed': True, 'execution_purpose': 'REPLAY'}
    first = backend.run(strategy, bundle, bundle['events'], lambda: receipt)
    path.unlink()
    with pytest.raises(ReplayInterrupted):
        backend.run(strategy, bundle, bundle['events'], lambda: receipt, stop_after_date=window['calendar'][65])
    committed = json.loads(path.read_text(encoding='utf-8'))
    assert committed['budget_reused'] is True
    assert committed['last_day'] == window['calendar'][65]
    second = backend.run(strategy, bundle, bundle['events'], lambda: receipt)
    assert first == second
    assert len({fill['trade_id'] for fill in second['fills']}) == len(second['fills'])


def test_changed_checkpoint_prefix_cannot_be_resumed(tmp_path):
    window, bundle = fixture()
    strategy = ResearchRuleStrategyV3(proposal(), strategy_id='REPLAY')
    path = tmp_path / 'CHECKPOINT.json'
    backend = UniverseAccountBackendV1(window, checkpoint_path=path)
    identity = universe_input_identity_v1(bundle, window)
    with pytest.raises(ReplayInterrupted):
        backend.run(strategy, bundle, [], lambda: {'input_identity': identity}, stop_after_date=window['calendar'][62])
    body = json.loads(path.read_text(encoding='utf-8'))
    body['prefix_hash'] = 'f' * 64
    body.pop('receipt_hash')
    path.write_text(json.dumps({**body, 'receipt_hash': stable_hash(body)}), encoding='utf-8')
    with pytest.raises(ValueError, match='RECOVERY_PREFIX_CONFLICT'):
        backend.run(strategy, bundle, [], lambda: {'input_identity': identity})


def test_halt_does_not_advance_bar_axis_and_resume_uses_prior_exchange_volume():
    window, bundle = fixture(symbols=['000001.SZ', '300001.SZ'], prices=[12., 12., 11.4])
    symbol, days = '300001.SZ', window['calendar']
    original = deepcopy(bundle['states'].loc[bundle['states'].symbol == symbol].iloc[0].to_dict())
    before, halted, after = (deepcopy(original) for _ in range(3))
    before['valid_to'] = days[62]
    halted.update(effective_date=days[63], valid_to=days[68], suspension_status='SUSPENDED')
    after['effective_date'] = days[69]
    bundle['states'] = pd.concat([bundle['states'].loc[bundle['states'].symbol != symbol],
                                 pd.DataFrame([before, halted, after])], ignore_index=True)
    bundle['daily'] = bundle['daily'].loc[~((bundle['daily'].symbol == symbol)
        & bundle['daily'].date.between(days[63], days[68]))].copy()
    result = account_result(bundle, window, exits={'stop_loss_pct': .04})
    dates = {row['date']: row for row in result['daily_accounts']}
    assert dates[days[65]]['stale_valuations'] == [{'symbol': symbol, 'status': 'STALE_VERIFIED_SUSPENSION'}]
    exits = result['final_account_checkpoint']['rule_exit_states']['universe']['evaluations']
    assert not any(row['symbol'] == symbol and days[63] <= row['date'] <= days[68] for row in exits)
    sold = [t for t in result['fills'] if t['symbol'] == symbol and t['side'] == 'SELL']
    assert sold
    assert int(pd.Timestamp(sold[0]['fill_time']).strftime('%Y%m%d')) == days[70]
