<script setup lang="ts">
import { ref } from 'vue'
import { postJson } from '../api'
import type { SignalItem } from '../types'

const limit = ref(100)
const loading = ref(false)
const count = ref(0)
const signals = ref<SignalItem[]>([])

async function runScreener() {
  loading.value = true
  try {
    const data = await postJson<{ count: number; signals: SignalItem[] }>('/api/screener', {
      limit: limit.value || null
    })
    count.value = data.count
    signals.value = data.signals
  } catch (e) {
    alert('选股失败：' + (e as Error).message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section>
    <div class="form-row">
      <label>股票数量上限 <input v-model.number="limit" type="number" min="1" /></label>
      <button class="primary" :disabled="loading" @click="runScreener">{{ loading ? '扫描中，请稍候…' : '开始选股' }}</button>
    </div>
    <h3>买点信号 <span class="muted">共 {{ count }} 个，展示前 500 个</span></h3>
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr><th>代码</th><th>市场</th><th>信号日</th><th>类型</th><th>参考价</th><th>止损低点</th><th>评分</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in signals" :key="s.code + s.signal_date + s.signal_type">
            <td>{{ s.code }}</td><td>{{ s.market === 1 ? '上海' : '深圳' }}</td><td>{{ s.signal_date }}</td>
            <td>{{ s.signal_type }}</td><td>{{ Number(s.price_ref).toFixed(3) }}</td><td>{{ Number(s.stop_low).toFixed(3) }}</td><td>{{ Number(s.score).toFixed(2) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.form-row label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
input[type='number'] { padding: 6px 8px; border: 1px solid #ccd; border-radius: 4px; font-size: 14px; width: 140px; }
button { padding: 8px 16px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
button.primary { background: #2e6be6; color: #fff; }
button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
h3 { margin: 18px 0 8px; font-size: 16px; }
.muted { color: #888; font-weight: normal; font-size: 13px; margin-left: 6px; }
.table-wrap { overflow: auto; max-height: 420px; background: #fff; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.data-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.data-table th, .data-table td { padding: 7px 10px; border-bottom: 1px solid #eee; text-align: right; white-space: nowrap; }
.data-table th:first-child, .data-table td:first-child { text-align: left; }
.data-table thead th { position: sticky; top: 0; background: #f8f9fc; z-index: 1; }
</style>
