<script setup lang="ts">
import { computed } from 'vue'
import { humanEvidenceRows } from '../presentation'

const props = defineProps<{ value?: Record<string, unknown> | null }>()
const rows = computed(() => humanEvidenceRows(props.value))
</script>

<template>
  <article class="human-evidence">
    <h3>统计可信度</h3>
    <p v-if="rows.length" class="evidence-intro">统计结果已按可读指标展示；同时测试多个策略时会执行多重检验校正。</p>
    <p v-else class="data-gap-box">当前没有可展示的统计校正结果。</p>
    <dl v-if="rows.length" class="human-evidence-list">
      <div v-for="row in rows" :key="row.key" :title="row.description">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
  </article>
</template>
