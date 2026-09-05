"""Close the remaining TRAIN 5m gap and PIT gates without starting Stage 3."""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from chanlun_trader.data.minute.base import date_chunks, source_symbol
from chanlun_trader.data.minute.baostock_provider import BaoStock5MinProvider
from chanlun_trader.data.minute.gap_resolution import build_gap_evidence
from chanlun_trader.data.minute.manifest import ManifestStore, sha256_file
from chanlun_trader.data.minute.normalizer import normalize_5m_frame
from chanlun_trader.data.minute.pit_security_master import PITSecurityMaster, SecurityState, make_record
from chanlun_trader.data.minute.tdx_raw_hq_provider import TdxRawHQProvider
from chanlun_trader.data.minute.universe import STAGE1_SYMBOLS, stage2_manifest, stage2_symbols
from chanlun_trader.data.tdx.tdx5min_adapter import TDX5MinAdapter
from chanlun_trader.research.io_safety import GuardedResearchReader, read_day_file_range


VIPDOC = Path(r"E:/new_tdx_mock/vipdoc")
TRAIN_START = date(2022, 8, 1)
TRAIN_END = date(2024, 7, 31)
STAGE2_START = 2022_08_01
STAGE2_END = 2024_07_31
GAPS = (("000895.SZ", 20240606), ("600900.SH", 20221026))
PIT_PATH = Path("data/research/pit_security_master.json")
GAP_JSON = Path("reports/STAGE2_DATA_GAP_RESOLUTION.json")
GAP_MD = Path("reports/STAGE2_DATA_GAP_RESOLUTION.md")
PIT_JSON = Path("reports/PIT_SECURITY_MASTER_AUDIT.json")
PIT_MD = Path("reports/PIT_SECURITY_MASTER_AUDIT.md")
DAILY_EVIDENCE = Path("data/market_raw/baostock/daily_gap_evidence.json")
RAW_MANIFEST = Path("data/market_raw/baostock/5m/manifest.jsonl")
TDX_TRANSACTION_ROOT = Path("data/market_raw/tdx_history_transaction")
TDX_TRANSACTION_PAGE_SIZE = 1800
TDX_TRANSACTION_MAX_PAGES = 20


def _all_symbols() -> list[str]:
    return list(STAGE1_SYMBOLS) + stage2_symbols()


def _build_pit_master() -> tuple[PITSecurityMaster, dict[str, Any]]:
    import baostock as bs

    login = bs.login()
    if str(login.error_code) != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    try:
        result = bs.query_stock_basic()
        if str(result.error_code) != "0":
            raise RuntimeError(f"BaoStock stock_basic failed: {result.error_code} {result.error_msg}")
        rows: list[dict[str, Any]] = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data())))
    finally:
        bs.logout()
    by_code = {str(row.get("code", "")).lower(): row for row in rows}
    board_by_symbol = {x["symbol"]: x["board"] for x in stage2_manifest()}
    master = PITSecurityMaster()
    missing = []
    for symbol in _all_symbols():
        raw = source_symbol(symbol)
        basic = by_code.get(raw)
        if basic is None:
            missing.append(symbol)
            basic = {"code": raw}
        master.add(make_record(symbol, basic, board_by_symbol.get(symbol)))
    master.write_json(PIT_PATH)
    audit = {
        "report": "PIT_SECURITY_MASTER_AUDIT",
        "record_count": len(master.to_dicts()),
        "requested_symbol_count": len(_all_symbols()),
        "missing_basic_records": missing,
        "source": "baostock.query_stock_basic",
        "source_fields_used": ["code", "ipoDate", "outDate"],
        "current_snapshot_projected_to_history": False,
        "scope": "fixed Stage1 + Stage2 acquisition universe only; not a complete market master",
        "statuses": {
            "PIT_LISTING_STATUS": "PASS" if not missing and all(r["list_date"] for r in master.to_dicts()) else "PARTIAL",
            "PIT_DELIST_STATUS": "PASS" if not missing and all("delist_date" in r for r in master.to_dicts()) else "PARTIAL",
            "PIT_ST_STATUS": "UNKNOWN",
            "PIT_SUSPENSION_STATUS": "UNKNOWN",
        },
        "records": master.to_dicts(),
        "state_contract": "SecurityStateAsOf(symbol,date) returns NOT_LISTED/ACTIVE/SUSPENDED/DELISTED/UNKNOWN; missing minute data never implies SUSPENDED",
    }
    PIT_JSON.parent.mkdir(parents=True, exist_ok=True)
    PIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# PIT Security Master Audit",
        "",
        f"- Fixed universe: `{audit['record_count']}/{audit['requested_symbol_count']}` symbols (Stage1 + Stage2 only).",
        "- Lifecycle source: BaoStock `query_stock_basic` `ipoDate/outDate`; the current snapshot status is not projected into 2022-2024.",
        "- `valid_from/list_date` and `valid_to/delist_date` are stored per symbol; ST and suspension remain separate fields.",
        f"- Status: listing `{audit['statuses']['PIT_LISTING_STATUS']}`, delist `{audit['statuses']['PIT_DELIST_STATUS']}`, ST `{audit['statuses']['PIT_ST_STATUS']}`, suspension `{audit['statuses']['PIT_SUSPENSION_STATUS']}`.",
        "",
        "## Gap-date state examples",
        "",
    ]
    for symbol, day in GAPS:
        row = master.SecurityStateAsOf(symbol, day)
        lines.append(f"- `{symbol}` `{day}`: `{row['security_state']}` before market-data observation; suspension status `{row['pit_suspension_status']}`.")
    lines += ["", f"- Machine-readable master: `{PIT_PATH}`.", ""]
    PIT_MD.write_text("\n".join(lines), encoding="utf-8")
    return master, audit


def _daily(symbol: str, trade_date: int) -> pd.DataFrame:
    code, market = symbol.split(".")
    prefix = "sh" if market == "SH" else "sz"
    path = VIPDOC / prefix / "lday" / f"{prefix}{code}.day"
    if not path.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    return read_day_file_range(str(path), trade_date, trade_date)


def _baostock_daily(provider: BaoStock5MinProvider, symbol: str, trade_date: int) -> list[dict[str, Any]]:
    day = date(trade_date // 10000, (trade_date // 100) % 100, trade_date % 100)
    result = provider.bs.query_history_k_data_plus(
        source_symbol(symbol),
        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,tradestatus",
        start_date=day.isoformat(), end_date=day.isoformat(), frequency="d", adjustflag="3",
    )
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock daily query failed: {result.error_code} {result.error_msg}")
    rows: list[dict[str, Any]] = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def _baostock_15m(provider: BaoStock5MinProvider, symbol: str, trade_date: int) -> list[dict[str, Any]]:
    day = date(trade_date // 10000, (trade_date // 100) % 100, trade_date % 100)
    result = provider.bs.query_history_k_data_plus(
        source_symbol(symbol),
        "date,time,code,open,high,low,close,volume,amount,adjustflag",
        start_date=day.isoformat(), end_date=day.isoformat(), frequency="15", adjustflag="3",
    )
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock 15m query failed: {result.error_code} {result.error_msg}")
    rows: list[dict[str, Any]] = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def _baostock_15m_gap_evidence(provider: BaoStock5MinProvider, symbol: str,
                               trade_date: int, five_minute: pd.DataFrame) -> dict[str, Any]:
    rows = _baostock_15m(provider, symbol, trade_date)
    target_time = f"{trade_date // 10000:04d}{trade_date // 100 % 100:02d}{trade_date % 100:02d}134500000"
    target = next((row for row in rows if str(row.get("time")) == target_time), None)
    preceding = five_minute[five_minute["bar_time"] == "13:35"] if not five_minute.empty else pd.DataFrame()
    preceding_row = preceding.iloc[0] if not preceding.empty else None
    preceding_payload = None
    if preceding_row is not None:
        preceding_payload = {
            str(key): (value.isoformat() if isinstance(value, pd.Timestamp) else value.item() if hasattr(value, "item") else value)
            for key, value in preceding_row.to_dict().items()
        }
    if target is None or preceding_row is None:
        return {
            "frequency": "15",
            "adjustflag": "3",
            "rows": len(rows),
            "target_bar_time": "13:45",
            "target_bar": target,
            "preceding_5m_bar": preceding_payload,
            "inferred_missing_interval_activity": False,
        }
    target_volume = int(float(target["volume"]))
    target_amount = float(target["amount"])
    preceding_volume = int(float(preceding_row["volume"]))
    preceding_amount = float(preceding_row["amount"])
    return {
        "frequency": "15",
        "adjustflag": "3",
        "rows": len(rows),
        "target_bar_time": "13:45",
        "target_bar": target,
        "preceding_5m_bar": preceding_payload,
        "inferred_missing_interval_volume": target_volume - preceding_volume,
        "inferred_missing_interval_amount": target_amount - preceding_amount,
        "inferred_missing_interval_activity": target_volume > preceding_volume,
        "interpretation": "15m aggregate confirms activity in the 13:40/13:45 interval; individual 5m OHLCV is not reconstructed",
    }


def _transaction_time(value: Any) -> datetime | None:
    text = str(value).strip()
    if ":" in text:
        text = text[:5]
    else:
        digits = "".join(ch for ch in text if ch.isdigit())
        text = f"{digits[:2]}:{digits[2:4]}" if len(digits) >= 4 else ""
    try:
        return datetime.strptime(text, "%H:%M")
    except ValueError:
        return None


def _reconstruct_tdx_transaction_bar(rows: list[dict[str, Any]], end_time: str) -> dict[str, Any] | None:
    """按 bar-end 口径从逐笔成交重建一个交叉源参考 bar。"""
    end = _transaction_time(end_time)
    if end is None:
        raise ValueError(f"invalid end_time: {end_time}")
    start = end - timedelta(minutes=4)
    selected: list[tuple[datetime, float, float]] = []
    for row in rows:
        stamp = _transaction_time(row.get("time"))
        if stamp is None or stamp < start or stamp > end:
            continue
        try:
            price = float(row["price"])
            volume_lots = float(row["vol"])
        except (KeyError, TypeError, ValueError):
            continue
        selected.append((stamp, price, volume_lots))
    if not selected:
        return None
    selected.sort(key=lambda item: item[0])
    volume_lots = int(round(sum(item[2] for item in selected)))
    return {
        "bar_end": end_time,
        "window_start": start.strftime("%H:%M"),
        "window_end": end.strftime("%H:%M"),
        "timestamp_semantics": "BAR_END",
        "tick_count": len(selected),
        "first_tick_time": selected[0][0].strftime("%H:%M"),
        "last_tick_time": selected[-1][0].strftime("%H:%M"),
        "open": selected[0][1],
        "high": max(item[1] for item in selected),
        "low": min(item[1] for item in selected),
        "close": selected[-1][1],
        "volume_lots": volume_lots,
        "volume_shares": volume_lots * 100,
        "amount_estimate": sum(item[1] * item[2] * 100 for item in selected),
    }


def _tdx_history_transaction_evidence(symbol: str, trade_date: int,
                                      servers: tuple[tuple[str, str, int], ...]) -> dict[str, Any]:
    from pytdx.hq import TdxHq_API

    market, code = TdxRawHQProvider._market(symbol)
    attempts: list[dict[str, Any]] = []
    partial: dict[str, Any] | None = None
    for name, host, port in servers:
        api = TdxHq_API()
        attempt: dict[str, Any] = {"server": name, "host": host, "port": port}
        try:
            if not api.connect(host, port, time_out=3.0):
                attempt["status"] = "CONNECT_FAILED"
                attempts.append(attempt)
                continue
            rows: list[dict[str, Any]] = []
            pages: list[dict[str, int]] = []
            for page in range(TDX_TRANSACTION_MAX_PAGES):
                start = page * TDX_TRANSACTION_PAGE_SIZE
                batch = api.get_history_transaction_data(
                    market, code, start, TDX_TRANSACTION_PAGE_SIZE, trade_date,
                ) or []
                batch_rows = [dict(row) for row in batch]
                pages.append({"start": start, "count": len(batch_rows)})
                if not batch_rows:
                    break
                rows.extend(batch_rows)
                if len(batch_rows) < TDX_TRANSACTION_PAGE_SIZE:
                    break
            attempt["status"] = "FOUND" if rows else "NO_DATA"
            attempt["rows"] = len(rows)
            attempts.append(attempt)
            if not rows:
                continue
            bars = {
                end_time: _reconstruct_tdx_transaction_bar(rows, end_time)
                for end_time in ("13:40", "13:45")
            }
            parsed_times = sorted(
                stamp for stamp in (_transaction_time(row.get("time")) for row in rows)
                if stamp is not None
            )
            result = {
                "status": "FOUND" if all(bars.values()) else "PARTIAL",
                "source": "tdx-history-transaction",
                "endpoint": "pytdx.hq.TdxHq_API.get_history_transaction_data",
                "market": market,
                "code": code,
                "trade_date": trade_date,
                "server": {"name": name, "host": host, "port": port},
                "query_pages": pages,
                "raw_row_count": len(rows),
                "raw_volume_lots": int(round(sum(float(row.get("vol", 0)) for row in rows))),
                "raw_volume_shares": int(round(sum(float(row.get("vol", 0)) for row in rows))) * 100,
                "first_time": parsed_times[0].strftime("%H:%M") if parsed_times else None,
                "last_time": parsed_times[-1].strftime("%H:%M") if parsed_times else None,
                "derived_bars": bars,
                "attempts": attempts,
                "_raw_rows": rows,
            }
            if result["status"] == "FOUND":
                return result
            partial = result
        except Exception as exc:
            attempt["status"] = "REQUEST_FAILED"
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            attempts.append(attempt)
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
    if partial is not None:
        partial["attempts"] = attempts
        return partial
    return {
        "status": "NO_DATA" if attempts and all(a.get("status") == "NO_DATA" for a in attempts) else "FAILED",
        "source": "tdx-history-transaction",
        "endpoint": "pytdx.hq.TdxHq_API.get_history_transaction_data",
        "market": market,
        "code": code,
        "trade_date": trade_date,
        "server": None,
        "query_pages": [],
        "raw_row_count": 0,
        "raw_volume_lots": 0,
        "raw_volume_shares": 0,
        "first_time": None,
        "last_time": None,
        "derived_bars": {"13:40": None, "13:45": None},
        "attempts": attempts,
        "_raw_rows": [],
    }


def _persist_tdx_history_transaction_snapshot(symbol: str, trade_date: int,
                                              evidence: dict[str, Any]) -> dict[str, Any]:
    rows = list(evidence.get("_raw_rows", []))
    report = {key: value for key, value in evidence.items() if key != "_raw_rows"}
    payload = dict(report)
    payload["raw_rows"] = rows
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    checksum = hashlib.sha256(encoded).hexdigest()
    safe = symbol.replace(".", "_")
    candidate = TDX_TRANSACTION_ROOT / f"symbol={safe}" / f"date={trade_date}.json"
    target = candidate
    status = "WRITTEN"
    if candidate.exists():
        if sha256_file(candidate) == checksum:
            status = "UNCHANGED"
        else:
            target = candidate.with_name(f"date={trade_date}.conflict-{checksum[:12]}.json")
            if target.exists() and sha256_file(target) == checksum:
                status = "UNCHANGED_CONFLICT"
            else:
                target.write_bytes(encoded)
                status = "DATA_CONFLICT"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
    report.update({
        "raw_snapshot_path": str(target),
        "raw_snapshot_sha256": checksum,
        "raw_snapshot_row_count": len(rows),
        "raw_snapshot_status": status,
    })
    return report


def _raw_gap_chunk(symbol: str, trade_date: int) -> dict[str, Any]:
    day = f"{trade_date // 10000:04d}-{trade_date // 100 % 100:02d}-{trade_date % 100:02d}"
    candidates = [
        row for row in ManifestStore(str(RAW_MANIFEST)).records()
        if row.get("symbol") == symbol
        and row.get("status") == "COMPLETE"
        and str(row.get("requested_start", "")) <= day <= str(row.get("requested_end", ""))
    ]
    if not candidates:
        return {"status": "NOT_FOUND", "trade_date": day}
    record = candidates[-1]
    raw_path = Path(str(record.get("raw_path") or record.get("path") or ""))
    checksum_verified = raw_path.is_file() and record.get("sha256") == sha256_file(raw_path)
    frame = pd.read_parquet(raw_path) if raw_path.is_file() else pd.DataFrame()
    target = frame[frame["date"].astype(str) == day] if not frame.empty else frame
    return {
        "status": "CHECKSUM_VERIFIED" if checksum_verified else "CHECKSUM_FAILED",
        "manifest_status": record.get("status"),
        "raw_path": str(raw_path),
        "manifest_row_count": record.get("row_count"),
        "manifest_sha256": record.get("sha256"),
        "checksum_verified": bool(checksum_verified),
        "target_date": day,
        "target_row_count": int(len(target)),
        "target_bar_times": sorted(str(value) for value in target.get("time", [])),
    }


def _stage2_manifest_check() -> dict[str, Any]:
    records = ManifestStore("data/market_raw/baostock/5m/manifest.jsonl").records()
    expected_chunks = len(list(date_chunks(TRAIN_START, TRAIN_END, 120)))
    per_symbol = {}
    for symbol in stage2_symbols():
        rows = [r for r in records if r.get("symbol") == symbol and r.get("requested_start") >= "2022-08-01" and r.get("requested_end") <= "2024-07-31"]
        request_status: dict[tuple[str, str], bool] = {}
        for row in rows:
            raw_path = str(row.get("path", "")).strip()
            path = Path(raw_path) if raw_path else None
            key = (str(row.get("requested_start")), str(row.get("requested_end")))
            verified = path is not None and path.is_file() and row.get("sha256") == sha256_file(path)
            request_status[key] = request_status.get(key, False) or bool(verified)
        per_symbol[symbol] = {"manifest_chunks": len(request_status), "checksum_verified_chunks": sum(request_status.values()), "expected_chunks": expected_chunks}
    return {
        "expected_symbol_count": len(stage2_symbols()),
        "complete_symbol_count": sum(v["checksum_verified_chunks"] == expected_chunks for v in per_symbol.values()),
        "expected_chunks_per_symbol": expected_chunks,
        "per_symbol": per_symbol,
    }


def _gap_records(master: PITSecurityMaster) -> list[dict[str, Any]]:
    reader = GuardedResearchReader()
    adapter = TDX5MinAdapter(VIPDOC)
    raw_provider = TdxRawHQProvider(initial_start=15000, max_pages=200)
    bs_provider = BaoStock5MinProvider()
    records: list[dict[str, Any]] = []
    daily_evidence: list[dict[str, Any]] = []
    with bs_provider.session():
        for symbol, trade_date in GAPS:
            normalized_all = reader.read_5m("data/research/market_5m", start_date=trade_date, end_date=trade_date)
            normalized = normalized_all[normalized_all["symbol"] == symbol].copy()
            start_failures = len(raw_provider.failures)
            gap_day = date(trade_date // 10000, (trade_date // 100) % 100, trade_date % 100)
            bs = normalize_5m_frame(bs_provider.fetch(symbol, gap_day, gap_day), symbol, bs_provider.source, bs_provider.timestamp_semantics)
            baostock_repeat_counts = [len(bs)]
            for _ in range(2):
                repeat = normalize_5m_frame(bs_provider.fetch(symbol, gap_day, gap_day), symbol, bs_provider.source, bs_provider.timestamp_semantics)
                baostock_repeat_counts.append(len(repeat))
            try:
                tdx = normalize_5m_frame(raw_provider.fetch(symbol, gap_day, gap_day), symbol, raw_provider.source, raw_provider.timestamp_semantics)
            except Exception:
                tdx = pd.DataFrame()
            local = adapter.read_range(symbol, trade_date, trade_date)
            daily = _daily(symbol, trade_date)
            source_daily_rows = _baostock_daily(bs_provider, symbol, trade_date)
            daily_evidence.append({"symbol": symbol, "trade_date": trade_date, "rows": source_daily_rows,
                                   "source": "baostock.query_history_k_data_plus", "adjustment": "NONE"})
            explicit_suspension = any(str(row.get("tradestatus", "")) == "0" for row in source_daily_rows)
            volume = float(daily.iloc[0]["volume"]) if not daily.empty else None
            transaction = _tdx_history_transaction_evidence(symbol, trade_date, raw_provider.servers)
            source_volume = None
            if source_daily_rows:
                try:
                    source_volume = float(source_daily_rows[0].get("volume"))
                except (TypeError, ValueError):
                    source_volume = None
            transaction["daily_volume_comparison"] = {
                "baostock_daily_volume": source_volume,
                "tdx_transaction_volume_shares": transaction.get("raw_volume_shares"),
                "delta_shares": transaction.get("raw_volume_shares", 0) - source_volume if source_volume is not None else None,
            }
            transaction = _persist_tdx_history_transaction_snapshot(symbol, trade_date, transaction)
            cross_source_evidence = (
                transaction.get("status") == "FOUND"
                and all(transaction.get("derived_bars", {}).get(key) for key in ("13:40", "13:45"))
            )
            state = master.SecurityStateAsOf(
                symbol, trade_date,
                market_data_present=bool(not daily.empty and (volume or 0) > 0),
                suspension_status=SecurityState.SUSPENDED.value if explicit_suspension else None,
            )
            evidence = build_gap_evidence(
                symbol, trade_date, normalized, bs, tdx, local, daily,
                security_state=state["security_state"],
                suspension_state=SecurityState.SUSPENDED.value if explicit_suspension else "UNKNOWN",
                explicit_suspension=explicit_suspension,
                cross_source_evidence=cross_source_evidence,
            ).to_dict()
            evidence["tdx_raw_failures"] = raw_provider.failures[start_failures:]
            evidence["local_lc5_earliest_date"] = adapter.coverage(symbol).earliest_date if adapter.coverage(symbol) else None
            evidence["security_state_detail"] = state
            evidence["evidence"] = list(evidence["evidence"]) + [
                f"BaoStock direct fetch rows={len(bs)}",
                f"TDX Raw HQ fetch rows={len(tdx)}",
                "local TDX .lc5 is outside its true coverage window" if evidence["tdx_local_lc5_rows"] == 0 else "local TDX .lc5 has rows",
                "local TDX daily has positive volume" if not daily.empty and (volume or 0) > 0 else "local TDX daily has no row",
                f"BaoStock daily tradestatus={source_daily_rows[0].get('tradestatus')}" if source_daily_rows else "BaoStock daily has no row",
            ]
            evidence["baostock_daily_rows"] = len(source_daily_rows)
            evidence["baostock_daily_tradestatus"] = source_daily_rows[0].get("tradestatus") if source_daily_rows else None
            evidence["baostock_query_contract"] = {"frequency": "5", "adjustflag": "3", "role": "repeatability_only"}
            evidence["baostock_direct_repeat_counts"] = baostock_repeat_counts
            evidence["baostock_direct_repeat_consistent"] = len(set(baostock_repeat_counts)) == 1
            evidence["baostock_15m_gap_evidence"] = _baostock_15m_gap_evidence(bs_provider, symbol, trade_date, bs)
            evidence["baostock_raw_manifest"] = _raw_gap_chunk(symbol, trade_date)
            evidence["tdx_history_transaction_evidence"] = transaction
            raw_evidence = evidence["baostock_raw_manifest"]
            fifteen_evidence = evidence["baostock_15m_gap_evidence"]
            evidence["evidence"] = list(evidence["evidence"]) + [
                f"BaoStock direct repeat counts={baostock_repeat_counts} under frequency=5/adjustflag=3",
                f"BaoStock 15m target activity={fifteen_evidence.get('inferred_missing_interval_activity')} with inferred missing-interval volume={fifteen_evidence.get('inferred_missing_interval_volume')}",
                f"raw manifest target rows={raw_evidence.get('target_row_count')} and checksum_verified={raw_evidence.get('checksum_verified')}",
                f"TDX history transaction status={transaction.get('status')} rows={transaction.get('raw_row_count')} volume_shares={transaction.get('raw_volume_shares')}",
                f"TDX transaction-derived bars 13:40/13:45 present={cross_source_evidence}; daily volume delta={transaction.get('daily_volume_comparison', {}).get('delta_shares')}",
                "TDX transaction bars are cross-source reference evidence; BaoStock remains canonical and is not overwritten",
            ]
            records.append(evidence)
    DAILY_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    DAILY_EVIDENCE.write_text(json.dumps(daily_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> int:
    master, pit_audit = _build_pit_master()
    stage2 = _stage2_manifest_check()
    gaps = _gap_records(master)
    if any(row["suspension_state"] == SecurityState.SUSPENDED.value for row in gaps):
        pit_audit["statuses"]["PIT_SUSPENSION_STATUS"] = "PARTIAL"
        PIT_JSON.write_text(json.dumps(pit_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pit_text = PIT_MD.read_text(encoding="utf-8")
        PIT_MD.write_text(pit_text.replace("suspension `UNKNOWN`", "suspension `PARTIAL`"), encoding="utf-8")
    counts: dict[str, int] = {}
    for row in gaps:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    payload = {
        "report": "STAGE2_DATA_GAP_RESOLUTION",
        "scope": {"start": "2022-08-01", "end": "2024-07-31", "expected_session_bars": 48},
        "stage2_acquisition": stage2,
        "gaps": gaps,
        "classification_counts": counts,
        "STAGE2_UNKNOWN_GAP_COUNT": counts.get("UNKNOWN", 0),
        "STAGE2_CROSS_SOURCE_CONFLICT_COUNT": counts.get("CROSS_SOURCE_CONFLICT", 0),
        "STAGE2_DATA_GAP_STATUS": "RESOLVED" if not counts.get("UNKNOWN") and not counts.get("PROVIDER_DATA_GAP") else "PARTIAL",
        "TRAIN_5M_STAGE2_STATUS": "PASS" if stage2["complete_symbol_count"] == stage2["expected_symbol_count"] and not counts.get("UNKNOWN") and not counts.get("PROVIDER_DATA_GAP") else "PARTIAL",
        "rule": "BaoStock no data is never sufficient to infer suspension; timestamped independent TDX transaction reconstruction classifies a missing interval as CROSS_SOURCE_CONFLICT; otherwise unresolved evidence remains UNKNOWN",
    }
    GAP_JSON.parent.mkdir(parents=True, exist_ok=True)
    GAP_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Stage2 5m Data Gap Resolution",
        "",
        f"- Stage2 acquisition: `{stage2['complete_symbol_count']}/{stage2['expected_symbol_count']}` symbols have all `{stage2['expected_chunks_per_symbol']}` chunks checksum-verified.",
        f"- Gap status: `{payload['STAGE2_DATA_GAP_STATUS']}`; `STAGE2_UNKNOWN_GAP_COUNT={payload['STAGE2_UNKNOWN_GAP_COUNT']}`; TRAIN Stage2 `{payload['TRAIN_5M_STAGE2_STATUS']}`.",
        "- Classification rule: no-data from BaoStock alone is not suspension evidence; timestamped independent TDX transaction reconstruction may classify a missing interval as `CROSS_SOURCE_CONFLICT`.",
        "",
        "| symbol | date | expected/actual | missing bar times | BaoStock | TDX Raw | local .lc5 | security state | suspension state | classification |",
        "|---|---|---:|---|---:|---:|---:|---|---|---|",
    ]
    for row in gaps:
        lines.append(f"| {row['symbol']} | {row['trade_date']} | {row['expected_count']}/{row['observed_count']} | {', '.join(row['missing_bar_times']) or '—'} | {row['baostock_rows']} | {row['tdx_raw_rows']} | {row['tdx_local_lc5_rows']} | {row['security_state']} | {row['suspension_state']} | **{row['classification']}** |")
        lines.append("")
        lines.append(f"  - Evidence: {'; '.join(row['evidence'])}")
    lines += ["", f"- Machine-readable evidence: `{GAP_JSON}`.", ""]
    GAP_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": payload["STAGE2_DATA_GAP_STATUS"], "unknown": payload["STAGE2_UNKNOWN_GAP_COUNT"], "provider_gap": counts.get("PROVIDER_DATA_GAP", 0), "cross_source_conflict": counts.get("CROSS_SOURCE_CONFLICT", 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
