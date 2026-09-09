"""Governed Codex proposal backend for the research factory.

The module deliberately stops at proposal generation.  It never reads factory
performance artifacts and it never owns candidate validation, trial budgets, or
research adjudication.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Protocol, Sequence

from .agent_backend import (
    AgentBackendError,
    AgentCallEstimateV1,
    ResearchAgentBackendV1,
    ResearchAgentInputV1,
    ResearchProposalBatchV1,
    ResearchProposalV1,
    assert_agent_design_time,
)
from .common import jsonable, now_timestamp, stable_hash
from .source_dependencies import SOURCE_ROOT
from .context import OutcomeBlindFieldPolicyV1, PerformanceBlindGuard


CODEX_BACKEND_TYPE = "CODEX"
CODEX_BACKEND_VERSION = "CodexResearchAgentBackendV1"
CODEX_RESEARCH_AGENT_ENABLED = False
DEFAULT_RESEARCH_AGENT_BACKEND = "TEMPLATE"
PROMPT_ID = "CodexResearchPromptV1"
PROMPT_VERSION = "1.0.0"
PROMPT_DOCUMENT = "docs/CODEX_RESEARCH_PROMPT_V1.md"
DEFAULT_CODEX_MODEL = "gpt-5.5"
CODEX_STARTUP_TIMEOUT_SECONDS = 45
CODEX_INACTIVITY_TIMEOUT_SECONDS = 75

_FORBIDDEN_OUTPUT_FIELDS = frozenset({
    *OutcomeBlindFieldPolicyV1.FORBIDDEN_FIELDS,
    "expected_return", "backtest_return", "ranking_by_performance",
    "research_passed", "promising", "weak", "rejected", "buy_list",
    "sell_list", "stock_list", "stock_recommendation",
})
_FORBIDDEN_OUTPUT_VALUES = frozenset({
    "RESEARCH_PASSED", "PROMISING", "WEAK", "REJECTED", "BUY", "SELL",
})
_BATCH_FIELDS = frozenset({
    "schema_version", "run_id", "batch_id", "proposals", "backend_type",
    "backend_version", "proposal_schema_version", "prompt_template_version",
    "model_id", "input_context_hash", "generated_at", "proposal_batch_hash",
    "provenance", "empty_reason",
})
_PROPOSAL_FIELDS = frozenset({
    "schema_version", "proposal_id", "hypothesis_id", "family_id", "mechanism",
    "economic_rationale", "factor_dependencies", "event_dependencies",
    "expected_holding_horizon", "candidate_complexity", "required_data",
    "novelty_claim", "provenance", "observable_conditions",
    "falsification_conditions",
})
_PROVENANCE_FIELDS = frozenset({
    "backend", "backend_type", "backend_version", "runtime_version", "model_id",
    "prompt_id", "prompt_version", "prompt_hash", "input_hash",
    "input_context_hash", "response_hash", "agent_call_id", "run_id", "batch_id",
    "generated_at", "generation_timestamp", "retry_count", "duration_seconds",
    "seed", "usage", "cost", "error", "source", "provenance_version",
})


class CodexInvocationError(AgentBackendError):
    """A runtime/transport error, never an alpha or research classification."""


def _safe_component(value: str, name: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or Path(text).name != text or any(char in text for char in "\\/"):
        raise CodexInvocationError(f"CODEX_SANDBOX_VIOLATION:{name}")
    return text


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class CodexResearchPromptV1:
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    created_at: str
    template: str
    document_path: str = PROMPT_DOCUMENT

    @classmethod
    def from_document(cls, path: str | Path) -> "CodexResearchPromptV1":
        document = Path(path)
        text = document.read_text(encoding="utf-8")
        metadata: dict[str, str] = {}
        for line in text.splitlines():
            if line.startswith("- ") and ":" in line:
                key, value = line[2:].split(":", 1)
                metadata[key.strip()] = value.strip()
        begin = "<!-- BEGIN CODEX PRODUCTION TEMPLATE -->"
        end = "<!-- END CODEX PRODUCTION TEMPLATE -->"
        if begin not in text or end not in text:
            raise CodexInvocationError("CODEX_PROMPT_DOCUMENT_INVALID")
        template = text.split(begin, 1)[1].split(end, 1)[0].strip()
        prompt_id = metadata.get("prompt_id", PROMPT_ID)
        prompt_version = metadata.get("prompt_version", PROMPT_VERSION)
        created_at = metadata.get("created_at", "")
        if not created_at:
            raise CodexInvocationError("CODEX_PROMPT_CREATED_AT_MISSING")
        return cls(prompt_id, prompt_version, stable_hash(template), created_at, template, str(document))

    @classmethod
    def default(cls, root: str | Path = ".") -> "CodexResearchPromptV1":
        return cls.from_document(Path(root) / PROMPT_DOCUMENT)

    def render(self, agent_input: Mapping[str, Any]) -> str:
        PerformanceBlindGuard.assert_blind(agent_input)
        return f"{self.template}\n\nRESEARCH_AGENT_INPUT_JSON\n{json.dumps(jsonable(agent_input), ensure_ascii=False, sort_keys=True)}\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "created_at": self.created_at,
            "document_path": self.document_path,
        }


@dataclass(frozen=True)
class CodexInvocationRequestV1:
    agent_call_id: str
    run_id: str
    batch_id: str
    objective_id: str
    policy_identity: Mapping[str, Any]
    input_context_hash: str
    research_agent_input: Mapping[str, Any]
    prompt: str
    prompt_metadata: Mapping[str, Any]
    allowed_capabilities: tuple[str, ...] = (
        "read_sanitized_design_input",
        "write_temporary_model_output",
        "write_proposal_artifact",
        "write_agent_audit_artifact",
    )
    resource_governance_reservation: Mapping[str, Any] = None  # type: ignore[assignment]
    model_route: str = "CODEX"
    output_schema_version: str = "research-proposal-batch-v1"

    def __post_init__(self) -> None:
        if self.resource_governance_reservation is None:
            object.__setattr__(self, "resource_governance_reservation", {})
        PerformanceBlindGuard.assert_blind(self.research_agent_input)
        assert_agent_design_time(self.research_agent_input)
        object.__setattr__(self, "policy_identity", jsonable(self.policy_identity))
        object.__setattr__(self, "allowed_capabilities", tuple(self.allowed_capabilities))
        object.__setattr__(self, "resource_governance_reservation", jsonable(self.resource_governance_reservation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "codex-invocation-request-v1",
            "agent_call_id": self.agent_call_id,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "objective_id": self.objective_id,
            "policy_identity": jsonable(self.policy_identity),
            "prompt": self.prompt,
            "prompt_metadata": jsonable(self.prompt_metadata),
            "research_agent_input": jsonable(self.research_agent_input),
            "input_context_hash": self.input_context_hash,
            "allowed_capabilities": list(self.allowed_capabilities),
            "resource_governance_reservation": jsonable(self.resource_governance_reservation),
            "model_route": self.model_route,
            "output_schema_version": self.output_schema_version,
            "performance_data_loaded": False,
            "outcome_fields_available": False,
        }


@dataclass(frozen=True)
class CodexInvocationResultV1:
    response_text: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    runtime_version: str = "UNKNOWN"
    model_id: str = "NONE"
    usage: Mapping[str, Any] = None  # type: ignore[assignment]
    duration_seconds: float = 0.0
    command: tuple[str, ...] = ()
    process_id: int | None = None
    started_at: str | None = None
    last_stdout_at: str | None = None
    last_stderr_at: str | None = None
    last_progress_at: str | None = None
    manifest_detected_at: str | None = None
    process_exit_at: str | None = None
    timeout_type: str | None = None
    output_mode: str = "OUTPUT_LAST_MESSAGE_FILE"

    def __post_init__(self) -> None:
        if self.usage is None:
            object.__setattr__(self, "usage", {})


class CodexExecutorV1(Protocol):
    def execute(
        self,
        request: CodexInvocationRequestV1,
        *,
        staging_dir: Path,
        output_schema_path: Path,
        response_path: Path,
        timeout_seconds: int,
    ) -> CodexInvocationResultV1:
        ...


def _launcher(executable: str | Path) -> list[str]:
    path = str(executable)
    if path.casefold().endswith(".ps1"):
        shell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not shell:
            raise CodexInvocationError("CODEX_RUNTIME_NOT_AVAILABLE")
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    if path.casefold().endswith(".cmd") or path.casefold().endswith(".bat"):
        wrapper_path = Path(path)
        bundled = wrapper_path.parent / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        if bundled.exists():
            return [str(bundled)]
        raise CodexInvocationError("CODEX_RUNTIME_NOT_AVAILABLE")
    return [path]


def build_codex_command(
    executable: str | Path,
    *,
    staging_dir: Path,
    output_schema_path: Path,
    response_path: Path,
    model_id: str = DEFAULT_CODEX_MODEL,
) -> list[str]:
    """Build the version-pinned, non-interactive staging command.

    The parent validates the final Manifest against the canonical contract.  The
    CLI ``--output-schema`` switch is deliberately not used here: its strict
    server-side dialect rejected otherwise valid nested research contracts in
    the failed real run.  The schema file remains in staging as a human- and
    test-visible contract reference.  ``--approve-for-me`` is the CLI's
    non-interactive workspace-write route; combining it with ``--sandbox`` is
    rejected by the current CLI.
    """
    del output_schema_path
    command = _launcher(executable) + [
        "exec", "-", "--json", "--ephemeral", "--skip-git-repo-check",
        "--ignore-user-config", "--model", model_id, "--approve-for-me",
        "--color", "never", "--cd", str(staging_dir),
        "--output-last-message", str(response_path),
    ]
    for feature in ("apps", "plugins", "browser_use", "computer_use", "shell_snapshot"):
        command.extend(("--disable", feature))
    return command


_DESKTOP_CONTEXT_ENV = frozenset({
    "CODEX_APP_TOOLS_PIPE_PATH", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_MCP_NODE_PATH",
    "CODEX_PERMISSION_PROFILE", "CODEX_SESSION_ID", "CODEX_SHELL", "CODEX_THREAD_ID",
})


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _DESKTOP_CONTEXT_ENV:
        environment.pop(name, None)
    return environment


def _redact_runtime_text(value: str) -> str:
    text = str(value or "")
    secret_values = [
        str(value) for name, value in os.environ.items()
        if re.search(r"(?i)(token|key|secret|password|credential|authorization)", name)
        and value
        and len(str(value)) >= 8
    ]
    for secret in sorted(set(secret_values), key=len, reverse=True):
        text = text.replace(secret, "<REDACTED>")
    text = re.sub(r"(?i)(bearer\s+|sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9_-]{10,})", "<REDACTED>", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;}]+", r"\1=<REDACTED>", text)
    return text


def _write_runtime_diagnostics(path: Path, payload: Mapping[str, Any]) -> None:
    _write_json(path, dict(payload))


class SubprocessCodexExecutorV1:
    """Invoke Codex with an isolated staging cwd and continuously drained pipes."""

    def __init__(self, executable: str | Path):
        self.executable = str(executable)

    def execute(
        self,
        request: CodexInvocationRequestV1,
        *,
        staging_dir: Path,
        output_schema_path: Path,
        response_path: Path,
        timeout_seconds: int,
    ) -> CodexInvocationResultV1:
        staging_dir = Path(staging_dir).resolve()
        response_path = Path(response_path).resolve()
        output_schema_path = Path(output_schema_path).resolve()
        staging_dir.mkdir(parents=True, exist_ok=True)
        command = build_codex_command(
            self.executable,
            staging_dir=staging_dir,
            output_schema_path=output_schema_path,
            response_path=response_path,
        )
        started = time.monotonic()
        started_at = now_timestamp()
        diagnostics_path = staging_dir / "codex_runtime_diagnostics.json"
        diagnostics: dict[str, Any] = {
            "schema_version": "codex-runtime-diagnostics-v1",
            "status": "STARTING",
            "started_at": started_at,
            "last_stdout_at": None,
            "last_stderr_at": None,
            "last_progress_at": started_at,
            "manifest_detected_at": None,
            "process_exit_at": None,
            "timeout_type": None,
            "process_id": None,
            "command": list(command),
            "output_mode": "OUTPUT_LAST_MESSAGE_FILE",
            "response_path": str(response_path),
            "staging_dir": str(staging_dir),
        }
        _write_runtime_diagnostics(diagnostics_path, diagnostics)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(staging_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=creationflags,
                env=_child_environment(),
            )
        except (FileNotFoundError, OSError) as exc:
            raise CodexInvocationError(f"CODEX_RUNTIME_NOT_AVAILABLE:{type(exc).__name__}") from exc
        process_id = int(getattr(process, "pid", 0) or 0) or None
        diagnostics.update({"status": "RUNNING", "process_id": process_id})
        _write_runtime_diagnostics(diagnostics_path, diagnostics)

        stdout_chunks: deque[str] = deque(maxlen=4096)
        stderr_chunks: deque[str] = deque(maxlen=4096)
        activity_lock = threading.Lock()
        activity: dict[str, str | None] = {"stdout": None, "stderr": None, "progress": started_at}
        activity_mono: dict[str, float | None] = {
            "stdout": None,
            "stderr": None,
            "progress": started,
        }

        def drain(stream: Any, name: str, chunks: deque[str]) -> None:
            if stream is None:
                return
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                if not isinstance(chunk, str):
                    chunk = str(chunk)
                chunks.append(chunk)
                timestamp = now_timestamp()
                with activity_lock:
                    activity[name] = timestamp
                    activity["progress"] = timestamp
                    activity_mono[name] = time.monotonic()
                    activity_mono["progress"] = activity_mono[name]
                diagnostics.update({"last_stdout_at": activity["stdout"], "last_stderr_at": activity["stderr"], "last_progress_at": timestamp})
                _write_runtime_diagnostics(diagnostics_path, diagnostics)

        stdout_stream = getattr(process, "stdout", None)
        stderr_stream = getattr(process, "stderr", None)
        threads = [
            threading.Thread(target=drain, args=(stdout_stream, "stdout", stdout_chunks), daemon=True),
            threading.Thread(target=drain, args=(stderr_stream, "stderr", stderr_chunks), daemon=True),
        ]
        for thread in threads:
            thread.start()

        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            stdin.write(request.prompt)
            stdin.flush()
            stdin.close()

        timeout_type: str | None = None
        first_activity = started
        overall_deadline = started + max(1, int(timeout_seconds))
        while True:
            if response_path.exists() and diagnostics.get("manifest_detected_at") is None:
                diagnostics["manifest_detected_at"] = now_timestamp()
                diagnostics["last_progress_at"] = diagnostics["manifest_detected_at"]
                with activity_lock:
                    activity["progress"] = str(diagnostics["manifest_detected_at"])
                    activity_mono["progress"] = time.monotonic()
                _write_runtime_diagnostics(diagnostics_path, diagnostics)
            returncode = process.poll()
            if returncode is not None:
                diagnostics["process_exit_at"] = now_timestamp()
                diagnostics["status"] = "EXITED"
                _write_runtime_diagnostics(diagnostics_path, diagnostics)
                break
            current = time.monotonic()
            with activity_lock:
                last_output = max(
                    (value for value in (activity["stdout"], activity["stderr"]) if value),
                    default=None,
                )
                progress_mono = activity_mono["progress"]
            if not last_output and current - first_activity >= CODEX_STARTUP_TIMEOUT_SECONDS:
                timeout_type = "STARTUP"
            elif progress_mono is not None:
                progress_age = current - progress_mono
                if progress_age >= CODEX_INACTIVITY_TIMEOUT_SECONDS:
                    timeout_type = "INACTIVITY"
            if timeout_type is None and current >= overall_deadline:
                timeout_type = "OVERALL"
            if timeout_type is not None:
                diagnostics.update({"status": "TIMED_OUT", "timeout_type": timeout_type, "last_stdout_at": activity["stdout"], "last_stderr_at": activity["stderr"], "last_progress_at": activity["progress"]})
                _write_runtime_diagnostics(diagnostics_path, diagnostics)
                self._terminate(process)
                break
            time.sleep(0.05)

        for thread in threads:
            thread.join(timeout=5)
        try:
            process.wait(timeout=5)
        except (AttributeError, subprocess.TimeoutExpired):
            pass
        process_exit_at = diagnostics.get("process_exit_at") or now_timestamp()
        if timeout_type is not None:
            diagnostics.update({"process_exit_at": process_exit_at, "status": "TIMED_OUT"})
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        response_text = response_path.read_text(encoding="utf-8") if response_path.exists() else ""
        metadata = self._parse_events(stdout)
        return CodexInvocationResultV1(
            response_text=response_text,
            stdout=stdout,
            stderr=stderr,
            exit_code=int(process.returncode if process.returncode is not None else -1),
            timed_out=timeout_type is not None,
            runtime_version=str(metadata.get("runtime_version", "UNKNOWN")),
            model_id=str(metadata.get("model_id", "NONE")),
            usage=metadata.get("usage", {}),
            duration_seconds=round(time.monotonic() - started, 6),
            command=tuple(command),
            process_id=process_id,
            started_at=started_at,
            last_stdout_at=activity["stdout"],
            last_stderr_at=activity["stderr"],
            last_progress_at=activity["progress"],
            manifest_detected_at=diagnostics.get("manifest_detected_at"),
            process_exit_at=process_exit_at,
            timeout_type=timeout_type,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            else:
                process.kill()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    @staticmethod
    def _parse_events(stdout: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, Mapping):
                continue
            if isinstance(item.get("usage"), Mapping):
                metadata["usage"] = dict(item["usage"])
            if item.get("model"):
                metadata["model_id"] = item["model"]
            if item.get("model_id"):
                metadata["model_id"] = item["model_id"]
            if item.get("version") and item.get("type") == "runtime" :
                metadata["runtime_version"] = item["version"]
        return metadata


class CodexProposalParserV1:
    """Parse only the exact proposal contract; no prose repair or field guessing."""

    def parse(
        self,
        raw_response: str,
        *,
        input: ResearchAgentInputV1,
        prompt: CodexResearchPromptV1,
        response_hash: str,
    ) -> ResearchProposalBatchV1:
        try:
            payload = json.loads(raw_response)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CodexInvocationError("CODEX_INVALID_RESPONSE") from exc
        if not isinstance(payload, Mapping):
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:top-level-object-required")
        self._reject_forbidden(payload)
        self._reject_unknown(payload, _BATCH_FIELDS, "batch")
        self._require(payload, ("schema_version", "run_id", "batch_id", "proposals", "backend_type", "backend_version", "prompt_template_version", "input_context_hash", "provenance"), "batch")
        if payload.get("schema_version") != "research-proposal-batch-v1":
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:schema_version")
        if payload.get("run_id") != input.run_id or payload.get("batch_id") != input.batch_id:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:request_identity")
        if payload.get("backend_type") != CODEX_BACKEND_TYPE:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:backend_type")
        if payload.get("input_context_hash") != input.input_context_hash:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:input_context_hash")
        if payload.get("prompt_template_version") != prompt.prompt_version:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:prompt_version")
        if not isinstance(payload.get("proposals"), list):
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:proposals-array-required")
        proposals = tuple(self._proposal(item, index) for index, item in enumerate(payload["proposals"], start=1))
        if not proposals and payload.get("empty_reason") != "NO_LEGAL_RESEARCH_PROPOSAL":
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:empty_reason")
        max_proposals = int(input.proposal_budget_view.get("max_proposals", input.proposal_budget_view.get("max_hypotheses", 0)) or 0)
        if max_proposals and len(proposals) > max_proposals:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:proposal_budget")
        for proposal in proposals:
            self._validate_dependencies(proposal, input)
            self._validate_horizon(proposal.expected_holding_horizon, input)
            self._validate_complexity(proposal.candidate_complexity, input)
            if not proposal.observable_conditions or not proposal.falsification_conditions:
                raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:falsifiable_hypothesis_required")
        if not isinstance(payload["provenance"], Mapping):
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:provenance-object-required")
        self._reject_unknown(payload["provenance"], _PROVENANCE_FIELDS, "batch-provenance")
        provenance = dict(payload["provenance"])
        provenance.update({
            "backend": CODEX_BACKEND_TYPE,
            "backend_version": CODEX_BACKEND_VERSION,
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.prompt_version,
            "prompt_hash": prompt.prompt_hash,
            "input_hash": input.input_context_hash,
            "response_hash": response_hash,
            "generated_at": str(payload.get("generated_at") or now_timestamp()),
        })
        try:
            return ResearchProposalBatchV1(
                run_id=str(payload["run_id"]),
                batch_id=str(payload["batch_id"]),
                proposals=proposals,
                backend_type=CODEX_BACKEND_TYPE,
                backend_version=CODEX_BACKEND_VERSION,
                proposal_schema_version="research-proposal-v1",
                prompt_template_version=prompt.prompt_version,
                model_id=str(payload.get("model_id") or "NONE"),
                input_context_hash=input.input_context_hash,
                generated_at=str(payload.get("generated_at") or now_timestamp()),
                proposal_batch_hash=str(payload.get("proposal_batch_hash") or ""),
                provenance=provenance,
                empty_reason=str(payload.get("empty_reason") or ""),
            )
        except AgentBackendError:
            raise
        except Exception as exc:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:proposal-contract") from exc

    @staticmethod
    def _require(payload: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
        missing = [field for field in fields if field not in payload]
        if missing:
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:{label}-missing-{','.join(missing)}")

    @staticmethod
    def _reject_unknown(payload: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:{label}-unknown-field-{unknown[0]}")

    @classmethod
    def _reject_forbidden(cls, value: Any) -> None:
        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    normalized = OutcomeBlindFieldPolicyV1.normalize_field(key)
                    if normalized in _FORBIDDEN_OUTPUT_FIELDS:
                        raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:forbidden-field-{key}")
                    if isinstance(nested, str) and nested.strip().upper() in _FORBIDDEN_OUTPUT_VALUES:
                        raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:forbidden-value-{nested}")
                    visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
        visit(value)

    def _proposal(self, item: Any, index: int) -> ResearchProposalV1:
        if not isinstance(item, Mapping):
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:proposal-{index}-object-required")
        self._reject_unknown(item, _PROPOSAL_FIELDS, f"proposal-{index}")
        self._require(item, ("schema_version", "proposal_id", "hypothesis_id", "family_id", "mechanism", "economic_rationale", "factor_dependencies", "event_dependencies", "expected_holding_horizon", "candidate_complexity", "required_data", "novelty_claim", "provenance", "observable_conditions", "falsification_conditions"), f"proposal-{index}")
        if item.get("schema_version") != "research-proposal-v1":
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:proposal-{index}-schema_version")
        string_fields = ("proposal_id", "hypothesis_id", "family_id", "mechanism", "economic_rationale", "expected_holding_horizon", "novelty_claim")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in string_fields):
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:proposal-{index}-string-field")
        for field in ("factor_dependencies", "event_dependencies", "required_data", "observable_conditions", "falsification_conditions"):
            if not isinstance(item.get(field), list) or any(not isinstance(value, str) or not value.strip() for value in item[field]):
                raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:proposal-{index}-{field}")
        if not isinstance(item.get("candidate_complexity"), Mapping) or not isinstance(item.get("provenance"), Mapping):
            raise CodexInvocationError(f"CODEX_SCHEMA_VIOLATION:proposal-{index}-object-field")
        self._reject_unknown(item["provenance"], _PROVENANCE_FIELDS, f"proposal-{index}-provenance")
        return ResearchProposalV1(
            proposal_id=item["proposal_id"], hypothesis_id=item["hypothesis_id"], family_id=item["family_id"],
            mechanism=item["mechanism"], economic_rationale=item["economic_rationale"],
            factor_dependencies=tuple(item["factor_dependencies"]), event_dependencies=tuple(item["event_dependencies"]),
            expected_holding_horizon=item["expected_holding_horizon"], candidate_complexity=dict(item["candidate_complexity"]),
            required_data=tuple(item["required_data"]), novelty_claim=item["novelty_claim"], provenance=dict(item["provenance"]),
            observable_conditions=tuple(item["observable_conditions"]), falsification_conditions=tuple(item["falsification_conditions"]),
        )

    @staticmethod
    def _catalog_ids(catalog: Any, key: str) -> set[str]:
        values: list[Any] = []
        if isinstance(catalog, Mapping):
            if isinstance(catalog.get(key + "s"), Sequence) and not isinstance(catalog.get(key + "s"), (str, bytes)):
                values.extend(catalog[key + "s"])
            else:
                values.extend(catalog.values())
        elif isinstance(catalog, Sequence) and not isinstance(catalog, (str, bytes)):
            values.extend(catalog)
        return {str(item.get(key + "_id")) for item in values if isinstance(item, Mapping) and item.get(key + "_id")}

    def _validate_dependencies(self, proposal: ResearchProposalV1, input: ResearchAgentInputV1) -> None:
        factors = self._catalog_ids(input.factor_event_catalog, "factor")
        events = self._catalog_ids(input.factor_event_catalog, "event")
        if factors and any(item not in factors for item in proposal.factor_dependencies):
            raise CodexInvocationError("FACTOR_RESEARCH_PROPOSAL_REQUIRED")
        if events and any(item not in events for item in proposal.event_dependencies):
            raise CodexInvocationError("EVENT_RESEARCH_PROPOSAL_REQUIRED")

    @staticmethod
    def _validate_horizon(horizon: str, input: ResearchAgentInputV1) -> None:
        match = re.fullmatch(r"(\d+)[_-](\d+)D?", horizon.strip(), flags=re.IGNORECASE)
        if not match:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:holding_horizon")
        low, high = int(match.group(1)), int(match.group(2))
        allowed = input.objective.get("holding_horizon", (2, 10))
        if not isinstance(allowed, Sequence) or len(allowed) != 2 or low < int(allowed[0]) or high > int(allowed[1]) or low > high:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:holding_horizon")

    @staticmethod
    def _validate_complexity(complexity: Mapping[str, Any], input: ResearchAgentInputV1) -> None:
        limits = input.complexity_constraints
        for field in ("factor_count", "event_count", "condition_count", "interaction_depth", "free_parameter_count"):
            if field in limits and field in complexity:
                try:
                    if int(complexity[field]) > int(limits[field]):
                        raise CodexInvocationError("COMPLEXITY_BLOCKED")
                except (TypeError, ValueError) as exc:
                    raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:complexity") from exc


class CodexInvocationAdapterV1:
    """Persist request/raw/parsed artifacts around one isolated invocation."""

    def __init__(self, *, root: str | Path = ".", executor: CodexExecutorV1 | None = None, executable: str | Path | None = None, parser: CodexProposalParserV1 | None = None):
        self.root = Path(root)
        if executor is None:
            if executable is None:
                executable = discover_codex_executable()
            if executable is None:
                raise CodexInvocationError("CODEX_RUNTIME_NOT_AVAILABLE")
            executor = SubprocessCodexExecutorV1(executable)
        self.executor = executor
        self.parser = parser or CodexProposalParserV1()
        self.timeout_seconds = 30
        self._last_artifact_dir: Path | None = None

    def set_timeout_seconds(self, value: int) -> None:
        self.timeout_seconds = max(1, int(value))

    def artifact_dir(self, *, run_id: str, batch_id: str, agent_call_id: str) -> Path:
        return self.root / "data" / "research" / "agent_runs" / _safe_component(run_id, "run_id") / _safe_component(batch_id, "batch_id") / _safe_component(agent_call_id, "agent_call_id")

    def invoke(self, request: CodexInvocationRequestV1) -> ResearchProposalBatchV1:
        staging = self.artifact_dir(run_id=request.run_id, batch_id=request.batch_id, agent_call_id=request.agent_call_id)
        staging.mkdir(parents=True, exist_ok=True)
        self._last_artifact_dir = staging
        schema_path = staging / "research_proposal_batch.schema.json"
        response_path = staging / "codex_response_last_message.json"
        _write_json(staging / "codex_request.json", {**request.to_dict(), "request_hash": stable_hash(request.to_dict())})
        _write_json(schema_path, _proposal_schema())
        result = self.executor.execute(request, staging_dir=staging, output_schema_path=schema_path, response_path=response_path, timeout_seconds=self.timeout_seconds)
        raw = result.response_text or result.stdout or ""
        (staging / "codex_response_raw.txt").write_text(raw, encoding="utf-8")
        if result.timed_out:
            raise CodexInvocationError("CODEX_TIMEOUT")
        if result.exit_code != 0:
            if "auth" in result.stderr.casefold() or "login" in result.stderr.casefold():
                raise CodexInvocationError("CODEX_AUTH_NOT_AVAILABLE")
            raise CodexInvocationError("CODEX_NONZERO_EXIT")
        if not raw.strip():
            raise CodexInvocationError("CODEX_INVALID_RESPONSE")
        prompt = CodexResearchPromptV1.from_document(request.prompt_metadata["document_path"])
        reconstructed_input = ResearchAgentInputV1(**_input_kwargs(request.research_agent_input))
        if reconstructed_input.input_context_hash != request.input_context_hash:
            raise CodexInvocationError("CODEX_SCHEMA_VIOLATION:input_context_hash")
        parsed = self.parser.parse(raw, input=reconstructed_input, prompt=prompt, response_hash=stable_hash(raw))
        parsed = replace(parsed, model_id=result.model_id if result.model_id != "NONE" else parsed.model_id, provenance={
            **parsed.provenance,
            "runtime_version": result.runtime_version,
            "model_id": result.model_id,
            "agent_call_id": request.agent_call_id,
            "run_id": request.run_id,
            "batch_id": request.batch_id,
            "duration_seconds": result.duration_seconds,
            "usage": dict(result.usage),
        })
        _write_json(staging / "research_proposal_batch.json", parsed.to_dict())
        return parsed

    def replay(self, *, run_id: str, batch_id: str, agent_call_id: str, input_context_hash: str) -> ResearchProposalBatchV1 | None:
        staging = self.artifact_dir(run_id=run_id, batch_id=batch_id, agent_call_id=agent_call_id)
        artifact = staging / "research_proposal_batch.json"
        if not artifact.exists():
            return None
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        if payload.get("input_context_hash") != input_context_hash:
            raise CodexInvocationError("CODEX_REPLAY_INPUT_IDENTITY_CONFLICT")
        return ResearchProposalBatchV1.from_dict(payload)


def _input_kwargs(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload["run_id"], "batch_id": payload.get("batch_id", ""), "objective": payload["objective"],
        "policy_identity": payload["policy_identity"], "no_outcome_context": payload["no_outcome_context"],
        "failure_knowledge_view": payload.get("failure_knowledge_view", {}), "factor_event_catalog": payload.get("factor_event_catalog", {}),
        "candidate_novelty_neighborhood": payload.get("candidate_novelty_neighborhood", {}),
        "family_mechanism_constraints": payload.get("family_mechanism_constraints", {}),
        "complexity_constraints": payload.get("complexity_constraints", {}), "proposal_budget_view": payload.get("proposal_budget_view", {}),
        "input_context_hash": payload["input_context_hash"],
    }


class CodexAgentCallEstimatorV1:
    """Estimate only when the configured runtime has an enforceable output cap."""

    def __init__(self, *, max_output_tokens: int | None = None, token_chars_per_token: int = 4, model_route: str = "CODEX", pricing_available: bool = False):
        self.max_output_tokens = max_output_tokens
        self.token_chars_per_token = max(1, int(token_chars_per_token))
        self.model_route = model_route
        self.pricing_available = bool(pricing_available)

    def estimate(self, prompt_text: str) -> AgentCallEstimateV1:
        if self.max_output_tokens is None:
            return AgentCallEstimateV1(model_route=self.model_route, estimate_status="UNAVAILABLE", estimate_method="CODEX_RUNTIME_NO_ENFORCEABLE_OUTPUT_CAP")
        input_tokens = (len(prompt_text) + self.token_chars_per_token - 1) // self.token_chars_per_token
        total = input_tokens + int(self.max_output_tokens)
        return AgentCallEstimateV1(
            estimated_input_tokens=input_tokens,
            reserved_output_tokens=int(self.max_output_tokens),
            reserved_total_tokens=total,
            estimated_cost_upper_bound=0.0,
            currency="NONE",
            model_route=self.model_route,
            estimate_status="READY",
            estimate_method="CODEX_CONFIGURED_OUTPUT_CAP",
            estimate_method_version="1.0.0",
            token_estimator_id="CHARACTER_BOUND",
            token_estimator_version="1.0.0",
            pricing_source="UNAVAILABLE" if not self.pricing_available else "CONFIGURED",
            pricing_version="NONE" if not self.pricing_available else "1.0.0",
            max_output_tokens=int(self.max_output_tokens),
        )


class CodexResearchAgentBackendV1(ResearchAgentBackendV1):
    backend_type = CODEX_BACKEND_TYPE
    backend_version = CODEX_BACKEND_VERSION

    def __init__(self, *, adapter: CodexInvocationAdapterV1, prompt: CodexResearchPromptV1 | None = None, estimator: CodexAgentCallEstimatorV1 | None = None):
        self.adapter = adapter
        self.prompt = prompt or CodexResearchPromptV1.default(SOURCE_ROOT)
        self.estimator = estimator or CodexAgentCallEstimatorV1()
        self._run_id = ""
        self._batch_id = ""
        self._agent_call_id = ""
        self._reservation: Mapping[str, Any] = {}

    def set_call_identity(self, *, run_id: str, batch_id: str, agent_call_id: str) -> None:
        self._run_id, self._batch_id, self._agent_call_id = str(run_id), str(batch_id), str(agent_call_id)

    def set_resource_governance_reservation(self, reservation: Mapping[str, Any]) -> None:
        self._reservation = dict(reservation)

    def set_timeout_seconds(self, value: int) -> None:
        self.adapter.set_timeout_seconds(value)

    def estimate_call(self, input: ResearchAgentInputV1) -> AgentCallEstimateV1:
        assert_agent_design_time(input.to_dict())
        rendered = self.prompt.render(input.to_dict())
        return self.estimator.estimate(rendered)

    def replay_completed(self, input: ResearchAgentInputV1, *, run_id: str, batch_id: str, agent_call_id: str) -> ResearchProposalBatchV1 | None:
        return self.adapter.replay(run_id=run_id, batch_id=batch_id, agent_call_id=agent_call_id, input_context_hash=input.input_context_hash)

    def generate_proposals(self, input: ResearchAgentInputV1) -> ResearchProposalBatchV1:
        assert_agent_design_time(input.to_dict())
        run_id = self._run_id or input.run_id
        batch_id = self._batch_id or input.batch_id
        agent_call_id = self._agent_call_id or stable_hash({"run_id": run_id, "batch_id": batch_id, "input_hash": input.input_context_hash, "prompt_hash": self.prompt.prompt_hash})
        request = CodexInvocationRequestV1(
            agent_call_id=agent_call_id,
            run_id=run_id,
            batch_id=batch_id,
            objective_id=str(input.objective.get("objective_id", "")),
            policy_identity=input.policy_identity,
            input_context_hash=input.input_context_hash,
            research_agent_input=input.to_dict(),
            prompt=self.prompt.render(input.to_dict()),
            prompt_metadata=self.prompt.to_dict(),
            resource_governance_reservation=self._reservation,
        )
        return self.adapter.invoke(request)


@dataclass(frozen=True)
class ResearchAgentBackendConfigV1:
    research_agent_backend: str = DEFAULT_RESEARCH_AGENT_BACKEND
    codex_research_agent_enabled: bool = CODEX_RESEARCH_AGENT_ENABLED

    def __post_init__(self) -> None:
        if self.research_agent_backend not in {"TEMPLATE", CODEX_BACKEND_TYPE}:
            raise ValueError("research_agent_backend must be TEMPLATE or CODEX")

    @classmethod
    def from_environment(cls) -> "ResearchAgentBackendConfigV1":
        return cls(
            research_agent_backend=os.getenv("RESEARCH_AGENT_BACKEND", DEFAULT_RESEARCH_AGENT_BACKEND).upper(),
            codex_research_agent_enabled=os.getenv("CODEX_RESEARCH_AGENT_ENABLED", "false").casefold() == "true",
        )

    def assert_allowed(self) -> None:
        if self.research_agent_backend == CODEX_BACKEND_TYPE and not self.codex_research_agent_enabled:
            raise CodexInvocationError("CODEX_RESEARCH_AGENT_DISABLED")


def discover_codex_executable() -> str | None:
    configured = os.getenv("CODEX_EXECUTABLE")
    if configured and Path(configured).exists():
        return configured
    configured_cli = os.getenv("CODEX_CLI_PATH")
    if configured_cli and Path(configured_cli).exists():
        return configured_cli
    found_exe = shutil.which("codex.exe")
    if found_exe:
        return found_exe
    for name in ("codex.ps1",):
        found = shutil.which(name)
        if found:
            return found
    command_wrapper = shutil.which("codex.cmd")
    if command_wrapper:
        wrapper_path = Path(command_wrapper)
        bundled = wrapper_path.parent / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        if bundled.exists():
            return str(bundled)
    return None


def audit_codex_runtime(executable: str | Path | None = None) -> dict[str, Any]:
    """Collect capability facts without invoking a model."""
    path = str(executable or discover_codex_executable() or "")
    report: dict[str, Any] = {
        "schema_version": "codex-runtime-capability-audit-v1",
        "audited_at": now_timestamp(),
        "runtime_detected": bool(path),
        "executable": path or None,
        "version": None,
        "non_interactive_capability": False,
        "stdin_support": False,
        "structured_output_capability": {"jsonl_events": False, "output_schema": False, "output_last_message": False},
        "timeout_behavior": {"finite_timeout_enforced_by_adapter": True, "process_cleanup": "targeted-child-process-termination"},
        "exit_code_semantics": "nonzero mapped to engineering error",
        "authentication_dependency": "Codex CLI stored authentication or configured provider is required",
        "sandbox_workdir_behavior": {"adapter_flags": ["--sandbox read-only", "--cd isolated-staging", "--skip-git-repo-check"], "source_tree_exposed": False},
        "model_invocation_performed": False,
    }
    if not path:
        return report
    try:
        version = subprocess.run(_launcher(path) + ["--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        report["version"] = (version.stdout or version.stderr).strip().splitlines()[0] if (version.stdout or version.stderr).strip() else None
        help_result = subprocess.run(_launcher(path) + ["exec", "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        help_text = (help_result.stdout or "") + "\n" + (help_result.stderr or "")
        report["non_interactive_capability"] = "Run Codex non-interactively" in help_text
        report["stdin_support"] = "read from stdin" in help_text
        report["structured_output_capability"] = {
            "jsonl_events": "--json" in help_text,
            "output_schema": "--output-schema" in help_text,
            "output_last_message": "--output-last-message" in help_text,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        report["audit_error"] = type(exc).__name__
    return report


def _proposal_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "run_id", "batch_id", "proposals", "backend_type", "backend_version", "prompt_template_version", "input_context_hash", "provenance"],
        "properties": {
            "schema_version": {"const": "research-proposal-batch-v1"},
            "run_id": {"type": "string"}, "batch_id": {"type": "string"},
            "backend_type": {"const": "CODEX"}, "backend_version": {"type": "string"},
            "proposal_schema_version": {"const": "research-proposal-v1"},
            "prompt_template_version": {"type": "string"}, "model_id": {"type": "string"},
            "input_context_hash": {"type": "string"}, "generated_at": {"type": "string"},
            "proposal_batch_hash": {"type": "string"}, "empty_reason": {"type": "string"},
            "provenance": {"type": "object"},
            "proposals": {"type": "array", "items": {"$ref": "#/$defs/proposal"}},
        },
        "$defs": {"proposal": {"type": "object", "additionalProperties": False, "required": sorted(_PROPOSAL_FIELDS), "properties": {field: {} for field in sorted(_PROPOSAL_FIELDS)}}},
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m chanlun_trader.research_factory.codex_backend <audit-output.json>")
    _write_json(Path(sys.argv[1]), audit_codex_runtime())
