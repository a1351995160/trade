"""证据生成器输入变异测试：状态必须由真实判定逻辑推导。

复核要求（PR16-03）必须变异**输入**、跑**真实整个生成逻辑**：
1. 删 DEMA condition/account 节点 → DEMA 降级，未受影响项保持原状态；
2. DEMA 仅留 [5-flat] → 不得继续签 window20 或其它未覆盖输出；
3. DEMA 账户改 failure/error/skip → 只有对应维度降级；
4. 删除期望参数节点、错指标节点、缺 JUnit、错源码身份 → 均不能 VERIFIED；
5. 实际有效输入仍能通过（不把所有行一律降级来凑结果）。

**禁止**：先改最终 ``row['status']='PARTIAL'`` 再断言它等于 PARTIAL；
也不能替换整个判定函数手工返回预期答案。

本模块通过修改**输入**（期望覆盖集合 / JUnit 内容）后调用真实的
``resolve_dimension`` / ``_indicator_evidence``，观察推导结果变化。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCRIPT = REPO_ROOT / "scripts" / "emit_price_only_evidence_v1.py"


_MODULE_CACHE: dict = {}


def _load_module():
    """加载生成器模块（**进程内只加载一次**）。

    生成器的期望覆盖集合需要跑 pytest --collect-only，代价高；
    重复加载会重置其内部缓存，使每个用例都重跑收集。这里做模块级复用。
    """
    if "module" not in _MODULE_CACHE:
        spec = importlib.util.spec_from_file_location("emit_evidence_v1", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE_CACHE["module"] = module
    return _MODULE_CACHE["module"]


def _write_junit(path: Path, cases: list, properties: dict | None = None) -> None:
    """写一个最小 JUnit，cases 为 (classname, name, kind)。"""
    suite = ET.Element("testsuite", {
        "name": "pytest", "tests": str(len(cases)),
        "failures": str(sum(1 for c in cases if c[2] == "failure")),
        "errors": str(sum(1 for c in cases if c[2] == "error")),
        "skipped": str(sum(1 for c in cases if c[2] == "skipped")),
    })
    if properties is not None:
        props = ET.SubElement(suite, "properties")
        for name, value in properties.items():
            ET.SubElement(props, "property", {"name": name, "value": value})
    for classname, name, kind in cases:
        case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
        if kind != "passed":
            ET.SubElement(case, kind)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _node_to_case(nodeid: str, kind: str = "passed"):
    module_path, func_name = nodeid.split("::")
    return (module_path[:-3].replace("/", "."), func_name, kind)


def _all_passing_junit(tmp_path: Path) -> Path:
    """用**真实 collection** 生成一份全 passed 的 JUnit。"""
    coverage = _real_coverage()
    cases = [_node_to_case(n) for nodes in coverage.values() for n in nodes]
    junit = tmp_path / "all_passing.xml"
    _write_junit(junit, cases)
    return junit


def _real_coverage():
    """真实期望覆盖集合（进程内缓存，只计算一次）。"""
    if "coverage" not in _MODULE_CACHE:
        _MODULE_CACHE["coverage"] = _load_module().build_expected_coverage()
    return _MODULE_CACHE["coverage"]


def _passing_rows(tmp_path: Path):
    module = _load_module()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    return module._indicator_evidence(outcomes)


# ==========================================================================
# 1) 删 DEMA condition/account 节点 → DEMA 降级，其它项不受影响
# ==========================================================================
def test_removing_dema_condition_account_downgrades_only_dema(tmp_path: Path):
    """删 DEMA 的条件/账户期望节点：DEMA 降级，PSY 等保持原状态。"""
    module = _load_module()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    coverage = _real_coverage()

    before_dema = module.resolve_dimension(coverage[("DEMA", "condition")], outcomes)
    before_psy = module.resolve_dimension(coverage[("PSY", "account")], outcomes)
    assert before_dema["status"] == "VERIFIED"
    assert before_psy["status"] == "VERIFIED"

    mutated = dict(coverage)
    mutated[("DEMA", "condition")] = []
    mutated[("DEMA", "account")] = []

    after_dema_cond = module.resolve_dimension(mutated[("DEMA", "condition")], outcomes)
    after_dema_acct = module.resolve_dimension(mutated[("DEMA", "account")], outcomes)
    after_psy = module.resolve_dimension(mutated[("PSY", "account")], outcomes)

    assert after_dema_cond["status"] == "PARTIAL", "删除条件节点未降级"
    assert after_dema_acct["status"] == "PARTIAL", "删除账户节点未降级"
    assert after_psy == before_psy, "无关项被误伤"


# ==========================================================================
# 2) DEMA 仅留 [5-flat] → 不得继续签其它 window
# ==========================================================================
def test_partial_coverage_does_not_verify_whole_function(tmp_path: Path):
    """只剩一个参数节点时，证据范围必须收缩到该节点，不得整体通过。"""
    module = _load_module()
    coverage = _real_coverage()
    dema_nodes = coverage[("DEMA", "formula")]
    assert len(dema_nodes) > 1, "前置：DEMA 公式应有多个参数节点"

    single = [n for n in dema_nodes if "[5-flat]" in n]
    assert single, "未找到 [5-flat] 节点"

    junit = tmp_path / "single.xml"
    _write_junit(junit, [_node_to_case(single[0])])
    outcomes = module.parse_junit_outcomes(junit)

    result = module.resolve_dimension(single, outcomes)
    assert result["expected"] == single, "证据未收缩到实际覆盖范围"
    assert not any("[20-" in n for n in result["expected"]), \
        "仍声称覆盖了未测参数"
    row = next(r for r in module._indicator_evidence(outcomes) if r["indicator"] == "DEMA")
    formula = row["dimensions"]["formula"]
    assert formula["status"] == "PARTIAL"
    assert formula["tested_parameter_sets"] == [{"window": 5}]
    assert formula["validated_outputs"] == []


def test_unexpected_node_in_junit_does_not_expand_coverage(tmp_path: Path):
    """JUnit 里多出的无关参数不得扩大证据范围。"""
    module = _load_module()
    coverage = _real_coverage()
    single = [n for n in coverage[("DEMA", "formula")] if "[5-flat]" in n]
    module_path, func_name = single[0].split("::")
    classname = module_path[:-3].replace("/", ".")

    junit = tmp_path / "extra.xml"
    _write_junit(junit, [
        (classname, func_name, "passed"),
        (classname, "test_dema_matches_oracle[999-uncovered]", "passed"),
    ])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(single, outcomes)
    assert result["expected"] == single
    assert not any("999" in n for n in result["expected"])


# ==========================================================================
# 3) DEMA 账户改 failure/error/skip → 只有该维度降级
# ==========================================================================
@pytest.mark.parametrize("kind", ["failure", "error", "skipped"])
def test_account_outcome_mutation_downgrades_only_account(tmp_path: Path, kind: str):
    """DEMA 账户节点改 failure/error/skip：账户维度降级，公式维度不受影响。"""
    module = _load_module()
    coverage = _real_coverage()
    acct_nodes = coverage[("DEMA", "account")]
    assert acct_nodes, "前置：DEMA 应有账户节点"

    cases = [_node_to_case(n) for nodes in coverage.values() for n in nodes]
    cases.append(_node_to_case(acct_nodes[0], kind))
    junit = tmp_path / "acct.xml"
    _write_junit(junit, cases)
    outcomes = module.parse_junit_outcomes(junit)

    acct = module.resolve_dimension(acct_nodes, outcomes)
    formula = module.resolve_dimension(coverage[("DEMA", "formula")], outcomes)
    assert acct["status"] == "PARTIAL", f"{kind} 未使账户维度降级"
    assert formula["status"] == "VERIFIED", "公式维度被误伤"


@pytest.mark.parametrize("failure_first", [True, False])
def test_duplicate_exact_node_keeps_worst_outcome(tmp_path: Path, failure_first: bool):
    """同一精确 nodeid 的失败无论出现顺序如何都不能被通过记录覆盖。"""
    module = _load_module()
    coverage = _real_coverage()
    node = next(n for n in coverage[("DEMA", "condition")]
                if "test_condition_layer_consumes_each_new_indicator[DEMA-" in n)
    cases = [_node_to_case(n) for nodes in coverage.values() for n in nodes]
    duplicate = _node_to_case(node, "failure")
    if failure_first:
        cases.insert(0, duplicate)
    else:
        cases.append(duplicate)
    junit = tmp_path / "duplicate.xml"
    current = {"head": "synthetic-head", "code_test_tree_sha256": "synthetic-tree",
               "status": "COMMITTED_SOURCE"}
    properties = {
        "price_only_source_head": current["head"],
        "price_only_code_test_tree_sha256": current["code_test_tree_sha256"],
        "price_only_source_status": current["status"],
    }
    _write_junit(junit, cases, properties)

    outcomes = module.parse_junit_outcomes(junit)
    rows, binding = module.bound_indicator_evidence(
        outcomes, junit, current, "duplicate.xml")
    dema = next(row for row in rows if row["indicator"] == "DEMA")
    assert binding["status"] == "MATCHED"
    assert outcomes[node] == "failed"
    assert outcomes[node.split("[", 1)[0]] == "failed"
    assert dema["dimensions"]["condition"]["outcome"] == "failed"
    assert dema["dimensions"]["condition"]["status"] == "PARTIAL"
    assert dema["status"] == "PARTIAL"
    assert next(row for row in rows if row["indicator"] == "TEMA")["status"] == "VERIFIED"


# ==========================================================================
# 4) 缺期望节点 / 错指标节点 / 缺 JUnit → 均不能 VERIFIED
# ==========================================================================
def test_missing_expected_node_does_not_verify(tmp_path: Path):
    """期望节点在 JUnit 中缺失 → PARTIAL。"""
    module = _load_module()
    coverage = _real_coverage()
    nodes = coverage[("CCI", "formula")]
    junit = tmp_path / "empty.xml"
    _write_junit(junit, [])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodes, outcomes)
    assert result["status"] == "PARTIAL"
    assert result["outcome"] == "missing_testcase"


def test_wrong_indicator_node_does_not_verify(tmp_path: Path):
    """只提供别的指标的节点 → 不得 VERIFIED。"""
    module = _load_module()
    coverage = _real_coverage()
    cci_nodes = coverage[("CCI", "formula")]
    dema_nodes = coverage[("DEMA", "formula")]
    junit = tmp_path / "wrong.xml"
    _write_junit(junit, [_node_to_case(n) for n in dema_nodes])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(cci_nodes, outcomes)
    assert result["status"] == "PARTIAL", "错指标节点被误判通过"


def test_missing_junit_raises_not_established(tmp_path: Path):
    """JUnit 缺失必须明确失败。"""
    module = _load_module()
    with pytest.raises(module.CollectionError) as excinfo:
        module.parse_junit_outcomes(tmp_path / "nope.xml")
    assert "JUNIT_MISSING" in str(excinfo.value)


def test_corrupt_junit_raises_not_established(tmp_path: Path):
    """JUnit 损坏必须明确失败。"""
    module = _load_module()
    bad = tmp_path / "bad.xml"
    bad.write_text("<testsuite><testcase", encoding="utf-8")
    with pytest.raises(module.CollectionError) as excinfo:
        module.parse_junit_outcomes(bad)
    assert "JUNIT_CORRUPT" in str(excinfo.value)


def test_junit_identity_is_recorded():
    """证据必须记录**实际被消费的 JUnit 文件身份**。"""
    import hashlib
    import json

    payload_path = (REPO_ROOT / "reports" / "price_only_validation_v1"
                    / "EVIDENCE_COUNTS_V1.json")
    if not payload_path.exists():
        pytest.skip("证据文件尚未生成")
    data = json.loads(payload_path.read_text(encoding="utf-8"))
    assert data.get("junit_consumed"), "未记录实际消费的 JUnit 身份"
    assert data["junit_consumed"].endswith(".xml")
    consumed = REPO_ROOT / data["junit_consumed"]
    assert data["junit_sha256"] == hashlib.sha256(consumed.read_bytes()).hexdigest()
    binding = data["junit_source_binding"]
    assert binding["status"] == "MATCHED"
    assert binding["run_code_test_tree_sha256"] == \
        data["source_identity"]["code_test_tree_sha256"]


def test_old_or_unbound_junit_cannot_certify_current_source(tmp_path: Path):
    """通过的旧 JUnit 即使 nodeid 相同，也不得签出 VERIFIED。"""
    module = _load_module()
    coverage = _real_coverage()
    cases = [_node_to_case(n) for nodes in coverage.values() for n in nodes]
    junit = tmp_path / "bound.xml"
    current = {"head": "new-docs-head", "code_test_tree_sha256": "current-tree",
               "status": "COMMITTED_SOURCE"}
    scenarios = (
        (None, "NOT_ESTABLISHED"),
        ({"price_only_source_head": "old-head",
          "price_only_code_test_tree_sha256": "old-tree",
          "price_only_source_status": "COMMITTED_SOURCE"}, "NOT_ESTABLISHED"),
        ({"price_only_source_head": "current-head",
          "price_only_code_test_tree_sha256": "current-tree",
          "price_only_source_status": "UNCOMMITTED_SOURCE_CHANGES"}, "NOT_ESTABLISHED"),
        ({"price_only_source_head": "old-docs-head",
          "price_only_code_test_tree_sha256": "current-tree",
          "price_only_source_status": "COMMITTED_SOURCE"}, "MATCHED"),
    )
    for properties, expected in scenarios:
        _write_junit(junit, cases, properties)
        outcomes = module.parse_junit_outcomes(junit)
        rows, binding = module.bound_indicator_evidence(
            outcomes, junit, current, "bound.xml")
        assert binding["status"] == expected
        if expected == "MATCHED":
            assert any(row["status"] == "VERIFIED" for row in rows)
        else:
            assert all(row["status"] == "PARTIAL" for row in rows)
            assert all(not dim["validated_outputs"] and not dim["tested_parameter_sets"]
                       for row in rows for dim in row["dimensions"].values())


# ==========================================================================
# 5) 实际有效输入仍能通过（不是一律降级）
# ==========================================================================
def test_valid_input_still_verifies(tmp_path: Path):
    """正对照：有效输入下，覆盖完整的维度仍 VERIFIED。"""
    module = _load_module()
    coverage = _real_coverage()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))

    verified_dims = sum(
        1 for nodes in coverage.values()
        if nodes and module.resolve_dimension(nodes, outcomes)["status"] == "VERIFIED")
    assert verified_dims > 0, "有效输入下没有任何维度通过（一律降级）"

    partial_dims = sum(1 for nodes in coverage.values() if not nodes)
    assert partial_dims > 0, "期望覆盖集合没有缺口，无法体现诚实收缩"


def test_full_row_derivation_reflects_mutation(tmp_path: Path):
    """端到端：跑真实整表，确认状态来自 outcome 与期望覆盖。"""
    module = _load_module()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    rows = module._indicator_evidence(outcomes)
    assert rows, "无证据行"
    for row in rows:
        for dim_name, dim in row["dimensions"].items():
            assert "outcome" in dim, f"{row['indicator']}.{dim_name} 缺 outcome"
            assert "nodeids" in dim, f"{row['indicator']}.{dim_name} 缺 nodeids"
            assert dim["strength"] in {"NUMERIC_ORACLE", "CONDITION_INJECTED",
                                      "ACCOUNT_WIDE_THRESHOLD"}
    statuses = {row["status"] for row in rows}
    assert statuses <= {"VERIFIED", "PARTIAL"}


def test_empty_junit_downgrades_every_dimension(tmp_path: Path):
    """空 JUnit：所有维度都必须降级。"""
    module = _load_module()
    junit = tmp_path / "empty.xml"
    _write_junit(junit, [])
    outcomes = module.parse_junit_outcomes(junit)
    rows = module._indicator_evidence(outcomes)
    assert all(row["status"] == "PARTIAL" for row in rows), \
        "空 JUnit 下仍有 VERIFIED 行"
    assert all(not dim["validated_outputs"] and not dim["tested_parameter_sets"]
               for row in rows for dim in row["dimensions"].values())


# ==========================================================================
# CLI 路径穿越防护（保留既有回归）
# ==========================================================================
def test_cli_out_path_traversal_is_rejected(capsys):
    """CLI 路径穿越必须被拒（Sonar S8707 回归）。"""
    module = _load_module()
    code = module.main(["--out", "../../evil.json"])
    assert code != 0, "穿越路径未被拒绝"
    assert "PATH_OUTSIDE_REPO" in capsys.readouterr().err


def test_cli_junit_path_traversal_is_rejected(capsys):
    """--junit 越界路径必须被拒（用平台无关的仓库外路径）。"""
    module = _load_module()
    outside = ".." + os.sep + "evil.xml" if os.sep == "/" else "../../evil.xml"
    code = module.main(["--junit", outside])
    assert code != 0, "越界 junit 路径未被拒绝"
    assert "PATH_OUTSIDE_REPO" in capsys.readouterr().err


def test_cli_in_repo_path_is_accepted():
    """正对照：仓库内路径正常接受。"""
    module = _load_module()
    resolved = module.resolve_within_repo(
        "reports/price_only_validation_v1/EVIDENCE_COUNTS_V1.json", label="out")
    assert resolved.is_relative_to(module.REPO_ROOT.resolve())


# ==========================================================================
# PR16-B：适用性剩余（未映射 vs 未测试；注册输出 vs 已验证输出）
# ==========================================================================
def test_single_condition_tests_are_mapped_not_unmapped(tmp_path: Path):
    """既有单独条件测试必须被映射进去，不得标成"无覆盖"。"""
    module = _load_module()
    coverage = _real_coverage()
    for indicator in ("CCI", "NATR", "PSY"):
        nodes = coverage[(indicator, "condition")]
        assert nodes, f"{indicator} 的条件覆盖为空 —— 单独测试未映射"
        assert any("test_condition_layer_consumes_" in n for n in nodes), \
            f"{indicator} 未映射到其单独测试"


def test_mapped_single_test_verifies_when_passed(tmp_path: Path):
    """被映射的单独测试 passed 时，该维度可 VERIFIED。"""
    module = _load_module()
    coverage = _real_coverage()
    nodes = coverage[("CCI", "condition")]
    junit = tmp_path / "cci.xml"
    _write_junit(junit, [_node_to_case(n) for n in nodes])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodes, outcomes)
    assert result["status"] == "VERIFIED"


def test_mapped_single_test_downgrades_when_removed(tmp_path: Path):
    """删除该单独测试节点后必须降级（不是"未测试"被当成通过）。"""
    module = _load_module()
    coverage = _real_coverage()
    nodes = coverage[("CCI", "condition")]
    junit = tmp_path / "empty.xml"
    _write_junit(junit, [])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodes, outcomes)
    assert result["status"] == "PARTIAL"
    assert result["outcome"] == "missing_testcase"


def test_registered_outputs_are_not_auto_validated(tmp_path: Path):
    """注册输出不得自动成为已验证输出（DEMA 注册 3 输出，仅 dema 被断言）。"""
    module = _load_module()
    rows = _passing_rows(tmp_path)
    dema = next(r for r in rows if r["indicator"] == "DEMA")
    registered = set(dema["registered_outputs"])
    validated = set(dema["dimensions"]["formula"]["validated_outputs"])
    assert registered == {"dema", "ema1", "ema2"}, f"注册输出异常：{registered}"
    assert validated == {"dema"}, f"已验证输出被夸大：{validated}"
    assert validated < registered, "已验证输出未严格小于注册输出"


def test_registered_params_are_not_auto_tested(tmp_path: Path):
    """注册参数不得自动成为已验证参数（字段已改为 tested_parameter_sets）。"""
    module = _load_module()
    rows = _passing_rows(tmp_path)
    dema = next(r for r in rows if r["indicator"] == "DEMA")
    registered = set(dema["registered_params"])
    tested = set()
    for ps in dema["dimensions"]["formula"]["tested_parameter_sets"]:
        tested |= set(ps)
    assert tested <= registered, "已验证参数超出注册范围"
    assert "price" in registered and "price" not in tested, \
        "price 参数未被断言却出现在已验证参数中"


def test_removing_one_node_affects_only_its_dimension(tmp_path: Path):
    """删除一个精确节点只影响其真正适用的维度。"""
    module = _load_module()
    coverage = _real_coverage()
    acct_nodes = coverage[("DEMA", "account")]
    cases = [_node_to_case(n) for nodes in coverage.values() for n in nodes
             if n not in acct_nodes]
    junit = tmp_path / "no_acct.xml"
    _write_junit(junit, cases)
    outcomes = module.parse_junit_outcomes(junit)

    acct = module.resolve_dimension(acct_nodes, outcomes)
    formula = module.resolve_dimension(coverage[("DEMA", "formula")], outcomes)
    assert acct["status"] == "PARTIAL", "被删节点维度未降级"
    assert formula["status"] == "VERIFIED", "无关维度被误伤"


def test_subset_and_full_diff_are_reported_separately():
    """子集口径与 BASE/HEAD 全量差集必须分别记录。"""
    import json

    payload_path = (REPO_ROOT / "reports" / "price_only_validation_v1"
                    / "EVIDENCE_COUNTS_V1.json")
    if not payload_path.exists():
        pytest.skip("证据文件尚未生成")
    data = json.loads(payload_path.read_text(encoding="utf-8"))
    assert data["new_tests"]["scope"] == "SUBSET_OF_NEW_FILES_NOT_FULL_BASE_HEAD_DIFF"
    diff = data.get("node_diff_vs_base", {})
    assert diff.get("status") in {"ESTABLISHED", "NOT_ESTABLISHED"}
    if diff.get("status") == "NOT_ESTABLISHED":
        assert "reason" in diff, "未建立全量身份时必须给出原因"


def test_unbound_node_lists_do_not_establish_full_diff(tmp_path: Path, monkeypatch):
    module = _load_module()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "base_nodes.txt").write_text("old::test_one\n", encoding="utf-8")
    (tmp_path / "tmp" / "head_nodes.txt").write_text("new::test_two\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    result = module.node_diff_vs_base()
    assert result["status"] == "NOT_ESTABLISHED"
    assert "identity" in result["reason"]

# ==========================================================================
# PR16 证据范围修正：声明不得超过实际断言
# ==========================================================================
def test_declared_outputs_never_exceed_asserted_outputs(tmp_path: Path):
    """25 项公式声明必须等于测试源码中进入逐值断言的输出。

    复核给定反例：DONCHIAN 声明 upper/lower/middle、KELTNER 声明含 middle/atr、
    ROLLING_VOLATILITY 声明含 return、MACD_HIST_RAW 声明含 dif/dea ——
    这些输出在对应测试中**没有**被逐值断言。
    """
    module = _load_module()
    rows = _passing_rows(tmp_path)
    source = module._formula_source_evidence()
    assert {row["indicator"] for row in rows} == set(source)
    offenders = []
    for row in rows:
        indicator = row["indicator"]
        formula = row["dimensions"]["formula"]
        if set(formula["asserted_outputs"]) != set(source[indicator]["outputs"]):
            offenders.append((indicator, "formula", "not-asserted-by-source"))
        if formula["tested_parameter_sets"] != source[indicator]["tested_parameter_sets"]:
            offenders.append((indicator, "formula", "params-not-in-source"))
        for dim_name, dim in row["dimensions"].items():
            declared = set(dim["validated_outputs"])
            registered = set(row["registered_outputs"])
            if not declared <= registered:
                offenders.append((row["indicator"], dim_name, "declared>registered"))
            # 未被断言的注册输出必须出现在 unvalidated 列表里
            unvalidated = set(dim["unvalidated_registered_outputs"])
            if declared | unvalidated != registered:
                offenders.append((row["indicator"], dim_name, "partition-incomplete"))
    assert not offenders, f"输出声明与注册范围不一致：{offenders}"


def test_formula_source_drift_blocks_evidence_generation(tmp_path: Path, monkeypatch):
    """多输出声明和单组合参数均不得超出公式测试源码。"""
    module = _load_module()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    for indicator, change in (
            ("TEMA", {"outputs": ["tema", "ema1"],
                      "tested_parameter_sets": [{"window": 5}, {"window": 20}]}),
            ("MACD_HIST_RAW", {"outputs": ["hist_raw"],
                               "tested_parameter_sets": [{"fast": 10, "slow": 26,
                                                          "signal": 9}]})):
        with monkeypatch.context() as patch:
            patch.setitem(module.VALIDATED_BY_DIMENSION, (indicator, "formula"), change)
            with pytest.raises(module.CollectionError, match=f"FORMULA_DECLARATION_DRIFT:{indicator}"):
                module._indicator_evidence(outcomes)


def test_known_over_declarations_are_corrected(tmp_path: Path):
    """四个已指出例子 + 全量核对结果都必须落在实际断言范围内。"""
    module = _load_module()
    rows = {r["indicator"]: r for r in _passing_rows(tmp_path)}
    expectations = {
        "DONCHIAN": {"upper", "lower"},
        "KELTNER": {"upper", "lower"},
        "ROLLING_VOLATILITY": {"volatility"},
        "MACD_HIST_RAW": {"hist_raw"},
        "DRAWDOWN_FROM_PEAK": {"drawdown", "peak"},
        "DEMA": {"dema"},
    }
    for indicator, expected in expectations.items():
        got = set(rows[indicator]["dimensions"]["formula"]["validated_outputs"])
        assert got == expected, f"{indicator}: 声明 {got} != 实际断言 {expected}"


def test_test_parameter_sets_record_actual_values_not_names(tmp_path: Path):
    """参数证据必须记录**实际取值与组合**，不能只写参数名。"""
    module = _load_module()
    rows = {r["indicator"]: r for r in _passing_rows(tmp_path)}
    dema = rows["DEMA"]["dimensions"]["formula"]["tested_parameter_sets"]
    assert dema == [{"window": 5}, {"window": 20}], f"DEMA 参数取值记录错误：{dema}"
    # 不能是参数名列表
    assert all(isinstance(ps, dict) and ps for ps in dema), "参数证据退化为参数名"

    macd = rows["MACD_HIST_RAW"]["dimensions"]["formula"]["tested_parameter_sets"]
    assert macd == [{"fast": 12, "slow": 26, "signal": 9}], \
        f"MACD_HIST_RAW 组合参数记录错误：{macd}"

    assert rows["DEMA"]["dimensions"]["account"]["tested_parameter_sets"] == [
        {"window": 20}]
    assert rows["TRIX"]["dimensions"]["account"]["tested_parameter_sets"] == [
        {"window": 12, "signal": 9}]
    assert rows["CCI"]["dimensions"]["condition"]["tested_parameter_sets"] == [
        {"threshold": 100.0}]
    for indicator in ("VOLUME_MA", "AMOUNT_MA", "RVOL_PRIOR", "RVOL_INCL_CURRENT"):
        assert rows[indicator]["dimensions"]["formula"]["tested_parameter_sets"] == [
            {"window": 20}, {"window": 5}]


def test_dimension_records_consumed_junit_name(tmp_path: Path):
    module = _load_module()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    rows = module._indicator_evidence(outcomes, "junit/price-only-a0-windows-latest.xml")
    assert {dim["junit"] for row in rows for dim in row["dimensions"].values()} == {
        "junit/price-only-a0-windows-latest.xml"}


def test_parameter_range_is_not_inflated_from_registry_default(tmp_path: Path):
    """修改注册默认值不得扩大已有证据的参数范围。

    测试只执行 window=5/20；注册默认 window=20 不构成"全范围"证据。
    """
    module = _load_module()
    rows = {r["indicator"]: r for r in _passing_rows(tmp_path)}
    dema = rows["DEMA"]
    tested_values = {ps["window"] for ps in
                     dema["dimensions"]["formula"]["tested_parameter_sets"]}
    assert tested_values == {5, 20}, f"参数范围被夸大：{tested_values}"
    # 注册默认值存在，但不得成为已验证取值
    assert dema["registered_params"].get("window") == 20
    # 未测试的取值（如 10）不得出现在证据中
    assert 10 not in tested_values, "未测试取值被列入证据"


def test_unvalidated_output_does_not_become_verified_from_sibling_output(tmp_path: Path):
    """同指标其他输出 passed 不得让未断言输出变成已验证。

    模拟：某指标声明两个输出，但只断言其中一个 —— 未断言的那个必须留在
    unvalidated_registered_outputs，不得进入 validated_outputs。
    """
    module = _load_module()
    rows = {r["indicator"]: r for r in _passing_rows(tmp_path)}
    # KELTNER 注册 4 输出，只断言 2
    keltner = rows["KELTNER"]
    assert set(keltner["registered_outputs"]) == {"upper", "middle", "lower", "atr"}
    assert set(keltner["dimensions"]["formula"]["validated_outputs"]) == {"upper", "lower"}
    assert set(keltner["dimensions"]["formula"]["unvalidated_registered_outputs"]) == {
        "middle", "atr"}, "未断言输出被附带认证"


def test_missing_testcase_downgrades_only_that_range(tmp_path: Path):
    """删除某指标某参数的预期 testcase：该范围降级，其他合格范围不受影响。"""
    module = _load_module()
    coverage = _real_coverage()
    # 删除 DEMA 的 window=20 节点
    dema_nodes = coverage[("DEMA", "formula")]
    keep = [n for n in dema_nodes if "[20-" not in n]
    assert len(keep) < len(dema_nodes), "前置：未找到 window=20 节点"

    junit = tmp_path / "partial.xml"
    _write_junit(junit, [_node_to_case(n) for n in keep])
    outcomes = module.parse_junit_outcomes(junit)

    dema = module.resolve_dimension(keep, outcomes)
    tema_nodes = coverage[("TEMA", "formula")]
    tema = module.resolve_dimension(tema_nodes, outcomes)
    assert dema["status"] == "VERIFIED", "保留范围未通过"
    # 完整期望（含 20）必须降级
    full = module.resolve_dimension(dema_nodes, outcomes)
    assert full["status"] == "PARTIAL", "缺失节点未导致降级"
    assert tema["status"] == "PARTIAL", "无关指标被误判（TEMA 节点不在该 JUnit）"


def test_valid_primary_output_positive_control(tmp_path: Path):
    """正对照：合格主输出仍可 VERIFIED，不靠把全部状态改 PARTIAL。"""
    module = _load_module()
    coverage = _real_coverage()
    outcomes = module.parse_junit_outcomes(_all_passing_junit(tmp_path))
    dema = module.resolve_dimension(coverage[("DEMA", "formula")], outcomes)
    assert dema["status"] == "VERIFIED", "合格主输出未通过"

    rows = module._indicator_evidence(outcomes)
    verified = sum(1 for r in rows if r["status"] == "VERIFIED")
    assert verified > 0, "全部降级 —— 未保留正对照"
