<script setup lang="ts">
import { computed } from 'vue'
import { stageLabel } from '../presentation'

const props = defineProps<{ stages: { stage: string; state_display_zh?: string; status?: string }[]; currentStage?: string | null }>()
const displayedStages = computed(() => {
  const source = props.stages.length ? props.stages : [
    { stage: 'HYPOTHESIS' }, { stage: 'CANDIDATE' }, { stage: 'FREEZE' }, { stage: 'STRUCTURAL' },
    { stage: 'PREDICTIVE' }, { stage: 'STATISTICAL' }, { stage: 'FINAL_CLASSIFICATION' },
  ]
  if (source.some((item) => item.stage === 'SAMPLE_FEASIBILITY')) return source
  const structuralIndex = source.findIndex((item) => item.stage === 'STRUCTURAL')
  if (structuralIndex < 0) return source
  return [...source.slice(0, structuralIndex + 1), { stage: 'SAMPLE_FEASIBILITY', state_display_zh: '样本可行性确认' }, ...source.slice(structuralIndex + 1)]
})
const currentIndex = computed(() => {
  const exact = displayedStages.value.findIndex((item) => item.stage === props.currentStage)
  if (exact >= 0) return exact
  if (String(props.currentStage).includes('STRUCTURAL')) return displayedStages.value.findIndex((item) => item.stage === 'STRUCTURAL')
  if (String(props.currentStage).includes('PREDICTIVE')) return displayedStages.value.findIndex((item) => item.stage === 'PREDICTIVE')
  return -1
})
function stateFor(index: number, stage: string) {
  if (displayedStages.value[index]?.status === 'FAILED') return 'failed'
  if (stage === props.currentStage || (currentIndex.value === index && props.currentStage)) return 'current'
  if (currentIndex.value >= 0 && index < currentIndex.value) return 'done'
  return 'pending'
}
</script>

<template>
  <ol class="pipeline-stepper" aria-label="研究生命周期">
    <li v-for="(item, index) in displayedStages" :key="item.stage" :class="`step-${stateFor(index, item.stage)}`">
      <span class="step-marker" aria-hidden="true">{{ stateFor(index, item.stage) === 'done' ? '✓' : stateFor(index, item.stage) === 'failed' ? '!' : index + 1 }}</span>
      <span><strong>{{ stageLabel(item.stage) }}</strong><small v-if="item.state_display_zh && item.state_display_zh !== stageLabel(item.stage)">{{ item.state_display_zh }}</small></span>
    </li>
  </ol>
</template>
