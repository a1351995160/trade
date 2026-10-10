export type LifecycleRecord = Record<string, unknown>
export interface LifecycleView {
  bindings: Record<string, LifecycleRecord>
  binding_catalog: Record<string, { kind: string }>
  jobs: Record<string, LifecycleRecord>
  continuous?: Record<string, LifecycleRecord>
  actions_allowed?: boolean
  operation_permissions?: { create_binding_ids: string[]; job_ids: string[]; continuous_ids?: string[] }
  background_enabled: boolean
}

const labels: Record<string, string> = {
  CREATED: '尚未启动', READY: '可推进下一阶段', RUNNING: '正在处理', PAUSED: '已暂停',
  WAITING_WINDOW: '等待计划时段', WAITING_DATA: '等待数据', WAITING_QUALIFICATION: '等待策略资格',
  WAITING_FOR_INDEPENDENT_WINDOW: '等待独立验证数据', WAITING_OBSERVATION: '等待观察记录',
  BLOCKED: '需要处理阻塞', RECOVERY_REQUIRED: '需要核对中断结果', EXPIRED: '任务已到期',
  ATTEMPT_BUDGET_EXHAUSTED: '研究尝试次数已用完',
  BUDGET_EXHAUSTED: '研究额度已用完', CALL_LIMIT_EXHAUSTED: '本任务次数已用完',
  COMPLETED: '指定阶段已完成', COMPLETED_WITH_ISSUES: '已结束，存在缺口',
  IN_PROGRESS: '研究进行中', REVOKED: '授权已撤销', HALTED: '观察已停止',
  RISK_EXIT_ONLY: '触及观察风险上限，仅允许退出', REVIEW_DUE: '已到观察评审点，停止新买入',
  NO_CANDIDATE_PASSED: '本轮没有通过初筛的策略', ADAPTER_NOT_BOUND: '尚未接入执行服务',
  MISSED_WINDOW: '错过采集时段', CAPTURE_CONFIGURED: '采集范围已配置',
  PORTFOLIO_READINESS: '组合准备核验', STRATEGY_ADMISSIONS: '策略资格核验',
  OWNER_APPROVAL_REQUIRED: '等待维护者批准总范围与预算', WAITING_MODEL: '等待有可信硬预算的模型',
  HARD_BUDGET_UNSUPPORTED: '当前模型没有可信费用硬上限', WAITING_MODEL_RECONCILIATION: '等待核对原模型请求消费',
  WAITING_RESOURCE: '等待明确追加资源批准', PAUSED_OR_SCOPE_WAITING: '已暂停或等待授权范围',
  WAITING_SCOPE: '等待范围授权', GOAL_MET: '最终业务目标证据已满足', GOAL_NOT_MET: '最终业务目标尚未满足',
  CONFIGURED: '已登记，尚未启动', PREPARATION_CONTINUE: '资料准备分段进行中',
  RECONCILIATION_REQUIRED: '需要核对原工作进程与消费', WAITING_FINAL_EVIDENCE: '等待最终标准所需证据',
  WAITING_TOTAL_AUTHORIZATION: '等待明确追加总范围或额度', WAITING_PERMISSION_OR_MODEL: '等待原授权或可信模型',
  BUSINESS_GOAL_MET: '最终业务目标证据已满足', WAITING_OR_IN_PROGRESS: '独立验证等待或推进中',
  BUSINESS_EVIDENCE_RECORDED: '独立业务证据已记录，仍须核对最终标准',
  DEPLOYED_GATEWAY_EVIDENCE_REQUIRED: '等待部署网关的硬限制证据核验',
  WAITING_PUBLIC_DEPENDENCY: '等待公共资料或权限依赖', WAITING_STORAGE: '等待证据存储恢复',
  WAITING_FINAL_EXPLORATION_DATA: '等待504日最终探索资料',
  WAITING_FINAL_EXPLORATION_AUTHORIZATION: '等待最终探索原范围额度',
  FINAL_EXPLORATION_PREPARING: '最终探索资料准备中',
  FINAL_EXPLORATION_REPORT_COMPLETED: '最终探索报告已完成，等待复核',
  FINAL_EXPLORATION_READY: '最终探索标准已满足，等待独立验证',
  FINAL_EXPLORATION_FAILED: '最终探索未满足业务标准',
  NO_FINAL_EXPLORATION_PENDING: '当前无待晋级候选',
}
export function lifecycleState(value: unknown): string {
  if (typeof value !== 'string' || !value) return '尚未提供状态'
  return labels[value] ?? `待解释状态（${value}）`
}
export function objectStatus(record: LifecycleRecord): string {
  if (record.schema_version === 'RESEARCH_DATA_QUALIFICATION_V1') return '数据用途已核验，逐项查看缺口'
  if (record.preview && typeof record.preview === 'object') return '验证路线已冻结，等待合格证据'
  return lifecycleState(record.status)
}
export function dataSummary(record: LifecycleRecord): string | null {
  if (!Array.isArray(record.datasets)) return null
  const datasets = record.datasets as LifecycleRecord[]
  return `已检查 ${datasets.length} 组数据；其中 ${datasets.filter(row => row.historical_independence === 'EXPOSED').length} 组涉及已用于研究的窗口。是否可运行账户，仍须核对原始字段和公司行动；未授予独立验证资格。`
}
export function observationDays(record: LifecycleRecord): string {
  const value = record.real_observation_days
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value} 天` : '尚无观察日证据'
}
export function qualificationText(record: LifecycleRecord): string {
  if (record.profile === 'SYNTHETIC') return '合成验证，不授予真实资格'
  return record.strategy_qualified === true || record.portfolio_qualified === true
    ? '原资格服务报告符合条件' : '尚未取得资格，或此视图不授予资格'
}
export function sourceText(record: LifecycleRecord): string {
  const profile = record.profile ?? record.source_profile
  return profile === 'SYNTHETIC' ? '合成数据' : profile === 'REAL_OBSERVED' ? '实际到达的观察数据'
    : profile === 'HISTORICAL_MODELED' || profile === 'S1_MODELED_DAILY' ? '真实历史数据，部分时点采用模型'
      : '以原服务来源记录为准'
}
export function canOperate(view: LifecycleView, job: LifecycleRecord, jobId = ''): boolean {
  return view.actions_allowed === true && job.status !== 'RUNNING'
    && view.operation_permissions?.job_ids.includes(jobId) === true
}
export function canCreateBinding(view: LifecycleView, bindingId: string): boolean {
  return view.actions_allowed === true
    && view.operation_permissions?.create_binding_ids.includes(bindingId) === true
}
export function canOperateContinuous(view: LifecycleView, researchId: string): boolean {
  return view.actions_allowed === true && view.operation_permissions?.continuous_ids?.includes(researchId) === true
}
export function continuousResourceRows(record: LifecycleRecord): { name: string; used: unknown; reserved: unknown; remaining: unknown }[] {
  const budget = record.scope_budget as LifecycleRecord | undefined
  const remaining = budget?.remaining as LifecycleRecord | undefined
  if (!remaining || typeof remaining !== 'object') return []
  const names: Record<string, string> = { candidate_attempts: '候选尝试', data_experiments: '资料实验',
    account_jobs: '账户用途', model_calls: '模型调用', model_tokens: '模型 token',
    model_cost_microunits: '模型费用（微美元）', verification_jobs: '核验用途', wall_seconds: '活动计算秒' }
  const used = budget?.used as LifecycleRecord | undefined, reserved = budget?.reserved as LifecycleRecord | undefined
  return Object.entries(remaining).map(([key, value]) => ({ name: names[key] ?? key,
    used: used?.[key] ?? '—', reserved: reserved?.[key] ?? '—', remaining: value }))
}
export async function lifecycleRequest(path = '', body?: unknown): Promise<LifecycleRecord> {
  const response = await fetch(`/api/research-lifecycle${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail ?? value))
  return value
}
