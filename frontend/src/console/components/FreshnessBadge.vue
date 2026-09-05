<script setup lang="ts">
import { computed } from 'vue'
import { formatDate, freshnessLabel } from '../presentation'

const props = defineProps<{ state?: string | null; source?: string | null; generatedAt?: string | null; observedAt?: string | null }>()
const display = computed(() => freshnessLabel(props.state))
const tone = computed(() => display.value.code === 'FRESH' ? 'success' : display.value.code === 'UNKNOWN' ? 'muted' : 'attention')
</script>

<template>
  <span class="freshness-wrap" :title="display.description">
    <span class="status-chip" :class="`tone-${tone}`"><span class="status-symbol" aria-hidden="true"></span>{{ display.label }}</span>
    <small v-if="generatedAt || source" class="freshness-meta">数据来源：{{ source || '未提供' }} · {{ formatDate(generatedAt) }}</small>
  </span>
</template>
