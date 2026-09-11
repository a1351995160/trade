"""新鲜进程实际准备/运行/适配；不运行 pytest 或完整治理执行入口。"""
import json
from pathlib import Path
import sys


counts = {"synthetic_engine_run": 0, "forbidden": 0, "writes": 0}


def audit(event, args):
    if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "subprocess.Popen", "socket.connect"}:
        counts["forbidden"] += 1
        raise AssertionError("R1_COLD_SIDE_EFFECT:" + event)
    if event == "open":
        mode, flags = args[1:3]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & 3):
            counts["writes"] += 1
            raise AssertionError("R1_COLD_WRITE")


def observe(frame, event, arg):
    if event != "call":
        return
    module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
    if module == "chanlun_trader.engine.engine" and name == "run":
        counts["synthetic_engine_run"] += 1
    if ((module.endswith("predictive_executor") and name == "execute")
            or (module.endswith("trial_adapter") and name == "mark_performance_accessed")
            or (module in {"chanlun_trader.research_factory.predictive_trial_start",
                "chanlun_trader.research_factory.predictive_trial_reauthorization",
                "chanlun_trader.research_factory.real_runtime",
                "chanlun_trader.research_factory.codex_backend"} and name in {"start", "run", "invoke", "resume", "recover_all"})):
        counts["forbidden"] += 1
        raise AssertionError("R1_COLD_FORMAL_PATH")


sys.addaudithook(audit)
sys.setprofile(observe)
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT

root = Path(sys.argv[1])
contract = DurableFrozenCandidateContractV1.from_dict(json.loads((root / "caller-contract.json").read_bytes()))
caller = CanonicalPredictiveExecutorV1(root, sys.argv[2])
policy, _, _ = caller._load_policy(contract)
record = contract.reconstruct_candidate()
inputs = caller._prepare_inputs(policy, record, caller._corrected_module(),
    root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=contract)
result = caller._invoke_runner(policy, record, "COLD_SYNTHETIC", inputs, portfolio_name="BASE_RESEARCH")
assert {t.side.value for t in result["engine"].ledger.valid_trades} == {"BUY", "SELL"}
assert result["metrics_ref"] is None
assert result["ready_for_real_trial"] is False
for name, module in list(sys.modules.items()):
    if name.startswith("chanlun_trader") and getattr(module, "__file__", None):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE_ROOT / "src")
assert counts == {"synthetic_engine_run": 1, "forbidden": 0, "writes": 0}
print(json.dumps({**counts, "python": sys.version, "source": str(SOURCE_ROOT), "metrics_identity": result["metrics"]["source_identity"]}))
