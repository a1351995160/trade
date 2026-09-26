"""实际到达快照驱动的前瞻模拟观察；合成输入不累计真实观察日。"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

import pandas as pd

from .bounded_research_v1 import _put, _read, source_identity
from .common import stable_hash
from .durability import _atomic_write
from .forward_paper_engine_v1 import ForwardPaperEngineV1
from .mutation_boundary import ObjectiveMutationLock


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    ts = pd.Timestamp(value)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError('FORWARD_PAPER_AWARE_TIME_REQUIRED')
    return ts.tz_convert('Asia/Shanghai')


def _data_check(payload, symbols, *, day=None, warmup=False):
    if not isinstance(payload, dict) or payload.get('corporate_actions_complete') is not True:
        raise ValueError('FORWARD_PAPER_ACTION_COVERAGE_REQUIRED')
    actions = payload.get('corporate_actions')
    if not isinstance(actions, list) or (actions and not warmup):
        raise ValueError('FORWARD_PAPER_CORPORATE_ACTION_UNSUPPORTED')
    bars, turns = payload.get('bars'), payload.get('turn')
    if not isinstance(bars, list) or not bars or not isinstance(turns, list):
        raise ValueError('FORWARD_PAPER_BARS_REQUIRED')
    keys = [(row['symbol'], row['date']) for row in bars]
    if len(keys) != len(set(keys)) or {key[0] for key in keys} != set(symbols):
        raise ValueError('FORWARD_PAPER_BAR_COVERAGE')
    days = sorted({key[1] for key in keys})
    if any(type(value) is not int for value in days) or (day is not None and days != [day]):
        raise ValueError('FORWARD_PAPER_BAR_DATE_CONFLICT')
    if set(keys) != {(symbol, date) for symbol in symbols for date in days}:
        raise ValueError('FORWARD_PAPER_BAR_COVERAGE')
    for row in bars:
        for key in ('open','high','low','close','volume','amount','prev_close'):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
                raise ValueError('FORWARD_PAPER_NONFINITE_BAR')
            if value < 0 or (key in ('open','high','low','close','prev_close') and value == 0):
                raise ValueError('FORWARD_PAPER_INVALID_BAR')
        if row['high'] < max(row['open'],row['close']) or row['low'] > min(row['open'],row['close']):
            raise ValueError('FORWARD_PAPER_OHLC_CONFLICT')
    if warmup or payload.get('_phase') != 'OPEN':
        turn_keys = [(row['symbol'],row['date']) for row in turns]
        if len(turn_keys) != len(set(turn_keys)) or set(turn_keys) != set(keys):
            raise ValueError('FORWARD_PAPER_TURN_COVERAGE')
        for row in turns:
            if (isinstance(row.get('turn'), bool) or not isinstance(row.get('turn'), (int,float))
                    or not math.isfinite(row['turn']) or row['turn'] < 0
                    or row.get('tradestatus') not in (0,1)):
                raise ValueError('FORWARD_PAPER_TURN_INVALID')
    if not warmup:
        states = payload.get('states')
        if not isinstance(states, list) or len(states) != len(symbols) or {x['symbol'] for x in states} != set(symbols):
            raise ValueError('FORWARD_PAPER_STATE_COVERAGE')
        for row in states:
            if (row.get('date') != day or any(type(row.get(key)) is not bool for key in
                    ('listed','delisted','is_st','suspended')) or row.get('board') not in
                    ('MAIN','SH_MAIN','SZ_MAIN')):
                raise ValueError('FORWARD_PAPER_STATE_UNKNOWN')
    return days


class ForwardPaperSessionV1:
    def __init__(self, root, *, clock=None):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root or not self.root.is_dir():
            raise ValueError('FORWARD_PAPER_ROOT_INVALID')
        self.clock = clock
        if clock is not None and self.header()['profile'] != 'SYNTHETIC':
            raise PermissionError('FORWARD_PAPER_REAL_CLOCK_NOT_INJECTABLE')

    def path(self, *parts):
        path = self.root.joinpath(*parts)
        if path.resolve() != path or not path.is_relative_to(self.root):
            raise ValueError('FORWARD_PAPER_PATH_REDIRECTED')
        return path

    def now(self):
        return _stamp(self.clock() if self.clock else _now())

    def lock(self):
        return ObjectiveMutationLock.for_resource(self.path('SESSION.json'))

    @classmethod
    def create(cls, root, *, archive_root, strategy_ids, policy, calendar, warmup,
               purpose='ENGINEERING_OBSERVATION', profile='REAL_OBSERVED', clock=None):
        from .portfolio_execution_v1 import PortfolioExecutionPolicyV1
        from .strategy_qualification_v1 import BoundedStrategyArchiveV1
        if profile not in ('REAL_OBSERVED','SYNTHETIC') or (clock is not None and profile != 'SYNTHETIC'):
            raise ValueError('FORWARD_PAPER_PROFILE_INVALID')
        if purpose not in ('ENGINEERING_OBSERVATION','FORMAL_OBSERVATION'):
            raise ValueError('FORWARD_PAPER_PURPOSE_INVALID')
        if (not isinstance(strategy_ids, (list,tuple)) or not strategy_ids
                or len(set(strategy_ids)) != len(strategy_ids)):
            raise ValueError('FORWARD_PAPER_STRATEGIES_INVALID')
        policy = deepcopy(policy)
        if set(policy) != {'portfolio','initial_cash','symbols','open_delay_minutes','close_delay_minutes'}:
            raise ValueError('FORWARD_PAPER_POLICY_FIELDS_INVALID')
        portfolio = PortfolioExecutionPolicyV1.model_validate_json(json.dumps(policy['portfolio']))
        if portfolio.lot_size != 100:
            raise ValueError('FORWARD_PAPER_MAIN_BOARD_LOT_SIZE_REQUIRED')
        if portfolio.purpose != purpose or {member.strategy_id for member in portfolio.members} != set(strategy_ids):
            raise ValueError('FORWARD_PAPER_PORTFOLIO_SCOPE_CONFLICT')
        symbols = policy['symbols']
        if (not isinstance(symbols,list) or not symbols or len(set(symbols)) != len(symbols)
                or any(not isinstance(s,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ)',s) for s in symbols)):
            raise ValueError('FORWARD_PAPER_SYMBOL_SCOPE_INVALID')
        if (isinstance(policy['initial_cash'],bool) or not isinstance(policy['initial_cash'],(int,float))
                or not math.isfinite(policy['initial_cash']) or policy['initial_cash'] <= 0):
            raise ValueError('FORWARD_PAPER_CASH_INVALID')
        for key, maximum in (('open_delay_minutes',30),('close_delay_minutes',480)):
            if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
                raise ValueError('FORWARD_PAPER_WINDOW_INVALID')
        if (not isinstance(calendar,list) or len(calendar)<2 or calendar != sorted(set(calendar))
                or any(type(day) is not int for day in calendar)):
            raise ValueError('FORWARD_PAPER_CALENDAR_INVALID')
        for day in calendar:
            pd.Timestamp(str(day))
        warm_days = _data_check(warmup,symbols,warmup=True)
        if len(warm_days)<60 or warm_days[-1]>=calendar[0]:
            raise ValueError('FORWARD_PAPER_WARMUP_INVALID')
        if profile == 'REAL_OBSERVED' and warmup.get('source_profile') not in ('HISTORICAL_REAL','REAL_OBSERVED'):
            raise PermissionError('FORWARD_PAPER_REAL_WARMUP_REQUIRED')
        archive = BoundedStrategyArchiveV1(archive_root)
        strategies = {}
        admissions = {}
        for strategy_id in strategy_ids:
            item = archive.load(strategy_id)
            admission = archive.admission(strategy_id,purpose=purpose)
            if not admission['allowed']:
                raise PermissionError('FORWARD_PAPER_STRATEGY_NOT_ADMITTED')
            if profile == 'REAL_OBSERVED' and item['source_profile'] == 'SYNTHETIC':
                raise PermissionError('FORWARD_PAPER_SYNTHETIC_STRATEGY_NOT_REAL')
            member = next(member for member in portfolio.members if member.strategy_id == strategy_id)
            if member.rule_identity != item['rule_identity']:
                raise ValueError('FORWARD_PAPER_RULE_IDENTITY_CONFLICT')
            strategies[strategy_id] = {key: deepcopy(item[key]) for key in
                                      ('proposal','rule_identity','archive_hash','source_profile')}
            admissions[strategy_id] = admission
        created = _stamp(clock() if clock else _now())
        if profile == 'REAL_OBSERVED' and calendar[0] < int(created.strftime('%Y%m%d')):
            raise ValueError('FORWARD_PAPER_NO_HISTORICAL_START')
        if created >= _stamp(portfolio.valid_until):
            raise ValueError('FORWARD_PAPER_POLICY_EXPIRED')
        header = {'schema_version':'FORWARD_PAPER_V1','profile':profile,'purpose':purpose,
            'created_at':created.isoformat(),'archive_root':str(Path(archive_root).absolute()),
            'strategies':strategies,'initial_admissions':admissions,'policy':policy,
            'calendar':calendar,'warmup':deepcopy(warmup),'source_identity':source_identity(),
            'execution':'SIMULATED_OBSERVED_OPEN_WITH_COSTS','real_execution_authorized':False,
            'company_actions':'STOP_ON_ANY_OBSERVATION_ACTION',
            'cost_model':{'commission_rate':0.00025,'min_commission':5.0,'stamp_tax_rate':0.0005,'slippage_bps':0.001}}
        header['header_id'] = stable_hash(header)
        root = Path(root).absolute()
        root.mkdir(parents=True,exist_ok=True)
        if root.resolve()!=root or any(root.iterdir()):
            raise ValueError('FORWARD_PAPER_EMPTY_ROOT_REQUIRED')
        _put(root/'SESSION.json',header)
        _atomic_write(root/'HEAD.json',cls._head(header,0,header['header_id']))
        return cls(root,clock=clock)

    @staticmethod
    def _head(header,count,last,revocation_hash=None):
        value={'header_id':header['header_id'],'count':count,'last_hash':last,'revocation_hash':revocation_hash}
        return {**value,'head_hash':stable_hash(value)}

    def header(self):
        value = _read(self.path('SESSION.json'))
        if value['header_id'] != stable_hash({k:v for k,v in value.items() if k!='header_id'}):
            raise ValueError('FORWARD_PAPER_HEADER_CORRUPT')
        return value

    def _admissions(self,header):
        from .strategy_qualification_v1 import BoundedStrategyArchiveV1
        archive = BoundedStrategyArchiveV1(header['archive_root'])
        result={}
        for key,frozen in header['strategies'].items():
            current=archive.load(key)
            if any(current[field] != frozen[field] for field in ('archive_hash','rule_identity','proposal','source_profile')):
                raise ValueError('FORWARD_PAPER_ARCHIVE_CHANGED')
            result[key]=archive.admission(key,purpose=header['purpose'])
        return result

    def _records(self,header,*,recover=False):
        head_path=self.path('HEAD.json')
        head=json.loads(head_path.read_text(encoding='utf-8'))
        if head.get('head_hash') != stable_hash({k:v for k,v in head.items() if k!='head_hash'}):
            raise ValueError('FORWARD_PAPER_HEAD_CORRUPT')
        paths=sorted(self.path('stages').glob('*.json'))
        if len(paths)!=head['count'] and not (recover and len(paths)==head['count']+1):
            raise ValueError('FORWARD_PAPER_COMMITTED_HISTORY_CONFLICT')
        records=[]
        previous=header['header_id']
        for i,path in enumerate(paths):
            if path.name != f'{i:08d}.json':
                raise ValueError('FORWARD_PAPER_STAGE_GAP')
            item=_read(self.path('stages',path.name))
            if (item['previous_hash']!=previous or item['record_hash']!=stable_hash(
                    {k:v for k,v in item.items() if k!='record_hash'})):
                raise ValueError('FORWARD_PAPER_STAGE_CORRUPT')
            snapshot=item['snapshot']
            body={k:v for k,v in snapshot.items() if k not in ('snapshot_id','snapshot_hash','processed_at')}
            if snapshot['snapshot_hash']!=stable_hash(body) or snapshot['snapshot_id']!='SNAP_'+stable_hash(body):
                raise ValueError('FORWARD_PAPER_SNAPSHOT_IDENTITY_CONFLICT')
            records.append(item)
            previous=item['record_hash']
        committed=records[:head['count']]
        committed_hash=committed[-1]['record_hash'] if committed else header['header_id']
        revocation=_read(self.path('REVOKED.json')) if self.path('REVOKED.json').exists() else None
        if revocation is not None and (revocation.get('header_id')!=header['header_id']
                or not isinstance(revocation.get('reason'),str) or not revocation['reason'].strip()
                or _stamp(revocation['recorded_at'])<_stamp(header['created_at'])):
            raise ValueError('FORWARD_PAPER_REVOCATION_IDENTITY_CONFLICT')
        revoked=stable_hash(revocation) if revocation else None
        if head!=self._head(header,len(committed),committed_hash,revoked):
            if (recover and revocation is not None
                    and head==self._head(header,len(committed),committed_hash,None)):
                # 只能提交一个已落盘、绑定当前会话的新增撤销；绝不恢复缺失的已提交撤销。
                self._recover(header,committed)
                _atomic_write(self.path('HEAD.json'),self._head(header,len(committed),committed_hash,revoked))
            else:
                raise ValueError('FORWARD_PAPER_COMMITTED_HISTORY_CONFLICT')
        if len(records)>len(committed):
            # 一个已完整落盘但HEAD尚未提交的阶段：重放核对后完成同一事务。
            if source_identity()!=header['source_identity']:
                raise PermissionError('FORWARD_PAPER_SOURCE_CHANGED')
            self._recover(header,records)
            _atomic_write(self.path('HEAD.json'),self._head(header,len(records),previous,revoked))
        return records

    def _apply(self,engine,header,snapshot,admissions,previous,*,admission_trace=None,live=False):
        from .portfolio_execution_v1 import build_portfolio_plan
        phase,day=snapshot['phase'],snapshot['market_date']
        plan=None
        trace=[]
        def admission_check(key):
            if live:
                try:
                    answer=self._admissions(header)[key]
                except (ValueError,KeyError,OSError) as exc:
                    # 权威证据读取损坏不是普通的买入风险拒绝，必须中止整个未提交阶段。
                    raise PermissionError('FORWARD_PAPER_ADMISSION_AUTHORITY_UNAVAILABLE') from exc
            else:
                if admission_trace is None or len(trace)>=len(admission_trace):
                    raise ValueError('FORWARD_PAPER_ADMISSION_TRACE_INCOMPLETE')
                item=admission_trace[len(trace)]
                if item['strategy_id']!=key:
                    raise ValueError('FORWARD_PAPER_ADMISSION_TRACE_CONFLICT')
                answer=item['admission']
            trace.append({'strategy_id':key,'admission':deepcopy(answer)})
            return answer
        if phase=='OPEN':
            engine.open(snapshot,previous['plan'],admissions,admission_check=admission_check)
        else:
            decisions=engine.close(snapshot)
            index=header['calendar'].index(day)
            if index+1<len(header['calendar']):
                plan=build_portfolio_plan(policy=engine.portfolio,decisions=decisions,
                    ledger=engine.engine.ledger,admissions=admissions,
                    input_identity=snapshot['snapshot_hash'],decision_at=snapshot['processed_at'],
                    next_session=header['calendar'][index+1])
        if not live and trace!=(admission_trace or []):
            raise ValueError('FORWARD_PAPER_ADMISSION_TRACE_CONFLICT')
        return {'state':engine.state(),'plan':plan,'admission_trace':trace}

    def _recover(self,header,records):
        engine=ForwardPaperEngineV1(header)
        previous=None
        for record in records:
            actual=self._apply(engine,header,record['snapshot'],record['admissions'],previous,
                               admission_trace=record['admission_trace'])
            if actual!={key:record[key] for key in ('state','plan','admission_trace')}:
                raise ValueError('FORWARD_PAPER_PREFIX_DIVERGED')
            previous=record
        return engine

    def ingest(self,snapshot_root,snapshot_id):
        from .forward_snapshot_v1 import SnapshotStoreV1
        with self.lock():
            header=self.header()
            records=self._records(header,recover=True)
            if any(row['snapshot']['snapshot_id']==snapshot_id for row in records):
                return self._status(header,records)
            if self.path('REVOKED.json').exists():
                raise PermissionError('FORWARD_PAPER_REVOKED')
            if source_identity()!=header['source_identity']:
                raise PermissionError('FORWARD_PAPER_SOURCE_CHANGED')
            snapshot=deepcopy(SnapshotStoreV1(snapshot_root).load(snapshot_id))
            if snapshot['profile']!=header['profile']:
                raise PermissionError('FORWARD_PAPER_SNAPSHOT_PROFILE_CONFLICT')
            now=self.now()
            received=_stamp(snapshot['received_at'])
            day=snapshot['market_date']
            phase=snapshot['phase']
            if phase not in ('OPEN','CLOSE') or day not in header['calendar']:
                raise ValueError('FORWARD_PAPER_SNAPSHOT_SCOPE_CONFLICT')
            if received<_stamp(header['created_at']) or now<received:
                raise ValueError('FORWARD_PAPER_SNAPSHOT_NOT_PROSPECTIVE')
            date=pd.Timestamp(str(day),tz='Asia/Shanghai')
            start=date+pd.Timedelta(hours=9,minutes=30) if phase=='OPEN' else date+pd.Timedelta(hours=15)
            end=start+pd.Timedelta(minutes=header['policy']['open_delay_minutes' if phase=='OPEN' else 'close_delay_minutes'])
            if not start<=received<=now<=end:
                raise ValueError('FORWARD_PAPER_LATE_OR_EARLY_SNAPSHOT')
            if now>=_stamp(header['policy']['portfolio']['valid_until']):
                raise PermissionError('FORWARD_PAPER_POLICY_EXPIRED')
            if not records:
                if phase!='CLOSE' or day!=header['calendar'][0]:
                    raise ValueError('FORWARD_PAPER_INITIAL_CLOSE_REQUIRED')
            else:
                prior=records[-1]['snapshot']
                index=header['calendar'].index(prior['market_date'])
                expected=('CLOSE',prior['market_date']) if prior['phase']=='OPEN' else (
                    'OPEN',header['calendar'][index+1] if index+1<len(header['calendar']) else None)
                if (phase,day)!=expected:
                    raise ValueError('FORWARD_PAPER_PHASE_SEQUENCE_OR_REVISION_CONFLICT')
            payload={**snapshot['payload'],'_phase':phase}
            if payload.get('corporate_actions_complete') is not True or payload.get('corporate_actions'):
                revoked={'header_id':header['header_id'],'reason':'UNSUPPORTED_CORPORATE_ACTION_OR_UNKNOWN_COVERAGE',
                         'recorded_at':now.isoformat(),'snapshot_id':snapshot_id}
                _put(self.path('REVOKED.json'),revoked)
                _atomic_write(self.path('HEAD.json'),self._head(header,len(records),
                    records[-1]['record_hash'] if records else header['header_id'],stable_hash(revoked)))
                raise ValueError('FORWARD_PAPER_CORPORATE_ACTION_HALTED')
            _data_check(payload,header['policy']['symbols'],day=day)
            last_bars=(records[-1]['snapshot']['payload']['bars'] if records else header['warmup']['bars'])
            if phase=='OPEN' or not records:
                prior={}
                for row in last_bars:
                    if row['symbol'] not in prior or row['date']>prior[row['symbol']]['date']:
                        prior[row['symbol']]=row
                if any(abs(row['prev_close']-prior[row['symbol']]['close'])>0.011 for row in payload['bars']):
                    raise ValueError('FORWARD_PAPER_UNEXPLAINED_PRICE_REFERENCE_CHANGE')
            else:
                prior={row['symbol']:row for row in last_bars}
                if any(abs(row['prev_close']-prior[row['symbol']]['prev_close'])>0.011 for row in payload['bars']):
                    raise ValueError('FORWARD_PAPER_PRICE_REFERENCE_REVISION')
            engine=self._recover(header,records)
            now=self.now()
            if not start<=received<=now<=end:
                raise ValueError('FORWARD_PAPER_LATE_OR_EARLY_SNAPSHOT')
            if now>=_stamp(header['policy']['portfolio']['valid_until']):
                raise PermissionError('FORWARD_PAPER_POLICY_EXPIRED')
            snapshot['processed_at']=now.isoformat()
            admissions=self._admissions(header)
            applied=self._apply(engine,header,snapshot,admissions,records[-1] if records else None,live=True)
            record={'index':len(records),'snapshot':snapshot,'admissions':admissions,
                    'previous_hash':records[-1]['record_hash'] if records else header['header_id'],**applied}
            record['record_hash']=stable_hash(record)
            _put(self.path('stages',f'{len(records):08d}.json'),record)
            _atomic_write(self.path('HEAD.json'),self._head(header,len(records)+1,record['record_hash']))
            return self._status(header,[*records,record])

    def _status(self,header,records):
        completed=sum(1 for i,row in enumerate(records) if i>0 and row['snapshot']['phase']=='CLOSE'
                      and records[i-1]['snapshot']['phase']=='OPEN')
        real=completed if header['profile']=='REAL_OBSERVED' else 0
        revoked=_read(self.path('REVOKED.json')) if self.path('REVOKED.json').exists() else None
        return {'session_id':header['header_id'],'profile':header['profile'],'purpose':header['purpose'],
            'status':('HALTED' if revoked['reason'].startswith('UNSUPPORTED_CORPORATE_ACTION') else 'REVOKED') if revoked else 'WAITING_DATA',
            'completed_stages':len(records),'completed_simulated_days':completed,
            'real_observation_days':real,
            'engineering_observation_days':real if header['purpose']=='ENGINEERING_OBSERVATION' else 0,
            'qualified_observation_days':real if header['purpose']=='FORMAL_OBSERVATION' else 0,
            'strategy_qualified':False,'real_execution_authorized':False,
            'last_snapshot':records[-1]['snapshot']['snapshot_id'] if records else None,
            'state':records[-1]['state'] if records else None,
            'next_plan':records[-1]['plan'] if records else None,
            'limitations':['模拟成交不代表真实成交；工程观察不授予策略资格。',
                          '观察期间公司行动尚不支持，遇事件或覆盖未知停止。',
                          '首版只支持沪深主板；历史预热来源标签不是历史发布时间证明。']}

    def status(self):
        with self.lock():
            header=self.header()
            records=self._records(header)
            return self._status(header,records)

    def revoke(self,reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError('FORWARD_PAPER_REVOCATION_REASON_REQUIRED')
        with self.lock():
            header=self.header()
            records=self._records(header,recover=True)
            if not self.path('REVOKED.json').exists():
                revoked={'header_id':header['header_id'],'reason':reason,'recorded_at':self.now().isoformat()}
                _put(self.path('REVOKED.json'),revoked)
                _atomic_write(self.path('HEAD.json'),self._head(header,len(records),
                    records[-1]['record_hash'] if records else header['header_id'],stable_hash(revoked)))
        return self.status()
