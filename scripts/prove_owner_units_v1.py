"""对已冻结四份窗口样本做唯一单位证明；不取新样本，不计算策略表现。"""
from pathlib import Path
import json
from chanlun_trader.data.tdx.owner_export_v1 import write_json,sha,compare_sources

ROOT=Path('E:/llmwiki/owner-execution-export-v1/run-v2')


def prove(samples):
    """金额由TQ共同文档的万元锚定；以成交额/股数落入当日RAW高低价验证股数单位。"""
    details=[];possible={1,100}
    for sample in samples:
        comparison=compare_sources(sample['tdx'],sample['tq'])
        if not comparison['price_semantics_compatible'] or comparison['scales']['amount']['unique_ratio']!=10000 or comparison['scales']['volume']['unique_ratio'] is None:
            return {'status':'UNKNOWN','reason':'FROZEN_CROSS_SOURCE_COMPARISON_NOT_PASS','rows':details}
        for r in sample['tdx']:
            amount,encoded=r['amount_encoded'],r['volume_encoded'];checks={}
            for scale in [1,100]:
                valid=amount==0 and encoded==0
                implied=None
                if encoded>0 and amount>0:
                    implied=amount/(encoded*scale)
                    valid=r['low']-0.005<=implied<=r['high']+0.005
                checks[str(scale)]={'compatible':bool(valid),'implied_mean_trade_price':implied}
                if not valid:possible.discard(scale)
            details.append({'date':r['date'],'symbol':sample['symbol'],'checks':checks})
    nonzero=any(row['checks']['1']['implied_mean_trade_price'] is not None for row in details)
    winner=next(iter(possible)) if len(possible)==1 and nonzero else None
    return {'status':'VERIFIED_DERIVED_UNIT_EVIDENCE' if winner else 'UNKNOWN',
        'volume_unit':{1:'SHARES',100:'LOTS_100_SHARES'}.get(winner,'UNKNOWN'),
        'amount_unit':'CNY','price_mode':'RAW','all_rows_retained':True,'rows':details,
        'anchor':'TQ Amount万元（两份本机接口说明一致）；TDX编码/TQ=10000逐日唯一；成交金额/股数须在同日RAW高低价内'}


if __name__=='__main__':
    if Path.cwd().resolve()!=ROOT.parent.resolve():raise PermissionError('OWNER_WORKSPACE_REQUIRED')
    freeze=json.loads((ROOT/'FROZEN_EXPORT_RULES.json').read_text(encoding='utf-8'))
    rule={'version':'ABSOLUTE_UNIT_PROOF_V1','samples':freeze['samples'],'dates':freeze['sample_dates'],
        'source_rule_sha256':sha(ROOT/'FROZEN_EXPORT_RULES.json'),'volume_scale_candidates':[1,100],
        'amount_anchor':'Both local TQ skills specify Amount in TEN_THOUSAND_CNY; require fixed comparison ratio10000 for every sample',
        'absolute_test':'For every row: low-0.005 <= amount_encoded/(volume_encoded*scale) <= high+0.005; double-zero is non-informative, all other zero conflicts fail',
        'must_have_exactly_one_scale_across_all_rows':True,'no_sample_exclusions_or_reweighting':True}
    write_json(ROOT/'ABSOLUTE_UNIT_PROOF_RULES.json',rule)
    samples=[]
    for symbol in freeze['samples']:
        path=ROOT/(symbol+'.sample.json');samples.append({'symbol':symbol,**json.loads(path.read_text(encoding='utf-8'))})
    result=prove(samples);result['rule_sha256']=sha(ROOT/'ABSOLUTE_UNIT_PROOF_RULES.json')
    result['source_identity']='OWNER_FROZEN_TDX_TQ_UNIT_PROOF_V1';result['sample_hashes']={s:sha(ROOT/(s+'.sample.json')) for s in freeze['samples']}
    write_json(ROOT/'ABSOLUTE_UNIT_PROOF.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['rows','sample_hashes']},ensure_ascii=False))
