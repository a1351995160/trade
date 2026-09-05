<script setup lang="ts">
import { computed } from 'vue'
import { candidatePresentation } from '../presentation'

const props = defineProps<{ candidateId?: string | null; summary?: string | null; compact?: boolean }>()
const display = computed(() => candidatePresentation({ candidate_id: props.candidateId || '' }))
</script>

<template>
  <span class="candidate-name" :class="{ compact }">
    <strong>{{ display.name }}</strong>
    <small v-if="summary || display.covered">{{ summary || display.summary }}</small>
  </span>
</template>
