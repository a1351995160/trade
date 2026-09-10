"""真实 provider 已允许的零动作安全统计必须能跨入持久化；收益字段仍拒绝。"""
import pytest

from chanlun_trader.research_daemon import StructuralResult, STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS
from chanlun_trader.research_factory.context import PerformanceBlindGuard
from chanlun_trader.research_factory.structural_reconciliation import _assert_structural_blind


def result(value=0, extra=None):
    safety = {"prospective": value, **(extra or {})}
    details = {"outcome_blind": True, "performance_data_loaded": False,
        "lower_bound_integrity": {"checks": [{"evidence": {"architecture_safety": safety}}]}}
    return StructuralResult("UNKNOWN", "D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED", details=details)


def test_producer_safe_zero_statistic_can_be_persisted():
    value = result()
    PerformanceBlindGuard.assert_blind(value.details, allowed_paths=STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS)
    _assert_structural_blind(value)


@pytest.mark.parametrize("value,extra", [(1, None), (0, {"net_return": 0.2}), (0, {"sharpe": 1.1})])
def test_structural_result_never_accepts_outcomes(value, extra):
    with pytest.raises(RuntimeError, match="outcome-blind"):
        _assert_structural_blind(result(value, extra))
