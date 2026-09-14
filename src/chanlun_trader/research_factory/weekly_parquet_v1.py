"""按证券增量落盘；固定Arrow类型避免各分块分类字典和空值类型冲突。"""
from contextlib import ExitStack
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


SCHEMAS={
    'DAILY.parquet':pa.schema([('symbol',pa.string()),('date',pa.int64()),
        *[(k,pa.float64()) for k in ['open','high','low','close','volume','amount','prev_close']],('adjustflag',pa.string())]),
    'STATES.parquet':pa.schema([('symbol',pa.string()),('trade_date',pa.int64()),
        *[(k,pa.bool_()) for k in ['listed','delisted','universe_member']],
        *[(k,pa.string()) for k in ['eligibility_status','st_status','suspension_status','board']]]),
    'COMPUTABILITY.parquet':pa.schema([('symbol',pa.string()),('timestamp',pa.int64()),('computable',pa.bool_())]),
}


class InputChunks:
    def __init__(self,root):
        self.root=root
        self.stack=ExitStack()
        self.writers={}
        self.rows={name:0 for name in SCHEMAS}

    def __enter__(self):
        try:
            for name,schema in SCHEMAS.items():
                # x模式防止覆盖未结算的部分输出。
                stream=self.stack.enter_context((self.root/name).open('xb'))
                self.writers[name]=self.stack.enter_context(pq.ParquetWriter(stream,schema))
        except BaseException:
            self.stack.close()
            raise
        return self

    def write(self,name,frame):
        schema=SCHEMAS[name]
        if list(frame.columns)!=schema.names:frame=frame[schema.names]
        table=pa.Table.from_pandas(frame,schema=schema,preserve_index=False,safe=True)
        self.writers[name].write_table(table,row_group_size=4096)
        self.rows[name]+=len(frame)

    def __exit__(self,*args):
        return self.stack.__exit__(*args)

    def close(self):
        self.stack.close()


def compact_read(path):
    frame=pd.read_parquet(path)
    for col in set(['symbol','signal_version','eligibility_status','st_status','suspension_status','board','adjustflag']).intersection(frame.columns):
        frame[col]=frame[col].astype('category')
    return frame
