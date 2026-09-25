"""历史输入资格：没有当时的供应商发布或采集证据就不宣称 PIT 已通过。"""
from __future__ import annotations

import pandas as pd


def availability_at_decision(*, decision_at: str, observed_at: str | None,
                             evidence_kind: str) -> bool:
    if (evidence_kind not in ("PROVIDER_PUBLISHED_AT", "CONTEMPORANEOUS_CAPTURE")
            or observed_at is None or pd.isna(observed_at)):
        return False
    observed, decision = pd.Timestamp(observed_at), pd.Timestamp(decision_at)
    if observed.tzinfo is None or decision.tzinfo is None:
        raise ValueError("AVAILABILITY_TIMESTAMP_REQUIRES_TIMEZONE")
    return observed <= decision


def s1_source_qualification(turn_manifest: dict, daily: pd.DataFrame, turn: pd.DataFrame,
                            historical_states: pd.DataFrame,
                            *, account_end_date: int) -> dict:
    fetched = turn_manifest.get("fetched_at_utc")
    turn_verified = turn_manifest.get("historical_available_at_verified") is True
    turn_rows_verified = (not turn.empty and "source_published_at" in turn
                          and "availability_evidence_kind" in turn
                          and turn.apply(lambda row: availability_at_decision(
                              decision_at=f"{row.date} 15:30:00+08:00",
                              observed_at=row.source_published_at,
                              evidence_kind=row.availability_evidence_kind), axis=1).all())
    state_lineage = historical_states["source_lineage"].astype(str)
    daily_observed = (not daily.empty and "source_published_at" in daily
                      and "availability_evidence_kind" in daily
                      and daily.apply(lambda row: availability_at_decision(
                          decision_at=f"{row.date} 15:30:00+08:00",
                          observed_at=row.source_published_at,
                          evidence_kind=row.availability_evidence_kind), axis=1).all())
    # 上游 available_at 是推定的次日开盘时间；不能把该列本身当成来源证明。
    state_observed = (not historical_states.empty and "source_published_at" in historical_states
                      and "availability_evidence_kind" in historical_states
                      and historical_states.apply(lambda row: availability_at_decision(
                          decision_at=f"{row.trade_date} 15:30:00+08:00",
                          observed_at=row.source_published_at,
                          evidence_kind=row.availability_evidence_kind), axis=1).all())
    blockers = []
    if (not turn_verified or not turn_rows_verified or not fetched
            or int(pd.Timestamp(fetched).strftime("%Y%m%d")) > account_end_date):
        blockers.append("TURN_HISTORICAL_PUBLICATION_UNVERIFIED")
    if not state_observed:
        blockers.append("STATE_HISTORICAL_PUBLICATION_UNVERIFIED")
    if not daily_observed:
        blockers.append("DAILY_HISTORICAL_PUBLICATION_UNVERIFIED")
    return {"strict_pit_status": "VERIFIED" if not blockers else "BLOCKED",
            "blockers": blockers, "turn_snapshot_fetched_at_utc": fetched,
            "turn_historical_available_at_verified": turn_verified,
            "turn_row_publication_verified": bool(turn_rows_verified),
            "turn_rows": len(turn),
            "daily_row_publication_verified": bool(daily_observed),
            "daily_rows": len(daily),
            "historical_state_rows": len(historical_states),
            "historical_state_lineages": sorted(set(state_lineage)),
            "modeled_available_at_is_source_evidence": False}
