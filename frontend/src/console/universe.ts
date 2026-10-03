export interface UniverseDataset {
  dataset_id: string
  adapter?: string
  universe_id?: string
  target_count?: number
  symbols?: string[]
  target_symbols?: string[]
  completeness?: string
  by_board?: Record<string, Record<string, unknown>>
}

export const universeBoards = [
  { id: 'SZ_MAIN', name: '深圳主板', prefix: '00' },
  { id: 'SH_MAIN', name: '上海主板', prefix: '60' },
  { id: 'CHINEXT', name: '创业板', prefix: '30' },
]

export function isFullUniverseDataset(dataset: UniverseDataset | undefined): boolean {
  return (dataset?.adapter === 'TDX_FULL_UNIVERSE_V1' || dataset?.adapter === 'BAOSTOCK_FULL_UNIVERSE_V1')
    && typeof dataset.universe_id === 'string' && dataset.universe_id.length > 0
}

export function submissionRequest(base: Record<string, unknown>, dataset: UniverseDataset | undefined,
  symbols: string, accountScope = 'FULL_REQUIRED', executionProfile?: Record<string, unknown>): Record<string, unknown> {
  const request = { ...base }
  delete request.symbols
  delete request.version
  delete request.universe_id
  delete request.account_scope
  delete request.execution_profile
  delete request.observation_plan
  if (isFullUniverseDataset(dataset)) {
    request.version = accountScope === 'DATA_QUALIFIED' ? 'FULL_UNIVERSE_SUBMISSION_V2' : 'FULL_UNIVERSE_SUBMISSION_V1'
    if (accountScope === 'DATA_QUALIFIED') request.account_scope = 'DATA_QUALIFIED'
    request.universe_id = dataset!.universe_id
    request.benchmark = 'CASH_AND_PRICE_REFERENCE'
    if (executionProfile) {
      request.version = 'FULL_UNIVERSE_SUBMISSION_V3'
      request.account_scope = 'DATA_QUALIFIED'
      request.execution_profile = { ...executionProfile }
      request.observation_plan = { version: 'SIGNAL_OBSERVATION_PLAN_V1', horizons: [5, 10, 20],
        entry: 'NEXT_EXCHANGE_SESSION_RAW_OPEN', exit: 'HORIZON_SESSION_RAW_CLOSE',
        price_policy: 'FRACTIONAL_ONE_SHARE_ENTITLED_ECONOMIC_VALUE_V1',
        comparator: 'QUALIFIED_SCOPE_EQUAL_WEIGHT_SAME_ENTRY_HORIZON',
        start: Number(String(request.account_start).replace(/-/g, '')),
        end: Number(String(request.account_end).replace(/-/g, '')) }
    }
  } else {
    request.symbols = symbols.split(/[，,\s]+/).filter(Boolean)
    request.benchmark = 'FULL_POOL_BUY_HOLD'
  }
  return request
}

export function universeDiagnosisAllowsFreeze(diagnosis: Record<string, unknown> | null,
  previewIdentity: unknown): boolean {
  if (!diagnosis || typeof previewIdentity !== 'string' || !previewIdentity) return false
  const scope = diagnosis.scope as Record<string, unknown> | undefined
  const coverage = diagnosis.coverage as Record<string, unknown> | undefined
  return scope?.preview_identity === previewIdentity &&
    ((diagnosis.status === 'ACCOUNT_INPUTS_READY' && coverage?.account_data_ready === true)
     || (diagnosis.status === 'QUALIFIED_SCOPE_READY' && diagnosis.qualified_account_ready === true))
}

function count(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value.toLocaleString('zh-CN') : '未知'
}

export function universeCoverage(record: Record<string, unknown>) {
  const raw = record.coverage
  const coverage = raw && typeof raw === 'object' ? raw as Record<string, unknown> : record
  const byBoard = coverage.by_board && typeof coverage.by_board === 'object'
    ? coverage.by_board as Record<string, Record<string, unknown>> : {}
  const perSymbol = Array.isArray(coverage.per_symbol) ? coverage.per_symbol as Record<string, unknown>[] : []
  const rawGaps = Array.isArray(coverage.gaps) ? coverage.gaps as Record<string, unknown>[] : []
  const globalGaps = Array.isArray(coverage.global_gaps) ? coverage.global_gaps.map(String) : []
  const boards = universeBoards.map(board => {
    const row = byBoard[board.id] ?? {}
    return { ...board, target: count(row.target_count), cached: count(row.cached_count),
      qualified: count(row.account_qualified_count ?? row.qualified_count),
      identityUnknown: count(row.identity_unknown_count),
      identityConflict: count(row.identity_conflict_count) }
  })
  return {
    target: count(coverage.target_count ?? coverage.target_symbol_count), boards,
    completeness: coverage.completeness === 'HISTORICAL_MASTER_VERIFIED'
      ? '已有历史清单完整性证据' : '历史清单完整性尚未证明',
    account: coverage.account_data_ready === true ? '数据检查报告账户输入齐全；运行仍需授权'
      : coverage.account_data_ready === false ? '账户数据仍有缺口，不能给出完整收益结论' : '账户数据尚未核验',
    checkedSymbols: count(perSymbol.length || undefined),
    gaps: [...globalGaps, ...rawGaps.map(row =>
      `${String(row.symbol ?? '')} ${String(row.reason ?? '未知缺口')}（${count(row.count)} 次）`.trim())],
  }
}

export function universeRunState(value: unknown): string {
  const labels: Record<string, string> = {
    DATA_GAPS: '数据存在缺口', ACCOUNT_BLOCKED: '账户计算被数据缺口阻断',
    ACCOUNT_INPUTS_READY: '数据诊断通过；账户执行仍需已有授权',
    SCAN_DATA_GAPS: '已检查完整范围，部分条件缺证据、保持未知',
    SCAN_BLOCKED: '信号检查未完成，需核对原任务后恢复',
    CONDITIONS_EVALUATED: '已计算原始规则条件，未运行交易账户',
    CONDITIONS_EVALUATED_WITH_UNKNOWN_SESSIONS: '已计算部分日期条件，其余日期未知',
    UNKNOWN: '未知，不能判断条件是否命中',
    QUALIFIED_SCOPE_READY: '全池检查完成，全部资料合格股票可进入账户冻结',
    QUALIFIED_SCOPE_BLOCKED: '全池检查完成，仍有共同缺口或没有合格股票',
    DATA_QUALIFIED: '资料合格，是否买入由策略决定', EXCLUDED: '资料不满足，本次账户范围排除',
    ACCOUNT_SCOPE_BLOCKED: '存在共同缺口，本股票也不能进入账户',
    PARTIAL_COMPLETED: '部分完成，尚未覆盖全部范围',
    ENGINEERING_NOT_ACCEPTED: '尚无新版本完整工程验收证据',
    REAL_NOT_ACCEPTED: '尚无真实全范围验收证据',
    WAITING_DATA: '等待补齐数据', SCANNING: '正在扫描登记的全部股票',
    RUNNING: '正在处理', COMPLETED: '指定任务已完成；策略资格另行评审',
    PAUSED: '已在安全边界暂停；原账户和预算保留', PAUSE_REQUESTED: '已请求暂停，等待收盘或换段安全边界',
    READY_TO_CONTINUE: '已完成一段，原用途可继续', RESUME_REQUIRED: '需核对原任务后恢复',
    PREPARATION_RUNNING: '正在准备全部合格股票的因果指标',
    ACCOUNT_RUNNING: '正在推进连续交易账户', VERIFICATION_RUNNING: '正在独立核对账户',
    REPORT_RUNNING: '正在生成账户与信号两份报告',
    PREPARATION: '指标准备', ACCOUNT: '连续账户', VERIFICATION: '独立核账', REPORT: '双报告',
    RECONCILED_DIAGNOSTIC: '账户已核对；不代表策略已证明有效',
  }
  return typeof value === 'string' ? labels[value] ?? value : '尚未提供状态'
}

export function universeTaskState(record: Record<string, unknown>): string {
  if (typeof record.status === 'string') return universeRunState(record.status)
  const items = Object.values((record.items ?? {}) as Record<string, Record<string, unknown>>)
  if (items.length && items.length === record.total && items.every(item => item.state === 'COMPLETED')) {
    return '全部账户用途已完成；核账、报告和策略资格须分别查看证据'
  }
  return universeRunState(record.status)
}

export function universeTaskAllowsResume(record: Record<string, unknown> | null, taskId: string): boolean {
  if (!record || record.task_id !== taskId || record.version !== 'UNIVERSE_TASK_METADATA_V1') return false
  const items = record.items && typeof record.items === 'object'
    ? record.items as Record<string, Record<string, unknown>> : {}
  const stages = record.compute_stages && typeof record.compute_stages === 'object'
    ? record.compute_stages as Record<string, Record<string, unknown>> : {}
  if (record.stage === 'COMPLETED' || ['COMPLETED', 'FAILED'].includes(String(stages.REPORT?.state))) return false
  if (record.execution_profile && (record.paused === true || Object.values(stages)
    .some(stage => ['PAUSED', 'READY_TO_CONTINUE', 'RESUME_REQUIRED'].includes(String(stage.state))))) return true
  return Object.values(items).some(item => item.state === 'UNSETTLED_CHECK_WORKER'
    || (record.execution_profile && ['PAUSED', 'READY_TO_CONTINUE', 'RESUME_REQUIRED'].includes(String(item.state))))
}

export function universeTaskAllowsPause(record: Record<string, unknown> | null, taskId: string): boolean {
  if (!record || record.task_id !== taskId || record.version !== 'UNIVERSE_TASK_METADATA_V1') return false
  const profile = record.execution_profile as Record<string, unknown> | undefined
  if (profile?.profile_id !== 'LONG_HORIZON_SEGMENTED_V1') return false
  const items = record.items && typeof record.items === 'object'
    ? record.items as Record<string, Record<string, unknown>> : {}
  const stages = record.compute_stages && typeof record.compute_stages === 'object'
    ? record.compute_stages as Record<string, Record<string, unknown>> : {}
  if (record.stage === 'COMPLETED' || ['COMPLETED', 'FAILED'].includes(String(stages.REPORT?.state))) return false
  return Object.values(items).some(item => item.state === 'RUNNING')
    || Object.values(stages).some(stage => stage.state === 'RUNNING')
}

export function universeLongProgress(record: Record<string, unknown> | null) {
  const profile = record?.execution_profile as Record<string, unknown> | undefined
  if (!profile) return null
  const items = record?.items && typeof record.items === 'object'
    ? record.items as Record<string, Record<string, unknown>> : {}
  const stages = record?.compute_stages && typeof record.compute_stages === 'object'
    ? record.compute_stages as Record<string, Record<string, unknown>> : {}
  const seconds = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value.toFixed(1) : '未知'
  return { stage: universeRunState(record?.stage), sessionBound: count(profile.account_sessions), workerSeconds: seconds(profile.worker_seconds),
    memoryMib: count(profile.memory_mib), rows: Object.entries(items).map(([name, item]) => ({
      name, state: universeRunState(item.state), phase: universeRunState(item.phase),
      processed: count(item.processed_sessions), sessions: count(item.account_sessions),
      lastDay: typeof item.last_day === 'number' ? String(item.last_day) : '尚未完成收盘',
      chargedSeconds: seconds(item.charged_seconds), remainingSeconds: seconds(item.remaining_seconds),
    })), stages: Object.entries(stages).map(([name, item]) => ({
      name: universeRunState(name), state: universeRunState(item.state),
      chargedSeconds: seconds(item.charged_seconds), remainingSeconds: seconds(item.remaining_seconds),
    })) }
}

export function universeScanSummary(record: Record<string, unknown>) {
  const rows = Array.isArray(record.per_symbol) ? record.per_symbol as Record<string, unknown>[] : []
  return {
    target: count(record.target_count), processed: count(record.processed_target_count),
    evaluated: count(record.signals_evaluated_target_count), sessions: count(record.signals_evaluated_session_count),
    unknown: count(record.unknown_target_count),
    anyEvaluated: typeof record.signals_evaluated_target_count === 'number' && record.signals_evaluated_target_count > 0,
    rows: rows.map(row => {
      const counts = row.condition_counts && typeof row.condition_counts === 'object'
        ? row.condition_counts as Record<string, unknown> : {}
      return { symbol: String(row.symbol), status: universeRunState(row.status),
        evaluated: count(row.evaluated_sessions), unknown: count(row.unknown_sessions),
        buy: count(counts.buy), sell: count(counts.sell) }
    }),
  }
}
