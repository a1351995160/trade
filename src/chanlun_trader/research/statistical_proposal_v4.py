"""V4合成研究原型：重新估计每次重抽样的均值标准误，不接正式裁决。"""
import numpy as np

from chanlun_trader.research.statistical_proposal_v3 import stationary_indices


def bartlett_mean_se(values, lag=20):
    """最后两轴为时间/成员；移动和恒等式计算Bartlett HAC，保留两端部分窗。"""
    x = np.asarray(values, dtype=float)
    if x.ndim < 2 or not 0 <= lag < x.shape[-2] or not np.isfinite(x).all():
        raise ValueError("INVALID_HAC_INPUT")
    n = x.shape[-2]
    centered = x - x.mean(axis=-2, keepdims=True)
    padding = [(0, 0)] * x.ndim
    padding[-2] = (lag + 1, lag)
    cumulative = np.cumsum(np.pad(centered, padding), axis=-2)
    sums = cumulative[..., lag+1:, :] - cumulative[..., :-lag-1, :]
    variance = np.sum(sums*sums, axis=-2) / (n*n*(lag+1))
    if np.any(variance <= 0):
        raise ValueError("DEGENERATE_HAC_NO_INFERENCE")
    return np.sqrt(variance)


def studentized_mean_test(values, *, draws=10000, seed=20260911):
    x = np.asarray(values, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if x.ndim != 2 or len(x) < 200 or x.shape[1] == 0 or not np.isfinite(x).all():
        raise ValueError("INVALID_STUDENTIZED_INPUT")
    observed = x.mean(axis=0) / bartlett_mean_se(x)
    centered = x - x.mean(axis=0)
    index = stationary_indices(len(x), draws, 20, seed)
    exceedances = np.zeros(x.shape[1], dtype=int)
    # 固定分块仅限制临时数组内存；同一完整索引保证不随计算分块改变抽样。
    for start in range(0, draws, 500):
        sampled = centered[index[start:start+500]]
        statistic = sampled.mean(axis=1) / bartlett_mean_se(sampled)
        exceedances += (statistic >= observed).sum(axis=0)
    return {"status": "PROPOSED_NOT_APPROVED", "p_values": ((1+exceedances)/(draws+1)).tolist(),
            "draws": draws, "seed": seed, "block_length": 20, "hac_lag": 20,
            "samples": len(x), "studentized_each_resample": True}
