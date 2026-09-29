<script setup lang="ts">
import { onMounted, ref } from 'vue'
interface Publication { status: string; feature_ids: string[]; case_count?: number; account_count?: number; initial_cash?: number; reason?: string; evidence_fingerprint?: string }
const publication = ref<Publication | null>(null)
const publishedFor = (id: string) => publication.value?.status === 'PUBLISHED_METADATA_VERIFIED' && publication.value.feature_ids.includes(id)
interface Feature { id: string; name: string; public_entry: boolean; engine: boolean; evidence: string }
const features = ref<Feature[]>([]), datasets = ref<string[]>([])
const enabled = ref(false), configured = ref(false), busy = ref(false), error = ref('')
const definition = ref(''), dataset = ref(''), symbols = ref(''), money = ref(50000), positions = ref(2), authority = ref('')
const preheat = ref(''), start = ref(''), end = ref(''), taskId = ref('')
const approval = ref<Record<string, unknown> | null>(null)
const preview = ref<Record<string, unknown> | null>(null), result = ref<Record<string, unknown> | null>(null)
let submitted: Record<string, unknown> | null = null
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
    submitted = { strategy_id: 'submitted_strategy', rule: JSON.parse(definition.value), dataset_id: dataset.value, symbols: symbols.value.split(/[，,\s]+/).filter(Boolean), initial_cash: money.value,
      feature_start: preheat.value, account_start: start.value, account_end: end.value, max_positions: positions.value, max_symbol_exposure_bps: Math.floor(10000 / positions.value),
      costs: ['BASE', 'STRESS'], benchmark: 'FULL_POOL_BUY_HOLD', purpose: 'EXPLORATORY', authorization_ref: authority.value }
    preview.value = await api('/preview', { request: submitted })
  })
}
async function freeze() { await action(async () => { result.value = await api('/freeze', { request: submitted, preview_identity: preview.value?.preview_identity }); taskId.value = String(result.value?.task_id ?? ''); preview.value = null }) }
async function inspectApproval() { await action(async () => { approval.value = await api('/tasks/' + taskId.value + '/approval') }) }
async function approve() { await action(async () => { result.value = await api('/approve', { task_id: taskId.value, preview_identity: approval.value?.preview_identity }); approval.value = null }) }
async function run() { await action(async () => { result.value = await api('/start', { task_id: taskId.value }) }) }
async function refresh() { await action(async () => { result.value = await api('/tasks/' + taskId.value) }) }
onMounted(() => action(async () => {
  const data = await api(''); features.value = data.capabilities.features; configured.value = data.configured; enabled.value = data.actions_allowed
  publication.value = data.publication ?? null
  definition.value = JSON.stringify(data.capabilities.examples.ma_cross, null, 2)
  datasets.value = (data.capabilities.data.datasets ?? []).map((item: { dataset_id: string }) => item.dataset_id)
}))
</script>
<template>
<section aria-labelledby="strategy-submission-title" class="submission">
  <h2 id="strategy-submission-title">提交策略并核对研究结果</h2>
  <p>回测是按历史行情模拟账户。盈利不等于未来有效，核账通过只说明账目能够核对。</p>
  <p v-if="error" role="alert">{{ error }}</p>
  <details open><summary>系统能做什么</summary><table><thead><tr><th>功能</th><th>公共入口</th><th>验证证据</th></tr></thead><tbody>
    <tr v-for="item in features" :key="item.id"><td>{{ item.name }}</td><td>{{ item.public_entry ? '已接通' : item.engine ? '底层存在，入口未接通' : '不支持' }}</td><td>{{ publishedFor(item.id) ? '已核对发布凭证（限定范围）' : item.evidence === 'NOT_ACCEPTED' ? '尚无整链验收凭证' : item.evidence }}</td></tr>
  </tbody></table></details>
  <section aria-labelledby="publication-evidence-title">
    <h3 id="publication-evidence-title">发布验收证据</h3>
    <p v-if="publication?.status === 'PUBLISHED_METADATA_VERIFIED'">已核对发布凭证：3条规则、2个股票池，共{{ publication.case_count }}个案例、{{ publication.account_count }}个账户，初始资金{{ publication.initial_cash }}元。</p>
    <p v-else>尚无有效发布验收凭证；文件缺失、内容变化或源码版本不符时保持未验收。</p>
    <p>这里只核对已有发布包元数据，不读取行情或重新核账。证据仅覆盖列出的功能和测试范围，不授予策略有效性、独立验证或交易资格。</p>
    <details v-if="publication"><summary>查看凭证身份与未通过原因</summary><p>凭证身份：{{ publication.evidence_fingerprint ?? '无' }}</p><p v-if="publication.reason">原因：{{ publication.reason }}</p></details>
  </section>
  <p v-if="!configured">尚未登记数据目录及授权范围，请维护者完成部署登记。</p>
  <p v-if="configured && !enabled">当前为只读模式，可以查询能力和已有任务；预检、冻结及执行需维护者启用已登记的研究权限。</p>
  <form v-if="configured" @submit.prevent="prepare">
    <fieldset><legend>1. 导入明确规则</legend><label>规则文件内容（系统统一 JSON 格式，可由 AI 生成）<textarea v-model="definition" rows="8" required /></label>
      <p>规则包含实际指标参数、普通卖出条件及退出设置。成本止损与均线卖出是不同规则；收盘确认、下一交易日尝试成交，不保证止损线价格。</p></fieldset>
    <fieldset><legend>2. 填写资金和股票范围</legend>
      <label>数据目录<select v-model="dataset" required><option value="">请选择</option><option v-for="item in datasets" :key="item">{{ item }}</option></select></label>
      <label>股票代码（逗号分隔）<input v-model="symbols" required placeholder="000001.SZ,600000.SH" /></label>
      <label>初始资金（元）<input v-model.number="money" type="number" min="1" required /></label><label>最多同时持有几只<input v-model.number="positions" type="number" min="1" required /></label>
      <label>指标预热起日<input v-model="preheat" type="date" required /></label><label>账户评价起日<input v-model="start" type="date" required /></label><label>账户评价结束日<input v-model="end" type="date" required /></label>
      <label>已登记授权编号<input v-model="authority" required /></label><p>股票池是可选择范围，持仓数是同时持有上限；正常成本、压力成本及全池买入持有基准分别记账。</p>
    </fieldset><button type="submit" :disabled="busy || !enabled">3. 查看实际规则和数据条件</button>
  </form>
  <section v-if="preview"><h3>3. 核对并冻结</h3><p>冻结保存本次规则和输入，修改需新任务；不会自行授予执行权限。</p><pre>{{ JSON.stringify(preview, null, 2) }}</pre><button :disabled="busy || !enabled" @click="freeze">冻结这份预览</button></section>
  <section v-if="approval"><h3>批准已冻结计划</h3><p>下列规则、资金、日期与计划身份必须在已有授权范围内。批准不会授予策略有效性资格。</p><pre>{{ JSON.stringify(approval, null, 2) }}</pre><button :disabled="busy || !enabled" @click="approve">批准这份固定计划</button></section>
  <section><h3>4. 查看任务</h3><label>任务编号<input v-model="taskId" /></label><button :disabled="busy || !taskId" @click="refresh">查询进度</button><button :disabled="busy || !taskId || !enabled" @click="inspectApproval">核对批准范围</button><button :disabled="busy || !taskId || !enabled" @click="run">启动已授权任务</button><pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre></section>
</section>
</template>
<style scoped>
.submission{display:grid;gap:16px}fieldset{display:grid;gap:14px;margin:16px 0;padding:20px;border:1px solid #ccd6e1}label{display:grid;gap:7px}input,select,textarea,button{padding:9px;border:1px solid #ccd6e1;border-radius:5px}button{margin-right:10px;cursor:pointer}button:disabled{opacity:.5}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #dde4ec}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}p{line-height:1.7}[role=alert]{color:#a52430}
</style>
