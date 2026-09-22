<script setup lang="ts">
/**
 * BT_BEHAVIOR_DAILY_V2 通用行为回测面板。
 *
 * 设计要点：可选指标、输出名与参数 schema **全部来自注册表**
 * （`GET /api/backtest/behavior/contracts`），界面不硬编码 MACD/KDJ。
 * 用户通过配置表填写指标参数与 JSON 条件表达式，提交到
 * `POST /api/backtest/behavior/v2`，与 CLI 共用同一服务。
 */
import { computed, onMounted, ref } from 'vue'
import { api, postJson } from '../api'

type IndicatorSpec = {
  indicator_id: string
  version: string
  family: string
  display_name: string
  outputs: string[]
  params: Record<string, unknown>
  inputs: string[]
  unit: string
  warmup_bars: number
  requires_extra_data: string[]
  aliases: string[]
}

type Contracts = {
  supported_modes_v2: string[]
  registry_version: string
  indicators_v2_contract: string
  condition_contract_v2: string
  exit_contract_v2: string
  families: string[]
  indicators: IndicatorSpec[]
  custom_entry_conditions: string[]
  custom_exit_conditions: string[]
  supported_exit_types_v2: string[]
  unsupported_exit_types_v2: string[]
}

type SelectedIndicator = {
  indicator_id: string
  paramsText: string
  outputs: string[]
}

const contracts = ref<Contracts | null>(null)
const loading = ref(false)
const errorText = ref('')
const resultText = ref('')
const resultSummary = ref<Record<string, unknown> | null>(null)

const calendarText = ref('')
const symbolText = ref('600000.SH')
const barsText = ref('')
const selected = ref<SelectedIndicator[]>([])
const entryConditionText = ref(
  JSON.stringify(
    {
      op: 'and',
      args: [
        { op: 'gt', args: [{ op: 'indicator', args: ['RSI'], params: { output: 'rsi' } }, { op: 'const', params: { value: 40 } }] },
        { op: 'gt', args: [{ op: 'field', args: ['close'] }, { op: 'indicator', args: ['EMA'], params: { output: 'ema' } }] }
      ]
    },
    null,
    2
  )
)
const namedEntry = ref('')
const exitRulesText = ref(JSON.stringify({ stop_loss_pct: 0.05, fixed_holding_sessions: 10 }, null, 2))
const initialCash = ref(100000)

const families = computed(() => contracts.value?.families ?? [])
const indicatorsByFamily = computed(() => {
  const grouped: Record<string, IndicatorSpec[]> = {}
  for (const spec of contracts.value?.indicators ?? []) {
    ;(grouped[spec.family] ||= []).push(spec)
  }
  return grouped
})

function specOf(indicatorId: string): IndicatorSpec | undefined {
  return contracts.value?.indicators.find((item) => item.indicator_id === indicatorId)
}

function addIndicator(indicatorId: string) {
  const spec = specOf(indicatorId)
  if (!spec) return
  if (selected.value.some((item) => item.indicator_id === indicatorId)) return
  selected.value.push({
    indicator_id: indicatorId,
    paramsText: JSON.stringify(spec.params),
    outputs: spec.outputs
  })
}

function removeIndicator(index: number) {
  selected.value.splice(index, 1)
}

function onSelectChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value) addIndicator(value)
  ;(event.target as HTMLSelectElement).value = ''
}

function useNamedCondition(name: string) {
  namedEntry.value = name
}

async function loadContracts() {
  loading.value = true
  errorText.value = ''
  try {
    contracts.value = await api<Contracts>('/api/backtest/behavior/contracts')
    for (const indicatorId of ['RSI', 'EMA']) {
      if (specOf(indicatorId)) addIndicator(indicatorId)
    }
  } catch (error) {
    errorText.value = String(error)
  } finally {
    loading.value = false
  }
}

function parseJson<T>(text: string, label: string): T {
  try {
    return JSON.parse(text) as T
  } catch (error) {
    throw new Error(`${label} 不是合法 JSON：${String(error)}`)
  }
}

function parseBars(): Record<string, unknown>[] {
  const rows = parseJson<Record<string, unknown>[]>(barsText.value, '行情')
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('行情必须是至少一行的数组')
  }
  return rows
}

async function run() {
  errorText.value = ''
  resultText.value = ''
  resultSummary.value = null
  try {
    const calendar = parseJson<number[]>(calendarText.value, '交易日历')
    const bars = parseBars()
    const indicators = selected.value.map((item) => ({
      indicator_id: item.indicator_id,
      params: parseJson<Record<string, unknown>>(item.paramsText, `${item.indicator_id} 参数`)
    }))
    const body: Record<string, unknown> = {
      mode: 'BT_BEHAVIOR_DAILY_V2',
      calendar,
      symbols: [symbolText.value.trim()],
      bars: { [symbolText.value.trim()]: bars },
      indicators,
      exit_rules: parseJson<Record<string, unknown>>(exitRulesText.value, '退出规则'),
      initial_cash: initialCash.value,
      max_positions: 1,
      max_position_weight: 1
    }
    if (namedEntry.value) {
      body.named_entry_condition = namedEntry.value
    } else {
      body.entry_condition = parseJson<Record<string, unknown>>(entryConditionText.value, '入场条件')
    }
    const response = await postJson<Record<string, unknown>>('/api/backtest/behavior/v2', body)
    resultSummary.value = {
      mode: response.mode,
      engine_version: response.engine_version,
      registry_version: response.registry_version,
      condition_contract: response.condition_contract,
      exit_contract: response.exit_contract,
      cash: response.cash,
      final_equity: response.final_equity,
      signal_count: (response.signals as unknown[])?.length ?? 0,
      fill_count: (response.fills as unknown[])?.length ?? 0,
      condition_trace: (response.resolved_config as Record<string, unknown>)?.condition_trace
    }
    resultText.value = JSON.stringify(response, null, 2)
  } catch (error) {
    errorText.value = String(error)
  }
}

onMounted(loadContracts)
</script>

<template>
  <section class="v2-panel">
    <header class="v2-head">
      <h2>通用行为回测（BT_BEHAVIOR_DAILY_V2）</h2>
      <p class="v2-note">
        可选指标与参数 schema 由注册表动态提供；条件使用受限表达式（不支持任意代码）。
        本页只做合成行情计算，不读取真实研究目录、不写盘。
      </p>
      <p v-if="contracts" class="v2-contract">
        注册表 {{ contracts.registry_version }} ·
        指标合同 {{ contracts.indicators_v2_contract }} ·
        条件合同 {{ contracts.condition_contract_v2 }} ·
        退出合同 {{ contracts.exit_contract_v2 }}
      </p>
    </header>

    <p v-if="loading" class="v2-status">正在载入指标注册表…</p>
    <p v-if="errorText" class="v2-error">{{ errorText }}</p>

    <div class="v2-grid">
      <div class="v2-field">
        <label for="v2-calendar">交易日历（JSON 数组，YYYYMMDD）</label>
        <textarea id="v2-calendar" v-model="calendarText" rows="4" spellcheck="false" />
      </div>
      <div class="v2-field">
        <label for="v2-symbol">证券代码</label>
        <input id="v2-symbol" v-model="symbolText" />
      </div>
      <div class="v2-field v2-wide">
        <label for="v2-bars">行情（JSON 数组：date/open/high/low/close/volume[/amount]）</label>
        <textarea id="v2-bars" v-model="barsText" rows="6" spellcheck="false" />
      </div>
    </div>

    <fieldset class="v2-box">
      <legend>指标（来自注册表，共 {{ contracts?.indicators.length ?? 0 }} 项 / {{ families.length }} 家族）</legend>
      <label class="v2-sr-only" for="v2-indicator-select">选择要加入的指标</label>
      <select id="v2-indicator-select" @change="onSelectChange">
        <option value="">— 选择一个指标加入 —</option>
        <optgroup v-for="family in families" :key="family" :label="family">
          <option v-for="spec in indicatorsByFamily[family]" :key="spec.indicator_id" :value="spec.indicator_id">
            {{ spec.indicator_id }}（{{ spec.display_name }}，输出 {{ spec.outputs.join('/') }}）
          </option>
        </optgroup>
      </select>
      <table class="v2-table">
        <thead>
          <tr><th>指标</th><th>输出</th><th>参数（JSON）</th><th>预热</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in selected" :key="item.indicator_id">
            <td>{{ item.indicator_id }}</td>
            <td>{{ item.outputs.join(', ') }}</td>
            <td>
              <label class="v2-sr-only" :for="`v2-params-${item.indicator_id}`">
                {{ item.indicator_id }} 参数
              </label>
              <input :id="`v2-params-${item.indicator_id}`" v-model="item.paramsText" spellcheck="false" />
            </td>
            <td>{{ specOf(item.indicator_id)?.warmup_bars }}</td>
            <td><button type="button" @click="removeIndicator(index)">移除</button></td>
          </tr>
          <tr v-if="selected.length === 0"><td colspan="5" class="v2-empty">尚未选择指标</td></tr>
        </tbody>
      </table>
    </fieldset>

    <fieldset class="v2-box">
      <legend>入场条件</legend>
      <p class="v2-hint">
        自定义组合 fixture：
        <button v-for="name in contracts?.custom_entry_conditions ?? []" :key="name" type="button" class="v2-chip" @click="useNamedCondition(name)">
          {{ name }}
        </button>
        <button type="button" class="v2-chip" @click="namedEntry = ''">清除（用表达式）</button>
      </p>
      <p v-if="namedEntry" class="v2-status">使用命名条件：{{ namedEntry }}</p>
      <label class="v2-sr-only" for="v2-entry-condition">入场条件表达式（JSON）</label>
      <textarea id="v2-entry-condition" v-model="entryConditionText" rows="8" spellcheck="false" :disabled="!!namedEntry" />
    </fieldset>

    <fieldset class="v2-box">
      <legend>退出规则（V2 支持 {{ contracts?.supported_exit_types_v2.length ?? 0 }} 类）</legend>
      <label class="v2-sr-only" for="v2-exit-rules">退出规则（JSON）</label>
      <textarea id="v2-exit-rules" v-model="exitRulesText" rows="4" spellcheck="false" />
      <p class="v2-hint">明确不支持：{{ (contracts?.unsupported_exit_types_v2 ?? []).join('、') }}</p>
    </fieldset>

    <div class="v2-actions">
      <div class="v2-field">
        <label for="v2-initial-cash">初始资金</label>
        <input id="v2-initial-cash" v-model.number="initialCash" type="number" />
      </div>
      <button type="button" class="v2-run" @click="run">运行回测</button>
    </div>

    <div v-if="resultSummary" class="v2-result">
      <h3>结果摘要</h3>
      <ul>
        <li>模式：{{ resultSummary.mode }}　引擎：{{ resultSummary.engine_version }}</li>
        <li>注册表：{{ resultSummary.registry_version }}</li>
        <li>条件合同：{{ resultSummary.condition_contract }}　退出合同：{{ resultSummary.exit_contract }}</li>
        <li>信号 {{ resultSummary.signal_count }}　成交 {{ resultSummary.fill_count }}</li>
        <li>期末现金 {{ resultSummary.cash }}　期末权益 {{ resultSummary.final_equity }}</li>
      </ul>
      <details>
        <summary>完整结果 JSON</summary>
        <pre>{{ resultText }}</pre>
      </details>
    </div>
  </section>
</template>

<style scoped>
.v2-panel { padding: 20px 28px; }
.v2-head h2 { font-size: 19px; margin-bottom: 6px; }
.v2-note { color: #55606e; font-size: 13px; margin-bottom: 4px; }
.v2-contract { color: #2b6cb0; font-size: 12px; margin-bottom: 12px; }
.v2-status { color: #2b6cb0; font-size: 13px; margin: 6px 0; }
.v2-error { color: #b03030; font-size: 13px; margin: 6px 0; white-space: pre-wrap; }
.v2-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; margin-bottom: 14px; }
.v2-wide { grid-column: 1 / -1; }
.v2-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #55606e; }
.v2-field textarea, .v2-field input { font-family: Consolas, monospace; font-size: 12px; padding: 6px; border: 1px solid #cfd8e3; border-radius: 4px; }
.v2-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.v2-box { border: 1px solid #dfe4ec; border-radius: 6px; padding: 12px; margin-bottom: 14px; }
.v2-box legend { font-size: 13px; font-weight: 600; padding: 0 6px; }
.v2-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
.v2-table th, .v2-table td { border-bottom: 1px solid #eef1f6; padding: 6px 8px; text-align: left; }
.v2-table input { width: 100%; font-family: Consolas, monospace; font-size: 12px; padding: 4px; border: 1px solid #cfd8e3; border-radius: 4px; }
.v2-empty { color: #98a2b3; }
.v2-hint { font-size: 12px; color: #55606e; margin-bottom: 6px; }
.v2-chip { margin: 2px 4px 2px 0; padding: 3px 8px; border: 1px solid #cfd8e3; border-radius: 12px; background: #f7f9fc; cursor: pointer; font-size: 12px; }
.v2-chip:hover { background: #eaf0f8; }
.v2-box textarea { width: 100%; font-family: Consolas, monospace; font-size: 12px; padding: 6px; border: 1px solid #cfd8e3; border-radius: 4px; }
.v2-actions { display: flex; align-items: flex-end; gap: 14px; margin-bottom: 14px; }
.v2-run { padding: 9px 18px; border: 0; border-radius: 6px; background: #1f2d3d; color: #fff; cursor: pointer; font-size: 14px; }
.v2-run:hover { background: #2c3e50; }
.v2-result { border: 1px solid #dfe4ec; border-radius: 6px; padding: 12px; font-size: 13px; }
.v2-result h3 { font-size: 15px; margin-bottom: 8px; }
.v2-result ul { list-style: none; }
.v2-result li { padding: 2px 0; }
.v2-result pre { max-height: 320px; overflow: auto; background: #f7f9fc; padding: 8px; font-size: 11px; }
</style>
