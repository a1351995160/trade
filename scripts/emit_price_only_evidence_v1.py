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
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT_DIR = REPO_ROOT / "reports" / "price_only_validation_v1"
TARGET_JUNIT_NAME = "junit-price-only-v1.xml"

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
    """按**最差** outcome 合并去参数键。

    否则先出现的 passed 参数会掩盖后续失败/跳过的兄弟参数，
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
        outcomes[f"{module_path}::{name}"] = outcome
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
        expected[(indicator_id, "condition")] = sorted(set(
            condition_map.get(indicator_id, [])))
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


def _indicator_evidence(outcomes: dict) -> list:
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
    from chanlun_trader.engine.indicator_registry_v2 import default_registry

    registry = default_registry()
    expected_coverage = build_expected_coverage()
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
            resolved["junit"] = TARGET_JUNIT_NAME
            resolved["strength"] = strength_by_dim[name]
            dims[name] = resolved
        rows.append({
            "indicator": indicator_id,
            "version": spec.version,
            "outputs": list(spec.outputs),
            "params_domain": dict(spec.params),
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
    if not base_file.exists() or not head_file.exists():
        return {"status": "NOT_ESTABLISHED",
                "reason": "missing node listing (base_nodes.txt / head_nodes.txt)"}
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
    local_full = _junit_counts(REPO_ROOT / "reports" / "junit-full-local.xml")

    rows = _indicator_evidence(outcomes)
    verified = sum(1 for r in rows if r.get("status") == "VERIFIED")
    partial = sum(1 for r in rows if r.get("status") == "PARTIAL")

    payload = {
        "generated_from": "实际 pytest --collect-only 与 JUnit testcase outcome",
        "junit_consumed": str(junit_path),
        "note": "计数与状态均由脚本自动派生，不硬编码；本机与 CI 分列。"
                "汇总成功不覆盖单项失败。",
        "source_identity": {
            "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                                   capture_output=True, text=True).stdout.strip(),
        },
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
        "local_full_suite": local_full,
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
    return 0


if __name__ == "__main__":
    # 直接运行时才解析命令行；被 import（如测试）时 main() 默认不读宿主 argv
    raise SystemExit(main(sys.argv[1:]))
