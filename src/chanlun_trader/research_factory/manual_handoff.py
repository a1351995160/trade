"""Controlled manual AI handoff staging and result watching.

The staging directory is intentionally objective-independent and keyed only by
the generated handoff identity.  A human may copy the prompt to an external
AI task and place one result file back in that directory; the orchestrator
remains the only component allowed to validate or ingest it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from .context import OutcomeBlindFieldPolicyV1
from .durability import DURABLE_INTERACTION_SEMANTICS_FIELDS


HANDOFF_ID_RE = re.compile(r"^HANDOFF_V2_[A-Za-z0-9._:-]{1,128}$")
INVOCATION_ID_RE = re.compile(r"^AI_[A-Za-z0-9._:-]{1,128}$")
RESULT_FILENAME = "AI_RESEARCH_BATCH_RESULT_V2.json"
STAGING_ROOT_NAME = "research_ai_staging"
BATCH_SCHEMA_FILENAME = "AI_RESEARCH_BATCH_RESULT_V2.schema.json"
DURABLE_CONTRACT_SCHEMA_FILENAME = "DURABLE_FROZEN_CANDIDATE_CONTRACT_V1.schema.json"
CONTRACT_HASH_HELPER_FILENAME = "codex_contract_hash_helper.py"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str, pattern: re.Pattern[str], label: str) -> str:
    normalized = str(value)
    if not pattern.fullmatch(normalized):
        raise ValueError(f"{label} 不合法")
    return normalized


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _contract_safety_appendix() -> str:
    return "\n".join((
        "",
        "## 结果合同安全检查",
        "递归按 OUTPUT_CONTRACT.json 的 no_outcome_forbidden_fields 检查；禁止使用任何列出的字段名，包括把 order 作为说明性字段。",
        f"interaction_semantics 只能包含这两个字段：{', '.join(DURABLE_INTERACTION_SEMANTICS_FIELDS)}；不得添加其他键。",
        "官方合同哈希工具会回填 candidate fingerprint/preregistration_hash、semantic_fingerprint、candidate_hash 和 content_hash；不要手算。",
        "Schema、提示词和草稿冲突时，以 OUTPUT_CONTRACT.json、冻结合同 Schema 和本地编排器规则为准；修正后再写结果。",
    ))


class ManualAIHandoffWriterV1:
    """Write the small, user-facing handoff bundle for one task."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def task_dir(self, handoff_id: str) -> Path:
        handoff_id = _safe_id(handoff_id, HANDOFF_ID_RE, "handoff_id")
        return self.root / STAGING_ROOT_NAME / handoff_id

    def result_path(self, handoff_id: str) -> Path:
        return self.task_dir(handoff_id) / RESULT_FILENAME

    def prepare(
        self,
        handoff: Mapping[str, Any],
        *,
        invocation_id: str,
        prompt: str,
        batch_schema: Mapping[str, Any],
        durable_contract_schema: Mapping[str, Any],
        contract_hash_helper: str,
    ) -> dict[str, Any]:
        handoff_id = _safe_id(str(handoff.get("handoff_id") or ""), HANDOFF_ID_RE, "handoff_id")
        invocation_id = _safe_id(invocation_id, INVOCATION_ID_RE, "ai_invocation_id")
        task_dir = self.task_dir(handoff_id)
        result_path = task_dir / RESULT_FILENAME
        relative_dir = task_dir.relative_to(self.root).as_posix()
        relative_result = result_path.relative_to(self.root).as_posix()
        created_at = _timestamp()
        ready_path = task_dir / "HANDOFF_READY.json"
        if ready_path.exists():
            try:
                existing_ready = json.loads(ready_path.read_text(encoding="utf-8"))
                if isinstance(existing_ready, Mapping) and existing_ready.get("created_at"):
                    created_at = str(existing_ready["created_at"])
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        output_contract = handoff.get("required_output_contract") if isinstance(handoff.get("required_output_contract"), Mapping) else {}
        source_context_id = str(handoff.get("source_context_id") or "") or None
        source_context_hash = str(handoff.get("source_context_hash") or "") or None
        source_context_version = str(handoff.get("source_context_version") or "") or None
        compact_context = {
            "schema_version": "manual-ai-no-outcome-context-v1",
            "handoff_id": handoff_id,
            "objective_id": str(handoff.get("objective_id") or ""),
            "performance_values_exposed": False,
            "source_context_id": source_context_id,
            "source_context_hash": source_context_hash,
            "source_context_version": source_context_version,
            "safe_runtime_context_identity": dict(handoff.get("safe_runtime_context_identity") or {}) if isinstance(handoff.get("safe_runtime_context_identity"), Mapping) else None,
            "source_hashes": dict(handoff.get("source_hashes") or {}),
            "context_refs": {
                "full_safe_handoff": f"{relative_dir}/RESEARCH_ORCHESTRATOR_AI_HANDOFF.json",
                "output_contract": f"{relative_dir}/OUTPUT_CONTRACT.json",
                "batch_result_schema": f"{relative_dir}/{BATCH_SCHEMA_FILENAME}",
                "durable_contract_schema": f"{relative_dir}/{DURABLE_CONTRACT_SCHEMA_FILENAME}",
                "contract_hash_helper": f"{relative_dir}/{CONTRACT_HASH_HELPER_FILENAME}",
            },
            "allowed_action": list(handoff.get("allowed_action") or ()),
            "forbidden_actions": list(handoff.get("forbidden_actions") or ()),
            "remaining_budget": dict(handoff.get("remaining_budget") or {}),
            "task_purpose": str(handoff.get("task_purpose") or "PROMISING_FOLLOWUP 机制确认设计"),
            "current_round": str(handoff.get("current_round") or "PROMISING_FOLLOWUP"),
            "ai_design_policy": dict(handoff.get("ai_design_policy") or {}),
            "parent_candidate_refs": [dict(item) for item in handoff.get("parent_candidate_refs", ()) if isinstance(item, Mapping)],
            "historical_ai_candidate_count": int(handoff.get("historical_ai_candidate_count", 0) or 0),
        }
        instructions = "\n".join((
            "# AI 研究手动交接任务",
            "",
            "这是一个仅用于研究设计的人工交接任务。请把下面的提示词完整复制到 AI 研究员任务中。",
            "AI 研究员只负责设计合法、去重且未访问绩效的候选策略；不得运行数据、预测试验、回测或真实交易。",
            "完成后，只把一个 JSON 对象保存为 `AI_RESEARCH_BATCH_RESULT_V2.json`，放回本目录；不要修改其他 canonical 研究文件。",
            "",
            f"- 交接编号：`{handoff_id}`",
            f"- 目标编号：`{handoff.get('objective_id')}`",
            f"- 调用编号：`{invocation_id}`",
            f"- 结果文件：`{RESULT_FILENAME}`",
            f"- 结果目录：`{relative_dir}`",
            f"- 任务生成时间：`{created_at}`",
            f"- 任务目的：`{handoff.get('task_purpose') or 'PROMISING_FOLLOWUP 机制确认设计'}`",
            f"- 当前研究轮次：`{handoff.get('current_round') or 'PROMISING_FOLLOWUP'}`",
            f"- 批次结果 Schema：`{BATCH_SCHEMA_FILENAME}`",
            f"- 冻结合同 Schema：`{DURABLE_CONTRACT_SCHEMA_FILENAME}`",
            f"- 合同哈希工具：`{CONTRACT_HASH_HELPER_FILENAME}`",
            "",
            "## 允许的人工动作",
            "复制提示词、读取本目录中的任务资料、生成一个合法候选批次结果文件。",
            "",
            "## 明确禁止",
            "不要修改候选、试验、预算、治理、最终测试、前瞻验证或真实订单记录；不要把绩效数值写回结果。",
            "",
            "## 验收方式",
            "先用任务目录中的官方哈希工具处理完整合同草稿，再保存结果文件；工具会统一回填候选、语义和合同哈希。本地编排器会自动发现结果文件，复用现有身份、时点一致性、去重、候选合同和预算闸门；失败结果不会被接入，也不会消耗预测试验预算。",
        ))
        effective_prompt = prompt.rstrip() + _contract_safety_appendix() + "\n"
        _atomic_text(task_dir / "AI任务说明.md", instructions)
        _atomic_text(task_dir / "AI研究提示词.md", effective_prompt)
        _atomic_json(task_dir / BATCH_SCHEMA_FILENAME, batch_schema)
        _atomic_json(task_dir / DURABLE_CONTRACT_SCHEMA_FILENAME, durable_contract_schema)
        _atomic_text(task_dir / CONTRACT_HASH_HELPER_FILENAME, contract_hash_helper.rstrip() + "\n")
        _atomic_json(task_dir / "NOOUTCOME_CONTEXT.json", compact_context)
        _atomic_json(task_dir / "OUTPUT_CONTRACT.json", {
            "schema_version": "manual-ai-output-contract-v1",
            "handoff_id": handoff_id,
            "objective_id": str(handoff.get("objective_id") or ""),
            "ai_invocation_id": invocation_id,
            "expected_filename": RESULT_FILENAME,
            "required_output_contract": dict(output_contract),
            "schema_refs": {
                "batch_result": BATCH_SCHEMA_FILENAME,
                "durable_contract": DURABLE_CONTRACT_SCHEMA_FILENAME,
            },
            "contract_hash_helper": CONTRACT_HASH_HELPER_FILENAME,
            "candidate_hashes_example": {"CANDIDATE_ID": "CANDIDATE_HASH"},
            "no_outcome_forbidden_fields": sorted(OutcomeBlindFieldPolicyV1.FORBIDDEN_FIELDS),
            "durable_contract_rules": {
                "interaction_semantics_allowed_fields": list(DURABLE_INTERACTION_SEMANTICS_FIELDS),
                "interaction_semantics_additional_properties": False,
                "contract_schema_version": "durable-frozen-candidate-contract-v1",
            },
            "hash_helper_behavior": {
                "candidate_hash_source": "full_semantic_record.candidate canonical StrategyCandidateSpec core",
                "semantic_fingerprint_source": "full_semantic_record.signal_predicate plus exit_predicate",
                "content_hash_source": "entire durable contract with content_hash removed",
            },
            "validation_owner": "LOCAL_ORCHESTRATOR",
            "performance_values_allowed": False,
            "source_context_id": source_context_id,
            "source_context_hash": source_context_hash,
            "source_context_version": source_context_version,
        })
        _atomic_json(task_dir / "RESEARCH_ORCHESTRATOR_AI_HANDOFF.json", dict(handoff))
        _atomic_json(task_dir / "HANDOFF_READY.json", {
            "schema_version": "manual-ai-handoff-ready-v1",
            "handoff_id": handoff_id,
            "objective_id": str(handoff.get("objective_id") or ""),
            "ai_invocation_id": invocation_id,
            "task_dir": relative_dir,
            "result_path": relative_result,
            "created_at": created_at,
            "prompt_character_count": len(effective_prompt),
            "prompt_token_estimate": max(1, (len(effective_prompt) + 3) // 4),
            "context_mode": "REFERENCES_ONLY",
            "background_ai_token_consumption": 0,
            "manual_handoff_id": handoff_id,
            "task_purpose": str(handoff.get("task_purpose") or "PROMISING_FOLLOWUP 机制确认设计"),
            "current_round": str(handoff.get("current_round") or "PROMISING_FOLLOWUP"),
            "ai_design_policy": dict(handoff.get("ai_design_policy") or {}),
            "source_context_id": source_context_id,
            "source_context_hash": source_context_hash,
            "source_context_version": source_context_version,
            "safe_runtime_context_identity": dict(handoff.get("safe_runtime_context_identity") or {}) if isinstance(handoff.get("safe_runtime_context_identity"), Mapping) else None,
            "planned_confirmation_candidate_limit": int(handoff.get("planned_confirmation_candidate_limit", 0) or 0),
            "bundle_files": [
                "RESEARCH_ORCHESTRATOR_AI_HANDOFF.json",
                "NOOUTCOME_CONTEXT.json",
                "OUTPUT_CONTRACT.json",
                BATCH_SCHEMA_FILENAME,
                DURABLE_CONTRACT_SCHEMA_FILENAME,
                CONTRACT_HASH_HELPER_FILENAME,
            ],
        })
        return {
            "task_dir": relative_dir,
            "result_path": relative_result,
            "expected_filename": RESULT_FILENAME,
            "created_at": created_at,
            "prompt_character_count": len(effective_prompt),
            "prompt_token_estimate": max(1, (len(effective_prompt) + 3) // 4),
            "context_mode": "REFERENCES_ONLY",
            "background_ai_token_consumption": 0,
            "manual_handoff_id": handoff_id,
            "task_purpose": str(handoff.get("task_purpose") or "PROMISING_FOLLOWUP 机制确认设计"),
            "current_round": str(handoff.get("current_round") or "PROMISING_FOLLOWUP"),
            "ai_design_policy": dict(handoff.get("ai_design_policy") or {}),
            "planned_confirmation_candidate_limit": int(handoff.get("planned_confirmation_candidate_limit", 0) or 0),
            "source_context_id": source_context_id,
            "source_context_hash": source_context_hash,
            "source_context_version": source_context_version,
        }


class ManualAIResultWatcherV1:
    """Safely inspect one expected result file; never accepts arbitrary paths."""

    def __init__(self, root: str | Path):
        self.writer = ManualAIHandoffWriterV1(root)

    def inspect(self, handoff_id: str) -> dict[str, Any]:
        result_path = self.writer.result_path(handoff_id)
        task_dir = result_path.parent
        ready_path = task_dir / "HANDOFF_READY.json"
        created_at = None
        if ready_path.exists():
            try:
                ready = json.loads(ready_path.read_text(encoding="utf-8"))
                created_at = ready.get("created_at") if isinstance(ready, Mapping) else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                return {"status": "INVALID_TASK", "reason_code": "MANUAL_TASK_METADATA_UNREADABLE", "reason_zh": "手动 AI 任务资料暂时不可读。", "result_path": result_path.relative_to(self.writer.root).as_posix()}
        relative_result = result_path.relative_to(self.writer.root).as_posix()
        if not result_path.exists():
            return {"status": "WAITING", "reason_code": "MANUAL_RESULT_NOT_FOUND", "reason_zh": "尚未发现 AI 研究结果文件。", "result_path": relative_result, "task_created_at": created_at}
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"status": "INVALID_JSON", "reason_code": "MANUAL_RESULT_JSON_INVALID", "reason_zh": "结果文件不是可读取的 JSON，未接入研究。", "result_path": relative_result, "task_created_at": created_at}
        if not isinstance(payload, Mapping):
            return {"status": "INVALID_JSON", "reason_code": "MANUAL_RESULT_OBJECT_REQUIRED", "reason_zh": "结果文件必须是一个 JSON 对象，未接入研究。", "result_path": relative_result, "task_created_at": created_at}
        return {"status": "FOUND", "reason_code": "MANUAL_RESULT_FOUND", "reason_zh": "已发现结果文件，等待本地编排器校验。", "result_path": relative_result, "task_created_at": created_at, "payload": dict(payload)}


__all__ = [
    "BATCH_SCHEMA_FILENAME",
    "CONTRACT_HASH_HELPER_FILENAME",
    "DURABLE_CONTRACT_SCHEMA_FILENAME",
    "HANDOFF_ID_RE",
    "INVOCATION_ID_RE",
    "RESULT_FILENAME",
    "STAGING_ROOT_NAME",
    "ManualAIHandoffWriterV1",
    "ManualAIResultWatcherV1",
]
