"""Read-only failure analysis for the Research Evolution Manager V1.

The manager consumes an already completed validation boundary and writes a
separate analysis artifact.  It never changes candidate contracts, trial
ledgers, budgets, multiple-testing records, or execution state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..presentation import ZhCNPresentation, write_human_report
from .common import now_timestamp, stable_hash
from .context import PerformanceBlindGuard


FAILURE_CATEGORIES = (
    "STATISTICAL_FAILURE",
    "RETURN_FAILURE",
    "RISK_FAILURE",
    "SAMPLE_FAILURE",
    "ENGINEERING_FAILURE",
    "OVERFITTING_RISK",
)

_FAILURE_CATEGORY_EXPLANATIONS = {
    "STATISTICAL_FAILURE": "统计证据不足，不能把观察到的差异当作稳定支持。",
    "RETURN_FAILURE": "基础研究目标未形成满足冻结规则的收益支持。",
    "RISK_FAILURE": "在风险或成本压力条件下，研究结论不够稳健。",
    "SAMPLE_FAILURE": "可用于安全判断的样本或独立机会不足。",
    "ENGINEERING_FAILURE": "数据、时点一致性或执行语义存在工程性阻断。",
    "OVERFITTING_RISK": "多重检验、集中度或分段稳定性提示过拟合风险。",
}

_CATEGORY_CONSTRAINTS = {
    "STATISTICAL_FAILURE": "下一轮先冻结统计检验、支持阈值和假设族边界，不重复同一统计条件。",
    "RETURN_FAILURE": "下一轮需要提出不同的机制假设，不能只对当前候选做参数微调。",
    "RISK_FAILURE": "下一轮先设计成本与风险压力下仍可解释的机制，并保留独立压力验证。",
    "SAMPLE_FAILURE": "下一轮先做样本可行性与独立机会预检，再决定是否进入预测验证。",
    "ENGINEERING_FAILURE": "下一轮必须先完成数据、PIT 和执行语义的工程预检。",
    "OVERFITTING_RISK": "下一轮应减少对已探索机制和假设族的重复搜索，扩大独立研究方向。",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_TRIAL_SPECIFIC_FILES = {"validation_results.json", "final_status.json", "multiple_testing.json"}


class ResearchEvolutionError(RuntimeError):
    """Raised when a completed research boundary cannot be reconciled safely."""

    def __init__(self, code: str, message_zh: str):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"TRUE", "PASS", "PASSED", "VALID", "OK", "SUPPORTED", "YES"}:
            return True
        if normalized in {"FALSE", "FAIL", "FAILED", "INVALID", "NO", "UNSUPPORTED"}:
            return False
    return None


def _canonical(value: Any) -> str:
    return str(value or "").strip().upper()


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _walk(item: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, item
    if isinstance(item, Mapping):
        for key, nested in item.items():
            yield from _walk(nested, path + (str(key),))
    elif isinstance(item, (list, tuple)):
        for index, nested in enumerate(item):
            yield from _walk(nested, path + (str(index),))


def _values_for_keys(payload: Any, keys: set[str]) -> list[tuple[tuple[str, ...], Any]]:
    wanted = {key.casefold() for key in keys}
    return [(path, value) for path, value in _walk(payload) if path and path[-1].casefold() in wanted]


def _first_value(payload: Any, keys: set[str]) -> Any:
    values = _values_for_keys(payload, keys)
    return values[0][1] if values else None


def _select_candidate_value(value: Any, candidate_id: str | None = None) -> Any:
    if not isinstance(value, Mapping):
        return value
    if candidate_id and candidate_id in value:
        return value[candidate_id]
    if len(value) == 1:
        return next(iter(value.values()))
    return None


def _value_at(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _section(payload: Any, name: str) -> Any:
    direct = _value_at(payload, name)
    if direct is not None:
        return direct
    wanted = name.casefold()
    for path, value in _walk(payload):
        if path and path[-1].casefold() == wanted:
            return value
    return None


def _status_is_failure(value: Any) -> bool:
    return _canonical(value) in {"FAIL", "FAILED", "BLOCKED", "REJECTED", "INVALID", "UNSTABLE", "ERROR"}


def _is_failure_terminal(final_status: Mapping[str, Any] | None) -> bool:
    if not final_status:
        return True
    values = [final_status.get(key) for key in ("status", "classification", "effective_classification")]
    return any(_status_is_failure(value) for value in values if value is not None)


def _failed_gates(payload: Any) -> set[str]:
    failed: set[str] = set()
    for path, value in _walk(payload):
        if not path:
            continue
        key = path[-1].casefold()
        if key in {"failed_hard_gates", "failed_gates", "failed_gate_ids", "failure_gates"} and isinstance(value, (list, tuple)):
            failed.update(str(item) for item in value if item)
        if key in {"gates", "gate_results"} and isinstance(value, Mapping):
            for gate_name, gate_value in value.items():
                passed = _bool(gate_value)
                if passed is False:
                    failed.add(str(gate_name))
                elif isinstance(gate_value, Mapping):
                    state = gate_value.get("passed", gate_value.get("valid", gate_value.get("status")))
                    if _bool(state) is False or _status_is_failure(state):
                        failed.add(str(gate_name))
        if key in {"passed", "valid", "supported"} and _bool(value) is False and len(path) >= 2:
            failed.add(str(path[-2]))
    return {item for item in failed if item}


def _finding(
    category: str,
    reason_code: str,
    evidence_id: str,
    explanation_zh: str,
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "category": category,
        "category_zh": ZhCNPresentation.term(category),
        "reason_code": reason_code,
        "evidence_id": evidence_id,
        "explanation_zh": explanation_zh,
        "observed": _json_value(observed),
        "next_research_constraint_zh": _CATEGORY_CONSTRAINTS[category],
    }


def classify_failures(
    validation_result: Mapping[str, Any],
    *,
    final_status: Mapping[str, Any] | None = None,
    multiple_testing: Mapping[str, Any] | None = None,
    candidate_id: str | None = None,
) -> list[dict[str, Any]]:
    """Classify explicit evidence into the six V1 failure categories.

    This function is deliberately pure.  It accepts in-memory payloads so the
    classifier can be tested without touching canonical artifacts.
    """
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, reason: str, evidence: str, explanation: str, observed: Mapping[str, Any]) -> None:
        key = (category, reason)
        if key not in seen:
            seen.add(key)
            findings.append(_finding(category, reason, evidence, explanation, observed))

    failed_gates = {gate.casefold() for gate in _failed_gates(validation_result)}
    failed_gates.update(gate.casefold() for gate in _failed_gates(final_status or {}))

    candidate_id = candidate_id or str(_first_value(validation_result, {"candidate_id"}) or "") or None
    bootstrap = _section(validation_result, "bootstrap")
    bootstrap_p = _number(_first_value(bootstrap, {"p_value", "pvalue", "bootstrap_p_value", "raw_p_value"}))
    bootstrap_support = _first_value(bootstrap, {"supported", "support", "raw_support", "significant"})
    if bootstrap_p is not None and bootstrap_p > 0.05:
        add("STATISTICAL_FAILURE", "BOOTSTRAP_SUPPORT_INSUFFICIENT", "bootstrap", "原始 Bootstrap 支持不足，统计证据没有达到预设支持门槛。", {"p_value": bootstrap_p, "threshold": 0.05})
    if _bool(bootstrap_support) is False or any("bootstrap" in gate and "support" in gate for gate in failed_gates):
        add("STATISTICAL_FAILURE", "BOOTSTRAP_SUPPORT_INSUFFICIENT", "raw_bootstrap_support", "原始 Bootstrap 支持不足，统计证据没有达到预设支持门槛。", {"supported": False})

    mt = multiple_testing or _value_at(validation_result, "multiple_testing")
    adjusted_p = _number(_select_candidate_value(_value_at(mt, "adjusted_p_values"), candidate_id))
    adjusted_p = adjusted_p if adjusted_p is not None else _number(_first_value(mt, {"adjusted_p", "adjusted_p_value"}))
    q_value = _number(_first_value(mt, {"q", "alpha", "threshold"})) or 0.05
    adjusted_support = _select_candidate_value(_value_at(mt, "adjusted_support"), candidate_id)
    adjusted_support = adjusted_support if adjusted_support is not None else _first_value(mt, {"supported_after_adjustment", "support_after_adjustment"})
    if adjusted_p is not None and adjusted_p > q_value:
        add("STATISTICAL_FAILURE", "ADJUSTED_SUPPORT_INSUFFICIENT", "multiple_testing", "多重检验调整后的统计支持不足。", {"adjusted_p": adjusted_p, "q": q_value})
    if _bool(adjusted_support) is False:
        add("STATISTICAL_FAILURE", "ADJUSTED_SUPPORT_INSUFFICIENT", "multiple_testing", "多重检验调整后的统计支持不足。", {"adjusted_support": False, "q": q_value})
        add("OVERFITTING_RISK", "MULTIPLE_TESTING_SUPPORT_LOST", "multiple_testing", "假设族校正后支持消失，重复搜索同一研究空间存在过拟合风险。", {"adjusted_support": False, "q": q_value})

    base_metrics = _section(validation_result, "base_metrics")
    net_return = _number(_first_value(base_metrics, {"net_return", "strategy_return", "portfolio_return"}))
    profit_factor = _number(_first_value(base_metrics, {"profit_factor", "pf"}))
    if net_return is not None and net_return <= 0:
        add("RETURN_FAILURE", "BASE_RETURN_NONPOSITIVE", "local_base_return", "基础研究结果没有形成正的净收益支持。", {"net_return": net_return})
    if profit_factor is not None and profit_factor < 1:
        add("RETURN_FAILURE", "PROFIT_FACTOR_UNSUPPORTED", "local_profit_factor", "基础盈利因子低于可接受的平衡线。", {"profit_factor": profit_factor, "threshold": 1})
    if any("base_return" in gate or "profit_factor" in gate or "excess_return" in gate for gate in failed_gates):
        add("RETURN_FAILURE", "RETURN_GATE_FAILED", "return_gates", "收益门槛未通过，当前机制不足以支持进入下一阶段。", {"failed_gates": sorted(failed_gates)})

    cost_stress = _section(validation_result, "cost_stress")
    negative_scenarios: dict[str, float] = {}
    if isinstance(cost_stress, Mapping):
        for scenario, scenario_value in cost_stress.items():
            candidate_return = _number(_first_value(scenario_value, {"net_return", "strategy_return", "portfolio_return"})) if isinstance(scenario_value, Mapping) else _number(scenario_value)
            if candidate_return is not None and candidate_return < 0:
                negative_scenarios[str(scenario)] = candidate_return
    if negative_scenarios or any("cost_stress" in gate or "fee" in gate or "slippage" in gate for gate in failed_gates):
        add("RISK_FAILURE", "COST_STRESS_FAILURE", "cost_stress", "成本压力测试未保持可接受的稳健性。", {"negative_scenarios": negative_scenarios, "failed_gates": sorted(gate for gate in failed_gates if "cost" in gate or "fee" in gate or "slippage" in gate)})

    sample_payload = _section(validation_result, "sample_feasibility") or _section(validation_result, "sample") or validation_result
    minimum = _number(_first_value(sample_payload, {"minimum_required", "minimum_sample_count", "min_sample_count", "minimum_event_count"}))
    for key in ("closed_trade_count", "sample_count", "event_count", "independent_opportunities", "qualified_observations"):
        observed_count = _number(_first_value(sample_payload, {key}))
        if observed_count is not None and minimum is not None and observed_count < minimum:
            add("SAMPLE_FAILURE", "INSUFFICIENT_INDEPENDENT_SAMPLE", "sample_feasibility", "样本或独立机会低于冻结的最低要求。", {"observed": observed_count, "minimum_required": minimum, "field": key})
            break
    if any("sample" in gate or "opportunit" in gate for gate in failed_gates):
        add("SAMPLE_FAILURE", "SAMPLE_GATE_FAILED", "sample_gates", "样本可行性门槛未通过。", {"failed_gates": sorted(gate for gate in failed_gates if "sample" in gate or "opportunit" in gate)})

    engine = _section(validation_result, "engine_integrity")
    engine_invalid = False
    if isinstance(engine, Mapping):
        for key in ("valid", "passed", "certified"):
            if _bool(engine.get(key)) is False:
                engine_invalid = True
        certification = _canonical(engine.get("certification_status"))
        if certification in {"INVALID", "FAIL", "FAILED", "BLOCKED", "ERROR"}:
            engine_invalid = True
        errors = engine.get("errors") or engine.get("invariant_errors")
        if isinstance(errors, (list, tuple, Mapping)) and len(errors) > 0:
            engine_invalid = True
    top_errors = _first_value(validation_result, {"errors", "error", "error_code", "exception"})
    if top_errors not in (None, "", [], {}, False):
        engine_invalid = True
    engineering_gate_tokens = ("engine", "data_valid", "pit_valid", "microstructure", "execution_sim", "execution_feasibility")
    engineering_gates = sorted(gate for gate in failed_gates if any(token in gate for token in engineering_gate_tokens))
    if engine_invalid or engineering_gates:
        add("ENGINEERING_FAILURE", "ENGINE_INTEGRITY_INVALID", "engine_integrity", "数据、PIT 或执行语义的完整性检查未通过。", {"failed_gates": engineering_gates, "integrity_invalid": engine_invalid})

    for section_name, reason_code, evidence_id in (
        ("concentration", "CONCENTRATION_INSTABILITY", "concentration"),
        ("regime", "REGIME_INSTABILITY", "regime"),
        ("regime_stability", "REGIME_INSTABILITY", "regime_stability"),
    ):
        section = _section(validation_result, section_name)
        if not isinstance(section, Mapping):
            continue
        warning = _bool(section.get("warning"))
        passed = _bool(section.get("passed", section.get("stable")))
        state = _canonical(section.get("status", section.get("state")))
        if warning is True or passed is False or state in {"FAIL", "FAILED", "UNSTABLE", "INVALID"}:
            add("OVERFITTING_RISK", reason_code, evidence_id, "分布集中或分段稳定性不足，重复当前搜索方向存在过拟合风险。", {"status": state or None, "warning": warning, "passed": passed})
            break

    if not findings and _is_failure_terminal(final_status):
        add("ENGINEERING_FAILURE", "FAILURE_EVIDENCE_UNAVAILABLE", "terminal_status", "研究已进入失败终态，但当前输入没有足够的可分类证据，需人工复核。", {"terminal_status": (final_status or {}).get("status") or (final_status or {}).get("classification")})

    order = {category: index for index, category in enumerate(FAILURE_CATEGORIES)}
    return sorted(findings, key=lambda item: (order.get(str(item["category"]), 99), str(item["reason_code"])))


def _contains_identity(payload: Any, identities: tuple[str, ...]) -> bool:
    serialized = json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, default=str)
    return all(identity in serialized for identity in identities if identity)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchEvolutionError("SOURCE_UNREADABLE", f"研究演进输入文件暂时不可读：{path.name}") from exc


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


class ResearchEvolutionManager:
    """Generate explicit, idempotent failure-analysis artifacts."""

    report_filename = "research_evolution_report.json"
    human_report_filename = "research_evolution_report.md"
    context_filename = "AI_RESEARCH_EVOLUTION_CONTEXT.json"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @staticmethod
    def classify_failures(
        validation_result: Mapping[str, Any],
        *,
        final_status: Mapping[str, Any] | None = None,
        multiple_testing: Mapping[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return classify_failures(validation_result, final_status=final_status, multiple_testing=multiple_testing, candidate_id=candidate_id)

    def _identifier(self, value: str, kind: str) -> str:
        candidate = str(value)
        if not _IDENTIFIER_RE.fullmatch(candidate):
            raise ResearchEvolutionError("INVALID_IDENTIFIER", f"{kind} 标识不合法")
        return candidate

    def _relative(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return path.resolve().relative_to(self.root).as_posix()

    def _find_json(
        self,
        filename: str,
        *,
        objective_id: str,
        candidate_id: str,
        trial_id: str,
        roots: Iterable[Path],
    ) -> tuple[Any, Path] | tuple[None, None]:
        candidates: list[tuple[int, str, Any, Path]] = []
        identities = (objective_id, candidate_id, trial_id)
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob(filename), key=lambda item: item.as_posix()):
                try:
                    payload = _load_json(path)
                except ResearchEvolutionError:
                    continue
                serialized = json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, default=str)
                path_text = path.as_posix()
                if filename in _TRIAL_SPECIFIC_FILES and not (trial_id in path_text or trial_id in serialized):
                    continue
                if objective_id not in path_text and objective_id not in serialized:
                    continue
                score = 0
                for weight, identity in ((100, trial_id), (70, candidate_id), (40, objective_id)):
                    if identity in path_text:
                        score += weight
                    if identity in serialized:
                        score += weight
                if all(identity in serialized or identity in path_text for identity in identities):
                    score += 1000
                candidates.append((score, path_text, payload, path))
        if not candidates:
            return None, None
        _, _, payload, path = max(candidates, key=lambda item: (item[0], item[1]))
        return payload, path

    def _objective(self, objective_id: str) -> tuple[Mapping[str, Any], Path]:
        path = self.root / "data" / "research" / "research_factory" / "objectives" / f"{objective_id}.json"
        if not path.exists():
            raise ResearchEvolutionError("OBJECTIVE_NOT_FOUND", "未找到请求的研究目标登记文件")
        payload = _load_json(path)
        if not isinstance(payload, Mapping) or str(payload.get("objective_id")) != objective_id:
            raise ResearchEvolutionError("OBJECTIVE_SOURCE_MISMATCH", "研究目标登记文件身份校验失败")
        return payload, path

    def _candidate_contract(self, objective_id: str, candidate_id: str) -> tuple[Mapping[str, Any], Path]:
        search_root = self.root / "data" / "research" / "research_factory"
        matches: list[tuple[int, str, Mapping[str, Any], Path]] = []
        for path in sorted(search_root.rglob("durable_frozen_candidate_contracts.json"), key=lambda item: item.as_posix()):
            try:
                payload = _load_json(path)
            except ResearchEvolutionError:
                continue
            contracts = payload.get("contracts", []) if isinstance(payload, Mapping) else payload
            if not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, Mapping) or str(contract.get("candidate_id")) != candidate_id:
                    continue
                serialized = json.dumps(_json_value(contract), ensure_ascii=False, sort_keys=True, default=str)
                score = 1000 + (100 if objective_id in serialized else 0) + (100 if objective_id in path.as_posix() else 0)
                matches.append((score, path.as_posix(), contract, path))
        if not matches:
            raise ResearchEvolutionError("CANDIDATE_CONTRACT_NOT_FOUND", "未找到请求候选的冻结合同")
        _, _, contract, path = max(matches, key=lambda item: (item[0], item[1]))
        return contract, path

    def _trial_ledger(self, objective_id: str, candidate_id: str, trial_id: str) -> tuple[Mapping[str, Any] | None, Path | None]:
        search_root = self.root / "data" / "research" / "research_factory"
        matches: list[tuple[str, Mapping[str, Any], Path]] = []
        for path in sorted(search_root.rglob("factory_trial_ledger.json"), key=lambda item: item.as_posix()):
            try:
                payload = _load_json(path)
            except ResearchEvolutionError:
                continue
            events = payload if isinstance(payload, list) else payload.get("events", payload.get("entries", [])) if isinstance(payload, Mapping) else []
            if not isinstance(events, list):
                continue
            for event in events:
                if isinstance(event, Mapping) and _contains_identity(event, (objective_id, candidate_id, trial_id)):
                    matches.append((json.dumps(_json_value(event), ensure_ascii=False, sort_keys=True), event, path))
        if not matches:
            return None, None
        _, event, path = matches[-1]
        return event, path

    def _trial_contract(self, objective_id: str, candidate_id: str, trial_id: str) -> tuple[Mapping[str, Any] | None, Path | None]:
        direct = self.root / "reports" / "research_daemon" / objective_id / "predictive" / "trial_contracts" / f"{trial_id}.json"
        if direct.exists():
            payload = _load_json(direct)
            if isinstance(payload, Mapping):
                return payload, direct
        payload, path = self._find_json(
            f"{trial_id}.json",
            objective_id=objective_id,
            candidate_id=candidate_id,
            trial_id=trial_id,
            roots=(self.root / "reports" / "research_daemon" / objective_id,),
        )
        return (payload, path) if isinstance(payload, Mapping) else (None, None)

    def _artifact_inputs(self, objective_id: str, candidate_id: str, trial_id: str) -> dict[str, Any]:
        objective, objective_path = self._objective(objective_id)
        candidate, candidate_path = self._candidate_contract(objective_id, candidate_id)
        ledger_event, ledger_path = self._trial_ledger(objective_id, candidate_id, trial_id)
        reports_root = self.root / "reports" / "research_daemon" / objective_id
        validation, validation_path = self._find_json("validation_results.json", objective_id=objective_id, candidate_id=candidate_id, trial_id=trial_id, roots=(reports_root,))
        final_status, final_status_path = self._find_json("final_status.json", objective_id=objective_id, candidate_id=candidate_id, trial_id=trial_id, roots=(reports_root,))
        multiple_testing, multiple_testing_path = self._find_json("multiple_testing.json", objective_id=objective_id, candidate_id=candidate_id, trial_id=trial_id, roots=(reports_root, self.root / "data" / "research" / "research_factory"))
        trial_contract, trial_contract_path = self._trial_contract(objective_id, candidate_id, trial_id)
        if not isinstance(validation, Mapping):
            raise ResearchEvolutionError("VALIDATION_RESULT_NOT_FOUND", "未找到与指定 Trial 精确对应的 validation_results.json")
        if not isinstance(final_status, Mapping):
            raise ResearchEvolutionError("FINAL_STATUS_NOT_FOUND", "未找到与指定 Trial 精确对应的 final_status.json")
        return {
            "objective": objective,
            "objective_path": objective_path,
            "candidate": candidate,
            "candidate_path": candidate_path,
            "trial_event": ledger_event,
            "trial_ledger_path": ledger_path,
            "validation": validation,
            "validation_path": validation_path,
            "final_status": final_status,
            "final_status_path": final_status_path,
            "multiple_testing": multiple_testing if isinstance(multiple_testing, Mapping) else {},
            "multiple_testing_path": multiple_testing_path,
            "trial_contract": trial_contract or {},
            "trial_contract_path": trial_contract_path,
        }

    @staticmethod
    def _candidate_descriptor(candidate: Mapping[str, Any]) -> dict[str, Any]:
        semantic = candidate.get("semantic_record") if isinstance(candidate.get("semantic_record"), Mapping) else {}
        def first(keys: set[str]) -> Any:
            for source in (candidate, semantic):
                value = _first_value(source, keys)
                if value not in (None, "", []):
                    return value
            return None
        factor_ids = first({"factor_ids", "factors"})
        if not isinstance(factor_ids, list):
            factor_ids = []
        return {
            "candidate_hash": first({"candidate_hash", "contract_hash", "identity_hash"}),
            "candidate_family": first({"family", "family_id", "mechanism_family", "candidate_family"}) or "UNKNOWN",
            "mechanism": first({"mechanism", "mechanism_key", "mechanism_description", "hypothesis"}) or "未提供机制说明",
            "factor_ids": [str(item) for item in factor_ids],
            "holding_horizon": first({"holding_horizon", "holding_days"}),
        }

    def _output_paths(self, objective_id: str, candidate_id: str, trial_id: str) -> dict[str, Path]:
        base = self.root / "reports" / "research_evolution" / objective_id / candidate_id / trial_id
        return {
            "report": base / self.report_filename,
            "human": base / self.human_report_filename,
            "context": base / self.context_filename,
            "landscape": self.root / "reports" / "research_evolution" / objective_id / "failure_landscape.json",
        }

    def _build_context(self, report: Mapping[str, Any]) -> dict[str, Any]:
        candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else {}
        failure_analysis = report.get("failure_analysis") if isinstance(report.get("failure_analysis"), Mapping) else {}
        categories = [str(item) for item in failure_analysis.get("primary_categories", [])]
        mechanism = str(candidate.get("mechanism") or "未提供机制说明")
        context = {
            "schema_version": "ai-research-evolution-context-v1",
            "context_type": "RESEARCH_DESIGN_CONTEXT",
            "generated_at": report.get("generated_at"),
            "objective_id": report.get("lineage", {}).get("objective_id"),
            "tested_mechanisms": [{
                "candidate_family": candidate.get("candidate_family"),
                "mechanism": mechanism,
                "failure_categories": categories,
                "research_constraint_zh": "；".join(_CATEGORY_CONSTRAINTS[item] for item in categories if item in _CATEGORY_CONSTRAINTS),
            }],
            "avoid": [
                "不要在同一机制家族上只调整参数后重复试验。",
                "不要把已完成试验的结果字段带入下一候选的设计输入。",
            ],
            "unexplored_directions": [
                {"direction_id": "EVENT_DRIVEN", "description_zh": "探索事件驱动的信息机制，保持与当前机制来源独立。"},
                {"direction_id": "PRICE_STRUCTURE", "description_zh": "探索价格结构或状态转移机制，不复用当前参与度假设。"},
                {"direction_id": "DATA_AND_EXECUTION_PREFLIGHT", "description_zh": "在提出新候选前先确认 PIT、样本可行性和执行语义。"},
            ],
            "research_space_coverage": {
                "tested_mechanism_families": [candidate.get("candidate_family")],
                "tested_failure_categories": categories,
                "coverage_boundary": "仅覆盖本次明确绑定的 Candidate 与 Trial。",
            },
            "research_constraints": [
                "保持 Candidate 身份冻结，不回写历史结果。",
                "研究演进上下文只用于人工审核后的研究设计。",
                "到达下一轮前必须重新完成治理确认。",
            ],
            "next_boundary": "HUMAN_REVIEW_REQUIRED",
            "automatic_candidate_generation_allowed": False,
            "automatic_trial_start_allowed": False,
            "codex_invocation_allowed": False,
            "budget_consumption_allowed": False,
            "outcome_blind": True,
            "outcome_fields_available": False,
            "exact_outcome_fields_exposed": False,
        }
        PerformanceBlindGuard.assert_blind(context)
        return context

    def _human_report(self, report: Mapping[str, Any]) -> str:
        lineage = report.get("lineage", {})
        candidate = report.get("candidate", {})
        analysis = report.get("failure_analysis", {})
        lines = [
            "# 研究演进分析",
            "",
            "本报告只分析已完成的研究结果，形成失败分类与下一轮研究设计上下文；不会自动创建 Candidate、启动 Trial 或调用 AI。",
            "",
            "## 研究对象",
            "",
            f"- 研究目标：`{lineage.get('objective_id', '未提供')}`",
            f"- Candidate：`{lineage.get('candidate_id', '未提供')}`",
            f"- Trial：`{lineage.get('trial_id', '未提供')}`",
            f"- 终态：{ZhCNPresentation.classification_name(report.get('research_result', {}).get('terminal_classification') or report.get('research_result', {}).get('terminal_status'))}",
            f"- 机制家族：{candidate.get('candidate_family', '未提供')}",
            f"- 研究机制：{candidate.get('mechanism', '未提供')}",
            "",
            "## 失败分类",
            "",
        ]
        findings = analysis.get("findings", []) if isinstance(analysis, Mapping) else []
        if findings:
            for item in findings:
                lines.append(f"- **{ZhCNPresentation.term(str(item.get('category')))}**：{item.get('explanation_zh', '未提供解释')}（证据 `{item.get('evidence_id', '未提供')}`）")
        else:
            lines.append("- 当前没有可分类的失败证据，需要人工复核。")
        lines.extend(["", "## 研究空间覆盖", "", f"本次已测试机制：{candidate.get('mechanism', '未提供')}。", f"失败类别分布：{json.dumps(analysis.get('category_counts', {}), ensure_ascii=False, sort_keys=True)}。", "", "## 下一轮研究建议", ""])
        for direction in report.get("next_research_directions", []):
            lines.append(f"- {direction.get('title_zh', direction.get('description_zh', '未提供建议'))}：{direction.get('reason_zh', '')}")
        lines.extend(["", "## 治理边界", "", "- 本轮输出为只读分析，不改变 Candidate、Trial、预算或 Multiple Testing 记录。", "- AI 研究上下文仅在人工审核后作为设计参考；本流程在生成上下文后停止。"])
        return "\n".join(lines)

    def _update_landscape(self, report: Mapping[str, Any], path: Path) -> dict[str, Any]:
        existing: dict[str, Any] = {}
        if path.exists():
            payload = _load_json(path)
            if not isinstance(payload, Mapping):
                raise ResearchEvolutionError("LANDSCAPE_INVALID", "已有失败空间文件格式不受支持")
            existing = dict(payload)
        lineage = report.get("lineage", {})
        candidate = report.get("candidate", {})
        analysis = report.get("failure_analysis", {})
        categories = [str(item) for item in analysis.get("primary_categories", [])]
        entries = [dict(item) for item in existing.get("entries", []) if isinstance(item, Mapping)]
        key = (str(lineage.get("candidate_id")), str(lineage.get("trial_id")))
        entry = next((item for item in entries if (str(item.get("candidate_id")), str(item.get("trial_id"))) == key), None)
        if entry is None:
            entry = {
                "candidate_id": lineage.get("candidate_id"),
                "trial_id": lineage.get("trial_id"),
                "candidate_family": candidate.get("candidate_family"),
                "mechanism": candidate.get("mechanism"),
                "failure_categories": categories,
                "first_seen_at": report.get("generated_at"),
                "last_seen_at": report.get("generated_at"),
            }
            entries.append(entry)
        else:
            entry["failure_categories"] = sorted(set(entry.get("failure_categories", [])) | set(categories))
            entry["last_seen_at"] = max(str(entry.get("last_seen_at") or ""), str(report.get("generated_at") or ""))
        category_totals = Counter()
        for item in entries:
            category_totals.update(str(category) for category in item.get("failure_categories", []) if category in FAILURE_CATEGORIES)
        landscape = {
            "schema_version": "research-failure-landscape-v1",
            "objective_id": lineage.get("objective_id"),
            "updated_at": existing.get("updated_at") or report.get("generated_at"),
            "entries": sorted(entries, key=lambda item: (str(item.get("candidate_id")), str(item.get("trial_id")))),
            "trial_count": len(entries),
            "category_totals": {category: int(category_totals.get(category, 0)) for category in FAILURE_CATEGORIES},
            "read_only": True,
        }
        return landscape

    def _build_report(self, objective_id: str, candidate_id: str, trial_id: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        candidate_descriptor = self._candidate_descriptor(inputs["candidate"])
        final_status = inputs["final_status"]
        validation = inputs["validation"]
        multiple_testing = inputs["multiple_testing"]
        findings = classify_failures(validation, final_status=final_status, multiple_testing=multiple_testing, candidate_id=candidate_id)
        counts = Counter(str(item["category"]) for item in findings)
        categories = [category for category in FAILURE_CATEGORIES if counts.get(category, 0)]
        refs = {
            "objective_ref": self._relative(inputs["objective_path"]),
            "candidate_contract_ref": self._relative(inputs["candidate_path"]),
            "trial_contract_ref": self._relative(inputs["trial_contract_path"]),
            "trial_ledger_ref": self._relative(inputs["trial_ledger_path"]),
            "validation_result_ref": self._relative(inputs["validation_path"]),
            "final_status_ref": self._relative(inputs["final_status_path"]),
            "multiple_testing_ref": self._relative(inputs["multiple_testing_path"]),
        }
        input_hash = stable_hash({key: _json_value(value) for key, value in inputs.items() if not key.endswith("_path")})
        generated_at = now_timestamp()
        report = {
            "schema_version": "research-evolution-report-v1",
            "manager_version": "research-evolution-manager-v1",
            "report_type": "RESEARCH_EVOLUTION_REPORT",
            "report_id": f"{objective_id}__{candidate_id}__{trial_id}",
            "generated_at": generated_at,
            "source_input_hash": input_hash,
            "lineage": {"objective_id": objective_id, "candidate_id": candidate_id, "trial_id": trial_id, **refs},
            "candidate": candidate_descriptor,
            "research_result": {
                "terminal_status": final_status.get("status") or final_status.get("classification") or "UNKNOWN",
                "terminal_classification": final_status.get("classification") or final_status.get("status") or "UNKNOWN",
                "analysis_boundary": "BLOCKED_RESULT_ONLY",
            },
            "failure_analysis": {
                "category_counts": {category: int(counts.get(category, 0)) for category in FAILURE_CATEGORIES},
                "primary_categories": categories,
                "findings": findings,
            },
            "research_space_coverage": {
                "tested_mechanisms": [candidate_descriptor.get("mechanism")],
                "tested_mechanism_families": [candidate_descriptor.get("candidate_family")],
                "coverage_statement_zh": "仅覆盖当前明确绑定的 Candidate/Trial，不能推断整个研究空间。",
            },
            "next_research_directions": [
                {"direction_id": "EVENT_DRIVEN", "title_zh": "切换到事件驱动机制", "description_zh": "寻找与当前机制来源独立的信息触发方式。", "reason_zh": "避免在同一机制家族上只做参数微调。"},
                {"direction_id": "PRICE_STRUCTURE", "title_zh": "探索价格结构机制", "description_zh": "从价格结构或状态转移角度提出可独立检验的假设。", "reason_zh": "扩大尚未覆盖的研究方向。"},
                {"direction_id": "PREFLIGHT_FIRST", "title_zh": "先做研究可行性预检", "description_zh": "在设计新候选前确认 PIT、样本可行性和执行语义。", "reason_zh": "降低工程阻断和重复无效试验的风险。"},
            ],
            "governance": {
                "read_only": True,
                "candidate_modified": False,
                "trial_result_modified": False,
                "validation_modified": False,
                "budget_modified": False,
                "multiple_testing_modified": False,
                "performance_modified": False,
                "automatic_candidate_created": False,
                "automatic_trial_started": False,
                "codex_called": False,
                "stops_before_candidate_generation": True,
                "next_action": "HUMAN_REVIEW_OF_RESEARCH_CONTEXT",
            },
        }
        report["report_hash"] = stable_hash(report)
        return report

    def generate_report(self, objective_id: str, candidate_id: str, trial_id: str) -> dict[str, Any]:
        objective_id = self._identifier(objective_id, "objective_id")
        candidate_id = self._identifier(candidate_id, "candidate_id")
        trial_id = self._identifier(trial_id, "trial_id")
        paths = self._output_paths(objective_id, candidate_id, trial_id)
        inputs = self._artifact_inputs(objective_id, candidate_id, trial_id)
        source_input_hash = stable_hash({key: _json_value(value) for key, value in inputs.items() if not key.endswith("_path")})
        if paths["report"].exists():
            existing = _load_json(paths["report"])
            if isinstance(existing, Mapping) and existing.get("manager_version") == "research-evolution-manager-v1" and existing.get("source_input_hash") == source_input_hash and existing.get("lineage", {}).get("trial_id") == trial_id:
                if not paths["context"].exists():
                    _atomic_write_json(paths["context"], self._build_context(existing))
                write_human_report(paths["human"], self._human_report(existing))
                if not paths["landscape"].exists():
                    _atomic_write_json(paths["landscape"], self._update_landscape(existing, paths["landscape"]))
                return dict(existing)
        report = self._build_report(objective_id, candidate_id, trial_id, inputs)
        context = self._build_context(report)
        landscape = self._update_landscape(report, paths["landscape"])
        _atomic_write_json(paths["report"], report)
        _atomic_write_json(paths["context"], context)
        _atomic_write_json(paths["landscape"], landscape)
        write_human_report(paths["human"], self._human_report(report))
        return report

    run = generate_report


def _main() -> int:
    parser = argparse.ArgumentParser(description="生成只读研究演进分析报告")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--trial-id", required=True)
    args = parser.parse_args()
    report = ResearchEvolutionManager(args.root).generate_report(args.objective_id, args.candidate_id, args.trial_id)
    print(json.dumps({"状态": "已生成研究演进分析", "报告编号": report["report_id"], "报告哈希": report["report_hash"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["FAILURE_CATEGORIES", "ResearchEvolutionError", "ResearchEvolutionManager", "classify_failures"]
