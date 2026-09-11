"""在新进程中复核比较集，或通过实际registry写入入口验证竞争。"""
import json
from pathlib import Path
import sys

from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractRegistryV1
from chanlun_trader.research_factory.mutation_boundary import MutationBusyError
from chanlun_trader.research_factory.synthetic_novelty import SyntheticNoveltyBindingServiceV1


root = Path(sys.argv[1])
if sys.argv[2] == "write":
    registry = DurableFrozenCandidateContractRegistryV1(root / sys.argv[3])
    for contract in DurableFrozenCandidateContractRegistryV1(root / sys.argv[4]).items():
        registry.append(contract)
    try:
        registry.write()
    except MutationBusyError:
        print("REGISTRY_WRITE_BLOCKED_BY_EXISTING_SHARED_LOCK", flush=True)
        sys.exit(23)
    print("REGISTRY_WRITE_COMPLETED", flush=True)
else:
    service = SyntheticNoveltyBindingServiceV1(root)
    with service.performance_boundary(*sys.argv[3:6]) as evidence:
        print(json.dumps(evidence), flush=True)
