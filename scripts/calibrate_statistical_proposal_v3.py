"""只生成合成时间序列，预登记后校准推荐方法，不接受真实数据路径。"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from chanlun_trader.research.statistical_proposal_v3 import by_adjust, centered_mean_test
import chanlun_trader.research.statistical_proposal_v3 as method_module


def wilson_upper(k, n):
    z=1.6448536269514722
    p=k/n
    return (p+z*z/(2*n)+z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)


def calibrate(output):
    output.mkdir(parents=True, exist_ok=False)
    plan={"schema_version":"synthetic-calibration-preregistration-v3", "replicates":200,
          "sessions":252, "members":3, "draws":10000, "block_length":20, "seed":20260911,
          "cases":["IID_NULL","AR06_COMMON_NULL","OVERLAP10_COMMON_NULL","T5_COMMON_NULL","KNOWN_POSITIVE"],
          "alpha":0.05, "diagnostic_null_upper_limit":0.10,
          "diagnostic_limit_is_not_real_acceptance_threshold":True,
          "real_data_input":False, "stop_seconds":1800,
          "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "method_module_sha256":hashlib.sha256(Path(method_module.__file__).read_bytes()).hexdigest()}
    (output/'CALIBRATION_PREREGISTRATION.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    started=time.monotonic()
    results=[]
    for case_i,case in enumerate(plan['cases']):
        raw_reject, family_reject=0,0
        for rep in range(plan['replicates']):
            if time.monotonic()-started > plan['stop_seconds']:
                raise TimeoutError('SYNTHETIC_CALIBRATION_TIME_LIMIT')
            rng=np.random.default_rng(plan['seed']+case_i*10000+rep)
            n=plan['sessions']
            if case=='T5_COMMON_NULL':
                common=rng.standard_t(5,size=n+300)
            else:
                common=rng.normal(size=n+300)
            if case=='AR06_COMMON_NULL':
                for t in range(1,len(common)):
                    common[t]+=0.6*common[t-1]
            if case=='OVERLAP10_COMMON_NULL':
                common=np.convolve(common,np.ones(10)/np.sqrt(10),mode='valid')
            common=common[-n:]
            x=0.8*common[:,None]+0.6*rng.normal(size=(n,3))
            if case=='KNOWN_POSITIVE':
                x+=0.4
            result=centered_mean_test(x,block_length=20,draws=plan['draws'],seed=1000000+case_i*10000+rep)
            p=np.asarray(result['p_values'])
            raw_reject+=int(p[0]<0.05)
            family_reject+=int((by_adjust(p,3)<=0.05).any())
        item={"case":case,"replicates":plan['replicates'],"raw_rejections_first_member":raw_reject,
              "family_any_rejections":family_reject,"raw_rate":raw_reject/plan['replicates'],
              "family_rate":family_reject/plan['replicates'],
              "raw_one_sided_95_upper":wilson_upper(raw_reject,plan['replicates']),
              "family_one_sided_95_upper":wilson_upper(family_reject,plan['replicates'])}
        item['diagnostic_status']='POWER_REPORT_ONLY' if case=='KNOWN_POSITIVE' else ('WITHIN_PREREGISTERED_DIAGNOSTIC_BAND' if max(item['raw_one_sided_95_upper'],item['family_one_sided_95_upper'])<=0.10 else 'CALIBRATION_LIMITATION_NOT_APPROVED')
        results.append(item)
        (output/(case+'.json')).write_text(json.dumps(item,indent=2),encoding='utf-8')
        print(json.dumps(item),flush=True)
    report={"schema_version":"statistical-proposal-synthetic-calibration-v3","status":"PROPOSED_NOT_APPROVED",
            "real_performance_trials":0,"calibration_results":results,"elapsed_seconds":time.monotonic()-started,
            "limitation":"Finite synthetic scenarios do not establish real-world stationarity, valid p-values, independent holdout or strategy qualification."}
    (output/'SYNTHETIC_CALIBRATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    calibrate(Path('E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2/statistical-calibration-v3-bound-source'))
