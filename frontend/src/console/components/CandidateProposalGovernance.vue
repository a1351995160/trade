<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ConsoleApiError, consoleApi } from '../api'
import type { CandidateExecutableMaterializationResult, CandidateProposalFreezeResult, CandidateProposalReviewResult, CandidateProposalView, JsonRecord } from '../types'

const props = defineProps<{
  model: CandidateProposalView | null
  objectiveId: string
}>()

const localProposal = ref<JsonRecord | null>(null)
const localFreezePreview = ref<JsonRecord | null>(null)
const localMaterialization = ref<JsonRecord>({})
const reviewer = ref('')
const reviewReason = ref('')
const busy = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

watch(() => props.model, (model) => {
  localProposal.value = model?.proposal || null
  localFreezePreview.value = model?.freeze_preview || null
  localMaterialization.value = model?.materialization || {}
  errorMessage.value = ''
}, { immediate: true })

const proposal = computed<JsonRecord | null>(() => localProposal.value || props.model?.proposal || null)
const preview = computed<JsonRecord | null>(() => localFreezePreview.value || props.model?.freeze_preview || null)
const status = computed(() => String(proposal.value?.governance_state || proposal.value?.status || props.model?.status || 'NEED_CANDIDATE_PROPOSAL'))
const canReview = computed(() => ['CANDIDATE_PROPOSAL_READY', 'HUMAN_REVIEW_REQUIRED'].includes(status.value))
const canFreeze = computed(() => ['FREEZE_PREVIEW_READY', 'CANDIDATE_FREEZE_READY'].includes(status.value))
const frozen = computed(() => ['FROZEN', 'CANDIDATE_GOVERNANCE_FROZEN', 'EXECUTABLE_CANDIDATE_FROZEN', 'READY_FOR_STRUCTURAL_PREFLIGHT'].includes(status.value) || proposal.value?.governance?.candidate_frozen === true)
const materialization = computed<JsonRecord>(() => localMaterialization.value || props.model?.materialization || {})
const materializationState = computed(() => String(materialization.value.materialization_state || materialization.value.effective_state || materialization.value.status || ''))
const executableFrozen = computed(() => ['EXECUTABLE_CANDIDATE_FROZEN', 'READY_FOR_STRUCTURAL_PREFLIGHT'].includes(materializationState.value) || materialization.value.structural_preflight_ready === true)
const canCreateMaterializationPreview = computed(() => frozen.value && !['EXECUTABLE_MATERIALIZATION_PREVIEW_READY', 'EXECUTABLE_CANDIDATE_FROZEN', 'READY_FOR_STRUCTURAL_PREFLIGHT'].includes(materializationState.value))
const canConfirmMaterialization = computed(() => materializationState.value === 'EXECUTABLE_MATERIALIZATION_PREVIEW_READY' && Boolean(materialization.value.preview?.preview_hash || materialization.value.preview_hash))
const factors = computed(() => list(proposal.value?.factor_contract))
const excluded = computed(() => list(proposal.value?.excluded_family))
const history = computed<JsonRecord[]>(() => Array.isArray(proposal.value?.governance_history) ? proposal.value?.governance_history as JsonRecord[] : [])

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  return String(value)
}

function statusLabel(value: string): string {
  return ({
    NEED_CANDIDATE_PROPOSAL: '等待候选策略建议',
    CANDIDATE_PROPOSAL_READY: '候选建议已生成，待人工审核',
    HUMAN_REVIEW_REQUIRED: '待人工审核',
    APPROVED: '已批准，等待冻结预览确认',
    FREEZE_PREVIEW_READY: '冻结预览已准备，等待第二次确认',
    CANDIDATE_FREEZE_READY: '冻结预览已准备，等待第二次确认',
    FROZEN: 'Candidate 已冻结',
    CANDIDATE_GOVERNANCE_FROZEN: '治理冻结已完成，等待执行合同预览',
    EXECUTABLE_CANDIDATE_FROZEN: '执行合同已冻结，具备结构预检资格',
    READY_FOR_STRUCTURAL_PREFLIGHT: 'Candidate 已冻结，等待 Structural Preflight 人工入口',
    REJECTED: '已拒绝',
    CLOSED: '已关闭',
  } as Record<string, string>)[value] || value
}

function statusTone(value: string): string {
  if (value === 'REJECTED' || value === 'CLOSED') return 'tone-muted'
  if (['FREEZE_PREVIEW_READY', 'CANDIDATE_FREEZE_READY', 'APPROVED', 'FROZEN', 'CANDIDATE_GOVERNANCE_FROZEN', 'EXECUTABLE_CANDIDATE_FROZEN', 'READY_FOR_STRUCTURAL_PREFLIGHT'].includes(value)) return 'tone-success'
  return 'tone-governance'
}

function historyActor(record: JsonRecord): string {
  return display(record.reviewer || record.confirmer)
}

async function submitReview(action: 'approve' | 'reject') {
  if (busy.value || !proposal.value || !reviewer.value.trim()) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await consoleApi.candidateProposalReview(String(proposal.value.proposal_id), {
      action,
      reviewer: reviewer.value.trim(),
      reason: reviewReason.value.trim() || undefined,
      proposal_hash: proposal.value.proposal_hash,
    }) as CandidateProposalReviewResult
    localProposal.value = result.proposal
    localFreezePreview.value = result.freeze_preview
    successMessage.value = action === 'approve' ? '候选建议已批准；系统仅生成冻结预览，仍未创建 Candidate。' : '候选建议已拒绝；原研究历史保持不变。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '候选建议审核未完成。'
  } finally {
    busy.value = false
  }
}

async function confirmFreeze() {
  if (busy.value || !proposal.value || !preview.value || !reviewer.value.trim()) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await consoleApi.candidateProposalFreeze(String(proposal.value.proposal_id), {
      action: 'FREEZE_CANDIDATE',
      confirmed: true,
      reviewer: reviewer.value.trim(),
      proposal_hash: proposal.value.proposal_hash,
      candidate_hash: preview.value.candidate_hash,
    }) as CandidateProposalFreezeResult
    localProposal.value = result.proposal
    localFreezePreview.value = result.proposal?.freeze_preview || preview.value
    localMaterialization.value = { materialization_state: 'CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION', required_action: 'CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW', structural_preflight_ready: false }
    successMessage.value = 'Candidate Governance Freeze 已完成；当前等待执行合同预览，未启动 Structural Preflight 或 Trial。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : 'Candidate 冻结未完成。'
  } finally {
    busy.value = false
  }
}

async function createMaterializationPreview() {
  if (busy.value || !proposal.value) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await consoleApi.createExecutableMaterializationPreview(props.objectiveId, { proposal_id: proposal.value.proposal_id }) as CandidateExecutableMaterializationResult
    localMaterialization.value = { ...result, materialization_state: result.status || 'EXECUTABLE_MATERIALIZATION_PREVIEW_READY', preview: result }
    successMessage.value = '执行合同预览已生成；Durable Contract 尚未写入，仍需第二次人工确认。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '执行合同预览未生成。'
  } finally {
    busy.value = false
  }
}

async function confirmMaterialization() {
  if (busy.value || !proposal.value || !reviewer.value.trim()) return
  const previewRecord = (materialization.value.preview || materialization.value) as JsonRecord
  const previewHash = String(previewRecord.preview_hash || '')
  if (!previewHash) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await consoleApi.confirmExecutableMaterialization(props.objectiveId, {
      proposal_id: proposal.value.proposal_id,
      confirmed: true,
      reviewer: reviewer.value.trim(),
      preview_hash: previewHash,
      idempotency_key: `CONSOLE_${previewHash.slice(0, 24)}`,
    }) as CandidateExecutableMaterializationResult
    localMaterialization.value = { ...result, materialization_state: result.effective_state || 'READY_FOR_STRUCTURAL_PREFLIGHT' }
    successMessage.value = '执行合同已确认；Candidate 已具备结构预检资格，系统不会自动进入 Structural。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '执行合同确认未完成。'
  } finally {
    busy.value = false
  }
}

function goToAIDesign() {
  emit('navigate', `/research/evolution/ai-design?objective_id=${encodeURIComponent(props.objectiveId)}`)
}

const emit = defineEmits<{ navigate: [path: string] }>()
</script>

<template>
  <div class="candidate-proposal-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">候选策略建议</span>
        <h1>Candidate Proposal Governance</h1>
        <p>把已生成的 AI 研究设计整理成候选方案，保留人工审核和冻结确认两道边界。</p>
      </div>
      <span class="status-chip" :class="statusTone(status)">{{ statusLabel(status) }}</span>
    </div>

    <div v-if="errorMessage" class="candidate-alert danger-alert">{{ errorMessage }}</div>
    <div v-if="successMessage" class="candidate-alert success-alert">{{ successMessage }}</div>

    <template v-if="proposal">
      <section class="surface candidate-boundary">
        <div>
          <span class="eyebrow">当前研究目标</span>
          <h2>{{ display(proposal.objective?.objective_name || objectiveId) }}</h2>
          <p>状态：{{ statusLabel(status) }}。只有点击确认冻结后才登记 Candidate；冻结完成仍不会自动进入 Structural Preflight 或 Trial。</p>
        </div>
        <div class="candidate-state"><strong>{{ status }}</strong><small>{{ display(proposal.governance?.next_action) }}</small></div>
      </section>

      <section class="candidate-grid">
        <article class="surface padded candidate-card">
          <span class="eyebrow">来源 Objective</span>
          <h2>研究身份与父来源</h2>
          <dl class="detail-list">
            <div><dt>Objective</dt><dd><code>{{ display(proposal.objective_id) }}</code></dd></div>
            <div><dt>Proposal ID</dt><dd><code>{{ display(proposal.parent_proposal?.proposal_id) }}</code></dd></div>
            <div><dt>AI 设计 ID</dt><dd><code>{{ display(proposal.ai_research_design_id || proposal.source_hashes?.ai_research_design) }}</code></dd></div>
            <div><dt>Lineage</dt><dd><code>{{ display(proposal.lineage?.lineage_id) }}</code></dd></div>
          </dl>
        </article>

        <article class="surface padded candidate-card">
          <span class="eyebrow">AI 研究设计</span>
          <h2>{{ display(proposal.candidate_name) }}</h2>
          <dl class="detail-list">
            <div><dt>研究假设</dt><dd>{{ display(proposal.research_hypothesis) }}</dd></div>
            <div><dt>设计意图</dt><dd>{{ display(proposal.candidate_design_intention) }}</dd></div>
            <div><dt>机制族</dt><dd><code>{{ display(proposal.mechanism_family) }}</code></dd></div>
          </dl>
        </article>

        <article class="surface padded candidate-card candidate-wide">
          <span class="eyebrow">因子合同</span>
          <h2>本方案允许使用的因子</h2>
          <div class="pill-list"><span v-for="factor in factors" :key="factor" class="candidate-pill factor-pill">{{ factor }}</span><small v-if="!factors.length">未提供可用因子</small></div>
        </article>

        <article class="surface padded candidate-card">
          <span class="eyebrow">排除机制</span>
          <h2>禁止重复探索</h2>
          <div class="pill-list"><span v-for="item in excluded" :key="item" class="candidate-pill excluded-pill">{{ item }}</span><small v-if="!excluded.length">未提供排除机制</small></div>
        </article>

        <article class="surface padded candidate-card">
          <span class="eyebrow">执行规则</span>
          <h2>只展示设计契约</h2>
          <dl class="detail-list">
            <div><dt>信号时间</dt><dd><code>{{ display(proposal.execution_contract?.signal_time) }}</code></dd></div>
            <div><dt>入场时间</dt><dd><code>{{ display(proposal.execution_contract?.entry) }}</code></dd></div>
            <div><dt>持有周期</dt><dd>{{ display(proposal.execution_contract?.holding_period) }} 个交易时段</dd></div>
            <div><dt>T+1 / 风控</dt><dd>{{ proposal.execution_contract?.same_session_sell_forbidden ? '禁止同日卖出' : '以合同为准' }}</dd></div>
          </dl>
        </article>

        <article class="surface padded candidate-card candidate-wide">
          <span class="eyebrow">数据与检验边界</span>
          <h2>不使用结果数据</h2>
          <dl class="detail-list">
            <div><dt>数据能力</dt><dd><code>{{ list(proposal.data_contract?.required_dataset_ids).join(' · ') || '未提供' }}</code></dd></div>
            <div><dt>可用数据</dt><dd>{{ list(proposal.data_contract?.ready_dataset_ids).join(' · ') || '暂无已登记 READY 数据' }}</dd></div>
            <div><dt>检验家族</dt><dd><code>{{ display(proposal.multiple_testing_family_id) }}</code></dd></div>
            <div><dt>预算上限</dt><dd>{{ display(proposal.budget?.max_total_trials) }} 次（未预留、未消耗）</dd></div>
          </dl>
        </article>
      </section>

      <section class="surface padded candidate-history">
        <div class="section-heading"><div><span class="eyebrow">治理历史</span><h2>候选建议审核记录</h2></div><span class="status-chip tone-muted">追加式审计</span></div>
        <div v-if="history.length" class="history-list"><div v-for="record in history" :key="String(record.review_id || record.record_id || record.timestamp)" class="history-row"><strong>{{ record.action === 'approve' ? '审核通过' : record.action === 'reject' ? '拒绝' : record.action === 'close' ? '关闭' : display(record.action) }}</strong><span>{{ historyActor(record) }}</span><span>{{ display(record.timestamp) }}</span><code>{{ display(record.review_id || record.record_id) }}</code></div></div>
        <p v-else class="plain-note">尚无人工审核记录。</p>
      </section>

      <section v-if="canReview" class="surface padded candidate-review">
        <div class="section-heading"><div><span class="eyebrow">第一道人工闸门</span><h2>审核候选策略建议</h2><p>批准只会生成 Candidate Freeze Preview，不会登记 Candidate 或启动试验。</p></div><span class="status-chip tone-governance">需人工操作</span></div>
        <div class="review-fields"><label>审核人<input v-model="reviewer" data-testid="candidate-proposal-reviewer" autocomplete="off" placeholder="填写审核人" /></label><label>审核意见（可选）<textarea v-model="reviewReason" rows="2" placeholder="记录本次审核依据"></textarea></label></div>
        <div class="review-actions"><button class="button-primary" type="button" data-testid="approve-candidate-proposal" data-freeze-preview="true" :disabled="busy || !reviewer.trim()" @click="submitReview('approve')">审核通过并生成冻结方案</button><button class="button-danger" type="button" data-testid="reject-candidate-proposal" :disabled="busy || !reviewer.trim()" @click="submitReview('reject')">拒绝</button></div>
      </section>

      <section v-if="preview" class="surface padded candidate-freeze-preview">
         <div class="section-heading"><div><span class="eyebrow">Candidate Freeze Preview</span><h2>{{ frozen ? 'Candidate Governance Frozen' : '冻结方案已准备' }}</h2><p>{{ frozen ? '冻结回执与 Candidate Registry 已保存；执行合同物化和 Structural Preflight 仍需独立人工入口。' : '这是 immutable 只读预览，不是 Candidate 登记；确认冻结后才会写入 Candidate Registry。' }}</p></div><span class="status-chip tone-success">{{ statusLabel(status) }}</span></div>
        <dl class="preview-grid"><div><dt>Candidate ID</dt><dd><code>{{ display(preview.candidate_id) }}</code></dd></div><div><dt>Candidate Hash</dt><dd><code>{{ display(preview.candidate_hash) }}</code></dd></div><div><dt>Objective ID</dt><dd><code>{{ display(preview.objective_id) }}</code></dd></div><div><dt>研究 lineage</dt><dd><code>{{ display(preview.lineage?.lineage_id) }}</code></dd></div><div><dt>机制族</dt><dd><code>{{ display(preview.mechanism_family) }}</code></dd></div><div><dt>检验家族</dt><dd><code>{{ display(preview.multiple_testing_family_id) }}</code></dd></div><div><dt>因子合同</dt><dd><code>{{ list(preview.factor_contract).join(' · ') || '未提供' }}</code></dd></div><div><dt>执行合同</dt><dd><code>{{ display(preview.execution_contract?.entry) }} / {{ display(preview.execution_contract?.signal_time) }}</code></dd></div><div><dt>数据合同</dt><dd><code>{{ list(preview.data_contract?.required_dataset_ids).join(' · ') || '未提供' }}</code></dd></div><div><dt>预算</dt><dd>未预留 · 未消耗</dd></div><div><dt>下一步</dt><dd>{{ frozen ? '等待 Structural Preflight 独立确认' : '第二次人工确认冻结' }}</dd></div></dl>
        <div v-if="canFreeze" class="review-actions"><button class="button-primary" type="button" data-testid="confirm-candidate-freeze" :disabled="busy || !reviewer.trim()" @click="confirmFreeze">确认冻结 Candidate</button></div>
         <div class="candidate-safety"><strong>安全边界</strong><p>Candidate Governance：{{ frozen ? '已登记并冻结' : '未登记' }}　·　Structural Preflight：未启动　·　Trial：未启动　·　AI：未调用　·　Budget：未消耗</p></div>
       </section>

       <section v-if="frozen" class="surface padded candidate-materialization">
         <div class="section-heading"><div><span class="eyebrow">Executable Materialization Bridge</span><h2>第二层：执行合同冻结</h2><p>治理冻结只证明人工批准了 Candidate Proposal；只有完整 DurableFrozenCandidateContractV1 通过两个校验后，才具备结构预检资格。</p></div><span class="status-chip" :class="executableFrozen ? 'tone-success' : 'tone-governance'">{{ statusLabel(materializationState || 'CANDIDATE_GOVERNANCE_FROZEN') }}</span></div>
         <dl class="preview-grid materialization-grid">
           <div><dt>Candidate ID</dt><dd><code>{{ display(materialization.value.candidate_id || proposal.candidate_id || proposal.freeze_record?.candidate_id) }}</code></dd></div>
           <div><dt>Candidate Hash</dt><dd><code>{{ display(materialization.value.candidate_hash || proposal.candidate_hash || proposal.freeze_record?.candidate_hash) }}</code></dd></div>
           <div><dt>Contract Hash</dt><dd><code>{{ display(materialization.value.durable_contract_hash || materialization.value.preview?.durable_contract_hash) }}</code></dd></div>
           <div><dt>Semantic Fingerprint</dt><dd><code>{{ display(materialization.value.semantic_fingerprint || materialization.value.preview?.semantic_fingerprint) }}</code></dd></div>
           <div><dt>Provider readiness</dt><dd>{{ display(materialization.value.preview?.provider_readiness?.status || materialization.value.provider_readiness?.status) }}</dd></div>
           <div><dt>研究周期</dt><dd><code>{{ display(materialization.value.preview?.research_period_identity?.id || materialization.value.research_period_identity?.id) }}</code></dd></div>
           <div><dt>缺失字段</dt><dd>{{ list(materialization.value.preview?.missing_fields || materialization.value.missing_fields).join(' · ') || '无' }}</dd></div>
           <div><dt>下一步</dt><dd>{{ display(materialization.value.required_action || materialization.value.preview?.next_action || 'CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW') }}</dd></div>
         </dl>
         <div v-if="canCreateMaterializationPreview" class="review-actions"><button class="button-primary" type="button" data-testid="create-executable-materialization-preview" :disabled="busy" @click="createMaterializationPreview">生成执行合同预览</button></div>
         <div v-if="canConfirmMaterialization" class="review-actions"><button class="button-primary" type="button" data-testid="confirm-executable-materialization" :disabled="busy || !reviewer.trim()" @click="confirmMaterialization">确认冻结执行合同</button></div>
         <div v-if="executableFrozen" class="candidate-safety"><strong>结构预检资格</strong><p>已具备 READY_FOR_STRUCTURAL_PREFLIGHT 资格；请通过独立入口人工进入 Structural Preflight，本页面不会自动触发。</p></div>
       </section>
    </template>

    <section v-else class="surface padded candidate-empty">
      <span class="eyebrow">当前目标数据边界</span>
      <h2>{{ model?.display?.title_zh || '等待候选策略建议' }}</h2>
      <p>{{ model?.display?.message_zh || '请先显式生成 Candidate Proposal；页面不会自动创建。' }}</p>
      <button class="button-secondary" type="button" @click="goToAIDesign">查看 AI 研究设计</button>
    </section>
  </div>
</template>

<style scoped>
.candidate-proposal-page { display: grid; gap: 18px; }
.candidate-alert { padding: 12px 16px; border-radius: 9px; line-height: 1.5; }
.danger-alert { color: #9d3f48; background: #fceeee; border: 1px solid #f1cacc; }
.success-alert { color: #2c9169; background: #e9f7f0; border: 1px solid #c6ead9; }
.candidate-boundary { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 22px 26px; border: 1px solid #d9d0f1; background: linear-gradient(135deg, #fff, #f8f5ff); }
.candidate-boundary h2, .candidate-card h2, .candidate-history h2, .candidate-review h2, .candidate-freeze-preview h2, .candidate-empty h2 { margin: 5px 0 8px; color: var(--qc-text); font-size: 20px; }
.candidate-boundary p, .candidate-empty p, .candidate-review p, .candidate-freeze-preview p { color: var(--qc-muted); line-height: 1.65; }
.candidate-state { display: grid; gap: 6px; min-width: 245px; padding: 14px 16px; color: #725f9d; background: #eeeafa; border-radius: 10px; }
.candidate-state small { color: inherit; line-height: 1.5; }
.candidate-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.candidate-card { min-width: 0; }
.candidate-wide { grid-column: 1 / -1; }
.pill-list { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; }
.candidate-pill { display: inline-flex; padding: 4px 8px; border-radius: 999px; font: 12px "Cascadia Mono", Consolas, monospace; overflow-wrap: anywhere; }
.factor-pill { color: #2f5d9f; background: #eaf2ff; }
.excluded-pill { color: #9d3f48; background: #fceeee; }
.history-list { display: grid; gap: 8px; }
.history-row { display: grid; grid-template-columns: 100px 120px minmax(160px, 1fr) minmax(150px, 0.9fr); gap: 10px; align-items: center; padding: 10px 12px; border-radius: 8px; background: var(--qc-surface-subtle); color: var(--qc-muted); font-size: 12px; }
.history-row strong { color: var(--qc-text); }
.candidate-review, .candidate-freeze-preview, .candidate-materialization { display: grid; gap: 16px; }
.review-fields { display: grid; grid-template-columns: minmax(180px, 0.5fr) minmax(280px, 1fr); gap: 14px; }
.review-fields label { display: grid; gap: 7px; color: var(--qc-muted); font-size: 12px; }
.review-fields input, .review-fields textarea { width: 100%; box-sizing: border-box; padding: 10px 11px; color: var(--qc-text); background: #fff; border: 1px solid var(--qc-border); border-radius: 8px; font: inherit; resize: vertical; }
.review-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 9px; }
.button-danger { padding: 9px 15px; color: #9d3f48; background: #fff; border: 1px solid #e2aab0; border-radius: 8px; cursor: pointer; }
.button-danger:hover { background: #fceeee; }
.preview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 0; }
.preview-grid > div { display: grid; gap: 7px; padding: 13px; border: 1px solid var(--qc-border); border-radius: 9px; background: var(--qc-surface-subtle); }
.preview-grid dt { color: var(--qc-muted); font-size: 12px; }
.preview-grid dd { margin: 0; color: var(--qc-text); line-height: 1.45; overflow-wrap: anywhere; }
.candidate-safety { padding: 12px 14px; color: #2c9169; background: #e9f7f0; border-radius: 8px; }
.candidate-safety p { margin: 5px 0 0; color: inherit; font-size: 12px; }
.candidate-empty { display: grid; gap: 10px; }
@media (max-width: 980px) { .candidate-boundary { align-items: flex-start; flex-direction: column; } .candidate-state { width: 100%; box-sizing: border-box; } .preview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .candidate-grid, .review-fields, .preview-grid { grid-template-columns: 1fr; } .candidate-wide { grid-column: auto; } .history-row { grid-template-columns: 1fr 1fr; } }
</style>
