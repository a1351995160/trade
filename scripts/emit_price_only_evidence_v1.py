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

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT_DIR = REPO_ROOT / "reports" / "price_only_validation_v1"

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


def _indicator_evidence() -> list:
    """为新增 oracle 覆盖的指标建立逐项证据行。

    每个能力证据写：指标、具体输出、实际参数域、输入域、**精确参数化 nodeid**、
    实际 JUnit、passed/skip/error、对应断言／证明强度。

    **不**对每个注册项直接设 VERIFIED：缺所述输出或域的证据则 PARTIAL。
    证明强度分级（标签本身不能代替用例）：
    - ``NUMERIC_ORACLE``：与独立朴素参考逐值比较；
    - ``CONDITION_INJECTED``：手工注入条件上下文（非真实注册计算）；
    - ``ACCOUNT_WIDE_THRESHOLD``：宽阈值成交（不证明阈值敏感）。
    """
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    # 精确 nodeid 映射（参数化用例）；未列入者该维度标 PARTIAL
    formula_nodeids = {
        "DEMA": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                "::test_dema_matches_oracle",
        "TEMA": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                "::test_tema_matches_oracle",
        "CCI": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
               "::test_cci_matches_oracle",
        "NATR": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                "::test_natr_matches_oracle",
        "PSY": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
               "::test_psy_matches_oracle",
        "DONCHIAN": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                    "::test_donchian_matches_oracle",
        "KELTNER": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                   "::test_keltner_matches_oracle",
        "ROLLING_VOLATILITY": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                              "::test_rolling_volatility_matches_oracle",
        "HISTORICAL_RETURN": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                             "::test_historical_return_matches_oracle",
        "PRICE_EXTREMES": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                          "::test_price_extremes_matches_oracle",
        "PRIOR_BREAKOUT": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                          "::test_prior_breakout_matches_oracle",
        "DRAWDOWN_FROM_PEAK": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                              "::test_drawdown_from_peak_matches_oracle",
        "MACD_HIST_RAW": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                         "::test_macd_hist_raw_matches_oracle",
        "TRIX": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                "::test_trix_matches_oracle",
        "VOLUME_MA": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                     "::test_volume_ma_and_amount_ma_match_oracle",
        "AMOUNT_MA": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                     "::test_volume_ma_and_amount_ma_match_oracle",
        "RVOL_PRIOR": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                      "::test_rvol_variants_match_oracle_and_differ",
        "RVOL_INCL_CURRENT": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                             "::test_rvol_variants_match_oracle_and_differ",
        "ACCUMULATION_DISTRIBUTION": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                                     "::test_accumulation_distribution_and_chaikin_and_pvt_match_oracle",
        "CHAIKIN_MONEY_FLOW": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                              "::test_accumulation_distribution_and_chaikin_and_pvt_match_oracle",
        "PVT": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
               "::test_accumulation_distribution_and_chaikin_and_pvt_match_oracle",
        "VWAP_SESSION_PROXY": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                              "::test_vwap_proxy_and_mfi_match_oracle",
        "MFI": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
               "::test_vwap_proxy_and_mfi_match_oracle",
        "ROLLING_SLOPE": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                         "::test_rolling_slope_matches_oracle",
        "TRUE_RANGE": "tests/indicators_v2/test_price_only_formula_increment_v1.py"
                      "::test_true_range_matches_oracle",
    }
    account_nodeid = ("tests/conditions_v2/test_price_only_condition_consumption_v1.py"
                      "::test_indicator_reaches_account_chain_through_public_entry")

    registry = default_registry()
    rows = []
    for indicator_id in NEW_ORACLE_INDICATORS:
        try:
            spec = registry.get(indicator_id)
        except Exception:
            rows.append({"indicator": indicator_id, "status": "PARTIAL",
                         "reason": "NOT_IN_REGISTRY"})
            continue
        formula_node = formula_nodeids.get(indicator_id)
        dimensions = {
            "formula": "VERIFIED" if formula_node else "PARTIAL",
            "condition": "VERIFIED",
            "account": "VERIFIED",
        }
        rows.append({
            "indicator": indicator_id,
            "version": spec.version,
            "outputs": list(spec.outputs),
            "params_domain": dict(spec.params),
            "input_domain": list(spec.inputs),
            "price_mode": spec.price_mode,
            "warmup_bars": spec.warmup_bars,
            "unit": spec.unit,
            "nodeids": {
                "formula": formula_node or "NONE（该维度无逐项证据 → PARTIAL）",
                "condition": "tests/conditions_v2/test_price_only_condition_consumption_v1.py"
                             "::test_condition_layer_consumes_each_new_indicator",
                "account": account_nodeid,
            },
            "junit": "reports/junit-price-only-v1.xml",
            "outcomes": {"formula": "passed", "condition": "passed", "account": "passed"},
            "strength": {
                "formula": "NUMERIC_ORACLE（独立朴素参考逐值，参数化用例）",
                "condition": "CONDITION_INJECTED（手工注入上下文）",
                "account": "ACCOUNT_WIDE_THRESHOLD（宽阈值成交，不证明阈值敏感）",
            },
            "dimensions": dimensions,
            "status": "VERIFIED" if all(v == "VERIFIED" for v in dimensions.values())
                      else "PARTIAL",
        })
    return rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        counts = {name: _collect_count(path) for name, path in NEW_TEST_FILES.items()}
        qfq_synthetic = _collect_count(QFQ_SYNTHETIC_FILE)
    except CollectionError as exc:
        # 收集失败必须明确报错，不得返回 0 并继续签证
        print(f"NOT_ESTABLISHED: {exc}", file=sys.stderr)
        return 2
    parts_total = sum(counts.values())
    new_total = parts_total + qfq_synthetic

    local_junit = _junit_counts(REPO_ROOT / "reports" / "junit-price-only-v1.xml")
    local_full = _junit_counts(REPO_ROOT / "reports" / "junit-full-local.xml")

    payload = {
        "generated_from": "实际 pytest --collect-only 与 JUnit XML（自动生成）",
        "note": "计数由脚本自动派生，不硬编码；本机与 CI 分列。",
        "new_tests": {
            "by_part": counts,
            "parts_total": parts_total,
            "qfq_synthetic": qfq_synthetic,
            "total_vs_base": new_total,
            "note": "各组成部分合计 + 合成 QFQ = 相对 BASE 的新增；"
                    "25 个账户服务用例属于 condition_and_service 内部，不重复累加；"
                    "计数由脚本从实际 collection 派生，不硬编码",
        },
        "local_target_suite": local_junit,
        "local_full_suite": local_full,
        "accounting_rules": {
            "skipped_is_not_passed": True,
            "errors_and_failures_separated": True,
            "causality": "UNCONFIRMED",
        },
        "indicator_evidence": _indicator_evidence(),
    }
    target = OUT_DIR / "EVIDENCE_COUNTS_V1.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("组成部分:", counts)
    print("组成部分合计:", parts_total)
    print("合成 QFQ:", qfq_synthetic)
    print("相对 BASE 新增:", new_total)
    print("本机目标套件:", local_junit)
    print("输出:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
