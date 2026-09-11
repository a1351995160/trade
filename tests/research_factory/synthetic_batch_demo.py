"""新建真实服务合成工作区供本机界面验收；不自动确认批次或使用资格。"""
import argparse
import json
import os

from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.research_factory.engineering_workbench import EngineeringWorkbenchV1
from chanlun_trader.research_factory.portfolio_plan import PortfolioPreviewPolicyV1, StrategyAllocationV1
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.structural_entry import _candidate_from_contract_path
from chanlun_trader.webapp import create_app
from test_synthetic_batch_contract import prepare_batch


def build():
    if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
        raise ValueError("IMPORT_TIME_ISOLATION_REQUIRED")
    root, execution, body = prepare_batch(multiple=True)
    sources = {}
    for member in body["candidates"]:
        _, contract, _ = _candidate_from_contract_path(root, member["objective_id"], member["candidate_id"])
        caller = CanonicalPredictiveExecutorV1(root, member["objective_id"])
        policy, _, _ = caller._load_policy(contract)
        record = contract.reconstruct_candidate()
        inputs = caller._prepare_inputs(policy, record, caller._corrected_module(),
            root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=contract)
        sources[contract.candidate_id] = dict(root=root, contract=contract, record=record, policy=policy, inputs=inputs)
    portfolio = PortfolioPreviewPolicyV1(policy_id="R3_SYNTHETIC_UI_TEST", version="1",
        allocations=tuple(StrategyAllocationV1(candidate_id=key, contract_hash=value["contract"].content_hash,
            cash_bps=5000, priority=index) for index, (key, value) in enumerate(sources.items())),
        overlap="ONE_STRATEGY_PER_SYMBOL", buy_sell_conflict="BLOCK_BUY_WHEN_EXIT_DUE",
        max_positions=20, max_symbol_exposure_bps=10000, max_buy_turnover_bps=10000,
        valid_until="2025-08-01T15:00:00+08:00")
    workbench = EngineeringWorkbenchV1(root, sources, PortfolioLedger(1000000.), portfolio,
        root.with_name(root.name + "-planning-output"))
    print(json.dumps({"synthetic_root": str(root), "candidates": body["candidates"],
        "batch_confirmed": False, "real_execution_authorized": False}), flush=True)
    return create_app(root, execution, engineering_workbench=workbench)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build(), host="127.0.0.1", port=args.port)
