"""从实际 collection / JUnit 自动生成计数与逐项证据行。

任务要求（PR16-03）：
- 从实际 collection/JUnit **自动生成**计数，不发明汇总通过数；
- 分列本机与 CI、passed/skipped/failed/errors，不把缺数据跳过记为通过；
- 全量口径必须区分"含 errors"与"仅 failed"；
- 为 25 项指标建立逐项证据行（indicator/output/params/input-domain/
  formula/condition/account/nodeid/JUnit/status），并声明证明强度。

用法：
    python scripts/emit_price_only_evidence_v1.py
"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
from itertools import product
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT_DIR = REPO_ROOT / "reports" / "price_only_validation_v1"
TARGET_JUNIT_NAME = "junit-price-only-v1.xml"
BASE_SHA = "f5acb0317719a42eb7071504048e341b50a7ac12"

# Windows 盘符前缀：在 POSIX 上会被误当仓库内相对路径，直接拒绝
_DRIVE_PREFIX_RE = re.compile(r"^[a-zA-Z]:[\\/]")

# 条件消费与账户接线的适用测试函数（**逐项**绑定，不用函数全体参数替单项作证）
CONDITION_MODULE = "tests/conditions_v2/test_price_only_condition_consumption_v1.py"
CONDITION_TEST = (CONDITION_MODULE, "test_condition_layer_consumes_each_new_indicator")
ACCOUNT_TEST = (CONDITION_MODULE, "test_indicator_reaches_account_chain_through_public_entry")

# 维度 -> (模块, 测试函数)；公式维度的函数名因指标而异，见 FORMULA_NODEIDS
DIMENSION_TESTS = {
    "condition": CONDITION_TEST,
    "account": ACCOUNT_TEST,
}

# 非参数化的**单独**条件消费测试：既有、已通过，但参数化扫描扫不到。
# 任务要求把它们映射进去（或明确标 UNMAPPED_EVIDENCE），不要求重复新写测试。
SINGLE_CONDITION_TESTS = {
    "CCI": f"{CONDITION_MODULE}::test_condition_layer_consumes_cci_output_with_true_false_unknown",
    "NATR": f"{CONDITION_MODULE}::test_condition_layer_consumes_natr_output",
    "PSY": f"{CONDITION_MODULE}::test_condition_layer_consumes_psy_output",
}

# 各维度**实际被断言**的输出与参数（逐项核对对应测试的真实断言，非注册表）。
#
# 核对方法：从测试源码 AST 提取该指标对应测试函数中所有 ``.value("<output>")``
# 调用（即被逐值断言的输出），以及 ``@pytest.mark.parametrize`` 的**实际取值**。
# 未出现在断言中的输出/参数一律不认证（保留 PARTIAL）——注册信息不能自动认证。
#
# ``tested_parameter_sets`` 记录测试**真正执行**的参数取值与组合，
# 不写参数名，也不从注册默认值推导范围（例：只执行 window=10/20 时，
# 不得声明 10~20 全范围）。
VALIDATED_BY_DIMENSION = {
    # ---- 公式维度：逐值断言的主输出 + 实际参数取值 ----
    ("DEMA", "formula"): {"outputs": ["dema"],
                          "tested_parameter_sets": [{"window": 5}, {"window": 20}]},
    ("TEMA", "formula"): {"outputs": ["tema"],
                          "tested_parameter_sets": [{"window": 5}, {"window": 20}]},
    ("CCI", "formula"): {"outputs": ["cci"],
                         "tested_parameter_sets": [{"window": 14}, {"window": 20}]},
    ("NATR", "formula"): {"outputs": ["natr"],
                          "tested_parameter_sets": [{"window": 14}, {"window": 20}]},
    ("PSY", "formula"): {"outputs": ["psy"],
                         "tested_parameter_sets": [{"window": 12}, {"window": 6}]},
    # DONCHIAN 只逐值断言 upper/lower；middle 未被断言
    ("DONCHIAN", "formula"): {"outputs": ["upper", "lower"],
                              "tested_parameter_sets": [{"window": 20}, {"window": 10}]},
    # KELTNER 只逐值断言 upper/lower；middle/atr 未被断言
    ("KELTNER", "formula"): {"outputs": ["upper", "lower"],
                             "tested_parameter_sets": [{"window": 20, "atr_window": 10,
                                                        "multiplier": 2.0}]},
    # ROLLING_VOLATILITY 只逐值断言 volatility；return 未被断言
    ("ROLLING_VOLATILITY", "formula"): {
        "outputs": ["volatility"],
        "tested_parameter_sets": [{"window": 20}, {"window": 10}]},
    ("HISTORICAL_RETURN", "formula"): {
        "outputs": ["return"],
        "tested_parameter_sets": [{"window": 20}, {"window": 5}]},
    ("PRICE_EXTREMES", "formula"): {
        "outputs": ["hhv", "llv"],
        "tested_parameter_sets": [{"window": 20}, {"window": 10}]},
    ("PRIOR_BREAKOUT", "formula"): {
        "outputs": ["prior_high", "prior_low"],
        "tested_parameter_sets": [{"window": 20}, {"window": 10}]},
    # DRAWDOWN_FROM_PEAK 同时逐值断言 drawdown 与 peak
    ("DRAWDOWN_FROM_PEAK", "formula"): {
        "outputs": ["drawdown", "peak"],
        "tested_parameter_sets": [{"window": 60}, {"window": 20}]},
    # MACD_HIST_RAW 只逐值断言 hist_raw；dif/dea 未被断言
    ("MACD_HIST_RAW", "formula"): {
        "outputs": ["hist_raw"],
        "tested_parameter_sets": [{"fast": 12, "slow": 26, "signal": 9}]},
    ("TRIX", "formula"): {"outputs": ["trix", "trix_ma"],
                          "tested_parameter_sets": [{"window": 12, "signal": 9},
                                                    {"window": 6, "signal": 9}]},
    ("VOLUME_MA", "formula"): {"outputs": ["volume_ma"],
                               "tested_parameter_sets": [{"window": 20}, {"window": 5}]},
    ("AMOUNT_MA", "formula"): {"outputs": ["amount_ma"],
                               "tested_parameter_sets": [{"window": 20}, {"window": 5}]},
    ("RVOL_PRIOR", "formula"): {"outputs": ["rvol"],
                                "tested_parameter_sets": [{"window": 20}, {"window": 5}]},
    ("RVOL_INCL_CURRENT", "formula"): {
        "outputs": ["rvol"],
        "tested_parameter_sets": [{"window": 20}, {"window": 5}]},
    ("ACCUMULATION_DISTRIBUTION", "formula"): {
        "outputs": ["ad_line"], "tested_parameter_sets": [{}]},
    ("CHAIKIN_MONEY_FLOW", "formula"): {"outputs": ["cmf"],
                                        "tested_parameter_sets": [{"window": 20}]},
    ("PVT", "formula"): {"outputs": ["pvt"], "tested_parameter_sets": [{}]},
    ("VWAP_SESSION_PROXY", "formula"): {
        "outputs": ["vwap_session_proxy"], "tested_parameter_sets": [{}]},
    ("MFI", "formula"): {"outputs": ["mfi"],
                         "tested_parameter_sets": [{"window": 14}]},
    ("ROLLING_SLOPE", "formula"): {"outputs": ["slope"],
                                   "tested_parameter_sets": [{"window": 20}]},
    ("TRUE_RANGE", "formula"): {"outputs": ["tr"], "tested_parameter_sets": [{}]},
    # ---- 条件维度：测试**手工注入**的输出（CONDITION_INJECTED）----
    # 单独测试：CCI/NATR/PSY（各断言 TRUE/FALSE/UNKNOWN 三值齐备）
    ("CCI", "condition"): {"outputs": ["cci"], "tested_parameter_sets": [{"threshold": 100.0}]},
    ("NATR", "condition"): {"outputs": ["natr"], "tested_parameter_sets": [{"threshold": 1.0}]},
    ("PSY", "condition"): {"outputs": ["psy"], "tested_parameter_sets": [{"threshold": 50.0}]},
    # 参数化用例：注入 (indicator, output, threshold) 三元组
    ("DEMA", "condition"): {"outputs": ["dema"],
                            "tested_parameter_sets": [{"threshold": 10.0}]},
    ("TEMA", "condition"): {"outputs": ["tema"],
                            "tested_parameter_sets": [{"threshold": 10.0}]},
    ("TRIX", "condition"): {"outputs": ["trix"],
                            "tested_parameter_sets": [{"threshold": 0.5}]},
    ("KELTNER", "condition"): {"outputs": ["upper"],
                               "tested_parameter_sets": [{"threshold": 10.0}]},
    ("DONCHIAN", "condition"): {"outputs": ["upper"],
                                "tested_parameter_sets": [{"threshold": 10.0}]},
    ("PRICE_EXTREMES", "condition"): {
        "outputs": ["hhv"], "tested_parameter_sets": [{"threshold": 10.0}]},
    ("HISTORICAL_RETURN", "condition"): {
        "outputs": ["return"], "tested_parameter_sets": [{"threshold": 0.5}]},
    ("ROLLING_VOLATILITY", "condition"): {
        "outputs": ["volatility"], "tested_parameter_sets": [{"threshold": 0.01}]},
    ("VOLUME_MA", "condition"): {
        "outputs": ["volume_ma"], "tested_parameter_sets": [{"threshold": 500000.0}]},
    ("RVOL_PRIOR", "condition"): {
        "outputs": ["rvol"], "tested_parameter_sets": [{"threshold": 1.0}]},
    ("MFI", "condition"): {"outputs": ["mfi"],
                           "tested_parameter_sets": [{"threshold": 50.0}]},
    # ---- 账户维度：经公开服务产生合成成交（ACCOUNT_WIDE_THRESHOLD）----
    # 测试断言"产生真实成交"，不断言具体输出或阈值敏感性。
    # 指标配置参数从账户测试的 parametrize 源码提取，避免以注册默认值冒充。
    **{(ind, "account"): {"outputs": [], "tested_parameter_sets": []}
       for ind in ("CCI", "NATR", "PSY", "DEMA", "TEMA", "TRIX", "DONCHIAN",
                   "KELTNER", "HISTORICAL_RETURN", "ROLLING_VOLATILITY",
                   "PRICE_EXTREMES", "PRIOR_BREAKOUT", "VOLUME_MA", "RVOL_PRIOR",
                   "MFI", "ACCUMULATION_DISTRIBUTION", "CHAIKIN_MONEY_FLOW", "PVT",
                   "VWAP_SESSION_PROXY", "MACD_HIST_RAW", "AMOUNT_MA",
                   "DRAWDOWN_FROM_PEAK", "ROLLING_SLOPE", "RVOL_INCL_CURRENT",
                   "TRUE_RANGE")},
}

# 本轮新增测试的组成部分（按文件，可自动计数）
NEW_TEST_FILES = {
    "formula": "tests/indicators_v2/test_price_only_formula_increment_v1.py",
    "condition_and_service": "tests/conditions_v2/test_price_only_condition_consumption_v1.py",
    "gbbq_guard": "tests/price_only_scope/test_gbbq_access_guard_v1.py",
    "gbbq_identity_bypass": "tests/price_only_scope/test_gbbq_identity_bypass_v1.py",
    "bounded_read": "tests/price_only_scope/test_bounded_read_boundary_v1.py",
    "real_raw_sample": "tests/price_only_scope/test_real_raw_sample_v1.py",
    "task_scope": "tests/price_only_scope/test_task_scope_v1.py",
    "vendor_entry_guard": "tests/price_only_scope/test_vendor_entry_guard_v1.py",
    "controlled_reader_boundary": "tests/price_only_scope/test_controlled_reader_boundary_v1.py",
    "scope_order_regression": "tests/price_only_scope/test_scope_order_regression_v1.py",
    "evidence_mutation": "tests/price_only_scope/test_evidence_mutation_v1.py",
    "relative_link_binding": "tests/price_only_scope/test_relative_link_binding_v1.py",
    "read_path_identity": "tests/price_only_scope/test_read_path_identity_v1.py",
}
QFQ_SYNTHETIC_FILE = "tests/pit/test_qfq_pit_synthetic_v1.py"

# 本轮新增 oracle 覆盖的指标（用于逐项证据行）
NEW_ORACLE_INDICATORS = [
    "DEMA", "TEMA", "CCI", "NATR", "PSY", "DONCHIAN", "KELTNER",
    "ROLLING_VOLATILITY", "HISTORICAL_RETURN", "PRICE_EXTREMES",
    "PRIOR_BREAKOUT", "DRAWDOWN_FROM_PEAK", "MACD_HIST_RAW", "TRIX",
    "VOLUME_MA", "AMOUNT_MA", "RVOL_PRIOR", "RVOL_INCL_CURRENT",
    "ACCUMULATION_DISTRIBUTION", "CHAIKIN_MONEY_FLOW", "PVT",
    "VWAP_SESSION_PROXY", "MFI", "ROLLING_SLOPE", "TRUE_RANGE",
]


class CollectionError(RuntimeError):
    """收集失败：不得返回正常 0 项并继续签证。"""


# 受审映射：指标 -> 公式维度的**完整参数化 nodeid**。
# 生成器负责验证这些 nodeid 在 JUnit 中的实际 outcome，而不是仅检查名字存在。
FORMULA_MODULE = "tests/indicators_v2/test_price_only_formula_increment_v1.py"


def _formula_node(func: str) -> str:
    """拼出公式验收用例的完整 nodeid（避免重复字面量）。"""
    return f"{FORMULA_MODULE}::{func}"


FORMULA_NODEIDS = {
    "DEMA": _formula_node("test_dema_matches_oracle"),
    "TEMA": _formula_node("test_tema_matches_oracle"),
    "CCI": _formula_node("test_cci_matches_oracle"),
    "NATR": _formula_node("test_natr_matches_oracle"),
    "PSY": _formula_node("test_psy_matches_oracle"),
    "DONCHIAN": _formula_node("test_donchian_matches_oracle"),
    "KELTNER": _formula_node("test_keltner_matches_oracle"),
    "ROLLING_VOLATILITY": _formula_node("test_rolling_volatility_matches_oracle"),
    "HISTORICAL_RETURN": _formula_node("test_historical_return_matches_oracle"),
    "PRICE_EXTREMES": _formula_node("test_price_extremes_matches_oracle"),
    "PRIOR_BREAKOUT": _formula_node("test_prior_breakout_matches_oracle"),
    "DRAWDOWN_FROM_PEAK": _formula_node("test_drawdown_from_peak_matches_oracle"),
    "MACD_HIST_RAW": _formula_node("test_macd_hist_raw_matches_oracle"),
    "TRIX": _formula_node("test_trix_matches_oracle"),
    "VOLUME_MA": _formula_node("test_volume_ma_and_amount_ma_match_oracle"),
    "AMOUNT_MA": _formula_node("test_volume_ma_and_amount_ma_match_oracle"),
    "RVOL_PRIOR": _formula_node("test_rvol_variants_match_oracle_and_differ"),
    "RVOL_INCL_CURRENT": _formula_node("test_rvol_variants_match_oracle_and_differ"),
    "ACCUMULATION_DISTRIBUTION": _formula_node("test_accumulation_distribution_and_chaikin_and_pvt_match_oracle"),
    "CHAIKIN_MONEY_FLOW": _formula_node("test_accumulation_distribution_and_chaikin_and_pvt_match_oracle"),
    "PVT": _formula_node("test_accumulation_distribution_and_chaikin_and_pvt_match_oracle"),
    "VWAP_SESSION_PROXY": _formula_node("test_vwap_proxy_and_mfi_match_oracle"),
    "MFI": _formula_node("test_vwap_proxy_and_mfi_match_oracle"),
    "ROLLING_SLOPE": _formula_node("test_rolling_slope_matches_oracle"),
    "TRUE_RANGE": _formula_node("test_true_range_matches_oracle"),
}


@lru_cache(maxsize=1)
def _formula_source_evidence() -> dict:
    """从公式 oracle 测试源码提取每项实际取值，防止静态证据声明漂移。"""
    tree = ast.parse((REPO_ROOT / FORMULA_MODULE).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    evidence = {}
    for indicator_id, nodeid in FORMULA_NODEIDS.items():
        func_name = nodeid.split("::", 1)[1]
        func = functions.get(func_name)
        if func is None:
            raise CollectionError(f"FORMULA_TEST_NOT_FOUND:{indicator_id}:{func_name}")
        implementation = indicator_id.lower()
        parameter_values = {}
        for decorator in func.decorator_list:
            if (not isinstance(decorator, ast.Call)
                    or not isinstance(decorator.func, ast.Attribute)
                    or decorator.func.attr != "parametrize"):
                continue
            names = ast.literal_eval(decorator.args[0]).split(",")
            if len(names) != 1:
                raise CollectionError(f"FORMULA_PARAMETRIZE_UNSUPPORTED:{indicator_id}")
            try:
                parameter_values[names[0].strip()] = ast.literal_eval(decorator.args[1])
            except (ValueError, TypeError):
                # 形状参数可由模块常量给出；仅用于输出取值的参数必须能静态确定。
                pass

        calls = [node for node in ast.walk(func) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == implementation]
        bound_names = {target.id for node in ast.walk(func)
                       if isinstance(node, ast.Assign)
                       and isinstance(node.value, ast.Call)
                       and node.value in calls
                       for target in node.targets if isinstance(target, ast.Name)}
        # 只计入进入逐值 assert_allclose 的 .value()；单独读取输出不作数值证明。
        asserted_values = set()
        asserted_names = set()
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "assert_allclose"):
                asserted_values.update(child for child in ast.walk(node)
                                       if isinstance(child, ast.Call))
                asserted_names.update(child.id for child in ast.walk(node)
                                      if isinstance(child, ast.Name))
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in asserted_names
                    for target in node.targets):
                asserted_values.update(child for child in ast.walk(node.value)
                                       if isinstance(child, ast.Call))
        outputs = []
        for node in ast.walk(func):
            if (not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr != "value" or len(node.args) != 1
                    or node not in asserted_values):
                continue
            receiver = node.func.value
            belongs = (isinstance(receiver, ast.Call) and receiver in calls
                       or isinstance(receiver, ast.Name) and receiver.id in bound_names)
            if belongs and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                value = node.args[0].value
                if value not in outputs:
                    outputs.append(value)

        parameter_sets = []
        for call in calls:
            keys = []
            alternatives = []
            for keyword in call.keywords:
                if keyword.arg is None:
                    raise CollectionError(f"FORMULA_PARAMS_UNRESOLVED:{indicator_id}")
                keys.append(keyword.arg)
                if isinstance(keyword.value, ast.Name):
                    values = parameter_values.get(keyword.value.id)
                    if values is None:
                        raise CollectionError(f"FORMULA_PARAMS_UNRESOLVED:{indicator_id}:{keyword.arg}")
                    alternatives.append(values)
                else:
                    try:
                        alternatives.append([ast.literal_eval(keyword.value)])
                    except (ValueError, TypeError) as exc:
                        raise CollectionError(
                            f"FORMULA_PARAMS_UNRESOLVED:{indicator_id}:{keyword.arg}") from exc
            for combination in product(*alternatives):
                item = dict(zip(keys, combination))
                if item not in parameter_sets:
                    parameter_sets.append(item)
        if not calls or not outputs or not parameter_sets:
            raise CollectionError(f"FORMULA_SOURCE_EVIDENCE_MISSING:{indicator_id}")
        evidence[indicator_id] = {"outputs": outputs,
                                  "tested_parameter_sets": parameter_sets}
    return evidence


def _check_formula_declarations() -> None:
    """任一静态声明与测试源码不符时拒绝签发证据。"""
    for indicator_id, source in _formula_source_evidence().items():
        declared = VALIDATED_BY_DIMENSION.get((indicator_id, "formula"), {})
        if set(declared.get("outputs", [])) != set(source["outputs"]) or \
                declared.get("tested_parameter_sets") != source["tested_parameter_sets"]:
            raise CollectionError(f"FORMULA_DECLARATION_DRIFT:{indicator_id}:"
                                  f"declared={declared}:source={source}")


def _collect_count(rel_path: str) -> int:
    """用 pytest --collect-only 实际计数（不硬编码）。

    收集失败（非零退出、无计数行、解析异常）必须**明确报错**，
    不得返回 0 并继续签发证据。
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", rel_path, "--collect-only", "-q"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise CollectionError(
            f"COLLECTION_FAILED:{rel_path}:rc={completed.returncode}:"
            f"{(completed.stdout or completed.stderr).strip()[:200]}")
    for line in completed.stdout.splitlines():
        if "tests collected" in line or "test collected" in line:
            token = line.strip().split(" ")[0]
            digits = "".join(ch for ch in token if ch.isdigit())
            if digits:
                return int(digits)
    raise CollectionError(
        f"COLLECTION_COUNT_NOT_PARSED:{rel_path}:"
        f"{completed.stdout.strip()[:200]}")


def _junit_counts(path: Path) -> dict:
    """从 JUnit XML 读实际计数（含 errors 与 failures 分开）。

    文件缺失时返回 NOT_ESTABLISHED 标记，不返回伪造的 0。
    """
    if not path.exists():
        return {"status": "NOT_ESTABLISHED", "reason": f"missing:{path.name}"}
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") or [root]
    total = errors = failures = skipped = 0
    for suite in suites:
        total += int(suite.get("tests", 0))
        errors += int(suite.get("errors", 0))
        failures += int(suite.get("failures", 0))
        skipped += int(suite.get("skipped", 0))
    return {
        "status": "ESTABLISHED",
        "tests": total,
        "errors": errors,
        "failures": failures,
        "skipped": skipped,
        "passed": total - errors - failures - skipped,
        "failed_including_errors": errors + failures,
    }


# outcome 严重度：任一参数化用例非 passed 都不能判 VERIFIED
_OUTCOME_SEVERITY = {"passed": 0, "skipped": 1, "error": 2, "failed": 3}


def _case_outcome(case) -> str:
    """由 JUnit testcase 的子元素判定 outcome。"""
    for tag, outcome in (("failure", "failed"), ("error", "error"),
                         ("skipped", "skipped")):
        if case.find(tag) is not None:
            return outcome
    return "passed"


def _merge_worst(outcomes: dict, key: str, outcome: str) -> None:
    """按**最差** outcome 合并同一精确节点或去参数键。

    否则重复 testcase 或兄弟参数中的 passed 可能掩盖失败/跳过，
    使整维度被误判 VERIFIED。
    """
    previous = outcomes.get(key)
    if previous is None or _OUTCOME_SEVERITY.get(outcome, 0) > \
            _OUTCOME_SEVERITY.get(previous, 0):
        outcomes[key] = outcome


def parse_junit_outcomes(path: Path) -> dict:
    """解析 JUnit，返回 {nodeid: outcome}。

    outcome ∈ {passed, failed, error, skipped}。
    参数化用例同时记录**带参数**与**去参数**两种键，便于把映射的基名
    匹配到实际的参数化 testcase 集合。
    """
    if not path.exists():
        raise CollectionError(f"JUNIT_MISSING:{path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise CollectionError(f"JUNIT_CORRUPT:{path}:{exc}") from exc

    outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        module_path = classname.replace(".", "/") + ".py"
        outcome = _case_outcome(case)
        _merge_worst(outcomes, f"{module_path}::{name}", outcome)
        if "[" in name:
            _merge_worst(outcomes, f"{module_path}::{name.split('[', 1)[0]}", outcome)
    return outcomes


_COLLECT_CACHE: dict = {}


def collect_parameterized_nodeids(module: str, func: str,
                                  group_by_indicator: bool = True) -> dict:
    """收集某测试函数的**实际参数化 nodeid**。

    ``group_by_indicator=True``：按参数首段（指标 ID）分组，用于条件/账户套件
    （参数形如 ``[DEMA-dema-10.0]``）。
    ``group_by_indicator=False``：返回 ``{"_all": [...]}``，用于公式套件
    （参数形如 ``[5-flat]``，首段是 window 而非指标名）。

    逐项作证的依据：每个指标只能引用**属于它自己**的参数化 nodeid，
    不能借整个函数的参数集合作证（复核 PR16-03 明确要求）。
    """
    cache_key = (module, func, group_by_indicator)
    if cache_key in _COLLECT_CACHE:
        return _COLLECT_CACHE[cache_key]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", module, "--collect-only", "-q"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise CollectionError(
            f"COLLECTION_FAILED:{module}:rc={completed.returncode}:"
            f"{(completed.stdout or completed.stderr).strip()[:200]}")
    prefix = f"{module}::{func}["
    plain = f"{module}::{func}"
    grouped: dict = {}
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line == plain:
            grouped.setdefault("_plain", []).append(line)
            continue
        if not line.startswith(prefix):
            continue
        params = line[len(prefix):-1]
        key = params.split("-", 1)[0] if group_by_indicator else "_all"
        grouped.setdefault(key, []).append(line)
    _COLLECT_CACHE[cache_key] = grouped
    return grouped


_COVERAGE_CACHE: dict = {}


def build_expected_coverage() -> dict:
    """建立**期望覆盖集合**：每个指标在每个维度上应被哪些 nodeid 覆盖。

    公式维度：该指标映射函数下的全部参数化节点。
    条件/账户维度：该指标自己的参数化节点（首段是指标 ID）。
    """
    if _COVERAGE_CACHE:
        return _COVERAGE_CACHE
    formula_cache: dict = {}
    condition_map = collect_parameterized_nodeids(*CONDITION_TEST)
    account_map = collect_parameterized_nodeids(*ACCOUNT_TEST)

    expected: dict = {}
    for indicator_id, base in FORMULA_NODEIDS.items():
        func_name = base.split("::", 1)[1]
        if func_name not in formula_cache:
            formula_cache[func_name] = collect_parameterized_nodeids(
                FORMULA_MODULE, func_name, group_by_indicator=False)
        nodes = list(formula_cache[func_name].get("_all", []))
        if not nodes and base in formula_cache[func_name].get("_plain", []):
            nodes = [base]
        expected[(indicator_id, "formula")] = sorted(set(nodes))
        # 条件维度：参数化用例 + 该指标的单独测试（若有）
        condition_nodes = set(condition_map.get(indicator_id, []))
        single = SINGLE_CONDITION_TESTS.get(indicator_id)
        if single:
            condition_nodes.add(single)
        expected[(indicator_id, "condition")] = sorted(condition_nodes)
        expected[(indicator_id, "account")] = sorted(set(
            account_map.get(indicator_id, [])))
    _COVERAGE_CACHE.update(expected)
    return expected


def resolve_dimension(expected: list, outcomes: dict) -> dict:
    """由**期望覆盖集合**的实际 JUnit outcome 推导维度状态。

    fail closed 规则（复核 PR16-03）：
    - 期望集合为空（映射缺失 / 该指标无对应 testcase）→ PARTIAL；
    - 任一期望节点缺失、failed、error、skipped → PARTIAL；
    - 只有**全部**期望节点 passed 才 VERIFIED。
    不得"只剩一条 passed 就整体通过"。
    """
    if not expected:
        return {"status": "PARTIAL", "outcome": "no_expected_coverage",
                "matched": "", "expected": [], "missing": []}
    missing = [n for n in expected if n not in outcomes]
    if missing:
        return {"status": "PARTIAL", "outcome": "missing_testcase",
                "matched": "", "expected": expected, "missing": missing}
    worst = max(expected, key=lambda n: _OUTCOME_SEVERITY.get(outcomes[n], 0))
    worst_outcome = outcomes[worst]
    return {
        "status": "VERIFIED" if worst_outcome == "passed" else "PARTIAL",
        "outcome": worst_outcome,
        "matched": worst,
        "expected": expected,
        "missing": [],
    }


@lru_cache(maxsize=1)
def _account_parameter_sets() -> dict:
    """从账户测试的实际参数化用例提取指标配置；不借用注册默认值。"""
    source = (REPO_ROOT / CONDITION_MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or \
                node.name != ACCOUNT_TEST[1]:
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) \
                    and decorator.func.attr == "parametrize":
                names = ast.literal_eval(decorator.args[0]).split(",")
                if [name.strip() for name in names] != ["indicator_id", "params", "condition"]:
                    continue
                cases = ast.literal_eval(decorator.args[1])
                return {indicator_id: dict(params) for indicator_id, params, _ in cases}
    raise CollectionError("ACCOUNT_PARAMETER_SOURCE_NOT_FOUND")


def _passed_parameter_sets(candidates: list, passed_nodeids: list) -> list:
    """只列实际 passed 的参数组合；多窗口用例按 collection 的参数 ID 收缩。"""
    if not passed_nodeids:
        return []
    if len(candidates) <= 1:
        return [dict(item) for item in candidates]
    return [dict(item) for item in candidates if "window" in item and any(
        f"[{item['window']}-" in nodeid or f"[{item['window']}]" in nodeid
        for nodeid in passed_nodeids)]


def _indicator_evidence(outcomes: dict, junit_name: str = TARGET_JUNIT_NAME) -> list:
    """为新增 oracle 覆盖的指标建立逐项证据行。

    每个能力证据写：指标、实现版本、具体输出、实际测试参数域、输入/价格域、
    **属于该指标的完整参数化 nodeid**、实际 JUnit、passed/skip/error、证明强度。

    逐项适用（复核 PR16-03）：
    - condition/account **不**共用去参数函数基名；
    - 每项绑定自己的参数化 nodeid 与期望覆盖集合；
    - 参数/输出范围只来自对应测试断言，不从注册表全部输出扩大。
    证明强度分级：
    - ``NUMERIC_ORACLE``：与独立朴素参考逐值比较；
    - ``CONDITION_INJECTED``：手工注入条件上下文（非真实注册计算）；
    - ``ACCOUNT_WIDE_THRESHOLD``：宽阈值成交（不证明阈值敏感）。
    """
    _check_formula_declarations()
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    expected_coverage = build_expected_coverage()
    account_params = _account_parameter_sets()
    strength_by_dim = {
        "formula": "NUMERIC_ORACLE",
        "condition": "CONDITION_INJECTED",
        "account": "ACCOUNT_WIDE_THRESHOLD",
    }
    rows = []
    for indicator_id in NEW_ORACLE_INDICATORS:
        try:
            spec = registry.get(indicator_id)
        except Exception:
            rows.append({"indicator": indicator_id, "status": "PARTIAL",
                         "reason": "NOT_IN_REGISTRY"})
            continue
        dims = {}
        for name in ("formula", "condition", "account"):
            expected = expected_coverage.get((indicator_id, name), [])
            resolved = resolve_dimension(expected, outcomes)
            resolved["nodeids"] = expected
            resolved["junit"] = junit_name
            resolved["strength"] = strength_by_dim[name]
            # 源码断言范围与本次通过范围分开；缺失/失败的旧 JUnit 不得签发。
            validated = VALIDATED_BY_DIMENSION.get((indicator_id, name), {})
            asserted_outputs = list(validated.get("outputs", []))
            resolved["asserted_outputs"] = asserted_outputs
            resolved["validated_outputs"] = (asserted_outputs if resolved["status"] == "VERIFIED"
                                             else [])
            candidates = ([account_params[indicator_id]] if name == "account"
                          else validated.get("tested_parameter_sets", []))
            passed = [node for node in expected if outcomes.get(node) == "passed"]
            resolved["tested_parameter_sets"] = _passed_parameter_sets(candidates, passed)
            # 该维度注册但未被断言的输出，如实列出（供复核对照）
            registered = list(spec.outputs)
            resolved["unvalidated_registered_outputs"] = [
                out for out in registered if out not in resolved["validated_outputs"]]
            dims[name] = resolved
        rows.append({
            "indicator": indicator_id,
            "version": spec.version,
            # registered_* 是实现清单，不是已验证范围
            "registered_outputs": list(spec.outputs),
            "registered_params": dict(spec.params),
            "input_domain": list(spec.inputs),
            "price_mode": spec.price_mode,
            "warmup_bars": spec.warmup_bars,
            "unit": spec.unit,
            "dimensions": dims,
            "status": "VERIFIED" if all(d["status"] == "VERIFIED" for d in dims.values())
                      else "PARTIAL",
        })
    return rows



def node_diff_vs_base() -> dict:
    """按一致隔离环境做 BASE/HEAD 节点差集。

    需要 tmp/base_nodes.txt 与 tmp/head_nodes.txt（由调用方在等价隔离环境中
    用 pytest --collect-only 生成）。缺失时返回 NOT_ESTABLISHED，不伪造。
    """
    base_file = REPO_ROOT / "tmp" / "base_nodes.txt"
    head_file = REPO_ROOT / "tmp" / "head_nodes.txt"
    identity_file = REPO_ROOT / "tmp" / "node_diff_identity.json"
    if not base_file.exists() or not head_file.exists():
        return {"status": "NOT_ESTABLISHED",
                "reason": "missing node listing (base_nodes.txt / head_nodes.txt)"}
    if not identity_file.exists():
        return {"status": "NOT_ESTABLISHED",
                "reason": "node listings have no source identity manifest"}
    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "NOT_ESTABLISHED", "reason": "invalid node identity manifest"}
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                              capture_output=True, text=True).stdout.strip()
    checksums = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for name, path in (("base", base_file), ("head", head_file))}
    if (identity.get("base_sha") != BASE_SHA or identity.get("head_sha") != head_sha
            or identity.get("node_sha256") != checksums):
        return {"status": "NOT_ESTABLISHED", "reason": "node listing identity mismatch"}
    base = {l.strip() for l in base_file.read_text(encoding="utf-8").splitlines() if l.strip()}
    head = {l.strip() for l in head_file.read_text(encoding="utf-8").splitlines() if l.strip()}
    return {
        "status": "ESTABLISHED",
        "base_nodes": len(base),
        "head_nodes": len(head),
        "added": len(head - base),
        "removed": len(base - head),
        "scope": "FULL_BASE_HEAD_NODE_DIFF",
    }


def resolve_within_repo(candidate: str, *, label: str) -> Path:
    """把 CLI 传入的路径解析并**限制在仓库根内**，拒绝路径穿越。

    安全说明：``--junit``/``--out`` 是外部输入，直接当作文件路径会产生
    路径穿越（Sonar ``pythonsecurity:S8707``）。本函数是唯一允许把外部
    字符串转成文件路径的入口：解析后必须落在 ``REPO_ROOT`` 之内。

    另拒绝含盘符的 Windows 风格写法（如 ``E:/x``）：在 POSIX 上它会被
    当作仓库内的相对路径，语义易混淆，直接拒绝更安全。
    """
    if _DRIVE_PREFIX_RE.match(candidate.strip()):
        raise ValueError(f"{label}_DRIVE_PATH_NOT_ALLOWED:{candidate}")
    root = REPO_ROOT.resolve()
    raw = Path(candidate)
    resolved = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"{label}_PATH_OUTSIDE_REPO:{candidate}") from None
    return resolved


def source_identity() -> dict:
    """把 commit 身份与未提交的源码/测试状态分开，避免旧 HEAD 冒充执行源码。"""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", "HEAD", "--", "src", "scripts", "tests",
         ".github/workflows"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True).stdout
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "src", "scripts", "tests", ".github/workflows"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True).stdout.strip()
    return {"head": head, "code_test_tree_sha256": hashlib.sha256(tree.encode()).hexdigest(),
            "status": "UNCOMMITTED_SOURCE_CHANGES" if changed else "COMMITTED_SOURCE"}


def junit_source_binding(path: Path, current: dict) -> dict:
    """核对 JUnit 中测试进程记录的源码树；缺失或不一致不得认证。"""
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") or [root]
    required = {"price_only_source_head", "price_only_code_test_tree_sha256",
                "price_only_source_status"}
    runs = []
    for suite in suites:
        properties = suite.find("properties")
        entries = [] if properties is None else properties.findall("property")
        names = [prop.get("name") for prop in entries]
        values = {prop.get("name"): prop.get("value") for prop in entries}
        if len(names) != len(set(names)):
            return {"status": "NOT_ESTABLISHED", "reason": "duplicate_junit_run_source_identity"}
        if not required <= values.keys() or any(not values[name] for name in required):
            return {"status": "NOT_ESTABLISHED", "reason": "missing_junit_run_source_identity"}
        runs.append(tuple(values[name] for name in sorted(required)))
    if not runs or len(set(runs)) != 1:
        return {"status": "NOT_ESTABLISHED", "reason": "inconsistent_junit_run_source_identity"}
    run = dict(zip(sorted(required), runs[0]))
    result = {
        "run_head": run["price_only_source_head"],
        "run_code_test_tree_sha256": run["price_only_code_test_tree_sha256"],
        "run_source_status": run["price_only_source_status"],
    }
    if run["price_only_source_status"] != "COMMITTED_SOURCE" or \
            current["status"] != "COMMITTED_SOURCE":
        return {"status": "NOT_ESTABLISHED", "reason": "uncommitted_source", **result}
    if run["price_only_code_test_tree_sha256"] != current["code_test_tree_sha256"]:
        return {"status": "NOT_ESTABLISHED", "reason": "code_test_tree_mismatch", **result}
    return {"status": "MATCHED", **result}


def bound_indicator_evidence(outcomes: dict, path: Path, current: dict,
                             junit_name: str) -> tuple[list, dict]:
    """仅使用绑定到当前源码的 JUnit outcome 签发逐项证据。"""
    binding = junit_source_binding(path, current)
    rows = _indicator_evidence(outcomes if binding["status"] == "MATCHED" else {},
                               junit_name)
    return rows, binding


def main(argv: list | None = None) -> int:
    """生成证据。

    ``--junit PATH``：指定消费的 JUnit（CI 传入本次实际产出）；
    ``--out PATH``：指定输出路径。默认消费仓库内已提交的 JUnit。
    两个路径都必须位于仓库根内。
    """
    import argparse

    parser = argparse.ArgumentParser(description="从实际 JUnit 推导能力证据")
    parser.add_argument("--junit", default=None,
                        help="消费的 JUnit 路径（默认 reports/junit-price-only-v1.xml）")
    parser.add_argument("--out", default=None, help="输出 JSON 路径")
    # 显式传入 argv 才解析；无参时用空列表，避免读到宿主进程的 argv
    args = parser.parse_args(argv if argv is not None else [])

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        junit_path = (resolve_within_repo(args.junit, label="junit") if args.junit
                      else (REPO_ROOT / "reports" / TARGET_JUNIT_NAME))
        out_path = (resolve_within_repo(args.out, label="out") if args.out
                    else (OUT_DIR / "EVIDENCE_COUNTS_V1.json"))
    except ValueError as exc:
        print(f"NOT_ESTABLISHED: {exc}", file=sys.stderr)
        return 2

    try:
        outcomes = parse_junit_outcomes(junit_path)
        counts = {name: _collect_count(path) for name, path in NEW_TEST_FILES.items()}
        qfq_synthetic = _collect_count(QFQ_SYNTHETIC_FILE)
    except CollectionError as exc:
        # 收集/解析失败必须明确报错，不得返回 0 并继续签证
        print(f"NOT_ESTABLISHED: {exc}", file=sys.stderr)
        return 2
    parts_total = sum(counts.values())
    new_total = parts_total + qfq_synthetic

    local_junit = _junit_counts(junit_path)
    current_source = source_identity()
    try:
        rows, binding = bound_indicator_evidence(
            outcomes, junit_path, current_source, junit_path.relative_to(REPO_ROOT).as_posix())
    except CollectionError as exc:
        print(f"NOT_ESTABLISHED: {exc}", file=sys.stderr)
        return 2
    verified = sum(1 for r in rows if r.get("status") == "VERIFIED")
    partial = sum(1 for r in rows if r.get("status") == "PARTIAL")

    payload = {
        "generated_from": "实际 pytest --collect-only 与 JUnit testcase outcome",
        "junit_consumed": junit_path.relative_to(REPO_ROOT).as_posix(),
        "junit_sha256": hashlib.sha256(junit_path.read_bytes()).hexdigest(),
        "note": "计数与状态均由脚本自动派生，不硬编码；本机与 CI 分列。"
                "汇总成功不覆盖单项失败。",
        "source_identity": current_source,
        "junit_source_binding": binding,
        "new_tests": {
            "by_part": counts,
            "parts_total": parts_total,
            "qfq_synthetic": qfq_synthetic,
            "total_vs_base": new_total,
            "note": "各组成部分合计 + 合成 QFQ；25 个账户服务用例属于 "
                    "condition_and_service 内部，不重复累加。"
                    "本清单为**子集**口径（本轮新增文件），非 BASE/HEAD 节点差集全量。",
            "scope": "SUBSET_OF_NEW_FILES_NOT_FULL_BASE_HEAD_DIFF",
        },
        "node_diff_vs_base": node_diff_vs_base(),
        "local_target_suite": local_junit,
        "local_full_suite": {"status": "NOT_RUN_AT_SOURCE_HEAD",
                             "note": "历史完整套件 JUnit 未绑定本次源码，不作为本次回归结论"},
        "accounting_rules": {
            "skipped_is_not_passed": True,
            "errors_and_failures_separated": True,
            "causality": "UNCONFIRMED",
            "status_derived_from_junit_outcome": True,
        },
        "indicator_evidence": rows,
        "summary": {"verified": verified, "partial": partial, "total": len(rows)},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target = out_path
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("junit outcomes parsed:", len(outcomes))
    print("组成部分:", counts)
    print("组成部分合计:", parts_total)
    print("合成 QFQ:", qfq_synthetic)
    print("相对 BASE 新增(子集口径):", new_total)
    print("本机目标套件:", local_junit)
    print("verified/partial/total:", verified, partial, len(rows))
    print("输出:", target)
    return 0 if binding["status"] == "MATCHED" else 2


if __name__ == "__main__":
    # 直接运行时才解析命令行；被 import（如测试）时 main() 默认不读宿主 argv
    raise SystemExit(main(sys.argv[1:]))
