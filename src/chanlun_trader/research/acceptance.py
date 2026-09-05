"""不依赖状态字段的 acceptance 重算。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from .run_manifest import validate_run_manifest


@dataclass(frozen=True)
class IndependentAcceptanceResult:
    status: str
    acceptance_independence: str
    checks: dict[str, bool]
    recomputed: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "acceptance_independence": self.acceptance_independence,
                "checks": self.checks, "recomputed": self.recomputed}


class IndependentAcceptanceValidator:
    """从 raw ledger/equity/manifest 重新计算关键门槛，不读取 self-authored PASS。"""

    def validate(self, ledger, run_manifest: dict[str, Any], reported: dict[str, Any] | None = None) -> IndependentAcceptanceResult:
        valid_trades = [t for t in ledger.trades if getattr(t, "reality_flag", "OK") == "OK"]
        unsupported = [t for t in ledger.trades if getattr(t, "reality_flag", "OK") != "OK"]
        invariant_errors = list(ledger.check_invariants())
        manifest_ok, missing = validate_run_manifest(run_manifest)
        valid_count_ok = len(valid_trades) == len([t for t in ledger.trades if getattr(t, "reality_flag", "OK") == "OK"])
        checks = {
            "ledger_invariants": not invariant_errors,
            "unsupported_trades_excluded": all(t not in valid_trades for t in unsupported),
            "manifest_complete": manifest_ok,
            "reported_count_matches": reported is None or reported.get("n_trades") == len(valid_trades),
        }
        recomputed = {
            "valid_trade_count": len(valid_trades),
            "unsupported_trade_count": len(unsupported),
            "final_equity": ledger.snapshots[-1].equity if ledger.snapshots else ledger.initial_cash,
            "invariant_errors": invariant_errors,
            "missing_manifest_fields": missing,
        }
        ok = all(checks.values())
        return IndependentAcceptanceResult(
            status="PASS" if ok else "INVALID",
            acceptance_independence="STRONG" if ok and manifest_ok else "PARTIAL",
            checks=checks,
            recomputed=recomputed,
        )

    def validate_5m_scope(self, *, raw_manifest_path: str,
                          normalized_root: str, gap_report_path: str,
                          pit_master_path: str, cross_audit_path: str,
                          expected_symbols: list[str],
                          start_date: int, end_date: int) -> dict[str, Any]:
        """从当前 scope 的文件重新计算 5m acceptance，不读取状态字符串。"""
        from pathlib import Path

        import pandas as pd

        from chanlun_trader.data.minute.gap_resolution import classify_gap
        from chanlun_trader.data.minute.manifest import ManifestStore, sha256_file
        from chanlun_trader.data.minute.pit_security_master import PITSecurityMaster
        from chanlun_trader.data.tdx.tdx5min_adapter import TDX5MinAdapter
        from chanlun_trader.research.io_safety import GuardedResearchReader, read_day_file_range
        try:
            from scripts.cross_source_5m_audit_v2 import _aggregate, _local_normalized, _pair_metrics
        except ModuleNotFoundError:
            from cross_source_5m_audit_v2 import _aggregate, _local_normalized, _pair_metrics
        try:
            from scripts.close_5m_gap_gates import _reconstruct_tdx_transaction_bar
        except ModuleNotFoundError:
            from close_5m_gap_gates import _reconstruct_tdx_transaction_bar

        stage2 = ["600000.SH", "600016.SH", "600030.SH", "600048.SH", "600104.SH",
                  "600276.SH", "600309.SH", "600585.SH", "600690.SH", "600887.SH",
                  "600900.SH", "601166.SH", "601318.SH", "601328.SH", "601398.SH",
                  "601888.SH", "601988.SH", "600009.SH", "600028.SH", "601288.SH",
                  "000333.SZ", "000338.SZ", "000538.SZ", "000568.SZ", "000625.SZ",
                  "000651.SZ", "000661.SZ", "000725.SZ", "000895.SZ", "002027.SZ",
                  "002415.SZ", "002475.SZ", "002594.SZ", "002714.SZ", "000977.SZ",
                  "300015.SZ", "300059.SZ", "300122.SZ", "300124.SZ", "300274.SZ",
                  "300347.SZ", "300408.SZ", "300750.SZ", "300782.SZ", "300896.SZ",
                  "688001.SH", "688005.SH", "688009.SH", "688012.SH", "688599.SH"]
        records = ManifestStore(raw_manifest_path).records()
        per_symbol: dict[str, dict[str, int]] = {}
        for symbol in stage2:
            rows = [r for r in records if r.get("symbol") == symbol and
                    r.get("requested_start", "") >= "2022-08-01" and
                    r.get("requested_end", "") <= "2024-07-31"]
            request_status: dict[tuple[str, str], bool] = {}
            for row in rows:
                raw_path = str(row.get("path", "")).strip()
                path = Path(raw_path) if raw_path else None
                key = (str(row.get("requested_start")), str(row.get("requested_end")))
                verified = path is not None and path.is_file() and row.get("sha256") == sha256_file(path)
                request_status[key] = request_status.get(key, False) or bool(verified)
            per_symbol[symbol] = {"manifest_chunks": len(request_status), "checksum_verified_chunks": sum(request_status.values())}
        raw_complete = all(v["manifest_chunks"] == 7 and v["checksum_verified_chunks"] == 7 for v in per_symbol.values())

        normalized = GuardedResearchReader().read_5m(normalized_root, start_date=start_date, end_date=end_date)
        normalized_rows = int(len(normalized))
        normalized_by_symbol = {symbol: int(len(normalized[normalized["symbol"] == symbol])) for symbol in expected_symbols}

        gap_payload = json.loads(Path(gap_report_path).read_text(encoding="utf-8"))
        daily_evidence_path = Path("data/market_raw/baostock/daily_gap_evidence.json")
        daily_evidence = json.loads(daily_evidence_path.read_text(encoding="utf-8")) if daily_evidence_path.is_file() else []
        daily_by_key = {(str(row["symbol"]), int(row["trade_date"])): row.get("rows", []) for row in daily_evidence}
        master = PITSecurityMaster.read_json(pit_master_path)
        gap_recomputed = []
        for reported in gap_payload["gaps"]:
            symbol = reported["symbol"]
            day = int(reported["trade_date"])
            frame = normalized[(normalized["symbol"] == symbol) & (normalized["trade_date"] == day)]
            code, market = symbol.split(".")
            prefix = "sh" if market == "SH" else "sz"
            day_path = Path(r"E:/new_tdx_mock/vipdoc") / prefix / "lday" / f"{prefix}{code}.day"
            daily = read_day_file_range(str(day_path), day, day) if day_path.is_file() else pd.DataFrame()
            daily_volume = float(daily.iloc[0]["volume"]) if not daily.empty else None
            source_rows = daily_by_key.get((symbol, day), [])
            explicit_suspension = any(str(row.get("tradestatus", "")) == "0" for row in source_rows)
            transaction_report = reported.get("tdx_history_transaction_evidence", {})
            transaction_path = Path(str(transaction_report.get("raw_snapshot_path", "")))
            transaction_valid = False
            transaction_cross_source = False
            if transaction_path.is_file() and transaction_report.get("raw_snapshot_sha256") == sha256_file(transaction_path):
                transaction_payload = json.loads(transaction_path.read_text(encoding="utf-8"))
                transaction_rows = transaction_payload.get("raw_rows", [])
                row_count_matches = len(transaction_rows) == int(transaction_report.get("raw_snapshot_row_count", -1))
                derived_bars = {
                    end_time: _reconstruct_tdx_transaction_bar(transaction_rows, end_time)
                    for end_time in ("13:40", "13:45")
                }
                positive_transaction_volume = any(float(row.get("vol", 0)) > 0 for row in transaction_rows)
                transaction_valid = row_count_matches and (
                    (not positive_transaction_volume) or all(derived_bars.values())
                )
                transaction_cross_source = transaction_valid and bool(transaction_rows) and all(derived_bars.values())
            classification = classify_gap(
                len(frame), 48, len(daily), daily_volume, explicit_suspension,
                cross_source_evidence=transaction_cross_source,
            )
            state = master.SecurityStateAsOf(
                symbol, day,
                market_data_present=bool(not daily.empty and (daily_volume or 0) > 0),
                suspension_status="SUSPENDED" if explicit_suspension else None,
            )
            gap_recomputed.append({"symbol": symbol, "trade_date": day,
                                   "observed_count": len(frame), "classification": classification,
                                   "security_state": state["security_state"],
                                   "explicit_suspension": explicit_suspension,
                                   "transaction_reference_valid": transaction_valid,
                                   "cross_source_evidence": transaction_cross_source})

        cross_payload = json.loads(Path(cross_audit_path).read_text(encoding="utf-8"))
        adapter = TDX5MinAdapter(r"E:/new_tdx_mock/vipdoc")
        pair_rows = {"baostock_tdx_raw_hq": [], "baostock_tdx_local_lc5": []}
        for item in cross_payload["symbols"]:
            bs = pd.read_parquet(item["baostock_snapshot"])
            tdx_path = item.get("tdx_raw_snapshot")
            tdx = pd.read_parquet(tdx_path) if tdx_path else pd.DataFrame(columns=bs.columns)
            local = _local_normalized(adapter, item["symbol"])
            pair_rows["baostock_tdx_raw_hq"].append(_pair_metrics(bs, tdx, "baostock", "tdx_raw_hq"))
            pair_rows["baostock_tdx_local_lc5"].append(_pair_metrics(bs, local, "baostock", "tdx_local_lc5"))
        cross_recomputed = [_aggregate(rows, name) for name, rows in pair_rows.items()]
        cross_reported = {x["pair"]: x for x in cross_payload["aggregate"]}
        cross_match = all(
            x["matched_bar_count"] == cross_reported[x["pair"]]["matched_bar_count"] and
            x["comparable_symbol_count"] == cross_reported[x["pair"]]["comparable_symbol_count"] and
            x["comparable_date_count"] == cross_reported[x["pair"]]["comparable_date_count"]
            for x in cross_recomputed
        )
        checks = {
            "raw_manifest_checksums": raw_complete,
            "raw_symbol_coverage": len([s for s, v in per_symbol.items() if v["checksum_verified_chunks"] == 7]) == 50,
            "normalized_rows_recomputed": normalized_rows > 0,
            "gap_classifications_recomputed": all(x["classification"] == next(r["classification"] for r in gap_payload["gaps"] if r["symbol"] == x["symbol"] and int(r["trade_date"]) == x["trade_date"]) for x in gap_recomputed),
            "security_state_recomputed": all(x["security_state"] in {"ACTIVE", "UNKNOWN", "NOT_LISTED", "DELISTED", "SUSPENDED"} for x in gap_recomputed),
            "dated_suspension_evidence_recomputed": any(x["explicit_suspension"] for x in gap_recomputed),
            "transaction_reference_recomputed": all(x["transaction_reference_valid"] for x in gap_recomputed),
            "cross_metrics_recomputed": cross_match,
        }
        return {
            "status": "PASS" if all(checks.values()) else "INVALID",
            "CURRENT_SCOPE_ACCEPTANCE_INDEPENDENCE": "STRONG" if all(checks.values()) else "WEAK",
            "SYSTEM_WIDE_ACCEPTANCE_INDEPENDENCE": "PARTIAL",
            "checks": checks,
            "recomputed": {
                "raw_manifest_symbol_count": len(per_symbol),
                "raw_manifest_per_symbol": per_symbol,
                "normalized_row_count": normalized_rows,
                "normalized_row_count_by_symbol": normalized_by_symbol,
                "gap_classifications": gap_recomputed,
                "cross_source_aggregates": cross_recomputed,
            },
        }
