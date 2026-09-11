"""实际冻结registry、测试确认、原Gate与共享锁；不授予Trial启动权限。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from r1_caller_fixture import fixture
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractRegistryV1, DurableFrozenCandidateContractV1
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research_factory.novelty import CandidateNoveltyGateV2
from chanlun_trader.research_factory.synthetic_novelty import SyntheticNoveltyBindingServiceV1
from chanlun_trader.research_factory.synthetic_novelty import canonical_novelty_boundary, SCHEMA
from chanlun_trader.research_factory.synthetic_novelty_cli import main
from chanlun_trader.research_factory.synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1
from chanlun_trader.research_factory.predictive_trial_start import PredictiveTrialStartError


POLICY = ExecutionPolicy("GOVERNED", "SYNTHETIC")


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("novelty-template")
    cases = [fixture(root / str(i), objective_id=f"NOVELTY_SYNTHETIC_{i}") for i in range(2)]
    return root, [(case[2], case[0].objective_id) for case in cases]


def setup(tmp_path, template, *, multiple=False):
    source, cases = template
    root = tmp_path / "workspace"
    shutil.copytree(source, root)
    paths = []
    for index, (contract, _) in enumerate(cases):
        registry = DurableFrozenCandidateContractRegistryV1(root / f"source-{index}.json")
        registry.append(contract)
        registry.write()
        paths.append(registry.path.relative_to(root).as_posix())
    service = SyntheticNoveltyBindingServiceV1(root)
    service.declare_sources(POLICY, paths if multiple else paths[:1])
    return service, cases[0][0], paths


def confirmed(service, contract):
    preview = service.preview(POLICY, contract.candidate_id, contract.content_hash)
    service.confirm(POLICY, {"preview_id": preview["preview_id"], "confirmed": True})
    return preview["preview_id"]


def test_verified_empty_and_nonempty_scopes_match_original_gate(tmp_path, template):
    service, contract, paths = setup(tmp_path, template)
    identifier = confirmed(service, contract)
    snapshot = service.historical_confirmation(identifier)[0]["snapshot"]
    assert snapshot["source_count"] == 1
    assert snapshot["member_occurrences_before_exclusion"] == 1
    assert snapshot["comparison_member_count"] == 0
    assert snapshot["excluded_self"]["contract_hash"] == contract.content_hash
    with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash) as evidence:
        assert evidence["passed"]
        assert not evidence["global_novelty_verified"]
    service.declare_sources(POLICY, paths)
    with pytest.raises(ValueError, match="STALE"):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("旧来源确认不能继续使用")
    identifier = confirmed(service, contract)
    snapshot = service.historical_confirmation(identifier)[0]["snapshot"]
    assert snapshot["comparison_member_count"] == 1
    expected = CandidateNoveltyGateV2().evaluate(snapshot["excluded_self"]["design"],
        historical_candidates=[item["design"] for item in snapshot["members"]])
    if expected.allowed:
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash) as actual:
            assert {key: actual[key] for key in expected.to_dict()} == expected.to_dict()
    else:
        with pytest.raises(ValueError, match=expected.reason):
            with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
                pytest.fail("原Gate拒绝不能被绑定服务绕过")


def test_duplicate_origins_are_preserved_and_only_exact_self_is_excluded(tmp_path, template):
    service, contract, paths = setup(tmp_path, template)
    shutil.copyfile(service.root / paths[0], service.root / "alias.json")
    service.declare_sources(POLICY, [paths[0], "alias.json"])
    snapshot = service.snapshot(contract.candidate_id, contract.content_hash)
    assert snapshot["member_occurrences_before_exclusion"] == 2
    assert snapshot["canonical_members_before_exclusion"] == 1
    assert len(snapshot["excluded_self"]["origins"]) == 2
    assert snapshot["comparison_member_count"] == 0
    with pytest.raises(ValueError, match="EXACT_SELF"):
        service.snapshot(contract.candidate_id, "wrong-contract")


@pytest.mark.parametrize("damage", ["missing", "malformed", "hash", "identity", "empty-replacement"])
def test_source_errors_never_become_empty_or_fall_back(tmp_path, template, damage):
    service, contract, paths = setup(tmp_path, template)
    identifier = confirmed(service, contract)
    path = service.root / paths[0]
    if damage == "missing":
        path.rename(service.root / "preserved-source.json")
    elif damage == "malformed":
        path.write_text("not-json", encoding="utf-8")
    else:
        value = json.loads(path.read_bytes())
        if damage == "hash":
            value["registry_hash"] = "wrong"
        elif damage == "identity":
            value["contracts"][0]["candidate_id"] = "CHANGED"
            value["registry_hash"] = stable_hash(value["contracts"])
        else:
            value["contracts"] = []
            value["registry_hash"] = stable_hash([])
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("损坏来源不能启动")
    assert service.historical_confirmation(identifier)[1]["test_confirmation"]


def test_scope_cannot_be_silently_reduced_or_client_members_substituted(tmp_path, template):
    service, contract, paths = setup(tmp_path, template, multiple=True)
    with pytest.raises(ValueError, match="SHRINK"):
        service.declare_sources(POLICY, paths[:1])
    with pytest.raises(ValueError, match="SOURCES_REQUIRED"):
        service.declare_sources(POLICY, [])
    with pytest.raises(TypeError):
        service.preview(POLICY, contract.candidate_id, contract.content_hash, members=[])
    unconfigured = SyntheticNoveltyBindingServiceV1(service.root / "0")
    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        unconfigured.snapshot(contract.candidate_id, contract.content_hash)


@pytest.mark.parametrize("damage", ["add", "delete", "modify"])
def test_confirmed_membership_changes_require_new_confirmation(tmp_path, template, damage):
    service, contract, paths = setup(tmp_path, template, multiple=damage != "add")
    identifier = confirmed(service, contract)
    target = service.root / paths[0 if damage == "add" else 1]
    if damage == "add":
        registry = DurableFrozenCandidateContractRegistryV1(target)
        registry.append(template[1][1][0])
        registry.write()
    else:
        value = json.loads(target.read_bytes())
        if damage == "delete":
            value["contracts"] = []
        else:
            value["contracts"][0]["content_hash"] = "changed"
        value["registry_hash"] = stable_hash(value["contracts"])
        target.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("不得自动刷新后沿用旧确认")


def test_confirmation_requires_actual_preview_explicit_operator_and_same_root(tmp_path, template):
    service, contract, _ = setup(tmp_path, template)
    preview = service.preview(POLICY, contract.candidate_id, contract.content_hash)
    with pytest.raises(ValueError, match="CONFIRMATION_REQUIRED"):
        service.confirm(POLICY, {"preview_id": preview["preview_id"], "approved": True})
    with pytest.raises(PermissionError):
        service.confirm(ExecutionPolicy(), {"preview_id": preview["preview_id"], "confirmed": True})
    with pytest.raises(FileNotFoundError):
        service.confirm(POLICY, {"preview_id": "0" * 64, "confirmed": True})
    identifier = confirmed(service, contract)
    before = (service.directory / "confirmations" / (identifier + ".json")).read_bytes()
    service.confirm(POLICY, {"preview_id": identifier, "confirmed": True})
    assert (service.directory / "confirmations" / (identifier + ".json")).read_bytes() == before
    shutil.copytree(service.root, tmp_path / "copied")
    copied = SyntheticNoveltyBindingServiceV1(tmp_path / "copied")
    with pytest.raises(ValueError, match="WORKSPACE_BINDING"):
        copied.historical_confirmation(identifier)


def test_comparison_success_does_not_grant_missing_start_permission(tmp_path, template):
    service, contract, paths = setup(tmp_path, template)
    # 正式start所需根是已生成合同的工作区，来源声明明确在该根内。
    candidate_root = service.root / "0"
    registry = next(candidate_root.glob("data/research/research_factory/batches/*/durable_frozen_candidate_contracts.json"))
    binding = SyntheticNoveltyBindingServiceV1(candidate_root)
    binding.declare_sources(POLICY, [registry.relative_to(candidate_root).as_posix()])
    identifier = confirmed(binding, contract)
    start = SyntheticNoveltyTrialStartServiceV1(candidate_root, POLICY, identifier)
    before = {path: path.read_bytes() for path in candidate_root.rglob("*budget*.json")}
    state = start.readiness(template[1][0][1])
    assert not state["available"]
    with pytest.raises(PredictiveTrialStartError):
        start.confirm(template[1][0][1], {"action": "START_PREDICTIVE_TRIAL_1", "confirmed": True,
            "start_intent_id": "UNAPPROVED", "candidate_id": contract.candidate_id,
            "candidate_hash": contract.candidate_hash, "preview_hash": "not-authority", "confirmation_token": "not-authority"})
    assert {path: path.read_bytes() for path in before} == before


def test_same_candidate_id_conflicting_valid_contracts_are_not_silently_deduplicated(tmp_path, template):
    service, contract, paths = setup(tmp_path, template)
    value = json.loads((service.root / paths[0]).read_bytes())
    value["contracts"][0]["created_frozen_timestamp"] = "2026-09-10T00:00:00+00:00"
    value["contracts"][0]["content_hash"] = stable_hash({k: v for k, v in value["contracts"][0].items() if k != "content_hash"})
    value["registry_hash"] = stable_hash(value["contracts"])
    (service.root / "conflict.json").write_text(json.dumps(value), encoding="utf-8")
    service.declare_sources(POLICY, [paths[0], "conflict.json"])
    with pytest.raises(ValueError, match="MEMBER_IDENTITY_CONFLICT"):
        service.snapshot(contract.candidate_id, contract.content_hash)


@pytest.mark.parametrize("holding_delta,reason", [(0, "EXACT_DUPLICATE_CANDIDATE"), (1, "PARAMETER_NEIGHBOR_CANDIDATE")])
def test_named_other_candidate_with_same_design_is_compared_by_original_gate(tmp_path, template, holding_delta, reason):
    service, contract, paths = setup(tmp_path, template)
    # 仅比较用合同夹具：不同候选身份继承同一冻结谓词；不创建启动批准或 Trial。
    record = contract.reconstruct_candidate()
    candidate = StrategyCandidateSpec.create({**record.candidate.to_dict(),
        "candidate_id": "CAND_SYNTHETIC_RENAMED_V1", "name": "合成同谓词比较候选",
        "holding_period": record.candidate.holding_period + holding_delta})
    record = replace(record, candidate=candidate, preregistration_hash=candidate.preregistration_hash)
    neighbor = DurableFrozenCandidateContractV1.from_semantic_record(record,
        {"hypothesis_fingerprint": contract.hypothesis_fingerprint},
        factor_event_registry_identities=contract.factor_event_registry_identities,
        research_period_identity=contract.research_period_identity, policy_identity=contract.policy_identity,
        source_provenance=contract.source_provenance)
    registry = DurableFrozenCandidateContractRegistryV1(service.root / paths[0])
    registry.append(neighbor)
    registry.write()
    snapshot = service.snapshot(contract.candidate_id, contract.content_hash)
    other = snapshot["members"][0]
    assert other["candidate_id"] != contract.candidate_id
    assert other["design"]["semantic_fingerprint"] == snapshot["excluded_self"]["design"]["semantic_fingerprint"]
    identifier = confirmed(service, contract)
    with pytest.raises(ValueError, match=reason):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("换名相似候选不能被当成自身排除")


def test_performance_reports_are_not_read_or_projected(tmp_path, template, monkeypatch):
    service, contract, _ = setup(tmp_path, template)
    report = service.root / "reports/performance.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text('{"net_return": 123.456, "classification":"RESEARCH_PASSED"}', encoding="utf-8")
    read_bytes, read_text = Path.read_bytes, Path.read_text
    def guard_bytes(path, *args, **kwargs):
        assert path != report
        return read_bytes(path, *args, **kwargs)
    def guard_text(path, *args, **kwargs):
        assert path != report
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_bytes", guard_bytes)
    monkeypatch.setattr(Path, "read_text", guard_text)
    identifier = confirmed(service, contract)
    with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash) as evidence:
        assert "123.456" not in json.dumps(evidence)
    snapshot = service.historical_confirmation(identifier)[0]["snapshot"]
    assert set(snapshot["excluded_self"]["design"]) == {"candidate_id", "candidate_hash", "mechanism",
        "factor_ids", "event_ids", "holding_period_days", "semantic_fingerprint", "parameter_fingerprint"}


def test_access_error_does_not_produce_empty_snapshot(tmp_path, template, monkeypatch):
    service, contract, paths = setup(tmp_path, template)
    original = Path.read_bytes
    def denied(path):
        if path == service.root / paths[0]:
            raise PermissionError("synthetic source access denied")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(PermissionError):
        service.preview(POLICY, contract.candidate_id, contract.content_hash)


def test_real_process_restart_and_registry_writer_cannot_cross_performance_boundary(tmp_path, template):
    service, contract, paths = setup(tmp_path, template)
    identifier = confirmed(service, contract)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / "novelty"
    output.mkdir(parents=True, exist_ok=True)
    worker = Path(__file__).with_name("novelty_worker.py").resolve()
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    def run(label, arguments):
        try:
            result = subprocess.run([sys.executable, str(worker), str(service.root), *arguments],
                cwd=tmp_path, env=environment, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            (output / (label + "-stdout.bin")).write_bytes(exc.stdout or b"")
            (output / (label + "-stderr.bin")).write_bytes(exc.stderr or b"")
            raise
        (output / (label + "-stdout.bin")).write_bytes(result.stdout)
        (output / (label + "-stderr.bin")).write_bytes(result.stderr)
        return result
    restart = run("restart", ["read", identifier, contract.candidate_id, contract.content_hash])
    assert restart.returncode == 0, restart.stderr.decode("utf-8")
    assert json.loads(restart.stdout)["passed"]
    before = (service.root / paths[0]).read_bytes()
    with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
        blocked = run("locked", ["write", *paths])
        assert blocked.returncode == 23, blocked.stderr.decode("utf-8")
        assert (service.root / paths[0]).read_bytes() == before
    changed = run("released", ["write", *paths])
    assert changed.returncode == 0, changed.stderr.decode("utf-8")
    with pytest.raises(ValueError, match="STALE"):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("已变化的来源不能复用重启前确认")


@pytest.mark.parametrize("damage", ["head", "identity", "all_scopes"])
def test_lost_scope_or_workspace_identity_never_falls_back(tmp_path, template, damage):
    service, contract, paths = setup(tmp_path, template)
    identifier = confirmed(service, contract)
    service.declare_sources(POLICY, paths)
    if damage == "head":
        path = service.directory / "scopes/00000001.json"
    elif damage == "identity":
        path = service.workspace_identity
    else:
        path = service.directory / "scopes"
    path.rename(path.with_name(path.name + ".removed"))
    with pytest.raises((ValueError, FileNotFoundError)):
        service.declare_sources(POLICY, paths[:1])
    with pytest.raises((ValueError, FileNotFoundError)):
        with service.performance_boundary(identifier, contract.candidate_id, contract.content_hash):
            pytest.fail("来源损坏不能恢复旧成功快照")


def test_cli_actual_confirmation_and_readonly_history(tmp_path, template, capsys):
    service, contract, paths = setup(tmp_path, template)
    args = ["--root", str(service.root), "--mode", "GOVERNED"]
    assert main([*args, "declare-sources", "--source", paths[0]]) == 0
    capsys.readouterr()
    assert main([*args, "preview", "--candidate-id", contract.candidate_id, "--contract-hash", contract.content_hash]) == 0
    identifier = json.loads(capsys.readouterr().out)["preview_id"]
    with pytest.raises(SystemExit):
        main([*args, "confirm", "--preview-id", identifier])
    assert main([*args, "confirm", "--preview-id", identifier, "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["test_confirmation"]
    assert main(["--root", str(service.root), "history", "--preview-id", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["receipt"]["execution_authorized"] is False


def test_canonical_new_metadata_cannot_replace_actual_start_intent(tmp_path, template):
    service, contract, _ = setup(tmp_path, template)
    identifier = confirmed(service, contract)
    candidate = SimpleNamespace(candidate_id=contract.candidate_id, candidate_hash=contract.candidate_hash,
        metadata={"synthetic_flow_version": SCHEMA, "synthetic_novelty_confirmation": identifier,
                  "start_intent_id": "CLIENT_ASSERTION", "trial_id": "NO_TRIAL"})
    with pytest.raises(ValueError, match="ACTUAL_START_INTENT_REQUIRED"):
        with canonical_novelty_boundary(service.root, "UNAPPROVED_OBJECTIVE", candidate, contract):
            pytest.fail("内容 hash 和客户端声明不能替代实际启动确认")
    candidate.metadata = {}
    with canonical_novelty_boundary(service.root, "UNAPPROVED_OBJECTIVE", candidate, contract) as evidence:
        assert evidence == {"passed": True}
        assert "binding_id" not in evidence
