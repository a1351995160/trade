<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ value?: string | null; hash?: string | null; full?: boolean }>()
const copied = ref(false)

async function copyId() {
  if (!props.value) return
  try {
    await navigator.clipboard.writeText(props.value)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1200)
  } catch {
    copied.value = false
  }
}
</script>

<template>
  <details v-if="value" class="technical-id" :class="{ full }" :title="value">
    <summary @click.stop><span>查看技术编号</span><button class="copy-button" type="button" :aria-label="`复制技术标识 ${value}`" @click.prevent.stop="copyId">{{ copied ? '已复制' : '复制' }}</button></summary>
    <code class="technical-value">{{ value }}</code>
    <small v-if="hash">身份哈希 · {{ hash }}</small>
  </details>
  <span v-else class="data-gap">未提供技术标识</span>
</template>
