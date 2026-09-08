"""通过 PYTHONPATH 显式启用，在测试收集及子进程 import 前安装探针。"""
import atexit
import json
import os
import sys


if os.environ.get("CHANLUN_TEST_ISOLATION") == "1":
    protected = os.path.normcase(os.path.abspath(os.environ["CHANLUN_PROTECTED_ROOT"]))
    counts = {"protected_accesses": 0, "network_calls": 0, "process_calls": 0}

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            for value in args[:2] if event == "os.rename" else args[:1]:
                if isinstance(value, (str, bytes, os.PathLike)):
                    path = os.path.normcase(os.path.abspath(os.fsdecode(value)))
                    if path == protected or path.startswith(protected + os.sep):
                        counts["protected_accesses"] += 1
                        raise AssertionError("PROTECTED_RESEARCH_WORKSPACE_ACCESS")
        if event == "socket.connect":
            # Windows asyncio 的 socketpair 是事件循环自身的本机唤醒通道。
            frame = sys._getframe(1)
            if frame.f_code.co_name != "_fallback_socketpair":
                counts["network_calls"] += 1
                raise AssertionError("BUSINESS_NETWORK_DISABLED")
        if event == "subprocess.Popen":
            command = args[1]
            if isinstance(command, str):
                command = command.lstrip()
                target = command.split('"', 2)[1] if command.startswith('"') else command.split(None, 1)[0]
            else:
                target = command[0]
            target = args[0] or target
            executable = os.path.basename(str(target)).lower()
            if executable not in {"python", "python.exe", "python3", "python3.11", "python3.13"}:
                counts["process_calls"] += 1
                raise AssertionError("RESEARCH_PROCESS_DISABLED")

    sys.addaudithook(audit)

    def report():
        print("P3A_PROCESS_PROBES=" + json.dumps(counts, sort_keys=True), file=sys.stderr)
        if any(counts.values()):
            sys.stderr.flush()
            os._exit(79)

    atexit.register(report)
