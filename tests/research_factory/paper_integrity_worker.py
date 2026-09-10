"""新进程核对实际Paper归档并尝试恢复，保留损坏拒绝证据。"""
import json
from pathlib import Path
import sys

from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.paper_replay import PaperReplaySessionV1, read_paper_archive
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1

root, output = Path(sys.argv[1]), Path(sys.argv[2])
contract = DurableFrozenCandidateContractV1.from_dict(json.loads((root / "caller-contract.json").read_bytes()))
caller = CanonicalPredictiveExecutorV1(root, sys.argv[3])
policy, _, _ = caller._load_policy(contract)
record = contract.reconstruct_candidate()
inputs = caller._prepare_inputs(policy, record, caller._corrected_module(),
    root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=contract)
results = {}
for name, operation in {
    "read": lambda: read_paper_archive(output),
    "advance": lambda: PaperReplaySessionV1(root, output, record, contract, policy, inputs).advance(10),
}.items():
    try:
        value = operation()
        results[name] = {"blocked": False, "completed_events": value["completed_events"]}
    except (ValueError, OSError) as exc:
        results[name] = {"blocked": True, "error": str(exc)}
print(json.dumps(results))
