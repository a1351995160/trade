<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ objectiveId: string }>()
type ScopeContext = {
  objective_id: string; context_hash: string; data_manifest_hash: string
  datasets: { dataset_id: string; status: string; earliest_date: string | number; latest_date: string | number }[]
  capabilities: { capability_id: string; description: string; action_types: string[] }[]
  required_stop_conditions: string[]
}
type Check = { request_valid: boolean; request_hash?: string; errors: { field: string; code: string }[] }
const context = ref<ScopeContext | null>(null)
const result = ref<Check | null>(null)
const busy = ref(false)
const error = ref('')
const datasets = ref<string[]>([])
const actions = ref<string[]>([])
const start = ref('')
const end = ref('')
const expiry = ref('')
const model = ref('NONE')
const withdrawn = ref(false)
const limits = ref({ candidates: 1, trials: 0, batches: 1, model_calls: 0, tokens: 0,
  cost_minor_units: 0, currency: 'CNY', wall_seconds: 30, memory_mib: 256 })
const limitFields = [
  ['candidates', '候选上限'], ['trials', 'Trial 上限'], ['batches', '批次数上限'],
  ['model_calls', '模型调用上限'], ['tokens', 'Token 上限'], ['cost_minor_units', '费用上限（分）'],
  ['wall_seconds', '运行时间上限（秒）'], ['memory_mib', '内存上限（MiB）'],
] as const
let controller: AbortController | undefined
let revision = 0
const scope = computed(() => ({
  schema_version: 'batch-scope-request-v1', objective_id: props.objectiveId,
  context_hash: context.value?.context_hash, data_manifest_hash: context.value?.data_manifest_hash,
  dataset_ids: datasets.value, data_start: start.value, data_end: end.value,
  actions: actions.value, model: model.value, limits: limits.value,
  expires_at: expiry.value ? new Date(expiry.value).toISOString() : '', withdrawn: withdrawn.value,
  stop_conditions: context.value?.required_stop_conditions,
}))
watch([datasets, actions, start, end, expiry, model, limits, withdrawn], () => {
  revision++
  result.value = null
}, { deep: true, flush: 'sync' })
watch(() => props.objectiveId, () => {
  controller?.abort()
  revision++
  context.value = null
  result.value = null
  error.value = ''
  busy.value = false
})
onBeforeUnmount(() => controller?.abort())

async function request(check: boolean) {
  controller?.abort()
  const active = new AbortController()
  controller = active
  const version = revision
  busy.value = true
  error.value = ''
  result.value = null
  try {
    const query = check ? `?${new URLSearchParams({ scope: JSON.stringify(scope.value) })}` : ''
    const response = await fetch(`/api/research-console/${encodeURIComponent(props.objectiveId)}/batch-scope-request${query}`, { signal: active.signal })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail?.message_zh || payload.message_zh || `读取失败（${response.status}）`)
    if (version !== revision || active.signal.aborted) return
    if (check) result.value = payload as Check
    else context.value = payload as ScopeContext
  } catch (cause) {
    if (!active.signal.aborted) error.value = cause instanceof Error ? cause.message : '请求失败'
  } finally {
    if (controller === active) busy.value = false
  }
}
</script>

<template>
  <section class="surface padded batch-request">
    <div class="section-heading">
      <div><h2>申请有限研究范围</h2><p>先核对数据与资源范围，再交政策审核。此处不会批准、冻结或启动研究。</p></div>
      <button class="button-secondary" type="button" :disabled="busy || !objectiveId" @click="request(false)">读取当前范围</button>
    </div>
    <p v-if="error" role="alert" class="status-chip tone-attention">{{ error }}</p>
    <p v-if="!context" class="plain-note">读取明确目标的当前上下文后填写申请；没有获准的批次自动执行政策。</p>
    <form v-else @submit.prevent="request(true)">
      <p class="plain-note">目标：{{ context.objective_id }} · 数据身份：{{ context.data_manifest_hash.slice(0, 12) }} · 等待政策批准</p>
      <fieldset><legend>数据与拟申请动作</legend>
        <label v-for="dataset in context.datasets" :key="dataset.dataset_id" class="scope-choice">
          <input v-model="datasets" type="checkbox" :value="dataset.dataset_id" />
          {{ dataset.dataset_id }} · {{ dataset.status }} · {{ dataset.earliest_date }}—{{ dataset.latest_date }}
        </label>
        <p v-if="!context.datasets.length" class="plain-note">当前没有登记数据，申请尚不具备条件。</p>
        <label v-for="capability in context.capabilities.filter(item => item.action_types.length)" :key="capability.capability_id" class="scope-choice">
          <input v-model="actions" type="checkbox" :value="capability.action_types[0]" />{{ capability.description }}
        </label>
      </fieldset>
      <div class="scope-fields">
        <label>数据起日<input v-model="start" type="date" required /></label>
        <label>数据止日<input v-model="end" type="date" required /></label>
        <label>申请到期时间（本地）<input v-model="expiry" type="datetime-local" required /></label>
        <label>模型标识（不调用填 NONE）<input v-model="model" required maxlength="120" /></label>
        <label v-for="[key, label] in limitFields" :key="key">{{ label }}<input v-model.number="limits[key]" type="number" min="0" step="1" required /></label>
        <label>费用币种<select v-model="limits.currency"><option>CNY</option><option>USD</option></select></label>
      </div>
      <p class="plain-note">遇人工门禁、预算不足、上下文变化、数据不足、重复候选、权限拒绝、异常或用户暂停时停止受影响路径。资源上限仅为申请值。</p>
      <label class="scope-choice"><input v-model="withdrawn" type="checkbox" />撤回此份申请（不修改任何既有授权）</label>
      <button class="button-primary" type="submit" :disabled="busy">{{ busy ? '核对中…' : '只读校验申请' }}</button>
      <div v-if="result" aria-live="polite">
        <p class="status-chip" :class="result.request_valid ? 'tone-governance' : 'tone-attention'">{{ result.request_valid ? '申请范围校验通过 · 仍待政策审核，未授权执行' : '申请需要修正' }}</p>
        <ul v-if="result.errors.length"><li v-for="(item, index) in result.errors" :key="index">{{ item.field }}：{{ item.code }}</li></ul>
        <details v-if="result.request_valid"><summary>查看可交审核的申请及身份</summary><p>{{ result.request_hash }}</p><pre>{{ JSON.stringify(scope, null, 2) }}</pre></details>
      </div>
    </form>
  </section>
</template>

<style scoped>
.batch-request fieldset { margin: 20px 0; padding: 16px; border: 1px solid var(--qc-border); }
.scope-choice { display: flex; align-items: baseline; gap: 8px; margin: 10px 0; }
.scope-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin: 20px 0; }
.scope-fields label { display: flex; flex-direction: column; gap: 6px; }
.scope-fields input, .scope-fields select { padding: 9px; border: 1px solid var(--qc-border-strong); border-radius: 6px; font: inherit; }
pre { overflow: auto; max-height: 320px; }
</style>
