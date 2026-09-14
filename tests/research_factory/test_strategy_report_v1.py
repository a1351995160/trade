from dataclasses import FrozenInstanceError
import pytest
from chanlun_trader.research_factory.etf_grid_spec_v1 import SPEC
from chanlun_trader.research_factory.strategy_report_v1 import build_report, render_markdown


def args():
    return dict(strategy=SPEC.as_dict(),stage='INPUT_CHECK',status='NOT_READY',reasons=['MISSING_DATA'],
                metrics=dict.fromkeys(['net_return','max_drawdown','total_fees','trade_count']),
                artifacts={'source_result':{'path':'SYNTHETIC.json','sha256':'SYNTHETIC'},'trades':None,'ledger':None},
                benchmark={'status':'NOT_RUN'},limitations=['SYNTHETIC'])


def test_config_cannot_be_changed_by_report_consumer():
    with pytest.raises(FrozenInstanceError):SPEC.grid_quantity=100
    copy=SPEC.as_dict();copy['ma_periods'][0]=1
    assert SPEC.ma_periods==(20,60,120)
    assert SPEC.core_budget+SPEC.grid_budget+SPEC.cash_reserve==SPEC.initial_cash


def test_missing_results_are_not_zero_and_failure_is_preserved():
    inputs=args();report=build_report(**inputs)
    inputs['reasons'].clear()
    assert report['reasons']==['MISSING_DATA']
    assert all(v is None for v in report['metrics'].values())
    assert '尚未生成' in render_markdown(report)
    assert report['qualification']=='NOT_ASSESSED'
    inputs['metrics']['net_return']=.1
    with pytest.raises(ValueError):build_report(**inputs)


def test_account_report_preserves_loss_without_qualification_upgrade():
    inputs=args();inputs['stage']='ACCOUNT_BACKTEST'
    inputs['metrics']=dict(net_return=-.12,max_drawdown=.2,total_fees=21.,trade_count=2)
    report=build_report(**inputs)
    assert report['metrics']['net_return']==-.12
    assert report['benchmark']['status']=='NOT_RUN'
    assert report['execution_authorization_granted'] is False
