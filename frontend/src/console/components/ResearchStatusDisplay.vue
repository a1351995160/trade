<script setup lang="ts">
import { computed } from 'vue'
import { displayState } from '../presentation'

const props = defineProps<{ state?: string | null; showCode?: boolean }>()
const display = computed(() => displayState(props.state))
const tone = computed(() => {
  const code = display.value.code
  if (code.includes('BLOCKED') || code.includes('EXHAUSTED') || code === 'SHUTDOWN') return 'danger'
  if (code.includes('RUNNING') || code === 'READY') return 'active'
  if (code.includes('PASS') || code.includes('COMPLETE') || code === 'COMPLETED') return 'success'
  if (code.includes('UNKNOWN') || code === 'NOT_RUN') return 'muted'
  return 'attention'
})
</script>

<template>
  <span class="status-chip" :class="`tone-${tone}`" :title="display.description"><span class="status-symbol" aria-hidden="true"></span>{{ display.label }}<small v-if="showCode" class="technical-state">内部状态：{{ display.code }}</small></span>
</template>
