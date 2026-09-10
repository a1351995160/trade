<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Entry = { candidate_id: string; symbol: string; action: string; quantity: number; reason: string; source_plan_id?: string }
type Plan = { plan_id: string; status: string; entries: Entry[]; holdings: Entry[]; unallocated_cash: number; excluded: { candidate_id: string; reason: string }[] }
type Replay = { completed_events?: number; total_events?: number; state?: { cash: number; fees: number; trades: unknown[]; lots: Record<string, unknown> }; status: string }
type View = { context_hash: string; actions_allowed: boolean; qualified_strategy_count: number; frozen_input_count: number; account: { cash: number; equity: number }; sources: { candidate_id: string; sessions: number[] }[]; paper: Record<string, Replay>; strategy_admission: Record<string, { research_state: string; reason: string; preview_blocked: boolean; registry_ref: string | null }> }

const view = ref<View | null>(null), plan = ref<Plan | null>(null)
const candidate = ref(''), session = ref(''), busy = ref(false), confirmed = ref(false)
const error = ref(''), notice = ref('')
const targetEvents = ref(1)
const replay = computed(() => view.value?.paper[candidate.value])
const admission = computed(() => view.value?.strategy_admission[candidate.value])
const dates = computed(() => view.value?.sources.find(item => item.candidate_id === candidate.value)?.sessions || [])
let controller: AbortController | undefined
let revision = 0
const endpoint = '/api/research-engineering/workbench'
const money = (value?: number) => value === undefined ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const labels: Record<string, string> = {
  STRATEGY_NOT_REGISTERED: '尚未登记研究策略', CANONICAL_REGISTRY_BINDING_MISSING: '尚无正式策略库绑定',
  WAITING_USAGE_AUTHORIZATION: '等待使用资格批准', REGISTRY_CONTRACT_MISMATCH: '策略库与冻结版本不一致',
  STRATEGY_RETIRED_OR_INVALIDATED: '策略已退役或证据失效',
  RESEARCH_PREVIEW: '研究预览', NOT_READY: '资料未就绪', NO_STRATEGIES: '没有策略输入',
  BUY_PLAN: '研究买入计划', NO_TRADE: '不交易', HOLD: '持有', EXIT: '退出计划',
  QUALIFIED_SIGNAL_THEN_RANKED: '信号符合冻结规则，按规则排序',
  SYMBOL_OWNED_BY_OTHER_STRATEGY: '同股已分配给另一策略', EXIT_BUY_CONFLICT: '存在退出计划，停止买入',
  PORTFOLIO_MEMBER_NOT_READY: '组合成员未就绪', PORTFOLIO_POSITION_LIMIT: '组合仓位已达上限',
  PORTFOLIO_CASH_EXPOSURE_OR_TURNOVER_LIMIT: '受现金、敞口或换手上限限制',
  CASH_LOT_OR_POSITION_LIMIT: '现金、整手或仓位不足',
  NO_SESSION: '尚未开始', HISTORICAL_REPLAY: '已核对的历史回放',
}
const label = (value?: string) => value ? labels[value] || value : '暂无数据'

function invalidate() { revision++; controller?.abort(); busy.value = false; confirmed.value = false; plan.value = null; notice.value = '' }
watch([candidate, session], invalidate)
watch(targetEvents, () => { confirmed.value = false })
watch(candidate, () => { targetEvents.value = Math.min((replay.value?.completed_events || 0) + 1, replay.value?.total_events ?? Infinity) })
onBeforeUnmount(() => controller?.abort())

async function request(path: string, init?: RequestInit) {
  const response = await fetch(path, { ...init, signal: controller?.signal })
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail?.reason || body.detail?.code || body.code || `HTTP ${response.status}`)
  return body
}

async function load() {
  invalidate(); controller = new AbortController(); busy.value = true; error.value = ''
  const ticket = revision
  try {
    const result: View = await request(endpoint)
    if (ticket !== revision) return
    view.value = result
    if (!candidate.value) candidate.value = result.sources[0]?.candidate_id || ''
    if (!session.value) session.value = String(result.sources[0]?.sessions[0] || '')
  } catch (err) { if (!(err instanceof DOMException && err.name === 'AbortError')) error.value = String(err) }
  finally { busy.value = false }
}

function planTime() { return `${session.value.slice(0, 4)}-${session.value.slice(4, 6)}-${session.value.slice(6, 8)}T15:00:00+08:00` }
async function act(action: 'preview' | 'publish' | 'advance') {
  controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''; notice.value = ''
  const ticket = revision
  try {
    const result = action === 'preview'
      ? await request(`${endpoint}?plan_at=${encodeURIComponent(planTime())}`)
      : await request(`${endpoint}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          confirmed: confirmed.value, context_hash: view.value?.context_hash, candidate_id: candidate.value,
          plan_at: planTime(), event_count: targetEvents.value,
        }) })
    if (ticket !== revision) return
    if (action === 'preview') plan.value = result
    else if (action === 'publish') { plan.value = result.plan; notice.value = '研究预览已归档；不会产生可执行订单。' }
    else { view.value = await request(endpoint); notice.value = '已处理至指定模拟事件并完成账务核对。' }
    confirmed.value = false
  } catch (err) { if (!(err instanceof DOMException && err.name === 'AbortError')) { error.value = String(err); confirmed.value = false } }
  finally { if (ticket === revision) busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="surface engineering-workbench" aria-labelledby="engineering-heading">
    <div class="section-heading"><div><span class="eyebrow">合成工程工作台 · SYNTHETIC</span><h2 id="engineering-heading">计划、组合与模拟对账</h2></div><button type="button" :disabled="busy" @click="load">刷新状态</button></div>
    <p>仅使用启动时明确装配的合成输入。冻结测试候选不是可用策略，回放不累计真实观察天数。</p>
    <p v-if="error" class="workbench-error" role="alert">{{ error }}<br />请检查当前工作区配置或重新读取上下文；本次操作未被计为成功。</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="!view && !busy">尚未配置工程工作台。真实策略、组合和 Paper 均未启用。</p>
    <template v-if="view">
      <dl class="workbench-metrics"><div><dt>冻结合成输入</dt><dd>{{ view.frozen_input_count }}</dd></div><div><dt>合格可用策略</dt><dd>{{ view.qualified_strategy_count }}</dd></div><div><dt>计划账户现金</dt><dd>{{ money(view.account.cash) }}</dd></div><div><dt>真实观察天数</dt><dd>0</dd></div></dl>
      <div class="workbench-controls"><label>测试候选<select v-model="candidate" :disabled="busy"><option v-for="item in view.sources" :key="item.candidate_id">{{ item.candidate_id }}</option></select></label><label>计划收盘日<select v-model="session" :disabled="busy"><option v-for="day in dates" :key="day" :value="String(day)">{{ day }}</option></select></label><button type="button" :disabled="busy || !session" @click="act('preview')">计算组合研究预览</button></div>
      <p v-if="admission" role="status">策略库研究状态：{{ admission.research_state }}。{{ label(admission.reason) }}。{{ admission.preview_blocked ? '已停止该版本的工程预览和回放。' : '仅可进行冻结输入的工程预览，未获得策略使用资格。' }}</p>
      <div v-if="plan" class="workbench-plan"><h3>组合计划 · {{ label(plan.status) }}</h3><p>分配后现金：{{ money(plan.unallocated_cash) }}。卖出款未计入可用现金。</p><p v-for="item in plan.excluded" :key="item.candidate_id">{{ item.candidate_id }}：{{ label(item.reason) }}</p><div class="table-scroll"><table class="research-table"><thead><tr><th>策略</th><th>股票</th><th>动作</th><th>数量</th><th>原因</th></tr></thead><tbody><tr v-for="(item, index) in [...plan.entries, ...plan.holdings]" :key="index"><td>{{ item.candidate_id }}</td><td>{{ item.symbol }}</td><td>{{ label(item.action) }}</td><td>{{ item.quantity }}</td><td>{{ label(item.reason) }}</td></tr></tbody></table></div><details><summary>计划与来源身份</summary><pre>{{ JSON.stringify(plan, null, 2) }}</pre></details></div>
      <div class="workbench-replay"><h3>所选候选的独立模拟账户</h3><p>{{ replay?.status }} · 已落盘 {{ replay?.completed_events || 0 }} / {{ replay?.total_events ?? '尚未初始化' }} 个事件</p><p>模拟现金 {{ money(replay?.state?.cash) }} · 费用 {{ money(replay?.state?.fees) }} · 成交 {{ replay?.state?.trades?.length || 0 }} 笔。各候选账户独立，不相加为组合资产。</p><details v-if="replay?.state"><summary>持仓、订单与对账证据</summary><pre>{{ JSON.stringify(replay, null, 2) }}</pre></details></div>
      <label class="workbench-consent"><input v-model="confirmed" type="checkbox" :disabled="busy || !view.actions_allowed" />我确认使用当前合成输入与隔离模拟账户执行所选工程操作。</label>
      <div class="workbench-controls"><button type="button" :disabled="busy || !confirmed || !view.actions_allowed || !plan" @click="act('publish')">归档研究预览</button><label>累计事件目标<input v-model.number="targetEvents" type="number" :min="replay?.completed_events || 1" :max="replay?.total_events" step="1" :disabled="busy" /></label><button type="button" :disabled="busy || !confirmed || !view.actions_allowed || !candidate || (replay?.total_events !== undefined && replay.completed_events === replay.total_events)" @click="act('advance')">推进至指定事件</button><span v-if="!view.actions_allowed">当前只读；不会自动恢复或启动回放。</span></div>
    </template>
  </section>
</template>

<style scoped>
.engineering-workbench { padding: 24px; margin-bottom: 24px; }
.engineering-workbench p { margin: 12px 0; line-height: 1.7; }
.workbench-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 24px 0; }
.workbench-metrics dt { color: #667583; font-size: 13px; }.workbench-metrics dd { margin: 8px 0 0; font-size: 24px; font-variant-numeric: tabular-nums; }
.workbench-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 14px; margin: 16px 0; }
label { display: grid; gap: 8px; }select, button, input[type=number] { padding: 9px 12px; border: 1px solid #bcc8d1; border-radius: 4px; background: white; color: #233745; }button:not(:disabled) { cursor: pointer; }button:disabled { opacity: .5; }
.research-table td:first-child, .research-table td:last-child { white-space: normal; overflow-wrap: anywhere; max-width: 210px; }
.workbench-consent { display: flex; align-items: center; margin-top: 20px; }.workbench-error { color: #a42b26; }.workbench-plan, .workbench-replay { border-top: 1px solid #dce3e8; padding-top: 20px; margin-top: 20px; }
pre { max-height: 360px; overflow: auto; padding: 12px; background: #f4f6f8; font-size: 12px; }summary { cursor: pointer; padding: 12px 0; }
@media (max-width: 700px) { .workbench-metrics { grid-template-columns: repeat(2, 1fr); }select { max-width: 100%; }.workbench-controls label { min-width: 0; width: 100%; } }
</style>
