"""Canonical scope and AI design policy helpers for promising follow-up work.

The scope manifest is a reconciliation layer over immutable candidate
contracts.  It never changes candidate identity or deletes historical AI
artifacts; it only tells readers which contracts belong to the current
follow-up queue.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


PROMISING_FOLLOWUP_OBJECTIVE_ID = "RESEARCH_OBJECTIVE_GOVERNED_PROMISING_FOLLOWUP_V1_97AA9C76F4453762D2BF"
DESIGN_POLICY_FILENAME = "research_ai_design_policy.json"
SCOPE_FILENAME = f"{PROMISING_FOLLOWUP_OBJECTIVE_ID}.json"


def _read_json(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def design_policy_path(root: str | Path) -> Path:
    return Path(root).resolve() / "config" / DESIGN_POLICY_FILENAME


def load_design_policy(root: str | Path) -> Mapping[str, Any]:
    return _read_json(design_policy_path(root)) or {}


def scope_path(root: str | Path, objective_id: str) -> Path:
    return Path(root).resolve() / "data" / "research" / "research_factory" / "scope" / f"{objective_id}.json"


def load_scope_manifest(root: str | Path, objective_id: str) -> Mapping[str, Any]:
    payload = _read_json(scope_path(root, objective_id)) or {}
    if str(payload.get("objective_id") or "") != str(objective_id):
        return {}
    return payload


def candidate_scope_info(root: str | Path, objective_id: str, candidate_id: str) -> dict[str, Any]:
    manifest = load_scope_manifest(root, objective_id)
    historical = {str(item) for item in manifest.get("out_of_scope_candidate_ids", ())}
    parents = {str(item.get("candidate_id")) for item in manifest.get("parent_candidate_refs", ()) if isinstance(item, Mapping) and item.get("candidate_id")}
    if str(candidate_id) in historical:
        return {
            "scope_status": "OUT_OF_SCOPE_HISTORICAL_AI",
            "scope_status_zh": "历史 AI 记录 · 不属于当前 Follow-up",
            "scope_reason_zh": "该 Candidate 来自历史 AI 交接批次，保留身份与记录，但不进入当前 Follow-up 验证队列。",
            "is_current_followup": False,
        }
    if str(candidate_id) in parents:
        return {
            "scope_status": "PARENT_PROMISING_REFERENCE",
            "scope_status_zh": "父级有潜力策略引用",
            "scope_reason_zh": "该 Candidate 是当前 Follow-up 的父级引用，不作为本轮新验证 Candidate 重复登记。",
            "is_current_followup": False,
        }
    return {
        "scope_status": "CURRENT_FOLLOWUP_CONFIRMATION",
        "scope_status_zh": "当前 Follow-up 确认候选",
        "scope_reason_zh": "该 Candidate 未命中历史 AI 排除清单，可在当前 Follow-up 的固定范围内继续校验。",
        "is_current_followup": True,
    }


def is_one_shot_followup(root: str | Path, objective_id: str) -> bool:
    policy = load_design_policy(root)
    overrides = policy.get("objective_overrides") if isinstance(policy.get("objective_overrides"), Mapping) else {}
    explicit = str(overrides.get(objective_id) or "")
    if explicit:
        return explicit == "ONE_SHOT"
    objective = _read_json(
        Path(root).resolve()
        / "data"
        / "research"
        / "research_factory"
        / "objectives"
        / f"{objective_id}.json"
    ) or {}
    objective_type = (
        "PROMISING_FOLLOWUP"
        if str(objective.get("governance_action") or "") == "START_PROMISING_FOLLOWUP_OBJECTIVE"
        else ""
    )
    policies = policy.get("policies") if isinstance(policy.get("policies"), Mapping) else {}
    return str(policies.get(objective_type) or "") == "ONE_SHOT"


__all__ = [
    "DESIGN_POLICY_FILENAME",
    "PROMISING_FOLLOWUP_OBJECTIVE_ID",
    "SCOPE_FILENAME",
    "candidate_scope_info",
    "design_policy_path",
    "is_one_shot_followup",
    "load_design_policy",
    "load_scope_manifest",
    "scope_path",
]
