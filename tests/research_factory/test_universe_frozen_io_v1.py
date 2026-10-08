"""冻结传输保留全列类型及缺值，不用整表 Arrow 展开长来源字段。"""
from datetime import date, datetime, timezone, timedelta
import numpy as np
import pyarrow.parquet as pq

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


@pytest.mark.parametrize('values,dtype', [
    ([None, '来源/' + 'x'*512], object),
    ([None, b'\x00\xff'], object),
    (['a', b'\xff'], object),
    ([None, None], object),
    ([None, pd.NaT, 'a'], object),
    ([np.nan, pd.NA, 'a'], object),
    ([1, 2.5], object),
    ([None, Decimal('1.1'), Decimal('100.00')], object),
    ([{'z':1}, {'a':2}], object),
    ([[1], [2.5]], object),
    ([date(2020,1,1), datetime(2020,1,2)], object),
    ([datetime(2020,1,1,tzinfo=timezone.utc), datetime(2020,1,1,tzinfo=timezone(timedelta(hours=8)))], object),
    (['a', 'b'], 'category'),
    ([None, 2], 'Int32'),
    ([True, None], 'boolean'),
    ([1.5, 2.5], 'float32'),
    (['a', None], 'string[python]'),
    (['a', None], 'string[pyarrow]'),
    ([1, None], pd.ArrowDtype(pa.int32())),
])
def test_all_value_schema_matches_original(values, dtype, tmp_path):
    series=pd.Series(values,dtype=dtype)
    frame=pd.DataFrame({'value':series})
    expected=pa.Schema.from_pandas(frame,preserve_index=False)
    actual=submission._schema_in_columns(frame)
    assert actual.equals(expected,check_metadata=True)
    path=tmp_path/'typed.parquet'
    submission._write_frame_in_batches(frame,path)
    reference=pa.Table.from_pandas(frame,schema=expected,preserve_index=False)
    reference_path=tmp_path/'original_path.parquet'
    pq.write_table(reference,reference_path)
    persisted=pq.read_table(path)
    original_persisted=pq.read_table(reference_path)
    assert persisted.schema.equals(original_persisted.schema,check_metadata=True)
    assert persisted.equals(original_persisted)


def test_pure_text_schema_never_materializes_full_strings(tmp_path,monkeypatch):
    count=16389
    frame=pd.DataFrame({
        'source':pd.Series(['原始完整资料/'+'x'*512]*count,dtype=object),
        'late_source':pd.Series([None]*16384+['late']*5,dtype=object),
        'bytes_source':pd.Series([None]+[b'\x00\xff']*(count-1),dtype=object),
        'date':pd.Series([20240102]*count,dtype='int64'),
    })
    expected=pa.Schema.from_pandas(frame,preserve_index=False)
    original_array=submission.pa.array
    calls=[]
    def limited_array(values,*args,**kwargs):
        if isinstance(values,pd.Series) and values.dtype==object:
            assert len(values)<=8192,'不得为纯文本类型推断复制整列 payload'
        calls.append((len(values),kwargs.get('memory_pool')))
        return original_array(values,*args,**kwargs)
    monkeypatch.setattr(submission.pa,'array',limited_array)
    before=pa.default_memory_pool().backend_name
    monkeypatch.setattr(submission.pa,'set_memory_pool',lambda *a:pytest.fail('不得更换全局池'))
    assert submission._schema_in_columns(frame).equals(expected,check_metadata=True)
    path=tmp_path/'complete.parquet'
    submission._write_frame_in_batches(frame,path)
    assert all(pool is not None and pool.backend_name=='system' for count,pool in calls if count)
    persisted=pq.read_table(path)
    assert persisted.num_rows==count
    assert persisted['late_source'].to_pylist()==frame['late_source'].tolist()
    assert persisted['source'].to_pylist()==frame['source'].tolist()
    assert persisted['bytes_source'].to_pylist()==frame['bytes_source'].tolist()
    assert pa.default_memory_pool().backend_name==before


def test_late_mixed_bytes_retains_binary_not_infer_type_string(tmp_path):
    frame=pd.DataFrame({'source':pd.Series(['x']*8192+[b'\xff'],dtype=object)})
    expected=pa.Schema.from_pandas(frame,preserve_index=False)
    assert expected.field('source').type==pa.binary()
    assert submission._schema_in_columns(frame).equals(expected,check_metadata=True)
    path=tmp_path/'binary.parquet'
    submission._write_frame_in_batches(frame,path)
    assert pq.read_table(path)['source'][-1].as_py()==b'\xff'


@pytest.mark.parametrize('arrow_type', [pa.string(), pa.large_string()])
def test_repeated_native_text_preserves_empty_unicode_null_and_frozen_identity(tmp_path, arrow_type):
    from chanlun_trader.research_factory.universe_account_inputs_v1 import _frame_identity
    values = (['', '中文来源/🙂/' + 'x' * 512, None, 'None'] * 4097) + ['最后一批']
    dates = np.arange(len(values), dtype=np.int64)
    table = pa.table({'symbol': pa.array(['000001.SZ'] * len(values), type=arrow_type),
        'date': pa.array(dates), 'source': pa.array(values, type=arrow_type)})
    path = tmp_path / 'repeated_native.parquet'
    pq.write_table(table, path)
    expected = pd.DataFrame({'symbol': pd.Series(['000001.SZ'] * len(values), dtype=object),
        'date': dates, 'source': pd.Series(values, dtype=object)})
    actual = UniverseDataProviderV1._read_parquet(path, preserve_pandas_objects=True)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert _frame_identity(actual, ['symbol', 'date']) == _frame_identity(expected, ['symbol', 'date'])
    assert actual.source.iloc[2] is None and actual.source.iloc[8194] is None
    assert actual.source.iloc[1] is actual.source.iloc[8193]


@pytest.mark.parametrize('values', [['text']*8192+[1], [True]*8192+[2]])
def test_late_invalid_mixed_values_keep_strict_rejection(values,tmp_path):
    frame=pd.DataFrame({'source':pd.Series(values,dtype=object)})
    with pytest.raises((pa.ArrowInvalid,pa.ArrowTypeError)):
        pa.Schema.from_pandas(frame,preserve_index=False)
    with pytest.raises((pa.ArrowInvalid,pa.ArrowTypeError)):
        submission._write_frame_in_batches(frame,tmp_path/'invalid.parquet')


def test_invalid_unicode_still_rejected_before_successful_freeze(tmp_path):
    frame=pd.DataFrame({'source':pd.Series(['text']*8192+['\ud800'],dtype=object)})
    with pytest.raises(UnicodeEncodeError):
        pa.Table.from_pandas(frame,preserve_index=False)
    with pytest.raises(UnicodeEncodeError):
        submission._write_frame_in_batches(frame,tmp_path/'invalid_unicode.parquet')
