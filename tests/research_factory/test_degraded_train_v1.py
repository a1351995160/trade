from chanlun_trader.research_factory.degraded_train_v1 import capacity, hazard_overlap, feasibility_verdict


def test_capacity_requires_schema_and_both_hypotheses():
    assert capacity(999, 100, schema_proven=True)['accepted_qty'] == 99
    assert capacity(0, 100, schema_proven=True)['accepted_qty'] == 0
    assert capacity(1000, 100, schema_proven=True)['accepted_qty'] == 100
    assert capacity(100000, 100, schema_proven=False)['reason'] == 'DEGRADED_VOLUME_MODEL_UNUSABLE'
    assert capacity(-1, 100, schema_proven=True)['accepted_qty'] == 0


def test_hazard_inclusive_unmapped_and_endpoints():
    assert hazard_overlap([1, 3, 5, 7], 3, 5) == [3, 5]


def test_thresholds_are_joint_and_do_not_count_rejections():
    paths = [dict(reason='COMPLETE_CLOSURE_PATH', entry_date=i % 20, symbol=str(i % 2)) for i in range(30)]
    assert feasibility_verdict(paths)['passed']
    assert not feasibility_verdict(paths[:29])['passed']
    assert not feasibility_verdict([{**p, 'symbol': 'ONE'} for p in paths])['passed']
    assert not feasibility_verdict([{**p, 'entry_date': p['entry_date'] % 19} for p in paths])['passed']
    assert not feasibility_verdict([{**p, 'reason': 'PARTICIPATION_LIMIT'} for p in paths])['passed']
