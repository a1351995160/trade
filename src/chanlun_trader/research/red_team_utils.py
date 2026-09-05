"""Red Team utilities: bootstrap on trade PnL + report consistency validator."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd


def bootstrap_trade_pnl(pnl: pd.Series, n_boot: int = 200, seed: int = 42) -> dict:
    """Monthly block bootstrap on realized trade PnL.

    Returns bootstrap_status OK with mean/median/CI/probability_positive when sample
    size is sufficient. Returns BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL instead of
    silently returning NaN.
    """
    pnl = pnl.dropna()
    if len(pnl) < 10:
        return dict(bootstrap_status="BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL",
                    bootstrap_mean=None, bootstrap_median=None,
                    bootstrap_ci_2_5=None, bootstrap_ci_97_5=None, probability_positive=None)
    rng = np.random.default_rng(seed)
    vals = pnl.to_numpy()
    try:
        months = pd.to_datetime(pd.Series(pnl.index)).dt.strftime("%Y-%m")
        blocks = [vals[months == m] for m in months.unique()]
    except Exception:
        blocks = [vals[i:i + 10] for i in range(0, len(vals), 10)]
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(b) for b in blocks if len(b) > 0]
        if sample:
            means.append(float(np.mean(np.concatenate([np.atleast_1d(x) for x in sample]))))
    means = np.array(means)
    if means.size == 0:
        return dict(bootstrap_status="BOOTSTRAP_UNAVAILABLE_SAMPLE_TOO_SMALL",
                    bootstrap_mean=None, bootstrap_median=None,
                    bootstrap_ci_2_5=None, bootstrap_ci_97_5=None, probability_positive=None)
    return dict(bootstrap_status="OK", bootstrap_mean=float(means.mean()),
                bootstrap_median=float(np.median(means)),
                bootstrap_ci_2_5=float(np.percentile(means, 2.5)),
                bootstrap_ci_97_5=float(np.percentile(means, 97.5)),
                probability_positive=float((means > 0).mean()))


class ReportConsistencyValidator:
    """Numeric consistency checks between tabular results and natural-language claims."""

    def __init__(self):
        self.errors: list[str] = []

    def validate_status_vs_numbers(self, df: pd.DataFrame) -> "ReportConsistencyValidator":
        for _, row in df.iterrows():
            status = row.get("status")
            pf = row.get("profit_factor")
            tr = row.get("total_return")
            sh = row.get("sharpe")
            if pd.isna(pf) or pd.isna(tr) or pd.isna(sh):
                continue
            if status == "PROMISING":
                if not (pf > 1 and tr > 0 and sh >= 0.3):
                    self.errors.append(f"{row.get('strategy_id')}: status PROMISING but numbers do not meet gate")
            if status == "REJECTED":
                if pf > 1 and tr > 0 and sh >= 0.3:
                    self.errors.append(f"{row.get('strategy_id')}: status REJECTED but numbers meet gate")
        return self

    def validate_no_false_claims(self, text: str, df: pd.DataFrame) -> "ReportConsistencyValidator":
        rejected = {str(r.get("strategy_id")) for _, r in df.iterrows() if r.get("status") == "REJECTED"}
        for word in ("survives", "passes", "robust"):
            if word in text.lower():
                for sid in rejected:
                    if sid in text:
                        self.errors.append(f"report claims '{word}' for REJECTED strategy {sid}")
        return self

    def validate_except_clause(self, text: str, df: pd.DataFrame) -> "ReportConsistencyValidator":
        low = text.lower()
        if "except " in low:
            for _, r in df.iterrows():
                sid = str(r.get("strategy_id"))
                if sid in text and "except" in low.split(sid.lower())[0][-80:]:
                    if (r.get("total_return") or 0) <= 0:
                        self.errors.append(f"except clause used for non-positive strategy {sid}")
        return self

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def report(self) -> dict:
        return {"report_consistency_status": "PASS" if self.passed else "FAILED", "errors": self.errors}
