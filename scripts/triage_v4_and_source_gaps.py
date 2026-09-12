"""固定来源缺件核对和已有V4失败的单一定位实验，不启动真实研究。"""
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
from chanlun_trader.research.statistical_proposal_v3 import stationary_indices
from chanlun_trader.research.statistical_proposal_v4 import bartlett_mean_se
from scripts.calibrate_statistical_proposal_v3 import wilson_upper

BASE = Path('E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2')
OUT = BASE/'exact-evidence-v1'


def save(name, value):
    (OUT/name).write_text(json.dumps(value, indent=2), encoding='utf-8')


def source_gaps():
    evidence = json.loads((BASE/'train-source-v1/DATA_PROVENANCE_AND_TIME_EVIDENCE.json').read_text())
    missing = evidence['missing_tdx_symbols']
    assert len(missing) == 146
    paths = []
    for symbol in missing:
        code, market = symbol.split('.')
        assert len(code) == 6 and code.isdigit() and market in {'SH', 'SZ'}
        paths.append(Path('E:/new_tdx_mock/vipdoc')/market.lower()/'lday'/(market.lower()+code+'.day'))
    calendar_path = Path('E:/llmwiki/chanlun-trading-system/data/research/security_state/raw/trade_calendar.json')
    gbbq = Path('E:/new_tdx_mock/T0002/hq_cache/gbbq')
    save('SOURCE_CHECK_PLAN.json', {'day_paths':list(map(str,paths)), 'day_access':'STAT_ONLY',
         'calendar':str(calendar_path), 'calendar_access':'PURE_DATE_METADATA',
         'gbbq':str(gbbq), 'gbbq_access':'STAT_ONLY_NO_BYTES',
         'reason':'Existing trusted GbbqReader.get_df reads entire encrypted file before filtering'})
    rows = []
    for symbol, path in zip(missing, paths):
        try:
            stat = path.stat()
            state = 'PRESENT_NOT_READ'
            size = stat.st_size
        except FileNotFoundError:
            state, size = 'MISSING', None
        except PermissionError:
            state, size = 'ACCESS_DENIED', None
        rows.append({'symbol':symbol, 'path':str(path), 'status':state, 'size':size,
                     'required_window':['2022-08-01','2024-07-31'], 'authorized_checked':True,
                     'missing_reason':'UNKNOWN_NOT_INFERRED_FROM_ABSENCE',
                     'owner_request':'Original daily records for actual eligible membership dates, acquisition identity, units and any evidenced listing/delisting explanation'})
    raw = calendar_path.read_bytes()
    calendar = json.loads(raw)
    def ensure_metadata(x):
        if isinstance(x,dict):
            if any(k.lower() in {'open','close','high','low','volume','amount','price','events','factor_values'} for k in x):
                raise ValueError('CALENDAR_NOT_PURE_METADATA')
            for v in x.values(): ensure_metadata(v)
        elif isinstance(x,list):
            for v in x: ensure_metadata(v)
    ensure_metadata(calendar)
    dates = sorted(set(int(str(d).replace('-','')) for d in calendar['trade_dates']))
    warmup = [d for d in dates if d < 20220801][-6:]
    ca = {'path':str(gbbq), 'bytes_read':0, 'extraction':'WINDOWED_EXTRACTION_NOT_AVAILABLE',
          'parser':'pytdx.reader.GbbqReader.get_df', 'format':'encrypted records; date inside decoded record',
          'owner_request':'Windowed TRAIN event export plus separately established six warmup sessions; code, event type, effective date, announcement date/available_at only if observed, amounts and units, source version and coverage statement'}
    try:
        st = gbbq.stat()
        ca.update(exists=True, regular_file=gbbq.is_file(), bytes=st.st_size)
    except FileNotFoundError:
        ca.update(exists=False, status='MISSING')
    except PermissionError:
        ca.update(status='ACCESS_DENIED')
    save('SOURCE_GAPS_AND_OWNER_EXPORT_REQUESTS.json', {'members':rows,
         'calendar_sha256':hashlib.sha256(raw).hexdigest(), 'calendar_first':dates[0],
         'warmup_sessions':warmup, 'warmup_status':'RESOLVED' if len(warmup)==6 else 'MISSING_SOURCE',
         'warmup_owner_request':'Trusted exchange-calendar dates for six actual sessions immediately before 2022-08-01; existing calendar lacks these dates, no additional price read attempted',
         'corporate_action':ca, 'available_at':'UNKNOWN_NOT_RECOVERABLE_FROM_DAY_FORMAT',
         'volume_units':'UNKNOWN', 'price_division_by_100':'VERIFIED_DERIVATION_FROM_DEPLOYED_FORMAT',
         'real_price_rows_read':0})


def diagnosis():
    cal = BASE/'statistical-calibration-v4'
    plan = json.loads((cal/'CALIBRATION_PREREGISTRATION.json').read_text())
    ledger_path = cal/'IID_NULL-replicates.jsonl'
    ledger = [json.loads(x) for x in ledger_path.read_text().splitlines()]
    assert len(ledger) == 200 and [r['replicate'] for r in ledger] == list(range(200))
    hypothesis = {'hypothesis':'Optimized HAC or tail/count implementation caused IID rejection excess',
         'diagnostic_selection':'First existing IID raw rejection not rejected by exact known-Normal first-member oracle',
         'bounds':'Reconstruct 200 existing IID draws only; direct-formula bootstrap for exactly one selected existing replicate, same 10000 indices; no new seed or formal calibration',
         'stop':'Finish one formula/tail comparison; retain original V4 failure regardless of diagnostic',
         'ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
         'diagnostic_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    save('V4_DIAGNOSTIC_PREREGISTRATION.json', hypothesis)
    checked_hashes = {p:hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in plan['source_sha256'].items()}
    assert all(checked_hashes.values())
    oracle_rejections, selected, se_ratios = [], None, []
    for row in ledger:
        rep = row['replicate']
        rng = np.random.default_rng(20260911+rep)
        common = rng.normal(size=552)[-252:]
        x = .8*common[:,None]+.6*rng.normal(size=(252,3))
        oracle = .5*math.erfc(x[:,0].mean()*math.sqrt(252)/math.sqrt(2))
        oracle_rejections.append(oracle<.05)
        se_ratios.append(float(bartlett_mean_se(x)[0]*math.sqrt(252)))
        if selected is None and row['p_values'][0]<.05 and oracle>=.05:
            selected = (rep,x,row)
    rep,x,row = selected
    z = x-x.mean(axis=0)
    def direct_se(a):
        c = a-a.mean(axis=-2,keepdims=True)
        n = a.shape[-2]
        variance = (c*c).sum(axis=-2)/n
        for k in range(1,21):
            variance += 2*(1-k/21)*(c[...,k:,:]*c[...,:-k,:]).sum(axis=-2)/n
        return np.sqrt(variance/n)
    observed = x.mean(axis=0)/direct_se(x)
    indices = stationary_indices(252,10000,20,1000000+rep)
    counts = np.zeros(3,dtype=int)
    max_error, ties, nonpositive = 0.,0,0
    for start in range(0,10000,500):
        sampled = z[indices[start:start+500]]
        se = direct_se(sampled)
        max_error = max(max_error,float(np.max(np.abs(se-bartlett_mean_se(sampled)))))
        t = sampled.mean(axis=1)/se
        counts += (t>=observed).sum(axis=0)
        ties += int((t==observed).sum())
        nonpositive += int((se<=0).sum())
    p = ((1+counts)/10001).tolist()
    assert p == row['p_values']
    k = sum(r['p_values'][0]<.05 for r in ledger)
    z95 = NormalDist().inv_cdf(.95)
    rate=k/200
    upper=(rate+z95*z95/400+z95*math.sqrt(rate*(1-rate)/200+z95*z95/(4*200**2)))/(1+z95*z95/200)
    assert abs(upper-wilson_upper(k,200))<1e-12
    save('V4_IID_FAILURE_DIAGNOSIS.json', {'source_hashes':checked_hashes,'raw_definition':'first member p<0.05, 200 IID replicates only',
         'raw_count':k,'denominator':200,'wilson_one_sided_confidence':.95,'wilson_upper':upper,
         'oracle_known_normal_rejections':sum(oracle_rejections),'oracle_is_diagnostic_not_replacement':True,
         'mean_first_member_se_over_known_se':float(np.mean(se_ratios)),
         'selected_existing_replicate':rep,'direct_formula_p_matches_ledger':True,
         'max_se_absolute_difference':max_error,'ties':ties,'nonpositive_bootstrap_se':nonpositive,
         'centered_max_mean':float(np.max(np.abs(z.mean(axis=0)))),
         'implementation_error_proven':False,'root_cause':'ROOT_CAUSE_UNCONFIRMED',
         'evidence':'Count, Wilson, centering and selected actual failure branch agree with independent formulas; finite-sample SE estimation and Monte Carlo variation remain plausible, not identified uniquely',
         'method_revision_proposal':'Separate method-development calibration from prospectively fixed acceptance experiments; assess studentized bandwidth/sample-length adequacy before any new approval; not an engineering fix',
         'V4_status':'CALIBRATION_LIMITATION_NOT_APPROVED','new_real_trials':0})


if __name__ == '__main__':
    source_gaps()
    diagnosis()
    print('SOURCE_GAPS_AND_TARGETED_V4_DIAGNOSIS_SAVED')
