<script setup lang="ts">
import { computed } from 'vue'
import { humanDecisionRows } from '../presentation'

const props = defineProps<{ decision?: Record<string, unknown> | null; summary?: Record<string, unknown> | null }>()
const rows = computed(() => humanDecisionRows(props.decision, props.summary))
</script>

<template>
  <article class="human-evidence decision-evidence">
    <h3>最终研究结果</h3>
    <p v-if="rows.length" class="evidence-intro">这里只说明本次研究记录能证明什么，不把工程异常误读为策略表现结论。</p>
    <p v-else class="data-gap-box">当前尚未形成可展示的最终研究结果。</p>
    <dl v-if="rows.length" class="human-evidence-list">
      <div v-for="row in rows" :key="row.key" :title="row.description">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
  </article>
</template>
