<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(defineProps<{
  label?: string
  entries?: Record<string, unknown> | null
  raw?: unknown
  compact?: boolean
}>(), { label: '查看技术信息', compact: false })

const copied = ref(false)
const expanded = ref(false)
const hasRaw = computed(() => props.raw !== undefined)
const rawText = computed(() => {
  if (!hasRaw.value) return ''
  try { return JSON.stringify(props.raw, null, 2) } catch { return String(props.raw) }
})
const entryItems = computed(() => Object.entries(props.entries || {}))

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 2) }
    catch { return String(value) }
  }
  return String(value)
}

async function copyRaw() {
  if (!rawText.value) return
  try {
    await navigator.clipboard.writeText(rawText.value)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1200)
  } catch { copied.value = false }
}

function syncExpanded(event: Event) {
  expanded.value = (event.currentTarget as HTMLDetailsElement).open
}
</script>

<template>
  <details class="technical-details" :class="{ compact }" @toggle="syncExpanded">
    <summary>{{ label }}</summary>
    <template v-if="expanded">
      <dl v-if="entryItems.length" class="technical-entry-list">
        <div v-for="([key, value]) in entryItems" :key="key">
          <dt>{{ key }}</dt>
          <dd><code>{{ displayValue(value) }}</code></dd>
        </div>
      </dl>
      <div v-if="hasRaw" class="raw-technical-data">
        <div class="raw-data-heading"><strong>原始技术数据</strong><button class="copy-button" type="button" @click.stop="copyRaw">{{ copied ? '已复制' : '复制数据' }}</button></div>
        <pre>{{ rawText }}</pre>
      </div>
    </template>
  </details>
</template>
