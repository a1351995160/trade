"""全范围诊断和任务状态：展示数据覆盖，查询不重新加载行情或运行账户。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .common import stable_hash
from .exploration_governance import immutable, read_json
from .research_data_provider_v1 import day


def _metadata(path: Path, root: Path) -> dict:
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("UNIVERSE_STATUS_METADATA_REDIRECTED_OR_MISSING")
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("UNIVERSE_STATUS_METADATA_INVALID")
    return value


def universe_task_metadata_v1(service, task_id: str) -> dict:
    """只读 TASK/固定 PREVIEW/INPUT/JOB 元数据；不打开任何 Parquet 原件。"""
    task = service._task(task_id)
    root = service.root / task_id
    preview = _metadata(root / "PREVIEW.json", root)
    if preview.get("request", {}).get("version") not in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'}:
        return {}
    if (preview.get("preview_identity") != task["preview_identity"]
            or stable_hash({k: v for k, v in preview.items() if k != "preview_identity"})
            != task["preview_identity"]):
        raise ValueError("UNIVERSE_STATUS_PREVIEW_CHANGED")
    job_path = root / "account" / "JOB.json"
    job = _metadata(job_path, root)
    if hashlib.sha256(job_path.read_bytes()).hexdigest() != task["job_sha256"]:
        raise ValueError("UNIVERSE_STATUS_JOB_CHANGED")
    input_path = root / "INPUT.json"
    if not job.get("items"):
        raise ValueError("UNIVERSE_STATUS_INPUT_BINDING_MISSING")
    expected = set()
    for item in job["items"].values():
        kwargs = item["loader_kwargs"]
        if (Path(kwargs["path"]) != input_path or kwargs["input_identity"] != task["input_identity"]):
            raise ValueError("UNIVERSE_STATUS_INPUT_BINDING_CONFLICT")
        expected.add(kwargs["sha256"])
    snapshot = _metadata(input_path, root)
    if expected != {hashlib.sha256(input_path.read_bytes()).hexdigest()}:
        raise ValueError("UNIVERSE_STATUS_INPUT_CHANGED")
    qualified = preview['request']['version'] == 'FULL_UNIVERSE_SUBMISSION_V2'
    scope = snapshot.get('bundle', {}).get('qualified_scope') if qualified else None
    if qualified:
        if (not isinstance(scope, dict) or scope != task.get('qualification_scope')
                or scope.get('scope_identity') != stable_hash({k: v for k, v in scope.items() if k != 'scope_identity'})
                or scope.get('target_symbols') != preview['request']['symbols']
                or scope.get('blocking_global_gaps')
                or set(scope.get('qualified_symbols', [])) & {row['symbol'] for row in scope.get('excluded', [])}
                or sorted(scope.get('qualified_symbols', []) + [row['symbol'] for row in scope.get('excluded', [])]) != preview['request']['symbols']):
            raise ValueError('UNIVERSE_STATUS_QUALIFIED_SCOPE_CONFLICT')
    if (snapshot.get("snapshot_version") != "UNIVERSE_FROZEN_INPUT_V1"
            or snapshot.get("input_identity") != task["input_identity"]
            or sorted(snapshot["window"]["symbols"]) != (scope['qualified_symbols'] if qualified else preview["request"]["symbols"])):
        raise ValueError("UNIVERSE_STATUS_INPUT_SCOPE_CONFLICT")
    qualification = snapshot.get("qualification", {})
    coverage = deepcopy(qualification.get("coverage", snapshot.get("coverage", {})))
    if not isinstance(coverage, dict):
        raise ValueError("UNIVERSE_STATUS_COVERAGE_INVALID")
    metadata = preview["data_metadata"]
    coverage.setdefault("target_count", len(preview["request"]["symbols"]))
    coverage.setdefault("by_board", deepcopy(metadata.get("by_board", {})))
    coverage["completeness"] = metadata.get("completeness", "UNIVERSE_COMPLETENESS_UNKNOWN")
    extra = {'qualification_scope': scope, 'registered_target_count': len(scope['target_symbols']),
             'qualified_target_count': len(scope['qualified_symbols']), 'excluded_target_count': len(scope['excluded'])} if qualified else {}
    return {**extra, "version": "UNIVERSE_TASK_METADATA_V1", "coverage": coverage,
        "universe_id": preview["request"]["universe_id"],
        "dataset_id": preview["request"]["dataset_id"], "input_identity": task["input_identity"],
        "historical_availability": qualification.get("historical_availability", "UNKNOWN"),
        "metadata_only": True, "freshly_reverified": False, "strategy_qualified": False}


def diagnose_universe(service, request: dict, preview_identity: str) -> dict:
    """按既有数据授权做全目标准备；不创建账户、候选或账户预算。

    同一预览/授权的记录只读复用，不自称已重新检查变化中的原件。
    实际账户冻结仍必须重新检查来源，诊断不成为执行权限。
    """
    if not isinstance(request, dict) or request.get("version") not in {'FULL_UNIVERSE_SUBMISSION_V1', 'FULL_UNIVERSE_SUBMISSION_V2'}:
        raise ValueError("UNIVERSE_DIAGNOSIS_REQUEST_REQUIRED")
    if request['version'] == 'FULL_UNIVERSE_SUBMISSION_V2':
        return service.scan(request, preview_identity)
    preview = service.preview(request)
    if preview["preview_identity"] != preview_identity:
        raise ValueError("SUBMISSION_PREVIEW_CHANGED")
    normalized = preview["request"]
    authority = service.authority(normalized["authorization_ref"])
    if (not isinstance(authority, dict) or not authority.get("objective_id")
            or not authority.get("budget_path") or not isinstance(authority.get("data_authorization"), dict)):
        raise PermissionError("UNIVERSE_DIAGNOSIS_AUTHORITY_INVALID")
    authorization = authority["data_authorization"]
    source = authority.get("source", {})
    try:
        expiry = datetime.fromisoformat(authority["expires_at"])
        dates_covered = (day(authorization["start"]) <= normalized["feature_start"]
                         and normalized["account_end"] <= day(authorization["end"]))
    except (KeyError, TypeError, ValueError):
        raise PermissionError("UNIVERSE_DIAGNOSIS_AUTHORITY_INVALID") from None
    if (not isinstance(source, dict) or source.get("origin") != "USER_EXPLICIT_CURRENT_TASK"
            or not source.get("statement") or expiry.tzinfo is None
            or expiry <= datetime.now(timezone.utc)
            or authorization.get("purpose") != normalized["purpose"]
            or not isinstance(authorization.get("dataset_ids"), list)
            or normalized["dataset_id"] not in authorization.get("dataset_ids", [])
            or not authorization.get("authorization_id") or not dates_covered):
        raise PermissionError("UNIVERSE_DIAGNOSIS_DATA_SCOPE_NOT_AUTHORIZED")
    scope = {"preview_identity": preview_identity, "objective_id": authority["objective_id"],
             "authorization_identity": stable_hash(authority),
             "dataset_id": normalized["dataset_id"], "universe_id": normalized["universe_id"]}
    identity = stable_hash(scope)
    root = service.root / "diagnostics" / identity
    if service.root.resolve() != service.root or root.resolve() != root:
        raise ValueError("UNIVERSE_DIAGNOSIS_ROOT_REDIRECTED")
    path = root / "DIAGNOSIS.json"
    if path.exists():
        old = _metadata(path, root)
        if (old.get("scope") != scope or old.get("diagnosis_identity")
                != stable_hash({k: v for k, v in old.items() if k != "diagnosis_identity"})):
            raise ValueError("UNIVERSE_DIAGNOSIS_RECORD_CONFLICT")
        return {**old, "recorded_only": True, "content_reread": False}
    prepared = service.provider.prepare(normalized["dataset_id"], symbols=normalized["symbols"],
        universe_id=normalized["universe_id"], feature_start=normalized["feature_start"],
        account_start=normalized["account_start"], account_end=normalized["account_end"],
        purpose=normalized["purpose"], required_fields=preview["required_fields"],
        authorization=authorization, stage="SCAN")
    if sorted(prepared["window"]["symbols"]) != normalized["symbols"]:
        raise ValueError("UNIVERSE_DIAGNOSIS_PROVIDER_SHRANK_TARGETS")
    qualification = prepared.get("qualification", {})
    coverage = deepcopy(qualification.get("coverage", prepared.get("coverage")))
    if (not isinstance(coverage, dict) or coverage.get("target_symbol_count", coverage.get("target_count"))
            != len(normalized["symbols"])):
        raise ValueError("UNIVERSE_DIAGNOSIS_COVERAGE_MISSING_OR_SHRUNK")
    coverage["completeness"] = preview["data_metadata"].get("completeness", "UNIVERSE_COMPLETENESS_UNKNOWN")
    coverage.setdefault("by_board", deepcopy(preview["data_metadata"].get("by_board", {})))
    value = {"version": "UNIVERSE_DATA_DIAGNOSIS_V1", "scope": scope,
        "status": "ACCOUNT_INPUTS_READY" if coverage.get("account_data_ready") is True else "DATA_GAPS",
        "input_identity": prepared["input_identity"], "rule_identity": preview["rule_identity"],
        "coverage": coverage, "historical_availability": qualification.get("historical_availability", "UNKNOWN"),
        "source_hashes": deepcopy(qualification.get("source_hashes", prepared["bundle"].get("source_hashes", {}))),
        "account_executed": False, "strategy_signals_scanned": False,
        "account_budget_created": False, "strategy_qualified": False,
        "recorded_at": datetime.now(timezone.utc).isoformat()}
    value["diagnosis_identity"] = stable_hash(value)
    immutable(path, value)
    return {**value, "recorded_only": False, "content_reread": True}
