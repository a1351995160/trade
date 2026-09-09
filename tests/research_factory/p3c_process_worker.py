"""仅从临时 root、Objective 和显式操作重建服务，不接收旧 Action。"""
import json
import os
from pathlib import Path
import sys

from p3c_scenario import Scenario
from chanlun_trader.research_factory.candidate_executable_materialization import CandidateExecutableMaterializationManagerV1


COUNTS = {"forbidden_predictive": 0, "forbidden_structural": 0, "forbidden_ai": 0}


def install_guards():
    from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1
    from chanlun_trader.research_factory.real_sample_feasibility import RealSampleFeasibilityProviderV1
    from chanlun_trader.research_factory.autonomous_orchestrator_v2 import CodexExecBatchInvokerV2

    def guard(key):
        def forbidden(*args, **kwargs):
            COUNTS[key] += 1
            raise AssertionError(key)
        return forbidden

    CanonicalPredictiveExecutorV1.execute = guard("forbidden_predictive")
    RealSampleFeasibilityProviderV1.build = guard("forbidden_structural")
    CodexExecBatchInvokerV2.invoke = guard("forbidden_ai")


def report():
    import sitecustomize
    print("P3C_WORKER_PROBES=" + json.dumps({"executors": COUNTS, "process": sitecustomize.counts}), file=sys.stderr, flush=True)


def main():
    install_guards()
    root, objective, operation = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    scenario = Scenario(root, objective)
    if operation in {"confirmation_exit", "durable_exit"}:
        point = "after_confirmation_receipt" if operation == "confirmation_exit" else "after_durable_contract_append"

        def crash(manager, *points):
            if point in points:
                scenario.event("hard_exit", point=point)
                report()
                os._exit(94)

        CandidateExecutableMaterializationManagerV1._inject_crash = crash
        scenario.confirm()
    elif operation == "structural_exit":
        scenario.structural()
        scenario.event("hard_exit", point="structural_canonical_written")
        report()
        os._exit(95)
    elif operation == "authorize":
        result = scenario.authorize()
    elif operation == "structural":
        result = scenario.structural()
    elif operation == "confirm":
        result = scenario.confirm()
    elif operation == "freeze":
        result = scenario.freeze()
    elif operation == "inspect":
        result = scenario.plane.inspect(objective)
    elif operation == "dry_run":
        result = scenario.plane.tick(objective, dry_run=True)
    else:
        raise ValueError(operation)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    report()


if __name__ == "__main__":
    main()
