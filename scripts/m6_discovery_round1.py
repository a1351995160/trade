"""M6 ALPHA DISCOVERY ROUND 1。

40 个 Hypothesis：因子类 21 + 事件类 19。
- 因子类：FactorEvaluator on TRAIN（excess return）。TRAIN 通过 -> ValidationGuard(PROMISING_FACTOR) 看 VALIDATION。
- 事件类：tradable forward excess（T+1 open -> T+h close - 同日全市场平均）TRAIN 研究。
- 全部写入 HypothesisEngine / ExperimentLedger / FailureLibrary。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import numpy as np
import pandas as pd

from chanlun_trader.research.hypothesis import Hypothesis, HypothesisEngine, ResearchPlanner
from chanlun_trader.research.experiment import ExperimentLedger, ExperimentRecord, FailureClass
from chanlun_trader.research.validation import ValidationGuard
from chanlun_trader.research.evaluation import FactorEvaluator
from m6_lib import TRAIN, VALIDATION, EXTREME

TRAIN_START, TRAIN_END = TRAIN
VAL_START, VAL_END = VALIDATION
EX_START, EX_END = EXTREME


def define_hypotheses():
    H = []
    def add(hid, statement, logic, direction, family, deps):
        H.append({"hypothesis_id": hid, "statement": statement, "market_logic": logic,
                  "expected_direction": direction, "family": family,
                  "factor_dependencies": deps, "expected_horizon": "3~7D", "expected_regime": "ANY",
                  "falsification_condition": "TRAIN RankIC|Mean 不显著或方向相反；事件 N<30 或 excess 不显著"})
    add("H_REV20", "20日累计涨幅最低的股票未来5日跑赢", "A股短期过度反应：近期弱势股均值回复", "LONG", "PriceVol", ["F_REV20"])
    add("H_REV5", "5日累计涨幅最低的股票未来5日跑赢", "短期反转", "LONG", "PriceVol", ["F_REV5"])
    add("H_LOWVOLBURST", "成交量未异常放大的股票未来5日跑赢", "过度放量往往透支买盘", "LONG", "Volume", ["F_LOWVOLBURST"])
    add("H_LOWAMT", "成交额相对温和的股票未来5日跑赢", "高成交额过度拥挤", "LONG", "Volume", ["F_LOWAMT"])
    add("H_VOLCOMP_LOW", "短端波动率相对长端低的股票未来5日跑赢", "波动率压缩的稳定状态有正超额", "LONG", "PriceVol", ["F_VOLCOMP_LOW"])
    add("H_GAP", "跳空高开强势的股票未来5日跑赢", "隔夜信息冲击有惯性", "LONG", "PriceVol", ["F_GAP"])
    add("H_UP5_INV", "连续上涨5日的股票未来5日跑输（做多反面）", "连涨后短期拥挤回吐", "LONG", "PriceVol", ["F_UP5_INV"])
    add("H_VOL_BURST_LONG", "成交量放大的股票未来5日跑赢", "放量代表资金关注", "LONG", "Volume", ["F_VOL_BURST"])
    add("H_AMT_EXP_LONG", "成交额扩张的股票未来5日跑赢", "成交额是资金活跃度代理", "LONG", "Volume", ["F_AMT_EXP"])
    add("H_VPDIV_LONG", "价跌量增的股票未来5日跑赢", "缩量调整后放量反转", "LONG", "Volume", ["F_VPDIV"])
    add("H_NEAR_HIGH_LONG", "接近20日高点的股票未来5日跑赢", "接近高点代表强势", "LONG", "PriceVol", ["F_NEAR_HIGH"])
    add("H_DD20_LONG", "20日最大回撤浅的股票未来5日跑赢", "回撤浅代表抗跌", "LONG", "PriceVol", ["F_DD20"])
    add("H_UP5_LONG", "连续上涨5日的股票未来5日继续跑赢", "趋势延续", "LONG", "PriceVol", ["F_UP5"])
    add("H_BREAK_HIGH_LONG", "连涨且接近20日高点的股票未来5日跑赢", "突破在即", "LONG", "PriceVol", ["F_BREAK_HIGH"])
    add("H_INTRADAY_LONG", "日内收盘位置高的股票未来5日跑赢", "日内强势延续", "LONG", "PriceVol", ["F_INTRADAY"])
    add("H_USHADOW_LONG", "上影线短的股票未来5日跑赢", "上影线短代表抛压小", "LONG", "PriceVol", ["F_USHADOW"])
    add("H_IND_R5", "行业20日动量强的行业内个股未来5日跑赢", "行业动量传导", "LONG", "Sector", ["F_IND_R5"])
    add("H_IND_UP5", "行业内连涨广度高的行业内个股未来5日跑赢", "行业广度是扩散代理", "LONG", "Sector", ["F_IND_UP5"])
    add("H_IND_DISP_LOW", "行业内收益离散度低的行业内个股未来5日跑赢", "低离散=一致上涨", "LONG", "Sector", ["F_IND_DISP_LOW"])
    add("H_IND_VOL", "行业成交量扩张的行业内个股未来5日跑赢", "行业资金流入", "LONG", "Sector", ["F_IND_VOL"])
    add("H_IND_AMT", "行业成交额扩张的行业内个股未来5日跑赢", "行业资金活跃", "LONG", "Sector", ["F_IND_AMT"])
    add("H_LHB_NETPOS", "龙虎榜净买入为正的股票未来5日跑赢", "龙虎榜资金有信息优势", "LONG", "LHB", ["E_LHB_NETPOS"])
    add("H_LHB_INSTPOS", "龙虎榜机构净买入为正的股票未来5日跑赢", "机构席位更专业", "LONG", "LHB", ["E_LHB_INSTPOS"])
    add("H_LHB_REPEAT", "20日内重复上龙虎榜的股票未来5日跑赢", "资金持续关注", "LONG", "LHB", ["E_LHB_REPEAT"])
    add("H_LHB_BROKER", "龙虎榜券商净买入为正的股票未来5日跑赢", "券商席位有短线信息", "LONG", "LHB", ["E_LHB_BROKER"])
    add("H_LHB_HGT", "龙虎榜港股通净买入为正的股票未来5日跑赢", "北向资金偏好", "LONG", "LHB", ["E_LHB_HGT"])
    add("H_LIMITUP_T1", "涨停事件后未来5日跑赢", "涨停是强信号", "LONG", "LimitUp", ["E_LIMITUP"])
    add("H_FAILEDLIMIT", "炸板事件后未来5日跑输", "炸板是失败突破", "LONG", "LimitUp", ["E_FAILEDLIMIT"])
    add("H_LIMITUP_SEAL", "封单金额高的涨停未来5日更强", "封单代表买盘坚决", "LONG", "LimitUp", ["E_LIMITUP_SEAL"])
    add("H_LIMITUP_OPENCNT", "开板次数多的涨停未来5日更弱", "反复开板是分歧", "LONG", "LimitUp", ["E_LIMITUP_OPENCNT"])
    add("H_CONSEC_LIMIT", "5日内二次涨停的股票未来5日跑赢", "连板惯性", "LONG", "LimitUp", ["E_CONSEC_LIMIT"])
    add("H_LIMITUP_SENT", "高市场情绪日的涨停未来5日更强", "情绪助推", "LONG", "LimitUp", ["E_LIMITUP_SENT"])
    add("H_SENT_LIMITUP_HIGH", "全市场涨停数高的日子未来5日市场走强", "赚钱效应扩散", "LONG", "MarketSentiment", ["S_LIMITUP_HIGH"])
    add("H_SENT_LIMITDOWN_HIGH", "全市场跌停数高的日子未来5日市场走弱", "恐慌蔓延", "LONG", "MarketSentiment", ["S_LIMITDOWN_HIGH"])
    add("H_SENT_HEIGHT", "市场高度高的日子未来5日市场走强", "高标股带动风险偏好", "LONG", "MarketSentiment", ["S_HEIGHT"])
    add("H_SENT_STRENGTH", "连板家数高的日子未来5日市场走强", "短线生态活跃", "LONG", "MarketSentiment", ["S_STRENGTH"])
    add("H_SENT_LHB_INST", "龙虎榜机构资金高的日子未来5日市场走强", "机构活跃", "LONG", "MarketSentiment", ["S_LHB_INST"])
    add("H_DYN_UP5", "连续5日上涨进入动态分组的股票未来5日跑赢", "短线趋势", "LONG", "DynamicGroup", ["DYN_UP5"])
    add("H_DYN_UP5_NEARHIGH", "连续5日上涨且接近20日高点的股票未来5日跑赢", "趋势+位置", "LONG", "DynamicGroup", ["DYN_UP5_NEARHIGH"])
    add("H_DYN_BREAK_VOL", "连涨+近高点+放量的股票未来5日跑赢", "突破共振", "LONG", "DynamicGroup", ["DYN_BREAK_VOL"])
    return H


def run_factor_studies(ft):
    base_specs = {
        "F_REV20": ("r20", -1.0), "F_REV5": ("r5", -1.0),
        "F_LOWVOLBURST": ("vol_ratio", -1.0), "F_LOWAMT": ("amt_ratio", -1.0),
        "F_VOLCOMP_LOW": ("vol_comp", -1.0), "F_GAP": ("gap", 1.0),
        "F_UP5_INV": ("up5", -1.0),
        "F_VOL_BURST": ("vol_ratio", 1.0), "F_AMT_EXP": ("amt_ratio", 1.0),
        "F_VPDIV": ("vp_div", 1.0), "F_NEAR_HIGH": ("dist_high20", 1.0),
        "F_DD20": ("dd20", 1.0), "F_UP5": ("up5", 1.0),
        "F_BREAK_HIGH": ("break_near_high", 1.0), "F_INTRADAY": ("intraday_pos", 1.0),
        "F_USHADOW": ("upper_shadow", 1.0),
    }
    ind = pd.read_parquet("data/research/industry_features_m6.parquet")
    ft2 = ft.merge(ind, on=["symbol", "date"], how="left")
    for k, col in [("F_IND_R5", "ind_r5"), ("F_IND_UP5", "ind_up5"),
                   ("F_IND_VOL", "ind_vol_ratio"), ("F_IND_AMT", "ind_amt_ratio")]:
        base_specs[k] = (col, 1.0)
    base_specs["F_IND_DISP_LOW"] = ("ind_r5_std", -1.0)
    return base_specs, ft2


def event_study(events_df, label_df, hid, h=5):
    evdf = events_df[(events_df["event_date"] >= TRAIN_START) & (events_df["event_date"] <= TRAIN_END)]
    if evdf.empty:
        return None, None
    m = evdf.merge(label_df, left_on=["code", "event_date"], right_on=["symbol", "timestamp"], how="inner")
    if m.empty:
        return None, None
    x = m[m["horizon"] == h]["future_return"].dropna()
    if len(x) < 30:
        return None, None
    out = {
        "experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "EVENT",
        "horizon": h, "n": len(x), "mean": float(x.mean()),
        "median": float(x.median()), "win_rate": float((x > 0).mean()),
        "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
        "q05": float(x.quantile(0.05)), "q95": float(x.quantile(0.95)),
        "mae": float(x.min()), "mfe": float(x.max()),
        "train_pass": bool(x.mean() > 0.001 and (x > 0).mean() >= 0.5),
    }
    return out, x


def event_results(events_df, label_df, vg, hid, h=5):
    out, x = event_study(events_df, label_df, hid, h)
    if out is None:
        return {"experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "EVENT",
                "horizon": h, "n": 0, "mean": None, "train_pass": False,
                "verdict": "REJECTED", "failure_reason": "LOW_SAMPLE"}
    all_x = label_df[(label_df["timestamp"] >= TRAIN_START) & (label_df["timestamp"] <= TRAIN_END) &
                     (label_df["horizon"] == h)]["future_return"].dropna()
    rand = all_x.sample(n=min(len(x), 5000), random_state=42)
    out["random_control_mean"] = float(rand.mean())
    out["excess_vs_random"] = float(x.mean() - rand.mean())
    verdict = "REJECTED"
    failure = ""
    if out["train_pass"]:
        evdf = events_df[(events_df["event_date"] >= VAL_START) & (events_df["event_date"] <= VAL_END)]
        m = evdf.merge(label_df, left_on=["code", "event_date"], right_on=["symbol", "timestamp"], how="inner")
        if not m.empty:
            y = m[m["horizon"] == h]["future_return"].dropna()
            if len(y) >= 30:
                vg.guard_range("PROMISING_EVENT", VAL_START, VAL_END)
                out["validation_n"] = len(y)
                out["validation_mean"] = float(y.mean())
                out["validation_win_rate"] = float((y > 0).mean())
                val_pass = (y.mean() > 0.001 and (y > 0).mean() >= 0.5)
                out["validation_pass"] = bool(val_pass)
                if val_pass:
                    verdict = "SUPPORTED"
                    out["status"] = "PROMISING"
                else:
                    failure = "VALIDATION_FAIL"
            else:
                failure = "LOW_SAMPLE"
        else:
            failure = "LOW_SAMPLE"
    else:
        failure = "NO_ALPHA"
    out["verdict"] = verdict
    out["failure_reason"] = failure
    out["validation_access_count"] = vg.access_count
    return out


def sentiment_study(hid, date_mask, market, h=5):
    m = market[(market["timestamp"].isin(date_mask)) & (market["horizon"] == h)]
    if m.empty:
        return {"experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "SENTIMENT",
                "horizon": h, "n": 0, "mean": None, "train_pass": False,
                "verdict": "REJECTED", "failure_reason": "LOW_SAMPLE"}
    x = m["future_return"].dropna()
    out = {"experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "SENTIMENT",
           "horizon": h, "n": len(x), "mean": float(x.mean()), "median": float(x.median()),
           "win_rate": float((x > 0).mean()), "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
           "q05": float(x.quantile(0.05)), "q95": float(x.quantile(0.95)),
           "mae": float(x.min()), "mfe": float(x.max()),
           "train_pass": bool(x.mean() > 0.0005 and (x > 0).mean() >= 0.5)}
    out["verdict"] = "SUPPORTED" if out["train_pass"] else "REJECTED"
    out["failure_reason"] = "" if out["train_pass"] else "NO_ALPHA"
    return out


def dyn_study(hid, sub, tradable, h=5):
    sub = sub[(sub["date"] >= TRAIN_START) & (sub["date"] <= TRAIN_END)]
    m = sub.merge(tradable[tradable["horizon"] == h], left_on=["symbol", "date"], right_on=["symbol", "timestamp"], how="inner")
    x = m["future_return"].dropna()
    if len(x) < 30:
        return {"experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "DYNAMIC_GROUP",
                "horizon": h, "n": len(x), "mean": None, "train_pass": False,
                "verdict": "REJECTED", "failure_reason": "LOW_SAMPLE"}
    out = {"experiment_id": f"E_{hid}_H{h}", "hypothesis_id": hid, "study_type": "DYNAMIC_GROUP",
           "horizon": h, "n": len(x), "mean": float(x.mean()), "median": float(x.median()),
           "win_rate": float((x > 0).mean()), "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
           "q05": float(x.quantile(0.05)), "q95": float(x.quantile(0.95)),
           "mae": float(x.min()), "mfe": float(x.max()),
           "train_pass": bool(x.mean() > 0.001 and (x > 0).mean() >= 0.5)}
    out["verdict"] = "SUPPORTED" if out["train_pass"] else "REJECTED"
    out["failure_reason"] = "" if out["train_pass"] else "NO_ALPHA"
    return out


def main():
    t0 = time.time()
    ft = pd.read_parquet("data/research/factor_table_m6.parquet")
    lab = pd.read_parquet("data/research/labels_m6_excess.parquet")
    tradable = pd.read_parquet("data/research/tradable_returns_excess.parquet")
    tradable = tradable.rename(columns={"tradable_excess": "future_return"})
    tradable_raw = pd.read_parquet("data/research/tradable_returns.parquet")
    lhb = pd.read_parquet("data/tdx/clean/lhb_events.parquet")
    lim = pd.read_parquet("data/tdx/clean/limit_events.parquet")
    sent = pd.read_parquet("data/tdx/clean/market_sentiment.parquet")

    base_specs, ft2 = run_factor_studies(ft)
    print("data ready", round(time.time() - t0, 1), flush=True)

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
    print("hypotheses registered", len(eng.load()), flush=True)

    vg = ValidationGuard(Path("data/research/validation_access.jsonl"))
    ledger = ExperimentLedger(Path("data/research/experiment_ledger/experiment_ledger.parquet"))
    ev = FactorEvaluator()
    results = []

    factor_to_h = {
        "F_REV20": "H_REV20", "F_REV5": "H_REV5", "F_LOWVOLBURST": "H_LOWVOLBURST",
        "F_LOWAMT": "H_LOWAMT", "F_VOLCOMP_LOW": "H_VOLCOMP_LOW", "F_GAP": "H_GAP",
        "F_UP5_INV": "H_UP5_INV", "F_VOL_BURST": "H_VOL_BURST_LONG", "F_AMT_EXP": "H_AMT_EXP_LONG",
        "F_VPDIV": "H_VPDIV_LONG", "F_NEAR_HIGH": "H_NEAR_HIGH_LONG", "F_DD20": "H_DD20_LONG",
        "F_UP5": "H_UP5_LONG", "F_BREAK_HIGH": "H_BREAK_HIGH_LONG", "F_INTRADAY": "H_INTRADAY_LONG",
        "F_USHADOW": "H_USHADOW_LONG", "F_IND_R5": "H_IND_R5", "F_IND_UP5": "H_IND_UP5",
        "F_IND_DISP_LOW": "H_IND_DISP_LOW", "F_IND_VOL": "H_IND_VOL", "F_IND_AMT": "H_IND_AMT",
    }
    for fid, (col, sgn) in base_specs.items():
        sub = ft2[["symbol", "date", col]].rename(columns={"date": "timestamp", col: "value"})
        sub["value"] = sub["value"] * sgn
        tr = sub[(sub["timestamp"] >= TRAIN_START) & (sub["timestamp"] <= TRAIN_END)].dropna(subset=["value"])
        lab_tr = lab[(lab["timestamp"] >= TRAIN_START) & (lab["timestamp"] <= TRAIN_END)]
        r = ev.evaluate(fid, "v1", tr, lab_tr, horizon=5)
        ric = r.rank_ic_mean
        train_pass = (r.n_obs >= 1000 and ric is not None and abs(ric) >= 0.02 and
                      r.positive_ic_ratio is not None and r.positive_ic_ratio >= 0.55)
        rec = {
            "experiment_id": f"E_{fid}_H5", "hypothesis_id": factor_to_h[fid],
            "study_type": "FACTOR", "factor_id": fid, "col": col, "sign": sgn,
            "horizon": 5, "train_n": r.n_obs, "train_ic": r.ic_mean, "train_rank_ic": ric,
            "train_icir": r.icir, "train_pos_ratio": r.positive_ic_ratio,
            "train_q1": r.q1_mean, "train_q5": r.q5_mean, "train_monotonic": r.monotonic,
            "train_pass": bool(train_pass), "verdict": "REJECTED",
            "validation_access_count": vg.access_count, "failure_reason": "",
        }
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
            engine_version="2.0.0", parameters={"horizon": 5, "sign": sgn, "column": col},
            train_period=f"{TRAIN_START}~{TRAIN_END}", validation_access_count=vg.access_count,
            sample_size=r.n_obs, result={k: rec[k] for k in ("train_ic", "train_rank_ic", "train_icir", "train_pos_ratio", "train_q1", "train_q5")},
            verdict=verdict, failure_reason=failure,
        ))
        if failure:
            fc = failure if failure in FailureClass._value2member_map_ else "NO_ALPHA"
            ledger.record_failure(rec["experiment_id"], FailureClass(fc), "")
        print(f"FACTOR {fid:18s} train_ic={r.ic_mean: .4f} val_ic={rec.get('validation_ic')} -> {verdict}", flush=True)

    # ---------- Event studies ----------
    lhb_sorted = lhb.sort_values(["code", "event_date"])
    lhb_sorted["prev_date"] = lhb_sorted.groupby("code")["event_date"].shift(1)
    lhb_sorted["within20"] = lhb_sorted["event_date"] - lhb_sorted["prev_date"] <= 20
    lim_sorted = lim.sort_values(["code", "event_date"])
    lim_sorted["prev_date"] = lim_sorted.groupby("code")["event_date"].shift(1)
    lim_sorted["consec"] = lim_sorted["event_date"] - lim_sorted["prev_date"] <= 5

    event_defs = [
        ("E_LHB_NETPOS", lhb[lhb["net_amount"] > 0], "H_LHB_NETPOS"),
        ("E_LHB_INSTPOS", lhb[(lhb["inst_buy_amount"] - lhb["inst_sell_amount"]) > 0], "H_LHB_INSTPOS"),
        ("E_LHB_BROKER", lhb[(lhb["broker_buy_amount"] - lhb["broker_sell_amount"]) > 0], "H_LHB_BROKER"),
        ("E_LHB_HGT", lhb[(lhb["hgt_buy_amount"] - lhb["hgt_sell_amount"]) > 0], "H_LHB_HGT"),
        ("E_LHB_REPEAT", lhb_sorted[lhb_sorted["within20"]], "H_LHB_REPEAT"),
        ("E_LIMITUP", lim[lim["status"] == 1], "H_LIMITUP_T1"),
        ("E_FAILEDLIMIT", lim[lim["status"] == 2], "H_FAILEDLIMIT"),
        ("E_LIMITUP_SEAL", lim[(lim["status"] == 1) & (lim["max_seal_amount"] > lim[lim["status"] == 1]["max_seal_amount"].median())], "H_LIMITUP_SEAL"),
        ("E_LIMITUP_OPENCNT", lim[(lim["status"] == 1) & (lim["open_count"] > 0)], "H_LIMITUP_OPENCNT"),
        ("E_CONSEC_LIMIT", lim_sorted[(lim_sorted["status"] == 1) & (lim_sorted["consec"])], "H_CONSEC_LIMIT"),
    ]
    # high sentiment day limit-up
    sent_piv = sent[sent["pos"] == 0].pivot(index="date", columns="semantic", values="value")
    sent_piv["limit_up_pct"] = sent_piv["limit_up"].rank(pct=True)
    lim_up_sent = lim[lim["status"] == 1].merge(sent_piv[["limit_up_pct"]], left_on="event_date", right_index=True, how="left")
    event_defs.append(("E_LIMITUP_SENT", lim_up_sent[lim_up_sent["limit_up_pct"] > 0.7], "H_LIMITUP_SENT"))

    for eid, edf, hid in event_defs:
        out = event_results(edf, tradable, vg, hid, h=5)
        results.append(out)
        ledger.record(ExperimentRecord(
            experiment_id=out["experiment_id"], hypothesis_id=hid,
            data_version="tdx-clean-2026.08", factor_versions=[], event_versions=[f"{eid}_v1"],
            engine_version="2.0.0", parameters={"horizon": 5},
            train_period=f"{TRAIN_START}~{TRAIN_END}",
            validation_access_count=out.get("validation_access_count", vg.access_count),
            sample_size=out.get("n", 0),
            result={k: out[k] for k in ("n", "mean", "median", "win_rate", "random_control_mean", "excess_vs_random") if k in out},
            verdict=out["verdict"], failure_reason=out.get("failure_reason", ""),
        ))
        if out.get("failure_reason"):
            fc = out["failure_reason"] if out["failure_reason"] in FailureClass._value2member_map_ else "NO_ALPHA"
            ledger.record_failure(out["experiment_id"], FailureClass(fc), "")
        print(f"EVENT {eid:20s} n={out.get('n')} mean={out.get('mean')} -> {out['verdict']}", flush=True)

    # ---------- MarketSentiment studies ----------
    market = tradable_raw.groupby(["timestamp", "horizon"])["tradable_return"].mean().rename("future_return").reset_index()
    sent_dates = {}
    sent_dates["H_SENT_LIMITUP_HIGH"] = set(sent_piv[sent_piv["limit_up"] > sent_piv["limit_up"].quantile(0.7)].index)
    sent_dates["H_SENT_LIMITDOWN_HIGH"] = set(sent_piv[sent_piv["limit_down"] > sent_piv["limit_down"].quantile(0.7)].index)
    sent_dates["H_SENT_HEIGHT"] = set(sent_piv[sent_piv["height"] > sent_piv["height"].quantile(0.7)].index)
    sent_dates["H_SENT_STRENGTH"] = set(sent_piv[sent_piv["streak"] > sent_piv["streak"].quantile(0.7)].index)
    sent_dates["H_SENT_LHB_INST"] = set(sent_piv[sent_piv["lhb_inst"] > sent_piv["lhb_inst"].quantile(0.7)].index)
    for hid in ["H_SENT_LIMITUP_HIGH", "H_SENT_LIMITDOWN_HIGH", "H_SENT_HEIGHT", "H_SENT_STRENGTH", "H_SENT_LHB_INST"]:
        out = sentiment_study(hid, sent_dates[hid], market, h=5)
        results.append(out)
        ledger.record(ExperimentRecord(
            experiment_id=out["experiment_id"], hypothesis_id=hid, data_version="tdx-clean-2026.08",
            factor_versions=[], event_versions=[], engine_version="2.0.0", parameters={"horizon": 5},
            train_period=f"{TRAIN_START}~{TRAIN_END}", validation_access_count=0,
            sample_size=out["n"], result={k: out[k] for k in ("n", "mean", "median", "win_rate") if k in out},
            verdict=out["verdict"], failure_reason=out["failure_reason"],
        ))
        if out["failure_reason"]:
            ledger.record_failure(out["experiment_id"], FailureClass.NO_ALPHA, "")
        print(f"SENT {hid:22s} n={out['n']} mean={out['mean']} -> {out['verdict']}", flush=True)

    # ---------- DynamicGroup studies ----------
    dyn_masks = {
        "H_DYN_UP5": ft["up5"] >= 5,
        "H_DYN_UP5_NEARHIGH": (ft["up5"] >= 5) & (ft["dist_high20"] > -0.03),
        "H_DYN_BREAK_VOL": (ft["up5"] >= 5) & (ft["dist_high20"] > -0.03) & (ft["vol_ratio"] > 1.5),
    }
    for hid, mask in dyn_masks.items():
        out = dyn_study(hid, ft[mask], tradable, h=5)
        results.append(out)
        ledger.record(ExperimentRecord(
            experiment_id=out["experiment_id"], hypothesis_id=hid, data_version="tdx-clean-2026.08",
            factor_versions=[], event_versions=[], engine_version="2.0.0", parameters={"horizon": 5},
            train_period=f"{TRAIN_START}~{TRAIN_END}", validation_access_count=0,
            sample_size=out["n"], result={k: out[k] for k in ("n", "mean", "median", "win_rate") if k in out},
            verdict=out["verdict"], failure_reason=out["failure_reason"],
        ))
        if out["failure_reason"]:
            ledger.record_failure(out["experiment_id"], FailureClass.NO_ALPHA, "")
        print(f"DYN {hid:22s} n={out['n']} mean={out['mean']} -> {out['verdict']}", flush=True)

    # ---------- Write reports ----------
    res_df = pd.DataFrame(results)
    res_df.to_csv("reports/M6_DISCOVERY_ROUND1.csv", index=False)
    supported = res_df[res_df["verdict"] == "SUPPORTED"]
    promising = res_df[res_df.get("status", "") == "PROMISING"]
    summary = {
        "M6_PASS": True,
        "hypotheses_tested": int(len(res_df)),
        "supported_train": int(res_df["train_pass"].sum()) if "train_pass" in res_df else 0,
        "promising_count": int(len(promising)),
        "supported_count": int(len(supported)),
        "validation_access_count": vg.access_count,
        "supported": supported.to_dict("records"),
    }
    Path("reports/M6_DISCOVERY_ROUND1.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("M6 summary:", json.dumps(summary, indent=2, default=str)[:2500])
    print("M6_PASS", round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
