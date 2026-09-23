import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from chanlun_trader.research.qfq import qfq_pit_acceptance, qfq_columns_asof
from price_only_scope.task_scope import real_gbbq_access_forbidden

# 分类说明（PR16-02）：
# - 本模块是**真实本机数据集成测试**：需要真实 gbbq 与真实 .day，属
#   REAL_DATA_INTEGRATION 类别，不是「合成可证的正确性」。
# - 合成可证的性质（as_of 前缀、未来事件不改历史、无事件时恒等）已拆到
#   tests/pit/test_qfq_pit_synthetic_v1.py 并**实际执行**。
# - 本任务（price-only 验收）禁止打开真实 gbbq，故本模块在**任务激活时**
#   显式标记为不执行（skip）。离开本任务环境（标记不存在）语义不变。
# - 不使用环境变量放开真实数据；任务激活条件来自工作区级标记。


def _task_forbids_real_gbbq() -> bool:
    return real_gbbq_access_forbidden()


pytestmark = [
    pytest.mark.real_data_integration,
    pytest.mark.skipif(
        _task_forbids_real_gbbq(),
        reason="TASK_FORBIDS_REAL_GBBQ_ACCESS: 本任务禁止真实 gbbq；"
               "合成可证性质见 tests/pit/test_qfq_pit_synthetic_v1.py"),
]


def test_qfq_pit_acceptance_future_mutation():
    """未来 gbbq 事件不能改变 as_of=t1 的历史 qfq 价格。"""
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    gbbq = tdx._load_gbbq()
    code_counts = gbbq.groupby("code")["datetime"].nunique().sort_values(ascending=False)
    stocks = list_a_stocks(cfg["tdx"]["vipdoc"])
    cm = {s["code"]: s["market"] for s in stocks}
    tested = 0
    for code in code_counts.index[:20]:
        mkt = cm.get(code)
        if mkt is None:
            continue
        raw = tdx.get_day(code, mkt)
        if raw.empty:
            continue
        dates = raw["date"].tolist()
        if len(dates) < 50:
            continue
        t1 = dates[len(dates) // 2]
        t2 = dates[-1]
        res = qfq_pit_acceptance(tdx, code, mkt, t1, t2)
        assert res.ok, f"{code}: {res.reason}"
        tested += 1
    assert tested >= 5


def test_full_sample_qfq_is_future_sensitive():
    """证明全样本 get_qfq_day 对未来事件敏感（因此 QFQ_PIT_SAFE=FALSE）。"""
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    gbbq = tdx._load_gbbq()
    code_counts = gbbq.groupby("code")["datetime"].nunique().sort_values(ascending=False)
    stocks = list_a_stocks(cfg["tdx"]["vipdoc"])
    cm = {s["code"]: s["market"] for s in stocks}
    found = False
    for code in code_counts.index[:20]:
        mkt = cm.get(code)
        if mkt is None:
            continue
        raw = tdx.get_day(code, mkt)
        if raw.empty:
            continue
        dates = raw["date"].tolist()
        if len(dates) < 50:
            continue
        t1 = dates[len(dates) // 2]
        t2 = dates[-1]
        g = gbbq[gbbq["code"] == code].copy()
        a = qfq_columns_asof(raw, g[g["datetime"] <= t1], t1)  # PIT as-of t1
        full = tdx.get_qfq_day(code, mkt)                      # 全样本 qfq（含未来事件）
        b = full[full["date"] <= t1].reset_index(drop=True)
        a = a[a["date"] <= t1].reset_index(drop=True)
        import numpy as np
        if len(a) and len(b) and not np.allclose(a["qfq_close"].to_numpy(), b["qfq_close"].to_numpy(), rtol=1e-12, atol=1e-9):
            found = True
            break
    assert found
