<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ used?: number | null; reserved?: number | null; available?: number | null; total?: number | null; conflict?: boolean }>()
const used = computed(() => Math.max(0, props.used ?? 0))
const reserved = computed(() => Math.max(0, props.reserved ?? 0))
const available = computed(() => Math.max(0, props.available ?? 0))
const total = computed(() => Math.max(0, props.total ?? 0))
const width = (value: number) => total.value ? `${Math.min(100, value / total.value * 100)}%` : '0%'
</script>

<template>
  <div class="budget-meter">
    <div class="budget-total"><strong>{{ total || '暂无数据' }}</strong><span>总预算</span></div>
    <div class="budget-track" role="img" :aria-label="`已使用 ${used}，处理中 ${reserved}，可用 ${available}，总预算 ${total}`"><span class="budget-used" :style="{ width: width(used) }"></span><span class="budget-reserved" :style="{ width: width(reserved) }"></span></div>
    <div class="budget-values">
      <div><i class="legend used"></i><span>已使用</span><b>{{ used }}</b></div>
      <div><i class="legend reserved"></i><span>处理中 / 已预留</span><b>{{ reserved }}</b></div>
      <div><i class="legend available"></i><span>可用</span><b>{{ available }}</b></div>
    </div>
    <p v-if="conflict" class="inline-alert tone-attention">数据来源更新时间不一致，预算采用已登记的主要预算记录。</p>
  </div>
</template>
