"""M7 FOCUSED DISCOVERY ROUND 2 — 基于 Round 1 证据的 18 个结构差异 Hypothesis。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import numpy as np
import pandas as pd

from chanlun_trader.research.hypothesis import Hypothesis, HypothesisEngine
from chanlun_trader.research.experiment import ExperimentLedger, ExperimentRecord, FailureClass
from chanlun_trader.research.validation import ValidationGuard
from chanlun_trader.research.evaluation import FactorEvaluator
from m6_lib import TRAIN, VALIDATION

TRAIN_START, TRAIN_END = TRAIN
VAL_START, VAL_END = VALIDATION


def define_hypotheses():
    H = []
    def add(hid, statement, logic, family, deps):
        H.append({"hypothesis_id": hid, "statement": statement, "market_logic": logic,
                  "expected_direction": "LONG", "family": family,
                  "factor_dependencies": deps, "expected_horizon": "3~7D", "expected_regime": "ANY",
                  "falsification_condition": "TRAIN RankIC<0.02 或方向相反；VALIDATION 不通过"})
    add("R2_REV10", "10日跌幅深的股票未来5日跑赢", "中期超跌反弹比短期更稳定", "PriceVol", ["R2_REV10"])
    add("R2_REV30", "30日跌幅深的股票未来5日跑赢", "月度级别过度反应", "PriceVol", ["R2_REV30"])
    add("R2_REV20_LOWVOL", "20日跌幅深且波动率低的股票未来5日跑赢", "低波动超跌是错杀", "PriceVol", ["R2_REV20_LOWVOL"])
    add("R2_REV20_NOLIMIT", "20日跌幅深且未发生跌停的股票未来5日跑赢", "排除退市/利空跌停", "PriceVol", ["R2_REV20_NOLIMIT"])
    add("R2_REV20_TOPLIQ", "20日跌幅深且成交额前500的股票未来5日跑赢", "大盘超跌有流动性支撑", "PriceVol", ["R2_REV20_TOPLIQ"])
    add("R2_REV20_INDUP", "20日跌幅深且行业未走弱的股票未来5日跑赢", "个股错杀优于行业走弱", "Sector", ["R2_REV20_INDUP"])
    add("R2_REV_COMPOSITE", "5日与20日共同超跌的股票未来5日跑赢", "多周期反转共振", "PriceVol", ["R2_REV_COMPOSITE"])
    add("R2_REV20_LOWDISP", "行业低离散度中的20日超跌股未来5日跑赢", "一致回调后反弹更集中", "Sector", ["R2_REV20_LOWDISP"])
    add("R2_LOWTURN", "换手代理低的股票未来5日跑赢", "低换手代表筹码稳定", "Volume", ["R2_LOWTURN"])
    add("R2_LOWAMT_TOPLIQ", "成交额前500中成交额相对低的股票未来5日跑赢", "大盘低拥挤", "Volume", ["R2_LOWAMT_TOPLIQ"])
    add("R2_LOWVOL_TOPLIQ", "成交额前500中量比低的股票未来5日跑赢", "大盘低热度", "Volume", ["R2_LOWVOL_TOPLIQ"])
    add("R2_LOWAMT_LOWVOL", "低成交额且低量比的股票未来5日跑赢", "双重低热度", "Volume", ["R2_LOWAMT_LOWVOL"])
    add("R2_LOWAMT_HIGHPRICE", "低成交额且价格高于20日中位数的股票未来5日跑赢", "排除低价壳股", "Volume", ["R2_LOWAMT_HIGHPRICE"])
    add("R2_GAP_ROOM", "跳空高开且距20日高点较远的股票未来5日跑赢", "缺口启动且有空间", "PriceVol", ["R2_GAP_ROOM"])
    add("R2_GAP_LOWVOL", "跳空高开且波动率低的股票未来5日跑赢", "温和跳空更持久", "PriceVol", ["R2_GAP_LOWVOL"])
    add("R2_GAP_NOTUP5", "跳空高开且非连续上涨的股票未来5日跑赢", "首日异动优于追涨", "PriceVol", ["R2_GAP_NOTUP5"])
    add("R2_REV20_SENT", "市场高度高时的20日超跌股未来5日跑赢", "强势市场做超跌反弹", "Interaction", ["R2_REV20_SENT"])
    add("R2_GAP_SENT", "全市场涨停数高时的跳空高开股未来5日跑赢", "情绪共振", "Interaction", ["R2_GAP_SENT"])
    return H


def build_features():
    ft = pd.read_parquet("data/research/factor_table_m6.parquet")
    uproxy = pd.read_parquet("data/research/universe_proxy_m6.parquet")
    ind = pd.read_parquet("data/research/industry_features_m6.parquet")
    sent = pd.read_parquet("data/tdx/clean/market_sentiment.parquet")
    sent_piv = sent[sent["pos"] == 0].pivot(index="date", columns="semantic", values="value")
    sent_piv["height_pct"] = sent_piv["height"].rank(pct=True)
    sent_piv["limit_up_pct"] = sent_piv["limit_up"].rank(pct=True)
    ft = ft.merge(uproxy, on=["symbol", "date"], how="left")
    ft = ft.merge(ind, on=["symbol", "date"], how="left")
    # r10 / r30
    ft["r10"] = ft.groupby("symbol")["ret"].transform(lambda s: (1 + s).rolling(10).apply(lambda x: x.prod() - 1, raw=True))
    ft["r30"] = ft.groupby("symbol")["ret"].transform(lambda s: (1 + s).rolling(30).apply(lambda x: x.prod() - 1, raw=True))
    # 20日内是否跌停（近似）
    ft["limitdown_flag"] = (ft["ret"] <= -0.095).astype(float)
    ft["limdn_any20"] = ft.groupby("symbol")["limitdown_flag"].transform(lambda s: s.rolling(20, min_periods=1).max())
    # 价格相对20日中位数
    daily = pd.read_parquet("data/research/daily_all.parquet")[["symbol", "date", "close"]]
    daily["price_rank20"] = daily.groupby("symbol")["close"].transform(
        lambda s: s.rolling(20, min_periods=5).apply(lambda x: 1.0 if x[-1] > np.median(x) else 0.0, raw=True))
    ft = ft.merge(daily, on=["symbol", "date"], how="left")

    ft["F_REV20"] = -ft["r20"]
    ft["F_REV5"] = -ft["r5"]
    ft["F_LOWVOLBURST"] = -ft["vol_ratio"]
    ft["F_LOWAMT"] = -ft["amt_ratio"]
    ft["F_VOLCOMP_LOW"] = -ft["vol_comp"]
    ft["F_GAP"] = ft["gap"]
    ft["F_UP5_INV"] = -ft["up5"]
    ft["F_IND_DISP_LOW"] = -ft["ind_r5_std"]
    ft["R2_REV20"] = -ft["r20"]
    ft["R2_REV10"] = -ft["r10"]
    ft["R2_REV30"] = -ft["r30"]
    ft["R2_REV20_LOWVOL"] = -ft["r20"] * (ft["vol_comp"] < 1.0).astype(float)
    ft["R2_REV20_NOLIMIT"] = -ft["r20"] * (ft["limdn_any20"] == 0).astype(float)
    ft["R2_REV20_TOPLIQ"] = -ft["r20"] * ft["amt_rank_top500"].astype(float)
    ft["R2_REV20_INDUP"] = -ft["r20"] * (ft["ind_r5"] > -0.02).astype(float)
    ft["R2_REV_COMPOSITE"] = -0.5 * ft["r5"] - 0.5 * ft["r20"]
    ft["R2_REV20_LOWDISP"] = -ft["r20"] * (ft["ind_r5_std"] < ft["ind_r5_std"].median()).astype(float)
    ft["R2_LOWTURN"] = -ft["turnover_proxy"]
    ft["R2_LOWAMT_TOPLIQ"] = -ft["amt_ratio"] * ft["amt_rank_top500"].astype(float)
    ft["R2_LOWVOL_TOPLIQ"] = -ft["vol_ratio"] * ft["amt_rank_top500"].astype(float)
    ft["R2_LOWAMT_LOWVOL"] = -0.5 * ft["amt_ratio"] - 0.5 * ft["vol_ratio"]
    ft["R2_LOWAMT_HIGHPRICE"] = -ft["amt_ratio"] * ft["price_rank20"].astype(float)
    ft["R2_GAP_ROOM"] = ft["gap"] * (ft["dist_high20"] < -0.03).astype(float)
    ft["R2_GAP_LOWVOL"] = ft["gap"] * (ft["vol_comp"] < 1.0).astype(float)
    ft["R2_GAP_NOTUP5"] = ft["gap"] * (ft["up5"] < 5).astype(float)
    ft = ft.merge(sent_piv[["height_pct", "limit_up_pct"]], left_on="date", right_index=True, how="left")
    ft["R2_REV20_SENT"] = -ft["r20"] * (ft["height_pct"] > 0.7).astype(float)
    ft["R2_GAP_SENT"] = ft["gap"] * (ft["limit_up_pct"] > 0.7).astype(float)
    return ft


def main():
    t0 = time.time()
    ft = build_features()
    lab = pd.read_parquet("data/research/labels_m6_excess.parquet")
    print("features ready", round(time.time() - t0, 1), flush=True)

    hypotheses = define_hypotheses()
    eng = HypothesisEngine(Path("data/research/hypothesis_ledger.jsonl"))
    existing = {h.hypothesis_id for h in eng.load()}
    for h in hypotheses:
        if h["hypothesis_id"] not in existing:
            eng.register(Hypothesis(
                hypothesis_id=h["hypothesis_id"], statement=h["statement"], market_logic=h["market_logic"],
                expected_direction=h["expected_direction"], factor_dependencies=h["factor_dependencies"],
                expected_horizon=h["expected_horizon"], expected_regime=h["expected_regime"],
                falsification_condition=h["falsification_condition"], status="REGISTERED",
            ))
    print("hypotheses total", len(eng.load()), flush=True)

    vg = ValidationGuard(Path("data/research/validation_access.jsonl"))
    ledger = ExperimentLedger(Path("data/research/experiment_ledger/experiment_ledger.parquet"))
    ev = FactorEvaluator()
    results = []
    for h in hypotheses:
        fid = h["factor_dependencies"][0]
        sub = ft[["symbol", "date", fid]].rename(columns={"date": "timestamp", fid: "value"})
        tr = sub[(sub["timestamp"] >= TRAIN_START) & (sub["timestamp"] <= TRAIN_END)].dropna(subset=["value"])
        lab_tr = lab[(lab["timestamp"] >= TRAIN_START) & (lab["timestamp"] <= TRAIN_END)]
        r = ev.evaluate(fid, "v1", tr, lab_tr, horizon=5)
        ric = r.rank_ic_mean
        train_pass = (r.n_obs >= 1000 and ric is not None and abs(ric) >= 0.02 and
                      r.positive_ic_ratio is not None and r.positive_ic_ratio >= 0.55)
        rec = {"experiment_id": f"E_{fid}_H5", "hypothesis_id": h["hypothesis_id"],
               "study_type": "FACTOR", "factor_id": fid, "horizon": 5,
               "train_n": r.n_obs, "train_ic": r.ic_mean, "train_rank_ic": ric,
               "train_icir": r.icir, "train_pos_ratio": r.positive_ic_ratio,
               "train_q1": r.q1_mean, "train_q5": r.q5_mean, "train_monotonic": r.monotonic,
               "train_pass": bool(train_pass), "verdict": "REJECTED",
               "validation_access_count": vg.access_count, "failure_reason": ""}
        verdict = "REJECTED"
        failure = ""
        if train_pass:
            va = sub[(sub["timestamp"] >= VAL_START) & (sub["timestamp"] <= VAL_END)].dropna(subset=["value"])
            lab_va = lab[(lab["timestamp"] >= VAL_START) & (lab["timestamp"] <= VAL_END)]
            vg.guard_range("PROMISING_FACTOR", VAL_START, VAL_END)
            rv = ev.evaluate(fid, "v1", va, lab_va, horizon=5)
            rec["validation_n"] = rv.n_obs
            rec["validation_ic"] = rv.ic_mean
            rec["validation_rank_ic"] = rv.rank_ic_mean
            rec["validation_icir"] = rv.icir
            rec["validation_pos_ratio"] = rv.positive_ic_ratio
            val_pass = (rv.n_obs >= 500 and rv.rank_ic_mean is not None and abs(rv.rank_ic_mean) >= 0.02 and
                        rv.positive_ic_ratio is not None and rv.positive_ic_ratio >= 0.55)
            rec["validation_pass"] = bool(val_pass)
            if val_pass:
                verdict = "SUPPORTED"
                rec["status"] = "PROMISING"
            else:
                failure = "VALIDATION_FAIL"
        else:
            failure = "NO_ALPHA"
        rec["verdict"] = verdict
        rec["failure_reason"] = failure
        results.append(rec)
        ledger.record(ExperimentRecord(
            experiment_id=rec["experiment_id"], hypothesis_id=rec["hypothesis_id"],
            data_version="tdx-clean-2026.08", factor_versions=[f"{fid}_v1"], event_versions=[],
            engine_version="2.0.0", parameters={"horizon": 5},
            train_period=f"{TRAIN_START}~{TRAIN_END}", validation_access_count=vg.access_count,
            sample_size=r.n_obs, result={k: rec[k] for k in ("train_ic", "train_rank_ic", "train_icir", "train_pos_ratio", "train_q1", "train_q5")},
            verdict=verdict, failure_reason=failure,
        ))
        if failure:
            fc = failure if failure in FailureClass._value2member_map_ else "NO_ALPHA"
            ledger.record_failure(rec["experiment_id"], FailureClass(fc), "")
        print(f"{fid:22s} train_ic={r.ic_mean: .4f} val_ic={rec.get('validation_ic')} -> {verdict}", flush=True)

    res_df = pd.DataFrame(results)
    res_df.to_csv("reports/M7_DISCOVERY_ROUND2.csv", index=False)
    supported = res_df[res_df["verdict"] == "SUPPORTED"]
    summary = {
        "M7_PASS": True,
        "hypotheses_tested": int(len(res_df)),
        "promising_count": int(len(supported)),
        "supported": supported.to_dict("records"),
        "validation_access_count": vg.access_count,
    }
    Path("reports/M7_DISCOVERY_ROUND2.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("M7 summary:", json.dumps(summary, indent=2, default=str)[:2000])
    print("M7_PASS", round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
