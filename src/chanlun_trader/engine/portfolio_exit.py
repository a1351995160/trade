"""Portfolio-aware exit lifecycle for engine-corrected research runs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

from .position import PositionLot
from .time_types import ensure_aware


@dataclass(frozen=True)
class PortfolioExitDecision:
    candidate_id: str
    portfolio_id: str
    lot_id: str
    symbol: str
    trade_session: int
    trade_session_index: int
    state: str
    reason_code: str
    factor_values: Mapping[str, float]


class PortfolioExitEvaluatorV1:
    """Evaluate frozen exit predicates against this portfolio's actual lots.

    Entry signals may be shared between portfolios.  This evaluator never
    reads another portfolio's state and never waits for a future alpha signal
    after a lot has entered ``EXIT_DUE`` or ``SELL_PENDING``.
    """

    VERSION = "PORTFOLIO_EXIT_EVALUATOR_V1"

    def __init__(self, candidate_id: str, portfolio_id: str, contract: Mapping[str, Any]):
        self.candidate_id = str(candidate_id)
        self.portfolio_id = str(portfolio_id)
        self.contract = contract
        self.exit_type = str(contract.get("exit_type", "FIXED_HOLD"))
        self.fixed_holding_sessions = int(contract.get("fixed_holding_sessions", 0))
        self.factor_conditions = list(contract.get("factor_conditions", []))
        self.logic = str(contract.get("logic", "OR"))

    @staticmethod
    def _compare(value: float, operator: str, target: float) -> bool:
        return {
            "GT": value > target,
            "GE": value >= target,
            "LT": value < target,
            "LE": value <= target,
            "EQ": value == target,
            "NE": value != target,
        }.get(str(operator), False)

    def _structure_invalidated(self, values: Mapping[str, float]) -> bool:
        if not self.factor_conditions:
            return False
        checks = []
        for condition in self.factor_conditions:
            factor_id = str(condition["factor_id"])
            if factor_id not in values:
                return False
            try:
                checks.append(self._compare(float(values[factor_id]), str(condition["operator"]), float(condition["value"])))
            except (TypeError, ValueError):
                return False
        return any(checks) if self.logic == "OR" else all(checks)

    def evaluate(
        self,
        lots: Iterable[PositionLot],
        trade_session: int,
        trade_session_index: int,
        factor_state: Mapping[str, Mapping[str, Any]],
        trade_timestamp: pd.Timestamp,
    ) -> list[PortfolioExitDecision]:
        """Return one decision per lot that is due or already pending."""
        trade_timestamp = ensure_aware(trade_timestamp)
        decisions: list[PortfolioExitDecision] = []
        for lot in sorted(lots, key=lambda item: (item.symbol, item.buy_time, item.lot_id)):
            if lot.remaining_quantity <= 0 or lot.exit_state == "CLOSED":
                continue
            if lot.entry_session_index is None:
                lot.exit_reason = "ENTRY_SESSION_INDEX_MISSING"
                continue
            due_index = int(lot.entry_session_index) + self.fixed_holding_sessions
            lot.exit_due_index = due_index
            if lot.exit_due_session is None and trade_session_index >= due_index:
                lot.exit_due_session = trade_session
            if lot.exit_state in {"EXIT_DUE", "SELL_PENDING", "PARTIALLY_FILLED"}:
                decisions.append(PortfolioExitDecision(
                    candidate_id=self.candidate_id,
                    portfolio_id=self.portfolio_id,
                    lot_id=lot.lot_id,
                    symbol=lot.symbol,
                    trade_session=trade_session,
                    trade_session_index=trade_session_index,
                    state="SELL_PENDING" if lot.exit_state != "EXIT_DUE" else "EXIT_DUE",
                    reason_code=lot.exit_reason or "EXIT_PENDING_RETRY",
                    factor_values={},
                ))
                continue
            state = factor_state.get(lot.symbol, {})
            values = state.get("values", {}) if isinstance(state, Mapping) else {}
            available_at = state.get("available_at") if isinstance(state, Mapping) else None
            if available_at is not None and ensure_aware(available_at) > trade_timestamp:
                continue
            fixed_due = trade_session_index - int(lot.entry_session_index) >= self.fixed_holding_sessions
            structure_due = self.exit_type == "STRUCTURE_INVALIDATION" and self._structure_invalidated(values)
            if not fixed_due and not structure_due:
                continue
            if structure_due:
                reason_code = "EXIT_DUE_STRUCTURE_INVALIDATION"
            else:
                reason_code = "EXIT_DUE_FIXED_HOLD"
            lot.exit_state = "EXIT_DUE"
            lot.exit_reason = reason_code
            lot.exit_due_session = trade_session
            decisions.append(PortfolioExitDecision(
                candidate_id=self.candidate_id,
                portfolio_id=self.portfolio_id,
                lot_id=lot.lot_id,
                symbol=lot.symbol,
                trade_session=trade_session,
                trade_session_index=trade_session_index,
                state="EXIT_DUE",
                reason_code=reason_code,
                factor_values=dict(values),
            ))
        return decisions
