"""A4: define 40~60 mechanism-driven hypotheses with lineage tracking (registration only; testing next)."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

OUT = Path("data/research/mechanism_hypotheses")
OUT.mkdir(parents=True, exist_ok=True)

def main():
    hyps = []
    def add(hid, statement, logic, mech, source, parent_factor=None, parent_seed=None, parent_event=None,
            expected_direction="TO_TEST", horizon="3~7D", falsify=None, data="DAILY"):
        hyps.append({
            "hypothesis_id": hid, "statement": statement, "market_logic": logic,
            "primary_mechanism_id": mech, "hypothesis_source": source,
            "parent_factor": parent_factor, "parent_seed": parent_seed, "parent_event": parent_event,
            "expected_direction": expected_direction, "expected_horizon": horizon,
            "falsification_condition": falsify or "TRAIN purged walk-forward RankIC not significantly > 0",
            "required_data": data, "status": "REGISTERED",
        })

    # --- source 1: existing promising factors (~35%, 16 hypotheses) ---
    add("A4-001","20日反转且最近5日跌幅收窄（卖压衰竭）","20日累计下跌后，最近5日跌幅显著小于前段，表明卖压在衰竭","M_REV_SELLING_EXHAUSTION",
        "EXISTING_FACTOR", parent_factor="F_REV20", horizon="3~7D", falsify="与单纯F_REV20相比RankIC无增量")
    add("A4-002","20日反转且隔夜收益为负（日内超跌）","20日下跌集中在隔夜跳空，日内已企稳","M_REV_T1_DELAYED_SUPPLY",
        "EXISTING_FACTOR", parent_factor="F_REV20", horizon="1~5D", falsify="与F_REV20相比RankIC无增量")
    add("A4-003","20日反转且量比<1（低热度确认）","20日下跌且缩量，表明抛压减弱","M_REV_SELLING_EXHAUSTION",
        "EXISTING_FACTOR", parent_factor="F_REV20", horizon="3~7D", falsify="量比条件不增加Top分位收益")
    add("A4-004","30日反转且成交额Top500（可交易反转）","30日深跌、流动性充足股票的反转更具容量","M_REV_T1_DELAYED_SUPPLY",
        "EXISTING_FACTOR", parent_factor="R2_REV30", horizon="3~7D", falsify="Top500池中RankIC显著低于全市场")
    add("A4-005","30日反转且价格在20日均线上（吸收确认）","深跌后价格重新站上短期均线，买方开始吸收","M_FLOW_ABSORPTION",
        "EXISTING_FACTOR", parent_factor="R2_REV30", horizon="3~10D", falsify="站上均线条件无增量")
    add("A4-006","20日反转且行业5日收益>-2%（板块不恶化）","行业层面未继续恶化时个股反转更可靠","M_SECTOR_MOMENTUM",
        "EXISTING_FACTOR", parent_factor="R2_REV20_INDUP", horizon="3~7D", falsify="行业条件无增量")
    add("A4-007","20日反转且行业离散度低（板块同步）","行业离散度低说明个股下跌非独立基本面恶化","M_DISPERSION_LOW",
        "EXISTING_FACTOR", parent_factor="F_IND_DISP_LOW", horizon="3~7D", falsify="离散度交互项无增量")
    add("A4-008","Top500中低成交额比（冷门流动性溢价）","大池中低成交额比代表冷门但可交易，存在流动性溢价","M_ATTENTION_LOW_HEAT",
        "EXISTING_FACTOR", parent_factor="R2_LOWAMT_TOPLIQ", horizon="3~7D", falsify="条件化分位收益与市场均值无差异")
    add("A4-009","Top500中低量比且价格20日中位以上（吸收中的冷门）","低热度且价格相对强，资金在低调吸收","M_FLOW_ABSORPTION",
        "EXISTING_FACTOR", parent_factor="R2_LOWAMT_HIGHPRICE", horizon="3~7D", falsify="价格位置条件无增量")
    add("A4-010","5日连涨反向且非涨停（普通追涨反转）","连涨但非涨停的股票更可能是情绪追涨","M_STREAK_FADE",
        "EXISTING_FACTOR", parent_factor="F_UP5_INV", horizon="1~5D", falsify="非涨停条件无增量")
    add("A4-011","20日反转 + 30日反转共振（多周期反转）","不同周期反转共振说明超跌一致","M_REV_T1_DELAYED_SUPPLY",
        "EXISTING_FACTOR", parent_factor="R2_REV_COMPOSITE", horizon="3~7D", falsify="共振因子RankIC不超过单因子")
    add("A4-012","20日反转 + 低波动压缩（波动收敛）","下跌后波动收敛代表抛压有序释放","M_VOLATILITY_RISK_PREMIUM",
        "EXISTING_FACTOR", parent_factor="F_REV20", horizon="3~7D", falsify="波动压缩交互无增量")
    add("A4-013","跳空高开且非连涨5日（冷静跳空）","非连涨背景下的跳空更可能是信息而非情绪","M_OVERNIGHT_GAP_REVERSAL",
        "EXISTING_FACTOR", parent_factor="R2_GAP_NOTUP5", horizon="1~5D", falsify="与R2_GAP_NOTUP5无差异")
    add("A4-014","20日反转且20日内无跌停（非崩溃股）","排除连续跌停的崩溃股，反转更健康","M_REV_T1_DELAYED_SUPPLY",
        "EXISTING_FACTOR", parent_factor="R2_REV20_NOLIMIT", horizon="3~7D", falsify="排除跌停无增量")
    add("A4-015","低成交额比且高价格位置（缩量强势）","缩量且价格相对强的股票被资金控制良好","M_FLOW_ABSORPTION",
        "EXISTING_FACTOR", parent_factor="R2_LOWAMT_HIGHPRICE", horizon="3~7D", falsify="Top分位收益不显著")
    add("A4-016","20日反转且Top500（可交易反转）","高流动性反转，容量大","M_TOP_LIQUIDITY_FILTER",
        "EXISTING_FACTOR", parent_factor="R2_REV20_TOPLIQ", horizon="3~7D", falsify="Top500条件无增量")
    print("internal hypotheses", len(hyps))

    # --- source 2: external mechanism seeds (~30%, 15 hypotheses) ---
    ext = pd.read_parquet("data/research/external_alpha_seed_library/external_alpha_seed_library.parquet")
    rec = ext[ext["recommended_round1"]].head(15)
    for i, (_, r) in enumerate(rec.iterrows(), 1):
        add(f"A4-{100+i:03d}", str(r["title"])[:120], str(r["idea_summary"])[:300], str(r["research_family"])[:40],
            "EXTERNAL_SEED", parent_seed=str(r["seed_id"]), horizon=str(r["expected_horizon"]),
            falsify="TRAIN purged walk-forward RankIC not > 0")

    # --- source 3: deep fusion seeds (~30%, 15 hypotheses) ---
    fus = pd.read_parquet("data/research/external_alpha_seed_library/deep_fusion_seeds.parquet")
    fus = fus[fus["recommended_top24"]].head(15)
    for i, (_, r) in enumerate(fus.iterrows(), 1):
        add(f"A4-{200+i:03d}", str(r["title"])[:120], str(r["market_logic"])[:300], str(r["research_family"])[:40],
            "DEEP_FUSION_SEED", parent_seed=str(r["seed_id"]), horizon=str(r["expected_horizon"]),
            falsify="TRAIN purged walk-forward RankIC not > 0; fusion ablation shows no incremental vs best single")

    # --- source 4: TDX/TQ new data mechanisms (~20%, 10 hypotheses) ---
    add("A4-301","涨停股次日溢价与封单质量","封单金额大、开板次数少的涨停次日延续性更强","LIMITUP_QUALITY",
        "TDX_TQ_DATA", parent_event="E_LIMITUP_SEAL", horizon="1~3D", falsify="封单质量分层无收益差")
    add("A4-302","炸板股后3日修复与行业涨停氛围","板块仍有多只涨停时炸板股修复更强","FAILED_LIMIT_RECOVERY",
        "TDX_TQ_DATA", parent_event="E_FAILEDLIMIT", horizon="1~5D", falsify="板块氛围条件无增量")
    add("A4-303","龙虎榜机构净买且非连板（信息型LHB）","非连板背景下机构净买更有信息含量","SMART_MONEY",
        "TDX_TQ_DATA", parent_event="E_LHB_INSTPOS", horizon="2~7D", falsify="与普通LHB净买入无差异")
    add("A4-304","龙虎榜游资净买且次日高开（注意型LHB）","游资席位高开引发关注度，随后反转","ATTENTION_OVERREACTION",
        "TDX_TQ_DATA", parent_event="E_LHB_BROKER", horizon="1~5D", falsify="高开条件无反转差异")
    add("A4-305","市场高度上升期20日反转增强","市场高度（连板空间）上升时资金活跃，超跌反弹更强","MARKET_SENTIMENT",
        "TDX_TQ_DATA", parent_factor="F_REV20", horizon="3~7D", falsify="情绪条件不增强反转RankIC")
    add("A4-306","涨停家数低位回升日全市场反弹","情绪冰点回升日买入超跌股","MARKET_SENTIMENT",
        "TDX_TQ_DATA", horizon="1~5D", falsify="情绪回升信号无市场级正收益")
    add("A4-307","首次涨停且行业首板（板块点火）","行业首板点火，后续跟风概率高","SECTOR_IGNITION",
        "TDX_TQ_DATA", parent_event="E_LIMITUP", horizon="1~3D", falsify="行业首板与孤立涨停无差异")
    add("A4-308","5日内二次涨停且不放量（筹码锁定）","二次涨停不放量说明筹码锁定好","CHIP_DISPOSITION",
        "TDX_TQ_DATA", parent_event="E_CONSEC_LIMIT", horizon="1~5D", falsify="放量条件无差异")
    add("A4-309","尾盘30分钟强势（14:30后）","尾盘资金抢筹预示次日强势","LATE_DAY_STRENGTH",
        "TDX_5M_DATA", horizon="1~2D", data="5M", falsify="尾盘强度无次日预测力")
    add("A4-310","早盘前30分钟放量突破日内区间","开盘放量突破区间，日内趋势延续","OPENING_STRENGTH",
        "TDX_5M_DATA", horizon="1~3D", data="5M", falsify="前30m突破无日内/次日预测力")

    # --- source 5: AI novel mechanisms (~15%, 8 hypotheses) ---
    add("A4-401","收益路径质量：20日下跌但路径平滑（非瀑布）","平滑下跌更可能是有序卖压，瀑布下跌是恐慌","RETURN_PATH_QUALITY",
        "AI_NOVEL", parent_factor="F_REV20", horizon="3~7D", falsify="路径平滑度分层无收益差")
    add("A4-402","隔夜与日内不对称：日内弱隔夜强","日内弱隔夜强代表尾盘有人吸筹","OVERNIGHT_INTRADAY_ASYMMETRY",
        "AI_NOVEL", horizon="1~5D", falsify="日内/隔夜拆分无预测差异")
    add("A4-403","价格冲击：下跌中量价背离（价跌量缩）","下跌量缩说明卖压不足","FLOW_ABSORPTION",
        "AI_NOVEL", parent_factor="F_LOWVOLBURST", horizon="3~7D", falsify="量价背离无增量")
    add("A4-404","20日反转 + 北向/机构龙虎榜净买共振","反转与聪明钱共振","SMART_MONEY",
        "AI_NOVEL", parent_factor="F_REV20", parent_event="E_LHB_INSTPOS", horizon="3~7D",
        falsify="与单因子/单事件相比无增量")
    add("A4-405","行业离散度低 + 行业首板（板块共振启动）","行业离散度低时出现首板，板块效应更强","SECTOR_DIFFUSION",
        "AI_NOVEL", parent_event="E_LIMITUP", horizon="1~5D", falsify="行业条件无增量")
    add("A4-406","波动压缩 + 跳空高开（压缩后突破）","低波动压缩后跳空突破方向性更强","VOLATILITY_COMPRESSION",
        "AI_NOVEL", parent_factor="F_VOLCOMP_LOW", horizon="1~5D", falsify="与单独跳空无差异")
    add("A4-407","连板高度与涨停家数背离（高度上/家数降）","高度上升但家数下降意味着情绪退潮前兆，次日风险","MARKET_SENTIMENT",
        "AI_NOVEL", horizon="1~3D", expected_direction="SHORT_OR_NO_TRADE",
        falsify="背离信号无市场级负/正收益")
    add("A4-408","龙头涨停后同行业低位股补涨（Leader-Follower）","龙头打出空间，低位同行业补涨","LEADER_FOLLOWER",
        "AI_NOVEL", horizon="1~5D", falsify="龙头-跟风收益差不显著")

    hyps = hyps[:60]
    print("total hypotheses (budget capped 60)", len(hyps))
    out = OUT / "hypotheses.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for h in hyps:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    pd.DataFrame(hyps).to_parquet(OUT / "hypotheses.parquet", index=False)
    pd.DataFrame(hyps).to_csv("reports/A4_HYPOTHESES_REGISTERED.csv", index=False)
    print("saved", len(hyps), "to", out)


if __name__ == "__main__":
    main()
