"""V4事前绑定的纯合成校准；不接受真实绩效输入。"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np

import chanlun_trader.research.statistical_proposal_v3 as v3
import chanlun_trader.research.statistical_proposal_v4 as v4
from scripts.calibrate_statistical_proposal_v3 import wilson_upper


def calibrate(output):
    output.mkdir(parents=True, exist_ok=False)
    plan = dict(version='V4_STUDENTIZED_PROPOSED', replicates=200, sessions=252, members=3,
                draws=10000, block_length=20, hac_lag=20, seed=20260911,
                cases=['IID_NULL', 'AR06_COMMON_NULL', 'OVERLAP10_COMMON_NULL', 'T5_COMMON_NULL', 'KNOWN_POSITIVE'],
                alpha=0.05, diagnostic_null_upper_limit=0.10, stop_seconds=1800,
                real_data_input=False, real_performance_trials=0,
                distinction='New method proposal, not a free correction or real approval')
    source_paths = [Path(__file__), Path(v3.__file__), Path(v4.__file__),
                    Path(__file__).with_name('calibrate_statistical_proposal_v3.py')]
    plan['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    (output/'CALIBRATION_PREREGISTRATION.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    started = time.monotonic()
    results = []
    for case_i, case in enumerate(plan['cases']):
        raw_reject = family_reject = 0
        # 每次模拟保留逐成员p值；均为合成数据，不与真实历史混同。
        with (output/(case+'-replicates.jsonl')).open('x', encoding='utf-8') as ledger:
            for rep in range(plan['replicates']):
                if time.monotonic()-started > plan['stop_seconds']:
                    raise TimeoutError('SYNTHETIC_CALIBRATION_TIME_LIMIT')
                rng = np.random.default_rng(plan['seed']+case_i*10000+rep)
                n = plan['sessions']
                common = rng.standard_t(5, size=n+300) if case == 'T5_COMMON_NULL' else rng.normal(size=n+300)
                if case == 'AR06_COMMON_NULL':
                    for t in range(1, len(common)):
                        common[t] += 0.6*common[t-1]
                if case == 'OVERLAP10_COMMON_NULL':
                    common = np.convolve(common, np.ones(10)/np.sqrt(10), mode='valid')
                x = 0.8*common[-n:, None]+0.6*rng.normal(size=(n, 3))
                if case == 'KNOWN_POSITIVE':
                    x += 0.4
                result = v4.studentized_mean_test(x, draws=plan['draws'], seed=1000000+case_i*10000+rep)
                p = np.asarray(result['p_values'])
                raw_reject += int(p[0] < 0.05)
                family_reject += int((v3.by_adjust(p, 3) <= 0.05).any())
                ledger.write(json.dumps({'replicate': rep, 'p_values': p.tolist()})+'\n')
                ledger.flush()
        item = dict(case=case, replicates=plan['replicates'], raw_rejections=raw_reject,
                    family_rejections=family_reject, raw_upper=wilson_upper(raw_reject, 200),
                    family_upper=wilson_upper(family_reject, 200))
        item['status'] = ('POWER_ONLY' if case == 'KNOWN_POSITIVE' else
                          'WITHIN_DIAGNOSTIC_BAND' if max(item['raw_upper'], item['family_upper']) <= 0.10 else
                          'CALIBRATION_LIMITATION_NOT_APPROVED')
        results.append(item)
        (output/(case+'.json')).write_text(json.dumps(item, indent=2), encoding='utf-8')
        print(json.dumps(item), flush=True)
    report = dict(status='PROPOSED_NOT_APPROVED', real_performance_trials=0, results=results,
                  elapsed_seconds=time.monotonic()-started)
    (output/'SYNTHETIC_CALIBRATION.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    calibrate(Path('E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2/statistical-calibration-v4'))
