"""独立进程通过实际创建/恢复服务确认合成目标。"""
import json
import os
from pathlib import Path
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.objective_execution_binding import ResearchProposalGovernanceServiceV2
from chanlun_trader.research_factory.mutation_boundary import MutationBusyError


root, proposal_id, action = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
service = ResearchProposalGovernanceServiceV2(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"),
    crash_at="after_objective_write" if action == "crash" else None)
try:
    if action == "recover":
        result = service.recover(proposal_id)
    else:
        result = service.confirm(proposal_id, json.loads((root / "test-confirm-input.json").read_bytes()))
except MutationBusyError:
    print("OBJECTIVE_CONFIRM_BLOCKED_BY_SHARED_LOCK", flush=True)
    sys.exit(23)
except RuntimeError as exc:
    if action == "crash" and str(exc) == "SYNTHETIC_PROPOSAL_GOVERNANCE_CRASH:after_objective_write":
        print("OBJECTIVE_CREATED_PROCESS_EXIT_BEFORE_REMAINING_TRANSACTION", flush=True)
        os._exit(73)
    raise
print(json.dumps(result), flush=True)
