"""唯一获准代码变更适配；原件保留，单日衔接明确标为派生研究价格。"""
from decimal import Decimal

VERSION='WEEKLY_300114_302132_ASOF_ALIAS_V1'
OLD,NEW='sz.300114','sz.302132'
CHANGE='2025-02-17'
FACTOR=Decimal('5.975678')


def adapt(raw,hfq,old_raw,old_hfq):
    def index(rows):
        result={r['date']:r for r in rows}
        if len(result)!=len(rows):raise ValueError('ALIAS_DUPLICATE_DATE')
        return result
    r,h,orr,oh=map(index,[raw,hfq,old_raw,old_hfq])
    before=sorted(d for d in r if d<CHANGE)
    if len(before)!=86 or before[0]!='2024-10-09' or before[-1]!='2025-02-14':
        raise ValueError('ALIAS_FIXED_PREWARM_CHANGED')
    for day in before:
        if day not in orr or day not in oh or orr[day]['code']!=OLD or oh[day]['code']!=OLD:
            raise ValueError('ALIAS_OLD_SOURCE_MISSING')
        if r[day]['code']!=NEW or h[day]['code']!=NEW or oh[day]['adjustflag']!='1':
            raise ValueError('ALIAS_SOURCE_IDENTITY_CONFLICT')
        for key,value in r[day].items():
            if key=='code':continue
            other=orr[day].get(key)
            if value!=other:
                try:equal=Decimal(value)==Decimal(other)
                except (ArithmeticError,TypeError):equal=False
                if not equal:raise ValueError('ALIAS_RAW_NON_IDENTITY')
        if Decimal(oh[day]['close'])!=Decimal(orr[day]['close'])*FACTOR:
            raise ValueError('ALIAS_PRIOR_FACTOR_NOT_FROZEN_VALUE')
    first=r[CHANGE];last=r[before[-1]]
    if (Decimal(first['preclose'])!=Decimal(last['close']) or first['tradestatus']!='1'
        or Decimal(h[CHANGE]['close'])!=Decimal(first['close'])):
        raise ValueError('ALIAS_TRANSITION_NOT_OBSERVED_RESET')
    # 次日只是事后无收益交叉证据；当天的派生只使用变更前已存在的因子。
    following='2025-02-18'
    if (Decimal(r[following]['preclose'])!=Decimal(first['close'])
        or Decimal(h[following]['close'])!=Decimal(r[following]['close'])*FACTOR):
        raise ValueError('ALIAS_FOLLOWING_FACTOR_CONFLICT')
    raw_out=[];hfq_out=[]
    for day in sorted(r):
        raw_out.append({**r[day],'source_code':OLD if day<CHANGE else NEW,
            'canonical_identity':NEW,'identity_adapter':VERSION})
        if day<CHANGE:
            hfq_out.append({**oh[day],'code':NEW,'source_code':OLD,
                'price_evidence':'ORIGINAL_OLD_CODE_HFQ_WITH_CANONICAL_IDENTITY'})
        elif day==CHANGE:
            hfq_out.append({**h[day],'close':str(Decimal(first['close'])*FACTOR),
                'source_code':NEW,'provider_original_close':h[day]['close'],
                'price_evidence':'DERIVED_PRIOR_FACTOR_CARRY_CODE_CHANGE_NOT_VENDOR_ATTESTED'})
        else:hfq_out.append(dict(h[day]))
    evidence={'version':VERSION,'before_change_source':OLD,'from_change_source':NEW,
        'canonical_identity':NEW,'change_date':CHANGE,'verified_raw_rows':86,
        'factor':str(FACTOR),'factor_available_basis':'BEFORE_CHANGE_OLD_HFQ_OVER_RAW',
        'derived_day':CHANGE,'provider_close':h[CHANGE]['close'],
        'derived_close':str(Decimal(first['close'])*FACTOR),
        'evidence_level':'DERIVED_AND_IDENTITY_CORROBORATED_NOT_VENDOR_VERSION_ATTESTED',
        'not_for_qualification':True,'raw_values_changed':False,'hazards_must_be_retained':True}
    return raw_out,hfq_out,evidence


def canonical_pools(pools):
    result={}
    for day,members in pools.items():
        mapping=dict(members)
        if day<20250217:
            if OLD not in mapping:raise ValueError('ASOF_OLD_MEMBERSHIP_MISSING')
            mapping[NEW]=mapping[OLD]
        result[day]=mapping
    return result
