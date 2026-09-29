"""一次冻结、完全枚举的引擎方法研究；不读取真实数据或确认资料。"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory import formal_rule_adapter_v3 as adapter
from chanlun_trader.research_factory.formal_statistics_v2 import family_test, METHOD_HASH
from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity

SPEC = {'version': 'ACCOUNT_METHOD_STUDY_V3', 'profile': 'SYNTHETIC_METHOD_DEVELOPMENT',
    'initial_cash': 50000, 'symbols': ['000001.SZ', '000002.SZ', '600000.SH'],
    'max_positions': 3, 'max_symbol_exposure_bps': 3000, 'sessions': 504,
    'benchmark': 'CASH_NO_INTEREST', 'holding_process': 'V3_BUY_ALL_HOLD_252_REENTER_NO_RISK_EXITS',
    'family_size': 5, 'family_dependence': 'FIVE_IDENTICAL_RULES_NO_SELECTION',
    'null': 'TIME_AVERAGE_EXPECTED_DAILY_NET_SIMPLE_EXCESS_LE_ZERO',
    'original_theorem_assumptions': 'COMMON_MEAN_APPROX_INDEPENDENT_NORMAL_GROUP_MEANS_NOT_ASSUMED',
    'method_hash': METHOD_HASH, 'alpha_budget': .025, 'test_alpha': .0125,
    'dgp': 'EQUAL_PROBABILITY_GLOBAL_SIGN_PRICE_10_PLUS_SIGN_TIMES_0.01_TIMES_MAX_ACCOUNT_INDEX_MINUS_1_0',
    'null_atoms': [-1, 1], 'atom_probability': .5, 'control_atom': 0, 'power_atom': 1,
    'enumeration': 'ALL_ATOMS_EXACT_NO_MONTE_CARLO_OR_CONFIDENCE_INTERVAL',
    'error_gate': .025, 'power_gate': .7, 'costs': 'BASE',
    'stop': 'ONE_FROZEN_METHOD_ONE_DGP_NO_REDESIGN_AFTER_RESULTS',
    'generalization': 'COUNTEREXAMPLE_ONLY_NO_MARKET_OR_ARBITRARY_POOL_CERTIFICATION'}


def proposal(cash=False):
    def node(op, *args, **params):
        return {'op': op, 'args': list(args), 'params': params}
    return {'version': 'RESEARCH_RULE_STRATEGY_V3', 'hypothesis': '冻结方法研究',
        'change_reason': '完全枚举共同状态而非策略搜索',
        'buy': node('gt', node('field', 'close'), node('const', value=1000000 if cash else 0)),
        'sell': node('lt', node('field', 'close'), node('const', value=0)),
        'market_filter': None, 'min_hold_sessions': 1, 'max_hold_sessions': 252,
        'cooldown_sessions': 0, 'target_weight': .3, 'indicator_instances': [],
        'exits': {'execution_mode': 'CLOSE_CONFIRM_NEXT_SESSION_OPEN', 'stop_loss_pct': None,
                  'take_profit_pct': None, 'trailing_activate_pct': None, 'trailing_pct': None}}


def inputs(sign):
    days = [int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2020-01-02', periods=584)]
    window = dict(symbols=SPEC['symbols'], calendar=days, feature_start=days[0],
                  account_start=days[80], account_end=days[-1])
    bars, turns, states = [], [], []
    for symbol in SPEC['symbols']:
        previous = 10.
        for i, day in enumerate(days):
            price = 10 + sign * .01 * max(i - 81, 0)
            bars.append(dict(symbol=symbol, date=day, open=price, high=price, low=price,
                close=price, prev_close=previous, volume=1000000., amount=price*1000000, adjustflag='3'))
            turns.append(dict(symbol=symbol, date=day, volume=1000000., turn=1., tradestatus=1))
            if i >= 80:
                states.append(dict(symbol=symbol, trade_date=day, listed=True, delisted=False,
                    universe_member=True, eligibility_status='ELIGIBLE', st_status='NORMAL',
                    suspension_status='TRADING', board='SZ_MAIN' if symbol.endswith('SZ') else 'SH_MAIN'))
            previous = price
    bundle = dict(daily=pd.DataFrame(bars), turn=pd.DataFrame(turns), states=pd.DataFrame(states),
        events=[], corporate_actions_complete=True, calendar=days,
        source_hashes={'fixture': 'SYNTHETIC_METHOD_DEVELOPMENT', 'execution_profile': 'HISTORICAL_MODELED'}, profile='HISTORICAL_MODELED',
        open_snapshots=[], close_snapshots=[])
    return window, bundle


def execute(sign, cash=False):
    window, bundle = inputs(sign)
    identity = rule_input_identity(bundle, window)
    kw = dict(strategy_id='method_cash' if cash else 'method_hold', window=window, costs='BASE',
              scope=SPEC, execution_profile='HISTORICAL_MODELED')
    plan = adapter.prepare_rule(proposal(cash), **kw)
    sid = kw['strategy_id']
    receipt = {'strategy_plans': {sid: plan}, 'input_identity': identity,
        'novelty': {sid: {'allowed': True}}, 'execution_purpose': sid, 'execution_consumed': True}
    result = adapter.run_rule(proposal(cash), bundle=bundle, input_identity=identity,
                              active_check=lambda: receipt, **kw)
    if result['reconciliation'] != {'passed': True, 'days': 504}:
        raise ValueError('METHOD_ACCOUNT_RECONCILIATION_FAILED')
    equities = np.array([50000.] + [row['equity'] for row in result['daily_accounts']])
    return result, equities[1:] / equities[:-1] - 1


def write_new(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(raw + '\n')


def main(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), *sorted((source_root/'src/chanlun_trader').rglob('*.py'))]
    hashes = {str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    frozen = {'spec': SPEC, 'source_hashes': hashes, 'proposal': proposal(), 'benchmark': proposal(True)}
    frozen['protocol_hash'] = stable_hash(frozen)
    # 必须先创建完整冻结原件，已有目录不得覆盖后复用。
    write_new(root/'protocol.json', frozen)
    atoms, returns = {}, {}
    for sign in (-1, 0, 1):
        result, r = execute(sign)
        cash, b = execute(sign, True)
        write_new(root/f'account_{sign}.json', result)
        write_new(root/f'cash_{sign}.json', cash)
        excess = r-b
        returns[sign] = excess
        test = family_test({f'member_{i}': excess for i in range(5)}, alpha=SPEC['test_alpha'])
        atoms[str(sign)] = {'test': test, 'rejected': any(test['supported'].values()),
            'mean_excess': float(excess.mean()), 'result_hash': stable_hash(result),
            'cash_hash': stable_hash(cash), 'excess': excess.tolist()}
    expected = (returns[-1]+returns[1])/2
    valid_null = float(expected.mean()) <= 0
    error = sum(atoms[str(s)]['rejected']*.5 for s in (-1, 1))
    power = float(atoms['1']['rejected'])
    report = {'profile': SPEC['profile'], 'protocol_hash': frozen['protocol_hash'], 'atoms': atoms,
        'expected_daily_excess': expected.tolist(), 'time_average_expected_excess': float(expected.mean()),
        'null_verified_by_complete_enumeration': valid_null, 'exact_family_error': error,
        'exact_power': power, 'error_gate_passed': valid_null and error <= SPEC['error_gate'],
        'power_gate_passed': power >= SPEC['power_gate'],
        'decision': 'UNSUPPORTED' if valid_null and error > SPEC['error_gate'] else 'INCONCLUSIVE',
        'real_publication_eligible': False, 'strategy_qualified': False}
    write_new(root/'report.json', report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('atoms','expected_daily_excess')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    main(parser.parse_args().output)
