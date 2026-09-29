export type LifecycleRecord = Record<string, unknown>
export interface LifecycleView {
  bindings: Record<string, LifecycleRecord>
  binding_catalog: Record<string, { kind: string }>
  jobs: Record<string, LifecycleRecord>
  actions_allowed?: boolean
  operation_permissions?: { create_binding_ids: string[]; job_ids: string[] }
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
export async function lifecycleRequest(path = '', body?: unknown): Promise<LifecycleRecord> {
  const response = await fetch(`/api/research-lifecycle${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail ?? value))
  return value
}
