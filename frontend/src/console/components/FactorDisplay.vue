<script setup lang="ts">
import { computed } from 'vue'
import { displayFactor } from '../presentation'

const props = defineProps<{ id?: string | null; direction?: string | null }>()
const display = computed(() => displayFactor(props.id))
</script>

<template>
  <span class="factor-display" :title="display.description">
    <strong>{{ display.label }}</strong>
    <small><code>{{ display.code || '未提供' }}</code><em v-if="direction">{{ direction === 'POSITIVE' ? '正向' : direction === 'NEGATIVE' ? '反向' : direction }}</em></small>
  </span>
</template>
