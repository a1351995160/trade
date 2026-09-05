# DATA ACCESS POLICY

- RESEARCH_END = 2025-07-31; FINAL_TEST_START = 2025-08-01. Research code may not materialize rows with date >= 2025-08-01.
- Parquet research reads must use `GuardedResearchReader.read_parquet` (PyArrow predicate pushdown).
- TDX .day/.lc5 research reads must use `read_day_file_range` / `read_lc5_file_range`; whole-file readers are legacy and not research-safe.
- Coverage/metadata scans may read only the first record date, never the file tail, when the tail is future data.
- Every safe read appends to `data/research/audit/physical_read_audit.jsonl`.
- Any accidental physical future-data read must be appended to `data/research/audit/final_test_access_incidents.jsonl` within the same session.
