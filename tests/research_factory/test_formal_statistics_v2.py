"""V2公式、证据边界与合成过程验证；完整校准由冻结后的独立命令执行。"""
from copy import deepcopy
import math

import numpy as np
import pytest

from chanlun_trader.research_factory import formal_statistics_v2 as stats
from chanlun_trader.research_factory.bounded_research_v1 import _put
from chanlun_trader.research_factory.common import stable_hash


def test_t7_tail_against_quantiles_and_independent_density_integral():
    assert stats.t7_upper_tail(0)==.5
    assert stats.t7_upper_tail(1.8945786050900073)==pytest.approx(.05,abs=1e-12)
    assert stats.t7_upper_tail(2.364624251592784)==pytest.approx(.025,abs=1e-12)
    # t=sqrt(7)tan(theta): density times Jacobian proportional to cos(theta)^6.
    factor=math.gamma(4)/(math.sqrt(math.pi)*math.gamma(3.5))
    for value in (.01,.5,2.,8.,100.):
        theta=np.linspace(math.atan(value/math.sqrt(7)),math.pi/2,20001)
        y=np.cos(theta)**6
        integral=(theta[1]-theta[0])/3*(y[0]+y[-1]+4*y[1:-1:2].sum()+2*y[2:-1:2].sum())*factor
        assert stats.t7_upper_tail(value)==pytest.approx(integral,rel=1e-9,abs=1e-14)
        assert stats.t7_upper_tail(-value)==pytest.approx(1-integral,abs=1e-12)


def test_complete_family_missing_members_remain_in_holm_and_qualification_unset():
    rng=np.random.default_rng(1)
    x=rng.normal(.2,1,504)
    result=stats.family_test({'good':x,'missing':np.full(504,np.nan),'zero':np.zeros(504)},alpha=.025)
    assert result['raw_p']['missing'] is None and result['adjusted_p']['missing'] is None
    assert result['raw_p']['zero']==1
    assert result['adjusted_p']['good']==pytest.approx(min(1,3*result['raw_p']['good']))
    assert result['qualification']=='NOT_ASSESSED'
    assert len(result['group_means']['good'])==8


@pytest.mark.parametrize('values',[np.full(504,.1),np.tile(np.arange(63)-31,8)])
def test_zero_group_variance_is_untestable(values):
    result=stats.family_test({'member':values},alpha=.01)
    assert result['raw_p']['member'] is None and not result['supported']['member']


@pytest.mark.parametrize('alpha',[0,-.1,.051,True,float('nan'),float('inf')])
def test_invalid_alpha(alpha):
    with pytest.raises(ValueError,match='ALPHA'):
        stats.family_test({'a':np.zeros(504)},alpha=alpha)


def test_wrong_window_family_and_nonfinite_statistic():
    with pytest.raises(ValueError,match='504'):stats.family_test({'a':np.zeros(503)},alpha=.01)
    with pytest.raises(ValueError,match='FAMILY'):stats.family_test({},alpha=.01)
    with pytest.raises(ValueError,match='T7'):stats.t7_upper_tail(float('nan'))


def test_new_dgp_seed_reproducibility_and_costs_only_subtract():
    x,seed=stats.generate_dgp('IID',0)
    assert seed[0]==2026092602
    assert np.array_equal(x,stats.generate_dgp('IID',0)[0])
    assert not np.all(x.mean(axis=0)==0)
    condition='COST_DRAG'
    x,seed=stats.generate_dgp(condition,0)
    rng=np.random.default_rng(np.random.SeedSequence(seed))
    innovations=rng.normal(size=(1528,5))
    positions=np.vstack([np.full((1,5),.25),.25+.75*(innovations[:-1]>0)])
    gross=positions*innovations
    changes=np.abs(positions-np.vstack([np.full((1,5),.25),positions[:-1]]))
    cost=.02*changes+.005*positions
    assert np.array_equal(x,(gross-cost)[-504:])
    assert np.all(x<=gross[-504:])


@pytest.fixture
def tiny_records(monkeypatch):
    # 仅缩小证据校验测试，不代表完整8192次验收，亦不更改生产冻结设计。
    monkeypatch.setitem(stats.CALIBRATION_SPEC,'replicates',1)
    return [stats.calibration_record(c,0) for c in (*stats.SUPPORT,*stats.STRESS)]


def test_all_null_families_and_power_target_are_explicit(tiny_records):
    summary=stats.summarize_records(tiny_records,verify_data=True)
    assert len(summary['conditions'])==18*3*3
    assert len(summary['power'])==12
    assert all(row['target_member']==0 and row['replicates']==1 for row in summary['power'])
    assert sum(row['minimum_cp_lower'] is not None for row in summary['power'])==2
    assert summary['real_strategy_qualification'] is False
    shifts=stats._variants('IID')['POWER_SR_4']
    assert shifts[0]>0 and np.count_nonzero(shifts)==1


@pytest.mark.parametrize('mutation,code',[
    ('seed','SEED'),('probability','PVALUE'),('statistic','STATISTIC'),('data','REPLAY'),('duplicate','MEMBERSHIP'),('missing','INCOMPLETE')])
def test_records_reject_tampering(tiny_records,mutation,code):
    records=deepcopy(tiny_records)
    if mutation=='seed':records[0]['data_seed'][0]+=1
    elif mutation=='probability':records[0]['raw_p']['ALL_NULL'][0]=1.1
    elif mutation=='statistic':records[0]['statistics']['ALL_NULL'][0]+=1
    elif mutation=='data':records[0]['data_hash']='0'*64
    elif mutation=='duplicate':records.append(records[0])
    elif mutation=='missing':records.pop()
    with pytest.raises(ValueError,match=code):stats.summarize_records(records,verify_data=True)


def test_loader_checks_preregistration_sources_and_summary(tmp_path,tiny_records,monkeypatch):
    sources=stats.source_hashes()
    prereg={'spec':deepcopy(stats.CALIBRATION_SPEC),'sources':sources,'method_hash':stats.METHOD_HASH}
    _put(tmp_path/'PREREGISTRATION.json',prereg)
    summary=stats.summarize_records(tiny_records)
    report={'method_hash':stats.METHOD_HASH,'records':tiny_records,'records_hash':stable_hash(tiny_records),
            'preregistration_hash':stable_hash(prereg),'summary':summary}
    _put(tmp_path/'CALIBRATION.json',report)
    loaded=stats.load_calibration(tmp_path/'CALIBRATION.json')
    assert loaded['method_approved']==(summary['support_passed'] and summary['power_passed'])
    altered=deepcopy(report)
    altered['summary']['support_passed']=not summary['support_passed']
    _put(tmp_path/'ALTERED.json',altered)
    with pytest.raises(ValueError,match='SUMMARY'):stats.load_calibration(tmp_path/'ALTERED.json')
    pin=stable_hash(loaded)
    def unexpected_replay(*args,**kwargs):
        raise AssertionError('固定审核hash快路径不得重复完整DGP')
    monkeypatch.setattr(stats,'summarize_records',unexpected_replay)
    assert stats.load_calibration(tmp_path/'CALIBRATION.json',expected_hash=pin)==loaded
    with pytest.raises(ValueError,match='REVIEWED_HASH'):
        stats.load_calibration(tmp_path/'CALIBRATION.json',expected_hash='0'*64)
    with pytest.raises(ValueError,match='REVIEWED_HASH'):
        stats.load_calibration(tmp_path/'ALTERED.json',expected_hash=pin)
    with pytest.raises(AssertionError,match='完整DGP'):
        stats.load_calibration(tmp_path/'CALIBRATION.json')
    monkeypatch.setattr(stats,'source_hashes',lambda:{**sources,'unexpected':'0'*64})
    with pytest.raises(ValueError,match='BINDING'):stats.load_calibration(tmp_path/'CALIBRATION.json')


def test_cp_lower_inverts_upper_and_power_is_not_family_any_rejection(tiny_records):
    assert 1-stats.cp_upper(0,8192,.025)>.99
    assert 1-stats.cp_upper(8192,8192,.025)==0
    iid=tiny_records[0]
    means=np.array(iid['group_means'])
    raw,_=stats._variant_results('IID',means,iid['all_zero'])
    for variant in ('POWER_SR_0.5','POWER_SR_1','POWER_SR_2','POWER_SR_4'):
        assert raw[variant][1:]==raw['ALL_NULL'][1:]
