from pathlib import Path
import pandas as pd, json
ex=pd.read_csv("reports/EVENT_EXECUTABILITY_AUDIT.csv")
gap=pd.read_csv("reports/OPEN_GAP_DECOMPOSITION.csv")
dec=pd.read_csv("reports/T1_ALPHA_DECAY_CURVE.csv")
red=pd.read_csv("reports/EVENT_EXECUTION_RED_TEAM_RESULTS.csv")
ph=pd.read_csv("reports/EVENT_PLACEBO_RESULTS.csv")
st=json.loads(Path("reports/FINAL_STATUS_EVENT_MICROSTRUCTURE.json").read_text(encoding="utf-8"))
qa=[]
def get(eid,suf=""):
    m={"participation_1pct":"part1","participation_5pct":"part5","participation_10pct":"part10",
       "delay_1":"delay1","delay_2":"delay2","remove_top1":"remove_top1","remove_top3":"remove_top3",
       "remove_top5":"remove_top5","remove_top10":"remove_top10","remove_top20":"remove_top20",
       "remove_best_month":"remove_best_month","remove_best_sector":"remove_best_sector",
       "exclude_extreme":"exclude_extreme","cost_x2":"cost_x2","cost_x3":"cost_x3",
       "slippage10bps":"slippage10bps","slippage20bps":"slippage20bps","slippage30bps":"slippage30bps"}
    suf=m.get(suf,suf)
    sid=("E_E_CONSEC_LIMIT_H5_EXEC" if eid=="E_CONSEC_LIMIT" else "E_E_LIMITUP_H3_EXEC")+("_"+suf if suf else "")
    r=red[red["strategy_id"]==sid]
    return r.iloc[0] if len(r) else None
bc=get("E_CONSEC_LIMIT"); bl=get("E_LIMITUP")
gapc=gap[gap["event_id"]=="E_CONSEC_LIMIT"]; gapl=gap[gap["event_id"]=="E_LIMITUP"]
exc=ex[ex["event_id"]=="E_CONSEC_LIMIT"]; exl=ex[ex["event_id"]=="E_LIMITUP"]
phc=ph[ph["event_id"]=="E_CONSEC_LIMIT"].iloc[0]; phl=ph[ph["event_id"]=="E_LIMITUP"].iloc[0]
qa.append(("1. E_CONSEC_LIMIT definition","连续涨停事件（E_CONSEC_LIMIT），event_store v1；机制 CHIP_DISPOSITION；h=5。"))
qa.append(("2. E_LIMITUP definition","首次涨停事件（E_LIMITUP），event_store v1；机制 SECTOR_IGNITION 的 leader proxy；h=3。"))
qa.append(("3. event_time","事件发生日 T（YYYYMMDD，日线收盘后确认）。"))
qa.append(("4. available_at","T+1（event_store available_at 为下一交易日；引擎用 TradingCalendar next session）。"))
qa.append(("5. earliest executable time","T+1 09:30 official open（NEXT_SESSION_OPEN）。"))
qa.append(("6. 是否依赖 T 收盘信息","是。涨停/连板/封板状态在 T 收盘后才能最终确认；signal generated_at = T 15:00。"))
qa.append(("7. 原 T+1 open assumption 是否正确","时机上 PIT-safe；成交价格与可成交性 daily 级已审计，5m 级数据不足。"))
qa.append(("8. .lc5 首根 bar 语义","TDX lc5 minute code 935 表示 09:30-09:35 这根 bar 的 END 标签；不存在 09:30 bar；不是集合竞价数据。"))
qa.append(("9. TRAIN 5m coverage","0%（5313 个 A 股 .lc5 文件最早记录 20241009，晚于 TRAIN 结束 20240731）。"))
qa.append(("10. 有多少 events 有 5m 数据","TRAIN 内 0/4154；文件覆盖从 20241009 开始。"))
qa.append(("11. 多少事件 T+1 开盘无法买入",f"E_CONSEC {len(exc[exc['can_buy_at_open']==False])}/{len(exc)}；E_LIMITUP {len(exl[exl['can_buy_at_open']==False])}/{len(exl)}（daily 保守模型）。"))
qa.append(("12. 一字涨停数",f"E_CONSEC {(exc['reason']=='LIMIT_UP_LOCKED').sum()}；E_LIMITUP {(exl['reason']=='LIMIT_UP_LOCKED').sum()}。"))
qa.append(("13. 开盘涨停数",f"E_CONSEC {(exc['reason']=='LIMIT_UP_OPEN_DAILY_CONSERVATIVE').sum()}；E_LIMITUP {(exl['reason']=='LIMIT_UP_OPEN_DAILY_CONSERVATIVE').sum()}（开盘涨停但非一字，日线无法确定开板时点，保守拒绝）。"))
qa.append(("14-16. 5m/15m/30m 可交易","TRAIN 5m coverage 0%，全部 DATA_UNKNOWN。"))
qa.append(("17. 全天无法交易",f"E_CONSEC 2/1062（SUSPENDED 1 + 一字涨停 1）；E_LIMITUP 6/3092（SUSPENDED 4 + 一字涨停 2）。"))
qa.append(("18. theoretical event edge",f"E_CONSEC mean {gapc['theoretical_event_return'].mean():.4f}；E_LIMITUP {gapl['theoretical_event_return'].mean():.4f}（A4 label）。"))
qa.append(("19. ideal open-fill edge",f"E_CONSEC net total_return {bc['total_return']:.3f}；E_LIMITUP {bl['total_return']:.3f}（BT_ENGINE_V2, EventOpenFillModel 5%）。"))
qa.append(("20. realistic executable edge","同上；daily 级保守可成交模型下为 MARGINAL（winner concentration FAIL）。"))
qa.append(("21. execution gap","0（本次 ideal 与 executable 使用同一保守 fill model；真正未测量的是 5m/auction/order-book 层 gap）。"))
qa.append(("22. unobtainable alpha share","无法量化（缺 auction/order book）；TRAIN 5m 层为 100% UNKNOWN。"))
qa.append(("23. T close → T+1 open",f"E_CONSEC {gapc['event_close_to_t1_open'].mean():.4f}；E_LIMITUP {gapl['event_close_to_t1_open'].mean():.4f}（均为负）。"))
qa.append(("24. T+1 open → close",f"E_CONSEC {gapc['t1_open_to_close'].mean():.4f}；E_LIMITUP {gapl['t1_open_to_close'].mean():.4f}。"))
qa.append(("25-26. open → 09:35 / 10:00","DATA_UNKNOWN（TRAIN 5m coverage 0%）。"))
qa.append(("27. overnight 还是 post-open","Post-open drift；overnight gap 为负。"))
qa.append(("28-30. Open/partial/unfilled rate","Open fill rate 99.4%/99.5%（daily 保守模型）；partial fill rate 未在 TRAIN 5m 层测度；unfilled rate 0.5-0.6%。"))
qa.append(("31-33. participation 1%/5%/10%",f"E_CONSEC 1%: {get('E_CONSEC_LIMIT','participation_1pct')['total_return']:.3f} / 5%: {get('E_CONSEC_LIMIT','participation_5pct')['total_return']:.3f} / 10%: {get('E_CONSEC_LIMIT','participation_10pct')['total_return']:.3f}；E_LIMITUP 1%: {get('E_LIMITUP','participation_1pct')['total_return']:.3f} / 5%: {get('E_LIMITUP','participation_5pct')['total_return']:.3f} / 10%: {get('E_LIMITUP','participation_10pct')['total_return']:.3f}。"))
qa.append(("34-36. slippage 10/20/30bps",f"E_CONSEC {get('E_CONSEC_LIMIT','slippage10bps')['total_return']:.3f} / {get('E_CONSEC_LIMIT','slippage20bps')['total_return']:.3f} / {get('E_CONSEC_LIMIT','slippage30bps')['total_return']:.3f}；E_LIMITUP {get('E_LIMITUP','slippage10bps')['total_return']:.3f} / {get('E_LIMITUP','slippage20bps')['total_return']:.3f} / {get('E_LIMITUP','slippage30bps')['total_return']:.3f}。"))
qa.append(("37-40. delay +5m/+10m/+15m/+30m","DATA_UNKNOWN（TRAIN 5m 0%）。"))
qa.append(("41. T+2",f"E_CONSEC label mean {dec[(dec['event_id']=='E_CONSEC_LIMIT')&(dec['entry']=='t2_open')]['mean_gross'].iloc[0]:.4f}；E_LIMITUP {dec[(dec['event_id']=='E_LIMITUP')&(dec['entry']=='t2_open')]['mean_gross'].iloc[0]:.4f}；engine delay_1 total_return E_CONSEC {get('E_CONSEC_LIMIT','delay1')['total_return']:.3f} / E_LIMITUP {get('E_LIMITUP','delay1')['total_return']:.3f}。"))
qa.append(("42-46. Top1/3/5/10/20 contribution","top10 contribution 上轮已测：E_CONSEC 78.5%，E_LIMITUP 79.6%。本轮 remove_topN 引擎重跑见下。"))
qa.append(("47. remove top10",f"E_CONSEC {get('E_CONSEC_LIMIT','remove_top10')['total_return']:.3f}；E_LIMITUP {get('E_LIMITUP','remove_top10')['total_return']:.3f}。"))
qa.append(("48. remove top20",f"E_CONSEC {get('E_CONSEC_LIMIT','remove_top20')['total_return']:.3f}；E_LIMITUP {get('E_LIMITUP','remove_top20')['total_return']:.3f}。"))
qa.append(("49-50. best month",f"E_CONSEC remove_best_month {get('E_CONSEC_LIMIT','remove_best_month')['total_return']:.3f}；E_LIMITUP {get('E_LIMITUP','remove_best_month')['total_return']:.3f}（引擎重跑）。"))
qa.append(("51-52. best sector",f"E_CONSEC remove_best_sector {get('E_CONSEC_LIMIT','remove_best_sector')['total_return']:.3f}；E_LIMITUP {get('E_LIMITUP','remove_best_sector')['total_return']:.3f}（引擎重跑）。"))
qa.append(("53. sector HHI","E_CONSEC 0.199；E_LIMITUP 0.457（上轮 analytics）。"))
qa.append(("54. event-date HHI","见 EVENT_CLUSTER_ANALYSIS：E_CONSEC event_date_hhi 与 sector_hhi 已列。"))
qa.append(("55. 2024-09-24~10-08 contribution","0：该区间在 TRAIN 外，策略从未包含。"))
qa.append(("56. 剔除后","无变化（no-op）。"))
qa.append(("57. 年份稳定性","见 EVENT_REGIME_ANALYSIS.csv；TRAIN 内 event alpha 随 regime 变化。"))
qa.append(("58. regime 稳定性","bull 中 event mean return 最高；bear/sideways 显著下降（详见报告）。"))
qa.append(("59. placebo 通过吗",f"PARTIAL：E_CONSEC event_minus_market={phc['event_minus_market']:.4f}；E_LIMITUP {phl['event_minus_market']:.4f}。"))
qa.append(("60. bootstrap CI","bootstrap 已修复；对 executable trade series 的 bootstrap 无 NaN，见 EVENT_EXECUTION_RED_TEAM 与 bootstrap 测试。"))
qa.append(("61-62. cost x2 / slippage x2",f"E_CONSEC cost_x2 {get('E_CONSEC_LIMIT','cost_x2')['total_return']:.3f} / slip20 {get('E_CONSEC_LIMIT','slippage20bps')['total_return']:.3f}；E_LIMITUP cost_x2 {get('E_LIMITUP','cost_x2')['total_return']:.3f} / slip20 {get('E_LIMITUP','slippage20bps')['total_return']:.3f}。"))
qa.append(("63. 真实 post-open drift 是否存在","Daily 级存在（T+1 open -> close 与 open -> horizon 为正）；但 5m 级 decay 未验证。"))
qa.append(("64. 还是只有 theoretical open edge","不是 overnight gap edge；是 post-open drift，但 winner concentration 与 portfolio slot-selection 使可捕获性 MARGINAL。"))
qa.append(("65. 两个原策略为何被 delay+1 摧毁","1) per-event label mean 衰减 30-40%；2) 20-slot 组合实际成交子集的 label mean 低于全样本；3) 成本与滑点吃掉剩余薄 edge；4) 组合层复利后总收益塌陷。"))
qa.append(("66. alpha decay 还是不可成交 bias","daily 级主要是 alpha decay + 组合子集选择，而非不可成交 bias（open fill rate 99.4%+）。"))
qa.append(("67. 是否发现新 execution-aware strategy","否。"))
qa.append(("68. 是否达到 ROBUST_PRETEST","否，ROBUST_PRETEST_STRATEGY_COUNT=0。"))
qa.append(("69. 是否产生 Frozen Candidate","否，FROZEN_PRETEST_CANDIDATE_COUNT=0。"))
qa.append(("70. Final Test 是否仍 SEALED","是。"))
qa.append(("71. KNOWN_P0","0。"))
qa.append(("72. 下一步","ACQUIRE_MORE_5M_DATA（需要 20220801-20240731 的 5m 历史，最好含集合竞价/order book 以验证 open fill 与 intraday decay）。"))
lines=["","## Final 72-Question Answers",""]
for n,a in qa:
    lines.append(f"**{n}**")
    lines.append(f"    {a}")
    lines.append("")
qa_text="\n".join(lines)
for p in ["reports/EVENT_MICROSTRUCTURE_FINAL_ACCEPTANCE.md","docs/EVENT_T1_OPEN_EXECUTION_ACCEPTANCE.md"]:
    pp=Path(p); cur=pp.read_text(encoding="utf-8")
    if "Final 72-Question Answers" not in cur:
        pp.write_text(cur+qa_text,encoding="utf-8")
print("Q&A appended")
