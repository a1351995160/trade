<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { isFullUniverseDataset, submissionRequest, universeCoverage, universeDiagnosisAllowsFreeze, universeRunState, universeTaskState, universeScanSummary, universeTaskAllowsResume, universeTaskAllowsPause, universeLongProgress } from '../universe'
import type { UniverseDataset } from '../universe'
import { displayReason } from '../presentation'
interface Publication { status: string; feature_ids: string[]; case_count?: number; account_count?: number; initial_cash?: number; actual_sessions?: number[]; reason?: string; evidence_fingerprint?: string }
const publication = ref<Publication | null>(null)
const longPublication = ref<Publication | null>(null)
const publishedFor = (id: string) => {
  const evidence = fullUniverse.value && longHorizon.value ? longPublication.value : !fullUniverse.value ? publication.value : null
  return evidence?.status === 'PUBLISHED_METADATA_VERIFIED' && evidence.feature_ids.includes(id)
}
interface Feature { id: string; name: string; public_entry: boolean; engine: boolean; evidence: string }
const features = ref<Feature[]>([]), datasets = ref<UniverseDataset[]>([])
const fullScope = ref<{ indicator_count: number; engineering_evidence: string; real_evidence: string } | null>(null)
const enabled = ref(false), configured = ref(false), busy = ref(false), error = ref('')
const definition = ref(''), dataset = ref(''), symbols = ref(''), money = ref(50000), positions = ref(2), authority = ref('')
const preheat = ref(''), start = ref(''), end = ref(''), taskId = ref('')
const approval = ref<Record<string, unknown> | null>(null)
const preview = ref<Record<string, unknown> | null>(null), result = ref<Record<string, unknown> | null>(null)
const diagnosis = ref<Record<string, unknown> | null>(null)
const signalScan = ref<Record<string, unknown> | null>(null)
const accountScope = ref('FULL_REQUIRED')
const longHorizon = ref(false), profileChoice = ref('252'), customProfile = ref('')
const executionProfiles = ref<Record<string, Record<string, unknown>>>({})
const scoreSummary = computed(() => {
  try {
    const rule = JSON.parse(definition.value)
    const selection = rule.selection
    return rule.version === 'RESEARCH_RULE_STRATEGY_V4' && selection
      ? { direction: selection.direction === 'ASCENDING' ? '分数较低者优先' : '分数较高者优先',
          expression: JSON.stringify(selection.score), tie: '同分按股票代码固定顺序' } : null
  } catch { return null }
})
const qualificationScope = computed(() => (result.value?.qualification_scope ?? diagnosis.value?.qualification_scope) as
  { target_symbols: string[]; qualified_symbols: string[]; blocking_global_gaps: string[]; excluded: {symbol: string; reasons: string[]}[] } | undefined)
const selectedDataset = computed(() => datasets.value.find(item => item.dataset_id === dataset.value))
const fullUniverse = computed(() => isFullUniverseDataset(selectedDataset.value))
const selectedCoverage = computed(() => universeCoverage((selectedDataset.value ?? {}) as unknown as Record<string, unknown>))
const actualCoverage = computed(() => universeCoverage(result.value ?? diagnosis.value ?? preview.value ?? {}))
const diagnosticReady = computed(() => universeDiagnosisAllowsFreeze(diagnosis.value, preview.value?.preview_identity))
const scanSummary = computed(() => universeScanSummary(signalScan.value ?? {}))
const canResume = computed(() => universeTaskAllowsResume(result.value, taskId.value))
const canPause = computed(() => universeTaskAllowsPause(result.value, taskId.value))
const longProgress = computed(() => universeLongProgress(result.value))
let submitted: Record<string, unknown> | null = null
let draftRevision = 0
async function api(path: string, body?: unknown) {
  const response = await fetch('/api/research-submission' + path, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(data.detail ?? data))
  return data
}
async function action(work: () => Promise<void>) { busy.value = true; error.value = ''; try { await work() } catch (failure) { error.value = String(failure) } finally { busy.value = false } }
async function prepare() {
  await action(async () => {
    preview.value = null
    diagnosis.value = null; signalScan.value = null; approval.value = null; result.value = null; taskId.value = ''
    const revision = draftRevision
    const profile = fullUniverse.value && longHorizon.value
      ? profileChoice.value === 'CUSTOM' ? JSON.parse(customProfile.value) : executionProfiles.value[profileChoice.value] : undefined
    if (fullUniverse.value && longHorizon.value && !profile) throw new Error('尚未获得系统登记的长期资源规格')
    submitted = submissionRequest({ strategy_id: 'submitted_strategy', rule: JSON.parse(definition.value), dataset_id: dataset.value, initial_cash: money.value,
      feature_start: preheat.value, account_start: start.value, account_end: end.value, max_positions: positions.value, max_symbol_exposure_bps: Math.floor(10000 / positions.value),
      costs: ['BASE', 'STRESS'], purpose: 'EXPLORATORY', authorization_ref: authority.value }, selectedDataset.value, symbols.value, accountScope.value, profile)
    const reviewed = await api('/preview', { request: submitted })
    if (revision === draftRevision) preview.value = reviewed
  })
}
async function diagnose() { await action(async () => {
  const identity = preview.value?.preview_identity
  const checked = await api('/diagnose', { request: submitted, preview_identity: identity })
  if (preview.value?.preview_identity === identity) diagnosis.value = checked
}) }
async function scan() { await action(async () => {
  const identity = preview.value?.preview_identity
  const checked = await api('/scan', { request: submitted, preview_identity: identity })
  if (preview.value?.preview_identity === identity) signalScan.value = checked
}) }
async function freeze() { await action(async () => { result.value = await api('/freeze', { request: submitted, preview_identity: preview.value?.preview_identity }); taskId.value = String(result.value?.task_id ?? ''); preview.value = null }) }
async function inspectApproval() { await action(async () => { approval.value = await api('/tasks/' + taskId.value + '/approval') }) }
async function approve() { await action(async () => { result.value = await api('/approve', { task_id: taskId.value, preview_identity: approval.value?.preview_identity }); approval.value = null }) }
async function run() { await action(async () => { result.value = await api('/start', { task_id: taskId.value }) }) }
async function refresh() { await action(async () => { result.value = await api('/tasks/' + taskId.value) }) }
async function resume() { await action(async () => { result.value = await api('/resume', { task_id: taskId.value }) }) }
async function pause() { await action(async () => { result.value = await api('/pause', { task_id: taskId.value }) }) }
onMounted(() => action(async () => {
  const data = await api(''); features.value = data.capabilities.features; configured.value = data.configured; enabled.value = data.actions_allowed
  publication.value = data.publication ?? null
  longPublication.value = data.long_horizon_publication ?? null
  definition.value = JSON.stringify(data.capabilities.examples.ma_cross, null, 2)
  datasets.value = data.capabilities.data.datasets ?? []
  fullScope.value = data.capabilities.full_universe ?? null
  executionProfiles.value = data.execution_profiles ?? {}
}))
watch([dataset, definition, symbols, money, positions, authority, preheat, start, end, accountScope, longHorizon, profileChoice, customProfile], () => {
  draftRevision += 1
  preview.value = null; diagnosis.value = null; signalScan.value = null; approval.value = null; submitted = null
}, { flush: 'sync' })
</script>
<template>
<section aria-labelledby="strategy-submission-title" class="submission">
  <h2 id="strategy-submission-title">提交策略并核对研究结果</h2>
  <p>回测是按历史行情模拟账户。盈利不等于未来有效，核账通过只说明账目能够核对。</p>
  <p v-if="error" role="alert">{{ error }}</p>
  <details open><summary>系统能做什么</summary><table><thead><tr><th>功能</th><th>公共入口</th><th>验证证据</th></tr></thead><tbody>
    <tr v-for="item in features" :key="item.id"><td>{{ item.name }}</td><td>{{ item.public_entry ? '已接通' : item.engine ? '底层存在，入口未接通' : '不支持' }}</td><td>{{ publishedFor(item.id) ? '已核对发布凭证（限定范围）' : item.evidence === 'NOT_ACCEPTED' ? '尚无整链验收凭证' : item.evidence }}</td></tr>
  </tbody></table></details>
  <p v-if="fullScope">深圳主板、上海主板和创业板共用同一套 {{ fullScope.indicator_count }} 类技术指标及组合规则。数据不足时显示原因；支持指标不代表策略盈利。market_filter 是同一股票的价格和成交条件，未接入大盘指数。</p>
  <section aria-labelledby="publication-evidence-title">
    <h3 id="publication-evidence-title">发布验收证据</h3>
    <p v-if="publication?.status === 'PUBLISHED_METADATA_VERIFIED'">已核对旧版本发布凭证：3条规则、2个股票池，共{{ publication.case_count }}个案例、{{ publication.account_count }}个账户，初始资金{{ publication.initial_cash }}元。</p>
    <p v-else>尚无有效发布验收凭证；文件缺失、内容变化或源码版本不符时保持未验收。</p>
    <p>这里只核对已有发布包元数据，不读取行情或重新核账。证据仅覆盖列出的功能和测试范围，不授予策略有效性、独立验证或交易资格。</p>
    <p v-if="fullScope">全范围版本：{{ universeRunState(fullScope.engineering_evidence) }}；{{ universeRunState(fullScope.real_evidence) }}。旧版本凭证不覆盖新版本或创业板。</p>
    <details v-if="publication"><summary>查看凭证身份与未通过原因</summary><p>凭证身份：{{ publication.evidence_fingerprint ?? '无' }}</p><p v-if="publication.reason">原因：{{ publication.reason }}</p></details>
    <p v-if="longPublication?.status === 'PUBLISHED_METADATA_VERIFIED'">新长期入口已核对当前源码对应的 {{ longPublication.actual_sessions?.join('／') }} 日实测凭证，共 {{ longPublication.account_count }} 个真实账户，包括暂停恢复与连续运行对照。该证据仅覆盖所列数据、规则复杂度和资金条件。</p>
    <p v-else>新长期入口尚无与当前源码匹配的实测凭证；旧版本凭证不能替代。</p>
    <details v-if="longPublication"><summary>查看新长期凭证身份与未通过原因</summary><p>凭证身份：{{ longPublication.evidence_fingerprint ?? '无' }}</p><p v-if="longPublication.reason">原因：{{ longPublication.reason }}</p></details>
  </section>
  <p v-if="!configured">尚未登记数据目录及授权范围，请维护者完成部署登记。</p>
  <p v-if="configured && !enabled">当前为只读模式，可以查询能力和已有任务；预检、冻结及执行需维护者启用已登记的研究权限。</p>
  <form v-if="configured" @submit.prevent="prepare">
    <fieldset><legend>1. 导入明确规则</legend><label>规则文件内容（系统统一 JSON 格式，可由 AI 生成）<textarea v-model="definition" rows="8" required /></label>
      <p>规则包含实际指标参数、普通卖出条件及退出设置。成本止损与均线卖出是不同规则；收盘确认、下一交易日尝试成交，不保证止损线价格。</p>
      <section v-if="scoreSummary" aria-label="固定评分选股规则"><h4>同一天有多个机会，先买谁</h4><p>{{ scoreSummary.direction }}；{{ scoreSummary.tie }}。排序在收盘固定，次日仍须满足资金、持仓及成交限制。</p><p>评分缺失会单列阻断；已知的买入条件仍进入信号观察，不能当成没有信号。V4 评分规则需下面的长期版本。</p><details><summary>查看原始评分表达式</summary><pre>{{ scoreSummary.expression }}</pre></details></section>
    </fieldset>
    <fieldset><legend>2. 填写资金和股票范围</legend>
      <label>数据目录<select v-model="dataset" required><option value="">请选择</option><option v-for="item in datasets" :key="item.dataset_id" :value="item.dataset_id">{{ item.dataset_id }}{{ isFullUniverseDataset(item) ? ' · 全范围扫描' : ' · 指定股票' }}</option></select></label>
      <section v-if="fullUniverse" aria-label="全范围股票覆盖">
        <label>执行版本<select v-model="longHorizon"><option :value="false">沿用原全范围版本</option><option :value="true">长期连续账户、固定评分及双报告（新版本）</option></select></label>
        <template v-if="longHorizon">
          <p>新版本先检查登记全池，回测全部资料合格股票并公布排除清单。分段是计算过程换段，资金、持仓和预算连续，不会每段重新买卖。</p>
          <label>已登记交易日数及资源规格<select v-model="profileChoice"><option value="252">实际 252 个交易日</option><option value="504">实际 504 个交易日</option><option value="CUSTOM">其他长度：导入系统登记规格</option></select></label>
          <label v-if="profileChoice === 'CUSTOM'">完整资源规格 JSON（由系统生成）<textarea v-model="customProfile" rows="5" required /></label>
          <p>交易日数必须与独立交易所日历及账户起止日匹配，不能用自然日或工作日估计。每段最多 900 秒、2048 MiB，累计耗时仍受登记上限约束。</p>
          <p>预先固定 5、10、20 个交易日的信号观察，使用下一交易日原价开盘和正确的分红送转权益；尾部不够、停牌或不能买的机会会明确标出，不补零或跳到以后再买。</p>
        </template>
        <label v-else>账户数据范围<select v-model="accountScope"><option value="FULL_REQUIRED">严格全池：全部股票资料齐全才回测</option><option value="DATA_QUALIFIED">检查全池：回测全部合格股票并公布排除清单</option></select></label>
        <p>扫描登记清单全部 {{ selectedCoverage.target }} 只，再由策略信号选股。这里无需手填股票代码，所有板块共用下面的一份资金。</p>
        <p>{{ selectedCoverage.completeness }}；{{ selectedCoverage.account }}。找到缓存文件不代表该股票可完整回测。</p>
        <table><thead><tr><th>板块</th><th>目标股票</th><th>找到缓存</th><th>账户数据合格</th><th>板块身份未知</th></tr></thead><tbody>
          <tr v-for="board in selectedCoverage.boards" :key="board.id"><td>{{ board.name }}</td><td>{{ board.target }}</td><td>{{ board.cached }}</td><td>{{ board.qualified }}</td><td>{{ board.identityUnknown }}</td></tr>
        </tbody></table>
      </section>
      <label v-else>股票代码（逗号分隔）<input v-model="symbols" required placeholder="000001.SZ,600000.SH" /></label>
      <label>初始资金（元）<input v-model.number="money" type="number" min="1" required /></label><label>最多同时持有几只<input v-model.number="positions" type="number" min="1" required /></label>
      <label>指标预热起日<input v-model="preheat" type="date" required /></label><label>账户评价起日<input v-model="start" type="date" required /></label><label>账户评价结束日<input v-model="end" type="date" required /></label>
      <p>账户交易日是整个模拟账户推进的日期，包含空仓日，不是强制持有股票的天数。持有多久仍由买卖信号、止盈止损和规则的持有边界决定。</p>
      <label>已登记授权编号<input v-model="authority" required /></label><p>股票池是可选择范围，持仓数是同时持有上限；正常成本和压力成本分别记账。全范围另报现金基准与不可投资的等权价格对照，不能把它当作可买入的账户收益。</p>
    </fieldset><button type="submit" :disabled="busy || !enabled">3. 查看实际规则和数据条件</button>
  </form>
  <section v-if="preview"><h3>3. 核对并冻结</h3><p>冻结保存本次规则和输入，修改需新任务；不会自行授予执行权限。</p><pre>{{ JSON.stringify(preview, null, 2) }}</pre>
    <template v-if="fullUniverse">
      <button :disabled="busy || !enabled" @click="diagnose">核对全范围数据（不运行账户）</button>
      <button v-if="accountScope === 'FULL_REQUIRED' && !longHorizon" :disabled="busy || !enabled" @click="scan">检查全部股票信号（不运行账户）</button>
      <p>信号检查先固定规则与全部范围，在已有数据权限内逐股核对。缺证据的股票保持未知；原始条件不是实际买卖，也不计算止损成交、收益或策略资格。</p>
      <p v-if="diagnosis">{{ universeRunState(diagnosis.status) }}。目标 {{ actualCoverage.target }} 只；{{ actualCoverage.account }}。这一步尚无账户收益。</p>
      <ul v-if="diagnosis && actualCoverage.gaps.length"><li v-for="gap in actualCoverage.gaps.slice(0, 10)" :key="gap">{{ gap }}</li></ul>
      <details v-if="diagnosis"><summary>查看全部数据缺口与诊断身份</summary><pre>{{ JSON.stringify(diagnosis, null, 2) }}</pre></details>
      <section v-if="signalScan" aria-label="全范围信号检查结果">
        <h4>{{ universeRunState(signalScan.status) }}</h4>
        <p>登记 {{ scanSummary.target }} 只，完成资格检查 {{ scanSummary.processed }} 只；实际计算条件 {{ scanSummary.evaluated }} 只、{{ scanSummary.sessions }} 个股票交易日；含未知日期 {{ scanSummary.unknown }} 只。</p>
        <p v-if="!scanSummary.anyEvaluated">本次没有可判断的规则条件，不能称为“零买入信号”。未运行账户，尚无收益结果。</p>
        <p v-else>下面是条件命中次数，包含空仓卖出条件；不等于成交或退出次数。仍未运行账户，尚无收益结果。</p>
        <table><thead><tr><th>股票</th><th>条件状态</th><th>计算日期</th><th>未知日期</th><th>买入条件命中</th><th>卖出条件命中</th></tr></thead><tbody>
          <tr v-for="row in scanSummary.rows.slice(0, 10)" :key="row.symbol"><td>{{ row.symbol }}</td><td>{{ row.status }}</td><td>{{ row.evaluated }}</td><td>{{ row.unknown }}</td><td>{{ row.buy }}</td><td>{{ row.sell }}</td></tr>
        </tbody></table>
        <details><summary>查看全部股票条件、缺口和固定身份</summary><pre>{{ JSON.stringify(signalScan, null, 2) }}</pre></details>
      </section>
    </template>
    <button :disabled="busy || !enabled || (fullUniverse && !diagnosticReady)" @click="freeze">冻结这份预览</button>
  </section>
  <section v-if="qualificationScope" aria-label="全池资格与排除清单">
    <h3>全池检查、合格范围和排除清单</h3>
    <p>检查 {{ qualificationScope.target_symbols.length }} 只；逐股初检通过 {{ qualificationScope.qualified_symbols.length }} 只；可执行合格 {{ qualificationScope.blocking_global_gaps.length ? 0 : qualificationScope.qualified_symbols.length }} 只；逐股排除 {{ qualificationScope.excluded.length }} 只。资料资格先于收益确定，合格不表示已经买入或策略有效。</p>
    <p v-if="qualificationScope.blocking_global_gaps.length">共同缺口仍影响整池：{{ qualificationScope.blocking_global_gaps.join('；') }}</p>
    <p>范围按整个评价区间资料可用性回顾确定，不能当作已证明当年可投资的完整市场。全池仍可补齐后重新检查，旧记录保留。</p>
    <details><summary>查看全部排除股票与原因</summary><table><thead><tr><th>股票</th><th>排除原因</th></tr></thead><tbody><tr v-for="row in qualificationScope.excluded" :key="row.symbol"><td>{{ row.symbol }}</td><td>{{ row.reasons.map(reason => displayReason(reason).label).join('；') }}</td></tr></tbody></table></details>
    <details><summary>查看完整登记、合格名单和检查证据</summary><pre>{{ JSON.stringify(qualificationScope, null, 2) }}</pre></details>
  </section>
  <section v-if="approval"><h3>批准已冻结计划</h3><p>下列规则、资金、日期与计划身份必须在已有授权范围内。批准不会授予策略有效性资格。</p><pre>{{ JSON.stringify(approval, null, 2) }}</pre><button :disabled="busy || !enabled" @click="approve">批准这份固定计划</button></section>
  <section><h3>4. 查看任务</h3><label>任务编号<input v-model="taskId" /></label><button :disabled="busy || !taskId" @click="refresh">查询进度</button><button :disabled="busy || !taskId || !enabled" @click="inspectApproval">核对批准范围</button><button :disabled="busy || !taskId || !enabled" @click="run">启动已授权任务</button>
    <template v-if="canPause"><button :disabled="busy || !enabled" @click="pause">在安全边界暂停原任务</button><p>账户暂停在完成收盘或正常换段后生效；核账或报告暂停在下个工作段边界或阶段完成后生效。账户和累计资源保留，绝对授权到期时间仍继续推进。</p></template>
    <template v-if="canResume"><button :disabled="busy || !enabled" @click="resume">核对并恢复原全范围账户任务</button><p>仅处理这个新版本未结算任务；后端会核对进程已退出、原输入、逐日记录、授权和剩余时间。仍在运行或证据不足时拒绝，不增加预算，也不自动恢复。</p></template>
    <template v-if="result"><p>{{ universeTaskState(result) }}</p><p v-if="result.coverage">目标 {{ actualCoverage.target }} 只；{{ actualCoverage.account }}。收益与核账证据须等真实账户结果。</p>
      <section v-if="longProgress" aria-label="长期账户进度与资源">
        <h4>连续账户推进</h4>
        <p>当前阶段：{{ longProgress.stage }}。登记交易日数 {{ longProgress.sessionBound }}；每段上限 {{ longProgress.workerSeconds }} 秒，内存 {{ longProgress.memoryMib }} MiB。换段不清零总消耗，也不强制卖出。</p>
        <table><thead><tr><th>用途</th><th>状态与阶段</th><th>完成交易日</th><th>最后收盘</th><th>累计耗时（秒）</th><th>剩余上限（秒）</th></tr></thead><tbody><tr v-for="row in longProgress.rows" :key="row.name"><td>{{ row.name }}</td><td>{{ row.state }}；{{ row.phase }}</td><td>{{ row.processed }} / {{ row.sessions }}</td><td>{{ row.lastDay }}</td><td>{{ row.chargedSeconds }}</td><td>{{ row.remainingSeconds }}</td></tr></tbody></table>
        <table v-if="longProgress.stages.length"><thead><tr><th>后续处理阶段</th><th>状态</th><th>累计耗时（秒）</th><th>剩余上限（秒）</th></tr></thead><tbody><tr v-for="row in longProgress.stages" :key="row.name"><td>{{ row.name }}</td><td>{{ row.state }}</td><td>{{ row.chargedSeconds }}</td><td>{{ row.remainingSeconds }}</td></tr></tbody></table>
        <p>完成后分别查看实际账户报告、所有条件机会的信号报告和逐项未成交原因；这些仍不授予正式有效性或 Paper 资格。</p>
      </section>
      <ul v-if="actualCoverage.gaps.length"><li v-for="gap in actualCoverage.gaps.slice(0, 10)" :key="gap">{{ gap }}</li></ul>
      <details><summary>查看完整范围、缺口与原服务证据</summary><pre>{{ JSON.stringify(result, null, 2) }}</pre></details>
    </template>
  </section>
</section>
</template>
<style scoped>
.submission{display:grid;gap:16px}fieldset{display:grid;gap:14px;margin:16px 0;padding:20px;border:1px solid #ccd6e1}label{display:grid;gap:7px}input,select,textarea,button{padding:9px;border:1px solid #ccd6e1;border-radius:5px}button{margin-right:10px;cursor:pointer}button:disabled{opacity:.5}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #dde4ec}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}p{line-height:1.7}[role=alert]{color:#a52430}
</style>
