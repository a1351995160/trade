"""公共能力目录：声明、入口接线与验证证据分别显示。"""
from pathlib import Path
import hashlib
import json
from copy import deepcopy
from .common import stable_hash
from .research_rule_strategy_v3 import rule_capabilities


def capabilities(*, data_catalog=None):
    rules = rule_capabilities()
    result = {
        "version": "RESEARCH_CAPABILITIES_V1",
        "rules": rules,
        "features": [
            {"id": "indicator_rules", "name": "指标买卖规则", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "cost_stop", "name": "按实际买入成本止损", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "take_profit", "name": "固定比例止盈", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "trailing_stop", "name": "从持仓最高收盘价回落退出", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "full_pool_benchmark", "name": "全股票池买入持有基准", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "atr_stop", "name": "按ATR波幅退出", "engine": True,
             "public_entry": False, "evidence": "NOT_ACCEPTED"},
            {"id": "volatility_rank", "name": "按波动率等评分挑选买入顺序（V4 长期入口）", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "long_horizon_account", "name": "全池长期账户、分段续跑与独立核账", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "signal_account_dual_report", "name": "信号表现与实际账户分开报告", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
            {"id": "continuous_universe_research", "name": "总授权内持续全池研究与批次接续", "engine": True,
             "public_entry": True, "evidence": "NOT_ACCEPTED"},
        ],
        "data": deepcopy(data_catalog) if data_catalog is not None else {"status": "UNKNOWN"},
        "qualification": "EXPLORATORY_ONLY",
        "limitations": ["配置可解析不等于账户入口已接通。", "测试通过不等于策略有效。",
                        "止损在收盘确认，下一交易日尝试成交，不能保证按止损线成交。",
                        "波动率指标与跨股票波动率排名是两项不同能力。",
                        "market_filter只引用同一股票的价格、成交字段，不代表大盘指数过滤。",
                        "全范围请求引用登记清单；缓存数量不证明完整历史市场或账户数据合格。"],
    }
    sources = ('research_capabilities_v1.py', 'research_rule_strategy_v3.py', 'strategy_submission_v1.py',
               'research_data_provider_v1.py', 'rule_account_backend_v2.py', 'rule_exit_adapter_v3.py',
               'forward_paper_engine_v1.py', 'strategy_interface_v1.py', 'research_benchmark_v1.py')
    result['source_hashes'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in sources}
    result['implementation_sha256'] = result['source_hashes']['research_capabilities_v1.py']
    baseline = example_rule(rules)
    with_exits = deepcopy(baseline)
    with_exits['hypothesis'] = '均线交叉与成本退出配置示例，效果尚未验证'
    with_exits['exits'].update(stop_loss_pct=.08, take_profit_pct=.2, trailing_activate_pct=.1, trailing_pct=.05)
    result['examples'] = {'ma_cross': baseline, 'ma_cross_with_exits': with_exits}
    result['examples']['multi_indicator'] = multi_indicator_example(rules)
    ranked = deepcopy(result['examples']['multi_indicator'])
    ranked['version'] = 'RESEARCH_RULE_STRATEGY_V4'
    volatility = next(item for item in rules['indicators'] if item['id'] == 'ROLLING_VOLATILITY')
    ranked['selection'] = {'score': {'op': 'indicator', 'args': ['volatility'],
                                   'params': {'output': volatility['outputs'][0], 'version': volatility['version']}},
                           'direction': 'ASCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'}
    result['examples']['multi_indicator_ranked'] = ranked
    result['full_universe'] = full_universe_capabilities_v1(rules)
    result['long_horizon'] = long_horizon_capabilities_v1()
    result['continuous_research'] = {'research_version': 'DIAGNOSIS_RESEARCH_V4',
        'submission_version': 'FULL_UNIVERSE_SUBMISSION_V4', 'rule_version': 'RESEARCH_RULE_STRATEGY_V4',
        'total_authorization': 'FINITE_OWNER_APPROVED_INCREMENTAL_CANONICAL_LEDGER',
        'channels': ['EXPLORATION', 'CONFIRMATION'], 'maximum_workers_per_advance': 1,
        'real_model_readiness': 'TRUSTED_HARD_TOKEN_AND_COST_BOUND_REQUIRED',
        'final_sessions': {'EXPLORATION': 504, 'CONFIRMATION': 252},
        'real_acceptance': 'NOT_ACCEPTED', 'formal_qualification': 'SEPARATE_NOT_GRANTED',
        'guide': 'CONTINUOUS_UNIVERSE_RESEARCH_GUIDE.md'}
    result['acceptance'] = {'s1': 'EXTERNAL_PUBLICATION',
                            'meaning': '发布验收单独查询已有凭证；无有效发布凭证时保持未验收，入口可用不代表验收完成。'}
    result["fingerprint"] = stable_hash(result)
    return result


def require_current(fingerprint, *, data_catalog=None):
    current = capabilities(data_catalog=data_catalog)
    if fingerprint != current["fingerprint"]:
        raise ValueError("CAPABILITY_SNAPSHOT_STALE")
    return current


def render_markdown(snapshot=None):
    item = capabilities() if snapshot is None else snapshot
    lines = ["# 系统能做什么", "", "本表由公共能力查询生成；入口接通与实际验证分别记录。", "",
             "| 功能 | 底层能力 | 公共入口 | 验证证据 |", "|---|---|---|---|"]
    for feature in item["features"]:
        lines.append(f'| {feature["name"]} | {"有" if feature["engine"] else "不支持"} | {"已接通" if feature["public_entry"] else "未接通"} | {feature["evidence"]} |')
    scope = item['full_universe']
    lines += ["", "## 全范围研究与三个板块", "",
              "全范围请求由系统扫描登记范围的全部证券，再由策略信号选股；所有证券竞争同一份账户资金。",
              "缓存股票数、历史清单是否完整、信号数据是否齐全、账户是否可运行分别记录。缺口不会缩成少数示例股票。",
              "", "| 板块 | 股票代码 | 指标与组合 | 账户与退出 | 工程验收 | 真实全范围验收 |",
              "|---|---|---|---|---|---|"]
    for board in scope['boards']:
        lines.append(f"| {board['name']} | {board['code_prefix']} | 同一指标目录、买卖组合规则 | 共享资金、止损/止盈/移动止损 | {board['engineering_evidence']} | {board['real_evidence']} |")
    lines += ["", f"三板块共同引用 V3 指标目录，共 {scope['indicator_count']} 类指标，版本与参数以本目录为准。",
              "历史长度不足或缺少所需字段时显示预热/数据缺口；不能填零、默认无信号或改称已完整扫描。",
              "交易制度按板块与生效日期执行，指标计算能力一致不代表各板块收益相同。",
              "", "全范围提交使用 `FULL_UNIVERSE_SUBMISSION_V1` 和登记的 `universe_id`，不手填缩小股票名单。",
              "`FULL_UNIVERSE_SUBMISSION_V2` 加 `account_scope=DATA_QUALIFIED`：先检查全池，再冻结全部资料合格股票并公布完整排除清单，最后由策略信号选股。",
              "范围依据数据资格确定，不依据收益、是否成交或信号次数；正常和压力成本共用同一范围。共同来源错误或没有合格股票仍阻断。",
              "补齐后生成新的范围证据，不改旧冻结记录；回顾性资料范围不等于当年完整可投资市场。",
              "`FULL_UNIVERSE_SUBMISSION_V3` 使用新长期执行规格，规则 V4 可事先声明评分与买入顺序；旧规则和旧任务保持原行为。",
              "`DIAGNOSIS_RESEARCH_V4` 通过 `FULL_UNIVERSE_SUBMISSION_V4` 持续推进多指标提案、全池准备、双成本账户、核账和报告。批次结束不代表目标完成；总授权内自动接续。",
              "探索与独立业务验证分别使用数据、阶段额度及状态。短探索不降低最终504日/252日标准；缺独立资料不会让合法探索停下来。",
              "模型须有可核验的 token/费用硬上限；未部署可信保障时显示等待，不能宣称真实AI循环已经验收。维护者批准的授权增量累积到原账本，普通模型不能自行增额。",
              "持续运行需要启动宿主；离线保留接续位置。完整方法见 [持续全池研究说明](CONTINUOUS_UNIVERSE_RESEARCH_GUIDE.md)。",
              "评分只使用当日已知价格或指标；收盘冻结排序，次日开盘按同一顺序分配共享资金。资料或评分未知不算信号失败。",
              "长期任务每段最多900秒、2048MiB、数值线程1；252账户日累计最多4小时，504账户日累计最多8小时。准备、核验、报告各自登记同类资源并单独列明累计耗时。",
              "账户日是观察长度，不是持有期限；每笔持仓仍由卖出信号、已声明止盈止损及最大持有期决定。",
              "分段从已提交完整收盘恢复，不再次消耗账户试验；中断不会重置累计额度或授权到期时间。",
              "同一报告分别列出所有条件机会与实际成交、资金/槽位/整手等拦截原因；5/10/20日信号观察是理论经济值，不冒充真实账户收益。",
              "公共 `scan` 在既有数据授权内固定规则并检查全目标；prepare、冻结、资格与条件计算同处受限进程（900秒/2048MiB/数值线程1）。",
              "完成资格检查的股票数和实际计算过条件的股票数分别报告；缺来源、当时状态或公司行动证据时保持UNKNOWN，不是零信号。",
              "信号检查不创建账户预算，不计算实际成交或收益；重复同一意图只读复用，已中断意图需对账，不能重开免费扫描。",
              "新版本工程及真实验收独立记录；旧发布包不能覆盖全范围、创业板或新源码。",
              "现金基准与非可投资的期初等权价格对照分开；五万元不能整手买下全池时不改成只买代码靠前几只。",
              "完整使用方法见 [全范围研究说明](FULL_UNIVERSE_RESEARCH.md)。",
              "", "## 指标参数", "", item["rules"]["indicator_parameters"], ""]
    for name, bounds in item["rules"]["window_overrides"].items():
        lines.append(f'- {name}：窗口 {bounds[0]}–{bounds[1]} 个交易日。')
    lines += ["", "## 使用边界", ""] + [f'- {text}' for text in item["limitations"]]
    lines += ["", "## 验收状态", "", item['acceptance']['meaning'],
              "所有 NOT_ACCEPTED 保留原状态；生成文档和示例预检不会将其改为通过。",
              "", "## 公共提交示例", "",
              "以下规则由同一能力目录生成。填入声明式提交的 rule 字段；数据目录、授权、股票范围和资金由实际任务另行冻结。",
              "示例只做公共 preview 预检，不读取行情、不创建账户、不证明有效性。"]
    for name, rule in item['examples'].items():
        lines += ["", f"### {name}", "", "```json", json.dumps(rule, ensure_ascii=False, indent=2), "```"]
    lines += ["", "## 维护方式", "",
              "运行 `python scripts/generate_research_capabilities_v1.py` 更新本文；",
              "CI 运行 `python scripts/generate_research_capabilities_v1.py --check` 检查本文与公共示例预检。",
              "实际任务的能力 fingerprint 仍绑定实现源码和登记数据，本文不冻结某个部署的指纹。"]
    return "\n".join(lines) + "\n"


def example_rule(rules):
    """同一目录生成可提交的明确示例，不声称有盈利能力。"""
    ma = next(item for item in rules['indicators'] if item['id'] == 'MA')
    def indicator(alias):
        return {'op': 'indicator', 'args': [alias], 'params': {'output': 'ma', 'version': ma['version']}}
    return {'version': rules['version'], 'hypothesis': '均线交叉示例，效果尚未验证', 'change_reason': '公共能力示例',
            'buy': {'op': 'cross_up', 'args': [indicator('fast'), indicator('slow')], 'params': {}},
            'sell': {'op': 'cross_down', 'args': [indicator('fast'), indicator('slow')], 'params': {}},
            'market_filter': None, 'min_hold_sessions': 5, 'max_hold_sessions': 40,
            'cooldown_sessions': 5, 'target_weight': .5,
            'indicator_instances': [{'instance_id': alias, 'id': 'MA', 'version': ma['version'],
                                    'params': {**ma['params'], 'window': window}} for alias, window in [('fast', 10), ('slow', 20)]],
            'exits': {'execution_mode': rules['exit_policy']['execution_mode'], 'stop_loss_pct': None,
                      'take_profit_pct': None, 'trailing_activate_pct': None, 'trailing_pct': None}}


def multi_indicator_example(rules):
    """三类指标加量能字段的合法示例；没有读取收益或挑选参数。"""
    rule = example_rule(rules)
    catalog = {item['id']: item for item in rules['indicators']}
    def node(name, args=(), params=None):
        return {'op': name, 'args': list(args), 'params': {} if params is None else params}
    def indicator(alias, key):
        item = catalog[key]
        rule['indicator_instances'].append({'instance_id': alias, 'id': key,
            'version': item['version'], 'params': deepcopy(item['params'])})
        return node('indicator', [alias], {'output': item['outputs'][0], 'version': item['version']})
    rsi, volatility = indicator('rsi', 'RSI'), indicator('volatility', 'ROLLING_VOLATILITY')
    rule['buy'] = node('and', [rule['buy'],
        node('between', [rsi, node('const', params={'value': 40}), node('const', params={'value': 70})]),
        node('lt', [volatility, node('const', params={'value': .6})])])
    volume = node('field', ['volume'])
    rule['market_filter'] = node('gt', [volume, node('ref', [deepcopy(volume)], {'periods': 1})])
    rule['hypothesis'] = '均线趋势、RSI区间与波动率组合，加同股量能确认的配置示例，效果未验证'
    rule['exits'].update(stop_loss_pct=.08, take_profit_pct=.2, trailing_activate_pct=.1, trailing_pct=.05)
    return rule


def full_universe_capabilities_v1(rules=None):
    """声明新路径覆盖，验收只认其独立来源凭证，不继承旧发布包。"""
    rules = rule_capabilities() if rules is None else rules
    sources = ('research_universe_v1.py', 'tdx_research_adapter_v1.py', 'baostock_universe_adapter_v1.py',
        'universe_data_provider_v1.py', 'universe_account_inputs_v1.py',
        'board_execution_policy_v1.py', 'universe_signal_scan_v1.py',
        'universe_account_backend_v1.py', 'universe_dividend_accounting_v1.py', 'universe_rule_exit_v1.py',
        'universe_corporate_accounting_v2.py', 'corporate_action_price_v2.py', 'causal_dividend_features_v1.py',
        'universe_submission_v1.py', 'universe_status_v1.py', 'universe_scan_service_v1.py', 'universe_qualified_scope_v1.py')
    supported = ['indicator_rules', 'multi_indicator_rules', 'full_range_scan', 'public_signal_scan',
                 'shared_account', 'cost_stop', 'take_profit', 'trailing_stop',
                 'cash_dividend', 'qualified_share_actions', 'suspension_recovery', 'account_audit',
                 'qualified_scope_account', 'published_exclusions', 'full_pool_completion',
                 'long_horizon_account', 'frozen_score_selection', 'signal_account_dual_report']
    return {'version': 'FULL_UNIVERSE_RESEARCH_CAPABILITIES_V1',
        'submission_version': 'FULL_UNIVERSE_SUBMISSION_V1',
        'submission_versions': ['FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2', 'FULL_UNIVERSE_SUBMISSION_V3'],
        'qualified_scope': {'version': 'UNIVERSE_QUALIFIED_SCOPE_V1', 'account_scope': 'DATA_QUALIFIED',
            'selection': 'ALL_ACCOUNT_DATA_QUALIFIED_SYMBOLS', 'manual_symbols': False,
            'uses_strategy_results': False, 'global_unknown': 'BLOCK', 'repair': 'NEW_IMMUTABLE_SCOPE',
            'historical_investability': 'NOT_PROVEN'},
        'deployment_version': 'FULL_UNIVERSE_DEPLOYMENT_V1',
        'data_adapters': ['TDX_FULL_UNIVERSE_V1', 'BAOSTOCK_FULL_UNIVERSE_V1'],
        'supplement_policy': 'REGISTERED_ORIGINALS_FIRST_THEN_SYMBOL_YEAR_FIELD_GAPS',
        'scan_version': 'UNIVERSE_PUBLIC_SIGNAL_SCAN_V1',
        'signal_scan_resources': {'memory_mib': 2048, 'wall_seconds': 900, 'numerical_threads': 1,
                                 'scope': 'PREPARE_FREEZE_QUALIFY_AND_SCAN_IN_ONE_WORKER'},
        'signal_scan_account_budget': 'NOT_CREATED_OR_CONSUMED',
        'signal_scan_unknown_policy': 'QUALIFICATION_PROCESSED_IS_NOT_CONDITIONS_EVALUATED',
        'indicator_catalog_sha256': rules['catalog_sha256'],
        'indicator_count': len(rules['indicators']),
        'indicator_ids': [item['id'] for item in rules['indicators']],
        'indicator_data_policy': 'EXPLICIT_REQUIRED_FIELDS_AND_VALID_BAR_WARMUP',
        'selection_policy': 'ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS',
        'cash_policy': 'ONE_SHARED_ACCOUNT_ACROSS_BOARDS',
        'cash_payment_policy': 'ACTUAL_PAYMENT_DATE_RECEIVABLE_NOT_SPENDABLE',
        'exit_price_policy': 'RAW_PLUS_ENTITLED_GROSS_CASH_V1',
        'share_action_account_version': 'UNIVERSE_CORPORATE_ACCOUNTING_V2',
        'share_exit_price_policy': 'RAW_IN_ORIGINAL_SHARE_UNITS_PLUS_ENTITLED_CASH_V2',
        'corporate_action_coverage_policy': 'PRICE_COVERAGE_AND_ACCOUNT_TERMS_SEPARATE',
        'share_action_requirements': ['EXPLICIT_RATIO_AND_SOURCE', 'SHARE_CREDIT_AND_TRADABLE_DATE_EVIDENCE',
                                     'EXPLICIT_TAX_RULE_AND_SOURCE', 'INTEGER_ENTITLEMENTS'],
        'share_tax_allocation': 'BONUS_CHILD_LOTS_MODELED_NOT_CSDC_VERIFIED',
        'tax_collection_time': 'SALE_FILL_MODELED_NOT_BROKER_VERIFIED',
        'market_filter_scope': rules['market_filter_scope'],
        'engineering_evidence': 'ENGINEERING_NOT_ACCEPTED', 'real_evidence': 'REAL_NOT_ACCEPTED',
        'strategy_qualified': False,
        'boards': [{'id': board, 'name': name, 'code_prefix': prefix,
                    'supported_features': list(supported),
                    'indicator_catalog_sha256': rules['catalog_sha256'],
                    'engineering_evidence': 'ENGINEERING_NOT_ACCEPTED', 'real_evidence': 'REAL_NOT_ACCEPTED'}
                   for board, name, prefix in [('SZ_MAIN', '深圳主板', '00xxxx.SZ'),
                                              ('SH_MAIN', '上海主板', '60xxxx.SH'),
                                              ('CHINEXT', '创业板', '30xxxx.SZ')]],
        'source_hashes': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                         for name in sources},
        'unsupported': ['atr_stop', 'STAR', 'BSE', 'BROKER_LIVE_TRADING'],
        'limitations': ['已登记范围不等于完整历史市场；缺退市/历史清单证据仍保留UNKNOWN。',
            '新入口的能力接线、工程测试、真实全范围验收、策略有效性分别判断。',
            '送转需完整条款；碎股、登记后持仓权益变化、配股及未知退市结算仍阻断完整账户结论。',
            '现金分红按原到账日处理，税款成交时扣收仍为有来源的模型时点。']}


def long_horizon_capabilities_v1():
    from .universe_execution_profile_v1 import execution_profile, SEGMENTED_PROFILE
    names = ('research_rule_strategy_v4.py', 'universe_selection_v1.py', 'universe_execution_profile_v1.py',
        'universe_compute_governance_v1.py', 'universe_account_backend_v2.py', 'universe_execution_state_v2.py',
        'universe_execution_artifacts_v1.py', 'universe_signal_scan_v2.py', 'universe_evidence_v2.py',
        'universe_signal_funnel_v1.py', 'universe_research_report_v2.py')
    return {'version': 'LONG_HORIZON_RESEARCH_CAPABILITIES_V1',
        'submission_version': 'FULL_UNIVERSE_SUBMISSION_V3', 'rule_versions': ['RESEARCH_RULE_STRATEGY_V3', 'RESEARCH_RULE_STRATEGY_V4'],
        'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2',
        'profiles': [execution_profile(SEGMENTED_PROFILE, count) for count in (252, 504)],
        'score_operators': ['const', 'field', 'indicator', 'ref', 'add', 'sub', 'mul', 'div'],
        'score_directions': ['ASCENDING', 'DESCENDING'], 'tie_breaker': 'SYMBOL_ASCENDING',
        'signal_horizons': [5, 10, 20], 'continuous_reference': 'MAINTAINER_ENGINEERING_ONLY',
        'feature_price_policy': 'CAUSAL_SUSPENDED_CASH_AND_SHARES_V3',
        'suspended_cash_mark_policy': 'MODELED_SUSPENDED_EX_REFERENCE_V3',
        'formal_method': 'UNSUPPORTED', 'strategy_qualified': False,
        'engineering_evidence': 'ENGINEERING_NOT_ACCEPTED', 'real_evidence': 'REAL_NOT_ACCEPTED',
        'source_hashes': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}}


def published_acceptance(snapshot=None):
    """只核验固定发布包的已有元数据，不读取行情、不调用账户或独立复算器。"""
    core = capabilities() if snapshot is None else snapshot
    return _published_acceptance(Path(__file__).resolve().parents[3], core)


def _published_acceptance(repo, core):
    prefix = Path('reports/trusted_workflow_acceptance')
    metadata = prefix / 'published_metadata'
    boundary = 'PUBLISHED_METADATA_ONLY_NO_MARKET_READ_OR_REAUDIT'
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)
    def read_ref(reference, expected_name):
        require(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'ARTIFACT_REFERENCE_INVALID')
        raw_path = reference['path']
        require(isinstance(raw_path, str) and '\\' not in raw_path and ':' not in raw_path, 'ARTIFACT_PATH_INVALID')
        relative = Path(raw_path)
        require(not relative.is_absolute() and '..' not in relative.parts
                and relative.is_relative_to(metadata) and relative.name == expected_name, 'ARTIFACT_PATH_INVALID')
        path = repo / relative
        require(path.resolve() == path and path.is_file(), 'ARTIFACT_MISSING_OR_REDIRECTED')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == reference['sha256'], 'ARTIFACT_HASH_CONFLICT')
        return json.loads(raw)
    try:
        repo = Path(repo).resolve(strict=True)
        path = repo / prefix / 'PUBLISHED_ACCEPTANCE.json'
        require(path.resolve() == path and path.is_file(), 'PUBLISHED_ACCEPTANCE_MISSING_OR_REDIRECTED')
        receipt = json.loads(path.read_bytes())
        require(set(receipt) == {'version', 'source_hashes', 'feature_ids', 'cases', 'self_hash'}, 'PUBLICATION_FIELDS_INVALID')
        require(receipt['version'] == 'PUBLISHED_RESEARCH_ACCEPTANCE_V1', 'PUBLICATION_VERSION_INVALID')
        require(receipt['self_hash'] == stable_hash({key: value for key, value in receipt.items() if key != 'self_hash'}), 'PUBLICATION_HASH_CONFLICT')
        require(receipt['source_hashes'] == core['source_hashes'] and len(receipt['source_hashes']) == 9, 'PUBLICATION_SOURCE_CHANGED')
        features = receipt['feature_ids']
        available = {item['id'] for item in core['features'] if item['public_entry']}
        require(isinstance(features, list) and len(features) == len(set(features))
                and set(features) <= available and features, 'PUBLICATION_FEATURE_SCOPE_INVALID')
        require(isinstance(receipt['cases'], list) and len(receipt['cases']) == 6, 'PUBLICATION_CASES_INVALID')
        pairs, rules, pools, covered = set(), set(), set(), set()
        for case in receipt['cases']:
            require(set(case) == {'rule_identity', 'symbols', 'pool_identity', 'initial_cash',
                                  'job', 'preview', 'verification', 'confirmation', 'index', 'accounts'}, 'PUBLICATION_CASE_FIELDS_INVALID')
            symbols = case['symbols']
            require(isinstance(symbols, list) and symbols == sorted(set(symbols)) and symbols
                    and case['pool_identity'] == stable_hash(symbols) and case['initial_cash'] == 50000,
                    'PUBLICATION_CASE_SCOPE_INVALID')
            pair = (case['rule_identity'], case['pool_identity'])
            require(pair not in pairs, 'PUBLICATION_CASE_DUPLICATE')
            pairs.add(pair); rules.add(pair[0]); pools.add(pair[1])
            job = read_ref(case['job'], 'JOB.json')
            preview = read_ref(case['preview'], 'PREVIEW.json')
            require(preview['preview_identity'] == stable_hash({key: value for key, value in preview.items() if key != 'preview_identity'})
                    and preview['capabilities']['source_hashes'] == core['source_hashes']
                    and preview['request']['initial_cash'] == 50000 and preview['request']['symbols'] == symbols
                    and preview['rule_identity'] == case['rule_identity']
                    and preview['request']['benchmark'] == 'FULL_POOL_BUY_HOLD', 'PUBLICATION_PREVIEW_SOURCE_OR_SCOPE_CONFLICT')
            archived_previews = [digest for name, digest in job['source_hashes'].items() if name.replace('\\', '/').rsplit('/', 1)[-1] == 'PREVIEW.json']
            require(archived_previews == [case['preview']['sha256']], 'PUBLICATION_PREVIEW_JOB_CONFLICT')
            verification = read_ref(case['verification'], 'VERIFICATION.json')
            confirmation = read_ref(case['confirmation'], 'CONFIRMATION.json')
            index = read_ref(case['index'], 'RESULTS_INDEX.json')['items']
            require(verification['job_sha256'] == case['job']['sha256'] and verification['advance_allowed'] is True,
                    'PUBLICATION_VERIFICATION_JOB_CONFLICT')
            require(confirmation['receipt_id'] == stable_hash({key: value for key, value in confirmation.items() if key != 'receipt_id'})
                    and confirmation['strategy_plans'] == job['plans'] and confirmation['input_identity'] == job['input_identity']
                    and confirmation['objective_id'] == job['objective_id'], 'PUBLICATION_CONFIRMATION_CONFLICT')
            require(len(job['plans']) == 3 and set(case['accounts']) == set(job['plans']) == set(verification['items']) == set(index),
                    'PUBLICATION_THREE_ACCOUNTS_REQUIRED')
            scenarios, payloads = set(), []
            for name, plan in job['plans'].items():
                require(plan['plan_id'] == stable_hash({key: value for key, value in plan.items() if key != 'plan_id'})
                        and plan['runtime'] == job['items'][name], 'PUBLICATION_PLAN_HASH_CONFLICT')
                backend = plan['backend']
                require(backend['initial_cash'] == 50000 and backend['window']['symbols'] == symbols, 'PUBLICATION_ACCOUNT_SCOPE_CONFLICT')
                scenario = name.rsplit('_', 1)[-1]
                require(scenario in {'BASE', 'STRESS', 'BENCHMARK'} and scenario not in scenarios, 'PUBLICATION_ACCOUNT_SCENARIO_INVALID')
                scenarios.add(scenario)
                parameters = plan['strategy']['parameters']
                if scenario != 'BENCHMARK':
                    require(parameters['rule_identity'] == case['rule_identity'], 'PUBLICATION_RULE_CONFLICT')
                    payloads.append(parameters['candidate_payload'])
                else:
                    require(parameters['candidate_payload'] == {'version': 'FULL_POOL_BUY_HOLD_V1', 'symbols': symbols}, 'PUBLICATION_BENCHMARK_SCOPE_CONFLICT')
                    covered.add('full_pool_benchmark')
                refs = case['accounts'][name]
                require(set(refs) == {'result', 'settlement', 'start', 'resource'}, 'PUBLICATION_ACCOUNT_ARTIFACT_FIELDS')
                result = read_ref(refs['result'], name + '_RESULT.json')
                settled = read_ref(refs['settlement'], name + '_SETTLEMENT.json')
                started = read_ref(refs['start'], name + '_START.json')
                resource = read_ref(refs['resource'], name + '_RESOURCE.json')
                proof = verification['items'][name]
                require(proof['status'] == 'PASS' and proof['advance_allowed'] is True and proof['reasons'] == []
                        and proof['plan_id'] == plan['plan_id'] and proof['input_identity'] == job['input_identity'], 'PUBLICATION_CHECK_NOT_PASS')
                require(proof['result_sha256'] == refs['result']['sha256'] == settled['result_sha256'] == index[name]['sha256']
                        and result['strategy_plan'] == plan and result['input_identity'] == job['input_identity'], 'PUBLICATION_RESULT_BINDING_CONFLICT')
                require(started['kind'] == name and started['receipt_id'] == confirmation['receipt_id']
                        and started['counted_before_account_calculation'] is True
                        and all(settled.get(key) == value for key, value in started.items())
                        and settled['completed'] is True and settled['error'] is None, 'PUBLICATION_SETTLEMENT_CONFLICT')
                require(type(resource['returncode']) is int and resource['returncode'] == 0 and resource['timed_out'] is False,
                        'PUBLICATION_WORKER_NOT_COMPLETED')
                require(proof['evidence_layers']['account_reconciled'] is True
                        and proof['evidence_layers']['input_profile'] == 'HISTORICAL_MODELED', 'PUBLICATION_EVIDENCE_PROFILE_INVALID')
            require(payloads[0] == payloads[1] and payloads[0]['version'] == 'RESEARCH_RULE_STRATEGY_V3', 'PUBLICATION_RULE_SCENARIO_CONFLICT')
            covered.add('indicator_rules')
            for field, feature in [('stop_loss_pct', 'cost_stop'), ('take_profit_pct', 'take_profit'), ('trailing_pct', 'trailing_stop')]:
                if payloads[0]['exits'][field] is not None:
                    covered.add(feature)
        require(len(rules) == 3 and len(pools) == 2 and pairs == {(rule, pool) for rule in rules for pool in pools}, 'PUBLICATION_MATRIX_INCOMPLETE')
        require(set(features) <= covered, 'PUBLICATION_FEATURE_NOT_COVERED')
        return {'status': 'PUBLISHED_METADATA_VERIFIED', 'feature_ids': features,
                'evidence_fingerprint': receipt['self_hash'], 'case_count': 6, 'account_count': 18,
                'initial_cash': 50000, 'assurance': boundary, 'strategy_qualified': False}
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return {'status': 'NOT_ACCEPTED', 'reason': str(error), 'feature_ids': [],
                'evidence_fingerprint': None, 'assurance': boundary, 'strategy_qualified': False}


def capabilities_display(*, data_catalog=None):
    """仅展示端使用；返回的core可继续显示，publication绝不进入提交身份。"""
    core = capabilities(data_catalog=data_catalog)
    return {'core': core, 'publication': published_acceptance(core),
            'long_horizon_publication': published_long_horizon_acceptance(core)}


def published_long_horizon_acceptance(snapshot=None):
    """新长期实测凭证单独读取；不改变冻结能力目录或沿用旧版本凭证。"""
    from .long_horizon_acceptance_publication_v1 import published_long_horizon_acceptance as read_publication
    return read_publication(Path(__file__).resolve().parents[3], capabilities() if snapshot is None else snapshot)
