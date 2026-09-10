"""新进程加载现场冻结合同与文件，持久化后真实退出，供恢复核验。"""
import json
import os
from pathlib import Path
import sys

from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.paper_replay import PaperReplaySessionV1
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.common import canonical_json

if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
    raise RuntimeError("IMPORT_TIME_ISOLATION_REQUIRED")
root, output = Path(sys.argv[1]), Path(sys.argv[2])
contract = DurableFrozenCandidateContractV1.from_dict(json.loads((root / "caller-contract.json").read_bytes()))
caller = CanonicalPredictiveExecutorV1(root, sys.argv[3])
policy, _, _ = caller._load_policy(contract)
record = contract.reconstruct_candidate()
inputs = caller._prepare_inputs(policy, record, caller._corrected_module(),
    root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=contract)
session = PaperReplaySessionV1(root, output, record, contract, policy, inputs)
if sys.argv[4] in {"before_head", "after_head"}:
    original_commit = session._commit_progress
    def interrupt_commit():
        if session.completed_events == 9 and sys.argv[4] == "before_head":
            os._exit(73)
        original_commit()
        if session.completed_events == 9:
            os._exit(73)
    session._commit_progress = interrupt_commit
    session.advance(9)
if sys.argv[4] == "interrupt":
    session.advance(9)
    import sitecustomize
    print("PAPER_ISOLATION_COUNTS=" + json.dumps(sitecustomize.counts), flush=True)
    print("PAPER_COMMITTED_BEFORE_EXIT=9", flush=True)
    os._exit(73)
result = session.advance(len(session.engine.clock.events))
print(canonical_json(result), flush=True)
