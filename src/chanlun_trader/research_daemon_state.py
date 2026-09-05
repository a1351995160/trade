"""Headless research daemon state and durable orchestration checkpoint."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import json
import os
from pathlib import Path
import uuid
from typing import Any, Mapping

from .research_factory.common import now_timestamp, stable_hash


class ResearchDaemonState(str, Enum):
    BOOTSTRAP = "BOOTSTRAP"
    RECOVER = "RECOVER"
    READY = "READY"
    STRUCTURAL_PENDING = "STRUCTURAL_PENDING"
    STRUCTURAL_RUNNING = "STRUCTURAL_RUNNING"
    STRUCTURAL_PASS = "STRUCTURAL_PASS"
    STRUCTURAL_UNKNOWN = "STRUCTURAL_UNKNOWN"
    STRUCTURAL_BLOCKED = "STRUCTURAL_BLOCKED"
    PREDICTIVE_PENDING = "PREDICTIVE_PENDING"
    PREDICTIVE_RUNNING = "PREDICTIVE_RUNNING"
    PREDICTIVE_COMPLETE = "PREDICTIVE_COMPLETE"
    CANDIDATE_COMPLETE = "CANDIDATE_COMPLETE"
    NEXT_CANDIDATE = "NEXT_CANDIDATE"
    NEED_AI_RESEARCH_DESIGN = "NEED_AI_RESEARCH_DESIGN"
    ENGINEERING_BLOCKED = "ENGINEERING_BLOCKED"
    GOVERNANCE_REQUIRED = "GOVERNANCE_REQUIRED"
    RESOURCE_WAIT = "RESOURCE_WAIT"
    PAUSED = "PAUSED"
    RESEARCH_PASSED = "RESEARCH_PASSED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    GLOBAL_SEARCH_EXHAUSTED = "GLOBAL_SEARCH_EXHAUSTED"
    SAFETY_STOP = "SAFETY_STOP"
    SHUTDOWN = "SHUTDOWN"


class ResearchDaemonTransitionError(RuntimeError):
    pass


_TRANSITIONS: dict[ResearchDaemonState, set[ResearchDaemonState]] = {
    ResearchDaemonState.BOOTSTRAP: {ResearchDaemonState.RECOVER, ResearchDaemonState.READY, ResearchDaemonState.SAFETY_STOP},
    ResearchDaemonState.RECOVER: {ResearchDaemonState.READY, ResearchDaemonState.RESOURCE_WAIT, ResearchDaemonState.ENGINEERING_BLOCKED, ResearchDaemonState.SAFETY_STOP},
    ResearchDaemonState.READY: {
        ResearchDaemonState.STRUCTURAL_PENDING, ResearchDaemonState.NEED_AI_RESEARCH_DESIGN,
        ResearchDaemonState.PREDICTIVE_PENDING,
        ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED,
        ResearchDaemonState.RESEARCH_PASSED, ResearchDaemonState.PAUSED, ResearchDaemonState.SAFETY_STOP,
    },
    ResearchDaemonState.STRUCTURAL_PENDING: {
        ResearchDaemonState.STRUCTURAL_RUNNING,
        ResearchDaemonState.RESOURCE_WAIT,
        ResearchDaemonState.PAUSED,
        ResearchDaemonState.SAFETY_STOP,
        ResearchDaemonState.RECOVER,
    },
    ResearchDaemonState.STRUCTURAL_RUNNING: {
        ResearchDaemonState.STRUCTURAL_PASS, ResearchDaemonState.STRUCTURAL_UNKNOWN,
        ResearchDaemonState.STRUCTURAL_BLOCKED, ResearchDaemonState.ENGINEERING_BLOCKED,
        ResearchDaemonState.RESOURCE_WAIT, ResearchDaemonState.SAFETY_STOP, ResearchDaemonState.RECOVER,
    },
    ResearchDaemonState.STRUCTURAL_PASS: {ResearchDaemonState.PREDICTIVE_PENDING, ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.CANDIDATE_COMPLETE, ResearchDaemonState.PAUSED},
    ResearchDaemonState.STRUCTURAL_UNKNOWN: {ResearchDaemonState.CANDIDATE_COMPLETE, ResearchDaemonState.NEXT_CANDIDATE},
    ResearchDaemonState.STRUCTURAL_BLOCKED: {ResearchDaemonState.CANDIDATE_COMPLETE, ResearchDaemonState.NEXT_CANDIDATE},
    ResearchDaemonState.PREDICTIVE_PENDING: {ResearchDaemonState.PREDICTIVE_RUNNING, ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.PAUSED, ResearchDaemonState.SAFETY_STOP, ResearchDaemonState.RECOVER},
    ResearchDaemonState.PREDICTIVE_RUNNING: {
        ResearchDaemonState.PREDICTIVE_COMPLETE, ResearchDaemonState.ENGINEERING_BLOCKED,
        ResearchDaemonState.GOVERNANCE_REQUIRED, ResearchDaemonState.RESOURCE_WAIT, ResearchDaemonState.SAFETY_STOP, ResearchDaemonState.RECOVER,
    },
    ResearchDaemonState.PREDICTIVE_COMPLETE: {ResearchDaemonState.CANDIDATE_COMPLETE},
    ResearchDaemonState.CANDIDATE_COMPLETE: {ResearchDaemonState.NEXT_CANDIDATE, ResearchDaemonState.READY, ResearchDaemonState.PAUSED},
    ResearchDaemonState.NEXT_CANDIDATE: {ResearchDaemonState.READY, ResearchDaemonState.NEED_AI_RESEARCH_DESIGN, ResearchDaemonState.BUDGET_EXHAUSTED, ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED, ResearchDaemonState.RESEARCH_PASSED},
    ResearchDaemonState.NEED_AI_RESEARCH_DESIGN: {ResearchDaemonState.READY, ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.ENGINEERING_BLOCKED: {ResearchDaemonState.READY, ResearchDaemonState.PREDICTIVE_PENDING, ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.GOVERNANCE_REQUIRED: {ResearchDaemonState.READY, ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.RESOURCE_WAIT: {ResearchDaemonState.READY, ResearchDaemonState.PAUSED, ResearchDaemonState.SAFETY_STOP, ResearchDaemonState.SHUTDOWN},
    ResearchDaemonState.PAUSED: {ResearchDaemonState.READY, ResearchDaemonState.SHUTDOWN, ResearchDaemonState.SAFETY_STOP},
    ResearchDaemonState.RESEARCH_PASSED: {ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.BUDGET_EXHAUSTED: {ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.GLOBAL_SEARCH_EXHAUSTED: {ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.SAFETY_STOP: {ResearchDaemonState.RECOVER, ResearchDaemonState.SHUTDOWN, ResearchDaemonState.PAUSED},
    ResearchDaemonState.SHUTDOWN: set(),
}


@dataclass(frozen=True)
class DaemonCheckpointV1:
    schema_version: str = "research-daemon-checkpoint-v1"
    daemon_run_id: str = ""
    objective_id: str = ""
    current_state: str = ResearchDaemonState.BOOTSTRAP.value
    current_candidate: Mapping[str, Any] | None = None
    current_trial: Mapping[str, Any] | None = None
    last_transition: Mapping[str, Any] | None = None
    last_completed_candidate: Mapping[str, Any] | None = None
    required_action: str | None = None
    canonical_refs: Mapping[str, Any] = field(default_factory=dict)
    budget_view: Mapping[str, Any] = field(default_factory=dict)
    research_counts: Mapping[str, Any] = field(default_factory=dict)
    pause_requested: bool = False
    stop_requested: bool = False
    daemon_started_at: str = field(default_factory=now_timestamp)
    checkpoint_at: str = field(default_factory=now_timestamp)
    last_error: str | None = None
    error_reason_code: str | None = None
    retry_safe: bool = True
    ai_auto_invocation: str = "DISABLED"
    no_new_predictive_trials_during_build: bool = True
    checkpoint_hash: str = ""

    def __post_init__(self) -> None:
        if not self.daemon_run_id:
            object.__setattr__(self, "daemon_run_id", f"DAEMON_{uuid.uuid4().hex[:16]}")
        if not self.objective_id:
            raise ValueError("objective_id is required")
        state = ResearchDaemonState(self.current_state)
        object.__setattr__(self, "current_state", state.value)
        object.__setattr__(self, "current_candidate", dict(self.current_candidate) if self.current_candidate else None)
        object.__setattr__(self, "current_trial", dict(self.current_trial) if self.current_trial else None)
        object.__setattr__(self, "last_transition", dict(self.last_transition) if self.last_transition else None)
        object.__setattr__(self, "last_completed_candidate", dict(self.last_completed_candidate) if self.last_completed_candidate else None)
        object.__setattr__(self, "canonical_refs", dict(self.canonical_refs))
        object.__setattr__(self, "budget_view", dict(self.budget_view))
        object.__setattr__(self, "research_counts", dict(self.research_counts))
        if not self.checkpoint_hash:
            object.__setattr__(self, "checkpoint_hash", stable_hash(self._hash_payload()))

    def _hash_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("checkpoint_hash", None)
        return payload

    def transition(self, target: ResearchDaemonState | str, reason_code: str, *, details: Mapping[str, Any] | None = None) -> "DaemonCheckpointV1":
        target_state = ResearchDaemonState(target)
        current = ResearchDaemonState(self.current_state)
        if target_state != current and target_state not in _TRANSITIONS[current]:
            raise ResearchDaemonTransitionError(f"invalid daemon transition: {current.value}->{target_state.value}")
        transition = {
            "timestamp": now_timestamp(),
            "previous_state": current.value,
            "new_state": target_state.value,
            "candidate": (self.current_candidate or {}).get("candidate_id"),
            "trial": (self.current_trial or {}).get("trial_id"),
            "reason_code": str(reason_code),
            "details": dict(details or {}),
        }
        return replace(self, current_state=target_state.value, last_transition=transition, checkpoint_at=now_timestamp(), checkpoint_hash="")

    def update(self, **changes: Any) -> "DaemonCheckpointV1":
        changes["checkpoint_at"] = now_timestamp()
        changes["checkpoint_hash"] = ""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "daemon_run_id": self.daemon_run_id,
            "objective_id": self.objective_id,
            "current_state": self.current_state,
            "current_candidate": self.current_candidate,
            "current_trial": self.current_trial,
            "last_transition": self.last_transition,
            "last_completed_candidate": self.last_completed_candidate,
            "required_action": self.required_action,
            "canonical_refs": dict(self.canonical_refs),
            "budget_view": dict(self.budget_view),
            "research_counts": dict(self.research_counts),
            "pause_requested": self.pause_requested,
            "stop_requested": self.stop_requested,
            "daemon_started_at": self.daemon_started_at,
            "checkpoint_at": self.checkpoint_at,
            "last_error": self.last_error,
            "error_reason_code": self.error_reason_code,
            "retry_safe": self.retry_safe,
            "ai_auto_invocation": self.ai_auto_invocation,
            "no_new_predictive_trials_during_build": self.no_new_predictive_trials_during_build,
            "checkpoint_hash": self.checkpoint_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DaemonCheckpointV1":
        fields = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in fields})


class DaemonCheckpointStoreV1:
    """Atomic daemon-only state; canonical research artifacts remain elsewhere."""

    def __init__(self, root: str | Path, objective_id: str):
        self.root = Path(root)
        self.objective_id = str(objective_id)
        self.runtime_dir = self.root / "reports" / "research_daemon" / self.objective_id
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.runtime_dir / "daemon_checkpoint.json"
        self.status_path = self.runtime_dir / "daemon_status.json"
        self.events_path = self.runtime_dir / "daemon_events.jsonl"
        self.control_path = self.runtime_dir / "daemon_control.json"
        self.telemetry_path = self.runtime_dir / "daemon_telemetry.jsonl"
        self.lock_path = self.runtime_dir / "daemon.lock"

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def load(self) -> DaemonCheckpointV1 | None:
        if not self.checkpoint_path.exists():
            return None
        payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "research-daemon-checkpoint-v1":
            raise ValueError("daemon checkpoint schema mismatch")
        if str(payload.get("objective_id")) != self.objective_id:
            raise ValueError("daemon checkpoint objective mismatch")
        return DaemonCheckpointV1.from_dict(payload)

    def save(self, checkpoint: DaemonCheckpointV1) -> None:
        if checkpoint.objective_id != self.objective_id:
            raise ValueError("checkpoint objective mismatch")
        self._atomic_write(self.checkpoint_path, checkpoint.to_dict())

    def save_status(self, payload: Mapping[str, Any]) -> None:
        self._atomic_write(self.status_path, dict(payload))

    def load_status(self) -> dict[str, Any] | None:
        if not self.status_path.exists():
            return None
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def append_event(self, event_type: str, payload: Mapping[str, Any], *, event_id: str | None = None) -> str:
        event = {"schema_version": "research-daemon-event-v1", "event_type": str(event_type), **dict(payload)}
        event_id = event_id or stable_hash(event)
        event["event_id"] = event_id
        if self.events_path.exists():
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("event_id") == event_id:
                    return event_id
        event["created_at"] = now_timestamp()
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return event_id

    def request(self, action: str) -> dict[str, Any]:
        payload = {"action": str(action).upper(), "requested_at": now_timestamp(), "requested_by_pid": os.getpid()}
        self._atomic_write(self.control_path, payload)
        return payload

    def read_control(self) -> dict[str, Any] | None:
        if not self.control_path.exists():
            return None
        return json.loads(self.control_path.read_text(encoding="utf-8"))

    def clear_control(self) -> None:
        try:
            self.control_path.unlink()
        except FileNotFoundError:
            return


class DaemonAlreadyRunningError(RuntimeError):
    pass


class DaemonInstanceLockV1:
    """Objective-scoped lock with PID validation and safe stale recovery."""

    def __init__(self, path: str | Path, objective_id: str):
        self.path = Path(path)
        self.objective_id = str(objective_id)
        self.owner_id = uuid.uuid4().hex
        self.record: dict[str, Any] | None = None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            import psutil  # type: ignore
            return bool(psutil.pid_exists(pid))
        except Exception:
            pass
        if os.name == "nt":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError, PermissionError):
            return False
        return True

    def acquire(self, *, run_id: str) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": "research-daemon-lock-v1",
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "objective_id": self.objective_id,
            "daemon_run_id": str(run_id),
            "started_at": now_timestamp(),
            "heartbeat_at": now_timestamp(),
        }
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            self.record = record
            return record
        except FileExistsError as exc:
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as read_exc:
                raise DaemonAlreadyRunningError("daemon lock exists but is unreadable") from read_exc
            existing_pid = int(existing.get("pid", 0) or 0)
            if self._pid_alive(existing_pid):
                raise DaemonAlreadyRunningError(f"daemon already running for objective {self.objective_id}: pid={existing_pid}") from exc
            stale = self.path.with_name(f"{self.path.name}.stale.{existing_pid or 'unknown'}")
            try:
                os.replace(self.path, stale)
            except FileNotFoundError:
                return self.acquire(run_id=run_id)
            return self.acquire(run_id=run_id)

    def heartbeat(self) -> None:
        if not self.record or not self.path.exists():
            return
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("owner_id") != self.owner_id:
            raise DaemonAlreadyRunningError("daemon lock ownership changed")
        current["heartbeat_at"] = now_timestamp()
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def release(self) -> None:
        if not self.record or not self.path.exists():
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if current.get("owner_id") == self.owner_id:
            self.path.unlink(missing_ok=True)
        self.record = None

    def __enter__(self) -> "DaemonInstanceLockV1":
        raise RuntimeError("use acquire(run_id=...) before entering the lock context")

    def __exit__(self, *_: Any) -> None:
        self.release()
