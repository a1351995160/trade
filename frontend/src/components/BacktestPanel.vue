<script setup lang="ts">
import { ref } from 'vue'
import { postJson, api } from '../api'
import type { BacktestStatus, BacktestResult } from '../types'

const limit = ref(100)
const start = ref('2021-08-02')
const end = ref('2026-08-05')
const initialCash = ref(1000000)
const maxPositions = ref(10)
const maxPicks = ref(0)
const indexFilter = ref(true)
const trailing = ref(true)
const running = ref(false)
const progress = ref(0)
const progressText = ref('')
const result = ref<BacktestResult | null>(null)
const taskId = ref('')

function fmtPct(x: number | undefined) {
  return ((x ?? 0) * 100).toFixed(2) + '%'
}

async function startBacktest() {
  running.value = true
  progress.value = 0
  progressText.value = '准备数据'
  result.value = null
  try {
    const data = await postJson<{ task_id: string }>('/api/backtest', {
      limit: limit.value || null,
      start: start.value,
      end: end.value,
      initial_cash: initialCash.value,
      max_positions: maxPositions.value,
      max_picks_per_day: maxPicks.value,
      index_filter_enabled: indexFilter.value,
      trailing_enabled: trailing.value
    })
    taskId.value = data.task_id
    poll()
  } catch (e) {
    alert('启动回测失败：' + (e as Error).message)
    running.value = false
  }
}

async function poll() {
  if (!taskId.value) return
  try {
    const task = await api<BacktestStatus>('/api/backtest/status/' + taskId.value)
    progress.value = task.progress
    progressText.value = task.message || task.status
    if (task.status === 'running') {
      setTimeout(poll, 800)
    } else if (task.status === 'error') {
      alert('回测失败：' + (task.result as unknown as { error?: string } | null)?.error || task.message)
      running.value = false
    } else {
      result.value = task.result
      running.value = false
    }
  } catch (e) {
    alert('查询状态失败：' + (e as Error).message)
    running.value = false
  }
}
</script>

<template>
  <section>
    <div class="form-row">
      <label>股票数量上限 <input v-model.number="limit" type="number" min="1" /></label>
      <label>开始日期 <input v-model="start" type="text" /></label>
      <label>结束日期 <input v-model="end" type="text" /></label>
      <label>初始资金 <input v-model.number="initialCash" type="number" step="100000" /></label>
      <label>最大持仓 <input v-model.number="maxPositions" type="number" min="1" max="30" /></label>
      <label>每日最大开仓 <input v-model.number="maxPicks" type="number" min="0" max="30" /></label>
    </div>
    <div class="form-row">
      <label><input v-model="indexFilter" type="checkbox" /> 大盘MA20过滤</label>
      <label><input v-model="trailing" type="checkbox" /> 移动止损</label>
      <button class="primary" :disabled="running" @click="startBacktest">开始回测</button>
    </div>

    <div v-if="running" class="progress-wrap">
      <div class="progress"><div class="progress-bar" :style="{ width: (progress * 100).toFixed(1) + '%' }"></div></div>
      <span>{{ progressText }}</span>
    </div>

    <div v-if="result" class="metrics">
      <div class="metric-card">
        <div class="label">总收益率</div>
        <div class="value" :class="result.metrics.total_return >= 0 ? 'positive' : 'negative'">{{ fmtPct(result.metrics.total_return) }}</div>
      </div>
      <div class="metric-card">
        <div class="label">年化收益率</div>
        <div class="value" :class="result.metrics.annual_return >= 0 ? 'positive' : 'negative'">{{ fmtPct(result.metrics.annual_return) }}</div>
      </div>
      <div class="metric-card">
        <div class="label">最大回撤</div>
        <div class="value negative">{{ fmtPct(result.metrics.max_drawdown) }}</div>
      </div>
      <div class="metric-card">
        <div class="label">胜率</div>
        <div class="value">{{ fmtPct(result.metrics.win_rate) }}</div>
      </div>
      <div class="metric-card">
        <div class="label">盈亏比</div>
        <div class="value">{{ result.metrics.profit_loss_ratio === Infinity ? '∞' : Number(result.metrics.profit_loss_ratio).toFixed(2) }}</div>
      </div>
      <div class="metric-card">
        <div class="label">交易次数</div>
        <div class="value">{{ result.trade_count }}</div>
      </div>
      <div class="metric-card">
        <div class="label">基准收益</div>
        <div class="value" :class="result.metrics.benchmark_return >= 0 ? 'positive' : 'negative'">{{ fmtPct(result.metrics.benchmark_return) }}</div>
      </div>
    </div>

    <h3 v-if="result">交易明细 <span class="muted">共 {{ result.trade_count }} 笔，展示最近 {{ result.trades.length }} 笔</span></h3>
    <div v-if="result" class="table-wrap">
      <table class="data-table">
        <thead>
          <tr><th>代码</th><th>信号</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>原因</th><th>持有</th><th>盈亏额</th><th>盈亏率</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in [...result.trades].reverse()" :key="t.code + t.buy_date + t.sell_date">
            <td>{{ t.code }}</td><td>{{ t.signal_type }}</td><td>{{ t.buy_date }}</td><td>{{ t.buy_price.toFixed(3) }}</td>
            <td>{{ t.sell_date }}</td><td>{{ t.sell_price.toFixed(3) }}</td><td>{{ t.sell_reason }}</td><td>{{ t.holding_days }}</td>
            <td :class="t.pnl >= 0 ? 'pos' : 'neg'">{{ t.pnl.toLocaleString() }}</td>
            <td :class="t.pnl >= 0 ? 'pos' : 'neg'">{{ fmtPct(t.pnl_pct) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.form-row label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
input[type='text'], input[type='number'] { padding: 6px 8px; border: 1px solid #ccd; border-radius: 4px; font-size: 14px; width: 140px; }
input[type='checkbox'] { width: auto; }
button { padding: 8px 16px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
button.primary { background: #2e6be6; color: #fff; }
button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.progress-wrap { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
.progress { width: 320px; height: 12px; background: #e0e4ee; border-radius: 6px; overflow: hidden; }
.progress-bar { height: 100%; background: #2e6be6; transition: width .3s; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0; }
.metric-card { background: #fff; border-radius: 8px; padding: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.metric-card .label { font-size: 12px; color: #777; margin-bottom: 6px; }
.metric-card .value { font-size: 20px; font-weight: 600; }
.value.negative { color: #1a9c63; }
.value.positive { color: #d33a3a; }
h3 { margin: 18px 0 8px; font-size: 16px; }
.muted { color: #888; font-weight: normal; font-size: 13px; margin-left: 6px; }
.table-wrap { overflow: auto; max-height: 420px; background: #fff; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.data-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.data-table th, .data-table td { padding: 7px 10px; border-bottom: 1px solid #eee; text-align: right; white-space: nowrap; }
.data-table th:first-child, .data-table td:first-child { text-align: left; }
.data-table thead th { position: sticky; top: 0; background: #f8f9fc; z-index: 1; }
.pos { color: #1a9c63; }
.neg { color: #d33a3a; }
</style>
