<script setup lang="ts">
import { computed } from 'vue'
import { displayAction, displayReason } from '../presentation'

const props = defineProps<{ action?: string | null; reasonCode?: string | null; allowed?: string[]; forbidden?: string[] }>()
const hasAction = computed(() => Boolean(props.action))
const reason = computed(() => displayReason(props.reasonCode))
</script>

<template>
  <section class="action-panel" :class="{ required: hasAction }" :role="hasAction ? 'alert' : undefined">
    <div class="section-heading"><div><span class="eyebrow">需要人工处理</span><h2>需要人工处理</h2></div><span class="status-chip" :class="hasAction ? 'tone-attention' : 'tone-success'">{{ hasAction ? '需要关注' : '无需处理' }}</span></div>
    <template v-if="hasAction">
      <p class="action-lead">发生了什么</p><strong>{{ displayAction(action) }}</strong>
      <p class="action-lead">为什么暂停</p><span>{{ reasonCode ? reason.label : '当前研究状态要求人工确认。' }}</span>
      <p class="action-lead">建议下一步</p><span>{{ allowed?.length ? allowed.map(displayAction).join('、') : '按当前研究处理说明完成复核。' }}</span>
      <small v-if="forbidden?.length" class="action-forbidden">页面不会执行：{{ forbidden.map(displayAction).join('、') }}</small>
    </template>
    <p v-else class="empty-copy">系统当前没有要求人工处理的问题；页面保持只读，不提供研究守护进程控制。</p>
  </section>
</template>
