<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import StrategySubmission from './StrategySubmission.vue'
import { canCreateBinding, canOperate, canOperateContinuous, continuousResourceRows, dataSummary, lifecycleRequest, lifecycleState, objectStatus, observationDays, qualificationText, sourceText } from '../lifecycle'
import type { LifecycleRecord, LifecycleView } from '../lifecycle'
import { universeCoverage, universeRunState } from '../universe'

const view = ref<(LifecycleView & { host?: { heartbeat_at?: string | null } }) | null>(null)
const error = ref('')
const busy = ref(false)
const preview = ref<LifecycleRecord | null>(null)
const pendingAction = ref('')
const pendingPayload = ref<LifecycleRecord>({})
const ownerReferences = ref<Record<string, string>>({})
const handover = ref<LifecycleRecord | null>(null)
const jobId = ref('')
const bindingId = ref('')
const startAt = ref('')
const endAt = ref('')
const tradeDate = ref('')
const count = ref(1)
const available = computed(() => Object.entries(view.value?.binding_catalog ?? {}).filter(([id]) =>
  view.value !== null && canCreateBinding(view.value, id)))
const actionNames: Record<string, string> = { create: '创建有限任务', start: '启动任务', pause: '暂停任务', resume: '恢复任务', tick: '推进一个阶段',
  'continuous:create': '登记持续研究', 'continuous:start': '启动持续研究', 'continuous:advance': '推进一个有界步骤',
  'continuous:pause': '暂停持续研究', 'continuous:resume': '恢复持续研究', 'continuous:revoke': '撤销后续研究' }
async function refresh() {
  busy.value = true; error.value = ''
  try { view.value = await lifecycleRequest() as unknown as LifecycleView }
  catch (failure) { error.value = String(failure) }
  finally { busy.value = false }
}
async function prepare(action: string, payload: LifecycleRecord) {
  busy.value = true; error.value = ''; preview.value = null
  try {
    preview.value = await lifecycleRequest('/preview', { action, payload })
    pendingAction.value = action; pendingPayload.value = payload
  } catch (failure) { error.value = String(failure) }
  finally { busy.value = false }
}
async function create() {
  const start = new Date(startAt.value), end = new Date(endAt.value)
  if (!jobId.value || !bindingId.value || !Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start) {
    error.value = '请填写任务名称、业务对象，以及先后有效的运行时段。'; return
  }
  const day = tradeDate.value ? Number(tradeDate.value.replace(/-/g, '')) : null
  await prepare('create', { job_id: jobId.value, binding_id: bindingId.value,
    expires_at: end.toISOString(), max_calls: count.value, trading_calendar: day === null ? [] : [day],
    stages: [{ key: 'STAGE_1', trade_date: day, not_before: start.toISOString(), not_after: end.toISOString() }] })
}
async function confirm() {
  if (!preview.value) return
  busy.value = true; error.value = ''
  try {
    if (pendingAction.value.startsWith('continuous:')) {
      await lifecycleRequest('/continuous/action', pendingPayload.value)
    } else {
      await lifecycleRequest('/action', { action: pendingAction.value, payload: pendingPayload.value,
        preview_hash: preview.value.preview_hash, confirmed: true })
    }
    preview.value = null; await refresh()
  } catch (failure) { error.value = String(failure); preview.value = null }
  finally { busy.value = false }
}
async function prepareContinuous(action: string, researchId: string) {
  busy.value = true; error.value = ''; preview.value = null
  try {
    const payload: LifecycleRecord = {}
    if (action === 'create') {
      const reference = JSON.parse(ownerReferences.value[researchId] ?? '') as LifecycleRecord
      payload.approval_ref = reference.approval_ref ?? reference
    }
    if (['pause', 'resume', 'revoke'].includes(action)) payload.reason = `用户从工作台请求${actionNames[`continuous:${action}`]}`
    preview.value = await lifecycleRequest(`/continuous/${encodeURIComponent(researchId)}${action === 'create' ? '/preview' : ''}`)
    pendingAction.value = `continuous:${action}`
    pendingPayload.value = { research_id: researchId, action, payload }
  } catch (failure) { error.value = String(failure) }
  finally { busy.value = false }
}
async function readHandover(researchId: string) {
  busy.value = true; error.value = ''
  try { handover.value = await lifecycleRequest(`/continuous/${encodeURIComponent(researchId)}/handover`) }
  catch (failure) { error.value = String(failure) }
  finally { busy.value = false }
}
function intents(record: LifecycleRecord): LifecycleRecord[] {
  const plan = record.next_plan as { intents?: LifecycleRecord[] } | null
  return Array.isArray(plan?.intents) ? plan.intents : []
}
onMounted(refresh)
</script>

<template>
  <StrategySubmission />
  <section class="lifecycle-workbench" aria-labelledby="lifecycle-title">
    <header><div><p class="eyebrow">研究 · 验证 · 观察 · 每日计划</p><h2 id="lifecycle-title">自主研究进展</h2>
      <p>查看每个阶段的真实证据和等待原因。阶段执行完成，不等于策略已被证明有效。</p></div>
      <button type="button" :disabled="busy" @click="refresh">{{ busy ? '正在读取…' : '刷新状态' }}</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="!view && !busy">尚未装配生命周期工作区。请使用显式部署配置启动控制台。</p>
    <template v-if="view">
      <div class="evidence-grid">
        <article><h3>工程能力</h3><p>查看入口与验收范围</p><small>旧版本通过不能代替新全范围或创业板验收。</small></article>
        <article><h3>测试验证</h3><p>查看交付测试报告</p><small>页面加载成功不能替代测试通过。</small></article>
        <article><h3>真实观察</h3><p>按各账户分别记录</p><small>不合计独立账户，不把合成天数算入。</small></article>
        <article><h3>策略有效性</h3><p>只认原资格服务</p><small>任务完成或回测盈利均不自动授予资格。</small></article>
      </div>
      <p class="notice">{{ view.actions_allowed ? '仅可操作维护者已登记且当前授权允许的对象；每次执行仍会重新核验。' : '当前为只读模式。刷新不会启动研究、采集或交易。' }}</p>
      <p class="notice" aria-label="研究服务心跳">研究服务最近心跳：<time v-if="view.host?.heartbeat_at" :datetime="view.host.heartbeat_at">{{ view.host.heartbeat_at }}</time><span v-else>暂无心跳</span>。实际研究进度请以候选、任务阶段和执行回执为准。</p>
      <section v-if="Object.keys(view.continuous ?? {}).length" aria-label="持续全范围研究">
        <h3>持续全范围研究</h3><p>按批准的总范围与分阶段储备推进，每次只执行一个有界步骤。资源用完会等待追加批准；短轮次通过和账户完成均不等于最终目标达成。</p>
        <article v-for="(record, id) in view.continuous" :key="id" class="object-card">
          <h4>{{ id }} <span>{{ lifecycleState(record.status) }}</span></h4>
          <p v-if="record.reason || record.stop_reason || record.waiting_reason">等待或停因：{{ record.reason ?? record.stop_reason ?? record.waiting_reason }}</p>
          <p v-if="record.progress">已完成候选尝试 {{ (record.progress as LifecycleRecord).completed_attempts }} / {{ (record.progress as LifecycleRecord).authorized_attempts }}；最终目标{{ record.goal_complete === true ? '证据已满足' : '尚未完成' }}。</p>
          <div v-if="record.channels"><p v-for="(channel, phase) in (record.channels as Record<string, LifecycleRecord>)" :key="phase">{{ phase === 'EXPLORATION' ? '探索' : phase === 'FINAL_EXPLORATION' ? '最终探索复核' : phase === 'CONFIRMATION' ? '独立确认' : phase }}：{{ lifecycleState(channel.status) }}<span v-if="channel.waiting_reason"> · {{ channel.waiting_reason }}</span></p></div>
          <p v-if="record.model_readiness">模型：{{ lifecycleState(record.model_readiness) }}。已冻结账户仍按原授权与额度核验。</p>
          <table v-if="continuousResourceRows(record).length"><caption>总研究额度，未知消费保留预留</caption>
            <thead><tr><th>资源</th><th>已使用</th><th>已预留</th><th>剩余</th></tr></thead><tbody>
              <tr v-for="row in continuousResourceRows(record)" :key="row.name"><td>{{ row.name }}</td><td>{{ row.used }}</td><td>{{ row.reserved }}</td><td>{{ row.remaining }}</td></tr>
            </tbody></table>
          <details v-if="record.scope_budget"><summary>核对探索与确认阶段剩余额度</summary><pre>{{ JSON.stringify((record.scope_budget as LifecycleRecord).stage_remaining, null, 2) }}</pre></details>
          <div v-if="canOperateContinuous(view, String(id))" class="actions">
            <template v-if="record.status === 'OWNER_APPROVAL_REQUIRED'"><label>维护者已批准引用 JSON<input v-model="ownerReferences[String(id)]" placeholder='{"approval_id":"…","summary_hash":"…"}' /></label>
              <button :disabled="busy || !ownerReferences[String(id)]" @click="prepareContinuous('create', String(id))">核对总授权并登记</button></template>
            <template v-else><button v-for="action in ['start', 'advance', 'pause', 'resume', 'revoke']" :key="action" :disabled="busy" @click="prepareContinuous(action, String(id))">{{ actionNames[`continuous:${action}`] }}</button></template>
          </div>
          <button v-if="record.status !== 'OWNER_APPROVAL_REQUIRED'" :disabled="busy" @click="readHandover(String(id))">读取接手包</button>
          <details><summary>核对合同、候选与原始证据</summary><pre>{{ JSON.stringify(record, null, 2) }}</pre></details>
        </article>
        <details v-if="handover" open><summary>接手包（只读）</summary><pre>{{ JSON.stringify(handover, null, 2) }}</pre></details>
      </section>
      <h3>业务对象</h3>
      <p v-if="!Object.keys(view.bindings).length">尚未配置研究或观察对象。</p>
      <article v-for="(record, id) in view.bindings" :key="id" class="object-card">
        <h4>{{ id }} <span>{{ objectStatus(record) }}</span></h4>
        <p v-if="dataSummary(record)">{{ dataSummary(record) }}</p>
        <section v-if="record.coverage" aria-label="全范围研究覆盖">
          <p>全范围目标 {{ universeCoverage(record).target }} 只；{{ universeCoverage(record).completeness }}。{{ universeCoverage(record).account }}。</p>
          <table><thead><tr><th>板块</th><th>目标</th><th>缓存</th><th>账户合格</th></tr></thead><tbody>
            <tr v-for="board in universeCoverage(record).boards" :key="board.id"><td>{{ board.name }}</td><td>{{ board.target }}</td><td>{{ board.cached }}</td><td>{{ board.qualified }}</td></tr>
          </tbody></table>
          <p>{{ universeRunState(record.status) }}。完成扫描、完成账目核对和取得策略资格分别记录。</p>
        </section>
        <dl><div><dt>来源</dt><dd>{{ sourceText(record) }}</dd></div><div><dt>真实观察</dt><dd>{{ observationDays(record) }}</dd></div>
          <div><dt>资格</dt><dd>{{ qualificationText(record) }}</dd></div></dl>
        <p v-if="record.reason">停因或等待：{{ record.reason }}</p>
        <p v-if="Array.isArray(record.reason_codes) && record.reason_codes.length">{{ record.reason_codes.join('；') }}</p>
        <div v-if="record.next_plan"><h5>每日计划</h5><table v-if="intents(record).length"><thead><tr><th>策略</th><th>证券</th><th>意图</th><th>原因</th></tr></thead>
          <tbody><tr v-for="(item, index) in intents(record)" :key="index"><td>{{ item.strategy_id }}</td><td>{{ item.symbol }}</td><td>{{ item.side === 'BUY' ? '买入' : item.side === 'SELL' ? '卖出' : item.side }}</td><td>{{ item.reason }}</td></tr></tbody></table>
          <p v-else>原账户尚无新交易意图；请同时核对上述数据与资格状态。</p></div>
        <details><summary>核对原服务证据</summary><pre>{{ JSON.stringify(record, null, 2) }}</pre></details>
      </article>
      <h3>有限任务</h3><p v-if="!Object.keys(view.jobs).length">尚未创建任务；系统不会自行启动。</p>
      <article v-for="(job, id) in view.jobs" :key="id" class="object-card">
        <h4>{{ id }} <span>{{ lifecycleState(job.status) }}</span></h4>
        <p>已开始 {{ job.calls_started ?? '—' }} / {{ job.max_calls ?? '—' }} 次；原研究预算仍单独约束。</p>
        <p v-if="job.reason">原因：{{ job.reason }}</p><p v-if="job.next_check_at">下一检查时间：{{ job.next_check_at }}</p>
        <div v-if="canOperate(view, job, String(id))" class="actions"><button v-for="action in ['start', 'pause', 'resume', 'tick']" :key="action" :disabled="busy" @click="prepare(action, { job_id: id })">{{ actionNames[action] }}</button></div>
        <details><summary>查看阶段记录与来源</summary><pre>{{ JSON.stringify(job, null, 2) }}</pre></details>
      </article>
      <details v-if="available.length"><summary>创建一个有限任务</summary><form @submit.prevent="create" class="create-form">
        <label>任务名称（英文字母、数字或下划线）<input v-model="jobId" required pattern="[A-Za-z0-9_-]{1,120}" /></label>
        <label>已有业务对象<select v-model="bindingId" required><option value="">请选择</option><option v-for="([id, item]) in available" :key="id" :value="id">{{ id }} · {{ item.kind }}</option></select></label>
        <label>开始时间<input v-model="startAt" type="datetime-local" required /></label><label>结束时间与任务到期<input v-model="endAt" type="datetime-local" required /></label>
        <label>对应交易日（采集和 Paper 必填）<input v-model="tradeDate" type="date" /></label><label>调用次数上限<input v-model.number="count" type="number" min="1" max="10000" required /></label>
        <p>此表创建一个阶段；Paper 的已冻结快照映射需使用阶段名称 STAGE_1。不会新建研究授权。</p><button type="submit" :disabled="busy">预览任务</button>
      </form></details>
      <section v-if="preview" class="confirm-panel" role="dialog" aria-label="确认生命周期操作"><h3>确认{{ actionNames[pendingAction] }}</h3>
        <p>对象：{{ pendingPayload.research_id ?? pendingPayload.job_id }}。提交时会重新核对当前授权与状态；按钮不能签发 Owner 批准。</p>
        <pre>{{ JSON.stringify(pendingPayload, null, 2) }}</pre><details v-if="pendingAction.startsWith('continuous:')"><summary>本次预览证据</summary><pre>{{ JSON.stringify(preview, null, 2) }}</pre></details><button :disabled="busy" @click="confirm">确认执行</button><button :disabled="busy" @click="preview = null">取消</button></section>
    </template>
  </section>
</template>

<style scoped>
.lifecycle-workbench{display:grid;gap:20px;color:#26364a}.lifecycle-workbench header{display:flex;justify-content:space-between;gap:20px;align-items:start}.eyebrow{color:#526c88;font-size:12px;letter-spacing:.08em}h2{font-size:26px;margin:5px 0 10px}p{line-height:1.7}button{border:1px solid #ccd6e1;background:white;color:#243b55;padding:8px 14px;border-radius:6px;cursor:pointer}button:disabled{opacity:.5;cursor:wait}.evidence-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.evidence-grid article,.object-card{background:white;border:1px solid #dde4ec;border-radius:10px;padding:20px}.evidence-grid h3{font-size:13px;color:#60758c}.evidence-grid p{font-weight:600;margin:9px 0}.evidence-grid small{color:#60758c}h4{display:flex;gap:16px;justify-content:space-between}h4 span{font-size:13px;color:#526c88;font-weight:400}dl{display:flex;gap:32px;flex-wrap:wrap;margin:16px 0}dt{font-size:12px;color:#718096}dd{margin:5px 0}details{margin-top:14px}summary{cursor:pointer;color:#45637f}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f5f7fa;padding:14px;margin:10px 0;max-height:360px;overflow:auto}.notice{padding:14px;border-left:3px solid #7a94b0;background:#edf2f7}.error{color:#a52430;background:#fff1f1;padding:14px}.actions{display:flex;gap:8px;margin-top:12px}.create-form{display:grid;gap:12px;max-width:640px;margin-top:15px}.create-form label{display:grid;gap:6px}input,select{padding:9px;border:1px solid #ccd6e1;border-radius:5px}.confirm-panel{border:2px solid #7a94b0;background:#fff;padding:20px;border-radius:8px}.confirm-panel button{margin-right:10px}table{width:100%;border-collapse:collapse;margin-top:10px}th,td{text-align:left;border-bottom:1px solid #e2e7ef;padding:10px;font-size:13px}@media(max-width:850px){.evidence-grid{grid-template-columns:repeat(2,1fr)}header,h4{flex-wrap:wrap}}@media(max-width:500px){.evidence-grid{grid-template-columns:1fr}.actions{flex-wrap:wrap}}
</style>
