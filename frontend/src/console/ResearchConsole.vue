<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ConsoleApiError, consoleApi, DEFAULT_OBJECTIVE_ID } from './api'
import type { AIInvocationModeView, AIResearchTaskListResponse, AIStatusView, CandidateDetailView, CandidateListResponse, CandidateProposalView, CandidateSummaryView, CloseoutView, ContractCorrectionPreview, DaemonHealthView, DaemonStatusView, DashboardView, DataHealthView, GovernanceActionChoice, GovernanceChoice, GovernanceDecisionView, GovernanceExecutionPreview, GovernanceExecutionReceipt, GovernancePreviewCatalog, ManualAIHandoffView, NoOutcomeHandoffView, OperationResult, OperationsView, OrchestratorEventView, OrchestratorEventsView, OrchestratorStatusView, ParentCandidateIdentityRef, PredictiveAuthorizationPreview, PredictiveTrialResumePreview, PredictiveTrialStartPreview, ResearchEvolutionAIDesignView, ResearchEvolutionProposalView, ResearchEvolutionView, ResearchObjectiveListView, ResearchObjectiveSummaryView, ResearchPipelineView, ReportIndexView, SearchBudgetView, ShadowDailyView, StructuralPreflightView, TrialDetailView, TrialReconciliationPreview, TrialSummaryView } from './types'
import { candidatePresentation, displayClassification, displayExecutionFeasibility, displayFactor, displayMechanism, displayPIT, displayReason, displayState, formatBytes, formatCoverage, formatDate, freshnessLabel, humanReportCategory, humanReportTitle, objectivePresentation, sourceLabel, stageLabel, termHelp, timingLabel } from './presentation'
import CandidateDisplayName from './components/CandidateDisplayName.vue'
import CandidateId from './components/CandidateId.vue'
import ClassificationBadge from './components/ClassificationBadge.vue'
import FactorDisplay from './components/FactorDisplay.vue'
import FreshnessBadge from './components/FreshnessBadge.vue'
import HumanActionPanel from './components/HumanActionPanel.vue'
import HumanDecisionEvidence from './components/HumanDecisionEvidence.vue'
import HumanPerformanceEvidence from './components/HumanPerformanceEvidence.vue'
import HumanStatisticalEvidence from './components/HumanStatisticalEvidence.vue'
import MechanismDisplay from './components/MechanismDisplay.vue'
import PipelineStepper from './components/PipelineStepper.vue'
import ReasonCodeDisplay from './components/ReasonCodeDisplay.vue'
import ResearchStatusDisplay from './components/ResearchStatusDisplay.vue'
import ResearchEvolutionAIDesign from './components/ResearchEvolutionAIDesign.vue'
import ResearchEvolutionProposals from './components/ResearchEvolutionProposals.vue'
import CandidateProposalGovernance from './components/CandidateProposalGovernance.vue'
import BudgetMeter from './components/BudgetMeter.vue'
import TechnicalDetails from './components/TechnicalDetails.vue'

const props = defineProps<{ path: string }>()
const emit = defineEmits<{ navigate: [path: string] }>()

const objectiveIdQuery = new URLSearchParams(window.location.search).get('objective_id')
const objectiveIdExplicit = Boolean(objectiveIdQuery || DEFAULT_OBJECTIVE_ID)
const objectiveId = ref(objectiveIdQuery || DEFAULT_OBJECTIVE_ID)
const objectiveList = ref<ResearchObjectiveListView | null>(null)
const dashboard = ref<DashboardView | null>(null)
const daemon = ref<DaemonStatusView | null>(null)
const daemonHealth = ref<DaemonHealthView | null>(null)
const pipeline = ref<ResearchPipelineView | null>(null)
const candidates = ref<CandidateSummaryView[]>([])
const candidateScopeSummary = ref<CandidateListResponse['scope_summary'] | null>(null)
const candidateDetail = ref<CandidateDetailView | null>(null)
const structural = ref<StructuralPreflightView | null>(null)
const contractCorrectionPreview = ref<ContractCorrectionPreview | null>(null)
const trials = ref<TrialSummaryView[]>([])
const trialDetail = ref<TrialDetailView | null>(null)
const trialReconciliationPreview = ref<TrialReconciliationPreview | null>(null)
const budget = ref<SearchBudgetView | null>(null)
const handoff = ref<NoOutcomeHandoffView | null>(null)
const shadow = ref<ShadowDailyView | null>(null)
const dataHealth = ref<DataHealthView | null>(null)
const reports = ref<ReportIndexView | null>(null)
const evolution = ref<ResearchEvolutionView | null>(null)
const evolutionProposals = ref<ResearchEvolutionProposalView | null>(null)
const evolutionAIDesign = ref<ResearchEvolutionAIDesignView | null>(null)
const candidateProposals = ref<CandidateProposalView | null>(null)
const openedReport = ref<Record<string, unknown> | null>(null)
const orchestrator = ref<OrchestratorStatusView | null>(null)
const orchestratorEvents = ref<OrchestratorEventsView | null>(null)
const aiStatus = ref<AIStatusView | null>(null)
const manualAiHandoff = ref<ManualAIHandoffView | null>(null)
const aiTasks = ref<AIResearchTaskListResponse | null>(null)
const aiResults = ref<AIResearchTaskListResponse | null>(null)
const aiInvocationMode = ref<AIInvocationModeView | null>(null)
const selectedAiMode = ref('MANUAL_HANDOFF')
const aiModeBusy = ref(false)
const manualRescanBusy = ref(false)
const aiTaskStatus = ref('ALL')
const aiTaskMode = ref('ALL')
const aiTaskSort = ref('created_at')
const aiTaskDirection = ref('desc')
const aiTaskPage = ref(1)
const closeout = ref<CloseoutView | null>(null)
const governance = ref<GovernanceDecisionView | null>(null)
const governanceCatalog = ref<GovernancePreviewCatalog | null>(null)
const governancePreview = ref<GovernanceExecutionPreview | null>(null)
const governanceReceipt = ref<GovernanceExecutionReceipt | null>(null)
const predictiveGovernancePreview = ref<PredictiveAuthorizationPreview | null>(null)
const predictiveAuthorizationRequestId = ref('')
const predictiveTrialStartPreview = ref<PredictiveTrialStartPreview | null>(null)
const predictiveTrialStartIntentId = ref('')
const predictiveTrialResumePreview = ref<PredictiveTrialResumePreview | null>(null)
const predictiveTrialResumeConfirmOpen = ref(false)
const selectedGovernanceParentId = ref('')
const governanceConfirmOpen = ref(false)
const structuralReconciliationConfirmOpen = ref(false)
const predictiveAuthorizationConfirmOpen = ref(false)
const predictiveTrialStartConfirmOpen = ref(false)
const contractCorrectionConfirmOpen = ref(false)
const trialReconciliationConfirmOpen = ref(false)
const governanceSearch = ref('')
const governanceSort = ref('created_at')
const governanceDirection = ref('desc')
const governancePage = ref(1)
const closeoutSearch = ref('')
const closeoutFilter = ref('ALL')
const closeoutSort = ref('candidate_id')
const closeoutDirection = ref('asc')
const closeoutPage = ref(1)
const operations = ref<OperationsView | null>(null)
const toasts = ref<{ id: string; title: string; body: string }[]>([])
const pendingOperation = ref<string | null>(null)
const pendingGovernance = ref<GovernanceActionChoice | null>(null)
const operationBusy = ref(false)
const governanceBusy = ref(false)
const structuralReconciliationBusy = ref(false)
const predictiveAuthorizationBusy = ref(false)
const predictiveTrialStartBusy = ref(false)
const predictiveTrialResumeBusy = ref(false)
const contractCorrectionBusy = ref(false)
const trialReconciliationBusy = ref(false)
let seenEventIds = new Set<string>()
let eventsInitialized = false

const loading = ref(false)
const refreshing = ref(false)
const pageError = ref<ConsoleApiError | null>(null)
const objectiveSearch = ref('')
const objectiveFilter = ref('ALL')
const objectiveSort = ref('created_at')
const objectiveDirection = ref('desc')
const objectivePage = ref(1)
const searchText = ref('')
const candidateFilter = ref('ALL')
const mechanismFilter = ref('ALL')
const candidateSort = ref('candidate_id')
const candidateDirection = ref('asc')
const candidatePage = ref(1)
const trialSearch = ref('')
const trialFilter = ref('ALL')
const trialSort = ref('trial_id')
const trialDirection = ref('asc')
const trialPage = ref(1)
const reportFilter = ref('全部')
const reportSearch = ref('')
const reportSort = ref('date')
const reportDirection = ref('desc')
const reportPage = ref(1)
let controller: AbortController | null = null
let pollTimer: number | undefined
let requestActive = false

const view = computed(() => {
  const path = props.path.replace(/\/$/, '') || '/research'
  if (path === '/research') return 'autonomous'
  if (path === '/research/objectives') return 'objectives'
  if (path === '/research/ai-researcher') return 'ai-researcher'
  if (path === '/research/ai-tasks') return 'ai-tasks'
  if (path === '/research/operations') return 'operations'
  if (path === '/research/closeout') return 'closeout'
  if (path === '/research/governance') return 'governance'
  if (path === '/research/evolution/ai-design') return 'evolution-ai-design'
  if (path === '/research/evolution/proposals') return 'evolution-proposals'
  if (path === '/research/candidates/proposals') return 'candidate-proposals'
  if (path === '/research/evolution') return 'evolution'
  if (path === '/research/dashboard') return 'dashboard'
  if (path === '/research/pipeline') return 'pipeline'
  if (path === '/research/candidates') return 'candidates'
  if (path.startsWith('/research/candidates/')) return 'candidate-detail'
  if (path === '/research/trials') return 'trials'
  if (path.startsWith('/research/trials/')) return 'trial-detail'
  if (path === '/daemon') return 'daemon'
  if (path === '/shadow') return 'shadow'
  if (path === '/data-health') return 'data-health'
  if (path === '/reports') return 'reports'
  return 'dashboard'
})

const candidateId = computed(() => {
  const match = props.path.match(/^\/research\/candidates\/(.+)$/)
  return match ? decodeURIComponent(match[1]) : ''
})
const trialId = computed(() => {
  const match = props.path.match(/^\/research\/trials\/(.+)$/)
  return match ? decodeURIComponent(match[1]) : ''
})
const candidateMap = computed(() => new Map(candidates.value.map((item) => [item.candidate_id, item])))
const currentCandidate = computed(() => dashboard.value?.current_candidate_id ? candidateMap.value.get(dashboard.value.current_candidate_id) : undefined)
const currentCandidateDisplay = computed(() => candidatePresentation({ candidate_id: currentCandidate.value?.candidate_id || dashboard.value?.current_candidate_id || '' }))
const detailDisplay = computed(() => candidatePresentation({ candidate_id: candidateDetail.value?.candidate_id || candidateId.value, semantics: candidateDetail.value?.semantics }))
const trialCandidateId = computed(() => String(trialDetail.value?.summary?.candidate_id || ''))
const trialCandidateDisplay = computed(() => candidatePresentation({ candidate_id: trialCandidateId.value }))
const objectiveDisplay = computed(() => objectivePresentation(objectiveId.value))
const pipelineStructuralReason = computed(() => displayReason(pipeline.value?.execution?.structural_reason_code))
const structuralGovernance = computed(() => governance.value?.structural_governance || pipeline.value?.governance_readiness || dashboard.value?.governance_readiness || null)
const structuralGovernancePending = computed(() => structuralGovernance.value?.decision_mode === 'STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED' && structuralGovernance.value?.status === 'PENDING_HUMAN_DECISION')
const structuralGovernanceActive = computed(() => structuralGovernance.value?.decision_mode === 'STRUCTURAL_PASS_PREDICTIVE_AUTHORIZATION_REQUIRED' && ['PENDING_HUMAN_DECISION', 'DEFERRED', 'AUTHORIZED', 'ENDED'].includes(String(structuralGovernance.value?.status || '')))
const governanceObjectives = computed(() => (objectiveList.value?.objectives || []).filter((item) => item.governance_pending))
const filteredGovernanceObjectives = computed(() => governanceObjectives.value.filter((item) => {
  const query = governanceSearch.value.trim().toLowerCase()
  return !query || [item.objective_id, item.objective_name, item.orchestrator_state_zh, item.lifecycle_state_zh].join(' ').toLowerCase().includes(query)
}))
const sortedGovernanceObjectives = computed(() => [...filteredGovernanceObjectives.value].sort((a, b) => {
  const left = governanceSort.value === 'name' ? objectiveName(a) : a.created_at
  const right = governanceSort.value === 'name' ? objectiveName(b) : b.created_at
  const result = String(left).localeCompare(String(right))
  return governanceDirection.value === 'desc' ? -result : result
}))
const governancePageItems = computed(() => sortedGovernanceObjectives.value.slice((governancePage.value - 1) * 20, governancePage.value * 20))
const governancePageCount = computed(() => Math.max(1, Math.ceil(sortedGovernanceObjectives.value.length / 20)))
const canonicalState = computed(() => orchestrator.value?.orchestrator_state || pipeline.value?.current_state || dashboard.value?.orchestrator_state || dashboard.value?.daemon_state || daemon.value?.daemon_state || String(evolutionProposals.value?.proposal?.governance_state || evolution.value?.report?.research_result?.terminal_classification || 'UNKNOWN'))
const canonicalStateZh = computed(() => orchestrator.value?.orchestrator_state_zh || pipeline.value?.current_state_zh || dashboard.value?.orchestrator_state_zh || (view.value === 'evolution' && evolution.value?.available ? displayClassification(canonicalState.value).label : statusDescription(canonicalState.value)))
const daemonStateZh = computed(() => orchestrator.value?.daemon_state_zh || daemon.value?.state_display_zh || '状态尚未读取')
const canonicalBudget = computed(() => orchestrator.value?.budget || budget.value || {})
function isTerminalState(value: string | undefined) {
  return ['TERMINAL_CLOSEOUT_PENDING', 'TERMINAL_CLOSEOUT_RUNNING', 'TERMINAL_CLOSEOUT_COMPLETE', 'GOVERNANCE_DECISION_REQUIRED', 'RESEARCH_PASSED', 'BUDGET_EXHAUSTED', 'GLOBAL_SEARCH_EXHAUSTED', 'SHUTDOWN', 'SAFETY_STOP', 'NO_PROGRESS_RESEARCH_LOOP'].includes(value || '')
}
const predictiveAuthorizationPending = computed(() => orchestrator.value?.next_action === 'PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED')
const terminal = computed(() => Boolean((orchestrator.value?.waiting_for_governance && !predictiveAuthorizationPending.value) || isTerminalState(orchestrator.value?.orchestrator_state)))
const terminalReasonZh = computed(() => orchestrator.value?.terminal_reason_zh || closeout.value?.terminal_reason_zh || '当前没有终止原因')
const topStatus = computed(() => {
  if (!orchestrator.value) return '正在读取当前研究状态'
  if (predictiveAuthorizationPending.value) return orchestrator.value.next_action_zh || '结构预检已通过，等待你授权预测试验'
  if (orchestrator.value.waiting_for_governance) return '本轮研究已自动收官，等待人工治理决定'
  if (terminal.value) return `当前研究已安全结束：${terminalReasonZh.value}`
  if (orchestrator.value.ai_status !== 'IDLE') return `AI 研究员${orchestrator.value.ai_status_zh || '正在处理'}`
  return `当前研究处于${canonicalStateZh.value}`
})
const currentEventList = computed(() => orchestratorEvents.value?.events || [])
const aiProgressEvents = computed(() => currentEventList.value.filter((event) => {
  const type = String(event.event_type || '').toUpperCase()
  return type.includes('AI') || type.includes('CODEX') || type.includes('HANDOFF')
}).slice(-8).reverse())
const availableGovernanceChoices = computed<GovernanceActionChoice[]>(() => {
  const catalogChoices = governanceCatalog.value?.choices || []
  if (catalogChoices.length) return catalogChoices
  return (governance.value?.choices || []).map((choice: GovernanceChoice) => ({ action: choice.choice, choice: choice.choice, label_zh: choice.label_zh, creates_new_objective: choice.creates_new_objective, requires_new_budget: choice.requires_new_budget, requires_explicit_confirmation: true, description_zh: choice.consequence_zh }))
})
const eligibleGovernanceParents = computed<ParentCandidateIdentityRef[]>(() => governanceCatalog.value?.eligible_parent_candidates || [])
const selectedGovernanceParent = computed(() => eligibleGovernanceParents.value.find((item) => item.candidate_id === selectedGovernanceParentId.value) || null)
const promisingFollowupAction = 'START_PROMISING_FOLLOWUP_OBJECTIVE'
const currentSubmission = computed(() => governance.value?.submission)

function actionLabel(action: string) {
  return ({ STATUS: '查看状态', PAUSE: '暂停研究', RESUME: '恢复研究', STOP: '停止研究', RECOVER: '恢复运行记录', START_AI: '启动 AI 研究调用' } as Record<string, string>)[action] || action
}
function objectiveName(item: ResearchObjectiveSummaryView) {
  return item.objective_name || objectivePresentation(item.objective_id).name
}
function objectivePath(path: string, id: string) {
  return `${path}?objective_id=${encodeURIComponent(id)}`
}
function stateLabel(code: unknown) {
  const value = String(code || '').toUpperCase()
  return ({ IDLE: '当前无需 AI 设计', SAFE_TERMINAL: '已在安全边界结束', GOVERNANCE_DECISION_REQUIRED: '等待人工治理决定', BUDGET_EXHAUSTED: '预测预算已用尽', TERMINAL_CLOSEOUT_COMPLETE: '自动收官已完成', RECORDED: '已记录', WAITING_FOR_MANUAL_HANDOFF: '等待手动交接', COMPLETED: '已完成，等待接入', ACCEPTED: '已通过并接入', INVALID: '未通过校验', PASS: '已通过', NOT_RUN: '尚未运行', PENDING: '等待校验' } as Record<string, string>)[value] || displayState(value).label
}
function eventTime(event: OrchestratorEventView) { return formatDate(event.timestamp) }
function eventNotice(event: OrchestratorEventView) {
  const type = String(event.event_type || '').toUpperCase()
  if (type.includes('AI') || type.includes('HANDOFF')) return { title: 'AI 研究员状态变化', body: 'AI 研究设计流程已记录新的状态。' }
  if (type.includes('BUDGET')) return { title: '预测试验预算已用尽', body: '本轮研究保持终态，未验证候选不会被标记为失败。' }
  if (type.includes('CLOSEOUT')) return { title: '自动收官已完成', body: '预算、试验和研究终态已完成对账。' }
  if (type.includes('GOVERNANCE')) return { title: '需要人工治理决定', body: '请在治理决策页选择并明确确认下一步。' }
  if (type.includes('RESUM')) return { title: '研究运行已恢复', body: '运行状态已交由自主研究编排器继续处理。' }
  return { title: '研究状态已更新', body: '研究流程已记录一次新的状态变化。' }
}
function pushToast(event: OrchestratorEventView) {
  const notice = eventNotice(event)
  const id = `${event.event_id}-toast`
  toasts.value = [{ id, ...notice }, ...toasts.value.filter((item) => item.id !== id)].slice(0, 4)
  window.setTimeout(() => { toasts.value = toasts.value.filter((item) => item.id !== id) }, 6500)
}
function applyEvents(next: OrchestratorEventsView | null) {
  if (!next) return
  const nextIds = new Set(next.events.map((event) => event.event_id))
  if (eventsInitialized) next.events.filter((event) => !seenEventIds.has(event.event_id)).slice(-4).forEach(pushToast)
  seenEventIds = nextIds
  eventsInitialized = true
  orchestratorEvents.value = next
}
function isNavActive(path: string) {
  if (path === '/research') return props.path === '/research'
  if (path === '/research/candidates') return view.value === 'candidates' || view.value === 'candidate-detail'
  if (path === '/research/trials') return view.value === 'trials' || view.value === 'trial-detail'
  return props.path === path
}
function requestKey(prefix: string) {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}-${random}`
}
function operationByName(action: string) { return operations.value?.actions.find((item) => item.action === action) }
function closeOperationModal() { if (!operationBusy.value) pendingOperation.value = null }
function closeGovernanceModal() { if (!governanceBusy.value) governanceConfirmOpen.value = false }
function closeStructuralReconciliationModal() { if (!structuralReconciliationBusy.value) structuralReconciliationConfirmOpen.value = false }
function closePredictiveAuthorizationModal() { if (!predictiveAuthorizationBusy.value) { predictiveAuthorizationConfirmOpen.value = false; predictiveGovernancePreview.value = null; predictiveAuthorizationRequestId.value = '' } }
function closePredictiveTrialStartModal() { if (!predictiveTrialStartBusy.value) { predictiveTrialStartConfirmOpen.value = false; predictiveTrialStartPreview.value = null; predictiveTrialStartIntentId.value = '' } }
function closePredictiveTrialResumeModal() { if (!predictiveTrialResumeBusy.value) { predictiveTrialResumeConfirmOpen.value = false; predictiveTrialResumePreview.value = null } }
function closeContractCorrectionModal() { if (!contractCorrectionBusy.value) contractCorrectionConfirmOpen.value = false }
function closeTrialReconciliationModal() { if (!trialReconciliationBusy.value) trialReconciliationConfirmOpen.value = false }
function resetGovernanceSelection() { if (!governanceBusy.value) { pendingGovernance.value = null; governancePreview.value = null; governanceConfirmOpen.value = false; selectedGovernanceParentId.value = '' } }
function openGovernanceConfirmation() {
  const preview = governancePreview.value
  if (!preview || governanceBusy.value) return
  if (preview.governance_action === promisingFollowupAction && preview.parent_candidate_identity_refs.length !== 1) return
  governanceConfirmOpen.value = true
}
async function confirmOperation() {
  const action = pendingOperation.value
  if (!action || operationBusy.value) return
  operationBusy.value = true
  try {
    const body = { idempotency_key: requestKey(action.toLowerCase()), confirmed: true }
    const callers: Record<string, (id: string, value: Record<string, unknown>) => Promise<OperationResult>> = { PAUSE: consoleApi.pause, RESUME: consoleApi.resume, STOP: consoleApi.stop, RECOVER: consoleApi.recover, START_AI: consoleApi.startAi }
    const response = await callers[action]?.(objectiveId.value, body)
    if (response) pageError.value = null
    pendingOperation.value = null
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('操作未完成，请稍后重试。')
  } finally { operationBusy.value = false }
}
async function preparePredictiveGovernance(decisionType = 'AUTHORIZE_FIRST_PREDICTIVE_TRIAL') {
  const authorization = pipeline.value?.predictive_authorization || (structuralGovernanceActive.value ? structuralGovernance.value : null)
  if (!authorization?.available || !authorization.candidate_id || predictiveAuthorizationBusy.value) return
  predictiveAuthorizationBusy.value = true
  predictiveAuthorizationRequestId.value = ''
  try {
    predictiveGovernancePreview.value = await consoleApi.predictiveAuthorizationPreview(objectiveId.value, decisionType)
    predictiveAuthorizationRequestId.value = requestKey('predictive-governance')
    predictiveAuthorizationConfirmOpen.value = true
    pageError.value = null
  } catch (error) {
    predictiveGovernancePreview.value = null
    predictiveAuthorizationRequestId.value = ''
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测治理方案暂时无法读取，请刷新后重试。')
  } finally { predictiveAuthorizationBusy.value = false }
}
async function confirmPredictiveAuthorization() {
  const authorization = pipeline.value?.predictive_authorization || (structuralGovernanceActive.value ? structuralGovernance.value : null)
  const preview = predictiveGovernancePreview.value
  if (!authorization?.available || !preview?.available || !preview.candidate_id || predictiveAuthorizationBusy.value) return
  predictiveAuthorizationBusy.value = true
  try {
    await consoleApi.authorizePredictive(objectiveId.value, {
      confirmed: true,
      decision_type: preview.decision_type,
      candidate_id: preview.candidate_id,
      candidate_hash: preview.candidate_hash,
      authorization_id: predictiveAuthorizationRequestId.value || (predictiveAuthorizationRequestId.value = requestKey('predictive-governance')),
      preview_hash: preview.preview_hash,
      confirmation_token: preview.confirmation_token,
    })
    predictiveAuthorizationConfirmOpen.value = false
    predictiveGovernancePreview.value = null
    predictiveAuthorizationRequestId.value = ''
    pageError.value = null
    const message = preview.decision_type === 'AUTHORIZE_FIRST_PREDICTIVE_TRIAL' ? '已授权进入第 1 次预测试验；尚未启动预测试验，Trial 数和预算保持不变。' : preview.decision_type === 'DEFER_PREDICTIVE_TRIAL' ? '已暂缓预测试验；后续仍可重新打开治理确认。' : '已结束当前 Candidate 研究路径；未创建预测 Trial。'
    toasts.value = [{ id: `predictive-${Date.now()}`, title: '预测治理决定已记录', body: message }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测治理决定未记录，请刷新页面后重试。')
  } finally { predictiveAuthorizationBusy.value = false }
}
async function preparePredictiveTrialStart() {
  const start = pipeline.value?.predictive_trial_start
  if (!start?.available || predictiveTrialStartBusy.value) return
  predictiveTrialStartBusy.value = true
  predictiveTrialStartIntentId.value = requestKey('predictive-trial-start')
  try {
    const preview = await consoleApi.predictiveTrialStartPreview(objectiveId.value)
    if (!preview.available) throw new ConsoleApiError(preview.reason_zh || '当前不可启动预测试验。', 'PREDICTIVE_TRIAL_START_UNAVAILABLE', 409)
    predictiveTrialStartPreview.value = preview
    predictiveTrialStartConfirmOpen.value = true
    pageError.value = null
  } catch (error) {
    predictiveTrialStartPreview.value = null
    predictiveTrialStartIntentId.value = ''
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测试验启动预览暂时无法读取，请刷新后重试。')
  } finally { predictiveTrialStartBusy.value = false }
}
async function confirmPredictiveTrialStart() {
  const preview = predictiveTrialStartPreview.value
  if (!preview?.available || !preview.candidate_id || predictiveTrialStartBusy.value) return
  predictiveTrialStartBusy.value = true
  try {
    await consoleApi.startPredictiveTrial(objectiveId.value, {
      confirmed: true,
      action: preview.action,
      start_intent_id: predictiveTrialStartIntentId.value || (predictiveTrialStartIntentId.value = requestKey('predictive-trial-start')),
      candidate_id: preview.candidate_id,
      candidate_hash: preview.candidate_hash,
      preview_hash: preview.preview_hash,
      confirmation_token: preview.confirmation_token,
    })
    predictiveTrialStartConfirmOpen.value = false
    predictiveTrialStartPreview.value = null
    predictiveTrialStartIntentId.value = ''
    pageError.value = null
    toasts.value = [{ id: `predictive-start-${Date.now()}`, title: '预测试验已启动', body: '第 1 次预测试验已创建并进入后台运行；Candidate 保持冻结。' }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测试验未启动，请刷新页面后重试。')
  } finally { predictiveTrialStartBusy.value = false }
}
async function preparePredictiveTrialResume() {
  const recovery = pipeline.value?.predictive_trial_recovery
  if (!recovery?.available || predictiveTrialResumeBusy.value) return
  predictiveTrialResumeBusy.value = true
  try {
    const preview = await consoleApi.predictiveTrialResumePreview(objectiveId.value)
    if (!preview.available) throw new ConsoleApiError(preview.reason_zh || '当前不可恢复预测试验。', 'PREDICTIVE_TRIAL_RESUME_UNAVAILABLE', 409)
    predictiveTrialResumePreview.value = preview
    predictiveTrialResumeConfirmOpen.value = true
    pageError.value = null
  } catch (error) {
    predictiveTrialResumePreview.value = null
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测试验恢复预览暂时无法读取，请刷新后重试。')
  } finally { predictiveTrialResumeBusy.value = false }
}
async function confirmPredictiveTrialResume() {
  const preview = predictiveTrialResumePreview.value
  if (!preview?.available || !preview.trial_id || predictiveTrialResumeBusy.value) return
  predictiveTrialResumeBusy.value = true
  try {
    await consoleApi.resumePredictiveTrial(objectiveId.value, {
      confirmed: true,
      action: preview.action,
      start_intent_id: preview.start_intent_id,
      trial_id: preview.trial_id,
      candidate_id: preview.candidate_id,
      candidate_hash: preview.candidate_hash,
      preview_hash: preview.preview_hash,
      confirmation_token: preview.confirmation_token,
    })
    predictiveTrialResumeConfirmOpen.value = false
    predictiveTrialResumePreview.value = null
    pageError.value = null
    toasts.value = [{ id: `predictive-resume-${Date.now()}`, title: '恢复请求已提交', body: '系统将只恢复第 1 次预测试验；不会创建 Trial #2 或再次扣预算。' }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('预测试验恢复未提交，请刷新页面后重试。')
  } finally { predictiveTrialResumeBusy.value = false }
}
async function confirmStructuralReconciliation() {
  const reconciliation = pipeline.value?.structural_reconciliation
  if (!reconciliation?.available || !reconciliation.candidate_id || structuralReconciliationBusy.value) return
  structuralReconciliationBusy.value = true
  try {
    await consoleApi.reconcileStructural(objectiveId.value, {
      confirmed: true,
      candidate_id: reconciliation.candidate_id,
    })
    structuralReconciliationConfirmOpen.value = false
    pageError.value = null
    toasts.value = [{ id: `structural-${Date.now()}`, title: '结构重新检查已完成', body: '页面已更新结构结果；该操作没有进入预测试验。' }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('结构重新检查未完成，请查看当前候选状态。')
  } finally { structuralReconciliationBusy.value = false }
}
async function confirmContractCorrection() {
  const preview = contractCorrectionPreview.value
  if (!preview?.available || !preview.preview_hash || !preview.confirmation_token || contractCorrectionBusy.value) return
  contractCorrectionBusy.value = true
  try {
    await consoleApi.confirmContractCorrection(objectiveId.value, preview.candidate_id, {
      confirmed: true,
      preview_hash: preview.preview_hash,
      confirmation_token: preview.confirmation_token,
    })
    contractCorrectionConfirmOpen.value = false
    pageError.value = null
    toasts.value = [{ id: `contract-correction-${Date.now()}`, title: '冻结合同已追加隔离', body: '原合同保持不变；系统只追加了不可执行记录。' }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('冻结合同隔离未执行，请刷新页面后重新检查。')
  } finally { contractCorrectionBusy.value = false }
}
async function confirmTrialReconciliation() {
  const preview = trialReconciliationPreview.value
  if (!preview?.available || !preview.preview_hash || !preview.confirmation_token || trialReconciliationBusy.value) return
  trialReconciliationBusy.value = true
  try {
    await consoleApi.confirmTrialReconciliation(objectiveId.value, preview.trial_id, {
      confirmed: true,
      preview_hash: preview.preview_hash,
      confirmation_token: preview.confirmation_token,
    })
    trialReconciliationConfirmOpen.value = false
    pageError.value = null
    toasts.value = [{ id: `trial-reconciliation-${Date.now()}`, title: 'canonical Trial 对账已完成', body: '中断 Trial 已追加工程终态；没有重跑绩效或创建新 Trial。' }, ...toasts.value].slice(0, 4)
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('canonical Trial 对账未执行，请刷新页面后重新检查。')
  } finally { trialReconciliationBusy.value = false }
}
async function confirmGovernance() {
  const preview = governancePreview.value
  if (!preview || governanceBusy.value) return
  governanceBusy.value = true
  try {
    governanceReceipt.value = await consoleApi.confirmGovernanceExecution(objectiveId.value, { confirmed: true, decision_id: preview.decision_id, preview_hash: preview.preview_hash, confirmation_token: preview.confirmation_token, governance_action: preview.governance_action, execution_mode: preview.execution_mode, selected_parent_candidate_id: preview.selected_parent_candidate_id, selected_parent_candidate_hash: preview.selected_parent_candidate_hash, idempotency_key: requestKey('governance') })
    governanceConfirmOpen.value = false
    governancePreview.value = null
    pendingGovernance.value = null
    pageError.value = null
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('治理决定未执行，请重新查看研究方案。')
  } finally { governanceBusy.value = false }
}
async function prepareGovernance(choice: GovernanceActionChoice) {
  if (governanceBusy.value) return
  const switching = pendingGovernance.value?.action !== choice.action
  pendingGovernance.value = choice
  governancePreview.value = null
  governanceConfirmOpen.value = false
  if (choice.action === promisingFollowupAction) {
    if (switching) selectedGovernanceParentId.value = ''
    pageError.value = null
    return
  }
  selectedGovernanceParentId.value = ''
  governanceBusy.value = true
  try {
    governancePreview.value = await consoleApi.governancePreview(objectiveId.value, { action: choice.action, execution_mode: choice.creates_new_objective ? 'CREATE_AND_ACTIVATE' : 'CREATE_ONLY' })
    pageError.value = null
  } catch (error) {
    governancePreview.value = null
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('下一轮研究方案暂时无法读取，请稍后重试。')
  } finally { governanceBusy.value = false }
}
function selectGovernanceParent(parent: ParentCandidateIdentityRef) {
  if (governanceBusy.value || governanceReceipt.value) return
  selectedGovernanceParentId.value = parent.candidate_id
  governancePreview.value = null
  governanceConfirmOpen.value = false
  pageError.value = null
}
async function prepareSelectedGovernanceParent() {
  const choice = pendingGovernance.value
  const parent = selectedGovernanceParent.value
  if (!choice || choice.action !== promisingFollowupAction || !parent || governanceBusy.value) return
  governanceBusy.value = true
  governancePreview.value = null
  try {
    governancePreview.value = await consoleApi.governancePreview(objectiveId.value, { action: choice.action, execution_mode: choice.creates_new_objective ? 'CREATE_AND_ACTIVATE' : 'CREATE_ONLY', selected_parent_candidate_id: parent.candidate_id, selected_parent_candidate_hash: parent.candidate_hash })
    pageError.value = null
  } catch (error) {
    governancePreview.value = null
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('所选父候选的研究方案暂时无法读取，请稍后重试。')
  } finally { governanceBusy.value = false }
}

async function copyManualPrompt() {
  const prompt = manualAiHandoff.value?.prompt_text || ''
  if (!prompt) return
  try {
    await navigator.clipboard.writeText(prompt)
    toasts.value = [{ id: `manual-copy-${Date.now()}`, title: '提示词已复制', body: '现在可以把提示词粘贴到 AI 研究员任务中。' }, ...toasts.value].slice(0, 4)
  } catch {
    pageError.value = new ConsoleApiError('提示词复制失败，请展开任务资料后手动复制。', 'CLIPBOARD_UNAVAILABLE', 0)
  }
}

async function rescanManualResult() {
  if (manualRescanBusy.value) return
  manualRescanBusy.value = true
  try {
    await consoleApi.rescanManualAi(objectiveId.value)
    pageError.value = null
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('结果扫描未完成，请稍后重试。')
  } finally { manualRescanBusy.value = false }
}

async function changeAiMode() {
  if (aiModeBusy.value || !selectedAiMode.value) return
  aiModeBusy.value = true
  try {
    aiInvocationMode.value = await consoleApi.setAiInvocationMode(selectedAiMode.value)
    pageError.value = null
    await loadView(true)
  } catch (error) {
    pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('AI 研究调用模式保存失败，请稍后重试。')
  } finally { aiModeBusy.value = false }
}

const pageMeta = computed(() => ({
  objectives: { eyebrow: '研究目标', title: '研究目标列表', description: '先选择明确的研究目标，再查看它自己的进度、运行状态或治理决定。' },
  autonomous: { eyebrow: '自主研究中心', title: '自主研究中心', description: '用一眼可读的状态确认当前研究目标、运行边界和下一项人工决定。' },
  'ai-researcher': { eyebrow: 'AI研究员', title: 'AI研究员', description: '查看 AI 研究设计是否启用、最近一次调用与批次校验，不展示候选绩效。' },
  'ai-tasks': { eyebrow: 'AI研究任务', title: 'AI研究任务', description: '查看当前目标的手动交接任务、结果文件和校验状态；页面不会手动导入候选。' },
  operations: { eyebrow: '研究运行控制', title: '研究运行控制', description: '所有操作都通过自主研究编排器服务，并在收到确认后刷新真实状态。' },
  closeout: { eyebrow: '自动收官', title: '自动收官', description: '阅读本轮研究的真实终态、预算对账和未完成候选，不把未验证误读为失败。' },
  governance: { eyebrow: '治理决策', title: '治理决策', description: '选择研究方向，查看不可变的下一轮研究方案，并在明确确认后由后端安全执行。' },
  dashboard: { eyebrow: '研究总览', title: '研究总览', description: '在一个视图内确认研究运行状态、当前候选、预算与安全边界。' },
  pipeline: { eyebrow: '研究流程', title: '研究进度', description: '按研究流程说明当前处于哪一步。' },
  candidates: { eyebrow: '候选策略', title: '候选策略', description: '先阅读中文策略含义，再按需查看技术标识与冻结规则。' },
  'candidate-detail': { eyebrow: '候选策略详情', title: detailDisplay.value.name, description: '候选策略详情，以已冻结规则为准。' },
  trials: { eyebrow: '预测试验', title: '预测试验', description: '分开查看试验生命周期、预算与已授权的研究证据。' },
  'trial-detail': { eyebrow: '预测试验详情', title: '预测试验详情', description: '以中文结论为主，原始技术数据按需展开。' },
  daemon: { eyebrow: '研究守护进程', title: '研究守护进程', description: '只读查看后台研究进程的状态、保存记录、资源与人工处理事项。' },
  shadow: { eyebrow: '每日研究观察', title: '每日研究观察', description: '这是研究观察结果，不是买入推荐，也不会产生真实订单。' },
  'data-health': { eyebrow: '数据健康', title: '数据健康', description: '查看每个数据来源能够证明什么，以及仍缺少什么。' },
  reports: { eyebrow: '报告中心', title: '报告中心', description: '只打开已登记的研究报告，不直接浏览项目文件系统。' },
  evolution: { eyebrow: '研究演进分析', title: '研究演进分析', description: '从已完成的研究结果提取失败原因，形成下一轮研究设计可参考的上下文；不会自动创建候选或启动试验。' },
  'evolution-proposals': { eyebrow: '研究演进建议', title: '研究演进建议', description: '把失败分析桥接成待人工审核的高层研究方向；不会自动创建目标、候选或启动试验。' },
  'evolution-ai-design': { eyebrow: 'AI 研究设计', title: 'AI 研究设计', description: '根据已治理的 Evolution Proposal 生成结果盲化的研究设计提案；不会自动创建候选或进入 Predictive Trial。' },
  'candidate-proposals': { eyebrow: '候选策略建议', title: '候选策略建议', description: '把 AI 研究设计整理成候选方案，经过人工审核和第二次确认后冻结；不会自动进入 Structural Preflight 或 Trial。' },
}[view.value] || { eyebrow: '研究总览', title: '研究总览', description: '' }))

const candidateFilters = [
  ['ALL', '全部'], ['STRUCTURAL', '结构预检中'], ['PREDICTIVE', '预测验证中'], ['RESEARCH_PASSED', '研究通过'], ['PROMISING', '有潜力'], ['REJECTED', '已淘汰'], ['BLOCKED', '预测验证未通过'],
]
const filteredCandidates = computed(() => candidates.value.filter((item) => {
  const text = searchText.value.trim().toLowerCase()
  const matchesText = !text || [item.candidate_id, item.candidate_hash, item.mechanism_family, ...item.factor_ids].join(' ').toLowerCase().includes(text)
  const matchesMechanism = mechanismFilter.value === 'ALL' || item.mechanism_family === mechanismFilter.value
  if (!matchesText || !matchesMechanism) return false
  if (candidateFilter.value === 'ALL') return true
  if (candidateFilter.value === 'STRUCTURAL') return item.structural_status.includes('STRUCTURAL')
  if (candidateFilter.value === 'PREDICTIVE') return item.predictive_trial_status.includes('PREDICTIVE') || item.predictive_trial_status === 'RUNNING'
  return item.effective_classification === candidateFilter.value
}))
const sortedCandidates = computed(() => [...filteredCandidates.value].sort((a, b) => {
  const field = candidateSort.value === 'mechanism' ? [a.mechanism_family, b.mechanism_family] : candidateSort.value === 'classification' ? [a.effective_classification || '', b.effective_classification || ''] : [a.candidate_id, b.candidate_id]
  const result = String(field[0]).localeCompare(String(field[1]))
  return candidateDirection.value === 'desc' ? -result : result
}))
const candidatePageItems = computed(() => sortedCandidates.value.slice((candidatePage.value - 1) * 20, candidatePage.value * 20))
const candidatePageCount = computed(() => Math.max(1, Math.ceil(sortedCandidates.value.length / 20)))
const filteredTrials = computed(() => trials.value.filter((item) => {
  const query = trialSearch.value.trim().toLowerCase()
  const matchesText = !query || [item.trial_id, item.candidate_id, item.candidate_hash, item.family_id].join(' ').toLowerCase().includes(query)
  const matchesFilter = trialFilter.value === 'ALL' || item.status === trialFilter.value || item.classification === trialFilter.value
  return matchesText && matchesFilter
}))
const sortedTrials = computed(() => [...filteredTrials.value].sort((a, b) => {
  const left = trialSort.value === 'candidate_id' ? a.candidate_id : trialSort.value === 'status' ? a.status : a.trial_id
  const right = trialSort.value === 'candidate_id' ? b.candidate_id : trialSort.value === 'status' ? b.status : b.trial_id
  const result = String(left).localeCompare(String(right))
  return trialDirection.value === 'desc' ? -result : result
}))
const trialPageItems = computed(() => sortedTrials.value.slice((trialPage.value - 1) * 20, trialPage.value * 20))
const trialPageCount = computed(() => Math.max(1, Math.ceil(sortedTrials.value.length / 20)))
const filteredObjectives = computed(() => (objectiveList.value?.objectives || []).filter((item) => {
  const query = objectiveSearch.value.trim().toLowerCase()
  const matchesText = !query || [item.objective_id, item.objective_name, item.orchestrator_state_zh, item.lifecycle_state_zh].join(' ').toLowerCase().includes(query)
  const matchesFilter = objectiveFilter.value === 'ALL' || (objectiveFilter.value === 'GOVERNANCE' ? item.governance_pending : objectiveFilter.value === 'ACTIVE' ? !item.governance_pending && !item.orchestrator_state.includes('TERMINAL') : item.orchestrator_state.includes('TERMINAL'))
  return matchesText && matchesFilter
}))
const sortedObjectives = computed(() => [...filteredObjectives.value].sort((a, b) => {
  const left = objectiveSort.value === 'name' ? objectiveName(a) : objectiveSort.value === 'state' ? a.orchestrator_state_zh : a.created_at
  const right = objectiveSort.value === 'name' ? objectiveName(b) : objectiveSort.value === 'state' ? b.orchestrator_state_zh : b.created_at
  const result = String(left).localeCompare(String(right))
  return objectiveDirection.value === 'desc' ? -result : result
}))
const objectivePageItems = computed(() => sortedObjectives.value.slice((objectivePage.value - 1) * 20, objectivePage.value * 20))
const objectivePageCount = computed(() => Math.max(1, Math.ceil(sortedObjectives.value.length / 20)))
const mechanismOptions = computed(() => Array.from(new Set(candidates.value.map((item) => item.mechanism_family).filter(Boolean))))
const filteredReports = computed(() => (reports.value?.reports || []).filter((item) => {
  const query = reportSearch.value.trim().toLowerCase()
  const matchesText = !query || [reportTitle(item), reportSummary(item), reportId(item)].join(' ').toLowerCase().includes(query)
  return matchesText && (reportFilter.value === '全部' || reportCategory(item) === reportFilter.value)
}))
const sortedReports = computed(() => [...filteredReports.value].sort((a, b) => {
  const left = reportSort.value === 'title' ? reportTitle(a) : String(a.generated_at || a.source_generated_at || a.updated_at || '')
  const right = reportSort.value === 'title' ? reportTitle(b) : String(b.generated_at || b.source_generated_at || b.updated_at || '')
  const result = String(left).localeCompare(String(right))
  return reportDirection.value === 'desc' ? -result : result
}))
const reportPageItems = computed(() => sortedReports.value.slice((reportPage.value - 1) * 20, reportPage.value * 20))
const reportPageCount = computed(() => Math.max(1, Math.ceil(sortedReports.value.length / 20)))
const filteredCloseoutCandidates = computed(() => (closeout.value?.promising_candidates || []).filter((item) => {
  const query = closeoutSearch.value.trim().toLowerCase()
  const matchesText = !query || [item.candidate_id, item.display_name_zh, item.trial_id, item.why_promising_zh].join(' ').toLowerCase().includes(query)
  const matchesFilter = closeoutFilter.value === 'ALL' || closeoutFilter.value === 'PROMISING'
  return matchesText && matchesFilter
}))
const sortedCloseoutCandidates = computed(() => [...filteredCloseoutCandidates.value].sort((a, b) => {
  const left = closeoutSort.value === 'name' ? a.display_name_zh : a.candidate_id
  const right = closeoutSort.value === 'name' ? b.display_name_zh : b.candidate_id
  const result = String(left || '').localeCompare(String(right || ''))
  return closeoutDirection.value === 'desc' ? -result : result
}))
const closeoutPageItems = computed(() => sortedCloseoutCandidates.value.slice((closeoutPage.value - 1) * 20, closeoutPage.value * 20))
const closeoutPageCount = computed(() => Math.max(1, Math.ceil(sortedCloseoutCandidates.value.length / 20)))
const detailFactors = computed(() => Array.isArray(candidateDetail.value?.semantics?.factor_ids) ? candidateDetail.value?.semantics?.factor_ids as string[] : [])
const detailTrials = computed(() => Array.isArray(candidateDetail.value?.predictive?.trials) ? candidateDetail.value?.predictive?.trials as TrialSummaryView[] : [])

function navigate(path: string) { emit('navigate', path) }
function candidateFor(id: string | null | undefined) { return id ? candidateMap.value.get(id) : undefined }
function nameFor(id: string | null | undefined) { return candidatePresentation({ candidate_id: id || '' }).name }
function statusDescription(code: string | null | undefined) {
  const value = String(code || '').toUpperCase()
  const labels: Record<string, string> = { EVOLUTION_ANALYZED: '已完成失败分析', PROPOSAL_CREATED: '研究演进建议已生成', HUMAN_REVIEW_REQUIRED: '等待人工审核', APPROVED: '已通过人工审核', CREATE_NEW_OBJECTIVE: '等待创建新的研究目标' }
  return labels[value] || displayState(code).label
}
function jsonText(value: unknown) { return JSON.stringify(value, null, 2) }
function reportId(item: Record<string, unknown>) { return String(item.report_id || item.id || '') }
function reportTitle(item: Record<string, unknown>) { return humanReportTitle(item) }
function reportSummary(item: Record<string, unknown>) {
  for (const key of ['summary_zh', 'description_zh', 'abstract_zh', 'message_zh']) {
    const value = item[key]
    if (typeof value === 'string' && value.trim() && /[\u4e00-\u9fff]/.test(value)) return value
  }
  const category = String(item.category || item.type || item.report_type || '').toLowerCase()
  const title = String(item.title_zh || item.title || '').toLowerCase()
  if (category.includes('architecture') || title.includes('架构')) return '记录平台架构、数据能力与安全边界，供研究协作复核。'
  if (category.includes('daemon') || title.includes('守护')) return '记录研究守护进程的状态、保存记录与处理边界。'
  if (category.includes('candidate') || title.includes('候选')) return '记录候选策略的结构预检与冻结规则。'
  if (category.includes('trial') || title.includes('试验')) return '记录预测试验的研究证据与最终规则判定。'
  if (category.includes('shadow') || title.includes('观察')) return '记录每日研究观察的扫描状态与研究边界。'
  if (category.includes('data') || title.includes('数据')) return '记录数据来源、覆盖范围与可用性。'
  if (category.includes('rule') || category.includes('govern') || title.includes('规则')) return '记录研究规则与安全边界。'
  return '当前仅有机器报告，暂无中文阅读版。'
}
function reportCategory(item: Record<string, unknown>) {
  return humanReportCategory(item.category || item.type || item.report_type)
}
function sourceRole(source: Record<string, unknown>) {
  const role = String(source.role || '')
  const translations: Record<string, string> = { 'current Shadow data': '当前每日研究观察数据', 'trade calendar': '交易日历', 'PIT universe/tradability state': '时点一致性证券与可交易状态', 'frozen factor definitions/materialization': '已冻结因子定义与计算结果', '5-minute research capability': '5分钟研究数据能力', 'TdxData(get_benchmark)': '通达信基准指数数据', 'TdxData(gbbq)': '通达信公司行动数据', 'TdxData(read_day_file)': '通达信日线行情数据', 'TdxData(read_lc5_file)': '通达信5分钟行情数据', 'TQClient': 'TQ 在线数据服务' }
  if (translations[role]) return translations[role]
  if (role.includes('daily OHLCVA')) return '日线行情结构输入'
  if (role.includes('research session calendar')) return '研究时段日历'
  if (role.includes('PIT-safe event')) return '时点一致性安全事件依赖'
  if (role.includes('unified factor')) return '已冻结因子定义与计算结果'
  if (role.includes('Shadow EOD')) return '每日研究观察收盘行情与证券状态'
  if (role.includes('separate data capability')) return '独立数据能力'
  return role && /[\u4e00-\u9fff]/.test(role) ? role : '当前来源角色暂无中文解释'
}
function humanSourceLabel(value: unknown) {
  const source = String(value || '')
  if (!source) return '暂无来源说明'
  if (/canonical|orchestrator/i.test(source)) return '自主研究编排器'
  if (/objective\s*registry/i.test(source)) return '研究目标登记库'
  if (/daemon/i.test(source)) return '研究守护进程'
  return /[\u4e00-\u9fff]/.test(source) ? source : '当前来源暂无中文说明'
}
function statusTone(code: unknown) {
  const value = String(code || '').toUpperCase()
  if (value.includes('BLOCK') || value.includes('FAIL') || value.includes('INVALID') || value.includes('EXHAUST')) return 'tone-danger'
  if (value.includes('PARTIAL') || value.includes('UNKNOWN') || value.includes('ONLINE') || value === 'STALE' || value === 'CONFLICT') return 'tone-attention'
  if (value.includes('READY') || value.includes('PASS') || value.includes('HEALTHY')) return 'tone-success'
  return 'tone-muted'
}
function evolutionCategoryLabel(value: unknown) {
  const labels: Record<string, string> = { STATISTICAL_FAILURE: '统计失败', RETURN_FAILURE: '收益失败', RISK_FAILURE: '风险失败', SAMPLE_FAILURE: '样本失败', ENGINEERING_FAILURE: '工程失败', OVERFITTING_RISK: '过拟合风险' }
  const code = String(value || '').toUpperCase()
  return labels[code] || code || '未分类'
}
function eventLabel(event: Record<string, unknown>) {
  const reason = event.reason_code ? displayReason(event.reason_code) : null
  if (reason?.known) return reason.label
  const type = String(event.event_type || '').toUpperCase()
  if (type === 'CODEX_INVOCATION_COMPLETED') return 'AI 调用已完成'
  if (type === 'CODEX_INVOCATION_STARTED') return 'AI 调用已启动'
  if (type === 'AI_BATCH_CONSUMED_NEXT_HANDOFF') return '已进入下一次 AI 研究设计'
  return displayState(event.new_state || event.state || event.event_type).label
}
function eventDescription(event: Record<string, unknown>) {
  const reason = event.reason_code ? displayReason(event.reason_code) : null
  if (reason?.known) return reason.description
  const type = String(event.event_type || '').toUpperCase()
  if (type === 'CODEX_INVOCATION_COMPLETED') return event.error_code ? `AI 调用已记录完成，结果：${event.error_code}。` : 'AI 调用已记录完成，具体结果以当前调用记录为准。'
  if (type === 'CODEX_INVOCATION_STARTED') return '自主研究编排器已按当前研究目标启动 AI 研究调用。'
  if (type === 'AI_BATCH_CONSUMED_NEXT_HANDOFF') return '上一 AI 批次已完成接入，编排器已进入下一次研究设计。'
  return '研究流程已记录一次状态变化。'
}
function countFor(key: string) { return orchestrator.value?.research_counts?.[key] ?? closeout.value?.research_counts?.[key] ?? 0 }
function budgetValue(key: string) {
  const canonical = canonicalBudget.value as Record<string, unknown>
  const closed = closeout.value?.budget as Record<string, unknown> | undefined
  return canonical[key] ?? closed?.[key] ?? '暂无数据'
}
function technicalEntries(value: Record<string, unknown> | null | undefined) { return value || {} }
function displayActionForHandoff() { return handoff.value?.allowed_next_actions || [] }
function applyCandidates(response: CandidateListResponse | null) {
  if (!response) return
  candidates.value = response.items
  candidateScopeSummary.value = response.scope_summary || null
}

async function read<T>(loader: () => Promise<T>, issues: string[]): Promise<T | null> {
  try { return await loader() } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    if (error instanceof ConsoleApiError) issues.push(`${error.message}（${error.code}）`)
    else issues.push('数据读取失败，请稍后重试。')
    return null
  }
}

async function readOptional<T>(loader: () => Promise<T>, issues: string[], optionalCodes: string[]): Promise<T | null> {
  try { return await loader() } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    if (error instanceof ConsoleApiError && optionalCodes.includes(error.code)) return null
    if (error instanceof ConsoleApiError) issues.push(`${error.message}（${error.code}）`)
    else issues.push('数据读取失败，请稍后重试。')
    return null
  }
}

async function loadView(silent = false) {
  if (requestActive) return
  requestActive = true
  controller?.abort()
  const currentController = new AbortController()
  controller = currentController
  const signal = currentController.signal
  const issues: string[] = []
  const canonicalContextNeeded = !['objectives', 'autonomous', 'ai-researcher', 'ai-tasks', 'operations', 'closeout', 'governance', 'evolution', 'evolution-proposals', 'evolution-ai-design', 'candidate-proposals'].includes(view.value)
  const canonicalContext = canonicalContextNeeded ? read(() => consoleApi.orchestrator(objectiveId.value, signal), issues) : Promise.resolve(null)
  const aiProgressContext = view.value === 'pipeline' ? read(() => consoleApi.aiStatus(objectiveId.value, signal), issues) : Promise.resolve(null)
  if (silent) refreshing.value = true
  else { loading.value = true; pageError.value = null }
  try {
    switch (view.value) {
      case 'objectives': {
        const next = await read(() => consoleApi.objectives(signal), issues)
        if (next) objectiveList.value = next
        break
      }
      case 'dashboard': {
        const [nextDashboard, nextBudget, nextCandidates, nextData, nextShadow, nextHandoff] = await Promise.all([
          read(() => consoleApi.dashboard(objectiveId.value, signal), issues), read(() => consoleApi.budget(objectiveId.value, signal), issues), read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues), read(() => consoleApi.dataHealth(objectiveId.value, signal), issues), read(() => consoleApi.shadow(objectiveId.value, signal), issues), read(() => consoleApi.handoff(objectiveId.value, signal), issues),
        ])
        if (nextDashboard) dashboard.value = nextDashboard
        if (nextBudget) budget.value = nextBudget
        applyCandidates(nextCandidates)
        if (nextData) dataHealth.value = nextData
        if (nextShadow) shadow.value = nextShadow
        if (nextHandoff) handoff.value = nextHandoff
        break
      }
      case 'autonomous': {
        const [nextOrchestrator, nextEvents, nextPipeline, nextCloseout, nextGovernance] = await Promise.all([
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestratorEvents(objectiveId.value, 50, signal), issues),
          read(() => consoleApi.pipeline(objectiveId.value, signal), issues),
          readOptional(() => consoleApi.closeout(objectiveId.value, signal), issues, ['CLOSEOUT_NOT_FOUND']),
          readOptional(() => consoleApi.governanceDecision(objectiveId.value, signal), issues, ['GOVERNANCE_NOT_FOUND']),
        ])
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        applyEvents(nextEvents)
        if (nextPipeline) pipeline.value = nextPipeline
        if (nextCloseout) closeout.value = nextCloseout
        if (nextGovernance) governance.value = nextGovernance
        break
      }
      case 'ai-researcher': {
        const [nextAi, nextManual, nextMode, nextOrchestrator, nextEvents, nextOperations] = await Promise.all([
          read(() => consoleApi.aiStatus(objectiveId.value, signal), issues),
          read(() => consoleApi.manualAiHandoff(objectiveId.value, signal), issues),
          read(() => consoleApi.aiInvocationMode(signal), issues),
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestratorEvents(objectiveId.value, 30, signal), issues),
          read(() => consoleApi.operations(objectiveId.value, signal), issues),
        ])
        if (nextAi) aiStatus.value = nextAi
        if (nextManual) manualAiHandoff.value = nextManual
        if (nextMode) { aiInvocationMode.value = nextMode; selectedAiMode.value = nextMode.ai_invocation_mode }
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        if (nextOperations) operations.value = nextOperations
        applyEvents(nextEvents)
        break
      }
      case 'ai-tasks': {
        const [nextManual, nextTasks, nextResults, nextMode, nextOrchestrator] = await Promise.all([
          read(() => consoleApi.manualAiHandoff(objectiveId.value, signal), issues),
          read(() => consoleApi.aiTasks(objectiveId.value, aiTaskPage.value, 20, searchText.value, signal, { status: aiTaskStatus.value, mode: aiTaskMode.value, sort: aiTaskSort.value, direction: aiTaskDirection.value }), issues),
          read(() => consoleApi.aiResults(objectiveId.value, 1, 20, searchText.value, signal), issues),
          read(() => consoleApi.aiInvocationMode(signal), issues),
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
        ])
        if (nextManual) manualAiHandoff.value = nextManual
        if (nextTasks) aiTasks.value = nextTasks
        if (nextResults) aiResults.value = nextResults
        if (nextMode) { aiInvocationMode.value = nextMode; selectedAiMode.value = nextMode.ai_invocation_mode }
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        break
      }
      case 'operations': {
        const [nextOperations, nextOrchestrator, nextEvents] = await Promise.all([
          read(() => consoleApi.operations(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestratorEvents(objectiveId.value, 30, signal), issues),
        ])
        if (nextOperations) operations.value = nextOperations
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        applyEvents(nextEvents)
        break
      }
      case 'closeout': {
        const [nextCloseout, nextOrchestrator, nextEvents] = await Promise.all([
          readOptional(() => consoleApi.closeout(objectiveId.value, signal), issues, ['CLOSEOUT_NOT_FOUND']),
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestratorEvents(objectiveId.value, 30, signal), issues),
        ])
        if (nextCloseout) closeout.value = nextCloseout
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        applyEvents(nextEvents)
        break
      }
      case 'governance': {
        if (!objectiveIdExplicit) {
          const next = await read(() => consoleApi.objectives(signal), issues)
          if (next) objectiveList.value = next
          break
        }
        const [nextGovernance, nextCatalog, nextCloseout, nextOrchestrator, nextEvents] = await Promise.all([
          readOptional(() => consoleApi.governanceDecision(objectiveId.value, signal), issues, ['GOVERNANCE_NOT_FOUND']),
          readOptional(() => consoleApi.governancePreviewCatalog(objectiveId.value, signal), issues, ['GOVERNANCE_NOT_REQUIRED']),
          readOptional(() => consoleApi.closeout(objectiveId.value, signal), issues, ['CLOSEOUT_NOT_FOUND']),
          read(() => consoleApi.orchestrator(objectiveId.value, signal), issues),
          read(() => consoleApi.orchestratorEvents(objectiveId.value, 30, signal), issues),
        ])
        if (nextGovernance) governance.value = nextGovernance
        if (nextCatalog) governanceCatalog.value = nextCatalog
        if (nextCloseout) closeout.value = nextCloseout
        if (nextOrchestrator) orchestrator.value = nextOrchestrator
        applyEvents(nextEvents)
        break
      }
      case 'pipeline': {
        const [nextPipeline, nextDaemon, nextHandoff, nextOperations] = await Promise.all([read(() => consoleApi.pipeline(objectiveId.value, signal), issues), read(() => consoleApi.daemon(objectiveId.value, signal), issues), read(() => consoleApi.handoff(objectiveId.value, signal), issues), read(() => consoleApi.operations(objectiveId.value, signal), issues)])
        if (nextPipeline) pipeline.value = nextPipeline
        if (nextDaemon) daemon.value = nextDaemon
        if (nextHandoff) handoff.value = nextHandoff
        if (nextOperations) operations.value = nextOperations
        break
      }
      case 'candidates': {
        const next = await read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues)
        applyCandidates(next)
        break
      }
      case 'candidate-detail': {
        const [nextDetail, nextStructural, nextCorrectionPreview, nextCandidates] = await Promise.all([read(() => consoleApi.candidate(objectiveId.value, candidateId.value, signal), issues), read(() => consoleApi.structural(objectiveId.value, candidateId.value, signal), issues), read(() => consoleApi.contractCorrectionPreview(objectiveId.value, candidateId.value, signal), issues), read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues)])
        if (nextDetail) candidateDetail.value = nextDetail
        if (nextStructural) structural.value = nextStructural
        if (nextCorrectionPreview) contractCorrectionPreview.value = nextCorrectionPreview
        applyCandidates(nextCandidates)
        break
      }
      case 'trials': {
        const [nextTrials, nextCandidates, nextBudget] = await Promise.all([read(() => consoleApi.trials(objectiveId.value, 100, signal), issues), read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues), read(() => consoleApi.budget(objectiveId.value, signal), issues)])
        if (nextTrials) trials.value = nextTrials.items
        applyCandidates(nextCandidates)
        if (nextBudget) budget.value = nextBudget
        break
      }
      case 'trial-detail': {
        const [nextDetail, nextReconciliationPreview, nextCandidates] = await Promise.all([read(() => consoleApi.trial(objectiveId.value, trialId.value, signal), issues), read(() => consoleApi.trialReconciliationPreview(objectiveId.value, trialId.value, signal), issues), read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues)])
        if (nextDetail) trialDetail.value = nextDetail
        if (nextReconciliationPreview) trialReconciliationPreview.value = nextReconciliationPreview
        applyCandidates(nextCandidates)
        break
      }
      case 'daemon': {
        const [nextDaemon, nextHealth, nextPipeline, nextHandoff] = await Promise.all([read(() => consoleApi.daemon(objectiveId.value, signal), issues), read(() => consoleApi.daemonHealth(objectiveId.value, signal), issues), read(() => consoleApi.pipeline(objectiveId.value, signal), issues), read(() => consoleApi.handoff(objectiveId.value, signal), issues)])
        if (nextDaemon) daemon.value = nextDaemon
        if (nextHealth) daemonHealth.value = nextHealth
        if (nextPipeline) pipeline.value = nextPipeline
        if (nextHandoff) handoff.value = nextHandoff
        break
      }
      case 'shadow': {
        const [nextShadow, nextCandidates] = await Promise.all([read(() => consoleApi.shadow(objectiveId.value, signal), issues), read(() => consoleApi.candidates(objectiveId.value, 100, signal), issues)])
        if (nextShadow) shadow.value = nextShadow
        applyCandidates(nextCandidates)
        break
      }
      case 'data-health': {
        const next = await read(() => consoleApi.dataHealth(objectiveId.value, signal), issues)
        if (next) dataHealth.value = next
        break
      }
      case 'evolution': {
        const next = await read(() => consoleApi.evolution(objectiveId.value, signal), issues)
        if (next) evolution.value = next
        break
      }
      case 'evolution-proposals': {
        const next = await read(() => consoleApi.evolutionProposals(objectiveId.value, signal), issues)
        if (next) evolutionProposals.value = next
        break
      }
      case 'evolution-ai-design': {
        const next = await read(() => consoleApi.evolutionAIDesign(objectiveId.value, signal), issues)
        if (next) evolutionAIDesign.value = next
        break
      }
      case 'candidate-proposals': {
        const next = await read(() => consoleApi.candidateProposals(objectiveId.value, signal), issues)
        if (next) candidateProposals.value = next
        break
      }
      case 'reports': {
        const next = await read(() => consoleApi.reports(objectiveId.value, signal), issues)
        if (next) reports.value = next
        break
      }
    }
    const nextCanonical = await canonicalContext
    if (nextCanonical) orchestrator.value = nextCanonical
    const nextAiProgress = await aiProgressContext
    if (nextAiProgress) aiStatus.value = nextAiProgress
    if (issues.length && !silent) pageError.value = new ConsoleApiError(issues[0])
  } catch (error) {
    if ((error as Error).name !== 'AbortError') pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('数据读取失败，请稍后重试。')
  } finally {
    if (controller === currentController) { requestActive = false; loading.value = false; refreshing.value = false }
  }
}

function pollingInterval() {
  if (view.value === 'autonomous' || view.value === 'operations' || view.value === 'ai-researcher' || view.value === 'ai-tasks' || view.value === 'dashboard' || view.value === 'daemon' || view.value === 'pipeline') return 5000
  if (view.value === 'closeout' || view.value === 'governance') return 15000
  if (view.value === 'candidates' || view.value === 'trials') return 15000
  if (view.value === 'candidate-detail' || view.value === 'trial-detail') return 30000
  return 60000
}
function startPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = window.setInterval(() => { if (!document.hidden && !requestActive) loadView(true) }, pollingInterval())
}
function onVisibilityChange() { if (!document.hidden && !requestActive) loadView(true) }
function refresh() { loadView(true) }
async function openReport(item: Record<string, unknown>) {
  const id = reportId(item)
  if (!id) return
  try { openedReport.value = await consoleApi.report(objectiveId.value, id) } catch (error) { pageError.value = error instanceof ConsoleApiError ? error : new ConsoleApiError('报告读取失败，请稍后重试。') }
}

onMounted(() => { document.addEventListener('visibilitychange', onVisibilityChange); loadView(); startPolling() })
watch(() => props.path, () => { controller?.abort(); requestActive = false; openedReport.value = null; loadView(); startPolling() })
onBeforeUnmount(() => { controller?.abort(); if (pollTimer) window.clearInterval(pollTimer); document.removeEventListener('visibilitychange', onVisibilityChange) })
</script>

<template>
  <div class="console-shell">
    <aside class="console-sidebar" aria-label="研究控制台导航">
      <button class="console-brand" type="button" @click="navigate('/research')"><span class="brand-mark"><i></i><i></i><i></i></span><span><strong>量化研究实验室</strong><small>研究控制台</small></span></button>
      <div class="nav-label">自主研究工作区</div>
      <nav class="console-nav">
         <button v-for="item in [{ path: '/research/objectives', label: '研究目标', icon: '▣' }, { path: '/research', label: '自主研究中心', icon: '◈' }, { path: '/research/ai-researcher', label: 'AI研究员', icon: '✦' }, { path: '/research/ai-tasks', label: 'AI研究任务', icon: '↗' }, { path: '/research/operations', label: '研究运行控制', icon: '◌' }, { path: '/research/closeout', label: '自动收官', icon: '✓' }, { path: '/research/evolution', label: '研究演进分析', icon: '⌁' }, { path: '/research/evolution/proposals', label: '研究演进建议', icon: '◇' }, { path: '/research/evolution/ai-design', label: 'AI研究设计', icon: '✦' }, { path: '/research/candidates/proposals', label: '候选策略建议', icon: '◇' }, { path: '/research/governance', label: '治理决策', icon: '◇' }, { path: '/research/dashboard', label: '研究总览', icon: '▦' }, { path: '/research/pipeline', label: '研究进度', icon: '⌁' }, { path: '/research/candidates', label: '候选策略', icon: '◇' }, { path: '/research/trials', label: '预测试验', icon: '⊙' }, { path: '/daemon', label: '研究守护进程', icon: '◌' }]" :key="item.path" type="button" :class="['nav-link', { active: isNavActive(item.path) }]" @click="navigate(item.path)"><span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}</span></button>
      </nav>
      <div class="nav-label secondary-label">观察与资料</div>
      <nav class="console-nav">
        <button v-for="item in [{ path: '/shadow', label: '每日研究观察', icon: '◎' }, { path: '/data-health', label: '数据健康', icon: '⌘' }, { path: '/reports', label: '报告中心', icon: '▤' }]" :key="item.path" type="button" :class="['nav-link', { active: props.path === item.path }]" @click="navigate(item.path)"><span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}</span></button>
      </nav>
      <div class="sidebar-bottom">
        <div class="safety-strip"><span>研究安全边界</span><strong>真实订单 · 已禁用</strong><small>最终测试集 0 / 0 / 0<br />前瞻验证 · 未启动</small></div>
        <button class="legacy-link" type="button" @click="navigate('/')">返回原有交易工具</button>
      </div>
    </aside>

    <div class="console-main">
       <header class="console-topbar"><div class="breadcrumb"><span>量化研究</span><b>/</b><strong>{{ pageMeta.title }}</strong></div><div class="topbar-actions"><span class="global-state" :class="statusTone(objectiveIdExplicit ? canonicalState : 'UNKNOWN')">{{ objectiveIdExplicit ? canonicalStateZh : '尚未选择研究目标' }}</span><span class="global-state" :class="statusTone(objectiveIdExplicit ? orchestrator?.ai_status : 'UNKNOWN')">AI · {{ objectiveIdExplicit ? stateLabel(orchestrator?.ai_status) : '等待选择目标' }}</span><span class="objective-chip" :title="objectiveId">当前研究目标：{{ objectiveDisplay.name }}</span><TechnicalDetails v-if="objectiveIdExplicit" compact :entries="{ 研究目标技术编号: objectiveDisplay.id }" /><span class="sync-dot" :class="{ refreshing }"></span><span class="sync-copy">{{ refreshing ? '正在刷新' : '只读监控' }}</span><button class="refresh-button" type="button" aria-label="刷新当前页面" @click="refresh">↻</button></div></header>
      <main class="console-content">
        <div v-if="loading && !objectiveList && !dashboard && !daemon && !candidateDetail && !shadow && !dataHealth && !reports && !evolution && !evolutionProposals && !evolutionAIDesign && !candidateProposals && !orchestrator && !aiStatus && !closeout && !governance && !operations" class="loading-state"><span class="loading-orbit"></span><strong>正在读取研究数据</strong><small>页面不会直接读取已登记报告或守护进程检查点。</small></div>
        <div v-if="pageError" class="error-state" role="alert"><span class="error-symbol">!</span><div><strong>数据读取失败</strong><p>{{ pageError.message }}</p><TechnicalDetails compact :entries="{ 错误代码: pageError.code }" /></div><button type="button" @click="refresh">重试</button></div>

        <template v-if="view === 'objectives'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="objectiveList" :state="objectiveList.freshness_state" source="研究目标登记目录" :generated-at="objectiveList.source_generated_at" /></div>
          <section class="surface padded objective-list-surface"><div class="section-heading"><div><span class="eyebrow">已登记目标</span><h2>选择一个研究目标</h2><p>每一行都来自目标登记文件，并优先显示自主研究编排器的实时状态。治理决定、研究进度和运行控制都必须从这里进入具体目标。</p></div><span class="status-chip tone-muted">共 {{ sortedObjectives.length }} 个匹配目标</span></div><section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="objectiveSearch" aria-label="搜索研究目标" placeholder="搜索研究目标名称、状态或编号" /></label><select v-model="objectiveFilter" aria-label="研究目标状态筛选"><option value="ALL">全部状态</option><option value="ACTIVE">进行中</option><option value="GOVERNANCE">等待治理决定</option><option value="TERMINAL">已安全结束</option></select><select v-model="objectiveSort" aria-label="研究目标排序"><option value="created_at">按创建时间</option><option value="name">按研究目标名称</option><option value="state">按当前状态</option></select><button class="filter-button" type="button" @click="objectiveDirection = objectiveDirection === 'asc' ? 'desc' : 'asc'">{{ objectiveDirection === 'asc' ? '正序' : '倒序' }}</button></section><div v-if="sortedObjectives.length" class="objective-list"><article v-for="item in objectivePageItems" :key="item.objective_id" class="objective-card"><div class="objective-card-top"><div><strong>{{ objectiveName(item) }}</strong><TechnicalDetails compact :entries="{ 研究目标技术编号: item.objective_id }" /></div><span class="status-chip" :class="item.governance_pending ? 'tone-governance' : statusTone(item.orchestrator_state)">{{ item.governance_pending ? '等待治理决定' : item.orchestrator_state_zh }}</span></div><dl class="detail-list"><div><dt>编排器状态</dt><dd>{{ item.orchestrator_state_zh }}<small class="objective-source">来源：{{ humanSourceLabel(item.state_source) }}</small></dd></div><div><dt>生命周期登记</dt><dd>{{ item.lifecycle_state_zh }}</dd></div><div><dt>创建时间</dt><dd>{{ formatDate(item.created_at) }}</dd></div><div><dt>预测预算</dt><dd>{{ item.max_total_trials ?? '暂无数据' }} 次</dd></div></dl><p v-if="item.orchestrator_error_message_zh" class="plain-note">状态暂时无法从自主研究编排器读取：{{ item.orchestrator_error_message_zh }}；页面不会把登记状态冒充运行状态。</p><div class="page-actions"><button class="button-secondary" type="button" @click="navigate(objectivePath('/research/dashboard', item.objective_id))">打开研究总览</button><button v-if="item.governance_pending" class="button-primary" type="button" @click="navigate(objectivePath('/research/governance', item.objective_id))">进入治理决策</button></div><TechnicalDetails compact :entries="{ 研究目标技术编号: item.objective_id, 编排器状态: item.orchestrator_state, 状态来源: item.state_source, 是否等待治理: item.governance_pending }" /></article></div><div v-else class="empty-state">当前没有匹配的研究目标</div><div v-if="sortedObjectives.length" class="pagination-bar"><span>第 {{ objectivePage }} / {{ objectivePageCount }} 页</span><button class="button-secondary" type="button" :disabled="objectivePage <= 1" @click="objectivePage--">上一页</button><button class="button-secondary" type="button" :disabled="objectivePage >= objectivePageCount" @click="objectivePage++">下一页</button></div></section>
        </template>

        <template v-else-if="view === 'autonomous'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="orchestrator" :state="orchestrator.freshness_state" source="自主研究编排器" :generated-at="orchestrator.source_generated_at" /></div>
          <section class="autonomous-status surface"><div><span class="eyebrow">当前研究状态</span><h2>{{ topStatus }}</h2><p>状态由自主研究编排器读取；研究守护进程只作为运行层上下文。</p></div><div class="status-pair"><span class="status-chip tone-active">编排状态 · {{ canonicalStateZh }}</span><span class="status-chip tone-muted">本地执行状态 · {{ daemonStateZh }}</span></div></section>
          <section class="dashboard-grid autonomous-grid"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究目标</span><h2>当前研究目标</h2></div><span class="status-chip tone-attention">不可变</span></div><strong class="objective-title">{{ objectiveDisplay.name }}</strong><p class="candidate-summary">{{ canonicalStateZh }}。页面只展示此研究目标已登记的状态、候选和预算，不把其他研究轮次的数据拼接进来。</p><TechnicalDetails :entries="{ 研究目标技术编号: objectiveId, 当前状态: canonicalState, 当前终态原因: orchestrator?.terminal_reason || '当前未登记终态原因' }" /></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">预算与结果</span><h2>本轮研究摘要</h2></div></div><div class="metric-grid compact-metrics"><div><span>已使用 / 总额</span><b>{{ budgetValue('used') }} / {{ budgetValue('total') }}</b></div><div><span>剩余</span><b>{{ budgetValue('remaining') }}</b></div><div><span>研究通过</span><b>{{ countFor('RESEARCH_PASSED') }}</b></div><div><span>有潜力</span><b>{{ countFor('PROMISING') }}</b></div><div><span>尚未正式验证</span><b>{{ closeout?.unevaluated_candidates.count ?? '暂无收官数据' }}</b></div><div><span>稳健 Alpha</span><b>{{ closeout ? (closeout.robust_alpha_established ? '已建立' : '未建立') : '暂无收官数据' }}</b></div></div></article></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">运行与收官</span><h2>当前研究上下文</h2></div><button class="text-button" type="button" @click="navigate('/research/operations')">查看运行控制 →</button></div><dl class="detail-list"><div><dt>当前候选策略</dt><dd>{{ orchestrator?.current_candidate ? nameFor(String(orchestrator.current_candidate.candidate_id || '')) : '当前终态无正在处理的候选策略' }}<TechnicalDetails compact :entries="{ 候选策略技术编号: orchestrator?.current_candidate?.candidate_id || '无' }" /></dd></div><div><dt>当前预测试验</dt><dd>{{ orchestrator?.current_trial?.trial_id || '当前终态无正在处理的预测试验' }}</dd></div><div><dt>终态原因</dt><dd>{{ terminalReasonZh }}<TechnicalDetails compact :entries="{ 原因代码: orchestrator?.terminal_reason || '未提供' }" /></dd></div><div><dt>自动收官</dt><dd>{{ orchestrator?.closeout_complete ? '已完成' : '尚未完成' }} · <button class="text-button" type="button" @click="navigate('/research/closeout')">打开收官页</button></dd></div><div><dt>治理决策</dt><dd>{{ orchestrator?.waiting_for_governance ? '等待人工决定' : '当前无需治理决定' }} · <button v-if="orchestrator?.waiting_for_governance" class="text-button" type="button" @click="navigate('/research/governance')">打开治理页</button></dd></div><div><dt>必须的人工动作</dt><dd>{{ orchestrator?.waiting_for_governance ? '选择并明确确认治理决定' : '当前没有必须的人工动作' }}</dd></div></dl></section>
          <section class="surface pipeline-surface"><div class="section-heading"><div><span class="eyebrow">完整生命周期</span><h2>研究流程</h2><p>候选设计、冻结、结构预检、预测验证、统计判断与最终分类均来自已登记状态。</p></div><button class="text-button" type="button" @click="navigate('/research/pipeline')">查看详细进度 →</button></div><PipelineStepper :stages="pipeline?.stages || [{ stage: 'HYPOTHESIS' }, { stage: 'CANDIDATE' }, { stage: 'FREEZE' }, { stage: 'STRUCTURAL' }, { stage: 'PREDICTIVE' }, { stage: 'STATISTICAL' }, { stage: 'FINAL_CLASSIFICATION' }]" :current-stage="pipeline?.current_stage || orchestrator?.orchestrator_state" /></section>
          <section class="surface event-surface"><div class="section-heading"><div><span class="eyebrow">最近状态变化</span><h2>研究编排器与本地执行时间线</h2></div><span class="helper-copy">页面只显示有限事件尾部，避免长时间轮询和过量信息。</span></div><div v-if="currentEventList.length" class="event-list"><div v-for="event in [...currentEventList].reverse().slice(0, 8)" :key="event.event_id" class="event-item"><span class="event-marker"></span><div><strong>{{ eventLabel(event) }}</strong><p>{{ eventDescription(event) }}</p><TechnicalDetails compact :entries="{ 来源: event.source, 事件编号: event.event_id, 内部状态: event.new_state || event.event_type }" /></div><time>{{ eventTime(event) }}</time></div></div><div v-else class="empty-state">暂无编排器事件</div></section>
          <p class="safety-line">安全边界：真实订单已禁用 · 最终测试集未访问 · 前瞻验证未启动 · 当前页面不创建新目标、不扩展预算。</p>
        </template>

        <template v-else-if="view === 'ai-researcher'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="aiStatus" :state="aiStatus.freshness_state" source="AI 研究状态" :generated-at="aiStatus.source_generated_at" /></div>
          <section class="hero-grid"><article class="surface hero-status"><div class="section-heading"><div><span class="eyebrow">AI 研究调用模式</span><h2>{{ aiInvocationMode?.ai_invocation_mode_zh || aiStatus?.ai_invocation_mode_zh || '手动 AI 交接（推荐）' }}</h2></div><span class="status-chip tone-active">当前模式</span></div><div class="hero-state"><strong>{{ aiStatus?.state_zh || '正在读取 AI 状态' }}</strong><span>{{ aiStatus?.ai_invocation_mode === 'AUTO_CODEX' ? '仅在进入允许的研究设计边界后自动调用 AI 研究员。' : aiStatus?.ai_invocation_mode === 'AI_DISABLED' ? '不会创建 AI 研究任务，也不会消耗后台 AI 额度。' : '不会自动调用 AI；进入设计边界后创建手动任务并等待结果文件。' }}</span></div><label class="mode-picker"><span>更改调用模式</span><select v-model="selectedAiMode" :disabled="aiModeBusy" @change="changeAiMode"><option value="MANUAL_HANDOFF">手动 AI 交接（推荐）</option><option value="AUTO_CODEX">自动调用 AI 研究员</option><option value="AI_DISABLED">AI 研究已禁用</option></select></label><dl class="detail-list"><div><dt>后台 AI 额度消耗</dt><dd>{{ aiStatus?.background_ai_token_consumption ?? 0 }}</dd></div><div><dt>自动恢复</dt><dd>{{ aiStatus?.auto_resume_status ? stateLabel(aiStatus.auto_resume_status) : '由本地编排器处理' }}</dd></div><div><dt>最近结果</dt><dd>{{ aiStatus?.recent_result ? stateLabel(aiStatus.recent_result) : '未运行' }}</dd></div></dl></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">平台验证</span><h2>研究设计能力记录</h2></div><span class="status-chip tone-muted">只读资料</span></div><div class="verification-list"><div><span>功能实现</span><strong>{{ aiStatus?.real_codex_validation?.functional_status || '未提供' }}</strong></div><div><span>合成流程</span><strong>{{ aiStatus?.real_codex_validation?.synthetic_flow_status || '未提供' }}</strong></div><div><span>当前目标检查</span><strong>{{ aiStatus?.real_codex_validation?.real_smoke_test === 'PASS' ? '已通过' : '未执行或待确认' }}</strong></div></div><p class="plain-note">平台能力记录不代表当前研究目标已经完成 AI 设计；当前任务状态以交接编号、结果状态和本地校验为准。</p></article></section>
          <section class="surface padded manual-handoff-card"><div class="section-heading"><div><span class="eyebrow">手动 AI 交接</span><h2>{{ manualAiHandoff?.handoff_id ? '现在需要你操作：是' : '等待进入 AI 研究设计边界' }}</h2><p>{{ manualAiHandoff?.task_purpose || '当前任务只允许一次 PROMISING_FOLLOWUP 研究设计；通过本地校验后才会接入候选。' }}</p></div><span class="status-chip" :class="statusTone(manualAiHandoff?.result_status)">{{ manualAiHandoff?.result_status_zh || '等待任务' }}</span></div><template v-if="manualAiHandoff?.handoff_id"><dl class="detail-list"><div><dt>当前研究轮次</dt><dd>{{ manualAiHandoff.current_round || 'PROMISING_FOLLOWUP' }}</dd></div><div><dt>交接编号</dt><dd><code>{{ manualAiHandoff.manual_handoff_id || manualAiHandoff.handoff_id }}</code></dd></div><div><dt>调用编号</dt><dd><code>{{ manualAiHandoff.invocation_id || '待生成' }}</code></dd></div><div><dt>任务目录</dt><dd><code>{{ manualAiHandoff.task_dir }}</code></dd></div><div><dt>结果文件路径</dt><dd><code>{{ manualAiHandoff.result_path }}</code></dd></div><div><dt>任务生成时间</dt><dd>{{ formatDate(manualAiHandoff.task_created_at) }}</dd></div><div><dt>提示词长度</dt><dd>{{ manualAiHandoff.prompt_character_count }} 字符，约 {{ manualAiHandoff.prompt_token_estimate }} tokens</dd></div></dl><div v-if="manualAiHandoff.validation_started_at || manualAiHandoff.validation_stage" class="validation-observability"><div><span>校验状态</span><strong>{{ manualAiHandoff.validation_status_zh }}</strong></div><div><span>当前步骤</span><strong>{{ manualAiHandoff.validation_stage_zh }}</strong></div><div><span>开始时间</span><strong>{{ formatDate(manualAiHandoff.validation_started_at) }}</strong></div><div><span>最后活动</span><strong>{{ formatDate(manualAiHandoff.validation_last_activity_at) }}</strong></div><div><span>完成时间</span><strong>{{ formatDate(manualAiHandoff.validation_finished_at) }}</strong></div></div><p v-if="manualAiHandoff.validation_stale" class="validation-stale">AI 研究结果校验可能已停止，请检查后台研究服务；系统未接入候选，也未消耗预测试验预算。</p><div class="page-actions"><button class="button-primary" type="button" :disabled="!manualAiHandoff.prompt_text" @click="copyManualPrompt">复制 AI 研究提示词</button><button class="button-secondary" type="button" @click="navigate(objectivePath('/research/ai-tasks', objectiveId))">打开 AI 研究任务页</button><button class="button-secondary" type="button" :disabled="manualRescanBusy" @click="rescanManualResult">{{ manualRescanBusy ? '扫描中…' : '重新扫描结果' }}</button></div><p class="plain-note">{{ manualAiHandoff.result_reason_zh }}</p><TechnicalDetails label="查看脱敏资料与输出契约" :raw="technicalEntries({ ai_design_policy: manualAiHandoff.ai_design_policy, allowed_actions_zh: manualAiHandoff.allowed_actions_zh, forbidden_actions_zh: manualAiHandoff.forbidden_actions_zh, allowed_info: manualAiHandoff.allowed_info, output_contract: manualAiHandoff.output_contract })" /></template><template v-else><p class="plain-note">当前没有可复制的手动任务。系统不会为已结束目标重建任务，也不会修改当前研究检查点。</p></template></section>
          <section class="two-column"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">实时活动</span><h2>{{ aiStatus?.runtime_progress?.activity_zh || '等待手动结果文件' }}</h2><p>只展示受限运行诊断，不展示原始提示词、结果内容或绩效数据。</p></div><span class="status-chip" :class="statusTone(aiStatus?.runtime_progress?.status)">{{ aiStatus?.runtime_progress?.status_zh || '等待中' }}</span></div><dl class="detail-list"><div><dt>结果发现时间</dt><dd>{{ formatDate(aiStatus?.runtime_progress?.manifest_detected_at) }}</dd></div><div><dt>最近进度</dt><dd>{{ formatDate(aiStatus?.runtime_progress?.last_progress_at) }}</dd></div><div><dt>运行时间</dt><dd>{{ aiStatus?.runtime_progress?.elapsed_seconds != null ? `${aiStatus.runtime_progress.elapsed_seconds} 秒` : '暂无后台运行' }}</dd></div><div><dt>结果文件状态</dt><dd>{{ manualAiHandoff?.result_status_zh || '等待任务' }}</dd></div></dl></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">AI 研究事件</span><h2>最近研究进展</h2><p>事件先显示中文说明，原始状态收纳在技术信息中。</p></div></div><div v-if="aiProgressEvents.length" class="event-list compact-event-list"><div v-for="event in aiProgressEvents" :key="`ai-${event.event_id}`" class="event-item"><span class="event-marker"></span><div><strong>{{ eventLabel(event) }}</strong><p>{{ eventDescription(event) }}</p><TechnicalDetails compact :entries="{ 事件编号: event.event_id, 内部状态: event.state || event.new_state || event.event_type }" /></div><time>{{ eventTime(event) }}</time></div></div><div v-else class="empty-state">暂无 AI 研究事件</div></article></section>
          <section class="two-column"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">单任务保护</span><h2>一次只处理一个研究任务</h2></div><span class="status-chip tone-success">已启用</span></div><p class="plain-note">{{ aiStatus?.exact_once?.message_zh || '同一研究任务不会并发启动多个 AI 研究员。' }}</p></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">结果隔离</span><h2>不把绩效带入设计</h2></div><span class="status-chip tone-success">已启用</span></div><p class="plain-note">{{ aiStatus?.no_outcome_isolation?.message_zh || 'AI 设计阶段不读取收益、胜率、回撤等私有绩效结果。' }}</p></article></section>
        </template>

        <template v-else-if="view === 'ai-tasks'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="count-badge">{{ aiTasks?.total ?? 0 }} 个任务</span></div>
          <section class="surface padded manual-task-summary"><div class="section-heading"><div><span class="eyebrow">当前交接</span><h2>{{ manualAiHandoff?.handoff_id ? manualAiHandoff.state_zh : '当前没有活动手动任务' }}</h2><p>{{ manualAiHandoff?.result_reason_zh || '先选择一个明确的研究目标，系统才会显示它的 AI 研究任务。' }}</p></div><span class="status-chip" :class="statusTone(manualAiHandoff?.result_status)">{{ manualAiHandoff?.result_status_zh || '暂无任务' }}</span></div><div v-if="manualAiHandoff?.handoff_id" class="page-actions"><button class="button-primary" type="button" @click="copyManualPrompt">复制当前提示词</button><button class="button-secondary" type="button" :disabled="manualRescanBusy" @click="rescanManualResult">{{ manualRescanBusy ? '扫描中…' : '检查结果文件' }}</button></div></section><section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="searchText" aria-label="搜索 AI 研究任务" placeholder="搜索任务编号、状态或执行模式" /></label><select v-model="aiTaskStatus" aria-label="AI 任务结果状态筛选" @change="aiTaskPage = 1; loadView(true)"><option value="ALL">全部结果状态</option><option value="WAITING">等待结果文件</option><option value="FOUND">已发现，等待校验</option><option value="ACCEPTED">已通过并接入</option><option value="INVALID">未通过，等待重跑</option></select><select v-model="aiTaskMode" aria-label="AI 执行模式筛选" @change="aiTaskPage = 1; loadView(true)"><option value="ALL">全部执行模式</option><option value="MANUAL_HANDOFF">手动 AI 交接</option><option value="AUTO_CODEX">自动调用 AI 研究员</option><option value="AI_DISABLED">AI 研究已禁用</option></select><select v-model="aiTaskSort" aria-label="AI 任务排序" @change="aiTaskPage = 1; loadView(true)"><option value="created_at">按创建时间</option><option value="result_status">按结果状态</option><option value="handoff_id">按任务编号</option></select><button class="filter-button" type="button" @click="aiTaskDirection = aiTaskDirection === 'asc' ? 'desc' : 'asc'; aiTaskPage = 1; loadView(true)">{{ aiTaskDirection === 'asc' ? '正序' : '倒序' }}</button></section>
          <section class="surface table-surface"><div class="table-head"><div><strong>AI 研究任务历史</strong><span>显示研究轮次、任务类型、执行模式、结果状态和自动接入情况；不会提供手动候选导入入口。</span></div><label class="search-box compact-search"><span>⌕</span><input v-model="searchText" aria-label="搜索 AI 研究任务" placeholder="搜索任务编号、状态或执行模式" /></label></div><div class="table-scroll"><table class="research-table"><thead><tr><th>研究任务</th><th>所属研究轮次</th><th>任务类型</th><th>当前状态</th><th>创建时间</th><th>AI 执行模式</th><th>结果状态</th><th>候选策略数量</th><th>自动接入状态</th><th>更新时间</th></tr></thead><tbody><tr v-for="item in aiTasks?.items || []" :key="item.handoff_id"><td><code>{{ item.handoff_id }}</code><small class="table-subline">{{ item.invocation_id || '无调用编号' }}</small></td><td>{{ item.objective_id }}</td><td>{{ item.task_type }}</td><td><span class="status-chip" :class="statusTone(item.result_status)">{{ item.result_status_zh }}</span></td><td>{{ formatDate(item.created_at) }}</td><td>{{ item.mode_zh }}</td><td>{{ item.result_status_zh }}</td><td>{{ item.candidate_count }}</td><td>{{ item.ingestion_status_zh }}</td><td>{{ formatDate(item.updated_at) }}</td></tr></tbody></table></div><div v-if="!aiTasks?.items.length" class="empty-state">暂无 AI 研究任务历史</div><div v-if="aiTasks?.total" class="pagination-bar"><span>第 {{ aiTaskPage }} / {{ Math.max(1, Math.ceil(aiTasks.total / 20)) }} 页，共 {{ aiTasks.total }} 条</span><button class="button-secondary" type="button" :disabled="aiTaskPage <= 1" @click="aiTaskPage--; loadView(true)">上一页</button><button class="button-secondary" type="button" :disabled="aiTaskPage >= Math.max(1, Math.ceil(aiTasks.total / 20))" @click="aiTaskPage++; loadView(true)">下一页</button></div></section><section class="surface table-surface"><div class="table-head"><div><strong>AI 研究结果列表</strong><span>结果必须经过本地编排器验证后才会进入研究候选流程。</span></div></div><div class="table-scroll"><table class="research-table"><thead><tr><th>结果状态</th><th>研究任务</th><th>结果路径</th><th>处理说明</th></tr></thead><tbody><tr v-for="item in aiResults?.items || []" :key="`result-${item.handoff_id}`"><td><span class="status-chip" :class="statusTone(item.result_status)">{{ item.result_status_zh }}</span></td><td><code>{{ item.handoff_id }}</code></td><td><code>{{ item.result_path }}</code></td><td>{{ item.result_reason_zh }}</td></tr></tbody></table></div><div v-if="!aiResults?.items.length" class="empty-state">暂无 AI 研究结果记录</div></section>
        </template>

        <template v-else-if="view === 'operations'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="operations" :state="'FRESH'" source="自主研究编排器" :generated-at="operations.source_generated_at" /></div>
          <section class="autonomous-status surface"><div><span class="eyebrow">当前编排状态</span><h2>{{ operations?.state_zh || '正在读取' }}</h2><p>{{ operations?.terminal ? `当前研究目标已进入终态：${operations.terminal_reason_zh}` : '操作只会提交给自主研究编排器服务。' }}</p></div><span class="status-chip" :class="operations?.terminal ? 'tone-attention' : 'tone-active'">{{ operations?.terminal ? '终态锁定' : '可控运行' }}</span></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">可用操作</span><h2>研究运行控制</h2><p>按钮状态来自后端；提交后等待编排器回执，再重新读取状态。</p></div><span class="status-chip tone-muted">仅限本机</span></div><div class="operation-grid"><article v-for="item in operations?.actions || []" :key="item.action" class="operation-card" :class="{ disabled: !item.available }"><div><strong>{{ item.label_zh || actionLabel(item.action) }}</strong><span class="status-chip" :class="item.available ? 'tone-active' : 'tone-muted'">{{ item.available ? '可用' : '不可用' }}</span></div><p>{{ item.description_zh }}</p><button v-if="item.action !== 'STATUS'" class="button-secondary" type="button" :disabled="!item.available || operationBusy" @click="pendingOperation = item.action">{{ item.available ? '需要确认' : '当前不可用' }}</button></article></div><p v-if="operations?.terminal" class="plain-note">当前研究已结束，暂停、恢复、停止、恢复记录和启动均被锁定；不会新建研究目标、预测试验、预算或最终测试。</p></section>
          <TechnicalDetails label="查看运行控制技术字段" :raw="technicalEntries({ objective_id: operations?.objective_id, state: operations?.state, terminal_reason: operations?.terminal_reason, shell_execution_exposed: operations?.shell_execution_exposed })" />
        </template>

        <template v-else-if="view === 'closeout'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><div class="page-heading-badges"><span class="status-chip" :class="closeout ? 'tone-success' : 'tone-attention'">{{ closeout ? '自动收官已完成' : '尚未生成收官记录' }}</span><span v-if="closeout?.conflict" class="status-chip tone-attention">来源存在时间差</span><FreshnessBadge v-if="closeout" :state="closeout.freshness_state" source="收官报告" :generated-at="closeout.source_generated_at" /></div></div>
          <template v-if="closeout"><section class="closeout-banner surface"><div><span class="eyebrow">本轮研究终态</span><h2>{{ closeout.terminal_reason_zh }}</h2><p>本轮研究已停止在安全边界内，下面是自主研究编排器生成的收官记录。</p></div><div class="closeout-state"><strong>{{ closeout.budget?.used ?? '暂无数据' }} / {{ closeout.budget?.total ?? '暂无数据' }}</strong><span>预测试验预算已使用</span></div></section>
          <section class="metric-grid closeout-metrics"><div class="surface"><span>研究通过</span><b>{{ closeout?.research_counts?.RESEARCH_PASSED ?? 0 }}</b></div><div class="surface"><span>有潜力</span><b>{{ closeout?.research_counts?.PROMISING ?? 0 }}</b></div><div class="surface"><span>尚未进行正式预测验证</span><b>{{ closeout?.unevaluated_candidates.count ?? 0 }}</b></div><div class="surface"><span>稳健 Alpha</span><b>{{ closeout?.robust_alpha_display_zh || '尚未建立' }}</b></div><div class="surface"><span>预算剩余</span><b>{{ closeout?.budget?.remaining ?? 0 }}</b></div><div class="surface"><span>预留预算</span><b>{{ closeout?.budget?.reserved ?? 0 }}</b></div></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">有潜力候选</span><h2>收官候选列表</h2><p>“有潜力”不是研究通过、生产建议或买入建议，也不会自动进入最终测试集。</p></div></div><section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="closeoutSearch" aria-label="搜索收官候选" placeholder="搜索候选策略名称、编号或说明" /></label><select v-model="closeoutFilter" aria-label="收官候选状态筛选"><option value="ALL">全部状态</option><option value="PROMISING">有潜力</option></select><select v-model="closeoutSort" aria-label="收官候选排序"><option value="candidate_id">按候选策略编号</option><option value="name">按候选策略名称</option></select><button class="filter-button" type="button" @click="closeoutDirection = closeoutDirection === 'asc' ? 'desc' : 'asc'">{{ closeoutDirection === 'asc' ? '正序' : '倒序' }}</button></section><div class="promising-grid"><article v-for="item in closeoutPageItems" :key="String(item.candidate_id)" class="promising-card"><span class="status-chip tone-attention">有潜力</span><h3>{{ item.display_name_zh }}</h3><p>{{ item.why_promising_zh }}</p><small>{{ item.why_not_research_passed_zh }}</small><TechnicalDetails compact :entries="{ 候选策略技术编号: item.candidate_id, 预测试验技术编号: item.trial_id || '未提供' }" /></article><div v-if="!sortedCloseoutCandidates.length" class="empty-state">当前没有匹配的收官候选</div></div><div v-if="sortedCloseoutCandidates.length" class="pagination-bar"><span>第 {{ closeoutPage }} / {{ closeoutPageCount }} 页，共 {{ sortedCloseoutCandidates.length }} 条</span><button class="button-secondary" type="button" :disabled="closeoutPage <= 1" @click="closeoutPage--">上一页</button><button class="button-secondary" type="button" :disabled="closeoutPage >= closeoutPageCount" @click="closeoutPage++">下一页</button></div></section>
          <section class="two-column"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">未完成候选</span><h2>尚未进行正式预测验证</h2></div><span class="status-chip tone-muted">不等于失败</span></div><p class="plain-note">{{ closeout.unevaluated_candidates.explanation_zh }}</p><p class="helper-copy">共 {{ closeout.unevaluated_candidates.count }} 个候选策略；只有在正式预测验证后才会形成研究分类。</p><TechnicalDetails label="查看未完成候选技术编号" :raw="closeout.unevaluated_candidates.candidate_ids" /></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究边界</span><h2>收官安全检查</h2></div></div><div class="safety-grid"><span>最终测试集<strong>未访问</strong><small>分析、决策、物理访问均为 0</small></span><span>前瞻验证<strong>未启动</strong><small>不会因收官自动开启</small></span><span>真实订单<strong>已禁用</strong><small>不会产生真实交易</small></span></div><p class="plain-note">多重检验与预测试验记录已完成自动对账。</p></article></section><div class="page-actions"><button class="button-primary" type="button" @click="navigate('/research/governance')">进入治理决策 →</button></div></template><section v-else class="surface padded"><div class="section-heading"><div><span class="eyebrow">当前状态</span><h2>当前研究目标尚未生成收官记录</h2></div><span class="status-chip tone-attention">研究尚未收官</span></div><p class="plain-note">当前页面收到的是合法的“尚未收官”响应。页面不会用 0 填充预算、分类或候选结果；请以当前自主研究编排器状态和研究进度为准。</p><p class="helper-copy">当前状态：{{ canonicalStateZh }}</p></section>
        </template>

        <template v-else-if="view === 'governance' && !objectiveIdExplicit">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="status-chip tone-attention">尚未选择研究目标</span></div>
          <section class="surface padded objective-selector"><div class="section-heading"><div><span class="eyebrow">治理入口</span><h2>研究决策列表</h2><p>先从需要人工处理的研究目标中选择一个，再进入具体治理决定；页面不会加载默认目标的决定，也不会让你误操作其他研究轮次。</p></div><span class="status-chip tone-governance">必须明确选择</span></div><section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="governanceSearch" aria-label="搜索研究决策" placeholder="搜索研究目标名称、状态或编号" /></label><select v-model="governanceSort" aria-label="研究决策排序"><option value="created_at">按创建时间</option><option value="name">按研究目标名称</option></select><button class="filter-button" type="button" @click="governanceDirection = governanceDirection === 'asc' ? 'desc' : 'asc'">{{ governanceDirection === 'asc' ? '正序' : '倒序' }}</button></section><div v-if="sortedGovernanceObjectives.length" class="objective-list"><article v-for="item in governancePageItems" :key="item.objective_id" class="objective-card governance-objective-card"><div class="objective-card-top"><div><strong>{{ objectiveName(item) }}</strong><TechnicalDetails compact :entries="{ 研究目标技术编号: item.objective_id }" /></div><span class="status-chip tone-governance">等待治理决定</span></div><p class="plain-note">{{ item.orchestrator_state_zh }} · 状态来自 {{ humanSourceLabel(item.state_source) }}。进入后将只读取这个研究目标的治理决定。</p><div class="page-actions"><button class="button-primary" type="button" @click="navigate(objectivePath('/research/governance', item.objective_id))">打开这个治理决定</button></div></article></div><div v-else class="empty-state">当前没有处于“等待人工治理决定”的研究目标。请先查看研究目标列表。</div><div v-if="sortedGovernanceObjectives.length" class="pagination-bar"><span>第 {{ governancePage }} / {{ governancePageCount }} 页，共 {{ sortedGovernanceObjectives.length }} 条</span><button class="button-secondary" type="button" :disabled="governancePage <= 1" @click="governancePage--">上一页</button><button class="button-secondary" type="button" :disabled="governancePage >= governancePageCount" @click="governancePage++">下一页</button></div><div class="page-actions"><button class="button-secondary" type="button" @click="navigate('/research/objectives')">查看全部研究目标</button></div></section>
        </template>

        <template v-else-if="view === 'governance'">
          <template v-if="structuralGovernanceActive">
            <div class="page-heading"><div><span class="eyebrow">结构预检后的治理</span><h1>研究治理决定</h1><p>{{ structuralGovernance?.status === 'AUTHORIZED' ? '已授权进入第 1 次预测试验，等待启动预测试验。' : structuralGovernance?.status === 'ENDED' ? '当前 Candidate 研究路径已结束，不创建预测 Trial。' : structuralGovernance?.status === 'DEFERRED' ? '预测试验已暂缓，之后仍可再次进行治理确认。' : '结构预检已通过，等待决定是否进入第 1 次预测试验。' }}</p></div><span class="status-chip" :class="structuralGovernance?.status === 'AUTHORIZED' ? 'tone-success' : structuralGovernance?.status === 'ENDED' ? 'tone-muted' : 'tone-governance'">{{ structuralGovernance?.status === 'AUTHORIZED' ? '已授权，等待启动' : structuralGovernance?.status === 'ENDED' ? '研究路径已结束' : structuralGovernance?.status === 'DEFERRED' ? '已暂缓' : '等待人工决定' }}</span></div>
            <section class="surface padded structural-governance-banner" data-testid="structural-governance-panel"><div class="section-heading"><div><span class="eyebrow">结构预检：通过</span><h2>当前候选具备进入预测验证的资格</h2><p>{{ structuralGovernance?.human_explanation_zh || '结构预检已通过，等待决定是否进入正式预测验证。' }}</p></div><span class="status-chip tone-success">预测试验尚未启动</span></div><div class="metric-grid"><div><span>已确认安全样本</span><b>{{ structuralGovernance?.structural?.lower_bound ?? '暂无数据' }}</b></div><div><span>最低要求</span><b>{{ structuralGovernance?.structural?.minimum_required ?? '暂无数据' }}</b></div><div><span>可能样本上限</span><b>{{ structuralGovernance?.structural?.upper_bound ?? '暂无数据' }}</b></div></div><p class="plain-note">结构预检通过不等于预测验证通过，不代表策略有效或盈利。</p></section>
            <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">明确选项</span><h2>下一步由研究治理决定</h2><p>确认只记录治理意图；授权不会自动启动 Trial，最终测试集、前瞻验证和真实订单保持关闭。</p></div><span class="status-chip tone-attention">需要明确确认</span></div><div class="governance-options structural-governance-options"><article v-for="choice in structuralGovernance?.choices || []" :key="String(choice.choice)" class="governance-option"><div><strong>{{ choice.label_zh }}</strong><span class="status-chip" :class="choice.creates_authorization ? 'tone-attention' : 'tone-muted'">{{ choice.creates_authorization ? '只记录授权' : '不创建 Trial' }}</span></div><p>{{ choice.consequence_zh }}</p><dl><div><dt>预测试验</dt><dd>保持 0（授权也不创建 Trial）</dd></div><div><dt>预算</dt><dd>{{ structuralGovernance?.budget_snapshot?.used ?? 0 }} / {{ structuralGovernance?.budget_snapshot?.total ?? '暂无数据' }}，不预留</dd></div></dl><button v-if="['PENDING_HUMAN_DECISION', 'DEFERRED'].includes(String(structuralGovernance?.status || ''))" class="button-secondary" type="button" :disabled="predictiveAuthorizationBusy" @click="preparePredictiveGovernance(String(choice.choice))">{{ choice.choice === 'AUTHORIZE_FIRST_PREDICTIVE_TRIAL' ? '查看并确认授权' : choice.choice === 'DEFER_PREDICTIVE_TRIAL' ? '确认暂缓' : '确认结束当前方向' }}</button><span v-else class="plain-note">治理决定已记录</span></article></div></section>
            <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">安全边界</span><h2>当前不会发生的动作</h2></div></div><div class="tag-row"><span>预测试验：0</span><span>绩效访问：0</span><span>最终测试集：0 / 0 / 0</span><span>前瞻验证：DISABLED</span><span>真实订单：DISABLED</span></div><TechnicalDetails label="查看治理与结构来源" :raw="technicalEntries({ decision_mode: structuralGovernance?.decision_mode, candidate_id: structuralGovernance?.candidate_id, candidate_hash: structuralGovernance?.candidate_hash, reconciliation_ref: structuralGovernance?.reconciliation_ref, final_test_access: structuralGovernance?.final_test_access, prospective: structuralGovernance?.prospective, real_order: structuralGovernance?.real_order })" /></section>
          </template>
          <template v-else>
          <section v-if="pendingGovernance?.action === promisingFollowupAction && !governancePreview" class="surface padded governance-parent-picker">
            <div class="section-heading"><div><span class="eyebrow">第二步 · 选择父候选</span><h2>本轮只验证一个父候选</h2><p>请选择本轮要确认的唯一父候选。其他处于 PROMISING 的候选保持不变，未来可以另行治理。</p></div><span class="status-chip tone-attention">必须选择 1 个</span></div>
            <div v-if="eligibleGovernanceParents.length" class="governance-parent-options">
              <label v-for="parent in eligibleGovernanceParents" :key="`${parent.candidate_id}:${parent.candidate_hash}`" class="governance-parent-option" :class="{ selected: selectedGovernanceParentId === parent.candidate_id }">
                <input type="radio" name="governance-parent-candidate" :value="parent.candidate_id" :checked="selectedGovernanceParentId === parent.candidate_id" :disabled="governanceBusy || Boolean(governanceReceipt)" @change="selectGovernanceParent(parent)" />
                <span class="governance-parent-option-body"><span class="governance-parent-option-title"><CandidateDisplayName :candidate-id="parent.candidate_id" /><MechanismDisplay :family="parent.family_id" :mechanism="parent.mechanism" /></span><span class="governance-parent-option-id"><span>父候选 ID</span><code>{{ parent.candidate_id }}</code></span><span class="governance-parent-option-id"><span>合同 hash</span><code>{{ parent.candidate_hash }}</code></span></span>
              </label>
            </div>
            <p v-else class="plain-note">当前没有可供选择的 canonical PROMISING 父候选，系统不会创建后续目标。</p>
            <p class="governance-parent-policy">选择后，候选编号和合同 hash 会进入不可变方案哈希；页面不能替换候选，也不会依据历史绩效排序。</p>
            <div class="page-actions"><button class="button-secondary" type="button" :disabled="governanceBusy" @click="resetGovernanceSelection">取消选择</button><button class="button-primary" type="button" :disabled="governanceBusy || !selectedGovernanceParent || Boolean(governanceReceipt)" @click="prepareSelectedGovernanceParent">查看不可变研究方案</button></div>
          </section>
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><div class="page-heading-badges"><span class="status-chip tone-governance">后端执行级别 {{ governance?.backend_level || governanceCatalog?.backend_level || '未提供' }}</span><span class="status-chip" :class="availableGovernanceChoices.length ? 'tone-success' : 'tone-muted'">{{ availableGovernanceChoices.length ? '可选择治理方向' : '当前无待处理决定' }}</span></div></div>
          <section class="surface padded governance-banner"><div><span class="eyebrow">安全治理门槛</span><h2>{{ governanceReceipt ? '治理决定已执行' : availableGovernanceChoices.length ? '等待人工选择研究方向' : '当前没有待处理治理决定' }}</h2><p>{{ governanceCatalog?.execution_capability_zh || governance?.execution_capability_zh || '治理接口没有返回当前研究目标的待处理决定；页面不会自行创建选择。' }}</p></div><div class="governance-locks"><span>最终测试集 · {{ governance?.final_test_access ? '保持封存' : '未提供' }}</span><span>前瞻验证 · {{ governance?.prospective || governanceCatalog?.prospective_status || '未提供' }}</span><span>真实订单 · {{ governance?.real_order || governanceCatalog?.real_order_status || '未提供' }}</span></div></section>
          <section v-if="availableGovernanceChoices.length" class="surface padded"><div class="section-heading"><div><span class="eyebrow">第一步 · 选择研究方向</span><h2>下一轮研究方向</h2><p>先选择方向，再查看不可变方案；所有写操作都只通过受保护的治理后端完成。</p></div><span class="status-chip tone-success">明确确认</span></div><div class="governance-options"><article v-for="choice in availableGovernanceChoices" :key="choice.action" class="governance-option" :class="{ selected: pendingGovernance?.action === choice.action }"><div><strong>{{ choice.label_zh }}</strong><span v-if="choice.creates_new_objective" class="status-chip tone-attention">创建新目标</span><span v-else class="status-chip tone-muted">保留当前终态</span></div><p>{{ choice.description_zh }}</p><dl><div><dt>新研究目标</dt><dd>{{ choice.creates_new_objective ? '将创建' : '不会创建' }}</dd></div><div><dt>新预算</dt><dd>{{ choice.requires_new_budget ? '将预注册' : '不会创建' }}</dd></div></dl><button class="button-secondary" type="button" :disabled="governanceBusy || Boolean(governanceReceipt)" @click="prepareGovernance(choice)">{{ governanceBusy && pendingGovernance?.action === choice.action ? '读取方案…' : '查看下一轮研究方案' }}</button></article></div></section><section v-else class="surface padded"><p class="plain-note">当前研究目标尚未进入需要人工治理的终态，因此没有可提交的治理选择。</p></section>
          <section v-if="governancePreview" class="surface padded governance-preview"><div class="section-heading"><div><span class="eyebrow">第二步 · 查看不可变方案</span><h2>{{ governancePreview.governance_action === 'STOP_RESEARCH' ? '结束研究方案' : '下一轮研究方案' }}</h2><p>{{ governancePreview.research_purpose }}</p></div><span class="status-chip tone-governance">方案哈希 {{ governancePreview.preview_hash.slice(0, 12) }}…</span></div><div class="governance-preview-grid"><dl class="detail-list"><div><dt>新研究目标</dt><dd><code>{{ governancePreview.proposed_new_objective_id || '不会创建' }}</code></dd></div><div><dt>执行模式</dt><dd>{{ governancePreview.execution_mode === 'CREATE_AND_ACTIVATE' ? '创建并具备自动启动资格' : '仅创建，不启动' }}</dd></div><div><dt>父研究证据</dt><dd>{{ governancePreview.parent_lineage.length }} 条不可变来源关系</dd></div><div><dt>机制范围</dt><dd>{{ governancePreview.mechanism_scope.join('、') }}</dd></div><div><dt>合法因子范围</dt><dd>{{ governancePreview.allowed_factor_scope?.length || 0 }} 个已验证可执行因子</dd></div></dl><dl class="detail-list"><div><dt>新预算</dt><dd>{{ governancePreview.budget_proposal.proposed_total_predictive_budget || 0 }} 次预测试验</dd></div><div><dt>预算依据</dt><dd>{{ governancePreview.budget_proposal.reason_zh }}</dd></div><div><dt>多重检验家族</dt><dd><code>{{ governancePreview.multiple_testing_family.family_id || '不创建' }}</code></dd></div><div><dt>AI 绩效边界</dt><dd>只接收脱敏语义，不接收具体历史绩效</dd></div></dl></div><section class="parent-candidate-preview"><div class="parent-candidate-heading"><strong>父候选身份</strong><span>{{ governancePreview.parent_candidate_identity_refs.length }} 个</span></div><div v-if="governancePreview.parent_candidate_identity_refs.length" class="parent-candidate-list"><article v-for="parent in governancePreview.parent_candidate_identity_refs" :key="`${parent.candidate_id}:${parent.candidate_hash}`" class="parent-candidate-item"><CandidateDisplayName :candidate-id="parent.candidate_id" /><div><span>父候选 ID</span><code>{{ parent.candidate_id }}</code></div><div><span>hash</span><code>{{ parent.candidate_hash }}</code></div><div><span>机制方向</span><MechanismDisplay :family="parent.family_id" :mechanism="parent.mechanism" /></div></article></div><p v-else class="plain-note">无</p><p class="parent-candidate-lock">父候选身份已进入方案哈希，确认时不可替换。</p></section><div class="governance-safety-strip"><span>最终测试集 · 未访问</span><span>前瞻验证 · 未启用</span><span>真实订单 · 未启用</span></div><div class="page-actions"><button class="button-secondary" type="button" :disabled="governanceBusy" @click="resetGovernanceSelection">重新选择</button><button class="button-primary" type="button" :disabled="governanceBusy" @click="openGovernanceConfirmation">{{ governancePreview.governance_action === 'STOP_RESEARCH' ? '确认结束研究' : '创建并启动下一轮研究' }}</button></div></section>
          <section v-if="governanceReceipt" class="surface padded governance-receipt"><div class="section-heading"><div><span class="eyebrow">第三步 · 执行回执</span><h2>治理决定已安全执行</h2><p>{{ governanceReceipt.message_zh || '执行结果已写入受保护回执。重复提交不会创建重复目标。' }}</p></div><span class="status-chip tone-success">{{ governanceReceipt.idempotent ? '重复提交已防重复处理' : '执行完成' }}</span></div><dl class="detail-list"><div><dt>执行编号</dt><dd><code>{{ governanceReceipt.execution_id }}</code></dd></div><div><dt>新研究目标</dt><dd><code>{{ governanceReceipt.new_objective_id || '不会创建' }}</code></dd></div><div><dt>新预算</dt><dd><code>{{ governanceReceipt.new_budget_id || '不会创建' }}</code></dd></div><div><dt>研究编排器</dt><dd>{{ governanceReceipt.orchestrator_activation?.status === 'STARTED' || governanceReceipt.orchestrator_activation?.status === 'ALREADY_RUNNING' ? '已启动，后续会进入 AI 研究设计边界' : governanceReceipt.new_objective_id ? '已具备观察与接管条件' : '保持终止' }}</dd></div></dl></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">安全边界</span><h2>本页面不会执行的动作</h2></div></div><div class="tag-row"><span>不会修改旧研究目标</span><span>不会重置旧预算</span><span>不会打开最终测试集</span><span>不会启动前瞻验证</span><span>不会启用真实订单</span><span>不会人工直接调用 Codex</span></div><TechnicalDetails label="查看治理来源与技术字段" :raw="technicalEntries({ decision_id: governanceCatalog?.decision_id, objective_id: governanceCatalog?.objective_id, source_state: governanceCatalog?.source_state, receipt: governanceReceipt })" /></section>
          </template>
        </template>

        <template v-else-if="view === 'dashboard'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="dashboard" :state="dashboard.freshness_state" :source="dashboard.source_id" :generated-at="dashboard.source_generated_at" /></div>
           <section class="hero-grid">
             <article class="surface hero-status"><div class="section-heading"><div><span class="eyebrow">当前研究状态</span><h2>当前正在做什么</h2></div><ResearchStatusDisplay v-if="dashboard" :state="dashboard.orchestrator_state" /></div><div v-if="dashboard" class="hero-state"><strong>{{ dashboard.orchestrator_state_zh || dashboard.display?.orchestrator_state_zh || statusDescription(dashboard.orchestrator_state) }}</strong><span>{{ dashboard.research_running ? '系统正在执行研究流程。' : '当前没有新的研究执行动作。' }}</span></div><div class="hero-foot"><span>当前阶段</span><b>{{ dashboard ? stageLabel(dashboard.stage) : '暂无数据' }}</b><span>后台研究守护进程</span><b>{{ dashboard?.display?.daemon_state_zh || statusDescription(dashboard?.daemon_state) }}</b></div></article>
             <article class="surface hero-candidate"><div class="section-heading"><div><span class="eyebrow">当前候选策略</span><h2>当前候选策略</h2></div><span class="status-chip tone-attention">只读观察</span></div><template v-if="dashboard?.current_candidate_id"><CandidateDisplayName :candidate-id="dashboard.current_candidate_id" :summary="currentCandidateDisplay.summary" /><p class="candidate-summary">{{ currentCandidateDisplay.summary }}</p><div class="candidate-meta"><span>研究机制</span><strong>{{ currentCandidateDisplay.mechanism.label }}</strong><span>持有周期</span><strong>{{ currentCandidate?.holding_horizon ? `${currentCandidate.holding_horizon} 个交易日` : '暂无数据' }}</strong></div><TechnicalDetails :entries="{ 候选策略技术编号: dashboard.current_candidate_id, 身份哈希: currentCandidate?.candidate_hash || '未提供' }" /></template><template v-else><strong>当前没有正在处理的候选策略</strong><p class="candidate-summary">{{ canonicalStateZh }}；候选队列不会被当作当前处理对象展示。</p></template></article>
           </section>
           <section v-if="dashboard?.structural?.status === 'PASS' && !(dashboard?.predictive_trial_count ?? 0)" class="surface padded structural-governance-banner" data-testid="dashboard-structural-pass"><div class="section-heading"><div><span class="eyebrow">结构预检：通过</span><h2>当前候选已满足进入正式预测验证的结构门槛</h2><p>这只说明历史样本、时点一致性和执行条件已经具备资格，不代表策略已经证明有效。</p></div><span class="status-chip tone-success">样本完整性：通过</span></div><div class="metric-grid"><div><span>已确认安全样本</span><b>{{ dashboard.structural.lower_bound ?? '暂无数据' }}</b></div><div><span>可能样本上限</span><b>{{ dashboard.structural.upper_bound ?? '暂无数据' }}</b></div><div><span>最低要求</span><b>{{ dashboard.structural.minimum_required ?? '暂无数据' }}</b></div><div><span>预测试验</span><b>{{ dashboard.predictive_trial_count ?? 0 }}</b></div></div><p class="plain-note">结构预检已通过，等待决定是否进入正式预测验证。最终测试集未访问，前瞻验证与真实订单保持关闭。</p><div class="page-actions"><button class="button-primary" type="button" @click="navigate(objectivePath('/research/governance', objectiveId))">打开治理决定</button></div></section>
           <section v-if="dashboard?.predictive_trial_recovery?.available" class="surface padded predictive-trial-recovery-panel" data-testid="dashboard-predictive-trial-recovery"><div class="section-heading"><div><span class="eyebrow">工程恢复入口</span><h2>第 1 次预测试验因工程问题未完成</h2><p>工程问题已修复，可以恢复同一次预测试验。策略表现尚未被判定为失败；恢复前仍需人工二次确认。</p></div><span class="status-chip tone-success">可以申请恢复</span></div><dl class="detail-list"><div><dt>恢复范围</dt><dd>同一 Trial · 同一 Candidate · 同一冻结合同</dd></div><div><dt>预算处理</dt><dd>已使用 1 次，剩余 3 次；不再次扣预算</dd></div><div><dt>安全边界</dt><dd>不创建 Trial #2 · Final Test 0 / 0 / 0 · Prospective 关闭 · Real Order 禁用</dd></div></dl><div class="page-actions"><button class="button-primary" type="button" :disabled="predictiveTrialResumeBusy" @click="preparePredictiveTrialResume">{{ predictiveTrialResumeBusy ? '正在读取恢复预览…' : '恢复第 1 次预测试验' }}</button></div></section>
           <section class="surface pipeline-surface"><div class="section-heading"><div><span class="eyebrow">研究流程</span><h2>研究流程</h2><p>每个阶段的状态均来自已登记的研究记录。</p></div><button class="text-button" type="button" @click="navigate('/research/pipeline')">查看进度 →</button></div><PipelineStepper :stages="pipeline?.stages || [{ stage: 'HYPOTHESIS' }, { stage: 'CANDIDATE' }, { stage: 'FREEZE' }, { stage: 'STRUCTURAL' }, { stage: 'PREDICTIVE' }, { stage: 'STATISTICAL' }, { stage: 'FINAL_CLASSIFICATION' }]" :current-stage="dashboard?.stage" /></section>
          <section class="dashboard-grid"><article class="surface"><div class="section-heading"><div><span class="eyebrow">预测试验预算</span><h2>预测试验预算</h2></div><FreshnessBadge v-if="budget" :state="budget.freshness_state" :source="budget.source_id" :generated-at="budget.source_generated_at" /></div><BudgetMeter :used="budget?.used ?? dashboard?.budget_used" :reserved="budget?.reserved" :available="budget?.remaining ?? dashboard?.budget_remaining" :total="budget?.total ?? dashboard?.budget_total" :conflict="budget?.conflict || dashboard?.budget_conflict" /><p class="helper-copy">为了避免反复试验导致过拟合，本轮研究只允许有限次数的正式预测验证。</p></article><article class="surface"><div class="section-heading"><div><span class="eyebrow">研究结果</span><h2>研究结果摘要</h2></div></div><div class="outcome-row"><span class="outcome-icon success">✓</span><div><strong>研究通过</strong></div><b>{{ dashboard?.research_passed_count ?? '暂无数据' }}</b></div><div class="outcome-row"><span class="outcome-icon attention">~</span><div><strong>有潜力</strong></div><b>{{ dashboard?.promising_count ?? '暂无数据' }}</b></div><p class="plain-note">“有潜力”不等于买入建议；研究通过也不代表生产交易授权。</p></article><HumanActionPanel :action="dashboard?.required_human_action" :reason-code="dashboard?.required_human_action ? handoff?.reason_code : null" :allowed="displayActionForHandoff()" :forbidden="handoff?.forbidden_actions" /></section>
          <section class="health-grid"><article class="surface health-summary"><div class="section-heading"><div><span class="eyebrow">系统健康</span><h2>运行与数据健康</h2></div><button class="text-button" type="button" @click="navigate('/data-health')">查看数据 →</button></div><div class="health-items"><div><span class="health-dot" :class="statusTone(dashboard?.resource_health)"></span><strong>研究守护进程资源</strong><small>{{ displayState(dashboard?.resource_health).label }}</small></div><div><span class="health-dot" :class="statusTone(dashboard?.shadow_latest_state)"></span><strong>每日研究观察</strong><small>{{ displayState(dashboard?.shadow_latest_state).label }}</small></div><div><span class="health-dot" :class="statusTone(dashboard?.data_health_state)"></span><strong>数据健康</strong><small>{{ displayState(dashboard?.data_health_state).label }}</small></div></div></article><article class="surface shadow-summary"><span class="eyebrow">每日研究观察</span><strong>今日研究观察</strong><span>{{ shadow?.available && shadow?.trade_date ? formatDate(shadow.trade_date) : '当前目标暂无绑定观察' }} · {{ shadow?.available ? `${shadow.raw_signal_count} 个原始信号` : '未使用全局数据填充' }}</span><p>{{ shadow?.warning_zh || '这是研究观察结果，不是买入推荐，也不会产生真实订单。' }}</p><button class="text-button" type="button" @click="navigate('/shadow')">打开观察 →</button></article></section>
        </template>

        <template v-else-if="view === 'pipeline'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="pipeline" :state="pipeline.freshness_state" :source="pipeline.source_id" :generated-at="pipeline.source_generated_at" /></div>
          <section class="execution-verdict" :class="`execution-${String(pipeline?.execution?.status || 'unknown').toLowerCase()}`">
            <div><span class="eyebrow">实际执行结论</span><h2>{{ pipeline?.execution?.status_zh || '尚无执行记录' }}</h2><p v-if="pipeline?.execution?.predictive_reason_zh">{{ pipeline.execution.predictive_reason_zh }}</p><p v-else-if="pipeline?.execution?.structural_reason_code">{{ pipelineStructuralReason.description }}</p><p v-else-if="pipeline?.execution?.structural_reason_zh">{{ pipeline.execution.structural_reason_zh }}</p></div>
            <dl>
              <div><dt>后台进程</dt><dd>{{ pipeline?.execution?.process_state_zh || '未知' }}<code v-if="pipeline?.execution?.process_pid">PID {{ pipeline.execution.process_pid }}</code></dd></div>
              <div><dt>结构预检</dt><dd>{{ pipeline?.execution?.structural_status_zh || '未运行' }}<code v-if="pipeline?.execution?.structural_reason_code">{{ pipeline.execution.structural_reason_code }}</code></dd></div>
              <div><dt>预测试验</dt><dd>{{ pipeline?.execution?.predictive_status_zh || '未运行' }}</dd></div>
              <div><dt>最终分类</dt><dd>{{ pipeline?.execution?.predictive_classification_zh || '尚未分类' }}</dd></div>
              <div v-if="pipeline?.execution?.predictive_reason_zh"><dt>分类原因</dt><dd>{{ pipeline.execution.predictive_reason_zh }}<code v-if="pipeline.execution.predictive_reason_codes?.length">{{ pipeline.execution.predictive_reason_codes[0] }}</code></dd></div>
              <div><dt>完成时间</dt><dd>{{ formatDate(pipeline?.execution?.completed_at) }}</dd></div>
            </dl>
          </section>
          <section v-if="['PENDING_HUMAN_DECISION', 'DEFERRED', 'AUTHORIZED', 'ENDED'].includes(String(pipeline?.governance_readiness?.status || ''))" class="surface padded structural-governance-banner" data-testid="pipeline-governance-readiness"><div class="section-heading"><div><span class="eyebrow">治理准备状态</span><h2>{{ pipeline?.governance_readiness?.status === 'AUTHORIZED' ? '已授权进入第 1 次预测试验，等待启动。' : pipeline?.governance_readiness?.status === 'ENDED' ? '当前 Candidate 研究路径已结束。' : pipeline?.governance_readiness?.status === 'DEFERRED' ? '预测试验已暂缓。' : '结构预检已通过，等待治理决定。' }}</h2><p>{{ pipeline?.governance_readiness?.human_explanation_zh }}</p></div><span class="status-chip" :class="pipeline?.governance_readiness?.status === 'AUTHORIZED' ? 'tone-success' : pipeline?.governance_readiness?.status === 'ENDED' ? 'tone-muted' : 'tone-governance'">{{ pipeline?.governance_readiness?.status_zh }}</span></div><div class="metric-grid"><div><span>结构样本</span><b>{{ pipeline?.governance_readiness?.structural?.lower_bound ?? '暂无数据' }} / {{ pipeline?.governance_readiness?.structural?.upper_bound ?? '暂无数据' }}</b></div><div><span>最低要求</span><b>{{ pipeline?.governance_readiness?.structural?.minimum_required ?? '暂无数据' }}</b></div><div><span>预测试验</span><b>{{ pipeline?.governance_readiness?.predictive_trials_created ?? 0 }}</b></div></div><p class="plain-note">下一步：{{ pipeline?.governance_readiness?.next_action_zh }}</p><div class="page-actions"><button v-if="pipeline?.governance_readiness?.status === 'PENDING_HUMAN_DECISION' || pipeline?.governance_readiness?.status === 'DEFERRED'" class="button-primary" type="button" @click="navigate(objectivePath('/research/governance', objectiveId))">打开治理决定</button></div></section>
          <section class="surface padded structural-reconciliation-panel">
            <div class="section-heading"><div><span class="eyebrow">结构修复入口</span><h2>重新进行结构预检</h2><p>{{ pipeline?.structural_reconciliation?.reason_zh || '正在检查候选是否允许重新进行结构预检。' }}</p></div><span class="status-chip" :class="pipeline?.structural_reconciliation?.available ? 'tone-success' : 'tone-muted'">{{ pipeline?.structural_reconciliation?.status_zh || '检查中' }}</span></div>
            <dl class="detail-list"><div><dt>执行范围</dt><dd>只重新计算结构证据并对账结果</dd></div><div><dt>当前候选</dt><dd>{{ nameFor(pipeline?.structural_reconciliation?.candidate_id) }}<CandidateId :value="pipeline?.structural_reconciliation?.candidate_id" /></dd></div><div><dt>安全边界</dt><dd>不访问预测绩效 · 不创建 Trial · 不改变预算</dd></div></dl>
            <div class="page-actions"><button class="button-primary" type="button" :disabled="!pipeline?.structural_reconciliation?.available || structuralReconciliationBusy" @click="structuralReconciliationConfirmOpen = true">{{ structuralReconciliationBusy ? '重新检查中…' : pipeline?.structural_reconciliation?.action_zh || '检查重新预检条件' }}</button></div>
          </section>
          <section class="surface padded predictive-authorization-panel">
            <div class="section-heading"><div><span class="eyebrow">人工执行入口</span><h2>正式预测验证授权</h2><p>{{ pipeline?.predictive_authorization?.reason_zh || '正在检查候选是否满足人工授权条件。' }}</p></div><span class="status-chip" :class="pipeline?.predictive_authorization?.available ? 'tone-success' : 'tone-muted'">{{ pipeline?.predictive_authorization?.status_zh || '检查中' }}</span></div>
            <dl class="detail-list"><div><dt>治理范围</dt><dd>一个冻结 Candidate · 第 1 次预测试验入口</dd></div><div><dt>当前候选</dt><dd>{{ nameFor(pipeline?.predictive_authorization?.candidate_id) }}<CandidateId :value="pipeline?.predictive_authorization?.candidate_id" /></dd></div><div><dt>确认后</dt><dd>只记录治理决定，不创建 Trial、不预留预算、不访问绩效</dd></div><div><dt>安全边界</dt><dd>Final Test 关闭 · Prospective 关闭 · Real Order 禁用</dd></div></dl>
            <div class="page-actions"><button class="button-primary" type="button" :disabled="!pipeline?.predictive_authorization?.available || predictiveAuthorizationBusy" @click="preparePredictiveGovernance('AUTHORIZE_FIRST_PREDICTIVE_TRIAL')">{{ predictiveAuthorizationBusy ? '提交中…' : pipeline?.predictive_authorization?.action_zh || '检查授权条件' }}</button></div>
          </section>
          <section v-if="pipeline?.predictive_trial_recovery?.available" class="surface padded predictive-trial-recovery-panel" data-testid="predictive-trial-recovery-panel">
            <div class="section-heading"><div><span class="eyebrow">工程恢复入口</span><h2>第 1 次预测试验因工程问题未完成。</h2><p>失败阶段：事件物化；失败对象：BENCHMARK_SENTIMENT_BREAKOUT_EVENT_V1。这不代表候选策略预测表现失败。</p></div><span class="status-chip tone-success">工程问题已修复</span></div>
            <dl class="detail-list"><div><dt>恢复范围</dt><dd>同一个 Trial #1 · 同一个 Candidate · 同一个冻结 Trial Contract</dd></div><div><dt>预算状态</dt><dd>已使用 1 · 已预留 0 · 剩余 3；不再次扣除预测预算</dd></div><div><dt>恢复后仍不会</dt><dd>创建 Trial #2 · 重复 Multiple Testing 登记 · 修改 Candidate · 访问 Final Test</dd></div></dl>
            <div class="page-actions"><button data-testid="predictive-trial-resume-button" class="button-primary" type="button" :disabled="predictiveTrialResumeBusy" @click="preparePredictiveTrialResume">{{ predictiveTrialResumeBusy ? '正在读取恢复预览…' : '恢复第 1 次预测试验' }}</button></div>
          </section>
          <section v-if="pipeline?.governance_readiness?.status === 'AUTHORIZED' && !pipeline?.predictive_trial_recovery?.available" class="surface padded predictive-trial-start-panel" data-testid="predictive-trial-start-panel">
            <div class="section-heading"><div><span class="eyebrow">授权后的正式动作</span><h2>启动第 1 次预测试验</h2><p>授权与运行是两个独立动作；启动前系统会再次核对所有 canonical 安全条件。</p></div><span class="status-chip" :class="pipeline?.predictive_trial_start?.available ? 'tone-success' : 'tone-muted'">{{ pipeline?.predictive_trial_start?.available ? '可以启动' : pipeline?.predictive_trial_start?.status_zh || '当前不可启动' }}</span></div>
            <dl class="detail-list"><div><dt>结构预检</dt><dd>{{ pipeline?.predictive_trial_start?.structural?.lower_bound ?? '暂无数据' }} / {{ pipeline?.predictive_trial_start?.structural?.upper_bound ?? '暂无数据' }} / {{ pipeline?.predictive_trial_start?.structural?.status || '未确认' }}</dd></div><div><dt>最低要求</dt><dd>{{ pipeline?.predictive_trial_start?.structural?.minimum_required ?? '暂无数据' }}</dd></div><div><dt>预测预算</dt><dd>{{ pipeline?.predictive_trial_start?.budget?.used ?? 0 }} / {{ pipeline?.predictive_trial_start?.budget?.total ?? '暂无数据' }}，启动后预留 1 次</dd></div><div><dt>不会执行</dt><dd>不会访问最终测试集、启用 Prospective、真实下单或调用 AI 修改策略</dd></div></dl>
            <div class="page-actions"><button data-testid="predictive-trial-start-button" class="button-primary" type="button" :disabled="!pipeline?.predictive_trial_start?.available || predictiveTrialStartBusy" @click="preparePredictiveTrialStart">{{ predictiveTrialStartBusy ? '正在读取启动预览…' : '启动第 1 次预测试验' }}</button></div>
          </section>
          <section class="two-column"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究生命周期</span><h2>研究生命周期</h2></div><ResearchStatusDisplay :state="pipeline?.execution?.status || pipeline?.current_state" /></div><PipelineStepper :stages="pipeline?.stages || []" :current-stage="pipeline?.current_stage" /><div class="pipeline-context"><span>已冻结候选策略</span><b>{{ pipeline?.candidate_count ?? '暂无数据' }}</b><span>已有预测试验</span><b>{{ pipeline?.trial_count ?? '暂无数据' }}</b></div></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">当前研究上下文</span><h2>{{ pipeline?.current_state_zh || '当前研究上下文' }}</h2></div></div><dl class="detail-list"><div><dt>当前精确状态</dt><dd>{{ pipeline?.current_state_zh || '暂无数据' }}</dd></div><div><dt>当前生命周期阶段</dt><dd>{{ pipeline ? stageLabel(pipeline.current_stage) : '暂无数据' }}</dd></div><div><dt>下一步</dt><dd>{{ pipeline?.next_action_zh || '暂无数据' }}</dd></div><div><dt>状态来源</dt><dd>{{ pipeline?.state_source || '暂无数据' }}</dd></div><div><dt>批次数量</dt><dd>{{ pipeline?.batch_ids.length ?? '暂无数据' }}</dd></div><div><dt>最近候选策略</dt><dd>{{ nameFor(pipeline?.execution?.candidate_id || pipeline?.current_candidate_id) }}<CandidateId :value="pipeline?.execution?.candidate_id || pipeline?.current_candidate_id" /></dd></div></dl><p v-if="pipeline?.last_error" class="plain-note">最近一次记录的问题：{{ pipeline.last_error }}；当前状态仍以自主研究编排器检查点为准。</p><p v-else-if="pipeline?.execution?.status === 'STRUCTURAL_FAILED'" class="plain-note">{{ pipelineStructuralReason.description }}</p><p v-else class="plain-note">页面只解释已登记的研究状态，不根据预算推算研究进度。</p><button v-if="operationByName('START_AI')?.available" class="button-primary" type="button" :disabled="operationBusy" @click="pendingOperation = 'START_AI'">启动 AI 研究调用</button></article></section>
          <section class="surface padded pipeline-ai-progress"><div class="section-heading"><div><span class="eyebrow">AI 实时进度</span><h2>{{ aiStatus?.runtime_progress?.activity_zh || aiStatus?.state_zh || '正在读取 AI 研究进度' }}</h2><p>上面的生命周期步骤是研究阶段映射；这里显示当前研究目标的 AI 调用心跳。</p></div><span class="status-chip" :class="statusTone(aiStatus?.runtime_progress?.status || aiStatus?.state)">{{ aiStatus?.runtime_progress?.status_zh || aiStatus?.state_zh || '状态未提供' }}</span></div><div class="pipeline-ai-summary"><dl class="detail-list"><div><dt>调用编号</dt><dd>{{ aiStatus?.invocation_id || '当前没有活动调用' }}</dd></div><div><dt>重试次数</dt><dd>{{ aiStatus?.attempt ?? 0 }} / {{ aiStatus?.max_attempts || '未提供' }}</dd></div><div><dt>最近进度</dt><dd>{{ formatDate(aiStatus?.runtime_progress?.last_progress_at) }}</dd></div><div><dt>AI 研究结果清单</dt><dd>{{ aiStatus?.runtime_progress?.manifest_detected_at ? '已发现，等待校验' : '尚未发现' }}</dd></div></dl><button class="button-secondary" type="button" @click="navigate(objectivePath('/research/ai-researcher', objectiveId))">查看 AI 研究员详情 →</button></div></section>
          <section class="surface event-surface"><div class="section-heading"><div><span class="eyebrow">最近研究事件</span><h2>最近研究事件</h2></div><span class="helper-copy">先显示中文说明；原始事件信息可在技术信息中查看。</span></div><div v-if="pipeline?.recent_events.length" class="event-list"><div v-for="event in [...pipeline.recent_events].reverse()" :key="String(event.event_id || event.timestamp || Math.random())" class="event-item"><span class="event-marker"></span><div><strong>{{ eventLabel(event) }}</strong><p>{{ eventDescription(event) }}</p><TechnicalDetails compact :entries="{ 事件编号: event.event_id || '未提供', 内部状态: event.new_state || event.event_type || '未提供', 原因代码: event.reason_code || '未提供' }" /></div><time>{{ formatDate(event.timestamp || event.created_at) }}</time></div></div><div v-else class="empty-state">暂无研究事件</div></section>
        </template>

        <template v-else-if="view === 'candidates'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="count-badge">{{ sortedCandidates.length }} / {{ candidates.length }} 个候选策略</span></div>
          <section class="surface padded scope-overview"><div><span>当前 Follow-up 确认候选</span><strong>{{ candidateScopeSummary?.current_followup_candidate_count ?? 0 }} / {{ candidateScopeSummary?.planned_confirmation_candidate_limit ?? '暂无数据' }}</strong></div><div><span>父级有潜力策略</span><strong>{{ candidateScopeSummary?.parent_promising_reference_count ?? 0 }}</strong></div><div><span>历史 AI 记录（超出当前范围）</span><strong>{{ candidateScopeSummary?.historical_ai_record_count ?? 0 }}</strong></div></section>
          <section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="searchText" aria-label="搜索候选策略" placeholder="搜索候选策略名称、机制或因子" /></label><button v-for="filter in candidateFilters" :key="filter[0]" type="button" :class="['filter-button', { active: candidateFilter === filter[0] }]" @click="candidateFilter = filter[0]">{{ filter[1] }}</button><select v-model="mechanismFilter" aria-label="机制筛选"><option value="ALL">全部机制</option><option v-for="option in mechanismOptions" :key="option" :value="option">{{ displayMechanism(option).label }}</option></select><select v-model="candidateSort" aria-label="候选排序"><option value="candidate_id">按候选编号</option><option value="mechanism">按研究机制</option><option value="classification">按最终分类</option></select><button class="filter-button" type="button" @click="candidateDirection = candidateDirection === 'asc' ? 'desc' : 'asc'">{{ candidateDirection === 'asc' ? '正序' : '倒序' }}</button></section>
          <section class="surface table-surface"><div class="table-head"><div><strong>候选策略清单</strong><span>支持搜索、状态筛选、排序与分页；当前 Follow-up、父级引用和历史 AI 记录分开标识。</span></div><FreshnessBadge v-if="candidates[0]" :state="candidates[0].freshness_state" :source="candidates[0].source_id" :generated-at="candidates[0].source_generated_at" /></div><div class="table-scroll"><table class="research-table"><thead><tr><th>候选策略</th><th>当前范围</th><th>研究机制</th><th>当前阶段</th><th>结构状态</th><th>预测状态</th><th>最终分类</th><th>持有周期</th><th>原因摘要</th></tr></thead><tbody><tr v-for="item in candidatePageItems" :key="item.candidate_id" tabindex="0" @click="navigate(`/research/candidates/${encodeURIComponent(item.candidate_id)}`)" @keydown.enter="navigate(`/research/candidates/${encodeURIComponent(item.candidate_id)}`)"><td><CandidateDisplayName :candidate-id="item.candidate_id" :summary="candidatePresentation(item).summary" /><CandidateId :value="item.candidate_id" :hash="item.candidate_hash" /></td><td><span class="status-chip" :class="item.is_current_followup ? 'tone-success' : 'tone-attention'">{{ item.scope_status_zh }}</span><small class="table-subline">{{ item.scope_reason_zh }}</small></td><td><MechanismDisplay :family="item.mechanism_family" /></td><td><ResearchStatusDisplay :state="item.pipeline_state" /></td><td><ResearchStatusDisplay :state="item.structural_status" /></td><td><ResearchStatusDisplay :state="item.predictive_trial_status" /></td><td><ClassificationBadge :value="item.effective_classification" /></td><td>{{ item.holding_horizon ? `${item.holding_horizon} 日` : '暂无数据' }}</td><td><ReasonCodeDisplay v-if="item.reasons[0]" :code="item.reasons[0].reason_code" :description="item.reasons[0].explanation_zh" compact /><span v-else class="data-gap">暂无原因</span></td></tr></tbody></table></div><div v-if="!sortedCandidates.length" class="empty-state">暂无匹配的候选策略</div><div v-else class="pagination-bar"><span>第 {{ candidatePage }} / {{ candidatePageCount }} 页</span><button class="button-secondary" type="button" :disabled="candidatePage <= 1" @click="candidatePage--">上一页</button><button class="button-secondary" type="button" :disabled="candidatePage >= candidatePageCount" @click="candidatePage++">下一页</button></div></section>
        </template>

        <template v-else-if="view === 'candidate-detail'">
          <div class="page-heading detail-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ detailDisplay.name }}</h1><p>候选策略 · {{ detailDisplay.summary }}</p></div><button class="button-secondary" type="button" @click="navigate('/research/candidates')">返回候选清单</button></div>
          <section class="surface detail-hero"><div><span class="eyebrow">候选策略概览</span><h2>{{ detailDisplay.name }}</h2><p>{{ detailDisplay.summary }}</p></div><div class="detail-hero-meta"><span>当前结构状态</span><ResearchStatusDisplay :state="structural?.status || candidateDetail?.structural?.status" /><span>最终分类</span><ClassificationBadge :value="candidateFor(candidateDetail?.candidate_id)?.effective_classification" /></div></section>
          <section class="detail-grid"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">策略逻辑</span><h2>策略逻辑</h2></div></div><dl class="detail-list readable-list"><div><dt>这个策略想找什么股票？</dt><dd>{{ detailDisplay.summary }}</dd></div><div><dt>什么时候产生信号？</dt><dd>{{ timingLabel(candidateDetail?.semantics?.entry_timing?.generated_at) }}<span v-if="candidateDetail?.semantics?.entry_timing" class="secondary-explanation">可用时点：{{ timingLabel(candidateDetail.semantics.entry_timing.available_at) }}</span></dd></div><div><dt>股票之间怎么排名？</dt><dd>{{ candidateDetail?.semantics?.selection_rule?.type === 'TOP_N' ? `按冻结规则选择前 ${candidateDetail.semantics.selection_rule.top_n || 'N'} 名` : '当前规则未提供可读说明' }}</dd></div><div><dt>最终选几只？</dt><dd>{{ candidateDetail?.semantics?.selection_rule?.top_n || '当前规则未提供可读说明' }}</dd></div><div><dt>计划持有多久？</dt><dd>{{ candidateDetail?.semantics?.holding_period_trading_sessions ? `${candidateDetail.semantics.holding_period_trading_sessions} 个交易日` : '当前规则未提供可读说明' }}</dd></div><div><dt>什么时候进入 / 退出？</dt><dd>{{ timingLabel(candidateDetail?.semantics?.entry_timing?.eligible_at) }}<span class="secondary-explanation">{{ candidateDetail?.semantics?.entry_timing?.fill_time ? `执行时点：${timingLabel(candidateDetail.semantics.entry_timing.fill_time)}` : '退出规则未提供' }}</span></dd></div></dl></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">因子与信号</span><h2>因子与信号</h2></div></div><div v-if="detailFactors.length" class="factor-list"><FactorDisplay v-for="factor in detailFactors" :key="factor" :id="factor" :direction="candidateDetail?.semantics?.factor_directions?.[factor]" /></div><div v-else class="empty-state">当前数据尚未接入因子定义</div><div class="detail-callout">因子名称来自统一的中文展示层，原始因子编号收纳在技术信息中。</div><dl class="detail-list"><div><dt>执行限制</dt><dd>仅使用当时可获得的数据；因子可用时间不得晚于生成时间；不允许未来数据平移。</dd></div><div><dt>执行合同版本</dt><dd><code>{{ candidateDetail?.semantics?.execution_contract_version || '暂无数据' }}</code></dd></div></dl></article></section>
          <section v-if="structural?.status === 'PASS'" class="surface padded structural-pass-banner" data-testid="structural-pass-banner"><div class="section-heading"><div><span class="eyebrow">结构预检：通过</span><h2>当前候选已经满足进入预测验证所需的结构样本门槛。</h2><p>结构预检已通过，仅代表数据、样本和执行规则具备进入正式预测试验的资格，并不代表策略已经证明有效。</p></div><span class="status-chip tone-success">样本完整性：通过</span></div><div class="metric-grid"><div><span>已确认安全样本</span><b>{{ structural.lower_bound ?? '暂无数据' }}</b></div><div><span>最低要求</span><b>{{ structural.minimum_required ?? '暂无数据' }}</b></div><div><span>可能样本上限</span><b>{{ structural.upper_bound ?? '暂无数据' }}</b></div></div><div class="tag-row"><span>预测验证：尚未通过</span><span>预测试验：0 次</span><span>最终测试集：未访问</span></div><div class="page-actions"><button class="button-primary" type="button" @click="navigate(objectivePath('/research/governance', objectiveId))">查看研究治理决定</button></div></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow" :title="termHelp('结构预检')">结构预检</span><h2>结构预检</h2><p>不查看策略赚不赚钱，只确认历史样本和执行条件是否成立。</p></div><ResearchStatusDisplay :state="structural?.status" /></div><div class="metric-grid"><div><span>有效结构样本</span><b>{{ structural?.qualified_observations ?? '暂无数据' }}</b></div><div><span>候选机会</span><b>{{ structural?.selected_opportunities ?? '暂无数据' }}</b></div><div><span>可执行机会</span><b>{{ structural?.portfolio_feasible_count ?? '暂无数据' }}</b></div><div><span>已确认的最少有效样本</span><b>{{ structural?.lower_bound ?? '暂无数据' }}</b></div><div><span>可能达到的最多有效样本</span><b>{{ structural?.upper_bound ?? '暂无数据' }}</b></div><div><span>最低要求</span><b>{{ structural?.minimum_required ?? '暂无数据' }}</b></div></div><div class="help-grid"><p><strong>已确认的最少有效样本</strong>当前已经能够确定真实可执行的最少历史机会。</p><p><strong>可能达到的最多有效样本</strong>在尚未完全确认的机会全部可执行时，理论上可能达到的最大数量。</p><p><strong>结构预检</strong>只确认数据完整、时点一致、信号可合法产生、标的可交易、历史机会足够且执行规则成立。</p></div><div class="tag-row"><span>数据完整性 · {{ structural?.provider_completeness?.data_complete_count ?? '暂无数据' }}</span><span :title="termHelp('时点一致性（PIT）')">时点一致性（PIT） · {{ displayPIT(structural?.pit_status) }}</span><span>执行可行性 · {{ displayExecutionFeasibility(structural?.execution_feasibility) }}</span><span>绩效数据已载入 · {{ structural?.performance_data_loaded ? '是' : '否' }}</span></div><div v-if="structural?.reason_codes?.length" class="reason-list"><ReasonCodeDisplay v-for="code in structural.reason_codes" :key="code" :code="code" /></div></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究规则与安全边界</span><h2>研究规则与记录来源</h2></div></div><div class="two-column inner"><dl class="detail-list"><div><dt>候选策略技术编号</dt><dd><CandidateId :value="candidateDetail?.identity?.candidate_id" :hash="candidateDetail?.identity?.candidate_hash" /></dd></div><div><dt>规则版本</dt><dd><code>{{ candidateDetail?.identity?.contract_schema_version || '暂无数据' }}</code></dd></div><div><dt>研究区间</dt><dd>{{ candidateDetail?.governance?.research_period_identity?.start || '暂无数据' }} — {{ candidateDetail?.governance?.research_period_identity?.end || '暂无数据' }}</dd></div></dl><dl class="detail-list"><div><dt>所属研究批次</dt><dd><code>{{ candidateFor(candidateDetail?.candidate_id)?.created_batch_id || '暂无数据' }}</code></dd></div><div><dt>权威身份哈希</dt><dd><code>{{ candidateDetail?.identity?.canonical_identity_hash || '暂无数据' }}</code></dd></div><div><dt>研究记录来源</dt><dd><code>{{ candidateDetail?.artifact_lineage?.contract_ref || '暂无数据' }}</code></dd></div></dl></div><TechnicalDetails label="查看原始技术数据" :raw="candidateDetail?.identity" /></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">冻结合同纠错</span><h2>不可执行合同的追加式隔离</h2><p>{{ contractCorrectionPreview?.reason_zh || '正在核对合同执行能力与安全证据。' }}</p></div><span class="status-chip" :class="contractCorrectionPreview?.available ? 'tone-success' : 'tone-muted'">{{ contractCorrectionPreview?.available ? '可以追加隔离' : '当前不可隔离' }}</span></div><dl class="detail-list"><div><dt>原合同处理</dt><dd>保持原样，不编辑、不删除</dd></div><div><dt>Trial 与绩效证据</dt><dd>{{ contractCorrectionPreview?.safety_evidence ? `${contractCorrectionPreview.safety_evidence.trial_records} 条 Trial · ${contractCorrectionPreview.safety_evidence.performance_accessed ? '已访问绩效' : '未访问绩效'}` : '当前条件不允许进入隔离确认' }}</dd></div><div><dt>活动预算占用</dt><dd>{{ contractCorrectionPreview?.safety_evidence?.candidate_active_reservations ?? '未进入安全证据核验' }}</dd></div><div><dt>操作后果</dt><dd>只追加不可执行记录，并从后续可执行候选中排除</dd></div></dl><div class="page-actions"><button class="button-primary" type="button" :disabled="!contractCorrectionPreview?.available || contractCorrectionBusy" @click="contractCorrectionConfirmOpen = true">{{ contractCorrectionBusy ? '隔离中…' : '查看并确认隔离' }}</button></div><TechnicalDetails compact :entries="{ 原因代码: contractCorrectionPreview?.reason_code || '未提供', 合同记录: contractCorrectionPreview?.contract_ref || '未提供' }" /></section>
        </template>

        <template v-else-if="view === 'trials'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="count-badge">{{ filteredTrials.length }} / {{ trials.length }} 个试验</span></div>
          <section class="surface budget-panel-wide"><div class="section-heading"><div><span class="eyebrow" :title="termHelp('预测试验预算')">预测试验预算</span><h2>预测试验预算</h2><p>已使用、处理中、剩余可用和本轮总额分别显示。</p></div><FreshnessBadge v-if="budget" :state="budget.freshness_state" :source="budget.source_id" :generated-at="budget.source_generated_at" /></div><BudgetMeter :used="budget?.used" :reserved="budget?.reserved" :available="budget?.remaining" :total="budget?.total" :conflict="budget?.conflict" /></section>
          <section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="trialSearch" aria-label="搜索预测试验" placeholder="搜索试验编号、候选策略或机制" /></label><select v-model="trialFilter" aria-label="试验状态筛选"><option value="ALL">全部状态</option><option value="COMPLETED">已完成</option><option value="BLOCKED">预测验证未通过</option><option value="REJECTED">已淘汰</option><option value="PROMISING">有潜力</option></select><select v-model="trialSort" aria-label="试验排序"><option value="trial_id">按试验编号</option><option value="candidate_id">按候选策略</option><option value="status">按当前状态</option></select><button class="filter-button" type="button" @click="trialDirection = trialDirection === 'asc' ? 'desc' : 'asc'">{{ trialDirection === 'asc' ? '正序' : '倒序' }}</button></section>
          <section class="surface table-surface"><div class="table-head"><div><strong>预测试验清单</strong><span>支持搜索、筛选、排序与分页；绩效访问仅在授权详情中读取，不在列表端触发。</span></div></div><div class="table-scroll"><table class="research-table"><thead><tr><th>试验</th><th>候选策略</th><th>当前状态</th><th>预算状态</th><th>绩效访问</th><th>最终分类</th><th>开始时间</th></tr></thead><tbody><tr v-for="item in trialPageItems" :key="item.trial_id" tabindex="0" @click="navigate(`/research/trials/${encodeURIComponent(item.trial_id)}`)" @keydown.enter="navigate(`/research/trials/${encodeURIComponent(item.trial_id)}`)"><td><CandidateId :value="item.trial_id" /></td><td><CandidateDisplayName :candidate-id="item.candidate_id" :summary="candidatePresentation({ candidate_id: item.candidate_id }).summary" /><CandidateId :value="item.candidate_id" /></td><td><ResearchStatusDisplay :state="item.status" /></td><td><span class="status-chip" :class="item.budget_consumed ? 'tone-attention' : 'tone-muted'">{{ item.budget_consumed ? '已消耗' : item.reserved ? '已预留' : '未消耗' }}</span></td><td><span class="status-chip" :class="item.performance_accessed ? 'tone-attention' : 'tone-muted'">{{ item.performance_accessed ? '已访问' : '未访问' }}</span></td><td><ClassificationBadge :value="item.classification" /></td><td>{{ formatDate(item.source_generated_at) }}</td></tr></tbody></table></div><div v-if="!filteredTrials.length" class="empty-state">暂无匹配的预测试验数据</div><div v-else class="pagination-bar"><span>第 {{ trialPage }} / {{ trialPageCount }} 页</span><button class="button-secondary" type="button" :disabled="trialPage <= 1" @click="trialPage--">上一页</button><button class="button-secondary" type="button" :disabled="trialPage >= trialPageCount" @click="trialPage++">下一页</button></div></section>
        </template>

        <template v-else-if="view === 'trial-detail'">
          <div class="page-heading detail-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>预测试验详情</h1><p>候选策略：{{ trialCandidateDisplay.name }} · 本次验证结果与证据边界</p></div><button class="button-secondary" type="button" @click="navigate('/research/trials')">返回试验清单</button></div>
          <section class="surface detail-hero"><div><span class="eyebrow">候选策略</span><CandidateDisplayName :candidate-id="trialCandidateId" :summary="trialCandidateDisplay.summary" /><CandidateId :value="trialCandidateId" :hash="trialDetail?.summary?.candidate_hash" /></div><div class="detail-hero-meta"><span>试验状态</span><ResearchStatusDisplay :state="trialDetail?.summary?.status" /><span>最终分类</span><ClassificationBadge :value="trialDetail?.summary?.classification" /></div></section>
          <section class="detail-grid"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">试验基本信息</span><h2>试验基本信息</h2></div></div><dl class="detail-list"><div><dt>预测试验编号</dt><dd><CandidateId :value="trialDetail?.trial_id" full /></dd></div><div><dt>所属研究批次</dt><dd><TechnicalDetails compact :entries="{ 批次技术编号: trialDetail?.summary?.batch_id || '未提供' }" /></dd></div><div><dt>试验生命周期</dt><dd>{{ displayState(trialDetail?.summary?.lifecycle_state).label }}</dd></div><div><dt>研究规则对账</dt><dd>{{ trialDetail?.summary?.reconciled ? '已对账' : '尚未对账' }}</dd></div></dl></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">预算与绩效访问权限</span><h2>预算与绩效访问权限</h2></div></div><dl class="detail-list"><div><dt>预算状态</dt><dd>{{ trialDetail?.summary?.budget_consumed ? '已消耗' : trialDetail?.summary?.reserved ? '已预留' : '未消耗' }}</dd></div><div><dt>绩效访问权限</dt><dd>{{ trialDetail?.summary?.performance_accessed ? '已访问' : '未访问' }}</dd></div></dl><p class="plain-note">绩效访问权限表示该试验是否已被允许读取收益、回撤等绩效结果。页面不会创建、重开或删除预测试验，也不会修改预算。</p><TechnicalDetails compact :entries="{ 预算预约技术编号: trialDetail?.summary?.budget_reservation_identity || '未提供' }" /></article></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">验证证据</span><h2>验证证据</h2><p>中文结论优先；原始技术数据只在展开后查看。</p></div></div><div class="evidence-grid"><HumanPerformanceEvidence :value="trialDetail?.performance" :authorized="trialDetail?.human_authorized && trialDetail?.summary?.performance_accessed" /><HumanStatisticalEvidence :value="trialDetail?.statistical" /><HumanDecisionEvidence :decision="trialDetail?.effective_decision" :summary="trialDetail?.summary" /></div><div v-if="trialDetail?.summary?.reason_codes?.length" class="reason-list"><ReasonCodeDisplay v-for="code in trialDetail.summary.reason_codes" :key="code" :code="code" /></div><TechnicalDetails label="查看原始技术数据" :raw="{ performance: trialDetail?.performance, statistical: trialDetail?.statistical, decision: trialDetail?.effective_decision }" /></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">中断 Trial 对账</span><h2>绩效访问后的 canonical 对账</h2><p>{{ trialReconciliationPreview?.reason_zh || '正在核对 Trial、预算和 daemon 检查点。' }}</p></div><span class="status-chip" :class="trialReconciliationPreview?.available ? 'tone-success' : 'tone-muted'">{{ trialReconciliationPreview?.available ? '可以安全对账' : '当前无需或不可对账' }}</span></div><dl class="detail-list"><div><dt>适用范围</dt><dd>已访问绩效，但未完成证据、最终裁决和策略登记的中断 Trial</dd></div><div><dt>处理方式</dt><dd>追加工程中断终态，并把既有预算预留结算为已消耗</dd></div><div><dt>严格禁止</dt><dd>不重跑绩效、不创建新 Trial、不补造统计结果、不回退预算</dd></div><div><dt>预算预留状态</dt><dd>{{ trialReconciliationPreview?.budget_reservation_status || '未进入安全对账证据' }}</dd></div></dl><div class="page-actions"><button class="button-primary" type="button" :disabled="!trialReconciliationPreview?.available || trialReconciliationBusy" @click="trialReconciliationConfirmOpen = true">{{ trialReconciliationBusy ? '对账中…' : '查看并确认 Trial 对账' }}</button></div><TechnicalDetails compact :entries="{ 原因代码: trialReconciliationPreview?.reason_code || '未提供', Trial状态: trialReconciliationPreview?.trial_status || trialDetail?.summary?.status || '未提供' }" /></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究安全边界</span><h2>研究安全边界</h2></div></div><div class="safety-grid"><span :title="termHelp('最终测试集')">最终测试集<strong>未访问</strong><small>预先封存的数据区间</small></span><span :title="termHelp('前瞻验证')">前瞻验证<strong>未启动</strong><small>研究完成后的新数据观察</small></span><span>真实订单<strong>已禁用</strong><small>不会自动发送到券商</small></span></div><TechnicalDetails label="查看其他技术信息" :raw="trialDetail?.artifact_lineage" /></section>
        </template>

        <template v-else-if="view === 'daemon'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="daemon" :state="daemon.freshness_state" :source="daemon.source_id" :generated-at="daemon.source_generated_at" /></div>
          <section class="hero-grid"><article class="surface hero-status"><div class="section-heading"><div><span class="eyebrow">运行状态</span><h2>运行状态</h2></div><ResearchStatusDisplay :state="daemon?.daemon_state" /></div><div class="hero-state"><strong>{{ daemon?.state_display_zh || statusDescription(daemon?.daemon_state) }}</strong><span>{{ daemon?.last_error ? '最近一次运行记录了错误，需要根据当前研究处理说明复核。' : '页面只读监控，不提供启动、暂停、恢复或停止按钮。' }}</span></div><dl class="daemon-kv"><div><dt>当前阶段</dt><dd>{{ daemon ? stageLabel(daemon.stage) : '暂无数据' }}</dd></div><div><dt>当前候选策略</dt><dd><CandidateDisplayName :candidate-id="daemon?.current_candidate_id" /><CandidateId :value="daemon?.current_candidate_id" /></dd></div><div><dt>进程编号 / 运行记录编号</dt><dd><TechnicalDetails compact :entries="{ 进程编号: daemon?.process_pid ?? '未提供', 运行记录编号: daemon?.daemon_run_id || '未提供' }" /></dd></div></dl></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">资源概览</span><h2>资源状态</h2></div><span class="status-chip" :class="statusTone(daemonHealth?.health_state)">{{ daemonHealth?.health_display_zh || '状态无法确认' }}</span></div><div class="resource-grid"><div><span>运行时内存</span><strong>{{ formatBytes(daemonHealth?.process_rss_bytes ?? daemon?.process_rss_bytes) }}</strong></div><div><span>系统剩余内存</span><strong>{{ formatBytes(daemonHealth?.system_available_memory_bytes ?? daemon?.system_available_memory_bytes) }}</strong></div><div><span>最近保存时间</span><strong>{{ formatDate(daemon?.last_checkpoint_time) }}</strong></div><div><span>保存记录年龄</span><strong>{{ daemonHealth?.checkpoint_age_seconds != null ? `${daemonHealth.checkpoint_age_seconds} 秒` : '暂无数据' }}</strong></div></div><p class="plain-note">不虚构处理器或显卡指标；只有接口提供的内存数据会显示。</p></article></section>
          <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">编排层状态</span><h2>{{ canonicalStateZh }}</h2><p>这是研究进度的权威状态；上方研究守护进程只反映本地执行层。</p></div><span class="status-chip" :class="statusTone(canonicalState)">{{ canonicalStateZh }}</span></div><dl class="detail-list"><div><dt>下一步</dt><dd>{{ orchestrator?.next_action_zh || pipeline?.next_action_zh || '暂无数据' }}</dd></div><div><dt>最近错误</dt><dd>{{ pipeline?.last_error || '暂无已登记错误' }}</dd></div><div><dt>状态来源</dt><dd>{{ humanSourceLabel(pipeline?.state_source || '自主研究编排器') }}</dd></div></dl></section><HumanActionPanel :action="daemon?.required_human_ai_action" :reason-code="daemon?.required_human_ai_action ? handoff?.reason_code : null" :allowed="handoff?.allowed_next_actions" :forbidden="handoff?.forbidden_actions" /><section class="surface event-surface"><div class="section-heading"><div><span class="eyebrow">研究事件时间线</span><h2>研究事件时间线</h2></div><span class="helper-copy">当前研究状态优先；较早的处理说明仅作为上下文。</span></div><div v-if="pipeline?.recent_events.length" class="event-list"><div v-for="event in [...pipeline.recent_events].reverse()" :key="String(event.event_id || event.timestamp || Math.random())" class="event-item"><span class="event-marker"></span><div><strong>{{ eventLabel(event) }}</strong><p>{{ eventDescription(event) }}</p><TechnicalDetails compact :entries="{ 事件编号: event.event_id || '未提供', 内部状态: event.new_state || event.event_type || '未提供' }" /></div><time>{{ formatDate(event.timestamp || event.created_at) }}</time></div></div><div v-else class="empty-state">暂无研究守护进程事件</div></section>
        </template>

        <template v-else-if="view === 'shadow'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div></div><section class="shadow-banner"><div><span class="shadow-symbol">◎</span><div><strong>每日研究观察</strong><p>{{ shadow?.warning_zh || '这是研究观察结果，不是买入推荐，也不会产生真实订单。' }}</p></div></div><span>真实订单 · 已禁用</span></section><section v-if="shadow && !shadow.available" class="surface padded"><div class="section-heading"><div><span class="eyebrow">当前目标数据边界</span><h2>当前研究目标没有可展示的每日研究观察数据</h2></div><span class="status-chip tone-attention">未绑定</span></div><p class="plain-note">{{ shadow.availability_reason_zh }}</p><TechnicalDetails label="查看来源技术信息" :raw="shadow" /></section><template v-else><section class="summary-grid"><article class="surface"><span>交易日</span><strong>{{ formatDate(shadow?.trade_date) }}</strong></article><article class="surface"><span>扫描状态</span><strong>{{ displayState(shadow?.scan_status).label }}</strong></article><article class="surface"><span>策略数 / 原始信号 / 观察候选</span><strong>{{ shadow?.strategies_scanned ?? '暂无数据' }} / {{ shadow?.raw_signal_count ?? '暂无数据' }} / {{ shadow?.unique_candidate_count ?? '暂无数据' }}</strong></article><article class="surface"><span>可交易状态</span><strong>{{ displayState(shadow?.tradability_state).label }}</strong></article></section><section class="surface table-surface"><div class="table-head"><div><strong>观察候选</strong><span>每日研究观察结果不会回写候选策略分类。</span></div><span class="status-chip tone-attention">研究观察</span></div><div class="table-scroll"><table class="research-table"><thead><tr><th>排名</th><th>股票代码</th><th>股票名称</th><th>来源策略</th><th title="只允许使用当时已经能够获得的数据，避免未来数据泄漏到过去。">时点一致性（PIT）</th><th>可交易状态</th><th>下一合法交易日</th></tr></thead><tbody><tr v-for="(item, index) in shadow?.observation_pool || []" :key="String(item.symbol || index)"><td>{{ item.rank || index + 1 }}</td><td><code>{{ item.symbol || '暂无数据' }}</code></td><td>{{ item.name || '暂无数据' }}</td><td><CandidateDisplayName :candidate-id="String(item.matched_strategy || item.strategy_id || '')" /><CandidateId :value="String(item.matched_strategy || item.strategy_id || '')" /></td><td><span class="status-chip" :class="statusTone(item.PIT_status)">{{ displayPIT(item.PIT_status || shadow?.pit_state) }}</span></td><td><span class="status-chip" :class="statusTone(item.ST_status)">{{ displayState(item.ST_status || shadow?.tradability_state).label }}</span></td><td>{{ formatDate(item.earliest_legal_execution_date || shadow?.next_legal_session) }}</td></tr></tbody></table></div><div v-if="!shadow?.observation_pool.length" class="empty-state">暂无可展示的每日观察候选 · 当前证据不足</div></section></template>
        </template>

        <template v-else-if="view === 'data-health'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="status-chip" :class="statusTone(dataHealth?.overall_status)">{{ displayState(dataHealth?.overall_status).label }}</span></div><section class="source-grid"><article v-for="source in dataHealth?.sources || []" :key="String(source.source_key || source.source_id)" class="surface source-card"><div class="source-top"><span class="source-logo">{{ String(source.source_id || '?').slice(0, 1).toUpperCase() }}</span><div><strong>{{ sourceLabel(source.source_id) }}</strong><small>{{ sourceRole(source) }} · {{ source.scope === 'OBJECTIVE' ? '当前目标' : source.scope === 'UNSCOPED' ? '未绑定目标' : '平台能力' }}</small></div><span class="status-chip" :class="statusTone(source.status)">{{ displayState(source.status).label }}</span></div><dl class="detail-list"><div><dt>覆盖范围</dt><dd>{{ formatCoverage(source.coverage) }}</dd></div><div><dt>最后更新时间</dt><dd>{{ formatDate(source.last_update) }}</dd></div><div><dt>缺失情况</dt><dd>{{ source.missing ?? '暂无已登记缺失' }}</dd></div><div><dt>未知情况</dt><dd>{{ source.unknown === true ? '存在未知项' : '暂无已登记未知项' }}</dd></div><div><dt>是否为主要数据源</dt><dd>{{ source.canonical ? '是' : '否' }}</dd></div></dl><TechnicalDetails compact :entries="{ 数据源技术编号: source.source_id || '未提供', 数据源实例编号: source.source_key || '未提供', 数据范围: source.scope || '未提供', 原始角色: source.role || '未提供', 原始状态: source.status || '未提供', 原始覆盖字段: source.coverage || '未提供' }" /></article></section><section class="surface padded"><div class="section-heading"><div><span class="eyebrow">判读说明</span><h2>数据健康判读</h2></div></div><div class="help-grid"><p><strong>数据最新</strong>来源当前可用，但仍应结合研究区间和时点一致性阅读。</p><p><strong>部分数据可用</strong>说明存在覆盖或字段限制，不自动等同于失败。</p><p><strong>暂无数据</strong>接口没有提供该字段，页面不会用推测值填充。</p></div></section>
        </template>

        <template v-else-if="view === 'evolution'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><FreshnessBadge v-if="evolution?.available" :state="evolution.freshness_state" source="研究演进分析报告" :generated-at="evolution.source_generated_at" /><span v-else class="status-chip tone-muted">等待明确输入</span></div>
          <template v-if="evolution?.available && evolution.report">
            <section class="evolution-hero surface"><div><span class="eyebrow">本轮结果边界</span><h2>BLOCKED · 已完成失败分析</h2><p>系统已经从指定 Trial 提取失败原因，形成下一轮研究设计可参考的上下文；流程在这里停止。</p></div><div class="evolution-lifecycle"><span class="done">BLOCKED</span><i>→</i><span class="done">失败分析</span><i>→</i><span class="current">AI 上下文</span><small>人工审核后才可进入新的研究设计</small></div></section>
            <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究对象</span><h2>本次分析绑定的目标与机制</h2><p>只覆盖当前明确绑定的 Candidate 与 Trial，不推断整个研究空间。</p></div><span class="status-chip tone-danger">{{ evolution.report.research_result?.terminal_classification || evolution.report.research_result?.terminal_status || 'BLOCKED' }}</span></div><dl class="detail-list evolution-object-details"><div><dt>研究目标</dt><dd><code>{{ evolution.report.lineage?.objective_id || objectiveId }}</code></dd></div><div><dt>Candidate</dt><dd><code>{{ evolution.report.lineage?.candidate_id || '未提供' }}</code></dd></div><div><dt>Trial</dt><dd><code>{{ evolution.report.lineage?.trial_id || '未提供' }}</code></dd></div><div><dt>机制家族</dt><dd>{{ evolution.report.candidate?.candidate_family || '未提供' }}</dd></div><div><dt>已测试机制</dt><dd>{{ evolution.report.candidate?.mechanism || '未提供' }}</dd></div></dl></section>
            <section class="surface padded"><div class="section-heading"><div><span class="eyebrow">失败分布</span><h2>失败原因分类</h2><p>分类用于约束下一轮研究设计，不改变原有结果。</p></div><span class="status-chip tone-attention">{{ evolution.report.failure_analysis?.primary_categories?.length || 0 }} 类</span></div><div class="evolution-category-grid"><article v-for="category in ['STATISTICAL_FAILURE', 'RETURN_FAILURE', 'RISK_FAILURE', 'SAMPLE_FAILURE', 'ENGINEERING_FAILURE', 'OVERFITTING_RISK']" :key="category" class="evolution-category-card" :class="{ active: Number(evolution.report.failure_analysis?.category_counts?.[category] || 0) > 0 }"><span class="evolution-category-code">{{ category }}</span><strong>{{ evolutionCategoryLabel(category) }}</strong><b>{{ evolution.report.failure_analysis?.category_counts?.[category] || 0 }}</b></article></div><div class="evolution-findings"><article v-for="finding in evolution.report.failure_analysis?.findings || []" :key="`${finding.category}:${finding.reason_code}`" class="evolution-finding"><div><span class="status-chip tone-danger">{{ evolutionCategoryLabel(finding.category) }}</span><strong>{{ finding.reason_code }}</strong></div><p>{{ finding.explanation_zh }}</p><small>证据对象：{{ finding.evidence_id }} · 下一轮约束：{{ finding.next_research_constraint_zh }}</small></article></div></section>
            <section class="evolution-two-column"><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">研究空间覆盖</span><h2>已经探索到哪里</h2></div><span class="status-chip tone-muted">局部覆盖</span></div><div class="coverage-pills"><span v-for="family in evolution.report.research_space_coverage?.tested_mechanism_families || []" :key="String(family)">{{ family }}</span></div><p class="plain-note">{{ evolution.report.research_space_coverage?.coverage_statement_zh || '仅覆盖当前绑定对象。' }}</p><div class="evolution-tested-mechanisms"><strong>已测试机制</strong><span v-for="mechanism in evolution.report.research_space_coverage?.tested_mechanisms || []" :key="String(mechanism)">{{ mechanism }}</span></div></article><article class="surface padded"><div class="section-heading"><div><span class="eyebrow">下一轮建议</span><h2>保持方向独立</h2></div><span class="status-chip tone-governance">需要人工审核</span></div><div class="evolution-direction-list"><div v-for="direction in evolution.report.next_research_directions || []" :key="String(direction.direction_id)"><strong>{{ direction.title_zh }}</strong><p>{{ direction.description_zh }}</p><small>{{ direction.reason_zh }}</small></div></div></article></section>
            <section class="surface padded evolution-safety"><div class="section-heading"><div><span class="eyebrow">AI 研究上下文</span><h2>只提供研究设计线索，不提供结果字段</h2><p>上下文可被 AI 读取为机制覆盖与失败约束，但不会携带收益、胜率、回撤或单笔交易结果。</p></div><span class="status-chip tone-success">结果盲化</span></div><div class="evolution-context-grid"><div><strong>已测试机制</strong><span v-for="item in evolution.context?.tested_mechanisms || []" :key="String(item.mechanism)">{{ item.candidate_family }} · {{ item.mechanism }}</span></div><div><strong>未探索方向</strong><span v-for="item in evolution.context?.unexplored_directions || []" :key="String(item.direction_id)">{{ item.direction_id }} · {{ item.description_zh }}</span></div><div><strong>强制边界</strong><span>自动生成 Candidate：禁止</span><span>自动启动 Trial：禁止</span><span>调用 Codex：禁止</span><span>消耗预算：禁止</span></div></div><TechnicalDetails label="查看上下文技术合同" :raw="evolution.context" /></section>
            <section class="surface padded evolution-governance"><div class="section-heading"><div><span class="eyebrow">治理安全检查</span><h2>输出已落在只读边界</h2></div><span class="status-chip tone-success">不可变输入</span></div><div class="safety-grid"><div><strong>Candidate</strong><small>历史冻结合同未修改</small></div><div><strong>Trial / 验证</strong><small>历史结果与状态未修改</small></div><div><strong>预算</strong><small>未新增、未回退、未消耗</small></div><div><strong>下一步</strong><small>人工审核研究上下文</small></div></div><TechnicalDetails compact label="查看 lineage 与哈希" :entries="{ 报告编号: evolution.report.report_id, 报告哈希: evolution.report.report_hash, 输入哈希: evolution.report.source_input_hash, 上下文边界: evolution.context?.next_boundary || 'HUMAN_REVIEW_REQUIRED' }" /></section>
          </template>
          <section v-else class="surface padded evolution-empty"><div class="section-heading"><div><span class="eyebrow">当前目标数据边界</span><h2>{{ evolution?.display?.title_zh || '尚未生成研究演进分析' }}</h2><p>{{ evolution?.display?.message_zh || '请在明确绑定的研究结果上显式生成分析。' }}</p></div><span class="status-chip tone-muted">只读</span></div><p class="plain-note">页面不会自动寻找 Trial，不会自动生成报告，也不会改变研究预算或治理状态。</p></section>
        </template>

        <template v-else-if="view === 'evolution-proposals'">
          <ResearchEvolutionProposals :model="evolutionProposals" :objective-id="objectiveId" @navigate="navigate" />
        </template>

        <template v-else-if="view === 'evolution-ai-design'">
          <ResearchEvolutionAIDesign :model="evolutionAIDesign" :objective-id="objectiveId" @navigate="navigate" />
        </template>

        <template v-else-if="view === 'candidate-proposals'">
          <CandidateProposalGovernance :model="candidateProposals" :objective-id="objectiveId" @navigate="navigate" />
        </template>

        <template v-else-if="view === 'reports'">
          <div class="page-heading"><div><span class="eyebrow">{{ pageMeta.eyebrow }}</span><h1>{{ pageMeta.title }}</h1><p>{{ pageMeta.description }}</p></div><span class="status-chip tone-muted">当前目标：{{ objectiveDisplay.name }}</span></div><section class="surface report-surface"><div class="report-tabs"><button v-for="category in ['全部', '研究报告', '研究收官', 'AI研究员', '研究守护进程', '候选策略', '预测试验', '每日研究观察', '数据', '研究规则', '架构']" :key="category" type="button" :class="{ active: reportFilter === category }" @click="reportFilter = category">{{ category }}</button></div><section class="filter-bar"><label class="search-box"><span>⌕</span><input v-model="reportSearch" aria-label="搜索研究报告" placeholder="搜索报告标题、说明或编号" /></label><select v-model="reportSort" aria-label="报告排序"><option value="date">按更新时间</option><option value="title">按报告标题</option></select><button class="filter-button" type="button" @click="reportDirection = reportDirection === 'asc' ? 'desc' : 'asc'">{{ reportDirection === 'asc' ? '正序' : '倒序' }}</button></section><div class="report-list"><button v-for="item in reportPageItems" :key="reportId(item)" class="report-row" type="button" @click="openReport(item)"><span class="report-category">{{ reportCategory(item) }} · {{ item.scope === 'OBJECTIVE' ? '当前目标' : '平台资料' }}</span><span><strong>{{ reportTitle(item) }}</strong><small class="report-human-summary">{{ reportSummary(item) }}</small></span><span class="report-date">{{ formatDate(item.generated_at || item.source_generated_at || item.updated_at) }}</span><span>→</span></button></div><div v-if="!sortedReports.length" class="empty-state">暂无与当前研究目标匹配的报告</div><div v-else class="pagination-bar"><span>第 {{ reportPage }} / {{ reportPageCount }} 页，共 {{ sortedReports.length }} 条</span><button class="button-secondary" type="button" :disabled="reportPage <= 1" @click="reportPage--">上一页</button><button class="button-secondary" type="button" :disabled="reportPage >= reportPageCount" @click="reportPage++">下一页</button></div></section><section v-if="openedReport" class="surface report-preview"><div class="section-heading"><div><span class="eyebrow">报告预览 · {{ openedReport.scope === 'OBJECTIVE' ? '当前目标' : '平台资料' }}</span><h2>{{ reportTitle(openedReport) }}</h2><p>{{ reportSummary(openedReport) }}</p></div><button class="button-secondary" type="button" @click="openedReport = null">关闭</button></div><TechnicalDetails label="查看原始报告数据" :raw="openedReport" /></section>
        </template>
        <div v-if="toasts.length" class="toast-stack" aria-live="polite"><div v-for="toast in toasts" :key="toast.id" class="toast-card"><span class="toast-mark">✓</span><div><strong>{{ toast.title }}</strong><p>{{ toast.body }}</p></div></div></div>
        <div v-if="pendingOperation" class="modal-backdrop" role="presentation" @click.self="closeOperationModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="operation-confirm-title"><span class="eyebrow">需要明确确认</span><h2 id="operation-confirm-title">{{ actionLabel(pendingOperation) }}</h2><p>将向当前研究目标提交自主研究编排器操作：{{ operations?.state_zh || '当前状态' }}。</p><dl class="detail-list"><div><dt>会发生什么</dt><dd>{{ operationByName(pendingOperation)?.description_zh || '提交一次受保护的研究运行操作。' }}</dd></div><div><dt>不会发生什么</dt><dd>不会新建研究目标、预测试验、预算或最终测试；不会拼接执行命令。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="operationBusy" @click="closeOperationModal">取消</button><button class="button-primary" type="button" :disabled="operationBusy" @click="confirmOperation">{{ operationBusy ? '提交中…' : '确认提交' }}</button></div></section></div>
        <div v-if="structuralReconciliationConfirmOpen && pipeline?.structural_reconciliation?.available" class="modal-backdrop" role="presentation" @click.self="closeStructuralReconciliationModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="structural-confirm-title"><span class="eyebrow">需要明确确认</span><h2 id="structural-confirm-title">重新进行结构预检</h2><p>确认后会重新读取当前冻结候选的结构数据并对账结果，运行时间可能较长。</p><dl class="detail-list"><div><dt>候选策略</dt><dd>{{ nameFor(pipeline.structural_reconciliation.candidate_id) }}<CandidateId :value="pipeline.structural_reconciliation.candidate_id" /></dd></div><div><dt>执行前复核</dt><dd>后端会重新核对 READY 状态、候选身份、零 Trial、零预算占用和运行锁。</dd></div><div><dt>通过后</dt><dd>页面会切换到“等待预测验证授权”，预测仍需另行确认。</dd></div><div><dt>不会执行</dt><dd>不会访问预测绩效、创建 Trial、消耗预算或启动真实交易。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="structuralReconciliationBusy" @click="closeStructuralReconciliationModal">取消</button><button class="button-primary" type="button" :disabled="structuralReconciliationBusy" @click="confirmStructuralReconciliation">{{ structuralReconciliationBusy ? '正在重新检查…' : '确认重新检查结构' }}</button></div></section></div>
        <div v-if="predictiveAuthorizationConfirmOpen && predictiveGovernancePreview" class="modal-backdrop" role="presentation" @click.self="closePredictiveAuthorizationModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="predictive-confirm-title"><span class="eyebrow">需要明确确认</span><h2 id="predictive-confirm-title">{{ predictiveGovernancePreview.choice.label_zh }}</h2><p>这是结构 PASS 后的治理写入。确认后不会立即运行预测试验，也不会消耗预算。</p><dl class="detail-list"><div><dt>候选策略</dt><dd>{{ nameFor(predictiveGovernancePreview.candidate_id) }}<CandidateId :value="predictiveGovernancePreview.candidate_id" :hash="predictiveGovernancePreview.candidate_hash" /></dd></div><div><dt>结构证据</dt><dd>PASS · {{ predictiveGovernancePreview.structural.lower_bound }} / {{ predictiveGovernancePreview.structural.upper_bound }}，最低要求 {{ predictiveGovernancePreview.structural.minimum_required }}</dd></div><div><dt>本次写入</dt><dd>{{ predictiveGovernancePreview.choice.consequence_zh }}</dd></div><div><dt>预算与 Trial</dt><dd>当前 {{ predictiveGovernancePreview.preview.budget_snapshot.used || 0 }} / {{ predictiveGovernancePreview.preview.budget_snapshot.total || 0 }}；Trial 保持 {{ predictiveGovernancePreview.preview.trial_count_before || 0 }}，不预留预算。</dd></div><div><dt>不会执行</dt><dd>不会调用预测执行器、访问绩效、打开 Final Test、启用 Prospective 或 Real Order。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="predictiveAuthorizationBusy" @click="closePredictiveAuthorizationModal">取消</button><button class="button-primary" type="button" :disabled="predictiveAuthorizationBusy" @click="confirmPredictiveAuthorization">{{ predictiveAuthorizationBusy ? '正在记录…' : predictiveGovernancePreview.choice.choice === 'AUTHORIZE_FIRST_PREDICTIVE_TRIAL' ? '确认授权进入第 1 次预测试验' : predictiveGovernancePreview.choice.choice === 'DEFER_PREDICTIVE_TRIAL' ? '确认暂缓预测试验' : '确认结束当前方向' }}</button></div></section></div>
        <div v-if="predictiveTrialStartConfirmOpen && predictiveTrialStartPreview" class="modal-backdrop" role="presentation" @click.self="closePredictiveTrialStartModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="predictive-trial-start-confirm-title" data-testid="predictive-trial-start-confirm"><span class="eyebrow">需要明确确认</span><h2 id="predictive-trial-start-confirm-title">确认启动预测试验</h2><p>启动后系统将首次访问当前候选的预测验证结果，并预留 1 次预测试验预算。</p><dl class="detail-list"><div><dt>即将启动</dt><dd>第 1 次预测试验</dd></div><div><dt>Candidate</dt><dd>{{ nameFor(predictiveTrialStartPreview.candidate_id) }}<CandidateId :value="predictiveTrialStartPreview.candidate_id" :hash="predictiveTrialStartPreview.candidate_hash" /></dd></div><div><dt>结构预检</dt><dd>{{ predictiveTrialStartPreview.structural.lower_bound }} / {{ predictiveTrialStartPreview.structural.upper_bound }} / {{ predictiveTrialStartPreview.structural.status }}</dd></div><div><dt>最低要求</dt><dd>{{ predictiveTrialStartPreview.structural.minimum_required }}</dd></div><div><dt>当前预测预算</dt><dd>{{ predictiveTrialStartPreview.budget.used || 0 }} / {{ predictiveTrialStartPreview.budget.total ?? '暂无数据' }}</dd></div><div><dt>启动后</dt><dd>预留 1 次预测预算</dd></div><div><dt>本次会</dt><dd>访问预测验证结果，并登记 Trial、预算和 Multiple Testing 归属</dd></div><div><dt>本次不会</dt><dd>修改 Candidate、访问 Final Test、启动 Prospective、真实下单或调用 AI 修改策略</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="predictiveTrialStartBusy" @click="closePredictiveTrialStartModal">取消</button><button class="button-primary" type="button" :disabled="predictiveTrialStartBusy" @click="confirmPredictiveTrialStart">{{ predictiveTrialStartBusy ? '正在启动…' : '确认启动预测试验' }}</button></div></section></div>
        <div v-if="contractCorrectionConfirmOpen && contractCorrectionPreview?.available" class="modal-backdrop" role="presentation" @click.self="closeContractCorrectionModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="contract-correction-confirm-title"><span class="eyebrow">需要明确确认</span><h2 id="contract-correction-confirm-title">追加冻结合同执行隔离</h2><p>仅当合同无法由当前 Provider 重建，且没有 Trial、绩效访问或活动预算占用时，后端才会接受确认。</p><dl class="detail-list"><div><dt>候选策略</dt><dd>{{ detailDisplay.name }}<CandidateId :value="contractCorrectionPreview.candidate_id" :hash="contractCorrectionPreview.candidate_hash" /></dd></div><div><dt>原冻结合同</dt><dd>不会编辑或删除，继续保留完整审计记录。</dd></div><div><dt>追加记录</dt><dd>写入一条带哈希链的 INVALIDATE_EXECUTION 纠错事件。</dd></div><div><dt>不会执行</dt><dd>不会创建替代候选、重新设计 ONE_SHOT 任务、访问绩效或消耗预算。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="contractCorrectionBusy" @click="closeContractCorrectionModal">取消</button><button class="button-primary" type="button" :disabled="contractCorrectionBusy" @click="confirmContractCorrection">{{ contractCorrectionBusy ? '正在追加隔离…' : '确认追加执行隔离' }}</button></div></section></div>
        <div v-if="trialReconciliationConfirmOpen && trialReconciliationPreview?.available" class="modal-backdrop" role="presentation" @click.self="closeTrialReconciliationModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="trial-reconciliation-confirm-title"><span class="eyebrow">需要明确确认</span><h2 id="trial-reconciliation-confirm-title">终结绩效访问中断 Trial</h2><p>确认后会把这个无法继续的 Trial 追加为工程失效终态，并完成既有预算对账。</p><dl class="detail-list"><div><dt>预测试验</dt><dd><CandidateId :value="trialReconciliationPreview.trial_id" full /></dd></div><div><dt>确认依据</dt><dd>已访问绩效、未形成完整证据、未完成最终裁决、当前 daemon 正在等待该 Trial 对账。</dd></div><div><dt>预算处理</dt><dd>已发生的绩效访问仍计入预算；活动预留会结算为已消耗，不会回退。</dd></div><div><dt>不会执行</dt><dd>不会重跑绩效、创建替代 Trial、补造统计结果、访问最终测试或启动真实交易。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="trialReconciliationBusy" @click="closeTrialReconciliationModal">取消</button><button class="button-primary" type="button" :disabled="trialReconciliationBusy" @click="confirmTrialReconciliation">{{ trialReconciliationBusy ? '正在对账…' : '确认终结并完成对账' }}</button></div></section></div>
        <div v-if="governanceConfirmOpen && governancePreview" class="modal-backdrop" role="presentation" @click.self="closeGovernanceModal"><section class="confirm-modal governance-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="governance-confirm-title"><span class="eyebrow">第三步 · 明确确认</span><h2 id="governance-confirm-title">确认{{ governancePreview.governance_action === 'STOP_RESEARCH' ? '结束研究' : '创建并启动下一轮研究' }}</h2><p>即将写入一个新的治理执行事务。请确认下面的研究边界后再提交。</p><dl class="detail-list"><div><dt>研究方向</dt><dd>{{ pendingGovernance?.label_zh }}</dd></div><div><dt>新研究目标</dt><dd><code>{{ governancePreview.proposed_new_objective_id || '不会创建' }}</code></dd></div><div><dt>新预算</dt><dd>{{ governancePreview.budget_proposal.proposed_total_predictive_budget || 0 }} 次，独立于当前目标 {{ canonicalBudget.used ?? '暂无数据' }}/{{ canonicalBudget.total ?? '暂无数据' }}</dd></div><div><dt>最终测试集</dt><dd>仍未开放 · 前瞻验证关闭 · 真实订单禁用</dd></div></dl><section class="parent-candidate-confirm"><strong>父候选身份（{{ governancePreview.parent_candidate_identity_refs.length }} 个）</strong><div v-if="governancePreview.parent_candidate_identity_refs.length" class="parent-candidate-confirm-list"><div v-for="parent in governancePreview.parent_candidate_identity_refs" :key="`${parent.candidate_id}:${parent.candidate_hash}`"><CandidateDisplayName :candidate-id="parent.candidate_id" /><code>{{ parent.candidate_id }}</code><code>{{ parent.candidate_hash }}</code><MechanismDisplay :family="parent.family_id" :mechanism="parent.mechanism" /></div></div><span v-else>无</span><p>身份已进入方案哈希，确认时不可替换。</p></section><p class="governance-confirm-warning">确认后会创建登记并启动受控研究编排器；不会人工塞入候选策略，AI 只会在研究编排器到达 AI 研究设计边界后按当前模式处理。</p><div class="modal-actions"><button class="button-secondary" type="button" :disabled="governanceBusy" @click="closeGovernanceModal">返回方案</button><button class="button-primary" type="button" :disabled="governanceBusy" @click="confirmGovernance">{{ governanceBusy ? '执行中…' : '确认并执行治理决定' }}</button></div></section></div>
        <div v-if="predictiveTrialResumeConfirmOpen && predictiveTrialResumePreview" class="modal-backdrop" role="presentation" @click.self="closePredictiveTrialResumeModal"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="predictive-trial-resume-confirm-title" data-testid="predictive-trial-resume-confirm"><span class="eyebrow">需要人工二次确认</span><h2 id="predictive-trial-resume-confirm-title">确认恢复第 1 次预测试验</h2><p>本次只恢复已经因工程事件物化问题中断的 Trial #1；策略表现没有被判定为失败。</p><dl class="detail-list"><div><dt>Trial ID</dt><dd><CandidateId :value="predictiveTrialResumePreview.trial_id" full /></dd></div><div><dt>Candidate</dt><dd>{{ nameFor(predictiveTrialResumePreview.candidate_id) }}<CandidateId :value="predictiveTrialResumePreview.candidate_id" :hash="predictiveTrialResumePreview.candidate_hash" /></dd></div><div><dt>Trial Contract</dt><dd>{{ predictiveTrialResumePreview.preview.trial_contract_hash || '未提供' }}</dd></div><div><dt>失败阶段与对象</dt><dd>{{ predictiveTrialResumePreview.preview.failure_stage }} · {{ predictiveTrialResumePreview.preview.failure_object }}</dd></div><div><dt>事件物化</dt><dd>{{ predictiveTrialResumePreview.preview.event_materialization?.status || '未提供' }} · {{ predictiveTrialResumePreview.preview.event_materialization?.event_day_count || 0 }} 个事件日</dd></div><div><dt>当前预算</dt><dd>已使用 {{ predictiveTrialResumePreview.budget.used || 0 }} · 已预留 {{ predictiveTrialResumePreview.budget.reserved || 0 }} · 剩余 {{ predictiveTrialResumePreview.budget.remaining || 0 }}</dd></div><div><dt>本次不会</dt><dd>不会创建 Trial #2、再次扣预算、重复 Multiple Testing 登记、修改 Candidate 或 Trial Contract，也不会访问 Final Test。</dd></div><div><dt>后续边界</dt><dd>Prospective 关闭 · Real Order 禁用；浏览器关闭后仍由后台 daemon/service 继续执行。</dd></div></dl><div class="modal-actions"><button class="button-secondary" type="button" :disabled="predictiveTrialResumeBusy" @click="closePredictiveTrialResumeModal">取消</button><button class="button-primary" type="button" :disabled="predictiveTrialResumeBusy" @click="confirmPredictiveTrialResume">{{ predictiveTrialResumeBusy ? '正在提交恢复…' : '确认恢复同一个 Trial #1' }}</button></div></section></div>
      </main>
    </div>
  </div>
</template>
