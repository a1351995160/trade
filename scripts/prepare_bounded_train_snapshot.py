"""V2许可的训练输入诊断；不计算策略、成交、绩效或补造历史时点。"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from chanlun_trader.research.io_safety import _DAY_STRUCT


START, END = 20220801, 20240731
FIELDS = ["date", "open", "high", "low", "close", "amount_encoded", "volume_encoded"]


def day_window(path, start, end):
    """只seek日期键以定位，再读取允许区间；全文hash永不计算。"""
    if not START <= start <= end <= END:
        raise ValueError("TRAIN_WINDOW_REQUIRED")
    path = Path(path)
    before = path.stat()
    if before.st_size % _DAY_STRUCT.size:
        raise ValueError("TDX_RECORD_SIZE_CONFLICT")
    n = before.st_size // _DAY_STRUCT.size
    date_reads = []
    with path.open("rb") as handle:
        def date_at(i):
            handle.seek(i * 32)
            data = handle.read(4)
            if len(data) != 4:
                raise ValueError("TRUNCATED_DATE_KEY")
            date_reads.append(i * 32)
            return struct.unpack("<I", data)[0]

        def lower_bound(target):
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if date_at(mid) < target:
                    lo = mid + 1
                else:
                    hi = mid
            return lo

        left, right = lower_bound(start), lower_bound(end + 1)
        # 首先逐一读允许段的日期键；遇异常不读取该段价格。
        dates = [date_at(i) for i in range(left, right)]
        if dates != sorted(set(dates)) or any(not start <= d <= end for d in dates):
            raise ValueError("TDX_WINDOW_DATE_INDEX_CONFLICT")
        handle.seek(left * 32)
        data = handle.read((right - left) * 32)
    if len(data) != (right - left) * 32:
        raise ValueError("TRUNCATED_ALLOWED_RECORDS")
    rows = []
    for encoded in _DAY_STRUCT.iter_unpack(data):
        rows.append([encoded[0], *(x / 100.0 for x in encoded[1:5]), encoded[5], encoded[6]])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("SOURCE_CHANGED_DURING_READ")
    return pd.DataFrame(rows, columns=FIELDS), {
        "path": str(path), "record_range_half_open": [left, right],
        "price_byte_range_half_open": [left * 32, right * 32], "price_bytes_read": len(data),
        "date_key_offsets": sorted(set(date_reads)), "date_key_bytes_read": len(date_reads) * 4,
        "window_bytes_sha256": hashlib.sha256(data).hexdigest(), "full_file_hash": None,
        "rows": len(rows), "date_index_assumption": "TDX_CHRONOLOGICAL_FIXED_RECORD_FORMAT",
    }


def prepare(input_root: Path, tdx_root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    calendar_path = input_root / "data/research/security_state/raw/trade_calendar.json"
    calendar = json.loads(calendar_path.read_bytes())
    dates = [int(str(d).replace("-", "")) for d in calendar["trade_dates"]]
    if dates != sorted(set(dates)):
        raise ValueError("CALENDAR_NOT_ORDERED_UNIQUE")
    sessions = [d for d in dates if START <= d <= END]
    if not sessions or sessions[0] != START or sessions[-1] != END:
        raise ValueError("CALENDAR_TRAIN_ENDPOINTS_MISSING")
    pit_root = input_root / "data/research/security_state/normalized/pit_universe_v2"
    pit_files = [pit_root / f"trade_date={str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}.jsonl" for d in sessions]
    inventory = []
    for exchange in ("sh", "sz"):
        for path in sorted((tdx_root / exchange / "lday").glob("*.day")):
            if path.resolve() != path:
                raise ValueError("SOURCE_REDIRECTED")
            stat = path.stat()
            inventory.append({"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    manifest = {"purpose": "TRAIN_INPUT_DIAGNOSTIC_ONLY", "window": [START, END],
        "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "format_source_sha256": hashlib.sha256(Path(__import__('chanlun_trader.research.io_safety', fromlist=['__file__']).__file__).read_bytes()).hexdigest(),
        "fields": FIELDS, "pit_files": [str(p) for p in pit_files], "tdx_inventory": inventory,
        "calendar_file_sha256": hashlib.sha256(calendar_path.read_bytes()).hexdigest(),
        "warmup_sessions_available": dates[max(0, dates.index(START)-6):dates.index(START)],
        "all_source_records_authorized": False}
    (output / "SOURCE_READ_PLAN.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    membership, status, available_times, lineages = defaultdict(set), Counter(), Counter(), Counter()
    pit_audit, missing, references = [], [], set()
    for day, path in zip(sessions, pit_files):
        if not path.is_file():
            missing.append(str(path))
            continue
        if path.resolve() != path:
            raise ValueError("PIT_PATH_REDIRECTED")
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as handle:
            for line in handle:
                digest.update(line)
                row = json.loads(line)
                if int(str(row["trade_date"]).replace("-", "")) != day:
                    raise ValueError("PIT_PARTITION_DATE_CONFLICT")
                count += 1
                if row.get("exchange") not in {"SH", "SZ"}:
                    continue
                status[(row.get("st_status"), row.get("suspension_status"), row.get("eligibility_status"))] += 1
                stamp = row.get("available_at")
                available_times["MISSING" if not stamp else "AFTER_TRADE_DATE" if str(stamp)[:10] > str(row["trade_date"]) else "DECLARED_BY_TRADE_DATE"] += 1
                for key, value in row.get("source_lineage", {}).items():
                    lineages[key] += 1
                    if isinstance(value, str) and ("/" in value or "\\" in value):
                        references.add(value)
                if row.get("universe_member") is True:
                    membership[row["symbol"]].add(day)
        pit_audit.append({"path": str(path), "sha256": digest.hexdigest(), "rows": count, "trade_date": day})
    path_index = {Path(f["path"]).stem: f for f in inventory}
    selected, missing_sources = [], []
    for symbol in sorted(membership):
        code, exchange = symbol.split(".")
        key = exchange.lower() + code
        if key not in path_index:
            missing_sources.append(symbol)
        else:
            selected.append((symbol, path_index[key]))
    (output / "SELECTED_SOURCE_PLAN.json").write_text(json.dumps({"selected": selected,
        "membership_basis": "EVER_UNIVERSE_MEMBER_IN_TRAIN; not performance or normal-state filtered",
        "missing_symbols": missing_sources}, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_total, invalid = 0, Counter()
    audits = []
    writer = None
    try:
        for symbol, source in selected:
            path = Path(source["path"])
            if (path.stat().st_size, path.stat().st_mtime_ns) != (source["size"], source["mtime_ns"]):
                raise ValueError("SOURCE_CHANGED_AFTER_PLAN")
            frame, audit = day_window(path, START, END)
            audits.append(audit)
            if frame.empty:
                missing_sources.append(symbol)
                continue
            frame["symbol"] = symbol
            frame["pit_member"] = frame.date.isin(membership[symbol])
            frame["available_at"] = None
            frame["historical_availability_evidence"] = "UNKNOWN"
            frame["corporate_action_evidence"] = "UNKNOWN"
            frame["price_decode_rule"] = "TDX_UINT32_CENTS_V1"
            invalid["nonpositive_ohlc_rows"] += int((frame[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
            invalid["ohlc_envelope_rows"] += int(((frame.high < frame[["open", "close", "low"]].max(axis=1)) | (frame.low > frame[["open", "close", "high"]].min(axis=1))).sum())
            invalid["outside_calendar_rows"] += int((~frame.date.isin(sessions)).sum())
            rows_total += len(frame)
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output / "TRAIN_INPUT_SNAPSHOT_DIAGNOSTIC.parquet", table.schema)
            writer.write_table(table)
    finally:
        if writer:
            writer.close()
    (output / "PHYSICAL_READ_AUDIT.json").write_text(json.dumps({"pit_partitions": pit_audit, "tdx_reads": audits}, ensure_ascii=False), encoding="utf-8")
    report = {"schema_version": "train-source-evidence-v2", "ready_for_real_trial": False,
        "train_sessions": len(sessions), "warmup_sessions": manifest["warmup_sessions_available"],
        "pit_partitions_read": len(pit_audit), "missing_pit_files": missing, "pit_member_symbols": len(membership),
        "tdx_files_in_inventory": len(inventory), "tdx_files_selected": len(selected), "rows_written": rows_total,
        "missing_tdx_symbols": missing_sources, "invalid_rows_preserved": dict(invalid),
        "pit_status_counts": [{"states": list(k), "count": v} for k,v in status.items()],
        "pit_time_declarations": dict(available_times), "source_lineage_fields": dict(lineages),
        "source_references_not_followed": sorted(references), "real_performance_trials": 0,
        "tdx_acquisition_lineage": "UNKNOWN_NO_SIDECAR_IN_AUTHORIZED_LDAY_DIRECTORIES",
        "snapshot_role": "DIAGNOSTIC_INPUT_NOT_EXECUTABLE_EVIDENCE"}
    (output / "DATA_PROVENANCE_AND_TIME_EVIDENCE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    report = prepare(Path("E:/llmwiki/chanlun-trading-system"), Path("E:/new_tdx_mock/vipdoc"),
        Path("E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2/train-source-v1"))
    print(json.dumps(report, ensure_ascii=False))
