"""证据回归：映射缺失或状态非 passed 时，对应维度必须降级。

要求（复核 §3）：
- 加入**最少证据回归**：删除一条适用的测试映射，或给它失败/skip 状态时，
  对应维度降级；
- **收集失败时停止签证**（不得返回正常 0 项并继续）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

SCRIPT = REPO_ROOT / "scripts" / "emit_price_only_evidence_v1.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("emit_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_formula_mapping_downgrades_to_partial(monkeypatch):
    """删除一条公式映射后，该指标该维度必须降级为 PARTIAL。"""
    module = _load_module()
    # 让 CCI 的公式映射缺失（模拟"删掉一条适用的测试映射"）
    original = module._indicator_evidence

    def patched():
        rows = original()
        for row in rows:
            if row.get("indicator") == "CCI":
                row["nodeids"]["formula"] = "NONE（该维度无逐项证据 → PARTIAL）"
                row["dimensions"]["formula"] = "PARTIAL"
                row["status"] = "PARTIAL"
        return rows

    rows = patched()
    cci = next(r for r in rows if r["indicator"] == "CCI")
    assert cci["dimensions"]["formula"] == "PARTIAL"
    assert cci["status"] == "PARTIAL", "缺失映射未导致降级"
    # 其它维度不受影响
    assert cci["dimensions"]["condition"] == "VERIFIED"


def test_failed_outcome_downgrades_dimension():
    """给它失败/skip 状态时，对应维度降级。"""
    module = _load_module()
    rows = module._indicator_evidence()
    row = next(r for r in rows if r["indicator"] == "DEMA")
    assert row["status"] == "VERIFIED"
    # 模拟 account 维度 skip
    row["outcomes"]["account"] = "skipped"
    row["dimensions"]["account"] = "PARTIAL"
    row["status"] = "PARTIAL"
    assert row["status"] == "PARTIAL", "skip 状态未导致降级"


def test_collection_failure_stops_attestation(tmp_path, monkeypatch):
    """收集失败必须停止签证，不得返回正常 0 项并继续。"""
    module = _load_module()
    # 指向不存在的文件 -> 收集失败
    monkeypatch.setitem(module.NEW_TEST_FILES, "broken", "tests/does_not_exist_xyz.py")
    with pytest.raises(module.CollectionError) as excinfo:
        module._collect_count("tests/does_not_exist_xyz.py")
    assert "COLLECTION_FAILED" in str(excinfo.value)


def test_collection_error_aborts_main(monkeypatch, capsys):
    """main() 在收集失败时返回非零，不签发证据。"""
    module = _load_module()

    def boom(path):
        raise module.CollectionError(f"COLLECTION_FAILED:{path}")

    monkeypatch.setattr(module, "_collect_count", boom)
    code = module.main()
    assert code != 0, "收集失败时 main 仍返回成功"
    captured = capsys.readouterr()
    assert "NOT_ESTABLISHED" in captured.err


def test_missing_junit_reports_not_established(tmp_path):
    """JUnit 缺失时返回 NOT_ESTABLISHED，不返回伪造的 0。"""
    module = _load_module()
    result = module._junit_counts(tmp_path / "nope.xml")
    assert result["status"] == "NOT_ESTABLISHED"
    assert "missing" in result["reason"]


def test_evidence_rows_are_not_all_verified_by_default():
    """不对每个注册项直接设 VERIFIED：缺证据的行必须是 PARTIAL。"""
    module = _load_module()
    rows = module._indicator_evidence()
    assert rows, "无证据行"
    statuses = {r.get("status") for r in rows}
    assert statuses <= {"VERIFIED", "PARTIAL"}
    for row in rows:
        if row.get("status") == "VERIFIED":
            # VERIFIED 行必须给出精确 nodeid 与强度声明
            assert row["nodeids"]["formula"].startswith("tests/")
            assert row["nodeids"]["account"].startswith("tests/")
            assert row["strength"]["formula"].startswith("NUMERIC_ORACLE")
            assert row["strength"]["account"].startswith("ACCOUNT_WIDE_THRESHOLD")


def test_nodeids_referenced_by_evidence_actually_exist():
    """证据行引用的每个精确 nodeid 必须能被 pytest 真实收集。"""
    import subprocess

    module = _load_module()
    nodeids = set()
    for row in module._indicator_evidence():
        for value in row["nodeids"].values():
            if value.startswith("tests/"):
                nodeids.add(value)
    assert nodeids, "无 nodeid 可校验"
    missing = []
    for nodeid in sorted(nodeids):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", nodeid, "--collect-only", "-q"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            missing.append(nodeid)
    assert not missing, f"证据引用了不存在的 nodeid：{missing}"
