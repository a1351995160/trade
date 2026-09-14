"""纯策略判断：不读文件、不访问行情服务、不改账户、不授予权限。"""
from decimal import Decimal
import numpy as np
from .etf_grid_spec_v1 import SPEC


def grid_spacing(atr_price,close):
    if not np.isfinite(atr_price) or not np.isfinite(close) or atr_price<0 or close<=0:
        raise ValueError('ATR_PRICE_DIMENSION_OR_INPUT_INVALID')
    return max(atr_price/close,SPEC.minimum_spacing)


def state(close,ma20,ma60,ma120):
    if not all(np.isfinite(x) for x in (close,ma20,ma60,ma120)):
        return {'state':'UNKNOWN','buy_slots':0,'sell_enabled':True}
    if close<ma120:return {'state':'C','buy_slots':0,'sell_enabled':True}
    return {'state':'A' if ma20>ma60 else 'B',
            'buy_slots':SPEC.bull_slots if ma20>ma60 else SPEC.range_slots,'sell_enabled':True}


def adjacent_grid(p0,d,n):
    p0,d=Decimal(str(p0)),Decimal(str(d))
    if p0<=0 or not 0<d<1 or n not in range(1,SPEC.bull_slots+1):raise ValueError('INVALID_GRID')
    buy=p0*(1-n*d);sell=buy+p0*d
    if buy<=0:raise ValueError('NONPOSITIVE_GRID')
    return {'buy':float(buy),'sell':float(sell),'gross_for_200':float(SPEC.grid_quantity*(sell-buy))}


class ETFGridStrategy:
    """核心/网格规则插件；只生成分桶订单，资金与lot由原账户管理。"""
    def __init__(self,benchmark=False):
        from pathlib import Path
        from copy import deepcopy
        from .etf_grid_account_v1 import CONTRACT
        from .strategy_interface_v1 import Requirements
        self.benchmark=bool(benchmark)
        self.strategy_id='ETF_BUY_HOLD' if benchmark else 'ETF_CORE_GRID'
        self.parameters={**deepcopy(CONTRACT),'benchmark_mode':self.benchmark}
        self.source_files=(str(Path(__file__).with_name('etf_grid_spec_v1.py')),
                           str(Path(__file__).with_name('etf_grid_account_v1.py')))
        self.requirements=Requirements('ETF','1D','RAW',('date','open','high','low','close','volume_shares','amount_cny'),
            120,('QUANTITY_ORDER',),capabilities=('SEGREGATED_BUCKETS','SELL_LOT','CASH_DIVIDEND'))

    def on_close(self,context):
        from copy import deepcopy
        from .etf_grid_account_v1 import indicators,ETFFeesV1
        from .strategy_interface_v1 import BucketDecision,BucketOrder,QuantityOrder
        st=deepcopy(context.state);row=indicators(context.history).iloc[-1];day=int(row.date)
        buckets=context.account['buckets'];commands=[];records=[]
        def order(bucket,side,qty,key,cap=None,metadata=None,lot_id=None):
            commands.append(BucketOrder(bucket,QuantityOrder(side,qty,lot_id),key,cap,metadata))
        if not st.get('core_attempted'):
            name='hold' if self.benchmark else 'core';cash=buckets[name]['initial_cash']
            price=round(float(row.close)*(1+SPEC.slippage_per_side),4);qty=int(cash/price/100)*100
            while qty and qty*price+ETFFeesV1().calc('BUY',qty,price).total_fee>cash:qty-=100
            if qty:order(name,'BUY',qty,'initial_core',cash)
            st['core_attempted']=True
        def done():return BucketDecision(tuple(commands),st,tuple(records))
        if self.benchmark:return done()
        if not all(np.isfinite(row[k]) for k in ('atr','rsi','ma20','ma60','ma120')):
            records.append({'date':day,'reason':'INDICATOR_UNAVAILABLE'});return done()
        grid=buckets['grid'];lots=[lot for lot in grid['lots'].values() if lot['remaining_quantity']]
        p0=st.get('p0');used=set(st.get('used',[]));level_keys=st.get('level_keys',{})
        if p0 is None or (not lots and used and abs(row.close/p0-1)<=SPEC.reset_band and row.ma20>row.ma60):
            p0=float(row.close);st.update(p0=p0,spacing=grid_spacing(float(row.atr),p0),cycle=st.get('cycle',0)+1)
            used=set();level_keys={}
        d=st['spacing'];st.update(used=sorted(used),level_keys=level_keys)
        for n,key in sorted(level_keys.items(),key=lambda x:int(x[0])):
            n=int(n);oid=context.account['order_keys'].get(key)
            for fill in grid['fills']:
                if fill['order_id']!=oid:continue
                lot=grid['lots'][fill['lot_allocations'][0]['lot_id']]
                if lot['remaining_quantity'] and row.close>=adjacent_grid(p0,d,n)['sell']:
                    order('grid','SELL',lot['remaining_quantity'],f'sell:{day}:{n}',lot_id=lot['lot_id'])
                    records.append({'date':day,'reason':'SELL_ADJACENT','level':n});return done()
        regime=state(row.close,row.ma20,row.ma60,row.ma120);reason='NO_LEVEL_TRIGGER'
        if regime['buy_slots']==0:reason='MA120_BUY_PAUSE'
        elif row.rsi>SPEC.rsi_buy_pause:reason='RSI_BUY_PAUSE'
        elif row.close>SPEC.max_price or row.amount_cny<SPEC.min_daily_amount:reason='SCREEN_REJECT'
        else:
            for n in range(1,regime['buy_slots']+1):
                level=adjacent_grid(p0,d,n)
                if n in used or row.close>level['buy']:continue
                if SPEC.grid_quantity*level['buy']*d<20:reason='GRID_ECONOMICS_REJECT';break
                if len(lots)>=regime['buy_slots']:reason='GRID_SLOT_CAP';break
                used.add(n);key=f'buy:{st["cycle"]}:{n}';level_keys[str(n)]=key
                order('grid','BUY',SPEC.grid_quantity,key,regime['buy_slots']*1000,{'grid':True})
                reason='BUY_LEVEL_TRIGGER';break
        st.update(used=sorted(used),level_keys=level_keys)
        records.append({'date':day,'reason':reason,'p0':p0,'spacing':d,'state':regime['state']})
        return done()

    @property
    def implementation_options(self):return {'benchmark':self.benchmark}

    @property
    def account_budgets(self):return {'hold':9000} if self.benchmark else {'core':5000,'grid':4000}

    def validate(self):
        from .etf_grid_account_v1 import CONTRACT
        if self.parameters!={**CONTRACT,'benchmark_mode':self.benchmark}:
            raise ValueError('FIXED_GRID_PARAMETERS_CHANGED')
