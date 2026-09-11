"""真实服务的性能前并发变化；测试只暂停进程，不替代 Gate 或授权结果。"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time

import pytest

from chanlun_trader.research_factory.synthetic_batch import SyntheticBatchServiceV1
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock, MutationBusyError
from test_synthetic_batch_contract import prepare_batch, approval


@pytest.mark.parametrize("change", ["revoke", "source", "snapshot"])
def test_current_parent_and_sources_rechecked_immediately_before_performance(monkeypatch, change):
    root, policy, body = prepare_batch()
    service = SyntheticBatchServiceV1(root, policy)
    preview = service.request(body)
    identifier = preview["batch_authorization_id"]
    service.confirm(identifier, approval(preview))
    assert service.execute_next(identifier)["status"] == "ACTIVE"
    from chanlun_trader.research_factory import synthetic_batch
    bounded = synthetic_batch.run_bounded_worker

    def wait_at_real_boundary(command, **kwargs):
        if change == "snapshot":
            kwargs["environment"] = {**kwargs["environment"], "R3_TEST_RACE_STAGE": "SNAPSHOT"}
        return bounded([command[0], str(Path(__file__).with_name("batch_boundary_race_worker.py"))], **kwargs)

    monkeypatch.setattr(synthetic_batch, "run_bounded_worker", wait_at_real_boundary)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.execute_next, identifier)
        try:
            deadline = time.monotonic() + 30
            while not (root / "r3-performance-boundary-ready").exists():
                if pending.done():
                    pytest.fail(str(pending.result()))
                if time.monotonic() >= deadline:
                    pytest.fail("真实 worker 未到达性能边界")
                time.sleep(0.01)
            if change == "revoke":
                service.control(identifier, "revoke", approval(preview))
            else:
                source = root / "data/research/data_routing/routing_policy.json"
                if change == "snapshot":
                    with pytest.raises(MutationBusyError):
                        with ObjectiveMutationLock.for_resource(source):
                            pytest.fail("输入快照读取期间不能替换已确认来源")
                else:
                    with ObjectiveMutationLock.for_resource(source):
                        source.write_bytes(source.read_bytes() + b"\n")
        finally:
            (root / "r3-performance-boundary-release").write_text("RELEASE", encoding="utf-8")
        result = pending.result(timeout=30)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / root.name
    (output / "boundary-result.json").write_text(json.dumps(result), encoding="utf-8")
    for path in (service._directory(identifier) / "executions").glob("*/*"):
        (output / (path.parent.name + "-" + path.name)).write_bytes(path.read_bytes())
    assert result["status"] == {"revoke": "REVOKED", "source": "BLOCKED", "snapshot": "ACTIVE"}[change]
    assert json.loads((root / "r3-boundary-counts.json").read_bytes()) == ({"engine": 2, "performance": 1}
        if change == "snapshot" else {"engine": 0, "performance": 0})
    budget = json.loads((root / preview["bindings"][0]["budget_ref"]).read_bytes())
    assert all(row["used"] == (1 if change == "snapshot" else 0) and row["reserved"] == 0 for row in budget["buckets"])
