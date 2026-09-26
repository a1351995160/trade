"""统一命令入口的错误边界和业务日报。"""
import json

from scripts import run_strategy_lifecycle_v1 as cli


def test_config_cannot_inject_clock_or_qualification(tmp_path, capsys):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'qualified':True}), encoding='utf-8')
    assert cli.main(['create-paper','--root',str(tmp_path/'paper'),'--config',str(config)]) == 1
    assert json.loads(capsys.readouterr().out)['reason'] == 'PAPER_CONFIG_UNKNOWN_FIELDS'
    assert not (tmp_path/'paper').exists()


def test_missing_session_is_blocked_instead_of_empty_success(tmp_path, capsys):
    assert cli.main(['status','--root',str(tmp_path/'missing')]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED'


def test_report_keeps_engineering_and_formal_days_distinct():
    text = cli.render_report({'status':'WAITING_DATA','profile':'REAL_OBSERVED',
        'purpose':'ENGINEERING_OBSERVATION','real_observation_days':3,'qualified_observation_days':0,
        'state':{'equity':1000,'economic':{'cash':500,'trades':[{}]}},
        'next_plan':{'next_session':20260929,'intents':[{'strategy_id':'S1','symbol':'600000.SH','side':'BUY','reason':'RULE'}]},
        'limitations':['模拟收益不是实盘收益。']})
    assert '真实观察：3 天；其中合格观察：0 天' in text
    assert '累计模拟成交：1 笔' in text and '20260929' in text
    assert '| S1 | 600000.SH | BUY | RULE |' in text
    assert '尚未取得正式资格' in text
