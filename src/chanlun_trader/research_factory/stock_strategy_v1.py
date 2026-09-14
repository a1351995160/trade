"""多股票策略接入：复用原时点、排序、风险拒绝、账户及逐lot退出。"""
from copy import deepcopy
from pathlib import Path
import math

from .fixed_account_rules import FixedAccountRules
from .strategy_interface_v1 import Requirements
from ..engine.portfolio_exit import PortfolioExitEvaluatorV1


class ConfiguredStockRules(FixedAccountRules):
    """按配置创建新规则，不再通过候选名称白名单选择持有天数。"""
    def __init__(self,contract):
        required={'operator','threshold','ranking','top_n','entry','exit','holding_sessions','signal_version','result_type'}
        if not required<=set(contract):raise ValueError('STOCK_RULE_CONFIG_INCOMPLETE')
        if contract['operator']!='LT' or contract['ranking']!='FACTOR_ASC_SYMBOL_ASC':
            raise ValueError('STOCK_RULE_ORDERING_UNSUPPORTED')
        if contract['entry']!='NEXT_SESSION_OPEN' or contract['exit']!='NEXT_SESSION_OPEN':
            raise ValueError('STOCK_EXECUTION_TIMING_UNSUPPORTED')
        if type(contract['top_n']) is not int or not 1<=contract['top_n']<=3:
            raise ValueError('STOCK_THREE_POSITION_BACKEND_LIMIT')
        if type(contract['holding_sessions']) is not int or contract['holding_sessions']<1 or not math.isfinite(contract['threshold']):
            raise ValueError('STOCK_RULE_PARAMETER_INVALID')
        self.contract=deepcopy(contract);self.strategy_id=contract['signal_version']
        from .monthly_window_v1 import signal_window
        self.signal_window=signal_window(contract)
        policy=contract.get('exit_policy')
        self.structure_exit=policy is not None
        if self.structure_exit:
            if contract.get('exit_input_timing')!='PRIOR_SESSION_ASOF_CLOSE_NEXT_OPEN_ORDER':
                raise ValueError('STOCK_EXIT_INPUT_TIMING_REQUIRED')
            self.exit_factor=policy['factor_conditions'][0]['factor_id']
        else:
            self.exit_factor=None;policy={'exit_type':'FIXED_HOLD','fixed_holding_sessions':contract['holding_sessions']}
        self.exit_evaluator=PortfolioExitEvaluatorV1(self.strategy_id,self.strategy_id,deepcopy(policy))


class StockFactorStrategy:
    """使用Provider已核验的因子切片；因子构思仍在策略/数据层，不进成交引擎。"""
    def __init__(self,contract,legacy=False):
        self.strategy_id=contract.get('signal_version','FIXED_REFERENCE');self.parameters=deepcopy(contract);self.legacy=bool(legacy)
        self.requirements=Requirements('A_SHARE','1D','RAW_EXECUTION_SEPARATE_FACTORS',
            ('daily','states','ready_factors','calendar','actions','hazards'),0,('RANKED_ENTRIES','LOT_EXITS'),
            capabilities=('MULTI_SYMBOL','T1_FIFO','CORPORATE_ACTION_HAZARDS'))
        root=Path(__file__).parent
        self.source_files=tuple(str(root/name) for name in ('fixed_account_rules.py','degraded_execution_v2.py',
            'degraded_train_v1.py','monthly_window_v1.py','structured_exit_trial_v1.py'))

    def build_rules(self):
        return FixedAccountRules(self.parameters) if self.legacy else ConfiguredStockRules(self.parameters)

    @property
    def implementation_options(self):return {'legacy_rules':self.legacy}

    def validate(self):self.build_rules()


class StockAccountBackend:
    """多证券原账户后端；不复制回测，兼容原合同行情及分红/事件约束。"""
    def check(self,req):
        if (req.asset,req.frequency,req.price_view,req.execution)!=('A_SHARE','1D','RAW_EXECUTION_SEPARATE_FACTORS','NEXT_SESSION_OPEN'):
            raise ValueError('STOCK_BACKEND_CAPABILITY_UNSUPPORTED')
        if set(req.intents)!={'RANKED_ENTRIES','LOT_EXITS'} or not set(req.fields)<= {'daily','states','ready_factors','calendar','actions','hazards'}:
            raise ValueError('STOCK_BACKEND_INPUT_OR_INTENT_UNSUPPORTED')
        if not set(req.capabilities)<= {'MULTI_SYMBOL','T1_FIFO','CORPORATE_ACTION_HAZARDS'}:
            raise ValueError('STOCK_BACKEND_FEATURE_UNSUPPORTED')

    def describe(self):
        from .etf_grid_account_v1 import ETFDailyBackend
        import hashlib
        root=Path(__file__).parent
        files=ETFDailyBackend().describe()['source_hashes']
        for name in ('stock_strategy_v1.py','fixed_account_rules.py','degraded_execution_v2.py','degraded_train_v1.py',
                     'train_account_runner_v1.py','monthly_window_v1.py'):
            p=root/name;files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        p=root.parent/'research/run_manifest.py';files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        return {'backend':'A_SHARE_MULTI_STOCK_DAILY_V1','source_hashes':files,
            'execution':'ORIGINAL_DEGRADED_ENGINE_RAW_FILLS_AND_ASOF_FACTORS',
            'limits':{'initial_cash':10000,'max_positions':3,'max_position_weight':1/3,'lot_size':100,
                'commission_rate':.00025,'minimum_commission':5,'stamp_tax_rate':.0005,'slippage':.001,
                'participation':.10,'unsupported_events':'PRESERVE_REJECT_OR_PARTIAL'}}

    def run(self,strategy,bundle,actions,guard):
        from .degraded_execution_v2 import _run_account
        from .common import stable_hash
        self.check(strategy.requirements)
        receipt=guard()
        if receipt['input_identity']!=bundle.input_identity:raise PermissionError('STOCK_BUNDLE_IDENTITY_CONFLICT')
        if bundle.contract_identity!=stable_hash(strategy.parameters):raise PermissionError('STOCK_BUNDLE_CONTRACT_CONFLICT')
        if actions is not None and actions!=bundle.actions:raise PermissionError('STOCK_ACTION_DATASET_CONFLICT')
        for field in strategy.requirements.fields:
            if not hasattr(bundle,field):raise ValueError('STOCK_BUNDLE_FIELD_MISSING:'+field)
        # 明示适配原合同形状；许可来自统一回执，绝不伪装新增人工逐项批准。
        def legacy_guard():
            current=guard()
            return {'plan':{'input_identity':bundle.input_identity,'contracts':{strategy.strategy_id:strategy.parameters}},
                'authorization_adapter':'STRATEGY_INTERFACE_TO_EXISTING_ACCOUNT_CONTRACT',
                'upstream_receipt_id':current.get('receipt_id')}
        runtime=receipt['strategy_plans'][strategy.strategy_id].get('runtime',{})
        source_identity=runtime.get('source_identity')
        if source_identity is None:
            import subprocess
            root=Path(__file__).resolve().parents[3]
            source_identity=(subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
                bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True,encoding='utf-8')))
        if len(source_identity)!=2 or len(source_identity[0])!=40 or type(source_identity[1]) is not bool:
            raise PermissionError('ACTUAL_SOURCE_IDENTITY_REQUIRED')
        return _run_account(bundle,tuple(source_identity),legacy_guard,
                            strategy.parameters,rules=strategy.build_rules())
