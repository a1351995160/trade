"""Web UI 后端（FastAPI）。

提供：
- 配置读取；
- 选股扫描（同步）；
- 回测任务（后台线程 + 进度查询）；
- K 线及缠论标记数据（用于前端画图）。
"""
from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .backtest import BacktestRunner
from .chan import analyze_stock
from .config import PROJECT_ROOT, load_config
from .metrics import compute_metrics
from .report import write_report
from .research_console import AIInvocationModeServiceV1, ResearchConsoleReadError, ResearchConsoleReadService
from .research_factory.autonomous_orchestrator_v2 import GovernanceDecisionServiceV1, OrchestratorControlServiceV1, OrchestratorOperationError
from .research_factory.contract_correction import ContractCorrectionError, FrozenContractCorrectionServiceV1
from .research_factory.governance_execution import GovernanceExecutionError, ResearchGovernanceExecutionServiceV1
from .research_factory.orchestrator_launcher import OrchestratorLaunchError, OrchestratorProcessLauncherV1
from .research_factory.predictive_authorization import PredictiveGovernanceError, PredictiveGovernanceServiceV1
from .research_factory.predictive_trial_reauthorization import PredictiveTrialReauthorizationServiceV1
from .research_factory.predictive_trial_start import PredictiveTrialStartError, PredictiveTrialStartServiceV1
from .research_factory.candidate_generation import CandidateGenerationError, CandidateGenerationManagerV1
from .research_factory.ai_design_approval import AIDesignApprovalError, AIDesignApprovalServiceV1
from .research_factory.candidate_executable_materialization import CandidateExecutableMaterializationError, CandidateExecutableMaterializationManagerV1
from .research_factory.research_evolution_ai_design import ResearchEvolutionAIDesignServiceV1
from .research_factory.research_proposal_governance import ResearchProposalGovernanceError, ResearchProposalGovernanceServiceV1
from .research_factory.structural_entry import StructuralEntryError, StructuralEntryServiceV1
from .research_factory.projection_reconciliation import ProjectionReconciliationError, ProjectionReconciliationServiceV1
from .research_factory.trial_reconciliation import CanonicalTrialReconciliationServiceV1, TrialReconciliationError
from .screener import scan_all
from .tdx_data import TdxData

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="缠论选股交易系统")

research_console_service = ResearchConsoleReadService(PROJECT_ROOT)
ai_invocation_mode_service = AIInvocationModeServiceV1(PROJECT_ROOT)
orchestrator_control_service = OrchestratorControlServiceV1(PROJECT_ROOT)
governance_decision_service = GovernanceDecisionServiceV1(PROJECT_ROOT)
governance_execution_service = ResearchGovernanceExecutionServiceV1(PROJECT_ROOT)
orchestrator_process_launcher = OrchestratorProcessLauncherV1(PROJECT_ROOT)
frozen_contract_correction_service = FrozenContractCorrectionServiceV1(PROJECT_ROOT)
canonical_trial_reconciliation_service = CanonicalTrialReconciliationServiceV1(PROJECT_ROOT)
predictive_governance_service = PredictiveGovernanceServiceV1(PROJECT_ROOT)
predictive_trial_start_service = PredictiveTrialStartServiceV1(PROJECT_ROOT)
predictive_trial_reauthorization_service = PredictiveTrialReauthorizationServiceV1(PROJECT_ROOT)
research_proposal_governance_service = ResearchProposalGovernanceServiceV1(PROJECT_ROOT)
research_evolution_ai_design_service = ResearchEvolutionAIDesignServiceV1(PROJECT_ROOT)
research_evolution_ai_design_approval_service = AIDesignApprovalServiceV1(PROJECT_ROOT)
candidate_generation_service = CandidateGenerationManagerV1(PROJECT_ROOT)
candidate_materialization_service = CandidateExecutableMaterializationManagerV1(PROJECT_ROOT)
structural_entry_service = StructuralEntryServiceV1(PROJECT_ROOT)
projection_reconciliation_service = ProjectionReconciliationServiceV1(PROJECT_ROOT)


@app.on_event("startup")
async def recover_formal_predictive_trials_on_backend_startup() -> None:
    """Backend restart only resumes already-confirmed durable Trial intents."""
    predictive_trial_start_service.recover_all()
    predictive_trial_reauthorization_service.recover_new_all()
    research_proposal_governance_service.recover_all()
    research_evolution_ai_design_service.recover_all()
    candidate_generation_service.recover_all()


@app.exception_handler(ResearchConsoleReadError)
async def research_console_error_handler(_, exc: ResearchConsoleReadError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(OrchestratorOperationError)
async def orchestrator_operation_error_handler(_, exc: OrchestratorOperationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message_zh": exc.message_zh, "details": {}})


@app.exception_handler(GovernanceExecutionError)
async def governance_execution_error_handler(_, exc: GovernanceExecutionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(PredictiveGovernanceError)
async def predictive_governance_error_handler(_, exc: PredictiveGovernanceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(PredictiveTrialStartError)
async def predictive_trial_start_error_handler(_, exc: PredictiveTrialStartError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(ResearchProposalGovernanceError)
async def research_proposal_governance_error_handler(_, exc: ResearchProposalGovernanceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(CandidateGenerationError)
async def candidate_generation_error_handler(_, exc: CandidateGenerationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(AIDesignApprovalError)
async def ai_design_approval_error_handler(_, exc: AIDesignApprovalError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(CandidateExecutableMaterializationError)
async def candidate_executable_materialization_error_handler(_, exc: CandidateExecutableMaterializationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(StructuralEntryError)
async def structural_entry_error_handler(_, exc: StructuralEntryError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


@app.exception_handler(ProjectionReconciliationError)
async def projection_reconciliation_error_handler(_, exc: ProjectionReconciliationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.envelope())


def _require_local_console_request(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="研究控制台写操作仅允许本机访问")


def _console_ai_design_approval_service() -> AIDesignApprovalServiceV1:
    scoped_service = getattr(_console_service(), "ai_design_approval", None)
    if scoped_service is not None:
        try:
            if Path(scoped_service.root).resolve() != PROJECT_ROOT.resolve():
                return scoped_service
        except (AttributeError, TypeError, OSError):
            pass
    return research_evolution_ai_design_approval_service


def _console_candidate_generation_service() -> CandidateGenerationManagerV1:
    scoped_service = getattr(_console_service(), "candidate_generation", None)
    if scoped_service is not None:
        try:
            if Path(scoped_service.root).resolve() != PROJECT_ROOT.resolve():
                return scoped_service
        except (AttributeError, TypeError, OSError):
            pass
    return candidate_generation_service


def _console_candidate_materialization_service() -> CandidateExecutableMaterializationManagerV1:
    scoped_service = getattr(_console_service(), "candidate_materialization", None)
    if scoped_service is not None:
        try:
            if Path(scoped_service.root).resolve() != PROJECT_ROOT.resolve():
                return scoped_service
        except (AttributeError, TypeError, OSError):
            pass
    return candidate_materialization_service


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

# 任务状态：task_id -> {status, progress, message, result}
TASKS: dict[str, dict] = {}


def _make_tdx(cfg: dict) -> TdxData:
    return TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))


@app.get("/")
def index() -> FileResponse:
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端未构建，请先执行 cd frontend && npm install && npm run build")
    return FileResponse(index_file)


@app.get("/api/config")
def get_config() -> dict:
    cfg = load_config()
    return {
        "tdx_vipdoc": cfg["tdx"]["vipdoc"],
        "backtest": cfg["backtest"],
        "universe": cfg["universe"],
        "index_filter": cfg.get("index_filter", {}),
        "trailing_stop": cfg.get("trailing_stop", {}),
        "report_output_dir": cfg["report"]["output_dir"],
    }


@app.post("/api/screener")
def run_screener(body: dict | None = None) -> dict:
    body = body or {}
    limit = body.get("limit") or None
    cfg = load_config()
    tdx = _make_tdx(cfg)
    df = scan_all(tdx, cfg, limit=limit)
    out_dir = Path(cfg["report"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "signals_latest.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    records = df.to_dict(orient="records") if not df.empty else []
    return {"count": len(records), "signals": records[:500], "output": str(out_path)}


@app.post("/api/backtest")
def start_backtest(body: dict | None = None) -> dict:
    body = body or {}
    limit = body.get("limit") or None
    cfg = load_config()
    # 允许前端覆盖部分回测参数
    if body.get("start"):
        cfg["backtest"]["start"] = body["start"]
    if body.get("end"):
        cfg["backtest"]["end"] = body["end"]
    if body.get("initial_cash"):
        cfg["backtest"]["initial_cash"] = float(body["initial_cash"])
    if body.get("max_positions"):
        cfg["backtest"]["max_positions"] = int(body["max_positions"])
    if body.get("max_picks_per_day") is not None:
        cfg["backtest"]["max_picks_per_day"] = int(body["max_picks_per_day"])
    cfg.setdefault("index_filter", {})["enabled"] = bool(body.get("index_filter_enabled", False))
    cfg.setdefault("trailing_stop", {})["enabled"] = bool(body.get("trailing_enabled", False))

    task_id = str(uuid.uuid4())[:8]
    TASKS[task_id] = {"status": "running", "progress": 0.0, "message": "准备数据", "result": None}

    def _run() -> None:
        task = TASKS[task_id]
        try:
            tdx = _make_tdx(cfg)

            def progress_cb(done: int, total: int) -> None:
                if total:
                    task["progress"] = round(0.9 * done / total, 4)
                task["message"] = f"扫描 {done}/{total}"

            runner = BacktestRunner(tdx, cfg)
            result = runner.run(limit=limit, progress_cb=progress_cb)
            task["progress"] = 0.95
            task["message"] = "计算绩效"
            bench = tdx.get_benchmark(cfg["backtest"].get("benchmark", "sh000300"))
            metrics = compute_metrics(result, bench)
            report_path = write_report(result, metrics, cfg["report"]["output_dir"])
            trades = [t.__dict__ for t in result["trades"]]
            task["result"] = {
                "metrics": metrics,
                "trades": trades[-1000:],
                "trade_count": len(trades),
                "report_path": report_path,
            }
            task["status"] = "done"
            task["progress"] = 1.0
            task["message"] = "完成"
        except Exception as e:  # noqa: BLE001
            task["status"] = "error"
            task["message"] = str(e)
            task["result"] = {"error": traceback.format_exc()}

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/backtest/status/{task_id}")
def backtest_status(task_id: str) -> dict:
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/kline/{code}")
def get_kline(code: str, market: int = Query(1), bars: int = 300) -> dict:
    """返回个股 K 线与缠论标记，供前端 Canvas 绘图。"""
    cfg = load_config()
    tdx = _make_tdx(cfg)
    try:
        df = tdx.get_qfq_day(code, market)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取行情失败：{e}") from e
    if df.empty:
        raise HTTPException(status_code=404, detail="没有行情数据")
    df = df.sort_values("date").reset_index(drop=True)
    df, merged, bis, signals, segments = analyze_stock(
        df, code=code,
        bi_gap=cfg.get("chan", {}).get("bi_gap", 4),
        fast=cfg.get("chan", {}).get("macd_fast", 12),
        slow=cfg.get("chan", {}).get("macd_slow", 26),
        signal=cfg.get("chan", {}).get("macd_signal", 9),
    )
    take = max(1, min(bars, len(df)))
    start_idx = len(df) - take
    kline = [
        {
            "date": int(row["date"]),
            "open": float(row["qfq_open"]),
            "high": float(row["qfq_high"]),
            "low": float(row["qfq_low"]),
            "close": float(row["qfq_close"]),
            "volume": float(row["volume"]),
            "macd": float(row["macd"]) if pd.notna(row["macd"]) else 0.0,
        }
        for _, row in df.tail(take).iterrows()
    ]
    # 只保留落在可视范围内的标记；merged 的 date 为合并组最后一根日期
    min_date = int(df.iloc[start_idx]["date"])
    merged_marks = [
        {
            "date": int(m["date"]),
            "high": float(m["high"]),
            "low": float(m["low"]),
            "open": float(m["open"]),
            "close": float(m["close"]),
        }
        for _, m in merged.iterrows()
        if int(m["date"]) >= min_date
    ]
    bi_marks = [
        {
            "start_date": int(b.start_date),
            "end_date": int(b.end_date),
            "direction": b.direction,
            "high": float(b.high),
            "low": float(b.low),
        }
        for b in bis
        if b.end_date >= min_date
    ]
    sig_marks = [
        {
            "date": int(s.signal_date),
            "type": s.signal_type,
            "direction": s.direction,
            "price": float(s.price_ref),
        }
        for s in signals
        if s.signal_date >= min_date
    ]
    seg_marks = [
        {
            "start_date": int(sg.start_date),
            "end_date": int(sg.end_date),
            "direction": sg.direction,
            "high": float(sg.high),
            "low": float(sg.low),
        }
        for sg in segments
        if sg.end_date >= min_date
    ]
    return {
        "code": code,
        "market": market,
        "kline": kline,
        "merged": merged_marks,
        "bi": bi_marks,
        "signals": sig_marks,
        "segments": seg_marks,
    }


def _console_service() -> ResearchConsoleReadService:
    return research_console_service


@app.get("/api/research-console/objectives")
def research_console_objectives() -> dict:
    return _console_service().list_objectives().to_dict()


@app.get("/api/research-console/ai-invocation-mode")
def research_console_ai_invocation_mode() -> dict:
    return ai_invocation_mode_service.get()


@app.post("/api/research-console/ai-invocation-mode")
def research_console_set_ai_invocation_mode(request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return ai_invocation_mode_service.set(str(body.get("ai_invocation_mode") or body.get("mode") or ""))


@app.get("/api/research-console/{objective_id}/dashboard")
def research_console_dashboard(objective_id: str) -> dict:
    return _console_service().get_dashboard(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/daemon")
def research_console_daemon(objective_id: str) -> dict:
    return _console_service().get_daemon(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/orchestrator")
def research_console_orchestrator(objective_id: str) -> dict:
    return dict(_console_service().get_orchestrator(objective_id))


@app.get("/api/research-console/{objective_id}/orchestrator/events")
def research_console_orchestrator_events(objective_id: str, limit: int = 50) -> dict:
    return _console_service().get_orchestrator_events(objective_id, limit=limit)


@app.get("/api/research-console/{objective_id}/ai-status")
def research_console_ai_status(objective_id: str) -> dict:
    return _console_service().get_ai_status(objective_id)


@app.get("/api/research-console/{objective_id}/manual-ai-handoff")
def research_console_manual_ai_handoff(objective_id: str) -> dict:
    return _console_service().get_manual_ai_handoff(objective_id)


@app.api_route("/api/research-console/{objective_id}/safe-runtime-context", methods=["GET", "HEAD"])
def research_console_safe_runtime_context(objective_id: str) -> dict:
    return _console_service().get_safe_runtime_context(objective_id)


@app.get("/api/research-console/{objective_id}/ai-tasks")
def research_console_ai_tasks(objective_id: str, page: int = 1, page_size: int = 20, search: str = "", status: str = "ALL", mode: str = "ALL", sort: str = "created_at", direction: str = "desc") -> dict:
    return _console_service().list_ai_tasks(objective_id, page=page, page_size=page_size, search=search, status=status, mode=mode, sort=sort, direction=direction)


@app.get("/api/research-console/{objective_id}/ai-results")
def research_console_ai_results(objective_id: str, page: int = 1, page_size: int = 20, search: str = "", sort: str = "created_at", direction: str = "desc") -> dict:
    return _console_service().list_ai_results(objective_id, page=page, page_size=page_size, search=search, sort=sort, direction=direction)


@app.post("/api/research-console/{objective_id}/manual-ai-handoff/rescan")
def research_console_manual_ai_rescan(objective_id: str, request: Request) -> dict:
    _require_local_console_request(request)
    from .research_factory.autonomous_orchestrator_v2 import AutonomousResearchOrchestratorV2

    return AutonomousResearchOrchestratorV2(PROJECT_ROOT, objective_id=objective_id).scan_manual_handoff()


@app.get("/api/research-console/{objective_id}/closeout")
def research_console_closeout(objective_id: str) -> dict:
    return _console_service().get_closeout(objective_id)


@app.get("/api/research-console/{objective_id}/governance-decision")
def research_console_governance_decision(objective_id: str) -> dict:
    return _console_service().get_governance_decision(objective_id)


@app.get("/api/research-console/{objective_id}/governance/preview")
def research_console_governance_preview(objective_id: str, action: str | None = Query(default=None), execution_mode: str = Query(default="CREATE_AND_ACTIVATE"), selected_parent_candidate_id: str | None = Query(default=None), selected_parent_candidate_hash: str | None = Query(default=None)) -> dict:
    if not action:
        return governance_execution_service.catalog(objective_id)
    return governance_execution_service.preview(objective_id, action, execution_mode=execution_mode, selected_parent_candidate_id=selected_parent_candidate_id, selected_parent_candidate_hash=selected_parent_candidate_hash)


@app.post("/api/research-console/{objective_id}/governance/preview")
def research_console_governance_preview_post(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return governance_execution_service.preview(objective_id, str(body.get("action") or body.get("governance_action") or ""), execution_mode=str(body.get("execution_mode") or "CREATE_AND_ACTIVATE"), selected_parent_candidate_id=body.get("selected_parent_candidate_id"), selected_parent_candidate_hash=body.get("selected_parent_candidate_hash"))


@app.post("/api/research-console/{objective_id}/governance/confirm")
def research_console_governance_confirm(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    receipt = governance_execution_service.confirm(objective_id, payload or {})
    if receipt.get("execution_mode") != "CREATE_AND_ACTIVATE" or not receipt.get("new_objective_id"):
        return receipt
    try:
        activation = orchestrator_process_launcher.start(str(receipt["new_objective_id"]), execution_id=str(receipt["execution_id"]))
    except OrchestratorLaunchError as exc:
        raise GovernanceExecutionError("ORCHESTRATOR_ACTIVATION_BLOCKED", exc.message_zh, status_code=503, details={"launch_code": exc.code, "governance_receipt": receipt, **exc.details}) from exc
    return {**receipt, "orchestrator_activation": activation}


@app.get("/api/research-console/{objective_id}/governance/execution/{execution_id}")
def research_console_governance_execution(objective_id: str, execution_id: str) -> dict:
    return governance_execution_service.get_execution(objective_id, execution_id)


@app.get("/api/research-console/{objective_id}/governance/execution/{execution_id}/receipt.json")
def research_console_governance_execution_receipt_file(objective_id: str, execution_id: str) -> dict:
    """Compatibility URL for the human-facing receipt link."""
    return governance_execution_service.get_execution(objective_id, execution_id)


@app.get("/api/research-console/{objective_id}/operations")
def research_console_operations(objective_id: str) -> dict:
    return _console_service().get_operations(objective_id)


def _console_operation(objective_id: str, action: str, request: Request, payload: dict[str, Any] | None) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return orchestrator_control_service.execute(objective_id, action, idempotency_key=str(body.get("idempotency_key") or ""), confirmed=bool(body.get("confirmed", False)))


@app.post("/api/research-console/{objective_id}/orchestrator/pause")
def research_console_pause(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    return _console_operation(objective_id, "PAUSE", request, payload)


@app.post("/api/research-console/{objective_id}/orchestrator/resume")
def research_console_resume(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    return _console_operation(objective_id, "RESUME", request, payload)


@app.post("/api/research-console/{objective_id}/orchestrator/stop")
def research_console_stop(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    return _console_operation(objective_id, "STOP", request, payload)


@app.post("/api/research-console/{objective_id}/orchestrator/recover")
def research_console_recover(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    return _console_operation(objective_id, "RECOVER", request, payload)


@app.post("/api/research-console/{objective_id}/orchestrator/start-ai")
def research_console_start_ai(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    return _console_operation(objective_id, "START_AI", request, payload)


@app.post("/api/research-console/{objective_id}/governance-decision")
def research_console_submit_governance(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True:
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "提交治理决定需要明确确认", status_code=400)
    return governance_decision_service.submit(objective_id, str(body.get("choice") or ""), idempotency_key=str(body.get("idempotency_key") or ""))


@app.get("/api/research-console/{objective_id}/daemon/health")
def research_console_daemon_health(objective_id: str) -> dict:
    return _console_service().get_daemon_health(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/pipeline")
def research_console_pipeline(objective_id: str) -> dict:
    return _console_service().get_pipeline(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/predictive/authorization/preview")
def research_console_predictive_governance_preview(objective_id: str, decision_type: str = Query(default="AUTHORIZE_FIRST_PREDICTIVE_TRIAL")) -> dict:
    return predictive_governance_service.preview(objective_id, decision_type)


@app.post("/api/research-console/{objective_id}/predictive/authorize")
def research_console_authorize_predictive(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return predictive_governance_service.confirm(objective_id, body)


@app.get("/api/research-console/{objective_id}/predictive/trial/start/preview")
def research_console_predictive_trial_start_preview(objective_id: str) -> dict:
    return predictive_trial_start_service.preview(objective_id)


@app.post("/api/research-console/{objective_id}/predictive/trial/start")
def research_console_predictive_trial_start(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return predictive_trial_start_service.confirm(objective_id, payload or {})


@app.get("/api/research-console/{objective_id}/predictive/trial/resume/preview")
def research_console_predictive_trial_resume_preview(objective_id: str) -> dict:
    return predictive_trial_start_service.resume_preview(objective_id)


@app.post("/api/research-console/{objective_id}/predictive/trial/resume")
def research_console_predictive_trial_resume(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return predictive_trial_start_service.confirm_resume(objective_id, payload or {})


@app.get("/api/research-console/{objective_id}/predictive/trial/new/authorization/preview")
def research_console_predictive_new_trial_authorization_preview(objective_id: str) -> dict:
    return predictive_trial_reauthorization_service.preview_authorization(objective_id)


@app.post("/api/research-console/{objective_id}/predictive/trial/new/authorize")
def research_console_predictive_new_trial_authorize(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return predictive_trial_reauthorization_service.confirm_authorization(objective_id, payload or {})


@app.get("/api/research-console/{objective_id}/predictive/trial/new/start/preview")
def research_console_predictive_new_trial_start_preview(objective_id: str) -> dict:
    return predictive_trial_reauthorization_service.preview_start_new_trial(objective_id)


@app.post("/api/research-console/{objective_id}/predictive/trial/new/start")
def research_console_predictive_new_trial_start(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return predictive_trial_reauthorization_service.confirm_start_new_trial(objective_id, payload or {})


@app.post("/api/research-console/{objective_id}/structural/reconcile")
def research_console_reconcile_structural(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True or str(body.get("action") or "").upper() != "RUN_STRUCTURAL_PREFLIGHT":
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "重新进行结构预检需要在页面中明确确认 RUN_STRUCTURAL_PREFLIGHT", status_code=400)
    return structural_entry_service.start(
        objective_id,
        candidate_id=str(body.get("candidate_id") or "") or None,
        confirmed=True,
        action="RUN_STRUCTURAL_PREFLIGHT",
    )


@app.get("/api/research-console/{objective_id}/structural/readiness")
def research_console_structural_readiness(objective_id: str) -> dict:
    return structural_entry_service.readiness(objective_id)


@app.get("/api/research-console/{objective_id}/structural/reconciliation")
def research_console_structural_reconciliation(objective_id: str) -> dict:
    return projection_reconciliation_service.read(objective_id)


@app.post("/api/research-console/{objective_id}/structural/start")
def research_console_start_structural(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True or str(body.get("action") or "RUN_STRUCTURAL_PREFLIGHT").upper() != "RUN_STRUCTURAL_PREFLIGHT":
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "启动结构预检需要明确确认 RUN_STRUCTURAL_PREFLIGHT", status_code=400)
    return structural_entry_service.start(
        objective_id,
        candidate_id=str(body.get("candidate_id") or "") or None,
        confirmed=True,
        action="RUN_STRUCTURAL_PREFLIGHT",
    )


@app.post("/api/research-console/{objective_id}/structural/projection-repair")
def research_console_repair_structural_projection(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True:
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "修复运行态 projection 需要明确确认", status_code=400)
    return projection_reconciliation_service.reconcile(objective_id, apply=True)


def _contract_correction_identity(objective_id: str, candidate_id: str) -> tuple[str, str]:
    detail = _console_service().get_candidate(objective_id, candidate_id).to_dict()
    candidate_hash = str((detail.get("identity") or {}).get("candidate_hash") or "")
    contract_ref = str((detail.get("artifact_lineage") or {}).get("contract_ref") or "")
    return candidate_hash, contract_ref


_CONTRACT_CORRECTION_REASON_ZH = {
    "TRIAL_HISTORY_PRESENT": "该候选已经存在正式 Trial 记录，不能追加执行隔离。",
    "FROZEN_CONTRACT_IS_PROVIDER_COMPATIBLE": "冻结合同可由当前 Provider 执行，无需隔离。",
    "ACTIVE_CANDIDATE_BUDGET_RESERVATION_PRESENT": "该候选仍有活动预算占用，不能追加执行隔离。",
    "CONTRACT_CORRECTION_ALREADY_APPLIED": "该冻结合同已经追加执行隔离，无需重复操作。",
    "STALE_CONTRACT_CORRECTION_PREVIEW": "确认依据已经变化，请刷新页面后重新预览。",
}


@app.get("/api/research-console/{objective_id}/candidates/{candidate_id}/contract-correction/preview")
def research_console_contract_correction_preview(objective_id: str, candidate_id: str, request: Request) -> dict:
    _require_local_console_request(request)
    candidate_hash, contract_ref = _contract_correction_identity(objective_id, candidate_id)
    try:
        preview = frozen_contract_correction_service.preview_incompatible_contract(
            objective_id=objective_id,
            candidate_id=candidate_id,
            expected_candidate_hash=candidate_hash,
            contract_ref=contract_ref,
        )
        return {**preview, "reason_zh": "冻结合同无法由当前 Provider 重建，且零 Trial、零绩效访问、零活动预算占用，可以追加执行隔离。"}
    except ContractCorrectionError as exc:
        reason_code = str(exc)
        return {
            "schema_version": "frozen-contract-correction-preview-v1",
            "status": "UNAVAILABLE",
            "available": False,
            "objective_id": objective_id,
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "contract_ref": contract_ref,
            "reason_code": reason_code,
            "reason_zh": _CONTRACT_CORRECTION_REASON_ZH.get(reason_code, "当前冻结合同不满足安全隔离条件，请检查合同身份与研究记录。"),
            "requires_confirmation": True,
        }


@app.post("/api/research-console/{objective_id}/candidates/{candidate_id}/contract-correction/confirm")
def research_console_contract_correction_confirm(objective_id: str, candidate_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True:
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "冻结合同隔离需要在页面中明确确认", status_code=400)
    candidate_hash, contract_ref = _contract_correction_identity(objective_id, candidate_id)
    try:
        return frozen_contract_correction_service.confirm_incompatible_contract(
            objective_id=objective_id,
            candidate_id=candidate_id,
            expected_candidate_hash=candidate_hash,
            contract_ref=contract_ref,
            preview_hash=str(body.get("preview_hash") or ""),
            confirmation_token=str(body.get("confirmation_token") or ""),
        )
    except ContractCorrectionError as exc:
        reason_code = str(exc)
        raise OrchestratorOperationError(
            reason_code,
            _CONTRACT_CORRECTION_REASON_ZH.get(reason_code, "冻结合同隔离未执行，请刷新页面并重新检查安全证据。"),
            status_code=409,
        ) from exc


@app.get("/api/research-console/{objective_id}/candidates")
def research_console_candidates(objective_id: str, page: int = 1, page_size: int = 50, search: str = "", status: str = "ALL", mechanism: str = "ALL", sort: str = "candidate_id", direction: str = "asc") -> dict:
    return _console_service().list_candidates(objective_id, page=page, page_size=page_size, search=search, status=status, mechanism=mechanism, sort=sort, direction=direction)


@app.get("/api/research-console/{objective_id}/candidates/{candidate_id}")
def research_console_candidate(objective_id: str, candidate_id: str) -> dict:
    return _console_service().get_candidate(objective_id, candidate_id).to_dict()


@app.get("/api/research-console/{objective_id}/candidates/{candidate_id}/structural")
def research_console_candidate_structural(objective_id: str, candidate_id: str) -> dict:
    return _console_service().get_structural(objective_id, candidate_id).to_dict()


@app.get("/api/research-console/{objective_id}/trials")
def research_console_trials(objective_id: str, page: int = 1, page_size: int = 50, search: str = "", status: str = "ALL", classification: str = "ALL", sort: str = "trial_id", direction: str = "asc") -> dict:
    return _console_service().list_trials(objective_id, page=page, page_size=page_size, search=search, status=status, classification=classification, sort=sort, direction=direction)


@app.get("/api/research-console/{objective_id}/trials/{trial_id}")
def research_console_trial(objective_id: str, trial_id: str) -> dict:
    return _console_service().get_trial(objective_id, trial_id).to_dict()


_TRIAL_RECONCILIATION_REASON_ZH = {
    "CANONICAL_TRIAL_ALREADY_TERMINAL": "该 Trial 已经形成 canonical 终态，无需再次对账。",
    "CANONICAL_TRIAL_EVIDENCE_RECOVERY_REQUIRED": "该 Trial 已存在绩效证据，必须走证据恢复与最终裁决流程，不能按工程中断终结。",
    "CANONICAL_TRIAL_NOT_ACCESSED_INCOMPLETE": "只有已访问绩效但尚未完成的 Trial 才能使用此对账入口。",
    "CANONICAL_TRIAL_RECONCILIATION_NOT_REQUIRED": "当前 daemon 检查点没有要求处理该 Trial。",
    "CANONICAL_TRIAL_CHECKPOINT_IDENTITY_MISMATCH": "页面 Trial 与当前中断检查点不一致，请刷新后重试。",
    "CANONICAL_TRIAL_REGISTRY_MISSING": "canonical Trial 登记册缺失，不能仅凭单一账本执行对账。",
    "CANONICAL_TRIAL_BUDGET_IDENTITY_UNRECONCILABLE": "该 Trial 的预算预留状态无法安全对账。",
    "STALE_CANONICAL_TRIAL_RECONCILIATION_PREVIEW": "Trial 或预算依据已经变化，请刷新页面后重新预览。",
}


@app.get("/api/research-console/{objective_id}/trials/{trial_id}/reconciliation/preview")
def research_console_trial_reconciliation_preview(objective_id: str, trial_id: str, request: Request) -> dict:
    _require_local_console_request(request)
    try:
        preview = canonical_trial_reconciliation_service.preview(objective_id=objective_id, trial_id=trial_id)
        return {**preview, "reason_zh": "绩效已访问但 Trial 尚未完成；可在不重跑绩效、不创建新 Trial 的前提下追加工程中断终态并结算既有预算。"}
    except TrialReconciliationError as exc:
        reason_code = str(exc)
        return {
            "schema_version": "canonical-trial-reconciliation-preview-v1",
            "status": "UNAVAILABLE",
            "available": False,
            "objective_id": objective_id,
            "trial_id": trial_id,
            "reason_code": reason_code,
            "reason_zh": _TRIAL_RECONCILIATION_REASON_ZH.get(reason_code, "当前 Trial 不满足安全对账条件，请检查身份、预算和 daemon 检查点。"),
            "requires_confirmation": True,
        }


@app.post("/api/research-console/{objective_id}/trials/{trial_id}/reconciliation/confirm")
def research_console_trial_reconciliation_confirm(objective_id: str, trial_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    if body.get("confirmed") is not True:
        raise OrchestratorOperationError("CONFIRMATION_REQUIRED", "canonical Trial 对账需要在页面中明确确认", status_code=400)
    try:
        return canonical_trial_reconciliation_service.confirm(
            objective_id=objective_id,
            trial_id=trial_id,
            preview_hash=str(body.get("preview_hash") or ""),
            confirmation_token=str(body.get("confirmation_token") or ""),
        )
    except TrialReconciliationError as exc:
        reason_code = str(exc)
        raise OrchestratorOperationError(
            reason_code,
            _TRIAL_RECONCILIATION_REASON_ZH.get(reason_code, "canonical Trial 对账未执行，请刷新页面后重新检查。"),
            status_code=409,
        ) from exc


@app.get("/api/research-console/{objective_id}/budget")
def research_console_budget(objective_id: str) -> dict:
    return _console_service().get_budget(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/handoff")
def research_console_handoff(objective_id: str) -> dict:
    return _console_service().get_handoff(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/shadow/latest")
def research_console_shadow_latest(objective_id: str) -> dict:
    return _console_service().get_shadow_latest(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/data-health")
def research_console_data_health(objective_id: str) -> dict:
    return _console_service().get_data_health(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/evolution")
def research_console_evolution(objective_id: str) -> dict:
    return _console_service().get_evolution(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/evolution/proposals")
def research_console_evolution_proposals(objective_id: str) -> dict:
    return _console_service().get_evolution_proposals(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/evolution/ai-design")
def research_console_evolution_ai_design(objective_id: str) -> dict:
    return _console_service().get_evolution_ai_design(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/evolution/ai-design/approval")
def research_console_evolution_ai_design_approval(objective_id: str) -> dict:
    return _console_ai_design_approval_service().evaluate(objective_id)


@app.post("/api/research-console/{objective_id}/evolution/ai-design/approval")
def research_console_confirm_evolution_ai_design(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return _console_ai_design_approval_service().confirm(objective_id, payload or {})


@app.get("/api/research-console/{objective_id}/candidate-proposals")
def research_console_candidate_proposals(objective_id: str) -> dict:
    return _console_service().get_candidate_proposals(objective_id).to_dict()


@app.post("/api/research-console/{objective_id}/candidate-proposals/generate")
def research_console_generate_candidate_proposal(objective_id: str, request: Request) -> dict:
    _require_local_console_request(request)
    return _console_candidate_generation_service().generate_proposal(objective_id)


@app.get("/api/research-console/{objective_id}/candidate-proposals/materialization")
def research_console_candidate_materialization(objective_id: str, proposal_id: str | None = Query(default=None)) -> dict:
    return _console_candidate_materialization_service().read_for_objective(objective_id, proposal_id)


@app.post("/api/research-console/{objective_id}/candidate-proposals/materialization/preview")
def research_console_create_candidate_materialization_preview(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    proposal_id = str(body.get("proposal_id") or "") or None
    return _console_candidate_materialization_service().create_preview(objective_id, proposal_id)


@app.post("/api/research-console/{objective_id}/candidate-proposals/materialization/confirm")
def research_console_confirm_candidate_materialization(objective_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    proposal_id = str(body.get("proposal_id") or "")
    if not proposal_id:
        raise CandidateExecutableMaterializationError("CANDIDATE_PROPOSAL_REQUIRED", "确认执行合同必须明确 Candidate Proposal", status_code=400)
    return _console_candidate_materialization_service().confirm(objective_id, proposal_id, body)


@app.get("/api/research/evolution/proposals")
def research_evolution_proposals(objective_id: str | None = Query(default=None), status: str | None = Query(default=None)) -> dict:
    return research_proposal_governance_service.list_proposals(objective_id=objective_id, status=status)


@app.get("/api/research/evolution/proposals/{proposal_id}")
@app.get("/research/evolution/proposals/{proposal_id}")
def research_evolution_proposal(proposal_id: str) -> dict:
    return research_proposal_governance_service.get_proposal(proposal_id)


@app.post("/api/research/evolution/proposals/{proposal_id}/review")
@app.post("/research/evolution/proposals/{proposal_id}/review")
def research_evolution_proposal_review(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return research_proposal_governance_service.review(proposal_id, payload or {})


@app.post("/api/research/evolution/proposals/{proposal_id}/close")
@app.post("/research/evolution/proposals/{proposal_id}/close")
def research_evolution_proposal_close(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return research_proposal_governance_service.close(
        proposal_id,
        reviewer=str(body.get("reviewer") or body.get("confirmer") or ""),
        reason=str(body.get("reason") or "") or None,
    )


@app.get("/api/research/evolution/proposals/{proposal_id}/preview")
@app.get("/research/evolution/proposals/{proposal_id}/preview")
def research_evolution_proposal_preview(proposal_id: str) -> dict:
    return research_proposal_governance_service.preview(proposal_id)


@app.post("/api/research/evolution/proposals/{proposal_id}/confirm")
@app.post("/research/evolution/proposals/{proposal_id}/confirm")
def research_evolution_proposal_confirm(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return research_proposal_governance_service.confirm(proposal_id, payload or {})


@app.get("/api/research/candidates/proposals")
def research_candidate_proposals(objective_id: str | None = Query(default=None), status: str | None = Query(default=None)) -> dict:
    return candidate_generation_service.list_proposals(objective_id=objective_id, status=status)


@app.get("/api/research/candidates/proposals/{proposal_id}/freeze-preview")
@app.get("/research/candidates/proposals/{proposal_id}/freeze-preview")
def research_candidate_proposal_freeze_preview(proposal_id: str) -> dict:
    return candidate_generation_service.get_freeze_preview(proposal_id)


@app.get("/api/research/candidates/proposals/{proposal_id}/materialization")
@app.get("/research/candidates/proposals/{proposal_id}/materialization")
def research_candidate_materialization(proposal_id: str) -> dict:
    service = candidate_materialization_service
    return service.read_for_objective(service.objective_id_for_proposal(proposal_id), proposal_id)


@app.get("/api/research/candidates/proposals/{proposal_id}/materialization/preview")
@app.get("/research/candidates/proposals/{proposal_id}/materialization/preview")
def research_candidate_materialization_preview(proposal_id: str) -> dict:
    service = candidate_materialization_service
    state = service.read_for_objective(service.objective_id_for_proposal(proposal_id), proposal_id)
    return state.get("preview") if isinstance(state.get("preview"), dict) else state


@app.post("/api/research/candidates/proposals/{proposal_id}/materialization/preview")
@app.post("/research/candidates/proposals/{proposal_id}/materialization/preview")
def research_create_candidate_materialization_preview(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    service = candidate_materialization_service
    return service.create_preview(service.objective_id_for_proposal(proposal_id), proposal_id)


@app.post("/api/research/candidates/proposals/{proposal_id}/materialization/confirm")
@app.post("/research/candidates/proposals/{proposal_id}/materialization/confirm")
def research_confirm_candidate_materialization(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    service = candidate_materialization_service
    return service.confirm(service.objective_id_for_proposal(proposal_id), proposal_id, payload or {})


@app.get("/api/research/candidates/proposals/{proposal_id}")
@app.get("/research/candidates/proposals/{proposal_id}")
def research_candidate_proposal(proposal_id: str) -> dict:
    return candidate_generation_service.get_proposal(proposal_id)


@app.post("/api/research/candidates/proposals/{proposal_id}/review")
def research_candidate_proposal_review(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return candidate_generation_service.review(proposal_id, payload or {})


@app.post("/api/research/candidates/proposals/{proposal_id}/freeze")
@app.post("/research/candidates/proposals/{proposal_id}/freeze")
def research_candidate_proposal_freeze(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    return candidate_generation_service.freeze(proposal_id, payload or {})


@app.post("/api/research/candidates/proposals/{proposal_id}/close")
def research_candidate_proposal_close(proposal_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)) -> dict:
    _require_local_console_request(request)
    body = payload or {}
    return candidate_generation_service.close(
        proposal_id,
        reviewer=str(body.get("reviewer") or body.get("confirmer") or ""),
        reason=str(body.get("reason") or "") or None,
    )


@app.get("/api/research-console/{objective_id}/reports")
def research_console_reports(objective_id: str) -> dict:
    return _console_service().get_reports(objective_id).to_dict()


@app.get("/api/research-console/{objective_id}/reports/{report_id}")
def research_console_report(objective_id: str, report_id: str) -> dict:
    return _console_service().read_report(objective_id, report_id)


@app.get("/api/research-console/{objective_id}/governance")
def research_console_governance(objective_id: str) -> dict:
    return _console_service().get_governance(objective_id).to_dict()


@app.get("/{frontend_path:path}")
def frontend_history_fallback(frontend_path: str) -> FileResponse:
    """Serve the Vue shell for history-mode routes after all API routes."""
    if frontend_path == "api" or frontend_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端未构建，请先执行 cd frontend && npm run build")
    return FileResponse(index_file)
