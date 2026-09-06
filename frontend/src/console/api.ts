import type {
  CandidateDetailView,
  ContractCorrectionPreview,
  CandidateListResponse,
  DaemonHealthView,
  DaemonStatusView,
  DashboardView,
  AutonomousControlPlaneView,
  DataHealthView,
  NoOutcomeHandoffView,
  ManualAIHandoffView,
  AIResearchTaskListResponse,
  AIInvocationModeView,
  AIStatusView,
  CloseoutView,
  GovernanceDecisionView,
  GovernanceExecutionPreview,
  GovernanceExecutionReceipt,
  GovernancePreviewCatalog,
  GovernancePreviewRequest,
  OperationResult,
  OperationsView,
  OrchestratorEventsView,
  OrchestratorStatusView,
  ResearchPipelineView,
  ResearchObjectiveListView,
  ReportIndexView,
  ResearchEvolutionView,
  ResearchEvolutionProposalView,
  ResearchEvolutionAIDesignView,
  CandidateProposalView,
  CandidateProposalFreezeResult,
  CandidateProposalReviewResult,
  CandidateExecutableMaterializationResult,
  ObjectiveCreationPreview,
  ProposalGovernanceReviewResult,
  SearchBudgetView,
  ShadowDailyView,
  StructuralPreflightView,
  TrialDetailView,
  TrialListResponse,
  TrialReconciliationPreview,
  PredictiveAuthorizationPreview,
  PredictiveTrialStartPreview,
  PredictiveTrialResumePreview,
} from './types'

export const DEFAULT_OBJECTIVE_ID = 'RESEARCH_OBJECTIVE_GOVERNED_PROMISING_FOLLOWUP_V1_97AA9C76F4453762D2BF'

export class ConsoleApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code = 'CONSOLE_READ_ERROR', status = 500) {
    super(message)
    this.name = 'ConsoleApiError'
    this.code = code
    this.status = status
  }
}

export async function api<T>(url: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ConsoleApiError('数据读取失败，请确认本地服务仍在运行。', 'NETWORK_ERROR', 0)
  }

  if (!response.ok) {
    let code = 'CONSOLE_READ_ERROR'
    let message = '数据读取失败，请稍后重试。'
    try {
      const body = await response.json() as { code?: string; message_zh?: string }
      code = body.code || code
      message = body.message_zh || message
    } catch {
      // Keep the safe Chinese message when the backend does not return an error envelope.
    }
    throw new ConsoleApiError(message, code, response.status)
  }
  return await response.json() as T
}

export async function mutate<T>(url: string, body: Record<string, unknown>): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ConsoleApiError('操作请求失败，请确认本地服务仍在运行。', 'NETWORK_ERROR', 0)
  }
  if (!response.ok) {
    let code = 'CONSOLE_OPERATION_ERROR'
    let message = '操作未完成，请查看当前研究状态。'
    try {
      const payload = await response.json() as { code?: string; message_zh?: string }
      code = payload.code || code
      message = payload.message_zh || message
    } catch {
      // Keep the safe Chinese message when the backend does not return an error envelope.
    }
    throw new ConsoleApiError(message, code, response.status)
  }
  return await response.json() as T
}

function endpoint(objectiveId: string, suffix: string): string {
  return `/api/research-console/${encodeURIComponent(objectiveId)}${suffix}`
}

export const consoleApi = {
  objectives: (signal?: AbortSignal) => api<ResearchObjectiveListView>('/api/research-console/objectives', signal),
  aiInvocationMode: (signal?: AbortSignal) => api<AIInvocationModeView>('/api/research-console/ai-invocation-mode', signal),
  dashboard: (objectiveId: string, signal?: AbortSignal) => api<DashboardView>(endpoint(objectiveId, '/dashboard'), signal),
  autonomousControlPlane: (objectiveId: string, signal?: AbortSignal) => api<AutonomousControlPlaneView>(endpoint(objectiveId, '/autonomous-control-plane'), signal),
  autonomousControlPlaneTick: (objectiveId: string, body: Record<string, unknown> = {}) => mutate<AutonomousControlPlaneView>(endpoint(objectiveId, '/autonomous-control-plane/tick'), body),
  daemon: (objectiveId: string, signal?: AbortSignal) => api<DaemonStatusView>(endpoint(objectiveId, '/daemon'), signal),
  daemonHealth: (objectiveId: string, signal?: AbortSignal) => api<DaemonHealthView>(endpoint(objectiveId, '/daemon/health'), signal),
  pipeline: (objectiveId: string, signal?: AbortSignal) => api<ResearchPipelineView>(endpoint(objectiveId, '/pipeline'), signal),
  predictiveAuthorizationPreview: (objectiveId: string, decisionType = 'AUTHORIZE_FIRST_PREDICTIVE_TRIAL', signal?: AbortSignal) => api<PredictiveAuthorizationPreview>(endpoint(objectiveId, `/predictive/authorization/preview?decision_type=${encodeURIComponent(decisionType)}`), signal),
  reconcileStructural: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/structural/reconcile'), body),
  authorizePredictive: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/predictive/authorize'), body),
  predictiveTrialStartPreview: (objectiveId: string, signal?: AbortSignal) => api<PredictiveTrialStartPreview>(endpoint(objectiveId, '/predictive/trial/start/preview'), signal),
  startPredictiveTrial: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/predictive/trial/start'), body),
  predictiveTrialResumePreview: (objectiveId: string, signal?: AbortSignal) => api<PredictiveTrialResumePreview>(endpoint(objectiveId, '/predictive/trial/resume/preview'), signal),
  resumePredictiveTrial: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/predictive/trial/resume'), body),
  candidates: (objectiveId: string, pageSize = 100, signal?: AbortSignal, query: Record<string, string> = {}) => api<CandidateListResponse>(endpoint(objectiveId, `/candidates?page=${query.page || 1}&page_size=${pageSize}&search=${encodeURIComponent(query.search || '')}&status=${encodeURIComponent(query.status || 'ALL')}&mechanism=${encodeURIComponent(query.mechanism || 'ALL')}&sort=${encodeURIComponent(query.sort || 'candidate_id')}&direction=${query.direction || 'asc'}`), signal),
  candidate: (objectiveId: string, candidateId: string, signal?: AbortSignal) => api<CandidateDetailView>(endpoint(objectiveId, `/candidates/${encodeURIComponent(candidateId)}`), signal),
  contractCorrectionPreview: (objectiveId: string, candidateId: string, signal?: AbortSignal) => api<ContractCorrectionPreview>(endpoint(objectiveId, `/candidates/${encodeURIComponent(candidateId)}/contract-correction/preview`), signal),
  confirmContractCorrection: (objectiveId: string, candidateId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, `/candidates/${encodeURIComponent(candidateId)}/contract-correction/confirm`), body),
  structural: (objectiveId: string, candidateId: string, signal?: AbortSignal) => api<StructuralPreflightView>(endpoint(objectiveId, `/candidates/${encodeURIComponent(candidateId)}/structural`), signal),
  trials: (objectiveId: string, pageSize = 100, signal?: AbortSignal, query: Record<string, string> = {}) => api<TrialListResponse>(endpoint(objectiveId, `/trials?page=${query.page || 1}&page_size=${pageSize}&search=${encodeURIComponent(query.search || '')}&status=${encodeURIComponent(query.status || 'ALL')}&classification=${encodeURIComponent(query.classification || 'ALL')}&sort=${encodeURIComponent(query.sort || 'trial_id')}&direction=${query.direction || 'asc'}`), signal),
  trial: (objectiveId: string, trialId: string, signal?: AbortSignal) => api<TrialDetailView>(endpoint(objectiveId, `/trials/${encodeURIComponent(trialId)}`), signal),
  trialReconciliationPreview: (objectiveId: string, trialId: string, signal?: AbortSignal) => api<TrialReconciliationPreview>(endpoint(objectiveId, `/trials/${encodeURIComponent(trialId)}/reconciliation/preview`), signal),
  confirmTrialReconciliation: (objectiveId: string, trialId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, `/trials/${encodeURIComponent(trialId)}/reconciliation/confirm`), body),
  budget: (objectiveId: string, signal?: AbortSignal) => api<SearchBudgetView>(endpoint(objectiveId, '/budget'), signal),
  handoff: (objectiveId: string, signal?: AbortSignal) => api<NoOutcomeHandoffView>(endpoint(objectiveId, '/handoff'), signal),
  orchestrator: (objectiveId: string, signal?: AbortSignal) => api<OrchestratorStatusView>(endpoint(objectiveId, '/orchestrator'), signal),
  orchestratorEvents: (objectiveId: string, limit = 50, signal?: AbortSignal) => api<OrchestratorEventsView>(endpoint(objectiveId, `/orchestrator/events?limit=${limit}`), signal),
  aiStatus: (objectiveId: string, signal?: AbortSignal) => api<AIStatusView>(endpoint(objectiveId, '/ai-status'), signal),
  manualAiHandoff: (objectiveId: string, signal?: AbortSignal) => api<ManualAIHandoffView>(endpoint(objectiveId, '/manual-ai-handoff'), signal),
  aiTasks: (objectiveId: string, page = 1, pageSize = 20, search = '', signal?: AbortSignal, query: Record<string, string> = {}) => api<AIResearchTaskListResponse>(endpoint(objectiveId, `/ai-tasks?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}&status=${encodeURIComponent(query.status || 'ALL')}&mode=${encodeURIComponent(query.mode || 'ALL')}&sort=${encodeURIComponent(query.sort || 'created_at')}&direction=${query.direction || 'desc'}`), signal),
  aiResults: (objectiveId: string, page = 1, pageSize = 20, search = '', signal?: AbortSignal) => api<AIResearchTaskListResponse>(endpoint(objectiveId, `/ai-results?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}`), signal),
  closeout: (objectiveId: string, signal?: AbortSignal) => api<CloseoutView>(endpoint(objectiveId, '/closeout'), signal),
  governanceDecision: (objectiveId: string, signal?: AbortSignal) => api<GovernanceDecisionView>(endpoint(objectiveId, '/governance-decision'), signal),
  governancePreviewCatalog: (objectiveId: string, signal?: AbortSignal) => api<GovernancePreviewCatalog>(endpoint(objectiveId, '/governance/preview'), signal),
  governancePreview: (objectiveId: string, body: GovernancePreviewRequest) => mutate<GovernanceExecutionPreview>(endpoint(objectiveId, '/governance/preview'), body),
  confirmGovernanceExecution: (objectiveId: string, body: Record<string, unknown>) => mutate<GovernanceExecutionReceipt>(endpoint(objectiveId, '/governance/confirm'), body),
  governanceExecution: (objectiveId: string, executionId: string, signal?: AbortSignal) => api<GovernanceExecutionReceipt>(endpoint(objectiveId, `/governance/execution/${encodeURIComponent(executionId)}`), signal),
  operations: (objectiveId: string, signal?: AbortSignal) => api<OperationsView>(endpoint(objectiveId, '/operations'), signal),
  shadow: (objectiveId: string, signal?: AbortSignal) => api<ShadowDailyView>(endpoint(objectiveId, '/shadow/latest'), signal),
  dataHealth: (objectiveId: string, signal?: AbortSignal) => api<DataHealthView>(endpoint(objectiveId, '/data-health'), signal),
  evolution: (objectiveId: string, signal?: AbortSignal) => api<ResearchEvolutionView>(endpoint(objectiveId, '/evolution'), signal),
  evolutionProposals: (objectiveId: string, signal?: AbortSignal) => api<ResearchEvolutionProposalView>(endpoint(objectiveId, '/evolution/proposals'), signal),
  evolutionAIDesign: (objectiveId: string, signal?: AbortSignal) => api<ResearchEvolutionAIDesignView>(endpoint(objectiveId, '/evolution/ai-design'), signal),
  confirmEvolutionAIDesign: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/evolution/ai-design/approval'), body),
  generateCandidateProposal: (objectiveId: string) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/candidate-proposals/generate'), {}),
  candidateProposals: (objectiveId: string, signal?: AbortSignal) => api<CandidateProposalView>(endpoint(objectiveId, '/candidate-proposals'), signal),
  proposalList: (objectiveId?: string, status?: string, signal?: AbortSignal) => api<Record<string, unknown>>(`/api/research/evolution/proposals${objectiveId ? `?objective_id=${encodeURIComponent(objectiveId)}${status ? `&status=${encodeURIComponent(status)}` : ''}` : status ? `?status=${encodeURIComponent(status)}` : ''}`, signal),
  proposal: (proposalId: string, signal?: AbortSignal) => api<Record<string, unknown>>(`/api/research/evolution/proposals/${encodeURIComponent(proposalId)}`, signal),
  reviewProposal: (proposalId: string, body: Record<string, unknown>) => mutate<ProposalGovernanceReviewResult>(`/api/research/evolution/proposals/${encodeURIComponent(proposalId)}/review`, body),
  closeProposal: (proposalId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(`/api/research/evolution/proposals/${encodeURIComponent(proposalId)}/close`, body),
  proposalPreview: (proposalId: string, signal?: AbortSignal) => api<ObjectiveCreationPreview>(`/api/research/evolution/proposals/${encodeURIComponent(proposalId)}/preview`, signal),
  confirmProposal: (proposalId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(`/api/research/evolution/proposals/${encodeURIComponent(proposalId)}/confirm`, body),
  candidateProposalReview: (proposalId: string, body: Record<string, unknown>) => mutate<CandidateProposalReviewResult>(`/api/research/candidates/proposals/${encodeURIComponent(proposalId)}/review`, body),
  candidateProposalFreezePreview: (proposalId: string, signal?: AbortSignal) => api<Record<string, unknown>>(`/api/research/candidates/proposals/${encodeURIComponent(proposalId)}/freeze-preview`, signal),
  candidateProposalFreeze: (proposalId: string, body: Record<string, unknown>) => mutate<CandidateProposalFreezeResult>(`/api/research/candidates/proposals/${encodeURIComponent(proposalId)}/freeze`, body),
  candidateMaterialization: (objectiveId: string, proposalId?: string, signal?: AbortSignal) => api<CandidateExecutableMaterializationResult>(endpoint(objectiveId, `/candidate-proposals/materialization${proposalId ? `?proposal_id=${encodeURIComponent(proposalId)}` : ''}`), signal),
  createExecutableMaterializationPreview: (objectiveId: string, body: Record<string, unknown>) => mutate<CandidateExecutableMaterializationResult>(endpoint(objectiveId, '/candidate-proposals/materialization/preview'), body),
  confirmExecutableMaterialization: (objectiveId: string, body: Record<string, unknown>) => mutate<CandidateExecutableMaterializationResult>(endpoint(objectiveId, '/candidate-proposals/materialization/confirm'), body),
  reports: (objectiveId: string, signal?: AbortSignal) => api<ReportIndexView>(endpoint(objectiveId, '/reports'), signal),
  report: (objectiveId: string, reportId: string, signal?: AbortSignal) => api<Record<string, unknown>>(endpoint(objectiveId, `/reports/${encodeURIComponent(reportId)}`), signal),
  pause: (objectiveId: string, body: Record<string, unknown>) => mutate<OperationResult>(endpoint(objectiveId, '/orchestrator/pause'), body),
  resume: (objectiveId: string, body: Record<string, unknown>) => mutate<OperationResult>(endpoint(objectiveId, '/orchestrator/resume'), body),
  stop: (objectiveId: string, body: Record<string, unknown>) => mutate<OperationResult>(endpoint(objectiveId, '/orchestrator/stop'), body),
  recover: (objectiveId: string, body: Record<string, unknown>) => mutate<OperationResult>(endpoint(objectiveId, '/orchestrator/recover'), body),
  startAi: (objectiveId: string, body: Record<string, unknown>) => mutate<OperationResult>(endpoint(objectiveId, '/orchestrator/start-ai'), body),
  rescanManualAi: (objectiveId: string) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/manual-ai-handoff/rescan'), {}),
  setAiInvocationMode: (mode: string) => mutate<AIInvocationModeView>('/api/research-console/ai-invocation-mode', { ai_invocation_mode: mode }),
  submitGovernance: (objectiveId: string, body: Record<string, unknown>) => mutate<Record<string, unknown>>(endpoint(objectiveId, '/governance-decision'), body),
}
