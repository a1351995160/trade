<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import BacktestPanel from './components/BacktestPanel.vue'
import ScreenerPanel from './components/ScreenerPanel.vue'
import KlinePanel from './components/KlinePanel.vue'
import ResearchConsole from './console/ResearchConsole.vue'
import './console/research-console.css'

const tab = ref<'backtest' | 'screener' | 'kline'>('backtest')
const path = ref(window.location.pathname || '/')
const routeKey = ref(`${window.location.pathname || '/'}${window.location.search || ''}`)
const isResearchConsole = computed(() => path.value === '/research' || path.value.startsWith('/research/') || ['/daemon', '/shadow', '/data-health', '/reports'].includes(path.value))

function isResearchPath(value: string) {
  return value === '/research' || value.startsWith('/research/') || ['/daemon', '/shadow', '/data-health', '/reports'].includes(value)
}

function navigate(nextPath: string) {
  const nextUrl = new URL(nextPath, window.location.origin)
  const currentObjectiveId = new URLSearchParams(window.location.search).get('objective_id')
  if (currentObjectiveId && isResearchPath(nextUrl.pathname) && !nextUrl.searchParams.has('objective_id')) {
    nextUrl.searchParams.set('objective_id', currentObjectiveId)
  }
  const nextLocation = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`
  if (nextLocation === routeKey.value) return
  window.history.pushState({}, '', nextLocation)
  path.value = nextUrl.pathname
  routeKey.value = nextLocation
}

function onPopState() {
  path.value = window.location.pathname || '/'
  routeKey.value = `${window.location.pathname || '/'}${window.location.search || ''}`
}
onMounted(() => window.addEventListener('popstate', onPopState))
onBeforeUnmount(() => window.removeEventListener('popstate', onPopState))
</script>

<template>
  <ResearchConsole v-if="isResearchConsole" :key="routeKey" :path="path" @navigate="navigate" />
  <div v-else class="legacy-app">
    <header class="legacy-header">
      <h1>缠论选股交易系统</h1>
      <span class="legacy-subtitle">Vue 3 + TypeScript + ECharts · 通达信本地数据 · FastAPI 后端</span>
      <button class="research-entry" type="button" @click="navigate('/research')">进入研究控制台 →</button>
    </header>
    <nav class="legacy-tabs" aria-label="原有交易工具">
      <button :class="['legacy-tab', { active: tab === 'backtest' }]" @click="tab = 'backtest'">回测</button>
      <button :class="['legacy-tab', { active: tab === 'screener' }]" @click="tab = 'screener'">选股</button>
      <button :class="['legacy-tab', { active: tab === 'kline' }]" @click="tab = 'kline'">K线图</button>
    </nav>
    <main class="legacy-main">
      <BacktestPanel v-if="tab === 'backtest'" />
      <ScreenerPanel v-else-if="tab === 'screener'" />
      <KlinePanel v-else />
    </main>
  </div>
</template>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #app { min-height: 100%; }
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #f5f6fa; color: #222; }
.legacy-header { position: relative; padding: 18px 28px; background: #1f2d3d; color: #fff; }
.legacy-header h1 { font-size: 22px; }.legacy-subtitle { margin-left: 12px; color: #aab4c3; font-size: 13px; }.research-entry { position: absolute; top: 15px; right: 28px; padding: 8px 12px; border: 1px solid #7891aa; border-radius: 6px; background: #2c3e50; color: #fff; cursor: pointer; }.research-entry:hover { background: #3b536a; }
.legacy-tabs { display: flex; gap: 8px; padding: 12px 28px 0; background: #1f2d3d; }.legacy-tab { padding: 10px 18px; border: 0; border-radius: 6px 6px 0 0; background: #2c3e50; color: #cfd8e3; cursor: pointer; font-size: 15px; }.legacy-tab.active { background: #f5f6fa; color: #1f2d3d; font-weight: 600; }.legacy-main { padding: 20px 28px; }
</style>
