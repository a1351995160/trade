import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.weekly_parquet_v1 import InputChunks,compact_read,SCHEMAS


def test_stream_matches_concat_with_different_categories_and_nulls(tmp_path):
    raw=[];states=[];diagnostics=[]
    for i in range(31):
        symbol=f'{i:06d}.SZ'
        raw.append(pd.DataFrame({'symbol':pd.Categorical([symbol,symbol]),'date':[20250102,20250103],
            'open':[10.,np.nan],'high':[11.,np.nan],'low':[9.,np.nan],'close':[10.,np.nan],
            'volume':[100.,np.nan],'amount':[1000.,np.nan],'prev_close':[10.,10.],
            'adjustflag':pd.Categorical(['3','3'])}))
        states.append(pd.DataFrame({'symbol':pd.Categorical([symbol,symbol]),'trade_date':[20250102,20250103],
            'listed':[True,True],'delisted':[False,False],'universe_member':[True,True],
            'eligibility_status':pd.Categorical(['ELIGIBLE','INELIGIBLE']),
            'st_status':pd.Categorical(['NORMAL','UNKNOWN']),
            'suspension_status':pd.Categorical(['TRADING','SUSPENDED']),'board':pd.Categorical(['MAIN','MAIN'])}))
        diagnostics.append(pd.DataFrame({'symbol':[symbol,symbol],'timestamp':[20250102,20250103],'computable':[True,False]}))
    groups=dict(zip(SCHEMAS,[raw,states,diagnostics]))
    with InputChunks(tmp_path) as writer:
        for name,frames in groups.items():
            for frame in frames:writer.write(name,frame)
        assert writer.rows=={k:62 for k in SCHEMAS}
    for name,frames in groups.items():
        actual=compact_read(tmp_path/name)
        expected=pd.concat(frames,ignore_index=True)
        pd.testing.assert_frame_equal(actual,expected,check_dtype=False,check_categorical=False)


def test_empty_streams_keep_schema_and_no_overwrite(tmp_path):
    with InputChunks(tmp_path):pass
    for name,schema in SCHEMAS.items():
        value=pd.read_parquet(tmp_path/name)
        assert list(value.columns)==schema.names and value.empty
    with pytest.raises(FileExistsError):
        with InputChunks(tmp_path):pass
