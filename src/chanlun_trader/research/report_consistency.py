"""Consistency checks for translation and red-team reports."""
from __future__ import annotations

from typing import Iterable

import math


def _finite(value) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def validate_report_consistency(summary: dict, rows: Iterable[dict]) -> list[str]:
    """Return actionable inconsistencies between numeric data and claims.

    The validator intentionally checks only claims emitted by the translation
    runner. It does not infer trading conclusions from arbitrary prose.
    """
    errors: list[str] = []
    rows = list(rows)
    baseline = [r for r in rows if r.get("run_type") == "BASELINE"]
    positive = [r for r in baseline if _finite(r.get("net_total_return")) and r["net_total_return"] > 0]
    if summary.get("baseline_positive_count") != len(positive):
        errors.append("baseline_positive_count does not match baseline rows")

    if baseline:
        best = max(baseline, key=lambda r: r.get("net_total_return", float("-inf")))
        if summary.get("best_strategy_id") != best.get("strategy_id"):
            errors.append("best_strategy_id does not match the maximum net_total_return")

    claims = summary.get("claims", {})
    if claims.get("universe_top800_negative") is True:
        universe_rows = [r for r in rows if r.get("stress") == "universe_top800"]
        if universe_rows and any((r.get("net_total_return") or 0) >= 0 for r in universe_rows):
            errors.append("universe_top800_negative claim conflicts with numeric rows")
    if claims.get("stress_survives_cost_x2") is True:
        stress = [r for r in rows if r.get("stress") == "cost_x2"]
        if stress and any((r.get("net_total_return") or 0) <= 0 for r in stress):
            errors.append("cost_x2 survivor claim conflicts with numeric rows")

    bootstrap_status = summary.get("bootstrap_status")
    for row in rows:
        if row.get("stress") != "bootstrap":
            continue
        if bootstrap_status == "PASS":
            for key in ("bootstrap_mean", "bootstrap_median", "bootstrap_ci_2_5", "bootstrap_ci_97_5", "probability_positive"):
                if not _finite(row.get(key)):
                    errors.append(f"bootstrap PASS row has invalid {key}")
        if row.get("bootstrap_status") == "PASS" and any(
            not _finite(row.get(key))
            for key in ("bootstrap_mean", "bootstrap_median", "bootstrap_ci_2_5", "bootstrap_ci_97_5", "probability_positive")
        ):
            errors.append("bootstrap row marked PASS contains NaN")

    promotion = summary.get("promotion_status")
    if promotion == "ROBUST_PRETEST" and summary.get("red_team_status") != "COMPLETE":
        errors.append("ROBUST_PRETEST promotion requires COMPLETE full-engine red team")
    if summary.get("final_test_status") != "SEALED":
        errors.append("final_test_status must remain SEALED")
    return errors
