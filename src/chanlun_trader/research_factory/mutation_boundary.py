"""单机本地文件系统互斥；锁文件永不删除，进程退出由内核释放锁。"""
from __future__ import annotations

from contextlib import ExitStack
from functools import wraps
import hashlib
import inspect
import os
from pathlib import Path
import threading


class MutationBusyError(RuntimeError):
    pass


_guard = threading.RLock()
_held: dict[str, tuple[int, int, object, int]] = {}


def _after_fork() -> None:
    global _guard
    # 子进程关闭继承副本，不 unlock 父进程的共享 open-file description。
    for held in _held.values():
        held[2].close()
    _held.clear()
    _guard = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _kernel_lock(handle, *, unlock: bool = False) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


class ObjectiveMutationLock:
    """同进程同线程可重入；其他线程/进程立即 BUSY，不使用 PID/TTL 接管。"""

    def __init__(self, root: str | Path, identity: str):
        root = Path(root).resolve(strict=True)
        normalized = os.path.normcase(str(root))
        key = hashlib.sha256(f"{normalized}\0{identity}".encode()).hexdigest()
        self.path = root / "reports" / "mutation_locks" / f"{key}.lock"
        self.key = os.path.normcase(str(self.path))
        self.owner = None

    @classmethod
    def for_resource(cls, path: str | Path):
        resource = Path(path).resolve()
        lock = cls.__new__(cls)
        lock.path = resource.with_name(resource.name + ".mutation.lock")
        lock.key = os.path.normcase(str(lock.path))
        lock.owner = None
        return lock

    def acquire(self, *, run_id: str = "") -> None:
        owner = (os.getpid(), threading.get_ident())
        with _guard:
            if self.owner is not None:
                raise MutationBusyError("lock instance already acquired")
            held = _held.get(self.key)
            if held:
                if held[:2] != owner:
                    raise MutationBusyError("objective mutation is busy")
                _held[self.key] = (*owner, held[2], held[3] + 1)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                try:
                    _kernel_lock(handle)
                except OSError as exc:
                    handle.close()
                    raise MutationBusyError("objective mutation is busy") from exc
                _held[self.key] = (*owner, handle, 1)
            self.owner = owner

    def release(self) -> None:
        with _guard:
            if self.owner is None:
                return
            if self.owner != (os.getpid(), threading.get_ident()):
                raise MutationBusyError("lock release owner mismatch")
            held = _held[self.key]
            if held[3] > 1:
                _held[self.key] = (*held[:3], held[3] - 1)
            else:
                _kernel_lock(held[2], unlock=True)
                held[2].close()
                del _held[self.key]
            self.owner = None

    def probe(self) -> None:
        """只读探测，不创建锁文件；文件不存在由调用方的二次对账检测竞争。"""
        with _guard:
            held = _held.get(self.key)
            if held:
                raise MutationBusyError("objective mutation is busy")
            if not self.path.exists():
                return
            with self.path.open("rb") as handle:
                try:
                    _kernel_lock(handle)
                except OSError as exc:
                    raise MutationBusyError("objective mutation is busy") from exc
                _kernel_lock(handle, unlock=True)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


def mutation_boundary(*, proposal: bool = False, resource: str | None = None, forbid_control_plane: bool = False):
    """服务边界先锁 Objective，再锁共享资源，再执行原有全部领域检查。"""
    def decorate(method):
        signature = inspect.signature(method)

        @wraps(method)
        def guarded(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            service = bound.arguments.get("self")
            root = service.root if service is not None else bound.arguments["root"]
            if bound.arguments.get("apply") is False:
                return method(*args, **kwargs)
            objective = bound.arguments.get("objective_id", getattr(service, "objective_id", None))
            if proposal:
                proposal_id = bound.arguments["proposal_id"]
                from .candidate_executable_materialization import CandidateExecutableMaterializationManagerV1
                objective = CandidateExecutableMaterializationManagerV1(root).objective_id_for_proposal(proposal_id)
            if not isinstance(objective, str) or not objective:
                raise ValueError("MUTATION_OBJECTIVE_REQUIRED")
            with ExitStack() as stack:
                stack.enter_context(ObjectiveMutationLock(root, "objective:" + objective))
                if forbid_control_plane and (Path(root) / "reports" / "research_control_plane" / objective).exists():
                    raise MutationBusyError("CONTROL_PLANE_OBJECTIVE_REQUIRES_EXPLICIT_SERVICE_ENTRY")
                if proposal:
                    current = CandidateExecutableMaterializationManagerV1(root).objective_id_for_proposal(proposal_id)
                    if current != objective:
                        raise ValueError("MUTATION_OBJECTIVE_CHANGED")
                if resource:
                    stack.enter_context(ObjectiveMutationLock(root, "resource:" + resource))
                return method(*args, **kwargs)
        return guarded
    return decorate
