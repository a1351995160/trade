"""固定家族的Holm校正；不生成单项p值、不读取绩效或授予统计资格。"""
import math

CALIBRATION_SPEC={'version':'ETF_FAMILY_SINGLE_TEST_DIAGNOSTIC_V1','seed':20260914,
    'sessions':486,'family_size':4,'bootstrap_replicates':10000,'mean_block':20,
    'synthetic_replicates':2000,'alpha':.05,'wilson_z':1.959963984540054,
    'max_fwer_wilson_upper':.065,
    'conditions':['IID','AR05','COMMON_SHOCK','HETEROSKEDASTIC','ZERO'],
    'null':'ZERO_EXPECTED_DAILY_EXCESS_MEAN','statistic':'UNSTUDENTIZED_MEAN',
    'centering':'SUBTRACT_EACH_SAMPLE_MEAN','tail':'GREATER_OR_EQUAL_PLUS_ONE',
    'reuse':'ONE_FIXED_INDEPENDENT_INDEX_BANK_SHARED_BY_SYNTHETIC_REPLICATES_AND_COLUMNS',
    'interpretation':'CONDITIONAL_ON_FIXED_MONTE_CARLO_BANK_NOT_FORMAL_VALIDATION',
    'real_pvalues':'DISABLED_EXPOSED_TRAIN_NO_CONFIRMATION_WINDOW',
    'zero_rule':'ALL_ZERO_RETURNS_P1_NONZERO_CONSTANT_UNTESTABLE',
    'source':'https://bashtage.github.io/arch/_modules/arch/bootstrap/base.html'}


def stationary_weights(n,reps,block,seed):
    """循环索引、概率1/block重启；按次数聚合与逐条采样均值严格等价。"""
    import numpy as np
    if n<2 or reps<1 or block<1:raise ValueError('BOOTSTRAP_SHAPE_INVALID')
    rng=np.random.default_rng(seed);index=rng.integers(n,size=reps)
    counts=np.zeros((reps,n),dtype=float);rows=np.arange(reps)
    for t in range(n):
        if t:
            restart=rng.random(reps)<1/block
            index=np.where(restart,rng.integers(n,size=reps),(index+1)%n)
        counts[rows,index]+=1
    return counts/n


def mean_null_pvalues(samples,weights):
    import numpy as np
    samples=np.asarray(samples,dtype=float);weights=np.asarray(weights,dtype=float)
    if samples.ndim!=2 or weights.ndim!=2 or len(samples)!=weights.shape[1] or not np.isfinite(samples).all():
        raise ValueError('BOOTOTSTRAP_INPUT_INVALID')
    if not np.isfinite(weights).all() or (weights<0).any() or not np.allclose(weights.sum(axis=1),1):
        raise ValueError('BOOTSTRAP_WEIGHTS_INVALID')
    observed=samples.mean(axis=0)
    null=weights@(samples-observed)
    p=(1+(null>=observed).sum(axis=0))/(len(weights)+1)
    constant=np.ptp(samples,axis=0)==0
    p[constant & (observed==0)]=1.
    p[constant & (observed!=0)]=np.nan
    return p


def synthetic_calibration():
    """一次冻结校准；不读取行情，不按结果改变方法或种子。"""
    import numpy as np
    spec=CALIBRATION_SPEC;n=spec['sessions'];total=spec['synthetic_replicates'];k=4
    weights=stationary_weights(n,spec['bootstrap_replicates'],spec['mean_block'],spec['seed'])
    reports={}
    for ci,condition in enumerate(spec['conditions']):
        rng=np.random.default_rng(np.random.SeedSequence([spec['seed'],ci,1]));all_p=[]
        for first in range(0,total,100):
            count=min(100,total-first)
            # 200session burn-in；零均值且条件/跨候选相关事前固定。
            x=rng.normal(size=(n+200,count,k))
            if condition in ('COMMON_SHOCK','HETEROSKEDASTIC'):
                common=rng.normal(size=(n+200,count,1))
                x=np.sqrt(.75)*common+np.sqrt(.25)*x
            if condition=='AR05':
                for t in range(1,len(x)):x[t]=.5*x[t-1]+np.sqrt(.75)*x[t]
            x=x[200:]
            if condition=='HETEROSKEDASTIC':x*=np.linspace(.5,2,n)[:,None,None]
            if condition=='ZERO':x[:]=0
            p=mean_null_pvalues(x.reshape(n,count*k),weights).reshape(count,k)
            all_p.extend(p.tolist())
        p=np.array(all_p);rejections=(p.min(axis=1)<=spec['alpha']/k)
        count=int(rejections.sum());rate=count/total;z=spec['wilson_z']
        upper=(rate+z*z/(2*total)+z*np.sqrt(rate*(1-rate)/total+z*z/(4*total*total)))/(1+z*z/total)
        reports[condition]={'replicates':total,'family_rejections':count,'raw_fwer':rate,
            'wilson_upper':float(upper),'passed':bool(upper<=spec['max_fwer_wilson_upper']),
            'single_rejections':(p<=.05).sum(axis=0).tolist(),'pvalues':all_p}
    return {'spec':spec,'conditions':reports,'passed':all(v['passed'] for v in reports.values()),
            'formal_method_approved':False,'real_pvalues_calculated':False}


def adjust_family(pvalues, family):
    family=tuple(family)
    if not family or len(set(family))!=len(family) or set(pvalues)!=set(family):
        raise ValueError('FROZEN_FAMILY_MEMBERSHIP_MISMATCH')
    for value in pvalues.values():
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or
                                  not math.isfinite(value) or not 0<=value<=1):
            raise ValueError('INVALID_P_VALUE')
    # 无法检验的成员按1保留在乘数中，展示仍为None，不伪装观测到p=1。
    order=sorted(family,key=lambda key:(1 if pvalues[key] is None else pvalues[key],family.index(key)))
    adjusted={};previous=0.
    for rank,key in enumerate(order):
        p=1. if pvalues[key] is None else pvalues[key]
        previous=max(previous,min(1.,(len(family)-rank)*p))
        adjusted[key]=None if pvalues[key] is None else previous
    return {'family_size':len(family),'adjusted_p':{key:adjusted[key] for key in family},
            'method':'HOLM','qualification':'NOT_ASSESSED',
            'limitation':'VALID_SINGLE_TEST_PVALUES_AND_CONFIRMATION_DESIGN_REQUIRED'}
