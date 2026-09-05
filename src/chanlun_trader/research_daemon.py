"""Headless local research daemon.

The daemon owns orchestration state only.  Candidate contracts, structural
preflight, predictive accounting, TrialLedger and ArtifactGraph stay in the
canonical research factory/runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

# ``python -m chanlun_trader.research_daemon`` executes this file as
# ``__main__``.  Keep the package name aliased to that same module so the
# canonical predictive executor imports the identical ``PredictiveResult``
# class instead of creating a second module/class identity.
if __name__ == "__main__":
    sys.modules.setdefault("chanlun_trader.research_daemon", sys.modules[__name__])

from .research_factory.common import stable_hash
from .research_factory.context import PerformanceBlindGuard
from .research_factory.contract_correction import load_effective_contract_invalidations
from .research_factory.objective import ResearchObjectiveV1
from .research_factory.budget import SearchBudgetRegistryV1
from .research_factory.sample_feasibility import CandidateSampleFeasibilityPreflightV1, minimum_required_sample_count
from .research_factory.sample_feasibility_v2 import CandidateSampleFeasibilityPolicyV2, audit_lower_bound_integrity_v2
from .research_factory.durability import DurableFrozenCandidateContractV1, canonical_frozen_contract_identity_hash
from .research_factory.real_sample_feasibility import RealSampleFeasibilityProviderV1
from .research_factory.trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from .research.validation_policy_v2 import load_validation_decision_policy_v2
from .research_daemon_state import (
    DaemonAlreadyRunningError,
    DaemonCheckpointStoreV1,
    DaemonInstanceLockV1,
    DaemonCheckpointV1,
    ResearchDaemonState,
)
from .presentation import render_cli_result, render_daemon_status


DEFAULT_OBJECTIVE_ID = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"
DEFAULT_SLEEP_SECONDS = 30.0
DAEMON_RUNTIME_VERSION = "HEADLESS_AUTONOMOUS_RESEARCH_DAEMON_V1"

# The V2 integrity audit carries canonical architecture safety counters as
# evidence. Only these exact evidence paths may contain the ``PROSPECTIVE``
# safety counter; all other performance-bearing fields remain forbidden.
STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS = frozenset({
    ("lower_bound_integrity", "checks", "evidence", "architecture_safety", "prospective"),
    ("lower_bound_integrity", "checks", "evidence", "safety", "prospective"),
})


@dataclass(frozen=True)
class CandidateWork:
    candidate_id: str
    candidate_hash: str
    contract_ref: str
    batch_id: str = ""
    mechanism: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "contract_ref": self.contract_ref,
            "batch_id": self.batch_id,
            "mechanism": self.mechanism,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StructuralResult:
    status: str
    reason_code: str = ""
    artifact_refs: tuple[str, ...] = ()
    provider_checkpoint: str | None = None
    partition_index: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = str(self.status).upper()
        if status not in {"PASS", "UNKNOWN", "BLOCKED", "ENGINEERING_BLOCKED"}:
            raise ValueError(f"unsupported structural status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "artifact_refs", tuple(str(item) for item in self.artifact_refs))
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True)
class PredictiveResult:
    trial_id: str
    status: str
    performance_accessed: bool
    classification: str | None = None
    reason_codes: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("predictive result requires trial_id")
        object.__setattr__(self, "status", str(self.status).upper())
        object.__setattr__(self, "reason_codes", tuple(str(item) for item in self.reason_codes))
        object.__setattr__(self, "artifact_refs", tuple(str(item) for item in self.artifact_refs))
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True)
class CanonicalPlatformContext:
    objective_id: str
    objective_hash: str
    budget: Mapping[str, Any]
    capability_context_ref: str
    architecture_manifest_ref: str
    frozen_candidate_refs: tuple[str, ...]
    checkpoint_refs: tuple[str, ...]
    failure_knowledge_ref: str | None = None
    global_search_exhausted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "objective_hash": self.objective_hash,
            "budget": dict(self.budget),
            "capability_context_ref": self.capability_context_ref,
            "architecture_manifest_ref": self.architecture_manifest_ref,
            "frozen_candidate_refs": list(self.frozen_candidate_refs),
            "checkpoint_refs": list(self.checkpoint_refs),
            "failure_knowledge_ref": self.failure_knowledge_ref,
            "global_search_exhausted": self.global_search_exhausted,
        }


class ResearchDaemonRuntime(Protocol):
    def load_context(self) -> CanonicalPlatformContext: ...
    def next_candidate(self) -> CandidateWork | None: ...
    def structural_preflight(self, candidate: CandidateWork) -> StructuralResult: ...
    def predictive_validate(self, candidate: CandidateWork) -> PredictiveResult: ...
    def mark_candidate_complete(self, candidate: CandidateWork, *, result: StructuralResult | PredictiveResult) -> None: ...
    def recover_interrupted(self, checkpoint: DaemonCheckpointV1) -> Mapping[str, Any] | None: ...
    def summary(self) -> Mapping[str, Any]: ...
    def accept_ai_batch(self, artifact: str | Path) -> Mapping[str, Any]: ...


class ResourceMonitorV1:
    """Low-frequency local telemetry and fail-before-OOM resource guard."""

    def __init__(self, telemetry_path: str | Path, *, minimum_available_memory_bytes: int = 512 * 1024 * 1024, interval_seconds: float = 30.0):
        self.telemetry_path = Path(telemetry_path)
        self.minimum_available_memory_bytes = int(minimum_available_memory_bytes)
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._last_record = 0.0

    @staticmethod
    def _windows_memory(pid: int | None = None) -> tuple[int | None, int | None]:
        if os.name != "nt":
            return None, None
        import ctypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dw_length", ctypes.c_ulong),
                ("dw_memory_load", ctypes.c_ulong),
                ("ull_total_phys", ctypes.c_ulonglong),
                ("ull_avail_phys", ctypes.c_ulonglong),
                ("ull_total_page_file", ctypes.c_ulonglong),
                ("ull_avail_page_file", ctypes.c_ulonglong),
                ("ull_total_virtual", ctypes.c_ulonglong),
                ("ull_avail_virtual", ctypes.c_ulonglong),
                ("ull_avail_extended_virtual", ctypes.c_ulonglong),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        process_handle = kernel32.GetCurrentProcess() if pid is None else kernel32.OpenProcess(0x1000, False, int(pid))
        if not process_handle:
            return None, None
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_memory_info = psapi.GetProcessMemoryInfo
            get_memory_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong]
            get_memory_info.restype = ctypes.c_bool
            rss = None
            if get_memory_info(process_handle, ctypes.byref(counters), ctypes.sizeof(counters)):
                rss = int(counters.working_set_size)

            memory_status = MemoryStatus()
            memory_status.dw_length = ctypes.sizeof(memory_status)
            available = None
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
                available = int(memory_status.ull_avail_phys)
            return rss, available
        finally:
            if pid is not None:
                kernel32.CloseHandle(process_handle)

    @staticmethod
    def _memory() -> tuple[int | None, int | None]:
        try:
            import psutil  # type: ignore
            return int(psutil.Process(os.getpid()).memory_info().rss), int(psutil.virtual_memory().available)
        except Exception:
            return ResourceMonitorV1._windows_memory()

    def snapshot(self, *, stage: str, candidate_id: str | None = None, partition_index: int | None = None, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        rss, available = self._memory()
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stage": stage,
            "candidate_id": candidate_id,
            "partition_index": partition_index,
            "process_rss_bytes": rss,
            "system_available_memory_bytes": available,
            "memory_pressure": available is not None and available < self.minimum_available_memory_bytes,
        }
        if force or now - self._last_record >= self.interval_seconds:
            self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            with self.telemetry_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            self._last_record = now
        return payload


class SyntheticResearchDaemonRuntime:
    """Deterministic fixture runtime used by daemon acceptance tests only."""

    def __init__(self, candidates: Sequence[CandidateWork], *, structural: Mapping[str, StructuralResult | str] | None = None, predictive: Mapping[str, PredictiveResult] | None = None, budget_total: int = 0, budget_used: int = 0, global_search_exhausted: bool = False):
        self.candidates = list(candidates)
        self.structural = dict(structural or {})
        self.predictive = dict(predictive or {})
        self.budget_total = int(budget_total)
        self.budget_used = int(budget_used)
        self.global_search_exhausted = bool(global_search_exhausted)
        self.completed: set[str] = set()
        self.trials: dict[str, PredictiveResult] = {}
        self.structural_calls: list[str] = []
        self.predictive_calls: list[str] = []
        self.context = CanonicalPlatformContext("SYNTHETIC_OBJECTIVE", "synthetic", {"used": self.budget_used, "total": self.budget_total, "remaining": max(0, self.budget_total - self.budget_used), "reserved": 0}, "synthetic-capability", "synthetic-architecture", tuple(item.contract_ref for item in self.candidates), (), global_search_exhausted=self.global_search_exhausted)

    def load_context(self) -> CanonicalPlatformContext:
        return self.context

    def next_candidate(self) -> CandidateWork | None:
        return next((item for item in self.candidates if item.candidate_id not in self.completed), None)

    def structural_preflight(self, candidate: CandidateWork) -> StructuralResult:
        self.structural_calls.append(candidate.candidate_id)
        result = self.structural.get(candidate.candidate_id, StructuralResult("PASS"))
        return StructuralResult(result) if isinstance(result, str) else result

    def predictive_validate(self, candidate: CandidateWork) -> PredictiveResult:
        if candidate.candidate_id in self.trials:
            return self.trials[candidate.candidate_id]
        self.predictive_calls.append(candidate.candidate_id)
        result = self.predictive.get(candidate.candidate_id) or PredictiveResult(f"TRIAL_{candidate.candidate_id}", "COMPLETED", True, "REJECTED")
        self.trials[candidate.candidate_id] = result
        if result.performance_accessed:
            self.budget_used += 1
            self.context = CanonicalPlatformContext(self.context.objective_id, self.context.objective_hash, {"used": self.budget_used, "total": self.budget_total, "remaining": max(0, self.budget_total - self.budget_used), "reserved": 0}, self.context.capability_context_ref, self.context.architecture_manifest_ref, self.context.frozen_candidate_refs, self.context.checkpoint_refs, self.context.failure_knowledge_ref, self.global_search_exhausted)
        return result

    def mark_candidate_complete(self, candidate: CandidateWork, *, result: StructuralResult | PredictiveResult) -> None:
        if isinstance(result, PredictiveResult) or result.status in {"UNKNOWN", "BLOCKED"}:
            self.completed.add(candidate.candidate_id)

    def recover_interrupted(self, checkpoint: DaemonCheckpointV1) -> Mapping[str, Any] | None:
        trial_id = str((checkpoint.current_trial or {}).get("trial_id") or "")
        for result in self.trials.values():
            if result.trial_id == trial_id:
                return {"status": result.status, "performance_accessed": result.performance_accessed, "trial_id": result.trial_id, "classification": result.classification}
        return None

    def summary(self) -> Mapping[str, Any]:
        classifications = [item.classification for item in self.trials.values()]
        return {"budget": dict(self.context.budget), "current_trial": None, "last_completed_trial": next(reversed(self.trials.values())).trial_id if self.trials else None, "research_passed_count": classifications.count("RESEARCH_PASSED"), "promising_count": classifications.count("PROMISING"), "remaining_frozen_candidates": sum(item.candidate_id not in self.completed for item in self.candidates), "global_search_exhausted": self.global_search_exhausted}

    def accept_ai_batch(self, artifact: str | Path) -> Mapping[str, Any]:
        return {"status": "SYNTHETIC_ACCEPTED", "artifact": str(artifact)}


class CanonicalResearchRuntime:
    """Read canonical state and run structural preflight without AI invocation.

    Predictive execution is deliberately bound through ``predictive_executor``.
    The executor must be the existing canonical preregistration/PerformanceAccess
    path; absent that binding the daemon fails closed instead of inventing a
    second trial accounting system.
    """

    def __init__(self, root: str | Path = ".", *, objective_id: str = DEFAULT_OBJECTIVE_ID, predictive_executor: Callable[[CandidateWork], PredictiveResult] | None = None):
        self.root = Path(root)
        self.objective_id = str(objective_id)
        if predictive_executor is None:
            from .research_factory.predictive_executor import CanonicalPredictiveExecutorV1

            predictive_executor = CanonicalPredictiveExecutorV1(self.root, self.objective_id)
        self.predictive_executor = predictive_executor
        self._completed: set[str] = set()
        self._engineering_blocked: dict[str, str] = {}
        self._contracts: dict[str, CandidateWork] = {}
        self._out_of_scope_contracts: dict[str, tuple[str, str]] = {}
        self._contract_conflicts: dict[str, tuple[str, ...]] = {}
        self._identity_repair_cache: dict[str, bool] = {}
        self._context: CanonicalPlatformContext | None = None
        self._load_contracts()
        self._load_prior_state()

    def _json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _objective(self) -> ResearchObjectiveV1:
        path = self.root / "data/research/research_factory/objectives" / f"{self.objective_id}.json"
        payload = self._json(path)
        if payload is None:
            raise FileNotFoundError(f"canonical research objective not found: {path}")
        tuple_fields = {"research_universe", "holding_horizon", "preferred_horizon", "mechanism_scope", "allowed_factor_scope", "research_priority"}
        objective_fields = set(ResearchObjectiveV1.__dataclass_fields__)
        payload = {
            key: (tuple(value) if key in tuple_fields else value)
            for key, value in payload.items()
            if key != "schema_version" and key in objective_fields
        }
        return ResearchObjectiveV1(**payload)

    def _budget_path(self) -> Path:
        paths = sorted((self.root / "data/research/research_factory/batches").glob("*/search_budget_registry.json"))
        matching = [path for path in paths if self._json(path, {}).get("objective_id") == self.objective_id]
        if not matching:
            raise FileNotFoundError(f"canonical search budget not found for {self.objective_id}")
        return max(matching, key=lambda path: str(self._json(path, {}).get("updated_at", "")))

    def _load_contracts(self) -> None:
        root = self.root / "data/research/research_factory/batches"
        for path in sorted(root.glob("*/durable_frozen_candidate_contracts.json")):
            payload = self._json(path, {}) or {}
            for raw in payload.get("contracts", ()):
                candidate_id = str(raw.get("candidate_id") or "")
                if not candidate_id:
                    continue
                contract_objective_id = str((raw.get("policy_identity") or {}).get("objective_id") or "")
                if contract_objective_id != self.objective_id:
                    self._out_of_scope_contracts[candidate_id] = (
                        contract_objective_id,
                        str(path.relative_to(self.root)).replace("\\", "/"),
                    )
                    continue
                work = CandidateWork(candidate_id, str(raw.get("candidate_hash") or raw.get("content_hash") or ""), str(path.relative_to(self.root)).replace("\\", "/"), path.parent.name, str(raw.get("mechanism") or raw.get("family") or ""), {"raw_contract": raw})
                existing = self._contracts.get(candidate_id)
                if existing is None:
                    self._contracts[candidate_id] = work
                elif existing.candidate_hash != work.candidate_hash:
                    self._contract_conflicts[candidate_id] = tuple(sorted({existing.contract_ref, work.contract_ref}))

    def _load_prior_state(self) -> None:
        for candidate_id, event in load_effective_contract_invalidations(self.root, self.objective_id).items():
            self._completed.add(candidate_id)
            self._engineering_blocked[candidate_id] = str(event.get("reason_code") or "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE")
        history = self._json(self.root / "reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json", {}) or {}
        for row in history.get("rows", ()):
            candidate_id = str(row.get("candidate_id") or "")
            if not candidate_id:
                continue
            classification = str(row.get("final_adjudicated_outcome") or row.get("classification") or "")
            if classification == "ENGINEERING_BLOCKED":
                reason = " ".join(str(item) for item in (row.get("reason_codes") or ()))
                if not self._identity_conflict_repaired(candidate_id, reason):
                    self._engineering_blocked[candidate_id] = "CANONICAL_ENGINEERING_BLOCKED_HISTORY"
            elif row.get("performance_accessed") or classification in {"REJECTED", "WEAK", "PROMISING", "RESEARCH_PASSED"}:
                self._completed.add(candidate_id)

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                candidate_id = str(value.get("candidate_id") or "")
                classification = str(value.get("classification") or value.get("status") or value.get("final_adjudicated_outcome") or "")
                reason_codes = value.get("reason_codes") or ()
                if isinstance(reason_codes, str):
                    reason_codes = (reason_codes,)
                reason_text = " ".join(str(item) for item in reason_codes)
                reason_text += " " + " ".join(str(value.get(key) or "") for key in ("reason", "final_reason", "error"))
                if not candidate_id and "frozen_contract:" in reason_text:
                    candidate_id = reason_text.split("frozen_contract:", 1)[1].split()[0].strip(";,)")
                if candidate_id and (classification == "ENGINEERING_BLOCKED" or "identity conflict" in reason_text.casefold()):
                    if not self._identity_conflict_repaired(candidate_id, reason_text):
                        self._engineering_blocked[candidate_id] = "CANONICAL_ENGINEERING_BLOCKED_HISTORY"
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)

        for path in (self.root / "reports").glob("**/AUTONOMOUS_ALPHA_RESEARCH_PREDICTIVE_TRIALS_V1.json"):
            payload = self._json(path, {}) or {}
            visit(payload)

        # A terminal canonical factory trial is already the authoritative
        # result for the Candidate, including a terminal BLOCKED outcome.
        # Load this state before the daemon selects work so restart cannot
        # present an already-consumed trial as a new predictive attempt.
        for path in (self.root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"):
            payload = self._json(path, {}) or {}
            latest: dict[str, Mapping[str, Any]] = {}
            for event in payload.get("events", ()):
                if isinstance(event, Mapping) and event.get("trial_id"):
                    latest[str(event["trial_id"])] = event
            for event in latest.values():
                if str(event.get("objective_id")) != self.objective_id:
                    continue
                candidate_id = str(event.get("candidate_id") or "")
                if not candidate_id or not event.get("performance_accessed"):
                    continue
                if str(event.get("status")) in ResearchFactoryTrialLedgerFacadeV1.TERMINAL:
                    self._completed.add(candidate_id)
                else:
                    self._engineering_blocked[candidate_id] = "CANONICAL_TRIAL_RECONCILIATION_REQUIRED"

        # 结构预检结果同样属于守护进程的 canonical 工作，但不会生成预测试验台账行。
        # 从守护进程追加式状态流恢复已完成候选，避免新的守护进程实例再次选择同一冻结合同。
        events_path = self.root / "reports/research_daemon" / self.objective_id / "daemon_events.jsonl"
        active_candidate: str | None = None
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                candidate = event.get("candidate")
                if isinstance(candidate, Mapping):
                    candidate_id = str(candidate.get("candidate_id") or "")
                else:
                    candidate_id = str(candidate or "")
                new_state = str(event.get("new_state") or "")
                if candidate_id:
                    active_candidate = candidate_id
                if new_state == "CANDIDATE_COMPLETE" and active_candidate:
                    self._completed.add(active_candidate)
                if new_state in {"NEXT_CANDIDATE", "READY"}:
                    active_candidate = None

    def _identity_conflict_repaired(self, candidate_id: str, reason_text: str) -> bool:
        if "identity conflict" not in reason_text.casefold() and "frozen_contract:" not in reason_text.casefold():
            return False
        if candidate_id in self._identity_repair_cache:
            return self._identity_repair_cache[candidate_id]
        work = self._contracts.get(candidate_id)
        if work is None:
            self._identity_repair_cache[candidate_id] = False
            return False
        proposed = dict(work.metadata.get("raw_contract") or {})
        proposed_identity_hash = canonical_frozen_contract_identity_hash(proposed)
        contract_node_id = f"frozen_contract:{candidate_id}"
        candidate_node_id = f"candidate:{candidate_id}"
        for graph_path in sorted((self.root / "reports").glob("**/artifact_graph.json")):
            graph = self._json(graph_path, {}) or {}
            nodes = {str(item.get("node_id")): item for item in graph.get("nodes", ())}
            if contract_node_id not in nodes or candidate_node_id not in nodes:
                continue
            if not any(
                str(edge.get("source_id")) == candidate_node_id
                and str(edge.get("edge_type")) == "HAS_DURABLE_CONTRACT"
                and str(edge.get("target_id")) == contract_node_id
                for edge in graph.get("edges", ())
            ):
                continue
            payload_hash = str(nodes[contract_node_id].get("payload_hash") or "")
            if payload_hash == stable_hash(proposed) and proposed_identity_hash == canonical_frozen_contract_identity_hash(proposed):
                self._identity_repair_cache[candidate_id] = True
                return True
            for contract_path in sorted((self.root / "data/research/research_factory/batches").glob("*/durable_frozen_candidate_contracts.json")):
                payload = self._json(contract_path, {}) or {}
                for contract in payload.get("contracts", ()):
                    if str(contract.get("candidate_id")) != candidate_id:
                        continue
                    if stable_hash(contract) == payload_hash and canonical_frozen_contract_identity_hash(contract) == proposed_identity_hash:
                        self._identity_repair_cache[candidate_id] = True
                        return True
        self._identity_repair_cache[candidate_id] = False
        return False

    def load_context(self) -> CanonicalPlatformContext:
        objective = self._objective()
        budget_path = self._budget_path()
        budget = SearchBudgetRegistryV1(objective.objective_id, budget_path).snapshot()
        objective_bucket = next((item for item in budget.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == objective.objective_id), {})
        global_state = self._json(self.root / "reports/AUTONOMOUS_ALPHA_RESEARCH_SEARCH_SPACE_STATUS_V1.json", {}) or {}
        refs = tuple(str(path.relative_to(self.root)).replace("\\", "/") for path in (self.root / "reports").glob("**/checkpoints/*.json"))
        self._context = CanonicalPlatformContext(
            objective_id=objective.objective_id,
            objective_hash=objective.objective_hash,
            budget={"objective_id": objective.objective_id, "used": int(objective_bucket.get("used", 0)), "total": int(objective_bucket.get("limit", objective.max_total_trials)), "remaining": int(objective_bucket.get("remaining", 0)), "reserved": int(objective_bucket.get("reserved", 0)), "registry_path": str(budget_path.relative_to(self.root)).replace("\\", "/"), "registry_head_hash": SearchBudgetRegistryV1(objective.objective_id, budget_path).head_hash},
            capability_context_ref="reports/AI_PLATFORM_CAPABILITY_CONTEXT_V2.json",
            architecture_manifest_ref="reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json",
            frozen_candidate_refs=tuple(item.contract_ref for item in self._contracts.values()),
            checkpoint_refs=refs,
            failure_knowledge_ref="reports/RUN_AUTONOMOUS_ALPHA_RESEARCH_NEW_BATCH_V1/AUTONOMOUS_ALPHA_RESEARCH_FAILURE_KNOWLEDGE_V1.json",
            global_search_exhausted=bool(global_state.get("global_legal_search_exhausted", False)),
        )
        return self._context

    def next_candidate(self) -> CandidateWork | None:
        for candidate_id in sorted(self._contracts):
            if candidate_id in self._completed:
                continue
            return self._contracts[candidate_id]
        return None

    def structural_preflight(self, candidate: CandidateWork) -> StructuralResult:
        if candidate.candidate_id in self._contract_conflicts:
            return StructuralResult("ENGINEERING_BLOCKED", "FROZEN_CONTRACT_IDENTITY_CONFLICT", self._contract_conflicts[candidate.candidate_id], details={"candidate_identity_preserved": True, "replacement_candidate_created": False})
        if candidate.candidate_id in self._engineering_blocked:
            return StructuralResult("ENGINEERING_BLOCKED", self._engineering_blocked[candidate.candidate_id], (candidate.contract_ref,), details={"candidate_identity_preserved": True, "replacement_candidate_created": False})
        raw = dict(candidate.metadata.get("raw_contract") or {})
        stage = "load_validation_policy"
        try:
            policy, _ = load_validation_decision_policy_v2(self.root / "data/research/strategy_validation/validation_decision_policy_v2.json")
            stage = "structural_contract"
            provider_candidate = DurableFrozenCandidateContractV1.from_dict(raw).provider_candidate_payload()
            stage = "structural_provider"
            # Match the proven canonical full-window runner configuration.
            provider = RealSampleFeasibilityProviderV1(self.root, streaming=True, partition_session_count=80)
            sample_input = provider(provider_candidate, policy)
            stage = "v1_structural_preflight"
            v1_row = CandidateSampleFeasibilityPreflightV1(policy).run(sample_input).to_dict()
            stage = "lower_bound_integrity"
            integrity = audit_lower_bound_integrity_v2(
                candidate_contract=raw,
                provider_report={"status": "COMPLETE", "result_counts": v1_row},
                architecture_manifest=self._json(self.root / "reports/PLATFORM_ARCHITECTURE_MANIFEST_V2.json", {}) or {},
                gap_analysis=self._json(self.root / "reports/PLATFORM_ARCHITECTURE_GAP_ANALYSIS_V2.json", {}) or {},
            )
            stage = "v2_sample_policy"
            v2_policy = CandidateSampleFeasibilityPolicyV2(
                minimum_required_count=minimum_required_sample_count(policy),
                activation_time="CANONICAL_VALIDATION_POLICY_V2_ACTIVE",
                governance_reason="V1 structural evidence is preserved; CandidateSampleFeasibilityPolicyV2 adjudicates only the proven lower-bound evidence.",
            )
            decision = v2_policy.decide(v1_row, integrity.to_dict())
            v2_row = {
                **v1_row,
                **decision,
                "preflight_version": "CandidateSampleFeasibilityPolicyV2",
                "v1_preflight_version": v1_row.get("preflight_version"),
                "v1_status": v1_row.get("status"),
                "v1_result": v1_row,
                "lower_bound_integrity": integrity.to_dict(),
            }
            stage = "performance_blind_guard"
            PerformanceBlindGuard.assert_blind(
                v2_row,
                allowed_paths=STRUCTURAL_SAFETY_STATISTIC_ALLOWED_PATHS,
            )
        except Exception as exc:
            reason_code = "FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE" if stage == "structural_contract" else "STRUCTURAL_PROVIDER_ERROR"
            return StructuralResult("ENGINEERING_BLOCKED", reason_code, (candidate.contract_ref,), details={"stage": stage, "error_type": type(exc).__name__, "error": str(exc)})
        status = str(v2_row.get("status") or "UNKNOWN")
        status_map = {"PASS": "PASS", "BLOCKED_INSUFFICIENT_FEASIBILITY": "BLOCKED", "UNKNOWN": "UNKNOWN"}
        return StructuralResult(status_map.get(status, "ENGINEERING_BLOCKED"), str(v2_row.get("decision_rule") or status), (candidate.contract_ref,), v1_row.get("checkpoint_path"), v1_row.get("partition_index"), v2_row)

    def predictive_validate(self, candidate: CandidateWork) -> PredictiveResult:
        if self.predictive_executor is None:
            raise RuntimeError("CANONICAL_PREDICTIVE_EXECUTOR_NOT_BOUND; bind existing preregistration, SearchBudget, PerformanceAccessGate and TrialLedger path")
        executor = self.predictive_executor
        result = executor(candidate) if callable(executor) else executor.execute(candidate)
        if not isinstance(result, PredictiveResult):
            raise TypeError("canonical predictive executor must return PredictiveResult")
        return result

    def mark_candidate_complete(self, candidate: CandidateWork, *, result: StructuralResult | PredictiveResult) -> None:
        if isinstance(result, PredictiveResult) or result.status in {"UNKNOWN", "BLOCKED"}:
            self._completed.add(candidate.candidate_id)

    def recover_interrupted(self, checkpoint: DaemonCheckpointV1) -> Mapping[str, Any] | None:
        return None

    def summary(self) -> Mapping[str, Any]:
        context = self._context or self.load_context()
        history = self._json(self.root / "reports/CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json", {}) or {}
        counts = dict(history.get("effective_classification_counts", {}))
        known_history_trials = {str(row.get("trial_id")) for row in history.get("rows", ()) if row.get("trial_id")}
        latest_trials: dict[str, Mapping[str, Any]] = {}
        for path in (self.root / "data/research/research_factory/batches").glob("*/factory_trial_ledger.json"):
            payload = self._json(path, {}) or {}
            for event in payload.get("events", ()):
                if isinstance(event, Mapping) and event.get("trial_id"):
                    latest_trials[str(event["trial_id"])] = event
        last_completed_trial = None
        last_completed_at = ""
        for trial in latest_trials.values():
            if str(trial.get("objective_id")) != self.objective_id or str(trial.get("status")) not in ResearchFactoryTrialLedgerFacadeV1.TERMINAL:
                continue
            trial_id = str(trial.get("trial_id"))
            if trial_id not in known_history_trials:
                classification = str(trial.get("classification") or "")
                if classification in {"RESEARCH_PASSED", "PROMISING"}:
                    counts[classification] = int(counts.get(classification, 0)) + 1
            updated_at = str(trial.get("updated_at") or trial.get("created_at") or "")
            if updated_at >= last_completed_at:
                last_completed_at = updated_at
                last_completed_trial = trial_id
        budget_path = self._budget_path()
        budget_snapshot = SearchBudgetRegistryV1(self.objective_id, budget_path).snapshot()
        objective_bucket = next((item for item in budget_snapshot.get("buckets", ()) if item.get("kind") == "objective" and item.get("key") == self.objective_id), {})
        budget = {"objective_id": self.objective_id, "used": int(objective_bucket.get("used", 0)), "total": int(objective_bucket.get("limit", context.budget.get("total", 0))), "remaining": int(objective_bucket.get("remaining", 0)), "reserved": int(objective_bucket.get("reserved", 0)), "registry_path": str(budget_path.relative_to(self.root)).replace("\\", "/"), "registry_head_hash": SearchBudgetRegistryV1(self.objective_id, budget_path).head_hash}
        return {"budget": budget, "current_trial": None, "last_completed_trial": last_completed_trial, "research_passed_count": int(counts.get("RESEARCH_PASSED", 0)), "promising_count": int(counts.get("PROMISING", 0)), "remaining_frozen_candidates": sum(item.candidate_id not in self._completed for item in self._contracts.values()), "global_search_exhausted": context.global_search_exhausted}

    def accept_ai_batch(self, artifact: str | Path) -> Mapping[str, Any]:
        path = Path(artifact)
        payload = self._json(path)
        if not isinstance(payload, Mapping):
            raise ValueError("AI batch artifact must be a JSON object")
        if str(payload.get("objective_id")) != self.objective_id:
            raise ValueError("AI batch objective identity mismatch")
        candidates = payload.get("candidates", ())
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("AI batch contains no candidates")
        existing = set(self._contracts)
        for item in candidates:
            if not isinstance(item, Mapping) or not item.get("candidate_id") or not item.get("candidate_hash"):
                raise ValueError("AI batch candidate contract is incomplete")
            if str(item["candidate_id"]) in existing:
                raise ValueError(f"AI batch candidate is not novel: {item['candidate_id']}")
            PerformanceBlindGuard.assert_blind(item)
        accepted = {"schema_version": "research-daemon-ai-batch-acceptance-v1", "objective_id": self.objective_id, "artifact": str(path), "artifact_hash": hashlib.sha256(path.read_bytes()).hexdigest(), "candidate_count": len(candidates), "status": "ACCEPTED_FOR_CANONICAL_FREEZE", "canonical_freeze_required": True, "ai_auto_invocation": "DISABLED"}
        out = self.root / "reports/research_daemon" / self.objective_id / "accepted_ai_batch.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(accepted, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return accepted


class ResearchDaemon:
    def __init__(self, root: str | Path = ".", *, objective_id: str = DEFAULT_OBJECTIVE_ID, runtime: ResearchDaemonRuntime | None = None, sleep_seconds: float = DEFAULT_SLEEP_SECONDS, memory_wait_seconds: float = 60.0):
        self.root = Path(root)
        self.objective_id = str(objective_id)
        self.runtime = runtime or CanonicalResearchRuntime(self.root, objective_id=self.objective_id)
        if runtime is not None and objective_id == DEFAULT_OBJECTIVE_ID:
            objective_id = str(runtime.load_context().objective_id)
        self.objective_id = str(objective_id)
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.memory_wait_seconds = max(1.0, float(memory_wait_seconds))
        self.store = DaemonCheckpointStoreV1(self.root, self.objective_id)
        self.monitor = ResourceMonitorV1(self.store.telemetry_path)
        self.lock = DaemonInstanceLockV1(self.store.lock_path, self.objective_id)
        self.checkpoint = self.store.load()
        self._shutdown = False
        self._predictive_trial_start_service: Any | None = None

    def _new_checkpoint(self, context: CanonicalPlatformContext) -> DaemonCheckpointV1:
        return DaemonCheckpointV1(daemon_run_id=f"DAEMON_{int(time.time())}_{os.getpid()}", objective_id=context.objective_id, current_state=ResearchDaemonState.BOOTSTRAP.value, canonical_refs=context.to_dict(), budget_view=dict(context.budget))

    def _save(self) -> None:
        if self.checkpoint is None:
            return
        self.store.save(self.checkpoint)
        summary = dict(self.runtime.summary())
        status = self.status_payload()
        self.store.save_status(status)

    def _transition(self, state: ResearchDaemonState, reason_code: str, **details: Any) -> None:
        if self.checkpoint is None:
            raise RuntimeError("daemon checkpoint is not initialized")
        previous = self.checkpoint.current_state
        self.checkpoint = self.checkpoint.transition(state, reason_code, details=details)
        self.store.append_event("STATE_TRANSITION", self.checkpoint.last_transition or {})
        self._save()
        if previous != state.value:
            self.monitor.snapshot(stage=state.value, candidate_id=(self.checkpoint.current_candidate or {}).get("candidate_id"), force=True)

    def _ensure_bootstrap(self) -> None:
        context = self.runtime.load_context()
        if self.checkpoint is None:
            self.checkpoint = self._new_checkpoint(context)
        elif self.checkpoint.objective_id != context.objective_id:
            raise RuntimeError("daemon checkpoint objective differs from canonical objective")
        self.checkpoint = self.checkpoint.update(canonical_refs={**dict(self.checkpoint.canonical_refs), **context.to_dict()}, budget_view=dict(context.budget), ai_auto_invocation="DISABLED")
        formal_predictive_trial_pending = self._formal_predictive_trial_pending()
        if self.checkpoint.current_state == ResearchDaemonState.BOOTSTRAP.value:
            self._transition(ResearchDaemonState.RECOVER, "BOOTSTRAP_CONTEXT_LOADED")
        elif not formal_predictive_trial_pending and self.checkpoint.current_state in {ResearchDaemonState.STRUCTURAL_PENDING.value, ResearchDaemonState.STRUCTURAL_RUNNING.value, ResearchDaemonState.PREDICTIVE_PENDING.value, ResearchDaemonState.PREDICTIVE_RUNNING.value}:
            interrupted_state = self.checkpoint.current_state
            self.checkpoint = self.checkpoint.update(canonical_refs={**dict(self.checkpoint.canonical_refs), "interrupted_state": interrupted_state})
            self._transition(ResearchDaemonState.RECOVER, "RESTART_RECOVERY")
        self._recover_if_needed()

    def _formal_predictive_trial_pending(self) -> bool:
        """Return whether a confirmed formal Trial needs durable recovery.

        The daemon must not infer a start from ``AUTHORIZED`` alone.  Only a
        persisted start intent, or its persisted current Trial reference, can
        open this recovery path.
        """
        path = self.root / "reports/research_daemon" / self.objective_id / "predictive_trial_start_intents.json"
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != self.objective_id:
            return True
        try:
            from .research_factory.predictive_trial_start import IN_FLIGHT_STAGES

            intents = payload.get("intents") if isinstance(payload.get("intents"), Mapping) else {}
            if any(str(item.get("stage")) in IN_FLIGHT_STAGES for item in intents.values() if isinstance(item, Mapping)):
                return True
        except (AttributeError, TypeError):
            return True
        return bool(self.checkpoint and self.checkpoint.current_trial and self.checkpoint.required_action == "PREDICTIVE_TRIAL_RUN_IN_PROGRESS")

    def _governed_predictive_authorization_required(self) -> bool:
        """Whether this objective must stop after structural PASS for a human decision.

        Objectives created by the governance execution service are explicitly
        human-governed research rounds.  They must never fall through from a
        first structural PASS directly into predictive performance access just
        because the structural governance projection has not been materialized
        yet.
        """

        path = self.root / "data/research/research_factory/objectives" / f"{self.objective_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, Mapping):
            return False
        return str(payload.get("governance_action") or "") in {
            "START_PROMISING_FOLLOWUP_OBJECTIVE",
            "START_NEW_MECHANISM_OBJECTIVE",
        }

    def _recover_formal_predictive_trial(self) -> Mapping[str, Any]:
        if self._predictive_trial_start_service is None:
            from .research_factory.predictive_trial_start import PredictiveTrialStartServiceV1

            self._predictive_trial_start_service = PredictiveTrialStartServiceV1(self.root, auto_run=True)
        try:
            return self._predictive_trial_start_service.recover(self.objective_id)
        except Exception as exc:
            from .research_factory.predictive_trial_start import PredictiveTrialStartError

            if isinstance(exc, PredictiveTrialStartError):
                return {"status": "WAITING_RECOVERY", "code": exc.code, "message_zh": exc.message_zh}
            return {"status": "WAITING_RECOVERY", "code": "PREDICTIVE_TRIAL_RECOVERY_ERROR", "message_zh": str(exc)[:500]}

    def _recover_if_needed(self) -> None:
        if self.checkpoint is None:
            return
        state = ResearchDaemonState(self.checkpoint.current_state)
        if state == ResearchDaemonState.RECOVER:
            interrupted_state = str(self.checkpoint.canonical_refs.get("interrupted_state") or "")
            if interrupted_state == ResearchDaemonState.PREDICTIVE_RUNNING.value:
                recovery = self.runtime.recover_interrupted(self.checkpoint)
                if recovery and recovery.get("performance_accessed"):
                    self.checkpoint = self.checkpoint.update(required_action="CANONICAL_TRIAL_RECONCILIATION_REQUIRED", retry_safe=False, last_error="interrupted predictive boundary was already accessed", error_reason_code="PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION")
                    self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, "PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION", recovery=recovery)
                    self._write_handoff("ENGINEERING_BLOCKED", "PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION")
                    return
                if recovery and recovery.get("status") in {"COMPLETED", "BLOCKED", "INVALIDATED"}:
                    self.checkpoint = self.checkpoint.update(required_action=None, retry_safe=True)
                    self._transition(ResearchDaemonState.CANDIDATE_COMPLETE, "INTERRUPTED_TRIAL_RECONCILED", recovery=recovery)
                    return
                self.checkpoint = self.checkpoint.update(current_trial=self.checkpoint.current_trial, required_action="PREDICTIVE_RETRY_NOT_AUTHORIZED_UNTIL_CANONICAL_RECONCILIATION", retry_safe=False)
                self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, "PREDICTIVE_RETRY_NOT_AUTHORIZED_UNTIL_CANONICAL_RECONCILIATION")
                self._write_handoff("ENGINEERING_BLOCKED", "PREDICTIVE_RETRY_NOT_AUTHORIZED_UNTIL_CANONICAL_RECONCILIATION")
                return
            if interrupted_state == ResearchDaemonState.PREDICTIVE_PENDING.value and self.checkpoint.current_trial:
                recovery = self.runtime.recover_interrupted(self.checkpoint)
                if recovery and recovery.get("performance_accessed"):
                    self.checkpoint = self.checkpoint.update(required_action="CANONICAL_TRIAL_RECONCILIATION_REQUIRED", retry_safe=False)
                    self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, "PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION", recovery=recovery)
                    self._write_handoff("ENGINEERING_BLOCKED", "PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION")
                    return
            if state == ResearchDaemonState.RECOVER:
                self._transition(ResearchDaemonState.READY, "RECOVERY_COMPLETE")
        elif state == ResearchDaemonState.STRUCTURAL_RUNNING:
            self.checkpoint = self.checkpoint.update(required_action="STRUCTURAL_RETRY_AFTER_RESTART", retry_safe=True)
            self._transition(ResearchDaemonState.STRUCTURAL_PENDING, "STRUCTURAL_INTERRUPTION_RECOVERED")

    def _control(self) -> str | None:
        control = self.store.read_control()
        return str(control.get("action")).upper() if control else None

    def _pause_boundary(self) -> bool:
        action = self._control()
        if action == "STOP":
            self._shutdown = True
            self.checkpoint = self.checkpoint.update(stop_requested=True, required_action="STOPPED_AT_SAFE_BOUNDARY")
            if self.checkpoint.current_state != ResearchDaemonState.SHUTDOWN.value:
                self._transition(ResearchDaemonState.SHUTDOWN, "STOP_REQUESTED_AT_SAFE_BOUNDARY")
            self.store.clear_control()
            return True
        if action == "PAUSE":
            self.checkpoint = self.checkpoint.update(pause_requested=True, required_action="RESUME_REQUIRED")
            if self.checkpoint.current_state != ResearchDaemonState.PAUSED.value:
                self._transition(ResearchDaemonState.PAUSED, "PAUSE_REQUESTED_AT_SAFE_BOUNDARY")
            self.store.clear_control()
            return True
        return False

    def _write_handoff(self, handoff_type: str, reason_code: str) -> Path:
        context = self.runtime.load_context()
        summary = dict(self.runtime.summary())
        failure_ref = context.failure_knowledge_ref
        payload = {
            "schema_version": "research-daemon-handoff-v1",
            "handoff_type": handoff_type,
            "objective": {"objective_id": context.objective_id, "objective_hash": context.objective_hash},
            "current_state": self.checkpoint.current_state if self.checkpoint else None,
            "budget": dict(context.budget),
            "remaining_candidate_state": {"current_candidate": self.checkpoint.current_candidate if self.checkpoint else None, "remaining_frozen_candidates": summary.get("remaining_frozen_candidates"), "global_search_exhausted": summary.get("global_search_exhausted", context.global_search_exhausted)},
            "sanitized_failure_knowledge_ref": failure_ref,
            "capability_context_ref": context.capability_context_ref,
            "blocking_reason": {
                "reason_code": reason_code,
                "artifact_refs": list(
                    dict.fromkeys(
                        list((self.checkpoint.current_trial or {}).get("artifact_refs", ()))
                        + list((self.checkpoint.current_candidate or {}).get("artifact_refs", ()))
                        + list((self.checkpoint.canonical_refs.get("last_structural_result", {}) if self.checkpoint else {}).get("artifact_refs", ()))
                    )
                    if self.checkpoint
                    else []
                ),
            },
            "allowed_next_actions": ["read canonical handoff", "perform explicitly human-triggered research design or scoped engineering repair", "canonicalize and freeze any new Candidate before resume"] if handoff_type == "NEED_AI_RESEARCH_DESIGN" else ["repair the existing canonical blocker", "reconcile canonical TrialLedger/ArtifactGraph", "resume only after identity and policy pins match"],
            "forbidden_actions": ["automatic Codex invocation", "replacement Candidate for an engineering-blocked Candidate", "performance retuning from private outcomes", "new predictive Trial during daemon build acceptance", "Final Test, Prospective or Real Order access"],
            "ai_auto_invocation": "DISABLED",
            "performance_values_exposed": False,
        }
        PerformanceBlindGuard.assert_blind(payload)
        path = self.root / "reports/RESEARCH_DAEMON_HANDOFF_CURRENT.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return path

    def _finish_no_candidate(self) -> None:
        summary = dict(self.runtime.summary())
        budget = dict(summary.get("budget", {}))
        if int(summary.get("research_passed_count", 0) or 0) >= 1:
            self._transition(ResearchDaemonState.RESEARCH_PASSED, "RESEARCH_PASSED_COUNT_REACHED")
        elif int(budget.get("remaining", 1) or 0) <= 0:
            self._transition(ResearchDaemonState.BUDGET_EXHAUSTED, "PREDICTIVE_BUDGET_EXHAUSTED")
        elif bool(summary.get("global_search_exhausted", False)):
            self._transition(ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED, "GLOBAL_LEGAL_SEARCH_EXHAUSTED")
        else:
            self.checkpoint = self.checkpoint.update(required_action="HUMAN_TRIGGERED_AI_RESEARCH_DESIGN", retry_safe=True)
            self._transition(ResearchDaemonState.NEED_AI_RESEARCH_DESIGN, "NO_FROZEN_CANDIDATE_LEGAL_SEARCH_REMAINS")
            self._write_handoff("NEED_AI_RESEARCH_DESIGN", "NO_FROZEN_CANDIDATE_LEGAL_SEARCH_REMAINS")

    def run_once(self) -> dict[str, Any]:
        self.lock.acquire(run_id=self.checkpoint.daemon_run_id if self.checkpoint else "PENDING")
        lock_released_for_formal_recovery = False
        try:
            self._ensure_bootstrap()
            if self.checkpoint.current_state in {item.value for item in (ResearchDaemonState.PAUSED, ResearchDaemonState.NEED_AI_RESEARCH_DESIGN, ResearchDaemonState.ENGINEERING_BLOCKED, ResearchDaemonState.GOVERNANCE_REQUIRED, ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED, ResearchDaemonState.RESEARCH_PASSED, ResearchDaemonState.SHUTDOWN)}:
                return self.status_payload()
            if self.checkpoint.required_action in {
                "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
                "PREDICTIVE_GOVERNANCE_DEFERRED",
                "START_PREDICTIVE_TRIAL_1",
                "PREDICTIVE_TRIAL_RUN_IN_PROGRESS",
                "PREDICTIVE_TRIAL_COMPLETED",
                "PREDICTIVE_TRIAL_FAILURE_REVIEW",
                "CANDIDATE_RESEARCH_DIRECTION_ENDED",
            }:
                if self.checkpoint.required_action in {"START_PREDICTIVE_TRIAL_1", "PREDICTIVE_TRIAL_RUN_IN_PROGRESS"} and self._formal_predictive_trial_pending():
                    self.lock.heartbeat()
                    self.lock.release()
                    lock_released_for_formal_recovery = True
                    recovery = self._recover_formal_predictive_trial()
                    self.checkpoint = self.store.load() or self.checkpoint
                    return {**self.status_payload(), "predictive_trial_recovery": dict(recovery)}
                return self.status_payload()
            if (
                self.checkpoint.current_state in {
                    ResearchDaemonState.READY.value,
                    ResearchDaemonState.STRUCTURAL_PASS.value,
                    ResearchDaemonState.NEXT_CANDIDATE.value,
                }
                and int(dict(self.checkpoint.budget_view).get("remaining", 1) or 0) <= 0
            ):
                self._transition(ResearchDaemonState.BUDGET_EXHAUSTED, "PREDICTIVE_BUDGET_EXHAUSTED_BEFORE_CANDIDATE_SELECTION")
                return self.status_payload()
            if self._pause_boundary():
                return self.status_payload()
            candidate = self.runtime.next_candidate()
            if candidate is None:
                self._finish_no_candidate()
                return self.status_payload()
            self.checkpoint = self.checkpoint.update(current_candidate=candidate.to_dict(), current_trial=None, required_action=None, last_error=None, retry_safe=True)
            self._transition(ResearchDaemonState.STRUCTURAL_PENDING, "FROZEN_CANDIDATE_SELECTED")
            if self._pause_boundary():
                return self.status_payload()
            resource = self.monitor.snapshot(stage=ResearchDaemonState.STRUCTURAL_PENDING.value, candidate_id=candidate.candidate_id, force=True)
            if resource["memory_pressure"]:
                self.checkpoint = self.checkpoint.update(required_action="RESOURCE_WAIT", retry_safe=True)
                self._transition(ResearchDaemonState.RESOURCE_WAIT, "MEMORY_PRESSURE_BEFORE_STRUCTURAL_RUN")
                return self.status_payload()
            self._transition(ResearchDaemonState.STRUCTURAL_RUNNING, "OFFICIAL_FULL_WINDOW_STRUCTURAL_PREFLIGHT_STARTED")
            structural = self.runtime.structural_preflight(candidate)
            self.checkpoint = self.checkpoint.update(canonical_refs={**dict(self.checkpoint.canonical_refs), "last_structural_result": {"status": structural.status, "reason_code": structural.reason_code, "artifact_refs": list(structural.artifact_refs), "provider_checkpoint": structural.provider_checkpoint, "partition_index": structural.partition_index, "details": dict(structural.details)}}, retry_safe=True)
            if structural.status == "PASS":
                self._transition(ResearchDaemonState.STRUCTURAL_PASS, structural.reason_code or "STRUCTURAL_PASS")
                budget = dict(self.runtime.summary().get("budget", {}))
                if int(budget.get("remaining", 1) or 0) <= 0:
                    self._transition(ResearchDaemonState.BUDGET_EXHAUSTED, "PREDICTIVE_BUDGET_EXHAUSTED_BEFORE_GATE")
                    return self.status_payload()
                governance_path = self.root / "reports/research_orchestrator_v2" / self.objective_id / "structural_governance_decision_required.json"
                governed_objective = self._governed_predictive_authorization_required()
                if governed_objective and not governance_path.is_file():
                    try:
                        from .research_factory.structural_reconciliation import persist_governed_structural_pass_boundary

                        boundary = persist_governed_structural_pass_boundary(
                            self.root,
                            objective_id=self.objective_id,
                            candidate=candidate,
                            result=structural,
                            budget_snapshot=budget,
                        )
                        refs = {
                            **dict(self.checkpoint.canonical_refs),
                            "structural_reconciliation": {
                                "status": "PASS",
                                "reconciliation_id": boundary["reconciliation_id"],
                                "report_ref": boundary["report_ref"],
                                "history_ref": boundary.get("history_ref"),
                                "previous_reconciliation_id": None,
                                "lineage_status": "FIRST_CANONICAL_GOVERNED_STRUCTURAL_PASS",
                                "governance_ref": boundary["governance_ref"],
                                "predictive_run_started": False,
                            },
                        }
                        self.checkpoint = self.checkpoint.update(canonical_refs=refs, retry_safe=True)
                        self._save()
                    except Exception as exc:
                        self.checkpoint = self.checkpoint.update(
                            last_error=str(exc),
                            error_reason_code="STRUCTURAL_GOVERNANCE_PUBLICATION_FAILED",
                            required_action="ENGINEERING_REPAIR_OR_RECONCILIATION",
                            retry_safe=True,
                        )
                        self._transition(
                            ResearchDaemonState.ENGINEERING_BLOCKED,
                            "STRUCTURAL_GOVERNANCE_PUBLICATION_FAILED",
                            error_type=type(exc).__name__,
                        )
                        self._write_handoff("ENGINEERING_BLOCKED", "STRUCTURAL_GOVERNANCE_PUBLICATION_FAILED")
                        return self.status_payload()
                governance_entry_required = False
                if governance_path.is_file():
                    try:
                        governance_artifact = json.loads(governance_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        governance_artifact = {}
                    governance_entry_required = isinstance(governance_artifact, Mapping) and str(governance_artifact.get("decision_mode") or "") == "STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED"
                if governed_objective and not governance_entry_required:
                    self.checkpoint = self.checkpoint.update(
                        last_error="governed objective did not produce a valid predictive-authorization boundary",
                        error_reason_code="STRUCTURAL_GOVERNANCE_BOUNDARY_MISSING",
                        required_action="ENGINEERING_REPAIR_OR_RECONCILIATION",
                        retry_safe=True,
                    )
                    self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, "STRUCTURAL_GOVERNANCE_BOUNDARY_MISSING")
                    self._write_handoff("ENGINEERING_BLOCKED", "STRUCTURAL_GOVERNANCE_BOUNDARY_MISSING")
                    return self.status_payload()
                if governance_entry_required:
                    self.checkpoint = self.checkpoint.update(
                        last_completed_candidate=candidate.to_dict(),
                        current_candidate=None,
                        current_trial=None,
                        required_action="PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
                        retry_safe=True,
                    )
                    self.store.append_event(
                        "STRUCTURAL_PASS_AWAITING_PREDICTIVE_AUTHORIZATION",
                        {
                            "objective_id": self.objective_id,
                            "candidate": {"candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash},
                            "required_action": "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED",
                            "predictive_run_started": False,
                        },
                        event_id=stable_hash({"event_type": "STRUCTURAL_PASS_AWAITING_PREDICTIVE_AUTHORIZATION", "candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash}),
                    )
                    return self.status_payload()
                self._transition(ResearchDaemonState.PREDICTIVE_PENDING, "STRUCTURAL_PASS_OPENS_PREDICTIVE_BOUNDARY")
                if self._pause_boundary():
                    return self.status_payload()
                self._transition(ResearchDaemonState.PREDICTIVE_RUNNING, "CANONICAL_PREDICTIVE_RUN_STARTED")
                try:
                    predictive = self.runtime.predictive_validate(candidate)
                except Exception as exc:
                    self.checkpoint = self.checkpoint.update(last_error=str(exc), error_reason_code="PREDICTIVE_RUNTIME_ERROR", required_action="CANONICAL_RUNTIME_REPAIR", retry_safe=False)
                    self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, "PREDICTIVE_RUNTIME_ERROR", error_type=type(exc).__name__)
                    self._write_handoff("ENGINEERING_BLOCKED", "PREDICTIVE_RUNTIME_ERROR")
                    return self.status_payload()
                self.checkpoint = self.checkpoint.update(current_trial={"trial_id": predictive.trial_id, "performance_accessed": predictive.performance_accessed, "status": predictive.status, "classification": predictive.classification, "reason_codes": list(predictive.reason_codes), "artifact_refs": list(predictive.artifact_refs)}, retry_safe=not predictive.performance_accessed)
                self._transition(ResearchDaemonState.PREDICTIVE_COMPLETE, predictive.status or "PREDICTIVE_COMPLETE")
                self.runtime.mark_candidate_complete(candidate, result=predictive)
            elif structural.status == "UNKNOWN":
                self._transition(ResearchDaemonState.STRUCTURAL_UNKNOWN, structural.reason_code or "STRUCTURAL_UNKNOWN")
                self.runtime.mark_candidate_complete(candidate, result=structural)
            elif structural.status == "BLOCKED":
                self._transition(ResearchDaemonState.STRUCTURAL_BLOCKED, structural.reason_code or "STRUCTURAL_BLOCKED")
                self.runtime.mark_candidate_complete(candidate, result=structural)
            else:
                self.checkpoint = self.checkpoint.update(required_action="ENGINEERING_REPAIR_OR_RECONCILIATION", retry_safe=True)
                self._transition(ResearchDaemonState.ENGINEERING_BLOCKED, structural.reason_code or "ENGINEERING_BLOCKED", artifact_refs=list(structural.artifact_refs))
                self._write_handoff("ENGINEERING_BLOCKED", structural.reason_code or "ENGINEERING_BLOCKED")
                return self.status_payload()
            self.checkpoint = self.checkpoint.update(last_completed_candidate=candidate.to_dict(), current_candidate=None, current_trial=None, required_action=None)
            self._transition(ResearchDaemonState.CANDIDATE_COMPLETE, "CANDIDATE_RESULT_PERSISTED")
            self._transition(ResearchDaemonState.NEXT_CANDIDATE, "ADVANCE_AFTER_CANONICAL_RESULT")
            self._transition(ResearchDaemonState.READY, "READY_FOR_NEXT_CANDIDATE")
            return self.status_payload()
        finally:
            if not lock_released_for_formal_recovery:
                self.lock.heartbeat()
                self._save()
                self.lock.release()

    def run_forever(self) -> dict[str, Any]:
        self._install_signal_handlers()
        while not self._shutdown:
            status = self.run_once()
            state = ResearchDaemonState(status["daemon_state"])
            if state in {ResearchDaemonState.PAUSED, ResearchDaemonState.NEED_AI_RESEARCH_DESIGN, ResearchDaemonState.ENGINEERING_BLOCKED, ResearchDaemonState.GOVERNANCE_REQUIRED, ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED, ResearchDaemonState.RESEARCH_PASSED, ResearchDaemonState.SHUTDOWN}:
                return status
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)
        return self.status_payload()

    def _install_signal_handlers(self) -> None:
        def handle(_: int, __: Any) -> None:
            self._shutdown = True
            if self.checkpoint is not None:
                self.checkpoint = self.checkpoint.update(stop_requested=True, required_action="CTRL_C_SAFE_BOUNDARY")
                self._save()
        signal.signal(signal.SIGINT, handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle)

    def status_payload(self) -> dict[str, Any]:
        summary = dict(self.runtime.summary())
        lock_pid = None
        if self.lock.record:
            lock_pid = int(self.lock.record.get("pid", 0) or 0)
        elif self.store.lock_path.exists():
            try:
                lock_pid = int(json.loads(self.store.lock_path.read_text(encoding="utf-8")).get("pid", 0) or 0)
            except (OSError, ValueError):
                lock_pid = None
        process_rss, available_memory = ResourceMonitorV1._memory()
        if lock_pid and lock_pid != os.getpid():
            try:
                import psutil  # type: ignore
                process_rss = int(psutil.Process(lock_pid).memory_info().rss)
            except Exception:
                process_rss, _ = ResourceMonitorV1._windows_memory(lock_pid)
        checkpoint = self.checkpoint
        return {
            "schema_version": "research-daemon-status-v1",
            "daemon_state": checkpoint.current_state if checkpoint else ResearchDaemonState.BOOTSTRAP.value,
            "daemon_run_id": checkpoint.daemon_run_id if checkpoint else None,
            "objective_id": self.objective_id,
            "current_candidate": checkpoint.current_candidate if checkpoint else None,
            "stage": checkpoint.current_state if checkpoint else ResearchDaemonState.BOOTSTRAP.value,
            "budget": dict(summary.get("budget", checkpoint.budget_view if checkpoint else {})),
            "active_trial": summary.get("current_trial") or (checkpoint.current_trial if checkpoint and checkpoint.current_trial else None),
            "last_completed_trial": summary.get("last_completed_trial"),
            "RESEARCH_PASSED": int(summary.get("research_passed_count", 0) or 0),
            "PROMISING": int(summary.get("promising_count", 0) or 0),
            "remaining_frozen_candidates": int(summary.get("remaining_frozen_candidates", 0) or 0),
            "last_checkpoint_time": checkpoint.checkpoint_at if checkpoint else None,
            "process_pid": lock_pid,
            "process_rss_bytes": process_rss,
            "system_available_memory_bytes": available_memory,
            "last_error": checkpoint.last_error if checkpoint else None,
            "required_human_ai_action": checkpoint.required_action if checkpoint else None,
            "ai_auto_invocation": "DISABLED",
            "retry_safe": checkpoint.retry_safe if checkpoint else True,
            "global_search_exhausted": bool(summary.get("global_search_exhausted", False)),
            "canonical_refs": dict(checkpoint.canonical_refs) if checkpoint else {},
        }


def _daemon_for_cli(args: argparse.Namespace) -> ResearchDaemon:
    return ResearchDaemon(args.root, objective_id=args.objective_id, sleep_seconds=args.sleep_seconds)


def _configure_utf8_output() -> None:
    """让 Windows 控制台和重定向输出都按 UTF-8 传递中文。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m chanlun_trader.research_daemon", description="本地确定性量化研究守护进程")
    parser.add_argument("command", choices=("start", "status", "pause", "resume", "stop", "run-once", "recover", "accept-ai-batch", "predictive-trial-preview", "start-predictive-trial"))
    parser.add_argument("artifact", nargs="?", help="accept-ai-batch 使用的 AI 批次制品路径")
    parser.add_argument("--root", default=".")
    parser.add_argument("--objective-id", default=DEFAULT_OBJECTIVE_ID)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--json", action="store_true", help="输出 canonical English machine JSON")
    parser.add_argument("--start-intent-id", help="start-predictive-trial 使用的幂等启动意图编号")
    parser.add_argument("--candidate-id", help="start-predictive-trial 使用的冻结 Candidate 编号")
    parser.add_argument("--candidate-hash", help="start-predictive-trial 使用的冻结 Candidate hash")
    parser.add_argument("--preview-hash", help="start-predictive-trial 使用的启动预览 hash")
    parser.add_argument("--confirmation-token", help="start-predictive-trial 使用的启动确认 token")
    parser.add_argument("--confirmed", action="store_true", help="明确确认执行受保护的预测试验启动动作")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    args = build_parser().parse_args(argv)
    if args.command == "status":
        daemon = _daemon_for_cli(args)
        status = daemon.status_payload()
    elif args.command in {"pause", "resume", "stop"}:
        store = DaemonCheckpointStoreV1(args.root, args.objective_id)
        action = {"pause": "PAUSE", "resume": "RESUME", "stop": "STOP"}[args.command]
        request = store.request(action)
        checkpoint = store.load()
        resumable = {ResearchDaemonState.PAUSED.value, ResearchDaemonState.NEED_AI_RESEARCH_DESIGN.value, ResearchDaemonState.ENGINEERING_BLOCKED.value, ResearchDaemonState.GOVERNANCE_REQUIRED.value, ResearchDaemonState.RESOURCE_WAIT.value, ResearchDaemonState.SAFETY_STOP.value}
        if args.command == "resume" and checkpoint and checkpoint.current_state in resumable:
            target = ResearchDaemonState.RECOVER if checkpoint.current_state == ResearchDaemonState.SAFETY_STOP.value else ResearchDaemonState.READY
            checkpoint = checkpoint.transition(target, "RESUME_REQUESTED")
            checkpoint = checkpoint.update(pause_requested=False, stop_requested=False, required_action=None)
            store.save(checkpoint)
        status = {"command": args.command, "request": request, "checkpoint": checkpoint.to_dict() if checkpoint else None}
    elif args.command == "accept-ai-batch":
        if not args.artifact:
            raise SystemExit("accept-ai-batch requires an artifact path")
        status = _daemon_for_cli(args).runtime.accept_ai_batch(args.artifact)
    elif args.command == "predictive-trial-preview":
        from .research_factory.predictive_trial_start import PredictiveTrialStartServiceV1

        status = PredictiveTrialStartServiceV1(args.root).preview(args.objective_id)
    elif args.command == "start-predictive-trial":
        from .research_factory.predictive_trial_start import PredictiveTrialStartServiceV1

        status = PredictiveTrialStartServiceV1(args.root).confirm(
            args.objective_id,
            {
                "confirmed": args.confirmed,
                "action": "START_PREDICTIVE_TRIAL_1",
                "start_intent_id": args.start_intent_id,
                "candidate_id": args.candidate_id,
                "candidate_hash": args.candidate_hash,
                "preview_hash": args.preview_hash,
                "confirmation_token": args.confirmation_token,
            },
        )
    else:
        daemon = _daemon_for_cli(args)
        if args.command == "start":
            status = daemon.run_forever()
        elif args.command in {"run-once", "recover"}:
            status = daemon.run_once()
        else:
            raise SystemExit(f"unsupported command: {args.command}")
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    elif args.command == "status":
        print(render_daemon_status(status))
    else:
        print(render_cli_result(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
