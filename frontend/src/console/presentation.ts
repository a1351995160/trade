import type { CandidateSummaryView, JsonRecord } from './types'

export interface DisplayItem {
  label: string
  description: string
  code: string
  known: boolean
}

const mechanismLabels: Record<string, string> = {
  PRICE_STRUCTURE: '价格结构',
  TRAILING_HIGH_STRUCTURE: '前高结构',
  LIQUIDITY_PREMIUM: '流动性溢价',
  PARTICIPATION_QUALITY: '参与质量',
  VOLATILITY_EXPANSION: '波动率扩张',
  VOLATILITY_COMPRESSION: '波动率收缩',
  RELATIVE_STRENGTH: '相对强弱',
  TREND_CONTINUATION: '趋势延续',
  BREAKOUT: '突破',
  VOLUME_PRICE_CONFIRMATION: '量价确认',
  EVENT_CONTINUATION: '事件延续',
  FAILED_LIMIT_REVERSAL: '涨停失败反转',
  SENTIMENT_EXHAUSTION_REVERSAL: '情绪衰竭反转',
  DAILY_FACTOR: '日频因子研究',
  'event reversal': '事件反转',
  sentiment_event_continuation: '情绪事件延续',
}

const mechanismSemantics: Record<string, string> = {
  alpha_new_lower_wick_participation: '价格位置与成交参与',
  alpha_new_close_strength_structure: '收盘强度与价格结构',
  alpha_new_turnover_return_quality: '换手与收益质量',
  alpha_new_slow_slope_persistence: '慢趋势斜率持续',
  alpha_new_short_location_medium_return: '短期位置与中期收益',
  alpha_new_compressed_overreaction: '压缩区间反应',
  alpha_new_medium_return_shadow: '中期收益观察',
  alpha_new_slope_position_strength: '斜率与位置强度',
  alpha_new_volume_accel_body: '成交量加速',
  alpha_new_amount_volatility_capacity: '成交额波动容量',
}

const factorLabels: Record<string, { label: string; description: string }> = {
  MA_DISTANCE_20: { label: '20日均线距离', description: '观察价格相对20日均线的位置。' },
  MA_DISTANCE_60: { label: '60日均线距离', description: '观察价格相对60日均线的位置。' },
  MA_SLOPE_20: { label: '20日均线斜率', description: '观察20日均线的方向与变化。' },
  VOLUME_ACCEL: { label: '成交量加速', description: '观察成交量相对近期水平的变化速度。' },
  VOLUME_RATIO_1_20: { label: '1/20日成交量比', description: '比较当前成交量与近期平均成交量。' },
  VOLUME_RATIO_5_20: { label: '5/20日成交量比', description: '比较短期成交量与中期成交量水平。' },
  DONCHIAN_POSITION_20: { label: '20日通道位置', description: '观察价格在近期高低区间中的位置。' },
  RETURN_5D: { label: '5日收益', description: '观察最近5个交易日的价格变化。' },
  RETURN_10D: { label: '10日收益', description: '观察最近10个交易日的价格变化。' },
  RETURN_20D: { label: '20日收益', description: '观察最近20个交易日的价格变化。' },
  RETURN_60D: { label: '60日收益', description: '观察最近60个交易日的价格变化。' },
  RANGE_COMPRESSION_10: { label: '10日区间收缩', description: '观察最近10日价格区间是否收缩。' },
  RANGE_COMPRESSION_20: { label: '20日区间收缩', description: '观察最近20日价格区间是否收缩。' },
  MOM_ACCEL_5_20: { label: '5/20日动量加速', description: '观察短中期动量变化的加速度。' },
  VOL_RATIO_5_20: { label: '5/20日波动率比', description: '比较短期与中期波动水平。' },
  PRICE_VOLUME_CORR: { label: '价量相关性', description: '观察价格变化与成交量变化的一致程度。' },
  limit_down_reversal_signal: { label: '跌停反转信号', description: '统一登记的跌停反转信号因子；具体可用时间和执行条件以冻结合同为准。' },
}

const stateLabels: Record<string, string> = {
  BOOTSTRAP: '正在初始化', RECOVER: '正在恢复运行状态', READY: '已就绪',
  HYPOTHESIS: '策略假设', CANDIDATE: '候选策略生成', FREEZE: '规则冻结', SAMPLE_FEASIBILITY: '样本可行性确认',
  STRUCTURAL: '结构预检', STRUCTURAL_PENDING: '等待结构预检', STRUCTURAL_RUNNING: '正在进行结构预检',
  STRUCTURAL_PASS: '结构预检通过', STRUCTURAL_FAILED: '结构预检未通过', STRUCTURAL_UNKNOWN: '结构预检结果仍无法确认', STRUCTURAL_BLOCKED: '结构预检未通过',
  PREDICTIVE: '正式预测验证', PREDICTIVE_PENDING: '等待正式预测验证', PREDICTIVE_RUNNING: '正在进行正式预测验证',
  PREDICTIVE_COMPLETE: '正式预测验证完成', STATISTICAL: '统计可信度验证', FINAL_CLASSIFICATION: '最终研究分类',
  CANDIDATE_COMPLETE: '候选处理完成', NEXT_CANDIDATE: '准备处理下一候选',
  NEED_AI_RESEARCH_DESIGN: '需要 AI 研究员设计下一批策略', AI_HANDOFF_PREPARING: '正在准备 AI 研究交接', AI_MANUAL_HANDOFF_REQUIRED: '需要 AI 设计新的研究方案', AI_RESEARCH_DISABLED: 'AI 研究已禁用', AI_INVOCATION_PENDING: '等待 AI 研究调用', AI_INVOCATION_RUNNING: 'AI 研究员正在设计新策略', AI_OUTPUT_VALIDATING: '正在校验 AI 批次', AI_BATCH_INGESTING: '正在接入 AI 候选批次', LOCAL_RESEARCH_RESUMING: '正在恢复本地研究', AI_INVOCATION_UNAVAILABLE: 'AI 研究员当前不可用', AI_HANDOFF_BLOCKED: 'AI 交接已封锁', ENGINEERING_BLOCKED: '工程问题导致研究暂停',
  GOVERNANCE_REQUIRED: '需要治理决策', RESOURCE_WAIT: '等待系统资源', PAUSED: '已暂停',
  RESEARCH_PASSED: '研究通过', BUDGET_EXHAUSTED: '预测试验预算已耗尽',
  GLOBAL_SEARCH_EXHAUSTED: '合法研究空间已耗尽', TERMINAL_CLOSEOUT_PENDING: '等待自动收官', TERMINAL_CLOSEOUT_RUNNING: '正在自动收官', TERMINAL_CLOSEOUT_COMPLETE: '自动收官已完成', GOVERNANCE_DECISION_REQUIRED: '等待人工治理决定', SAFETY_STOP: '因安全规则停止', NO_PROGRESS_RESEARCH_LOOP: '研究无进展，已安全停机', SHUTDOWN: '已停止',
  FROZEN: '规则已冻结', COMPLETED: '已完成', INVALIDATED: '已作废', NOT_RUN: '尚未运行',
  REGISTERED: '已登记', RUNNING: '正在运行',
  ONLINE_ONLY: '仅在线数据可用', PARTIAL: '部分数据可用', PARTIAL_SAFE: '部分数据可用（已按安全规则限制）',
  SHADOW_ONLY: '仅限每日研究观察', SHADOW_RESEARCH_ONLY: '仅供研究观察', DISABLED: '已禁用',
  CONTRACT_GATED: '受研究规则约束', READY_WITH_UNKNOWN_FAIL_CLOSED: '状态无法确认，已按安全规则限制',
  READY_FOR_STRUCTURAL_PREFLIGHT: '可以进行结构预检', READY_AFTER_RANGE_ADAPTER_FIX: '已完成必要适配',
  ISOLATED_NOT_USED_FOR_CURRENT_STRUCTURAL_FULL_WINDOW: '当前结构预检未使用',
  PIT_VERIFIED: '时点一致性已验证', PIT_UNVERIFIED: '时点一致性未验证',
  NEXT_LEGAL_SESSION_NOT_RETURNED_BY_CANONICAL_CALENDAR: '尚未确认下一合法交易日',
  NEXT_SESSION_OPEN: '下一交易日开盘', T_CLOSE_AFTER_ALL_REQUIRED_FACTORS_AVAILABLE: '收盘时点完成全部因子后产生信号',
  MAX_FACTOR_AND_EVENT_AVAILABLE_AT: '全部因子与事件可用后生效', DEFERRED_TO_BACKTEST_ENGINE_V2: '由回测执行合同决定成交时点',
  FRESH: '最新', STALE: '可能过期', CONFLICT: '来源存在时间差',
  SAFE_TERMINAL: '已在安全边界结束', UNKNOWN: '当前无法确认状态',
}

const stateDescriptions: Record<string, string> = {
  AI_MANUAL_HANDOFF_REQUIRED: '系统已生成一份绑定研究目标的 AI 任务，等待人工复制提示词并放回结果文件；不会自动调用 AI。',
  AI_RESEARCH_DISABLED: 'AI 研究调用已关闭，系统不会创建任务、调用外部 AI 或消耗后台 AI 额度。',
  BUDGET_EXHAUSTED: '本轮允许进行的正式预测试验已经全部使用，系统不会继续验证剩余候选策略。',
  ENGINEERING_BLOCKED: '研究流程遇到程序或数据接口问题，当前状态已保存，需要修复后才能继续。',
  STRUCTURAL_FAILED: '候选策略未通过结构预检，系统不会启动预测试验或消耗预测试验预算。',
  STRUCTURAL_RUNNING: '正在检查候选策略是否拥有足够、合法且可执行的历史样本，此阶段尚不判断策略是否赚钱。',
  PREDICTIVE_RUNNING: '候选策略已通过结构检查，当前正在运行正式预测验证，该阶段会占用预测试验预算。',
  GOVERNANCE_DECISION_REQUIRED: '本轮研究已完成自动收官，下一步必须由人工选择，页面不会自动创建新目标。',
  TERMINAL_CLOSEOUT_COMPLETE: '预算、预测试验和研究终态已完成自动对账。',
  PARTIAL_SAFE: '部分信息尚不完整，系统会按安全规则避免使用无法确认的数据。',
  ONLINE_ONLY: '该数据当前没有完整的本地历史存档，需要在线数据源支持。',
  SHADOW_RESEARCH_ONLY: '使用当前数据运行策略进行观察，仅用于研究，不代表买入建议。',
  READY_WITH_UNKNOWN_FAIL_CLOSED: '关键条件尚未确认，系统按安全规则限制使用，不会据此推进研究。',
}

const classificationLabels: Record<string, string> = {
  RESEARCH_PASSED: '研究通过', PROMISING: '有潜力', WEAK: '证据较弱', REJECTED: '已淘汰', BLOCKED: '预测验证未通过',
  ENGINEERING_INVALIDATED: '因工程问题作废',
}

const actionLabels: Record<string, string> = {
  ENGINEERING_REPAIR_OR_RECONCILIATION: '完成程序修复或正式记录对账',
  CANONICAL_RUNTIME_REPAIR: '完成正式运行时修复',
  CANONICAL_TRIAL_RECONCILIATION_REQUIRED: '完成正式预测试验对账',
  HUMAN_TRIGGERED_AI_RESEARCH_DESIGN: '等待人工触发研究设计',
  PREDICTIVE_RETRY_NOT_AUTHORIZED_UNTIL_CANONICAL_RECONCILIATION: '完成正式记录对账后再判断是否可重试',
  RESUME_REQUIRED: '需要恢复运行',
  STOPPED_AT_SAFE_BOUNDARY: '已在安全边界停止',
  RESOURCE_WAIT: '等待系统资源恢复',
}

const reasonLabels: Record<string, { title: string; description: string }> = {
  END_OF_WINDOW_TRUNCATION: { title: '研究窗口尾部存在截断机会', description: '部分候选机会位于研究窗口尾部，无法完整观察后续执行周期。' },
  RAW_BOOTSTRAP_NOT_SUPPORTED: { title: '原始自助法重采样证据暂不支持', description: '当前研究环境无法提供所需的自助法重采样检验（Bootstrap）证据，因此不能形成完整研究裁决。' },
  ENGINEERING_INTERRUPTION_AFTER_PERFORMANCE_ACCESS: { title: '绩效访问后发生工程中断', description: '预测试验已经访问绩效边界，随后发生工程中断，不能把结果当作完整证据。' },
  EVIDENCE_INCOMPLETE_AFTER_PERFORMANCE_ACCESS: { title: '绩效访问后的证据不完整', description: '已有绩效访问记录，但完整统计或治理证据尚未形成。' },
  B_VALID_LOWER_BOUND_AT_OR_ABOVE_MINIMUM: { title: '安全下界达到最低要求', description: '结构预检确认可执行样本的安全下界达到当前最低要求。' },
  A_UPPER_BOUND_BELOW_MINIMUM: { title: '结构样本上界不足', description: '即使纳入所有已知可能机会，安全样本上界仍低于冻结政策要求，不能进入预测验证。' },
  D_LOWER_BOUND_INTEGRITY_NOT_ESTABLISHED_FAIL_CLOSED: { title: '安全下界完整性尚未确认', description: '安全下界的完整性证据不足，系统按安全规则保持阻断。' },
  FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE: { title: '冻结候选合同与执行器不兼容', description: '冻结候选合同无法转换为当前执行器支持的策略定义，系统已在预测试验前阻断并等待人工治理决定。' },
  PREDICTIVE_BUDGET_EXHAUSTED_BEFORE_CANDIDATE_SELECTION: { title: '选择候选前预测预算已耗尽', description: '预测试验预算为零，系统没有继续选择或启动新试验。' },
  FROZEN_CANDIDATE_SELECTED: { title: '已选择冻结候选策略', description: '研究守护进程已从冻结候选队列中选择当前研究对象。' },
  STRUCTURAL_PASS_OPENS_PREDICTIVE_BOUNDARY: { title: '结构预检通过，进入预测边界', description: '候选已通过结构性检查，可以进入预测性试验边界。' },
  CANONICAL_PREDICTIVE_RUN_STARTED: { title: '预测验证已开始', description: '正式预测执行器已开始处理该候选策略。' },
  COMPLETED: { title: '处理已完成', description: '该阶段已由正式运行记录标记为完成。' },
  READY_FOR_NEXT_CANDIDATE: { title: '等待下一个候选策略', description: '当前阶段结束，研究守护进程等待处理下一个冻结候选策略。' },
  ADVANCE_AFTER_CANONICAL_RESULT: { title: '已根据正式结果推进', description: '系统已根据已记录的正式结果推进研究流程。' },
  OFFICIAL_FULL_WINDOW_STRUCTURAL_PREFLIGHT_STARTED: { title: '完整窗口结构预检已开始', description: '结构预检正在使用当前研究窗口检查样本、时点一致性与执行可行性。' },
}

const termHelpLabels: Record<string, string> = {
  '结构预检': '结构预检不会查看策略赚不赚钱，只确认数据完整、时点一致、信号可合法产生、标的可交易且历史机会足够。',
  '预测验证': '候选策略通过结构检查后，在预先约束的未来区间进行正式验证。',
  '预测试验预算': '为避免反复试验导致过拟合，本轮研究只允许有限次数的正式预测验证。',
  '样本可行性': '确认可用于正式预测验证的有效历史机会是否达到最低数量要求。',
  '时点一致性（PIT）': '只允许使用当时已经能够获得的数据，避免未来数据泄漏到过去。',
  '自助法重采样检验（Bootstrap）': '通过大量重复抽样，检查当前研究结果是否可能只是偶然出现。',
  '多重检验校正': '同时测试的策略越多，偶然出现漂亮结果的概率越高，因此要统一提高统计要求。',
  '有潜力': '存在一定研究价值，但尚未达到研究通过标准，不代表买入建议。',
  '研究通过': '已达到当前研究验证标准，但不代表已获准真实交易。',
  '前瞻验证': '使用研究完成之后新产生的数据，观察策略在真正未来数据上的表现。',
  '最终测试集': '预先封存、不能用于策略开发和调参的数据区间，只有达到规定条件后才允许访问。',
  '每日研究观察': '使用当前数据运行策略进行观察，仅用于研究，不代表买入建议。',
  '研究守护进程': '负责持续保存和推进研究状态的后台研究进程；本页面只读监控，不提供控制按钮。',
}
termHelpLabels.PIT = termHelpLabels['时点一致性（PIT）']
termHelpLabels.Bootstrap = termHelpLabels['自助法重采样检验（Bootstrap）']
termHelpLabels.BH = '多重检验校正（BH 方法）用于同时测试多个策略时统一提高统计要求，降低偶然发现假 Alpha 的概率。'
termHelpLabels['多重检验'] = '测试的策略越多，偶然出现漂亮结果的概率越高，因此需要对统计显著性进行统一校正。'
termHelpLabels['多重检验校正（BH 方法）'] = termHelpLabels.BH

const metricLabels: Record<string, { label: string; description: string }> = {
  return: { label: '累计收益率', description: '研究区间内的累计收益表现。' },
  net_return: { label: '累计收益率', description: '研究区间内的累计收益表现。' },
  total_return: { label: '累计收益率', description: '研究区间内的累计收益表现。' },
  max_drawdown: { label: '最大回撤', description: '研究期间从高点回落的最大幅度。' },
  drawdown: { label: '最大回撤', description: '研究期间从高点回落的最大幅度。' },
  profit_factor: { label: '盈亏比（Profit Factor）', description: '总盈利与总亏损的比值。' },
  win_rate: { label: '胜率', description: '产生盈利结果的交易占全部交易的比例。' },
  trade_count: { label: '交易次数', description: '研究期间记录的交易总次数。' },
  closed_trade_count: { label: '交易次数', description: '研究期间记录的已完成交易总次数。' },
  p_value: { label: '自助法重采样显著性（Bootstrap）', description: '通过重复抽样评估结果偶然性的统计值。' },
  bootstrap_p_value: { label: '自助法重采样显著性（Bootstrap）', description: '通过重复抽样评估结果偶然性的统计值。' },
  adjusted_p_value: { label: '多重检验校正后显著性', description: '对同时测试多个策略后的统计显著性进行统一校正。' },
  adjusted_p: { label: '多重检验校正后显著性', description: '对同时测试多个策略后的统计显著性进行统一校正。' },
}

export interface HumanEvidenceRow { key: string; label: string; value: string; description: string }

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== ''
}

function scalarText(value: unknown): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  return String(value)
}

const candidateRules: Array<{ match: RegExp; name: string[]; summary: string }> = [
  { match: /^CAND_PRICE_STRUCTURE_TRAILING_HIGH_STRUCTURE/, name: ['价格结构', '前高结构'], summary: '关注价格在近期高点结构中的位置，并按冻结合同执行结构化筛选。' },
  { match: /^CAND_PRICE_STRUCTURE_INTERMEDIATE_DISTANCE/, name: ['价格结构', '中期位置'], summary: '关注价格相对中期结构的位置，并按冻结合同执行结构化筛选。' },
  { match: /^CAND_LIQUIDITY_PREMIUM_PARTICIPATION_QUALITY/, name: ['流动性溢价', '参与质量'], summary: '关注流动性与参与质量的组合特征，具体信号以冻结合同为准。' },
  { match: /^CAND_LIQUIDITY_PREMIUM_/, name: ['流动性溢价', '参与质量'], summary: '关注流动性与市场参与特征，具体信号以冻结合同为准。' },
  { match: /^CAND_RELATIVE_STRENGTH/, name: ['相对强弱'], summary: '关注不同标的之间的相对强弱，具体排名规则以冻结合同为准。' },
  { match: /^CAND_SHORT_TERM_REVERSAL/, name: ['短期反转'], summary: '关注短期价格反应后的反转特征，具体信号以冻结合同为准。' },
  { match: /^CAND_MEAN_REVERSION/, name: ['均值回归'], summary: '关注价格偏离后的回归特征，具体信号以冻结合同为准。' },
  { match: /^CAND_TREND_CONTINUATION/, name: ['趋势延续'], summary: '关注趋势方向与延续条件，具体信号以冻结合同为准。' },
  { match: /^CAND_VOLATILITY_COMPRESSION/, name: ['波动率收缩'], summary: '关注价格区间收缩后的研究机会，具体信号以冻结合同为准。' },
  { match: /^CAND_VOLATILITY_EXPANSION/, name: ['波动率扩张'], summary: '关注波动水平扩张的结构特征，具体信号以冻结合同为准。' },
  { match: /^CAND_VOLUME_PRICE_CONFIRMATION/, name: ['量价确认'], summary: '关注价格变化与成交量变化的一致性，具体信号以冻结合同为准。' },
]

export function displayMechanism(code: unknown): DisplayItem {
  const value = String(code || '').trim()
  const label = mechanismLabels[value] || mechanismSemantics[value]
  return { code: value, label: label || `未知研究机制`, description: label ? '已配置中文研究机制展示。' : `原始机制标识：${value || '未提供'}`, known: Boolean(label) }
}

export function displayFactor(code: unknown): DisplayItem {
  const value = String(code || '').trim()
  const item = factorLabels[value]
  return { code: value, label: item?.label || '未配置中文因子名称', description: item?.description || `原始因子标识：${value || '未提供'}`, known: Boolean(item) }
}

export function displayState(code: unknown): DisplayItem {
  const value = String(code || 'UNKNOWN').trim().toUpperCase()
  return { code: value, label: stateLabels[value] || '当前状态无法确认', description: stateDescriptions[value] || stateLabels[value] || '当前字段暂无可靠的中文解释，请查看技术信息。', known: Boolean(stateLabels[value]) }
}

export function displayStatus(code: unknown): DisplayItem {
  return displayState(code)
}

export function displayClassification(code: unknown): DisplayItem {
  const value = String(code || '').trim().toUpperCase()
  const label = classificationLabels[value]
  const descriptions: Record<string, string> = {
    PROMISING: '存在一定研究价值，但尚未达到研究通过标准，不代表买入建议。',
    RESEARCH_PASSED: '已达到当前研究验证标准，但不代表已获准实盘交易。',
    WEAK: '现有证据较弱，尚不足以支持研究通过。',
    REJECTED: '当前研究规则下已淘汰，不代表任何单独的投资结论。',
    BLOCKED: '正式预测验证已完成，但未通过当前冻结研究门槛；这不是工程故障，也不等同于投资结论。',
    ENGINEERING_INVALIDATED: '该试验不是因为策略表现差而失败，而是在预测验证过程中发生工程异常，结果不能作为有效研究结论。',
  }
  return { code: value, label: label || '未分类', description: descriptions[value] || '当前尚未提供可读的最终分类说明。', known: Boolean(label) }
}

export function displayAction(code: unknown): string {
  const value = String(code || '').trim().toUpperCase()
  return actionLabels[value] || (value ? `需要人工确认（${value}）` : '无需处理')
}

export function timingLabel(code: unknown): string {
  const value = String(code || '').trim().toUpperCase()
  return displayState(value).known ? displayState(value).label : '当前合同未提供可读时点'
}

export function displayReason(code: unknown): DisplayItem {
  const value = String(code || '').trim().toUpperCase()
  const item = reasonLabels[value]
  return { code: value, label: item?.title || '当前原因无法确认', description: item?.description || '当前字段暂无可靠的中文解释，请查看技术信息。', known: Boolean(item) }
}

export function candidatePresentation(candidate: Partial<CandidateSummaryView> & { candidate_id?: string; semantics?: JsonRecord }): { name: string; summary: string; mechanism: DisplayItem; covered: boolean } {
  const id = String(candidate.candidate_id || '')
  const rule = candidateRules.find((item) => item.match.test(id))
  const semanticMechanism = candidate.semantics?.mechanism
  const mechanism = displayMechanism(semanticMechanism || candidate.mechanism_family)
  const knownMechanismName = mechanism.known ? `${mechanism.label}候选策略` : '未配置中文候选名称'
  const knownMechanismSummary = mechanism.known ? `围绕${mechanism.label}机制进行研究，具体信号与执行条件以冻结合同为准。` : '当前冻结合同未提供足够可靠的中文候选说明，请以技术标识和合同语义为准。'
  return {
    name: rule?.name.join(' + ') || knownMechanismName,
    summary: rule?.summary || knownMechanismSummary,
    mechanism: rule ? { code: rule.name.join('_'), label: rule.name[0], description: '由已登记的候选机制语义生成。', known: true } : mechanism,
    covered: Boolean(rule),
  }
}

export function stageLabel(code: unknown): string {
  return displayState(code).label
}

export function freshnessLabel(code: unknown): DisplayItem {
  const value = String(code || 'UNKNOWN').toUpperCase()
  const labels: Record<string, [string, string]> = {
    FRESH: ['数据最新', '当前来源时间在允许范围内。'], STALE: ['数据可能过期', '来源时间较旧，请结合时间戳判断。'],
    CONFLICT: ['不同来源更新时间不一致', '不同数据源更新时间不一致，系统已按照权威优先级采用更可靠的当前记录。'],
    UNKNOWN: ['无法确认更新时间', '当前没有足够来源时间信息。'],
  }
  const item = labels[value] || labels.UNKNOWN
  return { code: value, label: item[0], description: item[1], known: Boolean(labels[value]) }
}

export function termHelp(term: string): string {
  return termHelpLabels[term] || '当前术语暂无补充说明。'
}

export function objectivePresentation(objectiveId: string): { name: string; id: string } {
  if (objectiveId === 'RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1') return { name: 'A股短周期策略研究 V1', id: objectiveId }
  if (objectiveId.includes('GOVERNED_PROMISING_FOLLOWUP')) return { name: '有潜力候选后续研究', id: objectiveId }
  return { name: '当前研究目标', id: objectiveId }
}

export function metricPresentation(key: string): { label: string; description: string; known: boolean } {
  const item = metricLabels[key]
  return item ? { ...item, known: true } : { label: '当前字段暂无中文解释', description: '该字段仅在技术信息中保留。', known: false }
}

export function humanEvidenceRows(value: unknown, authorized = true): HumanEvidenceRow[] {
  if (!authorized || !value || typeof value !== 'object' || Array.isArray(value)) return []
  const rows: HumanEvidenceRow[] = []
  const seenLabels = new Set<string>()
  function collect(record: Record<string, unknown>, path: string[] = []) {
    for (const [key, raw] of Object.entries(record)) {
      if (raw && typeof raw === 'object' && !Array.isArray(raw) && path.length < 3) {
        collect(raw as Record<string, unknown>, [...path, key])
        continue
      }
      const presentation = metricPresentation(key)
      if (!presentation.known || !hasValue(raw) || typeof raw === 'object' || seenLabels.has(presentation.label)) continue
      rows.push({ key: [...path, key].join('.'), label: presentation.label, value: scalarText(raw), description: presentation.description })
      seenLabels.add(presentation.label)
    }
  }
  collect(value as Record<string, unknown>)
  return rows
}

export function humanDecisionRows(decision: Record<string, unknown> | null | undefined, summary?: Record<string, unknown> | null): HumanEvidenceRow[] {
  if (!decision) return []
  const source = { ...decision, ...summary }
  const classification = displayClassification(source.classification)
  const rows: HumanEvidenceRow[] = []
  if (hasValue(source.classification)) rows.push({ key: 'classification', label: '最终分类', value: classification.label, description: classification.description })
  if (hasValue(source.final_adjudicated)) rows.push({ key: 'final_adjudicated', label: '是否形成最终裁决', value: source.final_adjudicated ? '是' : '否', description: source.final_adjudicated ? '系统已形成最终研究规则裁决。' : '当前记录未标记为已完成最终裁决。' })
  if (hasValue(source.final_test_access) && typeof source.final_test_access === 'object' && source.final_test_access !== null) {
    const access = Object.values(source.final_test_access as Record<string, unknown>).some((item) => Number(item) > 0)
    rows.push({ key: 'final_test_access', label: '是否访问最终测试集', value: access ? '是' : '否', description: termHelpLabels['最终测试集'] })
  }
  if (hasValue(source.prospective)) rows.push({ key: 'prospective', label: '是否进行前瞻验证', value: Number(source.prospective) > 0 ? '是' : '否', description: termHelpLabels['前瞻验证'] })
  if (hasValue(source.real_order)) rows.push({ key: 'real_order', label: '是否产生真实订单', value: String(source.real_order).toUpperCase() === 'DISABLED' ? '否' : '当前无法确认', description: '当前系统只进行研究与观察，不会自动向券商发送真实交易订单。' })
  return rows
}

export function formatCoverage(value: unknown): string {
  if (!value) return '暂无覆盖范围记录'
  if (typeof value === 'string') return value === 'manifest-scoped' ? '按已登记清单覆盖' : value === 'research window' ? '覆盖研究区间' : value === 'trade_date' ? '覆盖当前交易日' : value
  if (typeof value !== 'object' || Array.isArray(value)) return '当前字段暂无中文解释'
  const record = value as Record<string, unknown>
  const start = record.start || record.earliest_date
  const end = record.end || record.latest_date
  const range = start && end ? `覆盖 ${formatDate(start)} 至 ${formatDate(end)}` : ''
  const tradeDays = hasValue(record.trade_day_count) ? `，${record.trade_day_count} 个交易日` : ''
  const securities = hasValue(record.security_count) ? `，${record.security_count} 个标的` : ''
  const signals = hasValue(record.raw_signal_count) ? `，${record.raw_signal_count} 个原始信号` : ''
  return `${range}${tradeDays}${securities}${signals}`.replace(/^，/, '') || '已登记覆盖范围（详细字段见技术信息）'
}

export function displayPIT(value: unknown): string {
  const code = String(value || '').trim().toUpperCase()
  if (code === 'PIT_VERIFIED') return '已通过时点一致性检查'
  if (code === 'PIT_UNVERIFIED') return '尚未通过时点一致性检查'
  if (code === 'UNKNOWN' || !code) return '当前无法确认时点一致性'
  if (code.includes('NEXT_LEGAL_SESSION')) return '尚未确认下一合法交易日'
  return '当前无法确认时点一致性'
}

export function displayExecutionFeasibility(value: unknown): string {
  const code = String(value || '').trim().toUpperCase()
  if (!code || code === 'UNKNOWN') return '当前无法确认执行可行性'
  if (code === 'PASS' || code === 'READY') return '已确认可执行'
  if (code === 'FAIL') return '未确认可执行'
  return stateLabels[code] || '当前无法确认执行可行性'
}

export function humanReportTitle(item: Record<string, unknown>): string {
  const title = String(item.title_zh || item.title || '').trim()
  const replacements: Array<[RegExp, string]> = [
    [/^Research Daemon 状态机$/, '研究守护进程状态机'],
    [/^Daemon 当前 Handoff$/, '研究守护进程当前处理说明'],
    [/^当前 Candidate 结构预检$/, '当前候选策略结构预检'],
    [/^Shadow 扫描就绪状态$/, '每日研究观察扫描就绪状态'],
    [/^Shadow 每日扫描$/, '每日研究观察扫描'],
    [/^ValidationDecisionPolicy 锁$/, '研究决策规则锁定记录'],
    [/^Autonomous Research.*Closeout/i, '自主研究自动收官报告'],
    [/^Research Governance.*Required/i, '研究治理决策记录'],
    [/^AI.*Invocation.*Acceptance/i, 'AI 研究员接入验收报告'],
  ]
  const mapped = replacements.find(([match]) => match.test(title))
  if (mapped) return mapped[1]
  if (!title) return '已登记研究报告'
  return title
    .replace(/NoOutcome/gi, '无绩效结果隔离')
    .replace(/Orchestrator/gi, '自主研究编排器')
    .replace(/Daemon/gi, '研究守护进程')
    .replace(/Handoff/gi, 'AI 研究任务')
    .replace(/Candidates?/gi, '候选策略')
    .replace(/Objective/gi, '研究目标')
    .replace(/Trials?/gi, '预测试验')
    .replace(/Governance/gi, '研究治理')
    .replace(/Canonical/gi, '受保护')
    .replace(/Manifest/gi, 'AI 研究结果清单')
    .replace(/Pipeline/gi, '研究流程')
    .replace(/Runtime/gi, '运行状态')
    .replace(/Validation/gi, '校验')
    .replace(/Classification/gi, '分类')
    .replace(/Shadow/gi, '每日研究观察')
    .replace(/Data Health/gi, '数据健康')
    .replace(/Report Center/gi, '报告中心')
}

export function humanReportCategory(value: unknown): string {
  const normalized = String(value || '').toUpperCase()
  if (normalized.includes('DAEMON')) return '研究守护进程'
  if (normalized.includes('CANDIDATE')) return '候选策略'
  if (normalized.includes('TRIAL')) return '预测试验'
  if (normalized.includes('SHADOW')) return '每日研究观察'
  if (normalized.includes('DATA')) return '数据'
  if (normalized.includes('GOVERN')) return '研究规则'
  if (normalized.includes('CLOSEOUT') || normalized.includes('AUTONOMOUS_RESEARCH')) return '研究收官'
  if (normalized.includes('AI') || normalized.includes('CODEX')) return 'AI研究员'
  if (normalized.includes('ARCH')) return '架构'
  return '研究报告'
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '暂无数据'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function formatDate(value: unknown): string {
  if (value === null || value === undefined || value === '') return '暂无数据'
  const raw = String(value)
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}`
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? raw : date.toLocaleString('zh-CN', { hour12: false })
}

export function sourceLabel(source: unknown): string {
  const value = String(source || '')
  const labels: Record<string, string> = {
    benchmark_index_daily: '基准指数日线', corporate_action_gbbq: '公司行动与除权除息', daily_ohlcva_raw: '日线行情',
    minute_5_ohlcva: '5分钟行情', financial_series: '财务序列', lhb_professional: '龙虎榜专业数据', limit_up_ecology: '涨停生态',
    money_flow: '资金流', current_shadow_data: '当前每日研究观察数据', trade_calendar: '交易日历', SHADOW_SECURITY_STATE: '每日研究观察证券状态',
    'daily_all.parquet': '日线行情结构输入', 'security_state/normalized/pit_universe_v2': '时点一致性证券状态',
    'security_state/raw/trade_calendar.json': '研究交易日历', 'unified_factor_registry + factor_library_v1': '冻结因子定义与物化',
    event_store_repaired: '修复后的事件存储', PIT_UNIVERSE_DATASET_V2: 'PIT 证券数据集',
    'TQ get_trading_calendar': 'TQ 交易日历', 'TQ get_market_data/get_stock_info/get_more_info': 'TQ 每日观察行情与证券状态',
    'TDX Raw HQ / BaoStock minute providers': 'TDX / BaoStock 分钟数据源',
  }
  return labels[value] || (value && /[\u4e00-\u9fff]/.test(value) ? value : '当前数据来源')
}
