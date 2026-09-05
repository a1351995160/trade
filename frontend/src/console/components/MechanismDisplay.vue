<script setup lang="ts">
import { computed } from 'vue'
import { displayMechanism } from '../presentation'

const props = defineProps<{ family?: string | null; mechanism?: string | null }>()
const display = computed(() => displayMechanism(props.mechanism || props.family))
</script>

<template>
  <span class="concept-display">
    <strong>{{ display.label }}</strong>
    <small v-if="!display.known">当前字段暂无中文解释，请查看技术信息</small>
  </span>
</template>
