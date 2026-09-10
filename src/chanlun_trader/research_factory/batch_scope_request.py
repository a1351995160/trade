"""批次范围申请的只读检查；不产生授权、回执、预算或执行副作用。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from .autonomous_control_plane import AgentCapabilityRegistryV1
from .common import stable_hash
from .safe_runtime_context import SafeRuntimeContextBuilderV1


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchResourceLimitsV1(RequestModel):
    candidates: StrictInt = Field(ge=1)
    trials: StrictInt = Field(ge=0)
    batches: StrictInt = Field(ge=1)
    model_calls: StrictInt = Field(ge=0)
    tokens: StrictInt = Field(ge=0)
    cost_minor_units: StrictInt = Field(ge=0)
    currency: Literal["CNY", "USD"]
    wall_seconds: StrictInt = Field(ge=1)
    memory_mib: StrictInt = Field(ge=1)


class BatchScopeRequestV1(RequestModel):
    schema_version: Literal["batch-scope-request-v1"]
    objective_id: StrictStr = Field(min_length=1)
    context_hash: StrictStr = Field(min_length=1)
    data_manifest_hash: StrictStr = Field(min_length=1)
    dataset_ids: list[StrictStr] = Field(min_length=1)
    data_start: date
    data_end: date
    actions: list[StrictStr] = Field(min_length=1)
    model: StrictStr = Field(min_length=1, max_length=120)
    limits: BatchResourceLimitsV1
    expires_at: AwareDatetime
    withdrawn: StrictBool
    stop_conditions: list[StrictStr] = Field(min_length=1)


STOP_CONDITIONS = (
    "HUMAN_GATE", "BUDGET_EXHAUSTED", "CONTEXT_CHANGED", "DATA_NOT_READY",
    "DUPLICATE_CANDIDATE", "PERMISSION_DENIED", "ERROR", "USER_PAUSED",
)


class BatchScopeRequestServiceV1:
    def __init__(self, root: str | Path):
        self.builder = SafeRuntimeContextBuilderV1(root)

    def context(self, objective_id: str) -> dict:
        context = self.builder.build(objective_id)
        datasets = [item["dataset"] for item in context.get("data_capabilities", [])
                    if item.get("category") == "dataset"]
        return {
            "objective_id": context.objective_id,
            "context_hash": context.context_hash,
            "data_manifest_hash": context.identity["data_manifest_hash"],
            "datasets": datasets,
            "budget": context.get("budget", {}),
            "capabilities": AgentCapabilityRegistryV1().to_dict()["capabilities"],
            "required_stop_conditions": list(STOP_CONDITIONS),
            "request_schema": BatchScopeRequestV1.model_json_schema(),
            "read_only": True, "execution_authorized": False,
            "authorization_status": "WAITING_POLICY_APPROVAL",
        }

    def check(self, objective_id: str, payload: str, *, now: datetime | None = None) -> dict:
        result = {"schema_version": "batch-scope-check-v1", "read_only": True,
                  "execution_authorized": False, "authorization_status": "WAITING_POLICY_APPROVAL",
                  "request_valid": False, "errors": []}
        try:
            request = BatchScopeRequestV1.model_validate_json(payload)
        except ValidationError as exc:
            result["errors"] = [{"field": ".".join(map(str, item["loc"])), "code": item["type"]}
                                for item in exc.errors()]
            return result
        current = self.context(objective_id)
        errors = result["errors"]

        def reject(field, code):
            errors.append({"field": field, "code": code})

        for key in ("objective_id", "context_hash", "data_manifest_hash"):
            if getattr(request, key) != current[key]:
                reject(key, "IDENTITY_CHANGED")
        if request.withdrawn:
            reject("withdrawn", "REQUEST_WITHDRAWN")
        if request.expires_at <= (now or datetime.now(timezone.utc)):
            reject("expires_at", "REQUEST_EXPIRED")
        if request.data_start > request.data_end:
            reject("data_start", "INVALID_WINDOW")
        known = {item["dataset_id"]: item for item in current["datasets"]}
        if len(set(request.dataset_ids)) != len(request.dataset_ids):
            reject("dataset_ids", "DUPLICATE_DATASET")
        for dataset_id in request.dataset_ids:
            dataset = known.get(dataset_id, {})
            if dataset.get("status") != "READY" or dataset.get("PIT_safe") is not True:
                reject("dataset_ids", "DATA_NOT_READY")
                continue
            try:
                first = date.fromisoformat(str(dataset["earliest_date"]))
                last = date.fromisoformat(str(dataset["latest_date"]))
            except (KeyError, ValueError):
                reject("dataset_ids", "DATA_WINDOW_UNKNOWN")
                continue
            if request.data_start < first or request.data_end > last:
                reject("dataset_ids", "DATA_WINDOW_OUT_OF_SCOPE")
        known_actions = {action for item in current["capabilities"] for action in item["action_types"]}
        if not set(request.actions) <= known_actions or len(set(request.actions)) != len(request.actions):
            reject("actions", "UNKNOWN_OR_DUPLICATE_ACTION")
        if set(request.stop_conditions) != set(STOP_CONDITIONS) or len(request.stop_conditions) != len(STOP_CONDITIONS):
            reject("stop_conditions", "REQUIRED_STOPS_CHANGED")
        remaining = current["budget"].get("remaining")
        if request.limits.trials and (type(remaining) is not int or request.limits.trials > remaining):
            reject("limits.trials", "INSUFFICIENT_CANONICAL_BUDGET")
        if request.limits.model_calls and (request.model == "NONE" or not request.limits.tokens or not request.limits.cost_minor_units):
            reject("limits.model_calls", "MODEL_LIMITS_REQUIRED")
        result.update(request_valid=not errors, request_hash=stable_hash(request.model_dump(mode="json")),
                      requested_scope=request.model_dump(mode="json"),
                      next_action="REQUEST_POLICY_REVIEW" if not errors else "CORRECT_REQUEST")
        return result
