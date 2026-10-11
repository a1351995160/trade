"""原生列哈希分块不改变任何既有冻结身份；测试只使用合成数据。"""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory import universe_account_inputs_v1 as inputs_module
from chanlun_trader.research_factory.common import canonical_json
from test_universe_account_inputs_v1 import _identity_frame, _legacy_frame_identity


@pytest.mark.parametrize('block_rows', [1, 2, 3, 5, 65536])
@pytest.mark.parametrize('case', ['plain_ints', 'ints_none', 'plain_bool', 'bool_none',
    'nested', 'sets', 'mixed', 'strings', 'dates', 'floats', 'nullable_int',
    'string_dtype', 'arrow_string', 'arrow_int', 'category_keys',
    'unordered_category_keys', 'empty'])
def test_every_block_boundary_preserves_legacy_sha_and_values(monkeypatch, case, block_rows):
    frame = _identity_frame(case)
    if len(frame):
        frame = frame.iloc[[2, 0, 3, 1]]
    original = frame.copy(deep=True)
    contents = canonical_json(frame.to_dict('records'))
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    monkeypatch.setattr(inputs_module, '_IDENTITY_HASH_ROWS', block_rows)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected
    pd.testing.assert_frame_equal(frame, original, check_exact=True)
    assert canonical_json(frame.to_dict('records')) == contents


@pytest.mark.parametrize('block_rows', [1, 2, 4, 7, 65536])
def test_mixed_object_dtype_is_inferred_for_the_whole_original_column(monkeypatch, block_rows):
    # 独立对每块 map 会把前半段 int/None 转为 float；全列 object 必须保持原值类型。
    values = [1, 2, None, 3., '1', False, {'a': [1, None]}, (2, 3),
              np.nan, pd.NA, {3, 2}, frozenset([2, 3])]
    frame = pd.DataFrame({'symbol': ['000001.SZ'] * len(values),
        'date': np.arange(len(values), dtype=np.int64),
        'mixed': pd.Series(deepcopy(values), dtype=object),
        'inferred_float': pd.Series([1, None, 2] * 4, dtype=object),
        'inferred_bool': pd.Series([True, False] * 6, dtype=object),
        'dates': pd.Series([pd.Timestamp('2022-01-01'), pd.NaT] * 6, dtype=object)})
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    monkeypatch.setattr(inputs_module, '_IDENTITY_HASH_ROWS', block_rows)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected
    reversed_columns = frame[frame.columns[::-1]]
    assert inputs_module._frame_identity(reversed_columns, ['symbol', 'date']) == \
        _legacy_frame_identity(reversed_columns, ['symbol', 'date'])
    assert inputs_module._frame_identity(reversed_columns, ['symbol', 'date']) != expected


def test_native_hash_input_is_bounded_across_multiple_real_default_blocks(monkeypatch):
    count = 65536 * 2 + 7
    frame = pd.DataFrame({'symbol': np.full(count, '000001.SZ', dtype=object),
        'date': np.arange(count, dtype=np.int64),
        'text': np.resize(np.asarray(['a', None, np.nan, pd.NA], dtype=object), count),
        'nullable': pd.Series(np.arange(count), dtype='Int64'),
        'category': pd.Categorical(np.resize(['a', 'b', 'a'], count),
                                   categories=['unused', 'b', 'a'], ordered=True)})
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    sizes, original_hash = [], pd.util.hash_pandas_object

    def bounded_hash(series, *args, **kwargs):
        assert isinstance(series, pd.Series)
        sizes.append(len(series))
        assert len(series) <= 65536
        return original_hash(series, *args, **kwargs)

    monkeypatch.setattr(pd.util, 'hash_pandas_object', bounded_hash)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected
    assert sizes == [65536, 65536, 7] * len(frame.columns)


def test_float_nan_payloads_and_timezone_columns_keep_exact_legacy_identity(monkeypatch):
    values = np.asarray([0x7ff8000000000001, 0x7ff8000000000002,
                         0x0000000000000000, 0x8000000000000000], dtype=np.uint64).view(np.float64)
    frame = pd.DataFrame({'symbol': ['000001.SZ'] * 4, 'date': [1, 2, 3, 4],
        'float_bits': values,
        'time': pd.to_datetime(['2022-01-01', None, '2022-01-03', '2022-01-04'], utc=True),
        'duration': pd.to_timedelta([1, None, 3, 4], unit='D')})
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    monkeypatch.setattr(inputs_module, '_IDENTITY_HASH_ROWS', 3)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected


@pytest.mark.parametrize('case', ['int8', 'int64', 'uint64', 'bool', 'float32', 'date32', 'timestamp', 'decimal'])
@pytest.mark.parametrize('block_rows', [1, 2, 3, 65536])
def test_arrow_missing_value_representation_is_global_not_per_block(monkeypatch, case, block_rows):
    import datetime
    from decimal import Decimal
    import pyarrow as pa
    arrow_type, values = {
        'int8': (pa.int8(), [1, None, 2, 1]),
        'int64': (pa.int64(), [1, None, 2, 1]),
        'uint64': (pa.uint64(), [2 ** 63 + 1, None, 2 ** 63 + 3, 1]),
        'bool': (pa.bool_(), [True, None, False, True]),
        'float32': (pa.float32(), [1.5, None, 2.5, 1.]),
        'date32': (pa.date32(), [datetime.date(2022, 1, 1), None, datetime.date(2022, 1, 3), datetime.date(2022, 1, 1)]),
        'timestamp': (pa.timestamp('ns', tz='UTC'), [pd.Timestamp('2022-01-01', tz='UTC'), None,
                      pd.Timestamp('2022-01-03', tz='UTC'), pd.Timestamp('2022-01-01', tz='UTC')]),
        'decimal': (pa.decimal128(8, 2), [Decimal('1.50'), None, Decimal('2.50'), Decimal('1.00')]),
    }[case]
    frame = pd.DataFrame({'symbol': ['000001.SZ'] * 4, 'date': [1, 2, 3, 4],
                          'arrow': pd.Series(values, dtype=pd.ArrowDtype(arrow_type))})
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    monkeypatch.setattr(inputs_module, '_IDENTITY_HASH_ROWS', block_rows)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected


@pytest.mark.parametrize('block_rows', [1, 2, 3, 65536])
def test_equal_mixed_object_values_keep_global_first_representative(monkeypatch, block_rows):
    values = [1, True, 1., 'text', False, 0, 0., None]
    frame = pd.DataFrame({'symbol': ['000001.SZ'] * len(values),
        'date': np.arange(len(values)), 'mixed': pd.Series(values, dtype=object)})
    expected = _legacy_frame_identity(frame, ['symbol', 'date'])
    monkeypatch.setattr(inputs_module, '_IDENTITY_HASH_ROWS', block_rows)
    assert inputs_module._frame_identity(frame, ['symbol', 'date']) == expected
