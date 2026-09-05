<script setup lang="ts">
import { computed } from 'vue'
import { displayReason } from '../presentation'

const props = defineProps<{ code?: string | null; description?: string | null; compact?: boolean; showCode?: boolean }>()
const display = computed(() => displayReason(props.code))
const humanDescription = computed(() => display.value.known ? display.value.description : (props.description && /[\u4e00-\u9fff]/.test(props.description) ? props.description : display.value.description))
</script>

<template>
  <span class="reason-display" :class="{ compact }" :title="humanDescription">
    <strong>{{ display.label }}</strong>
    <small v-if="!compact">{{ humanDescription }}</small>
    <code v-if="showCode">{{ display.code || '未提供原因' }}</code>
  </span>
</template>
