<script setup lang="ts">
import { computed } from 'vue'
import { displayClassification } from '../presentation'

const props = defineProps<{ value?: string | null }>()
const display = computed(() => displayClassification(props.value))
const tone = computed(() => ({ RESEARCH_PASSED: 'success', PROMISING: 'attention', WEAK: 'muted', REJECTED: 'danger', BLOCKED: 'danger', ENGINEERING_INVALIDATED: 'danger' }[display.value.code] || 'muted'))
</script>

<template>
  <span v-if="value" class="status-chip" :class="`tone-${tone}`" :title="display.description"><span class="status-symbol" aria-hidden="true"></span>{{ display.label }}</span>
  <span v-else class="status-chip tone-muted">尚未分类</span>
</template>
