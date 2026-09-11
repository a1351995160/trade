"""事前固定的量纲检验；不读取信号、标签或账户表现。"""
import numpy as np

RULES = {'version': 'DERIVED_VOLUME_SEMANTICS_EVIDENCE_V1',
    'hypotheses': {'SHARES': 1, 'LOTS_100_SHARES': 100}, 'eps': 0.001,
    'selection': 'ALL_READABLE_TRAIN_BARS_FROM_EXISTING_5036_FILE_LIST', 'N': None,
    'minimum_total_valid': 100000, 'minimum_market_valid': 10000, 'minimum_sample_valid': 100,
    'median_interval': [0.8, 1.25], 'winner_broad_min': 0.99, 'winner_narrow_min': 0.95,
    'loser_broad_max': 0.01, 'winner_ohlc_eps_min': 0.90,
    'groups_must_agree': ['ALL', 'SH', 'SZ', '600000.SH', '600004.SH', '000001.SZ', '000002.SZ'],
    'start': 20220801, 'end': 20240731, 'no_third_hypothesis': True,
    'not_for_qualification': True}


def summarize(amount, volume, low, high, scale):
    vwap = amount / (volume * scale)
    ratio = vwap / ((high + low) / 2)
    log = np.log10(ratio)
    percentiles = [1, 5, 50, 95, 99]
    return {'n': len(ratio), 'median_ratio': float(np.median(ratio)),
        'ratio_quantiles': dict(zip(map(str, percentiles), map(float, np.percentile(ratio, percentiles)))),
        'log10_ratio_quantiles': dict(zip(map(str, percentiles), map(float, np.percentile(log, percentiles)))),
        'log10_histogram_edges': [-4, -3, -2.5, -2, -1.5, -1, -0.3, 0, 0.3, 1, 2, 3, 4],
        'log10_histogram_counts': np.histogram(log, [-np.inf,-4,-3,-2.5,-2,-1.5,-1,-0.3,0,0.3,1,2,3,4,np.inf])[0].tolist(),
        'within_0_5_2': float(np.mean((ratio >= .5) & (ratio <= 2))),
        'within_0_8_1_25': float(np.mean((ratio >= .8) & (ratio <= 1.25))),
        'ohlc_exact': float(np.mean((vwap >= low) & (vwap <= high))),
        'ohlc_eps': float(np.mean((vwap >= low*(1-RULES['eps'])) & (vwap <= high*(1+RULES['eps']))))}


def decide(groups):
    winners = []
    for name in RULES['hypotheses']:
        other = next(n for n in RULES['hypotheses'] if n != name)
        accepted = True
        for key in RULES['groups_must_agree']:
            w, loser = groups[key][name], groups[key][other]
            minimum = RULES['minimum_total_valid'] if key == 'ALL' else RULES['minimum_market_valid'] if key in {'SH','SZ'} else RULES['minimum_sample_valid']
            accepted &= (w['n'] >= minimum and .8 <= w['median_ratio'] <= 1.25
                and w['within_0_5_2'] >= RULES['winner_broad_min']
                and w['within_0_8_1_25'] >= RULES['winner_narrow_min']
                and w['ohlc_eps'] >= RULES['winner_ohlc_eps_min']
                and loser['within_0_5_2'] <= RULES['loser_broad_max']
                and np.isclose(groups[key]['SHARES']['median_ratio'] / groups[key]['LOTS_100_SHARES']['median_ratio'], 100))
        if accepted:
            winners.append(name)
    return winners[0] if len(winners) == 1 else 'UNKNOWN'
