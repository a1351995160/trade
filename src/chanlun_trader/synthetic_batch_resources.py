"""合成执行子进程的实际资源边界；不支持的系统配置直接拒绝。"""
import ctypes
import json
import os
import signal
import subprocess
import sys


class WindowsMemoryJob:
    def __init__(self, memory_mib, *, process_limit=2):
        if type(process_limit) is not int or not 1 <= process_limit <= 3:
            raise ValueError("BATCH_PROCESS_LIMIT_INVALID")
        self.process_limit = process_limit
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        # 默认容纳venv启动器和解释器；固定DATA任务可显式增加一个Git进程，总提交内存上限不变。
        limits.basic.flags = 0x00000200 | 0x00002000 | 0x00000008
        limits.basic.active_processes = process_limit
        limits.job_memory = memory_mib * 1024 * 1024
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("thread_id", wintypes.DWORD),
                ("process_id", wintypes.DWORD), ("base_priority", wintypes.LONG),
                ("delta_priority", wintypes.LONG), ("flags", wintypes.DWORD)]

        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Thread32First", "Thread32Next"):
            getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            getattr(kernel, name).restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        identifiers = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            present = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.process_id == process.pid:
                    identifiers.append(entry.thread_id)
                present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        if len(identifiers) != 1:
            raise RuntimeError("BATCH_SUSPENDED_PRIMARY_THREAD_NOT_UNIQUE")
        thread = kernel.OpenThread(0x2, False, identifiers[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(thread)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def worker_resource_handshake():
    """worker 在导入领域/数值组件前阻塞；收到父进程完成约束安装后的启动信号。"""
    config = json.loads(sys.stdin.readline())
    if type(config.get("process_limit")) is not int or not 1 <= config["process_limit"] <= 3:
        raise ValueError("BATCH_PROCESS_LIMIT_INVALID")
    if os.name == "posix" and sys.platform.startswith("linux"):
        import resource
        library = ctypes.CDLL(None, use_errno=True)
        if library.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != config["parent_pid"]:
            raise RuntimeError("BATCH_PARENT_LIFETIME_BINDING_FAILED")
        memory = config["memory_mib"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, config["wall_seconds"])
    elif os.name == "nt":
        launcher = config.get("launcher_pid")
        if type(launcher) is not int or launcher not in (os.getpid(), os.getppid()):
            raise RuntimeError("BATCH_WINDOWS_LAUNCHER_BINDING_FAILED")
        inside = ctypes.c_int(0)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(inside)) or not inside.value:
            raise RuntimeError("BATCH_WINDOWS_JOB_NOT_INSTALLED")
        config["windows_job_verified"] = True
    else:
        raise RuntimeError("BATCH_RESOURCE_PLATFORM_UNSUPPORTED")
    return config


def run_bounded_worker(command, *, root, memory_mib, wall_seconds, on_started, environment=None, execution=None, process_limit=2):
    """仅启动显式 Python worker；先安装 OS 上限，再允许调用领域服务。"""
    if type(process_limit) is not int or not 1 <= process_limit <= 3:
        raise ValueError("BATCH_PROCESS_LIMIT_INVALID")
    if type(memory_mib) is not int or memory_mib < 1 or wall_seconds <= 0:
        raise ValueError("BATCH_RESOURCE_LIMIT_INVALID")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise RuntimeError("BATCH_RESOURCE_PLATFORM_UNSUPPORTED")
    job = WindowsMemoryJob(memory_mib, process_limit=process_limit) if os.name == "nt" else None
    process = None
    try:
        process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0)
        if job:
            job.attach(process)
        on_started(process.pid)
        if job:
            job.resume(process)
        config = {"memory_mib": memory_mib, "wall_seconds": wall_seconds, "parent_pid": os.getpid(),
                  "launcher_pid": process.pid, "resource_platform": os.name,
                  "execution": execution, "process_limit": process_limit, "process_limit_enforced": job is not None}
        try:
            stdout, stderr = process.communicate((json.dumps(config) + "\n").encode(), timeout=wall_seconds)
            return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr, "timed_out": False, "launcher_pid": process.pid, "resource_platform": os.name, "windows_job_bound": job is not None, "process_limit": process_limit, "process_limit_enforced": job is not None}
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr, "timed_out": True, "launcher_pid": process.pid, "resource_platform": os.name, "windows_job_bound": job is not None, "process_limit": process_limit, "process_limit_enforced": job is not None}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if job:
            job.close()
