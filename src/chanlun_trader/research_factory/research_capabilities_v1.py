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
            {"id": "volatility_rank", "name": "跨股票按波动率排名选股", "engine": False,
             "public_entry": False, "evidence": "UNSUPPORTED"},
        ],
        "data": data_catalog if data_catalog is not None else {"status": "UNKNOWN"},
        "qualification": "EXPLORATORY_ONLY",
        "limitations": ["配置可解析不等于账户入口已接通。", "测试通过不等于策略有效。",
                        "止损在收盘确认，下一交易日尝试成交，不能保证按止损线成交。",
                        "波动率指标与跨股票波动率排名是两项不同能力。"],
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
    lines += ["", "## 指标参数", "", item["rules"]["indicator_parameters"], ""]
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
    return {'core': core, 'publication': published_acceptance(core)}
