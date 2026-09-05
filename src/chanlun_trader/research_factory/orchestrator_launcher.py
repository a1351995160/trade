"""Canonical localhost launcher for one objective-scoped Orchestrator process."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping

from .common import now_timestamp, stable_hash


_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class OrchestratorLaunchError(RuntimeError):
    def __init__(self, code: str, message_zh: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message_zh)
        self.code = str(code)
        self.message_zh = str(message_zh)
        self.details = dict(details or {})


class OrchestratorProcessLauncherV1:
    """Start only the fixed Orchestrator V2 operation for a governed objective."""

    def __init__(self, root: str | Path, *, popen: Callable[..., Any] | None = None):
        self.root = Path(root).resolve()
        self._popen = popen or subprocess.Popen

    @property
    def _run_root(self) -> Path:
        return self.root / "reports" / "research_orchestrator_v2"

    def _run_dir(self, objective_id: str) -> Path:
        if not _IDENTIFIER.fullmatch(objective_id):
            raise OrchestratorLaunchError("INVALID_OBJECTIVE_ID", "研究目标编号不合法。")
        return self._run_root / objective_id

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OrchestratorLaunchError("ORCHESTRATOR_LAUNCH_RECORD_UNREADABLE", "编排器启动记录暂时不可读。") from exc
        if not isinstance(payload, Mapping):
            raise OrchestratorLaunchError("ORCHESTRATOR_LAUNCH_RECORD_INVALID", "编排器启动记录格式不正确。")
        return dict(payload)

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)

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
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError, PermissionError):
            return False
        return True

    def _validate_activation(self, objective_id: str) -> None:
        objective_path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{objective_id}.json"
        objective = self._read_json(objective_path)
        if objective is None or str(objective.get("objective_id")) != objective_id:
            raise OrchestratorLaunchError("CANONICAL_OBJECTIVE_MISSING", "找不到匹配的 canonical 研究目标。")
        if str(objective.get("lifecycle_state") or objective.get("status") or "") != "READY":
            raise OrchestratorLaunchError("ORCHESTRATOR_ACTIVATION_NOT_READY", "当前研究目标不处于可启动状态。")
        eligibility = self._read_json(self._run_dir(objective_id) / "activation_eligibility.json")
        if not eligibility or str(eligibility.get("objective_id")) != objective_id:
            raise OrchestratorLaunchError("ACTIVATION_ELIGIBILITY_MISSING", "找不到匹配的 Orchestrator 激活资格。")
        if eligibility.get("activation_authorized") is not True or str(eligibility.get("activation_mode")) != "CREATE_AND_ACTIVATE" or str(eligibility.get("target_state")) != "NEED_AI_RESEARCH_DESIGN":
            raise OrchestratorLaunchError("ACTIVATION_ELIGIBILITY_INVALID", "当前目标没有 CREATE_AND_ACTIVATE 激活资格。")

    def _live_orchestrator_pid(self, run_dir: Path) -> int | None:
        lock_path = run_dir / "orchestrator.lock"
        payload = self._read_json(lock_path)
        pid = int((payload or {}).get("pid", 0) or 0)
        return pid if self._pid_alive(pid) else None

    def live_pid(self, objective_id: str) -> int | None:
        """Return the live objective-scoped Orchestrator PID, if one exists."""
        return self._live_orchestrator_pid(self._run_dir(objective_id))

    @staticmethod
    def _reserve(path: Path, payload: Mapping[str, Any]) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return True

    @staticmethod
    def _acquire_start_guard(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OrchestratorLaunchError("ORCHESTRATOR_START_IN_PROGRESS", "编排器启动请求正在处理中，请稍后读取状态。") from exc
        os.close(fd)

    @staticmethod
    def _release_start_guard(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _append_activation_event(self, objective_id: str, execution_id: str, process_pid: int, launch_id: str) -> str | None:
        try:
            from .autonomous_orchestrator_v2 import OrchestratorStoreV2

            event_id = stable_hash({"objective_id": objective_id, "execution_id": execution_id, "launch_id": launch_id})
            return OrchestratorStoreV2(self.root, objective_id).append_event(
                "ORCHESTRATOR_ACTIVATED",
                {"objective_id": objective_id, "execution_id": execution_id, "process_pid": process_pid, "launch_id": launch_id},
                event_id=event_id,
            )
        except (OSError, RuntimeError, ValueError):
            return None

    def start(self, objective_id: str, *, execution_id: str) -> dict[str, Any]:
        objective_id = str(objective_id)
        execution_id = str(execution_id)
        if not _IDENTIFIER.fullmatch(execution_id):
            raise OrchestratorLaunchError("INVALID_EXECUTION_ID", "治理执行编号不合法。")
        self._validate_activation(objective_id)
        run_dir = self._run_dir(objective_id)
        launch_path = run_dir / "process_launch.json"
        live_pid = self._live_orchestrator_pid(run_dir)
        if live_pid is not None:
            return {"schema_version": "orchestrator-process-launch-v1", "objective_id": objective_id, "execution_id": execution_id, "status": "ALREADY_RUNNING", "process_identity": {"pid": live_pid}, "idempotent": True}
        guard_path = run_dir / "process_launch.start.lock"
        self._acquire_start_guard(guard_path)
        try:
            existing = self._read_json(launch_path)
            if existing is not None:
                if str(existing.get("execution_id")) != execution_id:
                    raise OrchestratorLaunchError("ORCHESTRATOR_START_ALREADY_RECORDED", "该研究目标已经有另一条编排器启动记录。", details={"existing_launch_id": existing.get("launch_id")})
                existing_pid = int((existing.get("process_identity") or {}).get("pid", 0) or 0)
                if existing_pid > 0 and self._pid_alive(existing_pid):
                    return {**existing, "idempotent": True}

            restart_count = int((existing or {}).get("restart_count", 0) or 0) + (1 if existing else 0)
            launch_id = f"ORCHESTRATOR_LAUNCH_V1_{stable_hash({'objective_id': objective_id, 'execution_id': execution_id, 'restart_count': restart_count})[:20]}"
            reservation = {
                "schema_version": "orchestrator-process-launch-v1",
                "launch_id": launch_id,
                "objective_id": objective_id,
                "execution_id": execution_id,
                "status": "STARTING",
                "start_operation": "python -m chanlun_trader.research_orchestrator start",
                "launcher_pid": os.getpid(),
                "started_at": now_timestamp(),
                "restart_count": restart_count,
            }
            if existing is None:
                if not self._reserve(launch_path, reservation):
                    raise OrchestratorLaunchError("ORCHESTRATOR_START_IN_PROGRESS", "编排器启动请求正在处理中，请稍后读取状态。")
            else:
                reservation["previous_launch_id"] = existing.get("launch_id")
                self._atomic_write(launch_path, reservation)

            log_dir = run_dir / "logs"
            stdout_path = log_dir / "orchestrator.stdout.log"
            stderr_path = log_dir / "orchestrator.stderr.log"
            command = [sys.executable, "-m", "chanlun_trader.research_orchestrator", "start", "--root", str(self.root), "--objective-id", objective_id, "--json"]
            environment = os.environ.copy()
            source_path = str(self.root / "src")
            environment["PYTHONPATH"] = os.pathsep.join(item for item in (source_path, environment.get("PYTHONPATH", "")) if item)
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
                    process = self._popen(command, cwd=str(self.root), env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, shell=False, creationflags=creationflags)
                event_id = self._append_activation_event(objective_id, execution_id, int(process.pid), launch_id)
                started = {**reservation, "status": "STARTED", "process_identity": {"pid": int(process.pid)}, "command": command, "stdout_path": str(stdout_path.relative_to(self.root)).replace("\\", "/"), "stderr_path": str(stderr_path.relative_to(self.root)).replace("\\", "/"), "activation_event_id": event_id, "started_at": reservation["started_at"], "observed_at": now_timestamp()}
                self._atomic_write(launch_path, started)
                return {**started, "idempotent": False}
            except Exception as exc:
                failed = {**reservation, "status": "FAILED", "error_code": type(exc).__name__, "completed_at": now_timestamp()}
                self._atomic_write(launch_path, failed)
                raise OrchestratorLaunchError("ORCHESTRATOR_START_FAILED", "治理执行已完成，但 Orchestrator 未能启动。", details={"launch_id": launch_id, "error_code": type(exc).__name__}) from exc
        finally:
            self._release_start_guard(guard_path)


__all__ = ["OrchestratorLaunchError", "OrchestratorProcessLauncherV1"]
