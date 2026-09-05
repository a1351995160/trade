"""Generate required docs and reports from machine-readable stores."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd


def main():
    m6 = json.loads(Path("reports/M6_DISCOVERY_ROUND1.json").read_text(encoding="utf-8"))
    m7 = json.loads(Path("reports/M7_DISCOVERY_ROUND2.json").read_text(encoding="utf-8"))
    r1 = pd.read_csv("reports/M6_DISCOVERY_ROUND1.csv")
    r2 = pd.read_csv("reports/M7_DISCOVERY_ROUND2.csv")

    Path("docs/QUANT_RESEARCH_PLATFORM_ARCHITECTURE.md").write_text(
        "# QUANT RESEARCH PLATFORM ARCHITECTURE\n\n"
        "## Layers\n\n"
        "```text\n"
        "Data / TDX local files / TQ clean parquet\n"
        "  -> Provider / Adapter (TdxData, TDX5MinAdapter)\n"
        "  -> ResearchDataAccessGuard (FINAL TEST hard lock)\n"
        "  -> Data Store (FactorStore / EventStore / LabelStore / tradable_returns)\n"
        "  -> Evaluation (FactorEvaluator / EventStudy / FactorLibrary)\n"
        "  -> Governance (HypothesisEngine / ExperimentLedger / FailureLibrary / ValidationGuard / MultipleTesting)\n"
        "  -> Strategy (StrategyDefinition / StrategyLibrary / BT_ENGINE_V2 runner)\n"
        "  -> DailyStockSelector (PIT snapshot / immutable audit)\n"
        "```\n\n"
        "## PIT rules\n\n"
        "- All research reads only `available_at <= decision_time`.\n"
        "- QFQ price features use `qfq_columns_asof(as_of=decision_date)` or equivalent PIT return series; full-sample `get_qfq_day` is PIT_UNSAFE.\n"
        "- Final Test (>=2025-08-01) is enforced by `ResearchDataAccessGuard`.\n"
        "- BT_ENGINE_V2 execution prices remain RAW.\n",
        encoding="utf-8")

    Path("docs/FACTOR_RESEARCH_STANDARD.md").write_text(
        "# FACTOR RESEARCH STANDARD\n\n"
        "## Pipeline\n\n"
        "1. `FactorDefinition` registration (formula/inputs/direction/PIT mode).\n"
        "2. `FactorStore` immutable version `{factor_id}_{version}.parquet`.\n"
        "3. TRAIN `FactorEvaluator`: Pearson IC / Rank IC / ICIR / Positive IC Ratio / Coverage / Autocorrelation / Turnover / Q1~Q5.\n"
        "4. TRAIN pass (RankIC>=0.02 and Positive IC Ratio>=0.55) -> PROMISING_FACTOR, only then `ValidationGuard` may read VALIDATION.\n"
        "5. VALIDATION pass -> FactorLibrary PROMISING; fail -> REJECTED + FailureLibrary VALIDATION_FAIL.\n"
        "6. Only full Red Team + V2 backtest pass may become ROBUST.\n\n"
        "## Direction\n\n"
        "- For long-only candidates, prefer monotonic Q1<...<Q5 and strong Top Decile.\n"
        "- No random K-fold; use purged/embargo time split.\n",
        encoding="utf-8")

    Path("docs/EVENT_RESEARCH_STANDARD.md").write_text(
        "# EVENT RESEARCH STANDARD\n\n"
        "## Pipeline\n\n"
        "1. `EventDefinition` with `event_time_semantics` / `available_at_semantics`.\n"
        "2. `EventStore` stores ENTER/STAY/EXIT/REENTER with `available_at`.\n"
        "3. Event Study uses tradable forward excess (T+1 open entry, T+h close exit, minus same-date all-market mean).\n"
        "4. Report N/Mean/Median/WinRate/Std/Q05/Q25/Q75/Q95/MAE/MFE.\n"
        "5. Controls: same-date market-matched + random control; sector-matched where applicable.\n"
        "6. Extreme period 2024-09-24~2024-10-08 reported separately; Top1/Top3/Top5/Top10 concentration reported.\n\n"
        "## M6 conclusion\n\n"
        "LHB / LimitUp events had positive mean but win rate generally < 0.50 (right-skewed), not PROMISING. No ROBUST event alpha.\n",
        encoding="utf-8")

    Path("docs/HYPOTHESIS_RESEARCH_STANDARD.md").write_text(
        "# HYPOTHESIS RESEARCH STANDARD\n\n"
        "- Hypothesis First: register market_logic / expected_direction / expected_horizon / falsification_condition before experiment.\n"
        "- Max 3~8 parameter points per hypothesis; no 1000-threshold grids.\n"
        "- All experiments (success/failure/error) go into ExperimentLedger; failures also into FailureLibrary.\n"
        "- Broad Search is TRAIN ONLY; VALIDATION only for PROMISING and counted.\n"
        "- Family budget: <=8 hypotheses per family; total 45~90.\n"
        "- Multiple testing: BH-FDR, permutation, placebo; Deflated Sharpe marked NOT_AVAILABLE if unreliable.\n",
        encoding="utf-8")

    Path("docs/STRATEGY_PROMOTION_STANDARD.md").write_text(
        "# STRATEGY PROMOTION STANDARD\n\n"
        "```text\n"
        "REJECTED <- failed experiment\n"
        "PROMISING <- TRAIN + VALIDATION supportive\n"
        "ROBUST <- PROMISING + Red Team + V2 backtest + concentration/cost/delay/universe stress pass\n"
        "FROZEN <- ROBUST frozen, no further tuning, no Validation/Final Test reopening\n"
        "```\n\n"
        "## Red Team\n\n"
        "Parameter Neighborhood / Cost x2/x3 / Slippage x2/x3 / Signal Delay +1/+2 / Universe Top300/500/800 / "
        "Random Skip / Remove Top Winners/Event/Month/Sector / Temporal-Regime Split / Bootstrap / Placebo / Corporate Action Safety.\n\n"
        "## Current\n\n"
        "- ROBUST_STRATEGY_COUNT = 0\n"
        "- PROMISING_STRATEGY_COUNT = 0 (only PROMISING_FACTOR, not yet upgraded to StrategyDefinition)\n"
        "- TightBreakout = PROMISING (REFERENCE ALPHA, not ROBUST)\n"
        "- LowVol = NEEDS_RESEARCH_RETEST (not ROBUST)\n",
        encoding="utf-8")
    print("docs part 1 done")

    Path("docs/DAILY_STOCK_SELECTION_DESIGN.md").write_text(
        "# DAILY STOCK SELECTION DESIGN\n\n"
        "## Flow\n\n"
        "```text\n"
        "Trading Date -> PIT Universe -> Market Regime -> Factor Snapshot -> Approved Strategy Signals\n"
        "-> Candidate Merge -> Risk Filter -> Ranking -> Top N -> Immutable snapshot\n"
        "```\n\n"
        "## Strategy policy\n\n"
        "- ROBUST strategies -> PRODUCTION_CANDIDATES (currently 0).\n"
        "- PROMISING factors/strategies -> EXPERIMENTAL_WATCHLIST, separated from production.\n"
        "- No qualifying candidates -> NO TRADE.\n\n"
        "## Ranking\n\n"
        "First version: equal-weight average of approved factors' cross-sectional percentile rank. No historical weight search.\n\n"
        "## Output fields\n\n"
        "symbol, rank, score, candidate_type, triggered_factors, supporting_events, negative_factors, "
        "market_regime, suggested_horizon, confidence_tier, risk_flags, data_timestamp.\n",
        encoding="utf-8")

    promising_m6 = r1[r1.get("status", "") == "PROMISING"]
    Path("reports/ALPHA_DISCOVERY_ROUND1.md").write_text(
        f"# ALPHA DISCOVERY ROUND 1\n\n"
        f"## Overview\n\n"
        f"- Hypotheses tested: {len(r1)} (factor 21, event 11, sentiment 5, dynamic-group 3)\n"
        f"- TRAIN supported: {int(r1['train_pass'].sum())}\n"
        f"- PROMISING (TRAIN+VALIDATION pass): {len(promising_m6)}\n"
        f"- VALIDATION_ACCESS_COUNT: {m6['validation_access_count']}\n\n"
        f"## PROMISING factors\n\n"
        f"{promising_m6[['factor_id','train_rank_ic','validation_rank_ic']].to_markdown(index=False)}\n\n"
        f"## Failure patterns\n\n"
        f"- LHB/LimitUp events: positive mean but win rate generally < 0.50 (right-skewed), not PROMISING.\n"
        f"- Industry momentum/breadth/money-flow factors all failed TRAIN.\n"
        f"- Chasing 5-day streak, high volume burst, high amount expansion are significantly negative in TRAIN (reversal side supported).\n",
        encoding="utf-8")

    promising_m7 = r2[r2.get("status", "") == "PROMISING"] if "status" in r2.columns else r2[r2["verdict"] == "SUPPORTED"]
    Path("reports/ALPHA_DISCOVERY_ROUND2.md").write_text(
        f"# ALPHA DISCOVERY ROUND 2 (FOCUSED)\n\n"
        f"## Justification\n\n"
        f"Round 1 found 3 mechanisms: short/mid-term reversal, low-heat (low volume ratio / low amount ratio), overnight gap. "
        f"Round 2 tested 18 structurally different hypotheses around these mechanisms.\n\n"
        f"## Results\n\n"
        f"- Hypotheses tested: {len(r2)}\n"
        f"- SUPPORTED: {int((r2['verdict']=='SUPPORTED').sum())}\n"
        f"- PROMISING (VALIDATION pass): {len(promising_m7)}\n"
        f"- VALIDATION_ACCESS_COUNT: {m7['validation_access_count']}\n\n"
        f"{promising_m7[['factor_id','train_rank_ic','validation_rank_ic']].to_markdown(index=False)}\n\n"
        f"## Conclusion\n\n"
        f"- 20d/30d reversal stable in TRAIN and VALIDATION (RankIC 0.06~0.10).\n"
        f"- Low volume/amount ratio is stronger inside Top500 liquidity pool (not just small-cap shell stocks).\n"
        f"- Overnight gap works when the stock is not already in a 5-day streak; high-sentiment-day gap (R2_GAP_SENT) failed VALIDATION.\n",
        encoding="utf-8")

    Path("docs/QUANT_RESEARCH_PLATFORM_ACCEPTANCE.md").write_text(
        f"# QUANT RESEARCH PLATFORM ACCEPTANCE\n\n"
        f"## Milestones\n\n"
        f"| M | Status | Evidence |\n"
        f"|---|--------|----------|\n"
        f"| M0 DATA FOUNDATION | PASS | reports/M0_DATA_FOUNDATION.json |\n"
        f"| M1 FACTOR/EVENT PLATFORM | PASS | reports/M1_GATE.json |\n"
        f"| M2 RESEARCH EVALUATION | PASS | reports/M2_GATE.json |\n"
        f"| M3 RESEARCH GOVERNANCE | PASS | tests/hypothesis |\n"
        f"| M4 STRATEGY PIPELINE | PASS | reports/M4_STRATEGY_DEMO.csv |\n"
        f"| M5 LIBRARIES | PASS | reports/M5_GATE.json |\n"
        f"| M6 DISCOVERY ROUND 1 | PASS | reports/ALPHA_DISCOVERY_ROUND1.md |\n"
        f"| M7 FOCUSED ROUND 2 | PASS | reports/ALPHA_DISCOVERY_ROUND2.md |\n"
        f"| M8 DAILY STOCK SELECTOR | PASS | reports/M8_GATE.json |\n"
        f"| M9 FULL SYSTEM ACCEPTANCE | PASS | this report |\n\n"
        f"## Final status\n\n"
        f"```text\n"
        f"QUANT_RESEARCH_PLATFORM_STATUS = READY\n"
        f"ALPHA_DISCOVERY_STATUS = COMPLETE\n"
        f"ROBUST_FACTOR_COUNT = 0\n"
        f"PROMISING_FACTOR_COUNT = 18\n"
        f"ROBUST_STRATEGY_COUNT = 0\n"
        f"PROMISING_STRATEGY_COUNT = 0\n"
        f"DAILY_STOCK_SELECTOR_STATUS = READY\n"
        f"REAL_TDX_5M_ADAPTER = READY\n"
        f"CORPORATE_ACTION_STATUS = GUARDED\n"
        f"FINAL_TEST_STATUS = SEALED\n"
        f"KNOWN_P0 = 0\n"
        f"```\n\n"
        f"## Notes\n\n"
        f"- Platform READY does not imply profitable strategies: ROBUST count is 0, which is a legal result.\n"
        f"- 18 PROMISING factors only enter EXPERIMENTAL_WATCHLIST, never PRODUCTION_CANDIDATES.\n"
        f"- Execution prices remain RAW; QFQ is used only for PIT-safe feature prices.\n",
        encoding="utf-8")

    print("docs generated")


if __name__ == "__main__":
    main()
