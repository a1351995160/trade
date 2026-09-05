<script setup lang="ts">
import { computed } from 'vue'
import { humanEvidenceRows } from '../presentation'

const props = defineProps<{ value?: Record<string, unknown> | null; authorized?: boolean }>()
const rows = computed(() => humanEvidenceRows(props.value, props.authorized !== false))
</script>

<template>
  <article class="human-evidence">
    <h3>绩效结果</h3>
    <p v-if="authorized && rows.length" class="evidence-intro">已授权读取的绩效指标以中文含义展示。</p>
    <p v-else-if="authorized" class="data-gap-box">已授权读取绩效数据，但当前没有可展示的指标。</p>
    <p v-else class="data-gap-box">当前未授权读取绩效数据。</p>
    <dl v-if="rows.length" class="human-evidence-list">
      <div v-for="row in rows" :key="row.key" :title="row.description">
        <dt>{{ row.label }}</dt>
        <dd>{{ row.value }}</dd>
      </div>
    </dl>
  </article>
</template>
