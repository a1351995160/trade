"""合成工作台的显式磁盘装配描述；只绑定文件，不携带运行批准。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

from ..engine.ledger import PortfolioLedger
from ..execution_policy import ExecutionPolicy, validate_research_root
from .common import stable_hash
from .engineering_workbench import EngineeringWorkbenchV1
from .paper_replay import _immutable
from .portfolio_plan import PortfolioPreviewPolicyV1
from .predictive_executor import CanonicalPredictiveExecutorV1


def _inside(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("WORKSPACE_RELATIVE_PATH_REQUIRED")
    result = root / path
    if result.resolve() != result or not result.is_relative_to(root):
        raise ValueError("WORKSPACE_LINKED_PATH")
    return result


def save_engineering_workspace(path, service: EngineeringWorkbenchV1):
    path = Path(path)
    base = path.parent
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("WORKSPACE_EXPLICIT_CONFIG_REQUIRED")
    ledger = service.ledger
    if ledger.positions or ledger.lots or ledger.trades or ledger.reserved_cash or ledger.cash != ledger.initial_cash:
        raise ValueError("WORKSPACE_INITIAL_PLANNING_ACCOUNT_REQUIRED")
    sources = []
    for candidate_id, source in service.sources.items():
        contract = source["contract"]
        batch = str(contract.source_provenance["batch_id"])
        contract_ref = f"data/research/research_factory/batches/{batch}/durable_frozen_candidate_contracts.json"
        _inside(Path(source["root"]), contract_ref)
        sources.append({"root": Path(source["root"]).relative_to(base).as_posix(),
            "objective_id": contract.policy_identity["objective_id"], "candidate_id": candidate_id,
            "candidate_hash": contract.candidate_hash, "contract_ref": contract_ref,
            "contract_hash": contract.content_hash,
            "input_identity": source["inputs"]["input_diagnostics"]["input_identity"]})
    value = {"schema_version": "engineering-workspace-v1", "sources": sources,
        "input_root": service.root.relative_to(base).as_posix(),
        "output_root": service.output_root.relative_to(base).as_posix(),
        "planning_initial_cash": ledger.initial_cash,
        "portfolio_policy": service.portfolio_policy.model_dump(mode="json"),
        "real_execution_authorized": False}
    value["manifest_identity"] = stable_hash(value)
    _immutable(path, value)
    return path


def load_engineering_workspace(path) -> EngineeringWorkbenchV1:
    """显式调用才读取指定配置与输入；不启动引擎、不修复历史归档。"""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("WORKSPACE_EXPLICIT_CONFIG_REQUIRED")
    base = path.parent
    validate_research_root(base, ExecutionPolicy("READ_ONLY", "SYNTHETIC"))
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("schema_version") != "engineering-workspace-v1" or value.get("real_execution_authorized") is not False
            or value.get("manifest_identity") != stable_hash({key: item for key, item in value.items() if key != "manifest_identity"})):
        raise ValueError("WORKSPACE_CONFIG_IDENTITY_CONFLICT")
    policy = PortfolioPreviewPolicyV1.model_validate_json(json.dumps(value["portfolio_policy"]))
    cash = value["planning_initial_cash"]
    if type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0:
        raise ValueError("WORKSPACE_PLANNING_CASH_INVALID")
    root = _inside(base, value["input_root"])
    output = _inside(base, value["output_root"])
    sources = {}
    for descriptor in value["sources"]:
        source_root = _inside(base, descriptor["root"])
        if not source_root.is_relative_to(root):
            raise ValueError("WORKSPACE_INPUT_ROOT_CONFLICT")
        _inside(source_root, descriptor["contract_ref"])
        caller = CanonicalPredictiveExecutorV1(source_root, descriptor["objective_id"])
        contract, record = caller._contract_and_record(SimpleNamespace(**descriptor))
        if contract.content_hash != descriptor["contract_hash"] or contract.candidate_id in sources:
            raise ValueError("WORKSPACE_CONTRACT_IDENTITY_CONFLICT")
        execution, _, _ = caller._load_policy(contract)
        inputs = caller._prepare_inputs(execution, record, caller._corrected_module(),
            source_root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=contract)
        if inputs["input_diagnostics"]["input_identity"] != descriptor["input_identity"]:
            raise ValueError("WORKSPACE_INPUT_CHANGED")
        sources[contract.candidate_id] = dict(root=source_root, contract=contract, record=record, policy=execution, inputs=inputs)
    return EngineeringWorkbenchV1(root, sources, PortfolioLedger(cash), policy, output)


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser(description="显式合成工作台配置检查与本机服务；默认只读")
    parser.add_argument("command", choices=("inspect", "serve"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--governed", action="store_true", help="允许已配置合成域的显式确认操作，不授予真实权限")
    args = parser.parse_args()
    if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
        parser.error("IMPORT_TIME_ISOLATION_REQUIRED")
    if args.command == "serve" and (args.port is None or not 1 <= args.port <= 65535):
        parser.error("EXPLICIT_LOCAL_PORT_REQUIRED")
    service = load_engineering_workspace(args.config)
    if args.command == "inspect":
        print(json.dumps({"read_only": True, **service.inspect()}, ensure_ascii=False))
    else:
        import uvicorn
        from chanlun_trader.webapp import create_app
        policy = ExecutionPolicy("GOVERNED" if args.governed else "READ_ONLY", "SYNTHETIC")
        uvicorn.run(create_app(service.root, policy, engineering_workbench=service), host="127.0.0.1", port=args.port)
