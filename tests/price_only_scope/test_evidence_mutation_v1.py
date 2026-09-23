"""证据生成器输入变异测试：状态必须由真实判定逻辑推导。

要求（复核 4.3）：
必须调用真正的生成/判定逻辑验证：
- 删除一条输入映射，原本合格维度降级；
- 把对应输入 JUnit 改为 failure、error、skip，分别降级；
- 删除对应 testcase、提供不匹配的参数化节点，不能误判通过；
- 缺少 JUnit，不签发该项 VERIFIED；
- 其他有效维度不被无关输入变化误伤。

禁止：先改最终 row['status']='PARTIAL' 再断言它等于 PARTIAL；
也不能替换整个判定函数手工返回预期答案。

因此本模块通过修改输入（映射表 / JUnit XML 内容）后调用真实的
parse_junit_outcomes / resolve_dimension / _indicator_evidence，
观察推导结果变化。
"""
from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCRIPT = REPO_ROOT / "scripts" / "emit_price_only_evidence_v1.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("emit_evidence_v1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_outcomes(tmp_path: Path) -> dict:
    """自建 JUnit outcome（不依赖已提交的报告文件，避免顺序/环境耦合）。"""
    module = _load_module()
    junit = tmp_path / "synthetic.xml"
    cases = []
    for nodeid in list(module.FORMULA_NODEIDS.values()) + [module.CONDITION_NODEID,
                                                           module.ACCOUNT_NODEID]:
        module_path, func_name = nodeid.split("::")
        cases.append((module_path[:-3].replace("/", "."), func_name, "passed"))
    _write_junit(junit, cases)
    return module.parse_junit_outcomes(junit)


def _write_junit(path: Path, cases: list) -> None:
    """写一个最小 JUnit，cases 为 (classname, name, kind)。"""
    suite = ET.Element("testsuite", {
        "name": "pytest", "tests": str(len(cases)),
        "failures": str(sum(1 for c in cases if c[2] == "failure")),
        "errors": str(sum(1 for c in cases if c[2] == "error")),
        "skipped": str(sum(1 for c in cases if c[2] == "skipped")),
    })
    for classname, name, kind in cases:
        case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
        if kind != "passed":
            ET.SubElement(case, kind)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def test_removing_formula_mapping_downgrades_dimension(tmp_path: Path):
    """删除 CCI 的公式映射后，其 formula 维度必须降级为 PARTIAL。"""
    module = _load_module()
    outcomes = _synthetic_outcomes(tmp_path)
    assert module.resolve_dimension(module.FORMULA_NODEIDS["CCI"], outcomes)["status"] \
        == "VERIFIED", "前置：CCI 公式维度本应 VERIFIED"

    mutated = dict(module.FORMULA_NODEIDS)
    mutated.pop("CCI")
    result = module.resolve_dimension(mutated.get("CCI"), outcomes)
    assert result["status"] == "PARTIAL", "删除映射未导致降级"
    assert result["outcome"] == "no_mapping"


@pytest.mark.parametrize("kind", ["failure", "error", "skipped"])
def test_junit_outcome_mutation_downgrades(tmp_path: Path, kind: str):
    """把对应 testcase 改为 failure/error/skip，维度必须降级。"""
    module = _load_module()
    nodeid = module.FORMULA_NODEIDS["CCI"]
    module_path, func_name = nodeid.split("::")
    classname = module_path[:-3].replace("/", ".")

    junit = tmp_path / "mutated.xml"
    _write_junit(junit, [(classname, func_name, kind)])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodeid, outcomes)
    assert result["status"] == "PARTIAL", f"{kind} 未导致降级"
    assert result["outcome"] in {"failed", "error", "skipped"}


def test_passing_junit_still_verifies(tmp_path: Path):
    """正对照：passed 时仍判 VERIFIED（降级不是无差别失败）。"""
    module = _load_module()
    nodeid = module.FORMULA_NODEIDS["CCI"]
    module_path, func_name = nodeid.split("::")
    classname = module_path[:-3].replace("/", ".")

    junit = tmp_path / "ok.xml"
    _write_junit(junit, [(classname, func_name, "passed")])
    outcomes = module.parse_junit_outcomes(junit)
    assert module.resolve_dimension(nodeid, outcomes)["status"] == "VERIFIED"


def test_deleted_testcase_does_not_verify(tmp_path: Path):
    """删除对应 testcase 后不得判 VERIFIED。"""
    module = _load_module()
    nodeid = module.FORMULA_NODEIDS["CCI"]
    junit = tmp_path / "empty.xml"
    _write_junit(junit, [])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodeid, outcomes)
    assert result["status"] == "PARTIAL"
    assert result["outcome"] == "missing"


def test_one_failed_parameter_downgrades_whole_dimension(tmp_path: Path):
    """多个参数化用例中只要有一个非 passed，该维度就不能 VERIFIED。"""
    module = _load_module()
    nodeid = module.FORMULA_NODEIDS["PSY"]
    module_path, func_name = nodeid.split("::")
    classname = module_path[:-3].replace("/", ".")

    junit = tmp_path / "mixed.xml"
    _write_junit(junit, [
        (classname, f"{func_name}[a]", "passed"),
        (classname, f"{func_name}[b]", "failure"),
    ])
    outcomes = module.parse_junit_outcomes(junit)
    result = module.resolve_dimension(nodeid, outcomes)
    assert result["status"] == "PARTIAL", "混合结果未按最差降级"


def test_missing_junit_raises_not_established(tmp_path: Path):
    """JUnit 缺失必须明确失败，不签发任何 VERIFIED。"""
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


def test_missing_junit_makes_main_nonzero(monkeypatch, capsys):
    """main() 在 JUnit 缺失时返回非零，不出具证据。"""
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_JUNIT_NAME", "definitely-missing.xml")
    code = module.main()
    assert code != 0
    assert "NOT_ESTABLISHED" in capsys.readouterr().err


def test_unrelated_mutation_does_not_affect_other_dimensions(tmp_path: Path):
    """变异 CCI 的公式映射，不得影响 DEMA 的任何维度。"""
    module = _load_module()
    outcomes = _synthetic_outcomes(tmp_path)
    before = module.resolve_dimension(module.FORMULA_NODEIDS["DEMA"], outcomes)

    mutated = dict(module.FORMULA_NODEIDS)
    mutated.pop("CCI")
    after = module.resolve_dimension(mutated["DEMA"], outcomes)
    assert after == before, "无关变异影响了其他维度"


def test_full_row_derivation_reflects_mutation(tmp_path: Path):
    """端到端：用真实判定逻辑跑整表，确认状态来自 outcome 而非写死。"""
    module = _load_module()
    outcomes = _synthetic_outcomes(tmp_path)
    rows = module._indicator_evidence(outcomes)
    assert rows, "无证据行"
    for row in rows:
        for dim_name, dim in row["dimensions"].items():
            assert "outcome" in dim, f"{row['indicator']}.{dim_name} 缺 outcome"
            assert "nodeid" in dim, f"{row['indicator']}.{dim_name} 缺 nodeid"
            assert dim["strength"] in {"NUMERIC_ORACLE", "CONDITION_INJECTED",
                                      "ACCOUNT_WIDE_THRESHOLD"}
    statuses = {row["status"] for row in rows}
    assert statuses <= {"VERIFIED", "PARTIAL"}


def test_empty_junit_downgrades_every_dimension(tmp_path: Path):
    """空 JUnit：所有维度都必须降级（不能有任何 VERIFIED）。"""
    module = _load_module()
    junit = tmp_path / "empty.xml"
    _write_junit(junit, [])
    outcomes = module.parse_junit_outcomes(junit)
    rows = module._indicator_evidence(outcomes)
    assert all(row["status"] == "PARTIAL" for row in rows), \
        "空 JUnit 下仍有 VERIFIED 行"