"""冻结的八组均值 t7 检验与有限合成过程校准；不授予策略资格。"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from .bounded_research_v1 import _read
from .common import stable_hash
from .formal_statistics_v1 import cp_upper, CALIBRATION_SPEC as V1_SPEC
from .holm_family_v1 import adjust_family

METHOD_SPEC = {
    'version':'FORMAL_STATISTICS_V2','sessions':504,'max_family':5,
    'groups':8,'group_sessions':63,'degrees_of_freedom':7,
    'statistic':'SQRT_8_TIMES_MEAN_GROUP_MEAN_OVER_SAMPLE_SD_GROUP_MEANS',
    'null':'COMMON_EXPECTED_DAILY_NET_SIMPLE_EXCESS_RETURN_LE_ZERO',
    'pvalue':'ONE_SIDED_STUDENT_T7_UPPER_TAIL','adjustment':'HOLM',
    'alpha_budgets':[.025,.015,.010],'test_alpha_fraction':.5,'maximum_test_alpha':.05,
    'constant_rule':'ALL_ZERO_P1_OTHER_ZERO_GROUP_VARIANCE_UNTESTABLE',
    'assumptions':['COMMON_GROUP_EXPECTATION','APPROXIMATELY_INDEPENDENT_NORMAL_GROUP_MEANS'],
    'interpretation':'CONDITIONAL_FINITE_DGP_VALIDATION_NOT_GENERAL_MARKET_GUARANTEE',
}
SUPPORT = ('IID','SKEW','AR_NEG03','AR03','AR06','MA10','COMMON_SHOCK','T5','GARCH',
           'ZERO','NEGATIVE_MEAN','VOL_BREAK','PREDICTABLE_EXPOSURE','COST_DRAG')
STRESS = ('AR09','T3','MEAN_BREAK','TREND')
FAMILIES = ('ALL_NULL','ONE_TRUE_NULL','THREE_TRUE_NULLS')
CALIBRATION_SPEC = {
    'version':'FORMAL_STATISTICS_CALIBRATION_V2','method':METHOD_SPEC,
    'replicates':8192,'family_size':5,'seed':2026092602,'burn_in':1024,
    'support':list(SUPPORT),'stress':list(STRESS),'families':list(FAMILIES),
    'support_checks':126,'simultaneous_error':.05,'power_simultaneous_error':.05,
    'acceptance':'EVERY_SUPPORT_CP_UPPER_LE_SLOT_BUDGET_AND_BOTH_POWER_GATES',
    'dgp':{**V1_SPEC['dgp'], 'ZERO':'EXACT_ZERO', 'NEGATIVE_MEAN':{'normal_mean':-.05,'sd':1.},
        'VOL_BREAK':{'normal_mean':0.,'first_half_sd':.5,'second_half_sd':2.},
        'PREDICTABLE_EXPOSURE':{'position':'0.25+0.75*I(previous_private_innovation>0)',
                              'innovation':'INDEPENDENT_NORMAL_0_1','initial_position':.25},
        'COST_DRAG':{'position':'0.25+0.75*I(previous_private_innovation>0)',
                     'innovation':'INDEPENDENT_NORMAL_0_1','initial_position':.25,
                     'cost_per_absolute_position_change':.02,'daily_holding_cost':.005}},
    'mixed_alternative_daily_mean':2/math.sqrt(252),
    'power_iid_annual_sharpe':[.5,1.,2.,4.], 'power_target_member':0,
    'power_alternative':'ONLY_MEMBER_0_SHIFTED_OTHER_FOUR_NULL',
    'power_gates':[{'slot':1,'sr':2.,'minimum_cp_lower':.10},
                   {'slot':1,'sr':4.,'minimum_cp_lower':.70}],
    'randomness':'SEED_SEQUENCE_MASTER_CONDITION_INDEX_REPLICATE_0_NO_REALIZATION_DEMEANING',
    'source_hash_encoding':'UTF8_BYTES_WITH_CRLF_AND_CR_NORMALIZED_TO_LF',
    'confidence':'SUPPORT_AND_POWER_EACH_SIMULTANEOUS_95_PERCENT_JOINT_AT_LEAST_90_PERCENT',
    'scope':'FINITE_FIXED_DGP_CONDITIONAL_ONLY_NOT_REAL_ENGINE_OR_MARKET_VALIDITY',
}
METHOD_HASH = stable_hash(METHOD_SPEC)


def _beta_fraction(a,b,x):
    # Lentz continued fraction for regularized incomplete beta.
    tiny, eps = 1e-300, 3e-14
    c, d = 1., 1.-(a+b)*x/(a+1)
    d = 1/max(abs(d),tiny) * (1 if d>=0 else -1)
    h = d
    for m in range(1,201):
        for numerator in (m*(b-m)*x/((a+2*m-1)*(a+2*m)),
                          -(a+m)*(a+b+m)*x/((a+2*m)*(a+2*m+1))):
            d=1+numerator*d
            if abs(d)<tiny:d=tiny
            c=1+numerator/c
            if abs(c)<tiny:c=tiny
            d=1/d; change=d*c; h*=change
        if abs(change-1)<eps:return h
    raise ValueError('T7_BETA_CONVERGENCE_FAILED')


def t7_upper_tail(value):
    value=float(value)
    if math.isnan(value):raise ValueError('T7_NONFINITE_STATISTIC')
    if value==0:return .5
    if math.isinf(value):return 0. if value>0 else 1.
    x=7/(7+value*value); a,b=3.5,.5
    if x==0:tail=0.
    else:
        factor=math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x)) if x<1 else 0.
        beta=(factor*_beta_fraction(a,b,x)/a if x<(a+1)/(a+b+2)
              else 1-factor*_beta_fraction(b,a,1-x)/b)
        tail=.5*min(1.,max(0.,beta))
    return tail if value>0 else 1-tail


def _group_test(groups, *, all_zero=False):
    groups=np.asarray(groups,dtype=float)
    if groups.shape!=(8,) or not np.isfinite(groups).all():return None,None
    if all_zero:return 1.,None
    sd=float(groups.std(ddof=1))
    if sd==0 or np.ptp(groups)==0:return None,None
    statistic=math.sqrt(8)*float(groups.mean())/sd
    if not math.isfinite(statistic):return None,None
    return t7_upper_tail(statistic),statistic


def family_test(excess, *, alpha):
    if not isinstance(excess,dict) or not 1<=len(excess)<=5:raise ValueError('FORMAL_FROZEN_FAMILY_REQUIRED')
    if any(not isinstance(k,str) or not k for k in excess):raise ValueError('FORMAL_MEMBER_ID_INVALID')
    if isinstance(alpha,bool) or not isinstance(alpha,(int,float)) or not 0<alpha<=.05:raise ValueError('FORMAL_ALPHA_INVALID')
    raw, groups, statistics, invalid = {},{},{},{}
    for key in sorted(excess):
        values=np.asarray(excess[key],dtype=float)
        if values.shape!=(504,):raise ValueError('FORMAL_COMPLETE_504_SESSION_WINDOW_REQUIRED')
        if not np.isfinite(values).all():
            raw[key]=None;groups[key]=None;statistics[key]=None;invalid[key]='NONFINITE_MEMBER_UNTESTABLE';continue
        means=values.reshape(8,63).mean(axis=1)
        raw[key],statistics[key]=_group_test(means,all_zero=bool(np.all(values==0)))
        groups[key]=means.tolist()
        if raw[key] is None:invalid[key]='ZERO_GROUP_VARIANCE_UNTESTABLE'
    corrected=adjust_family(raw,tuple(raw))['adjusted_p']
    return {'schema_version':'FORMAL_FAMILY_TEST_V2','method_hash':METHOD_HASH,'raw_p':raw,
            'adjusted_p':corrected,'supported':{k:p is not None and p<=alpha for k,p in corrected.items()},
            'group_means':groups,'statistics':statistics,'untestable':invalid,'family_size':len(raw),
            'alpha':alpha,'qualification':'NOT_ASSESSED','interpretation':METHOD_SPEC['interpretation']}


def generate_dgp(condition,replicate):
    conditions=(*SUPPORT,*STRESS)
    if condition not in conditions or type(replicate) is not int or not 0<=replicate<CALIBRATION_SPEC['replicates']:
        raise ValueError('CALIBRATION_DGP_ID_INVALID')
    data_seed=[CALIBRATION_SPEC['seed'],conditions.index(condition),replicate,0]
    rng=np.random.default_rng(np.random.SeedSequence(data_seed))
    n,burn,m=504,1024,5
    x=rng.normal(size=(n+burn,m))
    # 原过程参数保持V1，仅使用V2独立种子；不复用V1的全局seed。
    if condition=='SKEW':x=(rng.chisquare(3,size=x.shape)-3)/math.sqrt(6)
    elif condition.startswith('AR'):
        rho=CALIBRATION_SPEC['dgp'][condition]
        for i in range(1,len(x)):x[i]=rho*x[i-1]+math.sqrt(1-rho*rho)*x[i]
    elif condition=='MA10':
        sums=np.vstack([np.zeros((1,m)),np.cumsum(x,axis=0)])
        x=(sums[10:]-sums[:-10])/math.sqrt(10)
    elif condition=='COMMON_SHOCK':x=math.sqrt(.75)*rng.normal(size=(len(x),1))+.5*x
    elif condition in ('T5','T3'):
        df=5 if condition=='T5' else 3
        x=rng.standard_t(df,size=x.shape)*math.sqrt((df-2)/df)
    elif condition=='GARCH':
        variance=np.ones(m);previous=x[0].copy()
        for i in range(1,len(x)):
            variance=.05+.05*previous*previous+.90*variance
            x[i]*=np.sqrt(variance);previous=x[i].copy()
    elif condition in ('PREDICTABLE_EXPOSURE','COST_DRAG'):
        positions=np.vstack([np.full((1,m),.25),.25+.75*(x[:-1]>0)])
        x=positions*x
        if condition=='COST_DRAG':
            changes=np.abs(positions-np.vstack([np.full((1,m),.25),positions[:-1]]))
            x-=.02*changes+.005*positions
    x=x[-n:]
    if condition=='ZERO':x[:]=0.
    elif condition=='NEGATIVE_MEAN':x-=.05
    elif condition=='VOL_BREAK':x*=np.where(np.arange(n)<252,.5,2.)[:,None]
    elif condition=='MEAN_BREAK':x+=np.where(np.arange(n)<252,.5,-.5)[:,None]
    elif condition=='TREND':x+=np.linspace(-.5,.5,n)[:,None]
    return x,data_seed


def _variants(condition):
    delta=CALIBRATION_SPEC['mixed_alternative_daily_mean']
    shifts={'ALL_NULL':np.zeros(5),'ONE_TRUE_NULL':np.array([0,delta,delta,delta,delta]),
            'THREE_TRUE_NULLS':np.array([0,0,0,delta,delta])}
    if condition=='IID':
        shifts.update({f'POWER_SR_{sr:g}':np.array([sr/math.sqrt(252),0,0,0,0])
                       for sr in CALIBRATION_SPEC['power_iid_annual_sharpe']})
    return shifts


def _variant_results(condition,groups,all_zero):
    raw,statistics={},{}
    for name,shift in _variants(condition).items():
        values=[_group_test(groups[:,i]+shift[i],all_zero=bool(all_zero[i] and shift[i]==0)) for i in range(5)]
        raw[name]=[p for p,t in values];statistics[name]=[t for p,t in values]
    return raw,statistics


def calibration_record(condition,replicate):
    x,seed=generate_dgp(condition,replicate)
    groups=x.reshape(8,63,5).mean(axis=1)
    zero=np.all(x==0,axis=0).tolist()
    raw,statistics=_variant_results(condition,groups,zero)
    return {'condition':condition,'replicate':replicate,'data_seed':seed,
            'data_hash':hashlib.sha256(np.asarray(x,dtype='<f8').tobytes()).hexdigest(),
            'group_means':groups.tolist(),'all_zero':zero,'statistics':statistics,'raw_p':raw}


def summarize_records(records, *, verify_data=False):
    expected={(c,r) for c in (*SUPPORT,*STRESS) for r in range(CALIBRATION_SPEC['replicates'])}
    seen,counts,power=set(),{},{}
    for record in records:
        key=(record['condition'],record['replicate'])
        if key not in expected or key in seen:raise ValueError('CALIBRATION_RECORD_MEMBERSHIP_CONFLICT')
        seen.add(key);condition,replicate=key
        seed=[CALIBRATION_SPEC['seed'],(*SUPPORT,*STRESS).index(condition),replicate,0]
        if record['data_seed']!=seed:raise ValueError('CALIBRATION_SEED_CONFLICT')
        digest=record['data_hash']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('CALIBRATION_DATA_HASH_INVALID')
        groups=np.asarray(record['group_means'],dtype=float)
        zero=record['all_zero']
        if groups.shape!=(8,5) or not np.isfinite(groups).all() or len(zero)!=5 or any(type(v) is not bool for v in zero):raise ValueError('CALIBRATION_GROUPS_INVALID')
        if any(zero[i] and np.any(groups[:,i]!=0) for i in range(5)):raise ValueError('CALIBRATION_ZERO_CONFLICT')
        for raw in record['raw_p'].values():
            if len(raw)!=5 or any(p is not None and (isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1) for p in raw):raise ValueError('CALIBRATION_PVALUE_INVALID')
        raw,statistics=_variant_results(condition,groups,zero)
        if record['raw_p']!=raw or record['statistics']!=statistics:raise ValueError('CALIBRATION_STATISTIC_CONFLICT')
        if verify_data and record!=calibration_record(condition,replicate):raise ValueError('CALIBRATION_REPLAY_CONFLICT')
        for variant,values in raw.items():
            adjusted=adjust_family(dict(enumerate(values)),tuple(range(5)))['adjusted_p']
            for slot,budget in enumerate(METHOD_SPEC['alpha_budgets'],1):
                rejected=[adjusted[i] is not None and adjusted[i]<=budget*.5 for i in range(5)]
                if variant.startswith('POWER_'):
                    index=(variant,slot);power[index]=power.get(index,0)+int(rejected[0])
                else:
                    true_count={'ALL_NULL':5,'ONE_TRUE_NULL':1,'THREE_TRUE_NULLS':3}[variant]
                    index=(condition,variant,slot);counts[index]=counts.get(index,0)+int(any(rejected[:true_count]))
    if seen!=expected:raise ValueError('CALIBRATION_INCOMPLETE_RECORDS')
    n=CALIBRATION_SPEC['replicates'];rows=[]
    for (condition,family,slot),count in sorted(counts.items()):
        upper=cp_upper(count,n,.05/126);budget=METHOD_SPEC['alpha_budgets'][slot-1]
        rows.append({'condition':condition,'family':family,'slot':slot,'replicates':n,
            'false_family_rejections':count,'fwer':count/n,'cp_upper':upper,'alpha_budget':budget,
            'test_alpha':budget*.5,'support_domain':condition in SUPPORT,'passed':upper<=budget if condition in SUPPORT else None})
    power_rows=[]
    for (variant,slot),count in sorted(power.items()):
        sr=float(variant.removeprefix('POWER_SR_'));threshold=next((g['minimum_cp_lower'] for g in CALIBRATION_SPEC['power_gates'] if g['slot']==slot and g['sr']==sr),None)
        lower=1-cp_upper(n-count,n,.05/2)
        power_rows.append({'scenario':variant,'slot':slot,'replicates':n,'target_member':0,
            'target_rejections':count,'target_power':count/n,'cp_lower':lower,
            'minimum_cp_lower':threshold,'passed':lower>=threshold if threshold is not None else None})
    return {'support_passed':all(r['passed'] for r in rows if r['support_domain']),
            'power_passed':all(r['passed'] for r in power_rows if r['minimum_cp_lower'] is not None),
            'conditions':rows,'power':power_rows,'confidence':CALIBRATION_SPEC['confidence'],
            'scope':CALIBRATION_SPEC['scope'],'real_strategy_qualification':False}


def source_hashes():
    root=Path(__file__).resolve().parents[3]
    paths=('src/chanlun_trader/research_factory/formal_statistics_v2.py','scripts/calibrate_formal_statistics_v2.py',
        'tests/research_factory/test_formal_statistics_v2.py','src/chanlun_trader/research_factory/formal_statistics_v1.py',
        'src/chanlun_trader/research_factory/holm_family_v1.py','src/chanlun_trader/research_factory/common.py',
        'scripts/calibrate_formal_statistics_v1.py','tests/research_factory/test_formal_statistics_v1.py',
        'src/chanlun_trader/research_factory/bounded_research_v1.py','src/chanlun_trader/research_factory/mutation_boundary.py',
        'src/chanlun_trader/research_factory/exploration_governance.py')
    return {p:hashlib.sha256((root/p).read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n')).hexdigest() for p in paths}


def load_calibration(path, *, expected_hash=None):
    """默认完整重放；expected_hash必须由调用方独立审查完整报告后固定，不能来自请求。"""
    path=Path(path)
    if path.resolve()!=path.absolute():raise ValueError('CALIBRATION_REDIRECTED_PATH')
    report=_read(path);prereg=_read(path.parent/'PREREGISTRATION.json')
    if (prereg['spec']!=CALIBRATION_SPEC or prereg['sources']!=source_hashes() or prereg['method_hash']!=METHOD_HASH
        or report['preregistration_hash']!=stable_hash(prereg) or report['method_hash']!=METHOD_HASH
        or report['records_hash']!=stable_hash(report['records'])):raise ValueError('CALIBRATION_BINDING_CONFLICT')
    candidate={**report,'method_approved':report['summary']['support_passed'] and report['summary']['power_passed'],
               'interpretation':METHOD_SPEC['interpretation']}
    if expected_hash is not None:
        if not isinstance(expected_hash,str) or stable_hash(candidate)!=expected_hash:
            raise ValueError('CALIBRATION_REVIEWED_HASH_CONFLICT')
        return candidate
    summary=summarize_records(report['records'],verify_data=True)
    if summary!=report['summary']:raise ValueError('CALIBRATION_SUMMARY_CONFLICT')
    return candidate
