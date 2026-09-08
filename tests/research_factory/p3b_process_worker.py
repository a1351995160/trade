"""P3-B 子进程故障注入；只接收临时 root 与 Objective，不接收旧 Action。"""
from pathlib import Path
import json
import os
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.autonomous_control_plane import AutonomousResearchControlPlaneV1
from chanlun_trader.research_factory.candidate_generation import CandidateGenerationManagerV1
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock


def main():
    root, objective, operation = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    policy = ExecutionPolicy(mode="GOVERNED", workspace_kind="SYNTHETIC")
    plane = AutonomousResearchControlPlaneV1(root, execution_policy=policy)
    original = plane._execute_domain_action

    def event(value):
        with (root / "process_evidence.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"operation": operation, "event": value}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def execute(action):
        if operation == "exit_before":
            os._exit(91)
        if operation == "hold_tick":
            print("LOCKED", flush=True)
            assert sys.stdin.readline().strip() == "GO"
        event("provider_attempt")
        result = original(action)
        event("domain_returned")
        if operation == "exit_after":
            os._exit(92)
        return result

    plane._execute_domain_action = execute
    if operation.startswith("graph_"):
        from chanlun_trader.research_factory.artifact_graph import ResearchArtifactGraphV1
        graph = ResearchArtifactGraphV1(root / "shared_graph.json")
        print("LOADED", flush=True)
        assert sys.stdin.readline().strip() == "GO"
        graph.add_node(operation, "Objective", {"objective_id": objective})
        print(json.dumps(graph.to_dict()), flush=True)
        return
    if operation == "hold_lock":
        with ObjectiveMutationLock(root, "objective:" + objective):
            print("LOCKED", flush=True)
            assert sys.stdin.readline().strip() == "GO"
        return
    if operation == "hold_domain":
        original_load = CandidateGenerationManagerV1._load_sources

        def load(manager, objective_id):
            print("LOCKED", flush=True)
            assert sys.stdin.readline().strip() == "GO"
            return original_load(manager, objective_id)

        CandidateGenerationManagerV1._load_sources = load
        result = CandidateGenerationManagerV1(root).generate_proposal(objective)
    elif operation == "recover":
        result = plane.recover(objective)
    else:
        result = plane.tick(objective)
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
