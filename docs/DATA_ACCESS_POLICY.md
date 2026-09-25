# DATA ACCESS POLICY

- RESEARCH_END = 2025-07-31; FINAL_TEST_START = 2025-08-01. Research code may not materialize rows with date >= 2025-08-01.
- Parquet research reads must use `GuardedResearchReader.read_parquet` (PyArrow predicate pushdown).
- ISO `YYYY-MM-DD` 字符串日期的 Parquet 须显式传 `date_format="iso"`，并以整数 `YYYYMMDD` 指定允许区间；本次 S1 历史状态只读取至 2024-07-31。
- TDX .day/.lc5 research reads must use `read_day_file_range` / `read_lc5_file_range`; whole-file readers are legacy and not research-safe.
- Coverage/metadata scans may read only the first record date, never the file tail, when the tail is future data.
- 安全读取默认追加到 `data/research/audit/physical_read_audit.jsonl`；显式设置 `audit_sink` 的诊断运行把同样的物理读取记录保存在证据报告的 `read_audit` 中。
- Any accidental physical future-data read must be appended to `data/research/audit/final_test_access_incidents.jsonl` within the same session.
