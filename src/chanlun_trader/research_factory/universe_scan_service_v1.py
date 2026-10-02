"""全范围公共信号检查：固定意图、受限进程、未知保留；不运行账户。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

# 直接执行本文件；不能用 -m 在限额握手之前加载 research_factory 包。
HANDSHAKE = None
if __name__ == "__main__" and "--worker" in sys.argv:
    from chanlun_trader.synthetic_batch_resources import worker_resource_handshake
    HANDSHAKE = worker_resource_handshake()

from chanlun_trader.presentation import ZhCNPresentation
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock
from chanlun_trader.research_factory.research_data_provider_v1 import day
from chanlun_trader.synthetic_batch_resources import run_bounded_worker


VERSION = "UNIVERSE_PUBLIC_SIGNAL_SCAN_V1"
LIMITS = {"memory_mib": 2048, "wall_seconds": 900, "numerical_threads": 1,
          "scope": "PREPARE_FREEZE_QUALIFY_AND_SCAN_IN_ONE_WORKER"}
REPO = Path(__file__).resolve().parents[3]


def _file(path: Path, root: Path) -> Path:
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("UNIVERSE_SCAN_ARTIFACT_REDIRECTED_OR_MISSING")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature(path: Path) -> dict:
    if path.resolve() != path or not path.is_file():
        raise ValueError("UNIVERSE_SCAN_SOURCE_REDIRECTED_OR_MISSING")
    value = path.stat()
    return {"size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "inode": value.st_ino}


def _source_files(strategy) -> dict:
    folder = Path(__file__).parent
    files = set(map(Path, strategy.source_files))
    files.update(folder / name for name in (
        "universe_scan_service_v1.py", "universe_submission_v1.py", "universe_data_provider_v1.py",
        "tdx_research_adapter_v1.py", "research_universe_v1.py", "universe_account_inputs_v1.py",
        "board_execution_policy_v1.py",
        "universe_signal_scan_v1.py", "causal_dividend_features_v1.py", "common.py",
        "exploration_governance.py", "mutation_boundary.py", "strategy_submission_v1.py"))
    files.update(REPO / "src" / "chanlun_trader" / name for name in (
        "synthetic_batch_resources.py", "presentation.py", "research/guard.py"))
    return {str(_file(path, REPO)): _sha(path) for path in sorted(files)}


def _check_code(hashes: dict):
    for name, expected in hashes.items():
        if _sha(_file(Path(name), REPO)) != expected:
            raise ValueError("UNIVERSE_SCAN_CODE_CHANGED")


def _authorize(authority: dict, normalized: dict) -> dict:
    if (not isinstance(authority, dict) or not authority.get("objective_id")
            or not authority.get("budget_path") or not isinstance(authority.get("data_authorization"), dict)):
        raise PermissionError("UNIVERSE_SCAN_AUTHORITY_INVALID")
    authorization, source = authority["data_authorization"], authority.get("source", {})
    try:
        expiry = datetime.fromisoformat(authority["expires_at"])
        covered = (day(authorization["start"]) <= normalized["feature_start"]
                   and normalized["account_end"] <= day(authorization["end"]))
    except (KeyError, TypeError, ValueError):
        raise PermissionError("UNIVERSE_SCAN_AUTHORITY_INVALID") from None
    if (not isinstance(source, dict) or source.get("origin") != "USER_EXPLICIT_CURRENT_TASK"
            or not source.get("statement") or expiry.tzinfo is None
            or expiry <= datetime.now(timezone.utc) or not covered
            or authorization.get("purpose") != normalized["purpose"]
            or not isinstance(authorization.get("dataset_ids"), list)
            or normalized["dataset_id"] not in authorization["dataset_ids"]
            or not authorization.get("authorization_id")):
        raise PermissionError("UNIVERSE_SCAN_DATA_SCOPE_NOT_AUTHORIZED")
    return {"objective_id": authority["objective_id"], "budget_path": str(authority["budget_path"]),
            "expires_at": authority["expires_at"], "source": deepcopy(source),
            "data_authorization": deepcopy(authorization)}


def _registration(provider, normalized: dict) -> dict:
    from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
    if not isinstance(provider, UniverseDataProviderV1):
        raise ValueError("UNIVERSE_SCAN_REGISTERED_PROVIDER_REQUIRED")
    root, manifest, digest, universe = provider._datasets[normalized["dataset_id"]]
    if (root.resolve() != root or not root.is_dir() or
            sorted(universe.target_symbols) != normalized["symbols"]):
        raise ValueError("UNIVERSE_SCAN_REGISTRATION_SCOPE_CONFLICT")
    metadata = provider._metadata_paths.get(normalized["dataset_id"])
    if not isinstance(metadata, Path):
        raise ValueError("UNIVERSE_SCAN_REGISTERED_METADATA_PATH_MISSING")
    signatures = {name: _signature(provider._path(root, name)) for name in manifest["files"]}
    registration = {"derivation": "REGISTRATION_SNAPSHOT_V1", "root": str(root),
            "original_metadata_sha256": digest, "manifest": deepcopy(manifest),
            "original_metadata_path": str(metadata), "metadata_signature": _signature(metadata),
            "universe_identity": universe.universe_identity, "physical_signatures": signatures}
    _check_registration(registration)
    return registration


def _check_registration(registration: dict):
    from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
    metadata = Path(registration["original_metadata_path"])
    signature = _signature(metadata)
    # 这是已登记的元数据原件，不是行情；保持原始编码、BOM和字段顺序的字节身份。
    raw = metadata.read_bytes()
    if (hashlib.sha256(raw).hexdigest() != registration["original_metadata_sha256"]
            or signature != registration["metadata_signature"]
            or json.loads(raw.decode("utf-8-sig")) != registration["manifest"]):
        raise ValueError("UNIVERSE_SCAN_REGISTERED_METADATA_CHANGED")
    root = Path(registration["root"])
    if root.resolve() != root or not root.is_dir():
        raise ValueError("UNIVERSE_SCAN_REGISTERED_ROOT_CHANGED")
    for name, signature in registration["physical_signatures"].items():
        if _signature(UniverseDataProviderV1._path(root, name)) != signature:
            raise ValueError("UNIVERSE_SCAN_REGISTERED_SOURCE_CHANGED")


def _base_result(intent: dict) -> dict:
    preview = intent["preview"]
    return {"version": VERSION, "scan_id": intent["scan_id"],
        "scope": {"preview_identity": preview["preview_identity"],
                  "objective_id": intent["authority"]["objective_id"],
                  "authorization_identity": intent["authorization_identity"],
                  "dataset_id": preview["request"]["dataset_id"],
                  "universe_id": preview["request"]["universe_id"]},
        "rule_identity": preview["rule_identity"], "target_count": len(preview["request"]["symbols"]),
        "account_executed": False, "account_budget_created": False, "strategy_qualified": False,
        "independent_validation": "NOT_RUN", "paper_observation_days": 0,
        "market_filter_scope": "SAME_SECURITY_OHLC_AND_ACTIVITY_FIELDS_ONLY",
        "signal_meaning": "RAW_CONDITIONS_NOT_ORDERS_OR_ACTUAL_TRADES",
        "resources": deepcopy(LIMITS), "registration": {
            "derivation": "REGISTRATION_SNAPSHOT_V1",
            "original_metadata_sha256": intent["registration"]["original_metadata_sha256"],
            "snapshot_sha256": intent["registration_snapshot_sha256"]},
        "historical_independence": "UNKNOWN", "historical_availability": "UNKNOWN"}


def _blocked_result(intent: dict, reason: str) -> dict:
    symbols = intent["preview"]["request"]["symbols"]
    return {**_base_result(intent), "status": "SCAN_BLOCKED", "reason": reason,
        "processed_target_count": 0, "signals_evaluated_target_count": 0,
        "signals_evaluated_session_count": 0, "unknown_target_count": len(symbols),
        "strategy_signals_scanned": False, "qualification_checked": False,
        "coverage": {"target_symbol_count": len(symbols), "account_data_ready": False,
            "by_board": intent["preview"]["data_metadata"]["by_board"],
            "completeness": intent["preview"]["data_metadata"]["completeness"],
            "global_gaps": [reason], "gaps": []},
        "per_symbol": [{"symbol": symbol, "status": "UNKNOWN", "reasons": [reason],
                        "evaluated_sessions": 0, "unknown_sessions": None,
                        "condition_counts": None} for symbol in symbols]}


def _write_result(root: Path, value: dict) -> dict:
    value = {**value, "recorded_at": datetime.now(timezone.utc).isoformat()}
    value["scan_identity"] = stable_hash(value)
    immutable(root / "RESULT.json", value)
    projection = "NOT_READY" if value["status"] != "CONDITIONS_EVALUATED" else "PASS"
    explanation = {
        "CONDITIONS_EVALUATED": "已完成原始条件检查；是否成交、账户收益和策略资格尚未评定。",
        "SCAN_DATA_GAPS": "已检查完整目标范围，缺证据的条件保持未知，没有计算账户收益。",
        "SCAN_BLOCKED": "受限工作进程未完成资格检查；不能把未检查股票称为无信号。",
    }[value["status"]]
    lines = ["# 全范围策略信号检查", "", ZhCNPresentation.status_name(projection), explanation, "",
        f"登记目标：{value['target_count']} 只；完成资格检查：{value['processed_target_count']} 只。",
        f"实际计算条件：{value['signals_evaluated_target_count']} 只、{value['signals_evaluated_session_count']} 个股票交易日。",
        f"含未知条件的目标：{value['unknown_target_count']} 只。未知不是条件为假，也不是零次命中。", "",
        "规则条件不是买卖决策；本检查没有账户、交易、收益、正式资格或独立验证。",
        "market_filter 只引用同一股票行情，不表示大盘指数过滤。",
        "数据准备、冻结、资格与条件计算全部在同一受限进程中，内存上限 2048MiB、最长900秒、数值线程1。",
        "历史数据可见性和单位仍沿用来源声明；MODELED 不等于已证明当时可见。",
        "重复提交只读取同一检查记录，不重新计算；原件 stat/源码变化会拒绝复用。",
        "登记元数据副本与原文件分别记录哈希，副本不冒充原文件字节身份。"]
    if value.get("reason"):
        lines.extend(["", ZhCNPresentation.reason_title(value["reason"]),
                      ZhCNPresentation.reason_explanation(value["reason"])])
    path = root / "REPORT_CN.md"
    with path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value


def _evaluate_conditions(strategy, inputs) -> dict:
    """先检查全分母资格；只在已具备证据的股票上计算条件。"""
    from chanlun_trader.research_factory.universe_signal_scan_v1 import UniverseSignalScanV1
    targets, coverage = inputs.window["symbols"], inputs.coverage
    reasons = {symbol: set(coverage["global_gaps"]) for symbol in targets}
    for gap in coverage["gaps"]:
        # 昨收仅为成交参考时可不阻断纯信号；相关指标或除息复权仍按实际依赖校验。
        if gap["reason"] == "UNIVERSE_RAW_FIELD_INVALID:prev_close" and "prev_close" not in inputs.required_fields:
            continue
        reasons[gap["symbol"]].add(gap["reason"])
    qualified = [symbol for symbol in targets if not reasons[symbol]]
    scanner = None
    if qualified:
        bundle = inputs.bundle
        if len(qualified) != len(targets):
            bundle = {**bundle, "daily": bundle["daily"].loc[bundle["daily"].symbol.isin(qualified)],
                      "turn": bundle["turn"].loc[bundle["turn"].symbol.isin(qualified)]}
        # 内部计算视图保留完整 window；没有创建缩小范围的 Provider 或授权。
        scanner = UniverseSignalScanV1(strategy, SimpleNamespace(
            bundle=bundle, window=inputs.window, input_identity=inputs.input_identity), allow_data_gaps=True)
        for preparation in scanner.preparation:
            if preparation.get('reason'):
                reasons[preparation['symbol']].add(preparation['reason'])
    account_days = [d for d in inputs.calendar if d >= inputs.window["account_start"]]
    rows, total_evaluated = [], 0
    for symbol in targets:
        evaluated, unknown, entry_eligible, counts = 0, 0, 0, {"buy": 0, "sell": 0, "market_filter": 0}
        if reasons[symbol]:
            rows.append({"symbol": symbol, "status": "UNKNOWN", "reasons": sorted(reasons[symbol]),
                         "evaluated_sessions": 0, "unknown_sessions": len(account_days),
                         "condition_counts": None, "entry_eligible_sessions": None})
            continue
        for date in account_days:
            state = inputs.state(symbol, date, asof=inputs._close_times[date])
            eligibility = inputs._scan_status(symbol, date, state, inputs.bar(symbol, date))
            truth = scanner.at(symbol, date)
            if not eligibility["signal_ready"] or not truth["ready"]:
                unknown += 1
                continue
            evaluated += 1
            entry_eligible += int(eligibility["entry_eligible"])
            for key in counts:
                counts[key] += int(truth[key] is True)
        total_evaluated += evaluated
        rows.append({"symbol": symbol, "status": "CONDITIONS_EVALUATED" if not unknown else
                     "CONDITIONS_EVALUATED_WITH_UNKNOWN_SESSIONS" if evaluated else "UNKNOWN",
                     "reasons": [] if not unknown else ["UNIVERSE_WARMUP_OR_COMPLETED_BAR_UNAVAILABLE"],
                     "evaluated_sessions": evaluated, "unknown_sessions": unknown,
                     "condition_counts": counts if evaluated else None,
                     "entry_eligible_sessions": entry_eligible if evaluated else None})
    return {"processed_target_count": len(targets), "qualification_checked": True,
        "signals_evaluated_target_count": sum(row["evaluated_sessions"] > 0 for row in rows),
        "signals_evaluated_session_count": total_evaluated,
        "unknown_target_count": sum(row["unknown_sessions"] > 0 for row in rows),
        "strategy_signals_scanned": total_evaluated > 0, "per_symbol": rows,
        "internal_computation_symbols": [row['symbol'] for row in scanner.preparation
            if row['status'] == 'COMPUTED'] if scanner is not None else [],
        "scanner_identity": scanner.identity if scanner is not None else None}


def _worker(root: Path) -> int:
    if HANDSHAKE is None or root.resolve() != root:
        raise PermissionError("UNIVERSE_SCAN_RESOURCE_HANDSHAKE_REQUIRED")
    intent_path = _file(root / "SCAN_INTENT.json", root)
    intent = read_json(intent_path)
    if (intent["intent_identity"] != stable_hash({k: v for k, v in intent.items() if k != "intent_identity"})
            or intent["root"] != str(root) or HANDSHAKE.get("execution") != {
                "purpose": intent["scan_id"], "intent_identity": intent["intent_identity"]}
            or HANDSHAKE["memory_mib"] != LIMITS["memory_mib"]
            or not 0 < HANDSHAKE["wall_seconds"] <= LIMITS["wall_seconds"]):
        raise PermissionError("UNIVERSE_SCAN_WORKER_SCOPE_CONFLICT")
    try:
        _check_code(intent["source_hashes"])
        _check_registration(intent["registration"])
        request = intent["preview"]["request"]
        _authorize(intent["authority"], request)
        metadata = _file(root / "REGISTERED_MANIFEST.json", root)
        if (_sha(metadata) != intent["registration_snapshot_sha256"]
                or read_json(metadata) != intent["registration"]["manifest"]):
            raise ValueError("UNIVERSE_SCAN_REGISTRATION_SNAPSHOT_CHANGED")
        from chanlun_trader.research_factory.universe_data_provider_v1 import UniverseDataProviderV1
        from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
        from chanlun_trader.research_factory.universe_submission_v1 import freeze_universe_bundle
        provider = UniverseDataProviderV1({"registered": intent["registration"]["root"]})
        # 副本只作解析内容回执；来源身份必须沿用已核验的原登记文件字节哈希。
        provider.register_manifest(request["dataset_id"], "registered", intent["registration"]["original_metadata_path"])
        _, registered, digest, _ = provider._datasets[request["dataset_id"]]
        if (digest != intent["registration"]["original_metadata_sha256"]
                or registered != intent["registration"]["manifest"]):
            raise ValueError("UNIVERSE_SCAN_REGISTERED_METADATA_CHANGED")
        strategy = public_rule_factory(request["rule"], request["strategy_id"])
        if strategy.rule_identity != intent["preview"]["rule_identity"]:
            raise ValueError("UNIVERSE_SCAN_RULE_CHANGED")
        prepared, inputs = provider._prepare_with_inputs(request["dataset_id"], symbols=request["symbols"],
            universe_id=request["universe_id"], feature_start=request["feature_start"],
            account_start=request["account_start"], account_end=request["account_end"],
            purpose=request["purpose"], stage="SCAN", normalization_fields=(),
            required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions,
            authorization=intent["authority"]["data_authorization"])
        if sorted(prepared["window"]["symbols"]) != request["symbols"]:
            raise ValueError("UNIVERSE_SCAN_PROVIDER_SHRANK_TARGETS")
        _check_registration(intent["registration"])
        freeze_universe_bundle(prepared, root)
        evaluated = _evaluate_conditions(strategy, inputs)
        _check_code(intent["source_hashes"])
        _check_registration(intent["registration"])
        coverage = deepcopy(inputs.coverage)
        coverage["completeness"] = intent["preview"]["data_metadata"]["completeness"]
        _write_result(root, {**_base_result(intent), **evaluated,
            "status": "SCAN_DATA_GAPS" if evaluated["unknown_target_count"] else "CONDITIONS_EVALUATED",
            "input_identity": inputs.input_identity, "coverage": coverage,
            "historical_availability": inputs.historical_availability,
            "source_hashes": deepcopy(inputs.bundle["source_hashes"]),
            "source_bytes_verified_in_worker": True})
        return 0
    except Exception as error:
        _write_result(root, _blocked_result(intent, str(error) or type(error).__name__))
        return 1


def _receipt(root: Path, intent: dict, resource: dict) -> dict:
    result_path, resource_path = _file(root / "RESULT.json", root), _file(root / "RESOURCE.json", root)
    result = read_json(result_path)
    if (result.get("scan_identity") != stable_hash({k: v for k, v in result.items() if k != "scan_identity"})
            or result.get("scan_id") != intent["scan_id"]
            or result.get("scope", {}).get("preview_identity") != intent["preview"]["preview_identity"]
            or result.get("target_count") != len(intent["preview"]["request"]["symbols"])
            or sorted(row["symbol"] for row in result.get("per_symbol", [])) != intent["preview"]["request"]["symbols"]):
        raise ValueError("UNIVERSE_SCAN_RESULT_SCOPE_CONFLICT")
    if (resource.get("returncode") != 0 or resource.get("timed_out")) and result["status"] != "SCAN_BLOCKED":
        raise ValueError("UNIVERSE_SCAN_RESOURCE_RESULT_CONFLICT")
    artifacts = {str(path): {"sha256": _sha(path), "physical_signature": _signature(path)}
                 for path in (result_path, resource_path, _file(root / "REPORT_CN.md", root))}
    input_path = root / "INPUT.json"
    if input_path.exists():
        input_path = _file(input_path, root)
        snapshot = read_json(input_path)
        for info in snapshot["frames"].values():
            path = _file(Path(info["path"]), root)
            artifacts[str(path)] = {"sha256": info["sha256"], "physical_signature": _signature(path)}
        artifacts[str(input_path)] = {"sha256": _sha(input_path), "physical_signature": _signature(input_path)}
    value = {"version": VERSION, "intent_identity": intent["intent_identity"],
             "scan_identity": result["scan_identity"], "artifacts": artifacts}
    value["receipt_identity"] = stable_hash(value)
    immutable(root / "SCAN_RECEIPT.json", value)
    return result


def _archived(root: Path, intent: dict) -> dict:
    receipt = read_json(_file(root / "SCAN_RECEIPT.json", root))
    if (receipt.get("intent_identity") != intent["intent_identity"] or receipt.get("receipt_identity")
            != stable_hash({k: v for k, v in receipt.items() if k != "receipt_identity"})):
        raise ValueError("UNIVERSE_SCAN_RECEIPT_CONFLICT")
    for name, expected in receipt["artifacts"].items():
        path = _file(Path(name), root)
        if _signature(path) != expected["physical_signature"]:
            raise ValueError("UNIVERSE_SCAN_ARCHIVED_ARTIFACT_CHANGED")
        # 查询不读取行情 Parquet；只检查登记 stat 和非行情工件的绑定哈希。
        if path.suffix != ".parquet" and _sha(path) != expected["sha256"]:
            raise ValueError("UNIVERSE_SCAN_ARCHIVED_ARTIFACT_CHANGED")
    result = read_json(root / "RESULT.json")
    if result["scan_identity"] != receipt["scan_identity"]:
        raise ValueError("UNIVERSE_SCAN_RECEIPT_RESULT_CONFLICT")
    return {**result, "recorded_only": True, "content_reread": False, "freshly_scanned": False}


def scan_universe(service, request: dict, preview_identity: str) -> dict:
    """受既有数据授权执行一次公共全范围检查，不创建或消费账户预算。"""
    if not isinstance(request, dict) or request.get("version") != "FULL_UNIVERSE_SUBMISSION_V1":
        raise ValueError("UNIVERSE_SCAN_REQUEST_REQUIRED")
    preview = service.preview(request)
    if preview["preview_identity"] != preview_identity:
        raise ValueError("SUBMISSION_PREVIEW_CHANGED")
    normalized = preview["request"]
    authority = service.authority(normalized["authorization_ref"])
    approved = _authorize(authority, normalized)
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    strategy = public_rule_factory(normalized["rule"], normalized["strategy_id"])
    key = stable_hash({"objective_id": approved["objective_id"], "dataset_id": normalized["dataset_id"],
        "universe_id": normalized["universe_id"], "rule_identity": strategy.rule_identity,
        **{name: normalized[name] for name in ("feature_start", "account_start", "account_end")}})
    root = service.root / "signal-scans" / key
    if service.root.resolve() != service.root or root.resolve() != root:
        raise ValueError("UNIVERSE_SCAN_ROOT_REDIRECTED")
    registration, hashes = _registration(service.provider, normalized), _source_files(strategy)
    with ObjectiveMutationLock.for_resource(root / "SCAN_INTENT.json"):
        metadata_path = root / "REGISTERED_MANIFEST.json"
        if metadata_path.exists() and read_json(_file(metadata_path, root)) != registration["manifest"]:
            raise ValueError("UNIVERSE_SCAN_REGISTRATION_CHANGED_REQUIRES_RECONCILIATION")
        immutable(metadata_path, registration["manifest"])
        intent = {"version": VERSION, "scan_id": key, "root": str(root), "preview": preview,
            "authority": approved, "authorization_identity": stable_hash(authority),
            "registration": registration, "registration_snapshot_sha256": _sha(metadata_path),
            "source_hashes": hashes, "limits": deepcopy(LIMITS)}
        intent["intent_identity"] = stable_hash(intent)
        path = root / "SCAN_INTENT.json"
        if path.exists() and read_json(_file(path, root)) != intent:
            raise ValueError("UNIVERSE_SCAN_INTENT_CHANGED_REQUIRES_RECONCILIATION")
        immutable(path, intent)
        if (root / "SCAN_RECEIPT.json").exists():
            return _archived(root, intent)
        if (root / "START.json").exists():
            raise ValueError("UNIVERSE_SCAN_INTERRUPTED_REQUIRES_RECONCILIATION")
        remaining = (datetime.fromisoformat(approved["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise PermissionError("UNIVERSE_SCAN_AUTHORITY_EXPIRED")
        immutable(root / "START.json", {"intent_identity": intent["intent_identity"],
                                      "started_at": datetime.now(timezone.utc).isoformat()})
        env = os.environ.copy()
        env.update(PYTHONPATH=os.pathsep.join((str(REPO / "src"), str(REPO))), PYTHONDONTWRITEBYTECODE="1")
        env.pop("CHANLUN_TEST_ISOLATION", None)
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[name] = "1"
        try:
            resource = run_bounded_worker([sys.executable, str(Path(__file__)), "--worker", str(root)],
                root=REPO, memory_mib=LIMITS["memory_mib"], wall_seconds=min(remaining, LIMITS["wall_seconds"]),
                environment=env, execution={"purpose": key, "intent_identity": intent["intent_identity"]},
                on_started=lambda pid: immutable(root / "WORKER.json", {"pid": pid, "scan_id": key}))
        except Exception as error:
            resource = {"returncode": None, "timed_out": False, "error": str(error)}
        resource = {k: v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for k, v in resource.items()}
        immutable(root / "RESOURCE.json", resource)
        if not (root / "RESULT.json").exists():
            _write_result(root, _blocked_result(intent, "UNIVERSE_SCAN_WORKER_FAILED_OR_INTERRUPTED"))
        result = _receipt(root, intent, resource)
        return {**result, "recorded_only": False, "content_reread": True, "freshly_scanned": True}


def validated_scan_snapshot(service, scanned: dict) -> dict:
    """只从当前服务的已验证扫描取冻结输入引用；不读取 Parquet 或启动工作。"""
    if (not isinstance(scanned, dict) or scanned.get("version") != VERSION
            or not isinstance(scanned.get("scan_id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", scanned["scan_id"])):
        raise ValueError("UNIVERSE_SCAN_SNAPSHOT_REFERENCE_INVALID")
    root = service.root / "signal-scans" / scanned["scan_id"]
    if service.root.resolve() != service.root or root.resolve() != root:
        raise ValueError("UNIVERSE_SCAN_ROOT_REDIRECTED")
    intent = read_json(_file(root / "SCAN_INTENT.json", root))
    if (intent.get("root") != str(root) or intent.get("scan_id") != scanned["scan_id"]
            or intent.get("intent_identity") != stable_hash({k: v for k, v in intent.items() if k != "intent_identity"})):
        raise ValueError("UNIVERSE_SCAN_SNAPSHOT_INTENT_CONFLICT")
    # 路径允许范围来自当前服务的真实登记，不由可替换工件的自报哈希授予。
    if intent["registration"] != _registration(service.provider, intent["preview"]["request"]):
        raise ValueError("UNIVERSE_SCAN_SNAPSHOT_REGISTRATION_CONFLICT")
    _check_code(intent["source_hashes"])
    _check_registration(intent["registration"])
    archived = _archived(root, intent)
    if (archived.get("scan_identity") != scanned.get("scan_identity")
            or not archived.get("input_identity") or archived["input_identity"] != scanned.get("input_identity")
            or archived.get("scope") != scanned.get("scope")):
        raise ValueError("UNIVERSE_SCAN_SNAPSHOT_RESULT_CONFLICT")
    path = _file(root / "INPUT.json", root)
    receipt = read_json(root / "SCAN_RECEIPT.json")
    reference = receipt["artifacts"].get(str(path))
    snapshot = read_json(path)
    if (not reference or reference["sha256"] != _sha(path)
            or snapshot.get("snapshot_version") != "UNIVERSE_FROZEN_INPUT_V1"
            or snapshot.get("input_identity") != archived["input_identity"]
            or sorted(snapshot.get("window", {}).get("symbols", [])) != intent["preview"]["request"]["symbols"]):
        raise ValueError("UNIVERSE_SCAN_SNAPSHOT_BINDING_CONFLICT")
    return {"path": str(path), "sha256": reference["sha256"],
            "input_identity": archived["input_identity"], "scan_identity": archived["scan_identity"],
            "metadata_only": True, "receipt_identity": receipt["receipt_identity"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args(argv)
    return _worker(args.worker.absolute())


if __name__ == "__main__":
    raise SystemExit(main())
