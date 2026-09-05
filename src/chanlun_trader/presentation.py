"""项目级人类可读输出层。

机器合同、状态值和标识符在领域模块中保持 canonical English；本模块只负责
把这些值投影为默认的 zh-CN 展示文本。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_HUMAN_LANGUAGE = "zh-CN"


@dataclass(frozen=True)
class ReasonDisplay:
    """一个用户可见 Reason Code 的中文短标题和解释。"""

    title: str
    explanation: str


STATE_DISPLAY_NAMES: dict[str, str] = {
    "BOOTSTRAP": "正在初始化",
    "RECOVER": "正在恢复运行状态",
    "READY": "已就绪",
    "READY_FOR_STRUCTURAL_PREFLIGHT": "候选已就绪，等待显式结构预检",
    "WAITING_FOR_ORCHESTRATOR": "等待 Orchestrator 接管",
    "STRUCTURAL_PENDING": "等待结构预检",
    "STRUCTURAL_RUNNING": "结构预检运行中",
    "STRUCTURAL_PASS": "结构预检通过",
    "STRUCTURAL_UNKNOWN": "结构预检结果仍无法确认",
    "STRUCTURAL_BLOCKED": "结构预检未通过",
    "PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED": "等待人工授权预测验证",
    "PREDICTIVE_PENDING": "等待预测验证",
    "PREDICTIVE_RUNNING": "预测验证运行中",
    "PREDICTIVE_COMPLETE": "预测验证完成",
    "CANDIDATE_COMPLETE": "候选处理完成",
    "NEXT_CANDIDATE": "准备处理下一候选",
    "NEED_AI_RESEARCH_DESIGN": "需要 AI 设计新策略",
    "ENGINEERING_BLOCKED": "工程问题阻断",
    "CANONICAL_STATE_CONFLICT": "Canonical 事实冲突",
    "GOVERNANCE_REQUIRED": "需要治理决策",
    "RESOURCE_WAIT": "等待系统资源",
    "PAUSED": "已暂停",
    "RESEARCH_PASSED": "已发现研究通过策略",
    "BUDGET_EXHAUSTED": "预测试验预算已耗尽",
    "GLOBAL_SEARCH_EXHAUSTED": "合法研究空间已耗尽",
    "SAFETY_STOP": "因安全规则停止",
    "SHUTDOWN": "已停止",
}


REASON_DISPLAY_NAMES: dict[str, ReasonDisplay] = {
    "END_OF_WINDOW_TRUNCATION": ReasonDisplay(
        "研究窗口尾部截断", "研究窗口末端没有足够的后续数据完成严格验证。"
    ),
    "INSUFFICIENT_EXECUTABLE_OPPORTUNITIES": ReasonDisplay(
        "可执行机会不足", "满足执行约束的机会数量不足以支持当前研究要求。"
    ),
    "SAMPLE_FEASIBILITY_UNKNOWN": ReasonDisplay(
        "样本可行性未知", "当前证据不足以确认样本是否可执行，系统保持 fail closed。"
    ),
    "DATA_CAPABILITY_LIMIT": ReasonDisplay(
        "数据能力边界", "当前数据源覆盖或字段能力不足，无法安全完成该步骤。"
    ),
    "GOVERNANCE_REQUIRED": ReasonDisplay(
        "需要治理决策", "该动作触及治理边界，必须由明确的治理决策授权。"
    ),
    "ENGINEERING_BLOCKED": ReasonDisplay(
        "工程问题阻断", "实现或运行时问题阻止了安全继续，需先完成工程修复。"
    ),
    "STRUCTURAL_PASS": ReasonDisplay(
        "结构预检通过", "候选已通过不读取绩效结果的结构性预检。"
    ),
    "B_VALID_LOWER_BOUND_AT_OR_ABOVE_MINIMUM": ReasonDisplay(
        "安全下界达到最低要求", "完整性审计已证明候选的安全样本下界达到冻结政策门槛。"
    ),
    "A_UPPER_BOUND_BELOW_MINIMUM": ReasonDisplay(
        "结构样本上界不足", "即使纳入所有已知可能机会，安全样本上界仍低于冻结政策要求，不能进入预测验证。"
    ),
    "STRUCTURAL_UNKNOWN": ReasonDisplay(
        "结构预检未知", "结构性证据不完整，不能将候选当作已通过处理。"
    ),
    "STRUCTURAL_BLOCKED": ReasonDisplay(
        "结构预检未通过", "候选未满足结构性执行条件。"
    ),
    "PREDICTIVE_RUNTIME_ERROR": ReasonDisplay(
        "预测运行时错误", "预测验证运行时发生错误，需完成 canonical 运行时修复。"
    ),
    "RAW_BOOTSTRAP_NOT_SUPPORTED": ReasonDisplay(
        "原始 Bootstrap 支持不足", "当前预测样本未达到冻结统计政策要求的原始 Bootstrap 支持门槛。"
    ),
    "AI_ONE_SHOT_POLICY_EXHAUSTED": ReasonDisplay(
        "一次性研究设计已完成", "当前目标允许的唯一 AI 设计机会已经完成处理，系统不会生成第二个候选或重跑已有试验。"
    ),
    "PREDICTIVE_BOUNDARY_REQUIRES_RECONCILIATION": ReasonDisplay(
        "预测边界需要对账", "预测边界已经被访问，必须先完成 canonical Trial 对账。"
    ),
    "NO_FROZEN_CANDIDATE_LEGAL_SEARCH_REMAINS": ReasonDisplay(
        "没有剩余冻结候选", "当前合法搜索空间没有可继续处理的冻结候选。"
    ),
    "PREDICTIVE_BUDGET_EXHAUSTED": ReasonDisplay(
        "预测试验预算耗尽", "剩余预测试验预算为零，系统停止继续申请预测访问。"
    ),
    "PREDICTIVE_BUDGET_EXHAUSTED_BEFORE_CANDIDATE_SELECTION": ReasonDisplay(
        "选择候选前预测预算已耗尽", "本轮预测试验预算已用尽，系统没有继续选择或启动新的预测试验。"
    ),
    "GLOBAL_LEGAL_SEARCH_EXHAUSTED": ReasonDisplay(
        "合法研究空间耗尽", "已经没有符合当前规则的合法研究空间。"
    ),
    "MEMORY_PRESSURE_BEFORE_STRUCTURAL_RUN": ReasonDisplay(
        "结构运行前内存压力", "系统可用内存低于安全阈值，等待资源恢复。"
    ),
    "NEXT_LEGAL_SESSION_UNCONFIRMED": ReasonDisplay(
        "下一合法交易日未确认", "当前证据尚未确认后续合法交易日，不得伪造执行日期。"
    ),
    "MISSING_EXPECTED_BAR": ReasonDisplay(
        "缺少预期行情柱", "数据源没有提供当前步骤要求的行情柱，保持安全阻断。"
    ),
}


CLASSIFICATION_DISPLAY_NAMES: dict[str, str] = {
    "RESEARCH_PASSED": "研究通过",
    "PROMISING": "有潜力",
    "WEAK": "较弱",
    "REJECTED": "已淘汰",
    "BLOCKED": "预测验证未通过",
}


STATUS_DISPLAY_NAMES: dict[str, str] = {
    "COMPLETED": "已完成",
    "READY": "已就绪",
    "NOT_READY": "未就绪",
    "NOT_CHECKED": "尚未检查",
    "NOT_RUN": "尚未运行",
    "SIGNAL_GENERATED": "已生成信号",
    "NO_SIGNAL": "没有信号",
    "PAPER_OBSERVATION": "Paper 观察",
    "RECOMMENDATION_CANDIDATE": "推荐候选",
    "NO_TRADE": "不交易",
    "FILLED": "已成交",
    "ACCEPTED_PENDING_FILL": "已接受，等待成交",
    "DATA_NOT_READY": "数据未就绪",
    "PAPER_TRACKING": "Paper 跟踪中",
    "CLOSE_ARCHIVED": "收盘结果已归档",
    "PAPER_NOT_ARCHIVED": "Paper 结果未归档",
    "PASS": "通过",
    "FAIL": "未通过",
    "SHADOW_RESEARCH_ONLY": "仅限 Shadow 研究观察",
    "NEXT_LEGAL_SESSION_UNCONFIRMED": "下一合法交易日未确认",
    "QUALIFIED_T_CLOSE_THEN_RANKED": "收盘确认后完成排序",
}


ACTION_DISPLAY_NAMES: dict[str, str] = {
    "ENGINEERING_REPAIR_OR_RECONCILIATION": "进行工程修复或 canonical 对账",
    "CANONICAL_RUNTIME_REPAIR": "完成 canonical 运行时修复",
    "CANONICAL_TRIAL_RECONCILIATION_REQUIRED": "完成 canonical Trial 对账",
    "HUMAN_TRIGGERED_AI_RESEARCH_DESIGN": "等待人工触发 AI 研究设计",
    "PREDICTIVE_RETRY_NOT_AUTHORIZED_UNTIL_CANONICAL_RECONCILIATION": "完成 canonical 对账后再判断是否可重试",
    "RESUME_REQUIRED": "需要恢复运行",
    "STOPPED_AT_SAFE_BOUNDARY": "已在安全边界停止",
    "CTRL_C_SAFE_BOUNDARY": "已在安全边界响应停止信号",
    "RESOURCE_WAIT": "等待系统资源恢复",
    "RUN_STRUCTURAL_PREFLIGHT": "启动结构预检",
    "STRUCTURAL_RUN_IN_PROGRESS": "等待结构预检完成",
    "RECONCILE_STRUCTURAL": "重新对账结构结果",
    "ENGINEERING_REVIEW_REQUIRED": "需要工程复核",
    "AUTHORIZE_PREDICTIVE_TRIAL": "人工授权预测验证",
    "STOP_AND_RECONCILE_CANONICAL_CONFLICT": "停止并对账 canonical 冲突",
}


TERMINOLOGY: dict[str, str] = {
    "Research Period": "研究区间",
    "Final Test": "最终测试集",
    "Prospective": "前瞻验证",
    "Predictive Trial": "预测性试验",
    "Sample Feasibility": "样本可行性",
    "Structural Preflight": "结构预检",
    "Lower Bound": "安全下界",
    "Upper Bound": "可能上界",
    "Search Budget": "预测试验预算",
    "PerformanceAccessGate": "绩效访问闸门",
    "FailureKnowledge": "失败知识",
    "ArtifactGraph": "制品关系图 / ArtifactGraph",
    "PIT": "PIT（时点一致性）",
    "Shadow": "Shadow 观察 / 影子研究",
    "STATISTICAL_FAILURE": "统计失败",
    "RETURN_FAILURE": "收益失败",
    "RISK_FAILURE": "风险失败",
    "SAMPLE_FAILURE": "样本失败",
    "ENGINEERING_FAILURE": "工程失败",
    "OVERFITTING_RISK": "过拟合风险",
}


def _canonical(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).strip().upper()


def display_state(value: Any, *, include_code: bool = False) -> str:
    """返回状态的中文展示；未知状态保留原始值。"""

    code = _canonical(value)
    if not code:
        return "未知状态（未提供）"
    label = STATE_DISPLAY_NAMES.get(code, f"未知状态（{code}）")
    return f"{label}（{code}）" if include_code and code in STATE_DISPLAY_NAMES else label


def reason_display(value: Any) -> ReasonDisplay:
    """返回 Reason Code 的中文标题和解释，未知值安全回退。"""

    code = _canonical(value)
    if code in REASON_DISPLAY_NAMES:
        return REASON_DISPLAY_NAMES[code]
    if not code:
        return ReasonDisplay("未提供原因", "没有提供可供用户判断的 Reason Code。")
    return ReasonDisplay("未知原因", f"尚未配置该 Reason Code 的中文解释：{code}。")


def display_reason(value: Any, *, include_code: bool = True) -> str:
    code = _canonical(value)
    display = reason_display(code)
    if include_code and code:
        return f"{display.title}（{code}）"
    return display.title


def format_reason(value: Any, *, include_code: bool = True) -> str:
    code = _canonical(value)
    display = reason_display(code)
    title = f"{display.title}（{code}）" if include_code and code else display.title
    return f"{title}：{display.explanation}"


def display_classification(value: Any, *, include_code: bool = False) -> str:
    code = _canonical(value)
    label = CLASSIFICATION_DISPLAY_NAMES.get(code, f"未知分类（{code or '未提供'}）")
    return f"{label}（{code}）" if include_code and code in CLASSIFICATION_DISPLAY_NAMES else label


def display_status(value: Any, *, include_code: bool = False) -> str:
    """返回通用人类状态的中文展示，未知值保留原始值。"""

    code = _canonical(value)
    if code in STATE_DISPLAY_NAMES:
        return display_state(code, include_code=include_code)
    label = STATUS_DISPLAY_NAMES.get(code, f"未知状态（{code or '未提供'}）")
    return f"{label}（{code}）" if include_code and code in STATUS_DISPLAY_NAMES else label


def display_action(value: Any, *, include_code: bool = True) -> str:
    code = _canonical(value)
    if not code:
        return "无"
    label = ACTION_DISPLAY_NAMES.get(code, f"未知动作（{code}）")
    return f"{label}（{code}）" if include_code and code in ACTION_DISPLAY_NAMES else label


def display_term(value: str) -> str:
    return TERMINOLOGY.get(value, value)


def _format_int(value: Any, fallback: str = "未知") -> str:
    if value is None or value == "":
        return fallback
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_bytes(value: Any) -> str:
    if value is None:
        return "未知"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def render_daemon_status(status: Mapping[str, Any]) -> str:
    """渲染 daemon 的中文终端摘要，不改变输入 payload。"""

    state = status.get("daemon_state") or status.get("stage")
    budget = status.get("budget") or {}
    candidate = status.get("current_candidate") or {}
    candidate_id = candidate.get("candidate_id") if isinstance(candidate, Mapping) else None
    last_error = status.get("last_error")
    error_reason_code = status.get("error_reason_code")
    required_action = status.get("required_human_ai_action")
    reason_code = error_reason_code or required_action
    if last_error:
        error_text = str(last_error)
        if reason_code:
            error_text = f"{format_reason(reason_code)}；内部错误：{error_text}"
    else:
        error_text = "无"
    used = budget.get("used")
    total = budget.get("total")
    remaining = budget.get("remaining")
    if used is not None or total is not None:
        budget_text = f"已使用 {_format_int(used)} / {_format_int(total)}，剩余 {_format_int(remaining)}"
    else:
        budget_text = "未建立预算视图"

    lines = [
        "========== 自主量化研究守护进程 ==========",
        f"时间          : {status.get('last_checkpoint_time') or '未知'}",
        f"运行状态      : {display_state(state, include_code=True)}",
        f"当前阶段      : {display_state(status.get('stage') or state, include_code=True)}",
        f"当前候选      : {candidate_id or '无'}",
        f"剩余候选      : {_format_int(status.get('remaining_frozen_candidates'))}",
        f"预测试验预算  : {budget_text}",
        f"研究通过      : {_format_int(status.get('RESEARCH_PASSED'), '0')}",
        f"有潜力        : {_format_int(status.get('PROMISING'), '0')}",
        f"最近错误      : {error_text}",
        f"建议动作      : {display_action(required_action) if required_action else '无'}",
        f"进程内存      : {_format_bytes(status.get('process_rss_bytes'))}",
        f"系统可用内存  : {_format_bytes(status.get('system_available_memory_bytes'))}",
        "真实订单      : 已禁用（REAL_ORDER=DISABLED）",
    ]
    return "\n".join(lines)


def render_cli_result(payload: Mapping[str, Any]) -> str:
    """渲染非 status CLI 命令的最小中文结果。"""

    if "daemon_state" in payload or "stage" in payload:
        return render_daemon_status(payload)
    command = payload.get("command") or "未知命令"
    lines = ["========== 命令执行结果 ==========", f"命令          : {command}"]
    request = payload.get("request")
    if isinstance(request, Mapping):
        lines.append(f"请求动作      : {request.get('action') or '未知'}（已记录）")
    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, Mapping):
        lines.append(f"守护进程状态  : {display_state(checkpoint.get('current_state'), include_code=True)}")
        if checkpoint.get("required_action"):
            lines.append(f"下一步        : {display_action(checkpoint['required_action'])}")
    if "status" in payload:
        lines.append(f"结果状态      : {display_state(payload.get('status'), include_code=True)}")
    return "\n".join(lines)


def write_human_report(path: str | Path, content: str) -> Path:
    """以 UTF-8 写入人类报告，供未来 machine/human report pairing 复用。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")
    return target


def write_report_pair(
    machine_path: str | Path,
    machine_payload: Mapping[str, Any],
    human_path: str | Path,
    human_content: str,
) -> tuple[Path, Path]:
    """写入 canonical machine JSON 和独立 zh-CN human report。"""

    machine_target = Path(machine_path)
    machine_target.parent.mkdir(parents=True, exist_ok=True)
    machine_target.write_text(
        json.dumps(machine_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return machine_target, write_human_report(human_path, human_content)


class ZhCNPresentation:
    """共享 zh-CN 展示门面，避免各业务模块重复维护翻译字符串。"""

    default_language = DEFAULT_HUMAN_LANGUAGE

    @staticmethod
    def state_name(value: Any, *, include_code: bool = False) -> str:
        return display_state(value, include_code=include_code)

    @staticmethod
    def status_name(value: Any, *, include_code: bool = False) -> str:
        return display_status(value, include_code=include_code)

    @staticmethod
    def reason_title(value: Any, *, include_code: bool = True) -> str:
        return display_reason(value, include_code=include_code)

    @staticmethod
    def reason_explanation(value: Any) -> str:
        return reason_display(value).explanation

    @staticmethod
    def classification_name(value: Any, *, include_code: bool = False) -> str:
        return display_classification(value, include_code=include_code)

    @staticmethod
    def term(value: str) -> str:
        return display_term(value)

    @staticmethod
    def daemon_status(status: Mapping[str, Any]) -> str:
        return render_daemon_status(status)


__all__ = [
    "CLASSIFICATION_DISPLAY_NAMES",
    "ACTION_DISPLAY_NAMES",
    "DEFAULT_HUMAN_LANGUAGE",
    "REASON_DISPLAY_NAMES",
    "STATE_DISPLAY_NAMES",
    "STATUS_DISPLAY_NAMES",
    "TERMINOLOGY",
    "ReasonDisplay",
    "ZhCNPresentation",
    "display_classification",
    "display_action",
    "display_reason",
    "display_state",
    "display_status",
    "display_term",
    "format_reason",
    "reason_display",
    "render_cli_result",
    "render_daemon_status",
    "write_human_report",
    "write_report_pair",
]
