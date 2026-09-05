import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from chanlun_trader.data.minute.performance import (
    FAST_VERIFY,
    FULL_CRYPTO_VERIFY,
    DatasetIdentityError,
    EventWindowCache,
    EventWindowKey,
    IncrementalManifestVerifier,
    IncrementalVerificationError,
    MinuteDatasetIndex,
    ResearchCheckpoint,
    CheckpointIdentityError,
    ResearchMinuteReader,
    SessionSummaryStore,
    sha256_file,
)


def _frame(symbol: str, dates: list[int]) -> pd.DataFrame:
    rows = []
    for date in dates:
        for minute in (35, 40, 45):
            rows.append({
                "symbol": symbol,
                "timestamp": pd.Timestamp(f"{date} 09:{minute}:00", tz="Asia/Shanghai"),
                "trade_date": date,
                "bar_time": f"09:{minute}",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 100.0,
                "amount": 1000.0,
                "source": "test",
            })
    return pd.DataFrame(rows)


def _manifest(tmp_path: Path) -> tuple[Path, Path, str]:
    data_root = tmp_path / "canonical"
    data_root.mkdir()
    path_a = data_root / "part-a.parquet"
    path_b = data_root / "part-b.parquet"
    _frame("600000.SH", [20240102]).to_parquet(path_a, index=False)
    _frame("600000.SH", [20240103]).to_parquet(path_b, index=False)
    manifest = tmp_path / "manifest.jsonl"
    entries = []
    for path in (path_a, path_b):
        entries.append({"path": str(path), "row_count": len(pd.read_parquet(path)), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    return manifest, data_root, hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_index_lookup_is_identity_bound(tmp_path):
    manifest, _, manifest_hash = _manifest(tmp_path)
    index_path = tmp_path / "index.parquet"
    index = MinuteDatasetIndex.build(manifest, index_path, dataset_version="TEST_V1", manifest_hash=manifest_hash)
    found = index.lookup("600000.SH", 20240103, 20240103)
    assert len(found) == 1 and found.iloc[0]["start_date"] == 20240103
    with pytest.raises(DatasetIdentityError):
        MinuteDatasetIndex.open(index_path, dataset_version="OTHER", manifest_hash=manifest_hash)


def test_session_summary_matches_source_rows_and_reader_prunes_dates(tmp_path):
    manifest, _, manifest_hash = _manifest(tmp_path)
    index = MinuteDatasetIndex.build(manifest, tmp_path / "index.parquet", dataset_version="TEST_V1", manifest_hash=manifest_hash)
    summary = SessionSummaryStore.build(index, tmp_path / "sidecars", complete_bar_count=3)
    result = summary.lookup("600000.SH", 20240102, 20240103)
    assert result["bar_count"].tolist() == [3, 3]
    assert set(result["data_status"]) == {"COMPLETE_48"}
    reader = ResearchMinuteReader(index)
    selected = reader.read_symbol_range("600000.SH", 20240103, 20240103)
    assert len(selected) == 3 and set(selected["trade_date"]) == {20240103}


def test_event_window_cache_identity_and_bounded_eviction():
    cache = EventWindowCache(dataset_version="TEST_V1", manifest_hash="hash", max_items=1, max_bytes=100000)
    first = pd.DataFrame({"value": [1]})
    second = pd.DataFrame({"value": [2]})
    key_a = EventWindowKey("TEST_V1", "hash", "600000.SH", 20240102, "pre=1;post=1")
    key_b = EventWindowKey("TEST_V1", "hash", "600000.SH", 20240103, "pre=1;post=1")
    assert cache.get_or_load(key_a, lambda: first).iloc[0, 0] == 1
    assert cache.get_or_load(key_a, lambda: second).iloc[0, 0] == 1
    cache.put(key_b, second)
    assert cache.stats()["evictions"] == 1
    with pytest.raises(DatasetIdentityError):
        cache.get(EventWindowKey("OTHER", "hash", "600000.SH", 20240102, "pre=1;post=1"))


def test_incremental_verifier_full_then_fast_and_invalidates_on_change(tmp_path):
    manifest, _, manifest_hash = _manifest(tmp_path)
    cache_path = tmp_path / "verify-cache.json"
    verifier = IncrementalManifestVerifier(manifest, cache_path, dataset_version="TEST_V1", manifest_hash=manifest_hash)
    full = verifier.verify(FULL_CRYPTO_VERIFY)
    fast = verifier.verify(FAST_VERIFY)
    assert full["ok"] and full["hashes_computed"] == 3
    assert fast["ok"] and fast["hashes_computed"] == 0 and fast["hashes_skipped"] == 3
    target = Path(json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])["path"])
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(IncrementalVerificationError):
        verifier.verify(FAST_VERIFY)


def test_fast_verify_requires_full_cache(tmp_path):
    manifest, _, manifest_hash = _manifest(tmp_path)
    verifier = IncrementalManifestVerifier(manifest, tmp_path / "missing.json", dataset_version="TEST_V1", manifest_hash=manifest_hash)
    with pytest.raises(IncrementalVerificationError):
        verifier.verify(FAST_VERIFY)


def test_incremental_verifier_tracks_additional_manifest_identity(tmp_path):
    manifest, _, manifest_hash = _manifest(tmp_path)
    raw_manifest = tmp_path / "raw-manifest.jsonl"
    raw_manifest.write_text("{\"source\":\"test\"}\n", encoding="utf-8")
    raw_hash = sha256_file(raw_manifest)
    verifier = IncrementalManifestVerifier(
        manifest,
        tmp_path / "verify-cache.json",
        dataset_version="TEST_V1",
        manifest_hash=manifest_hash,
        additional_manifest_hashes={raw_manifest: raw_hash},
    )
    full = verifier.verify(FULL_CRYPTO_VERIFY)
    fast = verifier.verify(FAST_VERIFY)
    assert full["hashes_computed"] == 4
    assert fast["hashes_skipped"] == 4
    raw_manifest.write_text("{\"source\":\"changed\"}\n", encoding="utf-8")
    with pytest.raises(IncrementalVerificationError):
        verifier.verify(FAST_VERIFY)


def test_research_checkpoint_is_atomic_and_identity_bound(tmp_path):
    path = tmp_path / "checkpoint.json"
    checkpoint = ResearchCheckpoint(
        path,
        dataset_version="TEST_V1",
        code_snapshot_hash="code",
        config_hash="config",
        strategy_hash="strategy",
    )
    checkpoint.reset()
    checkpoint.mark_completed("E_CONSEC_LIMIT", artifact="derived.parquet")
    assert checkpoint.is_completed("E_CONSEC_LIMIT")
    assert not list(tmp_path.glob("*.tmp"))
    incompatible = ResearchCheckpoint(
        path,
        dataset_version="TEST_V1",
        code_snapshot_hash="other-code",
        config_hash="config",
        strategy_hash="strategy",
    )
    with pytest.raises(CheckpointIdentityError):
        incompatible.load()
