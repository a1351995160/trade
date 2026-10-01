"""冻结传输保留全列类型及缺值，不用整表 Arrow 展开长来源字段。"""
import pandas as pd
import pyarrow as pa
import pytest
from decimal import Decimal

from chanlun_trader.research_factory import universe_submission_v1 as submission
from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1


@pytest.mark.parametrize('empty', [False, True])
def test_frozen_io_preserves_types_and_late_non_null_values_in_bounded_batches(tmp_path, monkeypatch, empty):
    count = 0 if empty else 8195
    frame = pd.DataFrame({
        'source': pd.Series(['原件路径/' + 'x' * 512] * count, dtype=object),
        'late_source': pd.Series([None] * min(count, 8192) + ['later'] * max(0, count - 8192), dtype=object),
        'date': pd.Series([20240102] * count, dtype='int64'),
        'optional': pd.Series([None] * count, dtype='Int64'),
        'known': pd.Series([True] * count, dtype='boolean'),
        'default_text': pd.Series(['default'] * count),
    })
    original = submission.pq.ParquetWriter
    sizes = []
    class RecordingWriter:
        def __init__(self, *args, **kwargs):
            self.writer = original(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.writer.close()
        def write_table(self, table):
            sizes.append(table.num_rows)
            assert table.num_rows <= 8192
            self.writer.write_table(table)
    monkeypatch.setattr(submission.pq, 'ParquetWriter', RecordingWriter)
    monkeypatch.setattr(pd.DataFrame, 'to_parquet', lambda *a, **k: pytest.fail('不得回到整表写入'))
    path = tmp_path / 'frozen.parquet'
    submission._write_frame_in_batches(frame, path)
    assert sizes == ([0] if empty else [8192, 3])
    monkeypatch.setattr(pd, 'read_parquet', lambda *a, **k: pytest.fail('不得回到整表读取'))
    restored = UniverseDataProviderV1._read_parquet(path, preserve_pandas_objects=True)
    pd.testing.assert_frame_equal(restored, frame)
    if not empty:
        assert restored.late_source.iloc[0] is None
        assert restored.late_source.iloc[-1] == 'later'
        assert restored.source.iloc[0] is restored.source.iloc[-1]


def test_column_schema_matches_original_full_inference_and_metadata(tmp_path):
    frame = pd.DataFrame({
        'source': pd.Series([None, '来源/' + 'x' * 512], dtype=object),
        'nullable': pd.Series([None, 1], dtype='Int64'),
        'boolean': pd.Series([True, None], dtype='boolean'),
        'category': pd.Series(['a', 'b'], dtype='category'),
        'timestamp': pd.date_range('2024-01-02', periods=2, tz='Asia/Shanghai'),
        'nested': pd.Series([None, {'value': 2}], dtype=object),
        'list': pd.Series([None, [2]], dtype=object),
        'decimal': pd.Series([None, Decimal('2.35')], dtype=object),
        'arrow': pd.Series([None, 'later'], dtype='string[pyarrow]'),
    })
    expected = pa.Schema.from_pandas(frame, preserve_index=False)
    actual = submission._schema_in_columns(frame)
    assert actual.equals(expected, check_metadata=True)
    table = pa.Table.from_pandas(frame, schema=actual, preserve_index=False)
    reference = pa.Table.from_pandas(frame, schema=expected, preserve_index=False)
    # Arrow自身重建string的存储后端；与原完整类型推断路径逐项一致。
    pd.testing.assert_frame_equal(table.to_pandas(), reference.to_pandas())
    path = tmp_path / 'typed.parquet'
    submission._write_frame_in_batches(frame, path)
    restored = UniverseDataProviderV1._read_parquet(path, preserve_pandas_objects=True)
    pd.testing.assert_frame_equal(restored.drop(columns='arrow'), frame.drop(columns='arrow'))
    assert restored.arrow.tolist() == frame.arrow.tolist()


def test_schema_inference_does_not_change_process_memory_pool(monkeypatch):
    frame = pd.DataFrame({'source': [None, '来源/' + 'x' * 512]})
    before = pa.default_memory_pool().backend_name
    monkeypatch.setattr(pa, 'set_memory_pool', lambda *a: pytest.fail('不得更换进程默认池'))
    original_type = pa.Schema.from_pandas(frame, preserve_index=False).field('source').type
    assert submission._schema_in_columns(frame).field('source').type == original_type
    assert pa.default_memory_pool().backend_name == before
