<script setup lang="ts">
import { computed, ref } from 'vue'
import { consoleApi, ConsoleApiError } from '../api'
import type { JsonRecord, ResearchEvolutionAIDesignView } from '../types'
import TechnicalDetails from './TechnicalDetails.vue'

const props = defineProps<{
  model: ResearchEvolutionAIDesignView | null
  objectiveId: string
}>()
const emit = defineEmits<{ navigate: [path: string]; refresh: [] }>()

const input = computed<JsonRecord>(() => props.model?.input || {})
const proposal = computed<JsonRecord>(() => (input.value.research_evolution_proposal as JsonRecord) || {})
const landscapeEntries = computed<JsonRecord[]>(() => Array.isArray(input.value.failure_landscape?.entries) ? input.value.failure_landscape.entries as JsonRecord[] : [])
const excluded = computed<unknown[]>(() => Array.isArray(input.value.excluded_mechanisms) ? input.value.excluded_mechanisms as unknown[] : [])
const directions = computed<unknown[]>(() => Array.isArray(input.value.suggested_research_directions) ? input.value.suggested_research_directions as unknown[] : [])
const design = computed<JsonRecord | null>(() => props.model?.design || null)
const approval = computed<JsonRecord>(() => props.model?.approval || props.model?.governance?.approval || {})
const reviewBusy = ref(false)
const candidateBusy = ref(false)
const reviewer = ref('')
const reason = ref('')
const actionError = ref('')
const actionMessage = ref('')

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  return String(value)
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function statusLabel(value: unknown): string {
  return ({
    NEED_AI_RESEARCH_DESIGN: '等待 AI 研究设计',
    AI_DESIGN_READY: 'AI 设计已生成，等待人工确认',
    AI_DESIGN_AWAITING_CONFIRMATION: '等待人工确认 AI 设计',
    AI_DESIGN_APPROVED: 'AI 设计已批准',
    AI_DESIGN_REJECTED: 'AI 设计已拒绝',
  } as Record<string, string>)[String(value || '')] || display(value)
}

const approvalStatus = computed(() => String(approval.value.approval_status || 'NOT_AVAILABLE'))
const isPending = computed(() => approvalStatus.value === 'PENDING')
const isApproved = computed(() => approvalStatus.value === 'APPROVED' && approval.value.candidate_generation_allowed === true)
const isRejected = computed(() => approvalStatus.value === 'REJECTED')

function stableReviewKey(decision: string): string {
  const raw = `AI_DESIGN_CONSOLE_${decision}_${props.objectiveId}_${String(design.value?.design_hash || approval.value.ai_design_hash || '')}`
  return raw.slice(0, 255)
}

function errorMessage(error: unknown): string {
  if (error instanceof ConsoleApiError) return `${error.message}（${error.code}）`
  return '操作未完成，请重新读取当前 AI 设计状态。'
}

async function review(decision: 'APPROVED' | 'REJECTED') {
  if (reviewBusy.value || !props.model?.available || !isPending.value) return
  if (!reviewer.value.trim()) {
    actionError.value = '请先填写审核人。'
    return
  }
  reviewBusy.value = true
  actionError.value = ''
  actionMessage.value = ''
  try {
    await consoleApi.confirmEvolutionAIDesign(props.objectiveId, {
      confirmed: true,
      decision,
      reviewer: reviewer.value.trim(),
      reason: reason.value.trim(),
      idempotency_key: stableReviewKey(decision),
      ai_design_hash: design.value?.design_hash || approval.value.ai_design_hash,
    })
    actionMessage.value = decision === 'APPROVED' ? 'AI 研究设计已批准；Candidate Proposal 仍需显式生成。' : 'AI 研究设计已拒绝；Candidate Proposal 生成已关闭。'
    emit('refresh')
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    reviewBusy.value = false
  }
}

async function generateCandidateProposal() {
  if (candidateBusy.value || !isApproved.value) return
  candidateBusy.value = true
  actionError.value = ''
  actionMessage.value = ''
  try {
    await consoleApi.generateCandidateProposal(props.objectiveId)
    actionMessage.value = 'Candidate Proposal 已显式生成；尚未创建 Candidate 或启动后续验证。'
    emit('refresh')
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    candidateBusy.value = false
  }
}

function goToProposals() {
  emit('navigate', `/research/evolution/proposals?objective_id=${encodeURIComponent(props.objectiveId)}`)
}

function goToCandidateProposals() {
  emit('navigate', `/research/candidates/proposals?objective_id=${encodeURIComponent(props.objectiveId)}`)
}
</script>

<template>
  <div class="ai-design-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">AI 研究设计</span>
        <h1>Evolution Objective AI Research Design</h1>
        <p>AI 只读取结果盲化的失败经验、机制覆盖、父 Proposal 和数据能力；设计结果停在人工确认边界。</p>
      </div>
      <span class="status-chip" :class="isApproved ? 'tone-success' : isRejected ? 'tone-danger' : model?.available ? 'tone-governance' : 'tone-muted'">{{ statusLabel(model?.status) }}</span>
    </div>

    <section class="surface ai-design-boundary">
      <div>
        <span class="eyebrow">当前 Objective</span>
        <h2>{{ display(objectiveId) }}</h2>
        <p>{{ model?.display?.message_zh || '只读读取当前 Objective 的 AI 研究设计上下文。' }}</p>
      </div>
      <div class="ai-design-state"><strong>{{ model?.status || 'NEED_AI_RESEARCH_DESIGN' }}</strong><small>{{ model?.governance?.next_action || (model?.available ? 'HUMAN_CONFIRM_AI_RESEARCH_DESIGN' : 'GENERATE_AI_RESEARCH_DESIGN') }}</small></div>
    </section>

    <section v-if="model?.available" class="surface padded ai-design-approval" data-testid="ai-design-approval">
      <div class="section-heading">
        <div><span class="eyebrow">人工审批边界</span><h2>{{ isApproved ? 'AI 设计已批准' : isRejected ? 'AI 设计已拒绝' : '等待人工确认' }}</h2><p>{{ isApproved ? '批准只开放 Candidate Proposal 生成权限，不会自动生成。' : isRejected ? '原 AI Design 保留不变；必须生成新的设计身份后才能再次审核。' : '必须先由人工对当前设计作出明确批准或拒绝。' }}</p></div>
        <span class="status-chip" :class="isApproved ? 'tone-success' : isRejected ? 'tone-danger' : 'tone-governance'">{{ statusLabel(model.status) }}</span>
      </div>
      <dl class="detail-list approval-details">
        <div><dt>AI Design hash</dt><dd><code>{{ display(design?.design_hash || approval.ai_design_hash) }}</code></dd></div>
        <div><dt>审批状态</dt><dd>{{ display(approvalStatus) }}</dd></div>
        <div><dt>审核人</dt><dd>{{ display(approval.receipt?.reviewer || approval.reviewer) }}</dd></div>
        <div><dt>审核时间</dt><dd>{{ display(approval.receipt?.reviewed_at || approval.reviewed_at) }}</dd></div>
        <div><dt>审批决定</dt><dd>{{ display(approval.receipt?.decision || approval.decision) }}</dd></div>
        <div><dt>Receipt hash</dt><dd><code>{{ display(approval.receipt?.receipt_hash) }}</code></dd></div>
      </dl>
      <div v-if="isPending" class="approval-form">
        <label><span>审核人</span><input v-model="reviewer" aria-label="AI 设计审核人" placeholder="填写本次人工审核人" /></label>
        <label><span>审核说明（可选）</span><input v-model="reason" aria-label="AI 设计审核说明" placeholder="填写批准或拒绝理由" /></label>
        <div class="modal-actions"><button class="button-secondary" type="button" :disabled="reviewBusy" @click="review('REJECTED')">{{ reviewBusy ? '提交中…' : '拒绝 AI 设计' }}</button><button class="button-primary" type="button" :disabled="reviewBusy" @click="review('APPROVED')">{{ reviewBusy ? '提交中…' : '批准 AI 设计' }}</button></div>
      </div>
      <div v-if="isApproved" class="page-actions"><button class="button-primary" type="button" :disabled="candidateBusy" @click="generateCandidateProposal">{{ candidateBusy ? '正在生成…' : '显式生成 Candidate Proposal' }}</button><button class="button-secondary" type="button" @click="goToCandidateProposals">查看 Candidate Proposal</button></div>
      <p v-if="isRejected" class="plain-note approval-rejected-note">Candidate Proposal 生成按钮已关闭。</p>
      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <p v-if="actionMessage" class="action-message" role="status">{{ actionMessage }}</p>
    </section>

    <section class="ai-design-grid">
      <article class="surface padded ai-design-card">
        <span class="eyebrow">父研究来源</span>
        <h2>Proposal lineage</h2>
        <dl class="detail-list">
          <div><dt>父 Proposal</dt><dd><code>{{ display(proposal.proposal_id) }}</code></dd></div>
          <div><dt>父 Proposal hash</dt><dd><code>{{ display(proposal.proposal_hash) }}</code></dd></div>
          <div><dt>父研究目标</dt><dd><code>{{ display(proposal.parent_objective_id) }}</code></dd></div>
          <div><dt>Failure Landscape</dt><dd><code>{{ display(model?.source_refs?.failure_landscape) }}</code></dd></div>
          <div><dt>当前 lineage</dt><dd><code>{{ display(input.objective_lineage?.lineage_id) }}</code></dd></div>
        </dl>
      </article>

      <article class="surface padded ai-design-card">
        <span class="eyebrow">失败经验</span>
        <h2>只保留机制与失败分类</h2>
        <div v-if="landscapeEntries.length" class="ai-design-entry-list">
          <div v-for="(entry, index) in landscapeEntries" :key="String(entry.candidate_id || index)" class="ai-design-entry">
            <strong>{{ display(entry.candidate_family) }}</strong>
            <code>{{ display(entry.mechanism) }}</code>
            <small>{{ list(entry.failure_categories).join(' · ') || '未提供失败分类' }}</small>
          </div>
        </div>
        <p v-else class="plain-note">暂无可展示的失败空间条目。</p>
      </article>

      <article class="surface padded ai-design-card ai-design-wide">
        <span class="eyebrow">禁止重复机制</span>
        <h2>AI 不得回到已覆盖方向</h2>
        <div class="ai-design-pills"><span v-for="item in excluded" :key="String(item)" class="design-pill blocked-pill">{{ item }}</span><small v-if="!excluded.length">暂无登记的禁止机制</small></div>
      </article>

      <article class="surface padded ai-design-card ai-design-wide">
        <span class="eyebrow">AI 研究目标</span>
        <h2>{{ design ? display(design.research_hypothesis) : '等待显式生成研究设计' }}</h2>
        <div class="ai-design-two-column">
          <div><strong>建议探索方向</strong><span v-for="item in directions" :key="String(item)" class="design-pill direction-pill">{{ item }}</span><small v-if="!directions.length">未提供</small></div>
          <div><strong>机制族</strong><code>{{ display(design?.mechanism_family || directions[0]) }}</code><strong>候选设计意图</strong><p>{{ display(design?.candidate_design_intention || '尚未生成；本页不会自动创建 Candidate。') }}</p></div>
        </div>
      </article>

      <article v-if="design" class="surface padded ai-design-card ai-design-wide">
        <span class="eyebrow">AI 设计提案</span>
        <h2>验证预期与允许因子</h2>
        <dl class="detail-list">
          <div><dt>允许因子</dt><dd>{{ list(design.allowed_factors).join(' · ') || '未提供' }}</dd></div>
          <div><dt>排除机制</dt><dd>{{ list(design.excluded_mechanisms).join(' · ') || '未提供' }}</dd></div>
          <div><dt>验证预期</dt><dd>{{ list(design.validation_expectation).join('；') || '未提供' }}</dd></div>
        </dl>
      </article>
    </section>

    <section class="surface padded ai-design-safety">
      <div class="section-heading"><div><span class="eyebrow">安全边界</span><h2>当前页面不会推进研究执行</h2></div><span class="status-chip tone-success">Outcome Blind</span></div>
      <div class="safety-grid"><div><strong>Performance</strong><small>不读取结果数据</small></div><div><strong>Candidate</strong><small>不会自动创建</small></div><div><strong>Trial</strong><small>不会启动 Predictive Trial</small></div><div><strong>Budget</strong><small>不会消耗或预留</small></div></div>
      <div class="page-actions"><button class="button-secondary" type="button" @click="goToProposals">返回 Proposal 治理</button></div>
      <TechnicalDetails compact label="查看 AI 设计输入与来源哈希" :raw="{ source_refs: model?.source_refs, source_hashes: input.source_hashes, outcome_blind: model?.outcome_blind, output_path: model?.output_path }" />
    </section>
  </div>
</template>

<style scoped>
.ai-design-page { display: grid; gap: 18px; }
.ai-design-boundary { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 22px 26px; border: 1px solid #d9d0f1; background: linear-gradient(135deg, #fff, #f8f5ff); }
.ai-design-boundary h2, .ai-design-card h2, .ai-design-safety h2 { margin: 5px 0 8px; color: var(--qc-text); font-size: 20px; }
.ai-design-boundary p, .ai-design-card p { color: var(--qc-muted); line-height: 1.65; }
.ai-design-state { display: grid; gap: 6px; min-width: 210px; padding: 14px 16px; color: #2c9169; background: #e9f7f0; border-radius: 10px; }
.ai-design-state small { color: inherit; line-height: 1.5; }
.ai-design-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.ai-design-wide { grid-column: 1 / -1; }
.ai-design-card { min-width: 0; }
.ai-design-entry-list { display: grid; gap: 9px; margin-top: 14px; }
.ai-design-entry { display: grid; gap: 4px; padding: 11px 13px; border: 1px solid var(--qc-border); border-radius: 9px; background: var(--qc-surface-subtle); }
.ai-design-entry code, .ai-design-entry small { overflow-wrap: anywhere; color: var(--qc-muted); font-size: 12px; }
.ai-design-pills { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; }
.design-pill { display: inline-flex; padding: 4px 8px; border-radius: 999px; font: 12px "Cascadia Mono", Consolas, monospace; }
.blocked-pill { color: #9d3f48; background: #fceeee; }
.direction-pill { color: #2f5d9f; background: #eaf2ff; }
.ai-design-two-column { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; margin-top: 14px; }
.ai-design-two-column > div { display: grid; gap: 9px; align-content: start; padding: 14px; border: 1px solid var(--qc-border); border-radius: 10px; background: var(--qc-surface-subtle); }
.ai-design-two-column .design-pill { width: fit-content; }
.ai-design-safety { display: grid; gap: 16px; }
.ai-design-approval { display: grid; gap: 16px; border: 1px solid #d9d0f1; background: #fcfbff; }
.approval-details { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.approval-form { display: grid; gap: 12px; padding-top: 4px; }
.approval-form label { display: grid; gap: 6px; color: var(--qc-muted); font-size: 13px; }
.approval-form input { width: 100%; box-sizing: border-box; padding: 9px 11px; border: 1px solid var(--qc-border); border-radius: 8px; color: var(--qc-text); background: var(--qc-surface); }
.action-error { margin: 0; color: #a13948; }
.action-message { margin: 0; color: #2c9169; }
.approval-rejected-note { margin: 0; color: #a13948; }
@media (max-width: 760px) {
  .ai-design-boundary { align-items: flex-start; flex-direction: column; }
  .ai-design-state { width: 100%; box-sizing: border-box; }
  .ai-design-grid, .ai-design-two-column, .approval-details { grid-template-columns: 1fr; }
  .ai-design-wide { grid-column: auto; }
}
</style>
