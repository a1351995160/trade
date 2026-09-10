"""用户明确批准的隔离合成测试资格；不授予真实策略、Trial或CP权限。"""
from datetime import datetime, timezone
import json
import re
from types import SimpleNamespace

from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .paper_replay import _immutable
from .predictive_executor import CanonicalPredictiveExecutorV1
from .strategy_admission import inspect_strategy_admission


def utc_now():
    return datetime.now(timezone.utc)


def _time(value):
    if not isinstance(value, str):
        raise ValueError("SYNTHETIC_USAGE_AWARE_EXPIRY_REQUIRED")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("SYNTHETIC_USAGE_AWARE_EXPIRY_REQUIRED") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("SYNTHETIC_USAGE_AWARE_EXPIRY_REQUIRED")
    return stamp.astimezone(timezone.utc)


class SyntheticUsageServiceV1:
    def __init__(self, workbench):
        self.workbench = workbench
        self.root = workbench.output_root / "synthetic-usage"

    def _binding(self, candidate_id, *, refresh=False):
        self.workbench._replay_root(candidate_id)
        source = self.workbench.sources[candidate_id]
        contract = source["contract"]
        admission = inspect_strategy_admission(source)
        if refresh:
            caller = CanonicalPredictiveExecutorV1(source["root"], contract.policy_identity["objective_id"])
            reference = f"data/research/research_factory/batches/{contract.source_provenance['batch_id']}/durable_frozen_candidate_contracts.json"
            actual, record = caller._contract_and_record(SimpleNamespace(contract_ref=reference,
                candidate_id=candidate_id, candidate_hash=contract.candidate_hash))
            policy, _, _ = caller._load_policy(actual)
            inputs = caller._prepare_inputs(policy, record, caller._corrected_module(),
                source["root"] / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet", contract=actual)
            if actual.content_hash != contract.content_hash or inputs["input_diagnostics"]["input_identity"] != source["inputs"]["input_diagnostics"]["input_identity"]:
                raise ValueError("SYNTHETIC_USAGE_INPUT_CHANGED")
        return {"candidate_id": candidate_id, "candidate_hash": contract.candidate_hash,
            "contract_hash": contract.content_hash, "input_identity": source["inputs"]["input_diagnostics"]["input_identity"],
            "source_root": str(source["root"]), "output_root": str(self.workbench.output_root),
            "registry_identity": admission["registry_identity"], "registry_blocked": admission["preview_blocked"]}

    def _directory(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"SYNTHETIC_USAGE_[0-9a-f]{64}", request_id):
            raise ValueError("SYNTHETIC_USAGE_REQUEST_ID_INVALID")
        return self.root / request_id

    def _read(self, path):
        if path.resolve() != path:
            raise ValueError("SYNTHETIC_USAGE_LINKED_EVIDENCE")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("evidence_hash") != stable_hash({key: item for key, item in value.items() if key != "evidence_hash"}):
            raise ValueError("SYNTHETIC_USAGE_EVIDENCE_DAMAGED")
        return value

    def _write(self, path, value):
        _immutable(path, {**value, "evidence_hash": stable_hash(value)})

    def inspect(self):
        records = []
        for directory in sorted(self.root.glob("SYNTHETIC_USAGE_*")):
            request = self._read(directory / "request.json")
            request_id = "SYNTHETIC_USAGE_" + stable_hash(request["request"])
            if self._directory(request_id) != directory:
                raise ValueError("SYNTHETIC_USAGE_REQUEST_ID_CONFLICT")
            data = request["request"]
            if (data.get("schema_version") != "synthetic-test-usage-request-v1"
                    or data.get("domain") != "SYNTHETIC_TEST_ONLY" or data.get("real_execution_authorized") is not False
                    or not isinstance(data.get("purposes"), list) or not data["purposes"]
                    or any(item not in ("DAILY_PLAN", "PAPER_REPLAY") for item in data["purposes"])):
                raise ValueError("SYNTHETIC_USAGE_DOMAIN_INVALID")
            if _time(request["requested_at"]) >= _time(data["valid_until"]):
                raise ValueError("SYNTHETIC_USAGE_REQUEST_TIME_CONFLICT")
            status = "REQUESTED"
            approval_path, revoke_path = directory / "confirmation.json", directory / "revocation.json"
            if approval_path.exists():
                approval = self._read(approval_path)
                if (approval.get("request_evidence_hash") != request["evidence_hash"]
                        or approval.get("confirmed") is not True or approval.get("request_id") != request_id
                        or approval.get("domain") != "SYNTHETIC_TEST_ONLY" or approval.get("real_execution_authorized") is not False):
                    raise ValueError("SYNTHETIC_USAGE_CONFIRMATION_CONFLICT")
                if not _time(request["requested_at"]) <= _time(approval["confirmed_at"]) < _time(data["valid_until"]):
                    raise ValueError("SYNTHETIC_USAGE_CONFIRMATION_TIME_CONFLICT")
                status = "ACTIVE"
            if revoke_path.exists():
                revoke = self._read(revoke_path)
                if (revoke.get("request_id") != request_id or not approval_path.exists() or revoke.get("revoked") is not True
                        or revoke.get("domain") != "SYNTHETIC_TEST_ONLY" or revoke.get("real_execution_authorized") is not False):
                    raise ValueError("SYNTHETIC_USAGE_REVOCATION_CONFLICT")
                status = "REVOKED"
            elif _time(data["valid_until"]) <= utc_now():
                status = "EXPIRED"
            elif data["binding"]["candidate_id"] not in self.workbench.sources or data["binding"] != self._binding(data["binding"]["candidate_id"]):
                status = "BINDING_CHANGED"
            records.append({"request_id": request_id, "status": status, **data})
        return {"configured": bool(records), "records": records, "real_qualified_strategy_count": 0}

    def active(self, candidate_id, purpose):
        return [item for item in self.inspect()["records"] if item["status"] == "ACTIVE"
            and item["binding"]["candidate_id"] == candidate_id and purpose in item["purposes"]]

    def perform(self, action, policy, payload):
        """所有公开写动作复用真实执行策略、当前上下文和显式人工确认。"""
        if action not in {"request", "confirm", "revoke"}:
            raise ValueError("SYNTHETIC_USAGE_ACTION_INVALID")
        self.workbench._confirm(policy, payload)
        with ObjectiveMutationLock.for_resource(self.workbench.output_root / "workbench"):
            self.workbench._confirm(policy, payload)
            if action == "request":
                purposes = payload.get("purposes")
                if (not isinstance(purposes, list) or not purposes or any(item not in ("DAILY_PLAN", "PAPER_REPLAY") for item in purposes)
                        or len(set(purposes)) != len(purposes)):
                    raise ValueError("SYNTHETIC_USAGE_PURPOSE_INVALID")
                expiry = _time(payload.get("valid_until"))
                if expiry <= utc_now():
                    raise ValueError("SYNTHETIC_USAGE_EXPIRED")
                binding = self._binding(payload.get("candidate_id"), refresh=True)
                if expiry <= utc_now():
                    raise ValueError("SYNTHETIC_USAGE_EXPIRED")
                if binding["registry_blocked"]:
                    raise ValueError("SYNTHETIC_USAGE_STRATEGY_BLOCKED")
                data = {"schema_version": "synthetic-test-usage-request-v1", "domain": "SYNTHETIC_TEST_ONLY",
                    "binding": binding, "purposes": sorted(purposes), "valid_until": expiry.isoformat(),
                    "real_execution_authorized": False}
                request_id = "SYNTHETIC_USAGE_" + stable_hash(data)
                request_path = self._directory(request_id) / "request.json"
                if not request_path.exists():
                    self._write(request_path, {"request": data, "requested_at": utc_now().isoformat()})
                state = next(item["status"] for item in self.inspect()["records"] if item["request_id"] == request_id)
                return {"request_id": request_id, "status": state, "request": data}
            request_id = payload.get("request_id")
            directory = self._directory(request_id)
            request = self._read(directory / "request.json")
            current = next(item for item in self.inspect()["records"] if item["request_id"] == request_id)
            if action == "confirm":
                if current["status"] not in {"REQUESTED", "ACTIVE"}:
                    raise ValueError("SYNTHETIC_USAGE_REQUEST_NOT_CONFIRMABLE")
                if current["binding"] != self._binding(current["binding"]["candidate_id"], refresh=True):
                    raise ValueError("SYNTHETIC_USAGE_BINDING_CHANGED")
                if _time(current["valid_until"]) <= utc_now():
                    raise ValueError("SYNTHETIC_USAGE_EXPIRED")
                if not (directory / "confirmation.json").exists():
                    self._write(directory / "confirmation.json", {"request_id": request_id,
                        "request_evidence_hash": request["evidence_hash"], "confirmed": True,
                        "confirmed_at": utc_now().isoformat(),
                        "domain": "SYNTHETIC_TEST_ONLY", "real_execution_authorized": False})
                return {"request_id": request_id, "status": "ACTIVE", "real_execution_authorized": False}
            if not (directory / "confirmation.json").exists():
                raise ValueError("SYNTHETIC_USAGE_NOT_CONFIRMED")
            if not (directory / "revocation.json").exists():
                self._write(directory / "revocation.json", {"request_id": request_id, "revoked": True,
                    "revoked_at": utc_now().isoformat(),
                    "domain": "SYNTHETIC_TEST_ONLY", "real_execution_authorized": False})
            return {"request_id": request_id, "status": "REVOKED", "real_execution_authorized": False}
