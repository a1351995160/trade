<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import type { KlineData } from '../types'

const code = ref('600000')
const market = ref(1)
const bars = ref(250)
const loading = ref(false)
const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function fmtDate(d: number) {
  const s = String(d)
  return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8)
}

async function loadKline() {
  loading.value = true
  try {
    const data = await api<KlineData>(`/api/kline/${code.value}?market=${market.value}&bars=${bars.value}`)
    render(data)
  } catch (e) {
    alert('加载K线失败：' + (e as Error).message)
  } finally {
    loading.value = false
  }
}

function render(data: KlineData) {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const dates = data.kline.map((k) => fmtDate(k.date))
  const candles = data.kline.map((k) => [k.open, k.close, k.low, k.high])
  const volumes = data.kline.map((k) => k.volume)

  const biData: (null | [string, number])[] = []
  data.bi.forEach((b) => {
    const y1 = b.direction === 'up' ? b.low : b.high
    const y2 = b.direction === 'up' ? b.high : b.low
    biData.push([fmtDate(b.start_date), y1], [fmtDate(b.end_date), y2], null)
  })

  const segData: (null | [string, number])[] = []
  data.segments.forEach((sg) => {
    const y1 = sg.direction === 'up' ? sg.low : sg.high
    const y2 = sg.direction === 'up' ? sg.high : sg.low
    segData.push([fmtDate(sg.start_date), y1], [fmtDate(sg.end_date), y2], null)
  })

  const colorMap: Record<string, string> = {
    B1: '#d33a3a', B2: '#2e6be6', B3: '#7b3fd4',
    S1: '#1a9c63', S2: '#e68a00', S3: '#777'
  }
  const buySignals = data.signals.filter((s) => s.direction === 'buy').map((s) => ({
    value: [fmtDate(s.date), s.price], name: s.type,
    itemStyle: { color: colorMap[s.type] || '#333' }
  }))
  const sellSignals = data.signals.filter((s) => s.direction === 'sell').map((s) => ({
    value: [fmtDate(s.date), s.price], name: s.type,
    itemStyle: { color: colorMap[s.type] || '#333' }
  }))

  chart.setOption({
    animation: false,
    tooltip: { trigger: 'axis' },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 60, right: 20, top: 20, height: '58%' },
      { left: 60, right: 20, top: '72%', height: '14%' }
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, gridIndex: 0, axisLabel: { show: false }, min: 'dataMin', max: 'dataMax' },
      { type: 'category', data: dates, boundaryGap: true, gridIndex: 1, axisLabel: { show: false }, min: 'dataMin', max: 'dataMax' }
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: 'left' },
      { scale: true, gridIndex: 1, position: 'left', axisLabel: { show: false }, splitLine: { show: false } }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], start: 0, end: 100, bottom: 0, height: 18 }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: candles, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: '#d33a3a', color0: '#1a9c63', borderColor: '#d33a3a', borderColor0: '#1a9c63' }
      },
      {
        name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: '#9aa7bd' }
      },
      {
        name: '线段', type: 'line', data: segData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'none', connectNulls: false, lineStyle: { width: 5, color: 'rgba(120,120,200,0.35)' }, silent: true
      },
      {
        name: '笔', type: 'line', data: biData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'none', connectNulls: false, lineStyle: { width: 1.6, color: '#333' }, silent: true
      },
      {
        name: '买点', type: 'scatter', data: buySignals, xAxisIndex: 0, yAxisIndex: 0,
        symbolSize: 14, label: { show: true, formatter: '{b}', position: 'bottom', fontSize: 10, fontWeight: 'bold' }
      },
      {
        name: '卖点', type: 'scatter', data: sellSignals, xAxisIndex: 0, yAxisIndex: 0,
        symbolSize: 14, label: { show: true, formatter: '{b}', position: 'top', fontSize: 10, fontWeight: 'bold' }
      }
    ]
  }, true)
}

onMounted(() => {
  loadKline()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (chart) {
    chart.dispose()
    chart = null
  }
})

function handleResize() {
  chart?.resize()
}
</script>

<template>
  <section>
    <div class="form-row">
      <label>代码 <input v-model="code" type="text" /></label>
      <label>市场
        <select v-model.number="market">
          <option :value="1">上海</option>
          <option :value="0">深圳</option>
        </select>
      </label>
      <label>K线数量 <input v-model.number="bars" type="number" min="50" max="800" /></label>
      <button class="primary" :disabled="loading" @click="loadKline">{{ loading ? '加载中…' : '加载' }}</button>
    </div>
    <div class="chart-wrap">
      <div ref="chartEl" class="chart"></div>
    </div>
    <div class="legend">
      <span class="lg-red">● 一买</span><span class="lg-blue">● 二买</span><span class="lg-purple">● 三买</span>
      <span class="lg-green">● 一卖</span><span class="lg-orange">● 二卖</span><span class="lg-gray">● 三卖</span>
      <span>浅色粗线：简化线段；深色细线：笔</span>
    </div>
  </section>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.form-row label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
input[type='text'], input[type='number'], select { padding: 6px 8px; border: 1px solid #ccd; border-radius: 4px; font-size: 14px; width: 130px; }
button { padding: 8px 16px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
button.primary { background: #2e6be6; color: #fff; }
button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.chart-wrap { background: #fff; border-radius: 6px; padding: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.chart { width: 100%; height: 580px; }
.legend { margin-top: 10px; font-size: 13px; color: #555; display: flex; gap: 14px; flex-wrap: wrap; }
.lg-red { color: #d33a3a; } .lg-blue { color: #2e6be6; } .lg-purple { color: #7b3fd4; }
.lg-green { color: #1a9c63; } .lg-orange { color: #e68a00; } .lg-gray { color: #777; }
</style>
