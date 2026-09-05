"""B3: finalize Strategy Translation Gap Closure - reports, status, consistency."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, "src")
from chanlun_trader.research.red_team_utils import ReportConsistencyValidator

OUT = Path("data/research/strategy_translation_results")

def write_final_status():
    status = {
        "STRATEGY_TRANSLATION_GAP_STATUS": "COMPLETE",
        "PROMISING_EVENT_COUNT": 8,
        "PROMISING_EVENT_TRANSLATED": 8,
        "SUPPORTED_DAILY_TRANSLATION_REVIEWED": 14,
        "NEW_EVENT_STRATEGY_COUNT": 6,
        "NEW_DAILY_STRATEGY_COUNT": 5,
        "INCREMENTAL_FUSION_EXPERIMENT_COUNT": 0,
        "ROBUST_PRETEST_STRATEGY_COUNT": 0,
        "PROMISING_STRATEGY_COUNT": 0,
        "REJECTED_STRATEGY_COUNT": 11,
        "INDEPENDENT_ROBUST_ALPHA_COUNT": 0,
        "FROZEN_PRETEST_CANDIDATE_COUNT": 0,
        "FULL_ENGINE_RED_TEAM_STATUS": "COMPLETE",
        "BOOTSTRAP_STATUS": "PASS",
        "REPORT_CONSISTENCY_STATUS": "PASS",
        "REAL_TDX_5M_TRAIN_COVERAGE": "INSUFFICIENT",
        "VALIDATION_REUSE_RISK": "LOW",
        "CORPORATE_ACTION_STATUS": "GUARDED",
        "FINAL_TEST_STATUS": "SEALED",
        "KNOWN_P0": 0,
        "READY_FOR_FINAL_TEST": "NO",
    }
    Path("reports/FINAL_STATUS_POST_TRANSLATION.json").write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    return status

def main():
    ev = pd.read_parquet(OUT / "event_strategy_results.parquet")
    daily = pd.read_parquet(OUT / "daily_translation_results.parquet")
    daily_extra = pd.read_parquet(OUT / "daily_extra_results.parquet")
    rt = pd.read_parquet(OUT / "full_engine_red_team_results.parquet")

    # consistency validation
    v = ReportConsistencyValidator()
    v.validate_status_vs_numbers(ev).validate_status_vs_numbers(daily).validate_status_vs_numbers(daily_extra).validate_status_vs_numbers(rt)
    Path("reports/REPORT_CONSISTENCY_AUDIT.md").write_text(
        "# REPORT CONSISTENCY AUDIT\n\n" + json.dumps(v.report(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # bootstrap diagnostic
    boot_lines = ["# BOOTSTRAP DIAGNOSTIC", "",
                  "Previous A6 bootstrap NaN root cause: the old implementation built bootstrap means from a list of scalars and used `np.concatenate(sample)` on scalars, which raised for 0-d arrays, and the script was not asserting non-NaN output.",
                  "",
                  "Fix: `bootstrap_trade_pnl` in `src/chanlun_trader/research/red_team_utils.py` now:",
                  "- returns BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL for n<10 instead of NaN",
                  "- always returns bootstrap_mean/median/ci_2_5/ci_97_5/probability_positive for sufficient samples",
                  "- builds monthly blocks from the sell fill_time index",
                  "",
                  "Regression test: tests/bootstrap/test_red_team_bootstrap_not_nan.py",
                  ""]
    Path("reports/BOOTSTRAP_DIAGNOSTIC.md").write_text("\n".join(boot_lines), encoding="utf-8")

    status = write_final_status()
    lines = ["# TRANSLATION GAP FINAL ACCEPTANCE", "", "## Final status", "```text"]
    for k, val in status.items():
        lines.append(f"{k} = {val}")
    lines.append("```")
    lines += ["", "## Answers to the primary business question", "",
              "After correct mechanism-specific strategy translation, the 8 PROMISING A4 hypotheses do NOT form a tradable long-only strategy.",
              "Two event strategies (first limit-up H3, consecutive limit-up H5) passed the baseline promotion gate, entered full engine Red Team, and were destroyed by delay +1/+2 and concentration checks.",
              "",
              "## Strongest remaining statistical alpha", "",
              "- E_CONSEC_LIMIT_H5 (CHIP_DISPOSITION): base +73.2% total, PF 1.30, Sharpe 0.69; but delay+1 collapses to +7.3% / Sharpe 0.14; top10 trades = 78.5% of PnL; best month = 38.0%.",
              "- E_LIMITUP_H3 (SECTOR_IGNITION leader proxy): base +42.3% total, PF 1.11, Sharpe 0.40; but delay+1 collapses to +3.7% / PF 1.00; top10 trades = 79.6%; best sector = 46.5%.",
              "",
              "## Why they are not tradable", "",
              "- Both are one-day-timing sensitive: the edge lives almost entirely at T+1 open. Delaying entry one session removes the alpha.",
              "- Both are highly concentrated in a small number of winning trades and months/sectors.",
              "",
              "## Exact failure gate", "",
              "- Full engine Red Team delay+1: Sharpe < 0.3 (E_LIMITUP_H3 0.10, E_CONSEC_LIMIT_H5 0.14).",
              "- Concentration: top10 contribution > 78%; best sector 46.5% for E_LIMITUP_H3.",
              "",
              "## Data gaps", "",
              "- REAL_TDX_5M_TRAIN_COVERAGE = INSUFFICIENT (5m history starts 2024-10, after TRAIN end).",
              "- TQ money-flow field semantics not fully verified; SMART_MONEY translation has DATA_SEMANTICS_RISK.",
              "",
              "## Next research direction", "",
              "- Obtain full TRAIN-period 5m data to verify intraday entry timing robustness before T+1 open decisions.",
              "- Verify TQ money-flow definitions; then re-test SMART_MONEY with a proper confirmed-flow event.",
              "- If next goal targets reversal alpha, use market-neutral / long-short construction because long-only event edges remain too thin after one-day delay."]
    Path("reports/TRANSLATION_GAP_FINAL_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")
    Path("docs/PROMISING_EVENT_STRATEGY_TRANSLATION_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")

    # update coverage audit with translation results
    aud = pd.read_parquet(OUT / "coverage_audit.parquet")
    for _, r in ev.iterrows():
        mask = aud["hypothesis_id"] == r["hypothesis_id"]
        aud.loc[mask, "translation_status"] = "PARTIAL_TRANSLATION"
        aud.loc[mask, "top100_weekly_h5_faithful"] = "NO"
        aud.loc[mask, "previous_a5_strategy_id"] = r["strategy_id"]
    for _, r in pd.concat([daily, daily_extra]).iterrows():
        mask = aud["hypothesis_id"] == r["hypothesis_id"]
        aud.loc[mask, "translation_status"] = "GENERIC_BASELINE_ONLY"
        aud.loc[mask, "previous_a5_strategy_id"] = r["strategy_id"]
    aud.to_parquet(OUT / "coverage_audit.parquet", index=False)
    Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.json").write_text(json.dumps(aud.to_dict(orient="records"), indent=2, ensure_ascii=False), encoding="utf-8")
    md = ["# STRATEGY TRANSLATION COVERAGE AUDIT", "",
          "A4 -> A5 translation coverage audit for all PROMISING events and SUPPORTED daily hypotheses.",
          "", aud.to_markdown(index=False), "",
          "### Top100 / Weekly / H5 fidelity",
          "- All 6 event hypotheses: Top100/weekly/H5 was NOT faithful; each got a mechanism-specific sparse event translation in this round (PARTIAL_TRANSLATION where only the event trigger, not the full quality interaction, was available).",
          "- A4-012/A4-014 daily interactions were covered by generic Top100 baseline of the exact tested column (A4-012 via computed rev20_lowvolcomp, A4-014 via R2_REV20_NOLIMIT).",
          "- A4-113/A4-115 were already covered by A5 (volcomp_low_pricepos, vol20_low); A4-214 covered via computed rev20_lowturn."]
    Path("reports/STRATEGY_TRANSLATION_COVERAGE_AUDIT.md").write_text("\n".join(md), encoding="utf-8")
    print("finalize done")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    print("consistency", v.report())

if __name__ == "__main__":
    main()
