"""账户 worker 启动的协作截止；不导入领域或数值模块，不授予执行权限。"""
import contextlib
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import math
import sys
import time


_ACTIVE = ContextVar('account_startup_boundary_v1', default=None)
_CHUNK_BYTES = 1024 * 1024
_TRACE_CONTROL_FILES = frozenset((__file__, contextlib.__file__))
_SUSPENDABLE_CODE_FLAGS = 0x20 | 0x80 | 0x200  # generator, coroutine, async generator


class StartupBoundaryExceeded(Exception):
    """启动控制流独立于文件错误桶；未知原生调用仍受宿主硬限制。"""

    phase = 'STARTUP'

    def __init__(self, stage):
        super().__init__('UNIVERSE_WORKER_COOPERATIVE_DEADLINE')
        self.stage = stage


class AccountStartupBoundaryV1:
    def __init__(self, deadline, *, on_stage=None):
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError('ACCOUNT_STARTUP_DEADLINE_INVALID')
        self.deadline = deadline
        self.stage = 'ENTRY'
        self.checkpoint_stage = 'ENTRY'
        self.validation_complete = False
        self.enabled = True
        self.stages = []
        self._trace_lines = 0
        self._previous_trace = None
        self._frame_traces = {}
        self._frame_trace_lines = {}
        self._tracing = False
        self._on_stage = on_stage
        self._emitting = False
        self.before_call = None
        self._trace_callback = self._trace
        self._plain_trace_callback = self._trace_plain

    def checkpoint(self, stage='STARTUP'):
        if self.enabled and not self._emitting:
            self.checkpoint_stage = stage
            if time.monotonic() >= self.deadline:
                raise StartupBoundaryExceeded(self.stage)

    def _trace(self, frame, event, arg):
        control_frame = frame.f_code.co_filename in _TRACE_CONTROL_FILES
        if self.enabled and not self._emitting and not control_frame and event == 'call' and self.before_call is not None:
            self.before_call(frame)
        if not self._tracing:
            return self._previous_trace(frame, event, arg) if self._previous_trace is not None else None
        plain_frame = (event == 'call' and self._previous_trace is None and not control_frame
            and frame not in self._frame_traces and frame not in self._frame_trace_lines
            and frame.f_trace is None and not frame.f_code.co_flags & _SUSPENDABLE_CODE_FLAGS)
        previous = self._frame_traces.get(frame, self._previous_trace)
        if previous is not None:
            self._frame_traces[frame] = previous(frame, event, arg)
        elif event == 'call':
            self._frame_traces[frame] = None
        # yield also emits return; the registered caller may still be suspended.
        if event == 'return' and frame not in self._frame_trace_lines:
            self._frame_traces.pop(frame, None)
        # 清理帧必须能把截止异常交回调用方；领域代码仍按固定行数检查。
        if self.enabled and not self._emitting and not control_frame and event == 'line':
            self._trace_lines += 1
            if self._trace_lines >= 64:
                self._trace_lines = 0
                self.checkpoint('PYTHON_LINE_CHECKPOINT')
        if plain_frame and self._tracing and previous is None:
            return self._plain_trace_callback
        return self._trace_callback if self._tracing else previous

    def _trace_plain(self, frame, event, arg):
        # 仅全局 call 已登记的无 previous 普通帧；原有 caller/生成器和外部 trace 不走此路径。
        if not self._tracing:
            return None
        if event == 'return' and frame not in self._frame_trace_lines:
            self._frame_traces.pop(frame, None)
        if self.enabled and not self._emitting and event == 'line':
            self._trace_lines += 1
            if self._trace_lines >= 64:
                self._trace_lines = 0
                self.checkpoint('PYTHON_LINE_CHECKPOINT')
        return self._plain_trace_callback if self._tracing else None

    def _stop_trace(self):
        if self._tracing:
            self._tracing = False
            try:
                sys.settrace(self._previous_trace)
                for frame, previous in tuple(self._frame_traces.items()):
                    frame.f_trace = previous
            finally:
                for frame, lines in tuple(self._frame_trace_lines.items()):
                    frame.f_trace_lines = lines
                self._frame_traces.clear()
                self._frame_trace_lines.clear()

    @contextmanager
    def scope(self):
        token = _ACTIVE.set(self)
        self._previous_trace = sys.gettrace()
        try:
            # contextmanager 的 __enter__ 是中间一帧；已有调用帧不会自动收到 settrace。
            frame = sys._getframe(2)
            self._frame_traces[frame] = frame.f_trace
            self._frame_trace_lines[frame] = frame.f_trace_lines
            self._tracing = True
            sys.settrace(self._trace_callback)
            frame.f_trace_lines = True
            frame.f_trace = self._trace_callback
            yield self
        finally:
            try:
                self._stop_trace()
            finally:
                _ACTIVE.reset(token)

    @contextmanager
    def stage_scope(self, stage):
        previous = self.stage
        self.stage = stage
        row = {'stage': stage, 'started_at_monotonic': time.monotonic(), 'completed': False}
        self.stages.append(row)
        index = len(self.stages)
        try:
            self._emit_stage(index, row, 'ENTER')
            self.checkpoint(stage)
            yield
            self.checkpoint(stage + '_COMPLETE')
            row['completed'] = True
        finally:
            row.setdefault('ended_at_monotonic', time.monotonic())
            self._emit_stage(index, row, 'EXIT')
            self.stage = previous

    def _emit_stage(self, index, row, event):
        if self._on_stage is not None:
            self._emitting = True
            try:
                self._on_stage(index, dict(row), event)
            finally:
                self._emitting = False

    def completed(self):
        self.checkpoint('VALIDATION_COMPLETE')
        completed_at = time.monotonic()
        for index, row in enumerate(self.stages, 1):
            if 'ended_at_monotonic' not in row:
                row.update(completed=True, ended_at_monotonic=completed_at)
                self._emit_stage(index, row, 'VALIDATED')
        self._stop_trace()
        # 诊断提交及 trace 收尾也花费本段时间；写出诊断不能再次获得经济入口时间。
        self.checkpoint('ECONOMIC_ENTRY')
        self.validation_complete = True
        self.enabled = False

    def disable(self):
        self.enabled = False
        self._stop_trace()

    def snapshot(self):
        return {'schema_version': 'account-startup-boundary-v1', 'deadline_monotonic': self.deadline,
            'validation_complete': self.validation_complete, 'last_checkpoint': self.checkpoint_stage,
            'interruptibility': 'PYTHON_CHECKPOINTS_NATIVE_CALLS_SUBJECT_TO_SUPERVISOR',
            'native_call_upper_bound_proven': False, 'stages': list(self.stages)}


def startup_checkpoint(stage='STARTUP'):
    """非账户启动调用保持原语义；活动启动调用共用同一绝对截止。"""
    boundary = _ACTIVE.get()
    if boundary is not None:
        boundary.checkpoint(stage)


def startup_boundary_active():
    boundary = _ACTIVE.get()
    return boundary is not None and boundary.enabled


def startup_read_bytes(path, *, chunk_bytes=_CHUNK_BYTES):
    """逐块读取，保留调用方原有路径、来源及字节身份检查。"""
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= _CHUNK_BYTES:
        raise ValueError('ACCOUNT_STARTUP_READ_CHUNK_INVALID')
    result = bytearray()
    startup_checkpoint('READ_OPEN')
    from .research_factory.secure_file_reference_v1 import _regular_reader
    with _regular_reader(path, error_code='ACCOUNT_STARTUP_FILE_INVALID', root=None) as stream:
        while True:
            startup_checkpoint('READ_CHUNK')
            block = stream.read(chunk_bytes)
            startup_checkpoint('READ_CHUNK_COMPLETE')
            if not block:
                break
            result.extend(block)
    return bytes(result)


def startup_file_sha256(path):
    """完整核对全部字节；每个固定大小块之间允许启动截止退出。"""
    digest = hashlib.sha256()
    startup_checkpoint('HASH_OPEN')
    from .research_factory.secure_file_reference_v1 import _regular_reader
    with _regular_reader(path, error_code='ACCOUNT_STARTUP_FILE_INVALID', root=None) as stream:
        while True:
            startup_checkpoint('HASH_CHUNK')
            block = stream.read(_CHUNK_BYTES)
            startup_checkpoint('HASH_CHUNK_COMPLETE')
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
