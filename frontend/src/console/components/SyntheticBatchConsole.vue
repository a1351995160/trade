<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Member = { objective_id: string; candidate_id: string; contract_hash: string; novelty_confirmation: string }
type Option = { member: Member; status: string; reason?: string; sources: string[] }
type History = { batch_authorization_id: string; status: string; candidate_count?: number; completed_actions?: number; reason?: string }
type Context = { workspace: string; actions_allowed: boolean; candidates: Option[]; batches: History[]; issues: string[]; comparison_sources?: string[] }
type Limits = { candidates: number; trials: number; batches: number; wall_seconds: number; memory_mib: number; concurrency: number; retries: number; model_calls: number; tokens: number; cost_minor_units: number; currency: string }
type Preview = { batch_authorization_id: string; hash: string; request: { candidates: Member[]; actions: string[]; limits: Limits; effective_at: string; expires_at: string }; bindings: unknown[]; control_rules: Record<string, string> }
type State = { active_execution: { action: string } | null; completed_actions: unknown[]; failed_actions: unknown[] }
type Batch = { preview: Preview; status: string; execution_authorized: boolean; state: State | null }

const endpoint = '/api/research-engineering/batches'
const context = ref<Context | null>(null), batch = ref<Batch | null>(null)
const selected = ref<string[]>([]), identifier = ref(''), consent = ref(false)
const loading = ref(false), submitting = ref(false), running = ref(false), controlling = ref(false)
const error = ref(''), notice = ref('')
const actions = ref(['RUN_STRUCTURAL_PREFLIGHT', 'START_PREDICTIVE_TRIAL_1'])
const localTime = (date: Date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
const effective = ref(localTime(new Date())), expiry = ref(localTime(new Date(Date.now() + 3600000)))
const limits = ref<Limits>({ candidates: 1, trials: 1, batches: 1, wall_seconds: 120, memory_mib: 2048,
  concurrency: 1, retries: 0, model_calls: 0, tokens: 0, cost_minor_units: 0, currency: 'CNY' })
const fields = [['candidates', '候选上限'], ['trials', 'Trial 上限'], ['batches', '原批次数上限'],
  ['wall_seconds', '总墙钟时间（秒）'], ['memory_mib', 'Worker 内存（MiB）']] as const
const actionOptions = [['RUN_STRUCTURAL_PREFLIGHT', '结构检查'], ['START_PREDICTIVE_TRIAL_1', '首个预测 Trial 及收尾']] as const
const labels: Record<string, string> = { REQUESTED: '等待人工确认', ACTIVE: '批准有效', PAUSED: '已暂停',
  STOPPED: '已停止', REVOKED: '已撤销', EXPIRED: '已到期', NOT_YET_EFFECTIVE: '尚未生效',
  TIME_LIMIT_REACHED: '已达时间上限', BLOCKED: '已阻断', COMPLETED: '清单动作已完成' }
const status = (value: string) => labels[value] || value
const picked = computed(() => context.value?.candidates.filter(item => item.status === 'READY' && selected.value.includes(item.member.candidate_id)).map(item => item.member) || [])
const editable = computed(() => !!context.value?.actions_allowed && !submitting.value && !running.value)
const canControl = computed(() => context.value?.actions_allowed && !!batch.value && !controlling.value)
const terminal = new Set(['COMPLETED', 'BLOCKED', 'STOPPED', 'REVOKED', 'EXPIRED', 'TIME_LIMIT_REACHED'])
let alive = true, readRevision = 0, timer: ReturnType<typeof setInterval> | undefined
let readsInFlight = 0

async function request(path: string, body?: unknown) {
  const response = await fetch(path, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.reason || result.detail?.code || result.message_zh || result.code || `HTTP ${response.status}`)
  return result
}

watch([selected, actions, effective, expiry, limits], () => {
  consent.value = false
  if (batch.value?.status === 'REQUESTED') { readRevision++; batch.value = null; identifier.value = '' }
}, { deep: true })

async function loadContext(clearError = true) {
  loading.value = true; if (clearError) error.value = ''
  try { const result = await request(`${endpoint}/context`); if (alive) context.value = result }
  catch (cause) { if (alive) error.value = String(cause) }
  finally { if (alive) loading.value = false }
}

async function readBatch(id = identifier.value) {
  if (!id) return
  readsInFlight++
  const revision = ++readRevision
  try {
    const result: Batch = await request(`${endpoint}/${encodeURIComponent(id)}`)
    if (alive && revision === readRevision && id === identifier.value) batch.value = result
  } catch (cause) { if (alive && revision === readRevision) error.value = String(cause) }
  finally { readsInFlight-- }
}

async function choose(id: string) {
  if (running.value || submitting.value) return
  readRevision++; identifier.value = id; batch.value = null; consent.value = false; error.value = ''
  await readBatch(id)
}

async function preview() {
  submitting.value = true; error.value = ''; notice.value = ''; consent.value = false
  try {
    const result: Preview = await request(`${endpoint}/request`, { schema_version: 'synthetic-batch-authorization-v1',
      candidates: picked.value, actions: actionOptions.map(item => item[0]).filter(item => actions.value.includes(item)),
      model: 'NONE', limits: limits.value, effective_at: new Date(effective.value).toISOString(), expires_at: new Date(expiry.value).toISOString() })
    if (!alive) return
    readRevision++; identifier.value = result.batch_authorization_id
    batch.value = { preview: result, status: 'REQUESTED', execution_authorized: false, state: null }
    notice.value = '预览已保存。请核对完整来源、候选和数值，再确认这个批次。'
    await loadContext(false)
  } catch (cause) { if (alive) error.value = String(cause) }
  finally { if (alive) submitting.value = false }
}

async function confirm() {
  if (!batch.value || !consent.value) return
  submitting.value = true; error.value = ''
  readRevision++
  const id = identifier.value
  try {
    const result: Batch = await request(`${endpoint}/${id}/confirm`, { confirmed: true, test_confirmation: true, preview_hash: batch.value.preview.hash })
    if (alive && id === identifier.value) { readRevision++; batch.value = result; consent.value = false; notice.value = '已记录合成测试人工确认。可以在此批次范围内连续执行。' }
    await loadContext(false)
  } catch (cause) { if (alive) error.value = String(cause) }
  finally { if (alive) submitting.value = false }
}

async function run() {
  if (!batch.value?.execution_authorized || running.value) return
  running.value = true; error.value = ''; notice.value = '服务正在连续处理已确认清单；暂停、停止或撤销仍可操作。'
  const id = identifier.value
  try {
    const result: Batch = await request(`${endpoint}/${id}/run`, {})
    if (alive && id === identifier.value) { readRevision++; batch.value = result; notice.value = `${status(result.status)}。清单动作完成不表示策略研究通过或获得真实使用资格。` }
  } catch (cause) {
    if (alive) { error.value = String(cause); notice.value = '连接失败不表示服务已停止。请刷新当前状态，必要时明确停止或恢复结算。' }
  } finally {
    if (alive) { running.value = false; await readBatch(id); await loadContext(false) }
  }
}

async function control(action: string) {
  controlling.value = true; error.value = ''
  readRevision++
  const id = identifier.value
  try {
    const result: Batch = await request(`${endpoint}/${id}/${action}`, { confirmed: true, test_confirmation: true })
    if (alive && id === identifier.value) { readRevision++; batch.value = result; notice.value = `${status(result.status)}。已有副作用按原协议结算。` }
  } catch (cause) { if (alive) error.value = String(cause) }
  finally { if (alive) controlling.value = false }
}

onMounted(() => {
  void loadContext()
  timer = setInterval(() => { if (!readsInFlight && batch.value && (running.value || !terminal.has(batch.value.status))) void readBatch() }, 1500)
})
onBeforeUnmount(() => { alive = false; readRevision++; if (timer) clearInterval(timer) })
</script>

<template>
  <section class="surface batch-console" aria-labelledby="batch-title">
    <div class="section-heading"><div><span class="eyebrow">已批准候选 · 隔离 synthetic</span><h2 id="batch-title">有界合成研究批次</h2></div><button type="button" :disabled="loading" @click="loadContext()">刷新清单</button></div>
    <p>一次确认明确候选和额度后，连续执行允许的动作。真实数据未核验，真实 Trial、Paper、订单及真实策略使用资格均未授予。</p>
    <p v-if="error" role="alert" class="batch-error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="context">
      <p v-if="!context.actions_allowed">当前只读，可以查询历史；生成、确认和执行批次均已禁用。</p>
      <p v-for="issue in context.issues" :key="issue" class="batch-error">前置条件未满足：{{ issue }}</p>
      <details><summary>声明的完整比较来源</summary><ul><li v-for="source in context.comparison_sources" :key="source">{{ source }}</li></ul><p>来源缺失不能视为空集合；候选必须已完成正式目标创建、设计审批、冻结、物化和当前新颖性绑定确认。</p></details>
      <fieldset :disabled="!editable"><legend>1 · 选择已完成绑定的候选</legend>
        <p v-if="!context.candidates.length">当前没有可列入新批次的候选。不会自动生成、批准或冻结新设计。</p>
        <label v-for="option in context.candidates" :key="option.member.candidate_id + option.member.contract_hash" class="candidate-option"><input v-model="selected" type="checkbox" :value="option.member.candidate_id" :disabled="option.status !== 'READY'" /><span><strong>{{ option.member.candidate_id }}</strong><small>{{ option.member.objective_id }} · {{ option.status === 'READY' ? '绑定已核验，可生成预览' : option.reason }}</small></span></label>
        <div class="batch-actions"><label v-for="action in actionOptions" :key="action[0]"><input v-model="actions" type="checkbox" :value="action[0]" />{{ action[1] }}</label></div>
      </fieldset>
      <fieldset :disabled="!editable"><legend>2 · 明确额度与有效期</legend><div class="batch-fields"><label v-for="field in fields" :key="field[0]">{{ field[1] }}<input v-model.number="limits[field[0]]" type="number" :min="field[0] === 'trials' ? 0 : 1" step="1" /></label><label>生效时间<input v-model="effective" type="datetime-local" /></label><label>到期时间<input v-model="expiry" type="datetime-local" /></label></div><p>并发 1，重试 0；模型 NONE，模型调用、token 和费用上限均为 0。首次预留后连续计时，暂停不会重置时间或预算。</p><button type="button" :disabled="!picked.length || !actions.length" @click="preview">生成版本化批次预览</button></fieldset>
      <section v-if="batch" class="batch-review"><div class="section-heading"><h3>{{ status(batch.status) }}</h3><button type="button" @click="readBatch()">刷新所选批次</button></div><p class="batch-identity">{{ identifier }}</p><p>候选 {{ batch.preview.request.candidates.length }} 个 · 完成动作 {{ batch.state?.completed_actions.length || 0 }} / {{ batch.preview.request.candidates.length * batch.preview.request.actions.length }} · 失败动作 {{ batch.state?.failed_actions.length || 0 }}</p><p v-if="batch.state?.active_execution">当前动作：{{ batch.state.active_execution.action }}</p>
        <dl class="batch-summary"><div><dt>候选 / Trial / 原批次上限</dt><dd>{{ batch.preview.request.limits.candidates }} / {{ batch.preview.request.limits.trials }} / {{ batch.preview.request.limits.batches }}</dd></div><div><dt>墙钟 / Worker 内存</dt><dd>{{ batch.preview.request.limits.wall_seconds }} 秒 / {{ batch.preview.request.limits.memory_mib }} MiB</dd></div><div><dt>生效至到期</dt><dd>{{ new Date(batch.preview.request.effective_at).toLocaleString('zh-CN') }} — {{ new Date(batch.preview.request.expires_at).toLocaleString('zh-CN') }}</dd></div></dl>
        <details><summary>核对候选、完整来源、政策、原家族与额度绑定</summary><pre>{{ JSON.stringify(batch.preview, null, 2) }}</pre></details>
        <label v-if="batch.status === 'REQUESTED'" class="batch-consent"><input v-model="consent" type="checkbox" :disabled="!editable" />我已核对以上清单、完整来源及明确数值，仅确认这个隔离合成测试批次。</label>
        <div class="batch-buttons"><button v-if="batch.status === 'REQUESTED'" type="button" :disabled="!editable || !consent" @click="confirm">确认该批次</button><button type="button" :disabled="!canControl || !batch.execution_authorized || running || submitting" @click="run">连续执行已确认清单</button><button type="button" :disabled="!canControl || !['ACTIVE', 'NOT_YET_EFFECTIVE'].includes(batch.status)" @click="control('pause')">暂停新动作</button><button type="button" :disabled="!canControl || batch.status !== 'PAUSED'" @click="control('resume')">恢复批准状态</button><button type="button" :disabled="!canControl || ['COMPLETED', 'BLOCKED', 'STOPPED', 'REVOKED', 'REQUESTED'].includes(batch.status)" @click="control('stop')">停止</button><button type="button" :disabled="!canControl || ['COMPLETED', 'BLOCKED', 'STOPPED', 'REVOKED', 'REQUESTED'].includes(batch.status)" @click="control('revoke')">撤销</button><button type="button" :disabled="!canControl || running || batch.status === 'REQUESTED'" @click="control('recover')">恢复审计与结算</button></div><p>恢复结算不会重新执行已开始的动作，不返还已消费预算。暂停或撤销后，已发生的副作用仍须安全收尾。</p>
      </section>
      <section class="batch-history"><h3>该隔离工作区的批次历史</h3><p v-if="!context.batches.length">尚无批次记录。</p><ul><li v-for="item in context.batches" :key="item.batch_authorization_id"><button type="button" :disabled="running || submitting" @click="choose(item.batch_authorization_id)">{{ status(item.status) }} · {{ item.batch_authorization_id }}</button><span v-if="item.reason">{{ item.reason }}</span></li></ul></section>
    </template>
  </section>
</template>

<style scoped>
.batch-console { padding: 24px; margin-bottom: 24px; }.batch-console p { margin: 12px 0; line-height: 1.7; }
fieldset, .batch-review, .batch-history { margin-top: 24px; padding: 20px 0 0; border: 0; border-top: 1px solid #dce3e8; }legend { font-weight: 600; padding-right: 16px; }
.batch-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }.batch-fields label { display: grid; gap: 8px; }
input[type=number], input[type=datetime-local], button { padding: 9px 12px; border: 1px solid #bcc8d1; border-radius: 4px; background: white; color: #233745; }button:not(:disabled) { cursor: pointer; }button:disabled, fieldset:disabled { opacity: .55; }
.candidate-option, .batch-consent { display: flex; align-items: start; gap: 10px; margin: 14px 0; }.candidate-option span { min-width: 0; }.candidate-option strong, .candidate-option small { display: block; overflow-wrap: anywhere; }.candidate-option small { color: #667583; margin-top: 5px; }
.batch-actions, .batch-buttons { display: flex; flex-wrap: wrap; gap: 14px; margin: 18px 0; }.batch-actions label { display: flex; gap: 8px; align-items: center; }
.batch-summary { display: grid; gap: 14px; }.batch-summary dt { color: #667583; font-size: 13px; }.batch-summary dd { margin: 5px 0 0; }.batch-identity { color: #667583; overflow-wrap: anywhere; }.batch-error { color: #a42b26; }
summary { cursor: pointer; padding: 10px 0; }pre { max-height: 420px; overflow: auto; background: #f4f6f8; padding: 14px; font-size: 12px; }.batch-history ul { padding: 0; list-style: none; }.batch-history li { margin: 10px 0; overflow-wrap: anywhere; }.batch-history button { max-width: 100%; overflow-wrap: anywhere; text-align: left; }
@media (max-width: 760px) { .batch-fields { grid-template-columns: 1fr; }.batch-console { padding: 18px; } }
</style>
