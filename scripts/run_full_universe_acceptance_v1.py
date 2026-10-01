"""冻结两种规则，经公共入口完成全范围账户验收；不搜索盈利参数。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for local_path in (ROOT, ROOT / "src"):
    if str(local_path) not in sys.path:
        sys.path.insert(0, str(local_path))

from chanlun_trader.presentation import ZhCNPresentation, write_human_report
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json


CONFIG_VERSION = "FULL_UNIVERSE_ACCEPTANCE_CONFIG_V1"
CASES = {"single_indicator": "ma_cross_with_exits", "multi_indicator": "multi_indicator"}
STRATEGY_IDS = {"single_indicator": "FULL_U_SINGLE", "multi_indicator": "FULL_U_MULTI"}
BOARDS = ("SZ_MAIN", "SH_MAIN", "CHINEXT")


def fixed_acceptance_requests_v1(config: dict, capability_snapshot: dict) -> dict:
    """规则先从同源目录固定，资金、成本情境和全范围选股口径不可按结果改变。"""
    fields = {"version", "dataset_id", "universe_id", "feature_start", "account_start",
              "account_end", "authorization_refs"}
    if (not isinstance(config, dict) or set(config) != fields
            or config.get("version") != CONFIG_VERSION
            or not isinstance(config["authorization_refs"], dict)
            or set(config["authorization_refs"]) != set(CASES)
            or any(not isinstance(ref, str) or not ref for ref in config["authorization_refs"].values())):
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_CONFIG_INVALID")
    requests = {}
    for case, example in CASES.items():
        rule = capability_snapshot["examples"][example]
        expected_types = {"MA"} if case == "single_indicator" else {"MA", "RSI", "ROLLING_VOLATILITY"}
        if {item["id"] for item in rule["indicator_instances"]} != expected_types:
            raise ValueError("FULL_UNIVERSE_ACCEPTANCE_FIXED_RULE_TYPES_CHANGED")
        requests[case] = {"version": "FULL_UNIVERSE_SUBMISSION_V1",
            "strategy_id": STRATEGY_IDS[case], "rule": deepcopy(rule),
            "dataset_id": config["dataset_id"], "universe_id": config["universe_id"],
            "feature_start": config["feature_start"], "account_start": config["account_start"],
            "account_end": config["account_end"], "initial_cash": 50000, "max_positions": 2,
            "max_symbol_exposure_bps": 5000, "costs": ["BASE", "STRESS"],
            "benchmark": "CASH_AND_PRICE_REFERENCE", "purpose": "EXPLORATORY",
            "authorization_ref": config["authorization_refs"][case]}
    return requests


def _finish(root: Path, plan: dict, rows: dict, *, execute: bool, status: str) -> dict:
    value = {"version": "FULL_UNIVERSE_PUBLIC_ACCEPTANCE_RUN_V1",
        "acceptance_identity": plan["acceptance_identity"], "status": status,
        "execution_requested": execute, "initial_cash": 50000,
        "selection_policy": "ALL_REGISTERED_TARGETS_THEN_STRATEGY_SIGNALS",
        "cash_policy": "ONE_SHARED_ACCOUNT_ACROSS_BOARDS", "cases": rows,
        "engineering_evidence": "PUBLIC_ENTRY_MATRIX_COMPLETED" if status == "ACCOUNT_VERIFIED" else "ENGINEERING_NOT_ACCEPTED",
        "real_evidence": "REAL_NOT_ACCEPTED", "strategy_qualified": False,
        "independent_validation": "NOT_RUN", "paper": "NOT_RUN"}
    value["run_identity"] = stable_hash(value)
    phase = "ACCOUNT" if execute else "DIAGNOSIS"
    report_path = root / ("RUN_" + phase + "_" + value["run_identity"] + ".json")
    immutable(report_path, value)
    description = {
        "ACCOUNT_VERIFIED": "两种固定规则的正常与压力成本账户全部核验完成。",
        "DATA_GAPS": "全范围数据仍有缺口，未进入账户执行。",
        "DIAGNOSED_REQUIRES_EXECUTION": "全范围数据诊断已就绪，账户尚未执行。",
        "RECOVERY_REQUIRED": "已存在未完成冻结记录，需先核对原任务，不能另起免费重试。",
        "EVIDENCE_BLOCKED": "公共入口返回证据阻断，不能认定账户验收通过。",
        "EXECUTION_BLOCKED": "原任务执行或权限校验未完成；未创建替代候选。",
    }[status]
    lines = ["# 全范围公共入口验收", "", description, "",
        "每个账户只有一份 50,000 元；全部登记证券共同竞争这份资金。",
        "规则在读取行情前固定，不根据盈亏调整参数。现金对照和不可投资的价格对照由公共报告输出。",
        "", "| 固定规则 | 全部目标 | 数据诊断 | 账户任务 |",
        "|---|---:|---|---|"]
    for case, row in rows.items():
        diagnosis = row.get("diagnosis", {})
        good = diagnosis.get("status") == "ACCOUNT_INPUTS_READY"
        lines.append(f"| {'单类均线规则' if case == 'single_indicator' else '均线、RSI与波动率组合'} | "
                     f"{row['target_count']} | {ZhCNPresentation.status_name('PASS' if good else 'NOT_READY')} | "
                     f"{row.get('task_id', '未执行')} |")
        if row.get("error"):
            lines += ["", f"{case} 的阻断原因：`{row['error']['type']}: {row['error']['message']}`。"]
        if row.get("result", {}).get("reports"):
            lines += ["", f"{case} 的账户报告：`{row['result']['reports']}`。"]
    lines += ["", "本报告不授予策略资格。工程运行、真实全范围数据验收、历史盈利、独立验证及 Paper 观察分别判断。",
              "历史证券状态或税款扣收时点为模型时，继续保留模型声明；测试夹具不能充当真实数据证据。",
              "", f"固定验收身份：`{plan['acceptance_identity']}`。"]
    human_path = root / (phase + "_REPORT_CN_" + value["run_identity"] + ".md")
    write_human_report(human_path, "\n".join(lines) + "\n")
    return {**value, "report_path": str(report_path), "human_report_path": str(human_path)}


def _existing_task(service, expected: dict) -> dict | None:
    path = service.root / expected["task_id"]
    if path.resolve() != path:
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_TASK_REDIRECTED")
    if (path / "TASK.json").exists():
        task = service._task(expected["task_id"])
        if any(task.get(key) != expected[key] for key in ("task_id", "preview_identity", "objective_id")):
            raise ValueError("FULL_UNIVERSE_ACCEPTANCE_TASK_CONFLICT")
        return task
    if path.exists():
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_PARTIAL_FREEZE_RECOVERY_REQUIRED")
    return None


def run_full_universe_acceptance_v1(service, config: dict, output_root, *, execute=False) -> dict:
    """只编排公共提交服务，预算和执行仍由其既有授权及判重机制处理。

    默认停在数据诊断。execute=True 只能引用已登记的两个账户许可，
    不生成授权文件、不新建研究目标、不扩大搜索预算。
    """
    root = Path(output_root).absolute()
    if root.resolve() != root or service.root.resolve() != service.root:
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_ROOT_REDIRECTED")
    snapshot = service.capabilities()
    requests = fixed_acceptance_requests_v1(config, snapshot)
    previews, bindings = {}, {}
    for case, request in requests.items():
        preview = service.preview(request)
        all_targets = sorted(preview["data_metadata"]["target_symbols"])
        if preview["request"]["symbols"] != all_targets or not all_targets:
            raise ValueError("FULL_UNIVERSE_ACCEPTANCE_PREVIEW_SHRANK_TARGETS")
        if any(preview["data_metadata"].get("by_board", {}).get(board, {}).get("target_count", 0) < 1
               for board in BOARDS):
            raise ValueError("FULL_UNIVERSE_ACCEPTANCE_THREE_BOARD_TARGET_REQUIRED")
        authority = service.authority(request["authorization_ref"])
        if not isinstance(authority, dict) or not authority.get("objective_id") or not authority.get("budget_path"):
            raise PermissionError("FULL_UNIVERSE_ACCEPTANCE_EXISTING_AUTHORITY_REQUIRED")
        previews[case] = preview
        bindings[case] = {"preview_identity": preview["preview_identity"],
            "objective_id": authority["objective_id"], "authorization_identity": stable_hash(authority),
            "budget_identity": stable_hash([authority["objective_id"], str(authority["budget_path"])]),
            "task_id": stable_hash({"preview": preview["preview_identity"], "objective_id": authority["objective_id"]})}
    plan = {"version": "FULL_UNIVERSE_ACCEPTANCE_PLAN_V1", "config": deepcopy(config),
        "requests": requests, "previews": previews, "bindings": bindings,
        "capability_fingerprint": snapshot["fingerprint"],
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "criteria": {"account_cases": ["single_indicator_BASE", "single_indicator_STRESS",
                                       "multi_indicator_BASE", "multi_indicator_STRESS"],
            "all_targets_required": True, "shared_initial_cash": 50000,
            "benchmark": "CASH_AND_PRICE_REFERENCE", "positive_return_required": False,
            "real_data_qualification_is_separate": True}}
    plan["acceptance_identity"] = stable_hash(plan)
    immutable(root / "FROZEN_ACCEPTANCE.json", plan)
    rows, blocked = {}, False
    for case, request in requests.items():
        row = {"target_count": len(previews[case]["request"]["symbols"]),
               "by_board": deepcopy(previews[case]["data_metadata"]["by_board"])}
        rows[case] = row
        try:
            diagnosis = service.diagnose(request, previews[case]["preview_identity"])
            row["diagnosis"] = {key: value for key, value in diagnosis.items()
                                 if key not in {"recorded_only", "content_reread"}}
            immutable(root / case / "DIAGNOSIS.json", row["diagnosis"])
            if diagnosis["status"] != "ACCOUNT_INPUTS_READY":
                blocked = True
        except (ValueError, PermissionError) as exc:
            row["error"] = {"type": type(exc).__name__, "message": str(exc)}
            immutable(root / case / "DIAGNOSIS_FAILURE.json", row["error"])
            blocked = True
    if blocked:
        return _finish(root, plan, rows, execute=execute, status="DATA_GAPS")
    if not execute:
        return _finish(root, plan, rows, execute=False, status="DIAGNOSED_REQUIRES_EXECUTION")
    for case, request in requests.items():
        row, expected = rows[case], bindings[case]
        try:
            case_root = root / case
            immutable(case_root / "FREEZE_INTENT.json", expected)
            task = _existing_task(service, expected)
            if task is None:
                task = service.freeze(request, expected["preview_identity"])
            if task["input_identity"] != row["diagnosis"]["input_identity"]:
                raise ValueError("FULL_UNIVERSE_ACCEPTANCE_DIAGNOSIS_INPUT_CHANGED")
            immutable(case_root / "TASK.json", task)
            row["task_id"] = task["task_id"]
            shown = service.approval_preview(task["task_id"])
            if (shown["preview_identity"] != expected["preview_identity"]
                    or shown["rule_identity"] != previews[case]["rule_identity"]
                    or shown["input_identity"] != task["input_identity"] or len(shown["plan_ids"]) != 2
                    or shown["request"]["symbols"] != previews[case]["request"]["symbols"]
                    or shown["request"]["initial_cash"] != 50000
                    or shown["request"]["costs"] != ["BASE", "STRESS"]
                    or shown["request"]["benchmark"] != "CASH_AND_PRICE_REFERENCE"):
                raise ValueError("FULL_UNIVERSE_ACCEPTANCE_ACCOUNT_PLAN_CONFLICT")
            immutable(case_root / "APPROVAL_PREVIEW.json", shown)
            approved = service.approve(task["task_id"], expected["preview_identity"])
            immutable(case_root / "APPROVAL.json", approved)
            result = service.start(task["task_id"])
            immutable(case_root / "RESULT.json", result)
            row["result"] = result
            if result.get("status") != "ACCOUNT_VERIFIED" or result.get("strategy_qualified") is not False:
                return _finish(root, plan, rows, execute=True, status="EVIDENCE_BLOCKED")
        except (ValueError, PermissionError, RuntimeError) as exc:
            row["error"] = {"type": type(exc).__name__, "message": str(exc)}
            # 故障回执保留在原目录，不替换原任务和账户许可。
            failure = {"preview_identity": expected["preview_identity"], **row["error"]}
            immutable(root / case / ("FAILURE_" + stable_hash(failure) + ".json"), failure)
            state = "RECOVERY_REQUIRED" if "PARTIAL_FREEZE_RECOVERY_REQUIRED" in str(exc) else "EXECUTION_BLOCKED"
            return _finish(root, plan, rows, execute=True, status=state)
    return _finish(root, plan, rows, execute=True, status="ACCOUNT_VERIFIED")


def main(argv=None):
    parser = argparse.ArgumentParser(description="两种固定规则的全范围公共入口验收")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--deployment-config", required=True)
    parser.add_argument("--acceptance-config", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--execute", action="store_true", help="仅使用部署已经登记的账户授权执行")
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).absolute()
    output = Path(args.output_root)
    if not output.is_absolute():
        output = workspace / output
    if workspace.resolve() != workspace or output.resolve() != output or not output.is_relative_to(workspace):
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_OUTPUT_OUTSIDE_WORKSPACE")
    from scripts.lifecycle_deployment_v2 import build_submission_service
    service = build_submission_service(workspace, read_json(Path(args.deployment_config)))
    if not service.provider.catalog().get("schema_version") == "TDX_FULL_UNIVERSE_V1":
        raise ValueError("FULL_UNIVERSE_ACCEPTANCE_DEPLOYMENT_REQUIRED")
    result = run_full_universe_acceptance_v1(service, read_json(Path(args.acceptance_config)), output, execute=args.execute)
    summary = {key: result[key] for key in ("version", "status", "initial_cash", "run_identity",
               "report_path", "human_report_path", "real_evidence", "strategy_qualified")}
    summary["cases"] = {case: {"target_count": row["target_count"],
        "diagnosis_status": row.get("diagnosis", {}).get("status", "DATA_BLOCKED"),
        "task_id": row.get("task_id"), "account_status": row.get("result", {}).get("status", "NOT_RUN")}
        for case, row in result["cases"].items()}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ACCOUNT_VERIFIED", "DIAGNOSED_REQUIRES_EXECUTION"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
