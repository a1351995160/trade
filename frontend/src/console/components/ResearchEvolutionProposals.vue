<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { consoleApi, ConsoleApiError } from '../api'
import type { JsonRecord, ObjectiveCreationPreview, ProposalGovernanceReviewResult, ResearchEvolutionProposalView } from '../types'

const props = defineProps<{
  model: ResearchEvolutionProposalView | null
  objectiveId: string
}>()
const emit = defineEmits<{ navigate: [path: string] }>()

const statusFilter = ref('ALL')
const statusOptions = [
  { value: 'ALL', label: '全部建议' },
  { value: 'PENDING', label: '待审核' },
  { value: 'APPROVED', label: '已批准' },
  { value: 'REJECTED', label: '已拒绝' },
]
const proposalItems = ref<JsonRecord[]>([])
const selectedProposalId = ref('')
const selectedProposal = ref<JsonRecord | null>(null)
const preview = ref<ObjectiveCreationPreview | null>(null)
const reviewer = ref('')
const reviewReason = ref('')
const confirmOpen = ref(false)
const confirmChecked = ref(false)
const busy = ref(false)
const loading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const fallbackProposal = computed<JsonRecord | null>(() => props.model?.proposal || null)
const activeProposal = computed<JsonRecord | null>(() => selectedProposal.value || fallbackProposal.value)
const currentState = computed(() => String(activeProposal.value?.governance_state || activeProposal.value?.status || ''))
const objectiveCreated = computed(() => Boolean(activeProposal.value?.objective_created))
const currentStateDisplay = computed(() => objectiveCreated.value ? 'OBJECTIVE_CREATED' : currentState.value)
const canReview = computed(() => ['CREATED', 'HUMAN_REVIEW_REQUIRED'].includes(currentState.value))
const canConfirm = computed(() => currentState.value === 'OBJECTIVE_CREATION_READY' && Boolean(preview.value) && !Boolean(activeProposal.value?.objective_created))
const history = computed<JsonRecord[]>(() => Array.isArray(activeProposal.value?.governance_history) ? activeProposal.value?.governance_history as JsonRecord[] : [])
const directions = computed<unknown[]>(() => Array.isArray(activeProposal.value?.suggested_research_directions) ? activeProposal.value?.suggested_research_directions as unknown[] : [])
const failureSummary = computed<unknown[]>(() => Array.isArray(activeProposal.value?.failure_summary) ? activeProposal.value?.failure_summary as unknown[] : [])
const coverage = computed<JsonRecord>(() => (activeProposal.value?.mechanism_coverage as JsonRecord) || {})
const covered = computed<unknown[]>(() => Array.isArray(coverage.value.covered) ? coverage.value.covered as unknown[] : [])
const unexplored = computed<unknown[]>(() => Array.isArray(coverage.value.unexplored) ? coverage.value.unexplored as unknown[] : [])

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供'
  return String(value)
}

function statusLabel(value: unknown): string {
  const state = String(value || '')
  return ({
    CREATED: '已创建',
    HUMAN_REVIEW_REQUIRED: '待人工审核',
    APPROVED: '已批准',
    OBJECTIVE_CREATION_READY: '待确认创建目标',
    REJECTED: '已拒绝',
    CLOSED: '已关闭',
    OBJECTIVE_CREATED: '已创建新 Objective',
  } as Record<string, string>)[state] || state || '未知状态'
}

function historyActor(record: JsonRecord): string {
  return display(record.reviewer || record.confirmer)
}

function historyTimestamp(record: JsonRecord): string {
  return display(record.timestamp || record.confirmed_at)
}

function historyRecordId(record: JsonRecord): string {
  return display(record.review_id || record.confirmation_id || record.record_id)
}

function seedFromModel() {
  const modelItems = props.model?.proposals || []
  const source = modelItems.length ? modelItems : (fallbackProposal.value ? [fallbackProposal.value] : [])
  const known = new Map(proposalItems.value.map((item) => [String(item.proposal_id || ''), item]))
  source.forEach((item) => {
    const id = String(item.proposal_id || '')
    if (id) known.set(id, item)
  })
  proposalItems.value = [...known.values()]
  if (!selectedProposalId.value && fallbackProposal.value?.proposal_id) selectedProposalId.value = String(fallbackProposal.value.proposal_id)
  if (!selectedProposal.value && fallbackProposal.value) selectedProposal.value = fallbackProposal.value
}

function mergeProposal(item: JsonRecord) {
  const id = String(item.proposal_id || '')
  if (!id) return
  const index = proposalItems.value.findIndex((candidate) => String(candidate.proposal_id || '') === id)
  if (index < 0) proposalItems.value.push(item)
  else proposalItems.value.splice(index, 1, { ...proposalItems.value[index], ...item })
}

async function loadList() {
  seedFromModel()
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await consoleApi.proposalList(props.objectiveId, statusFilter.value)
    const items = Array.isArray(result.items) ? result.items as JsonRecord[] : []
    proposalItems.value = items
    if (!selectedProposalId.value && items[0]?.proposal_id) selectedProposalId.value = String(items[0].proposal_id)
    if (selectedProposalId.value && !items.some((item) => String(item.proposal_id || '') === selectedProposalId.value)) {
      selectedProposalId.value = items[0]?.proposal_id ? String(items[0].proposal_id) : ''
      selectedProposal.value = items[0] || null
    }
    if (selectedProposalId.value) await loadProposal(selectedProposalId.value, false)
  } catch (error) {
    if (!proposalItems.value.length) errorMessage.value = error instanceof ConsoleApiError ? error.message : 'Proposal 列表暂时不可读。'
  } finally {
    loading.value = false
  }
}

async function loadProposal(proposalId: string, reset = true) {
  selectedProposalId.value = proposalId
  if (reset) {
    selectedProposal.value = proposalItems.value.find((item) => String(item.proposal_id || '') === proposalId) || null
    preview.value = null
    successMessage.value = ''
  }
  try {
    selectedProposal.value = await consoleApi.proposal(proposalId)
    mergeProposal(selectedProposal.value)
    if (selectedProposal.value.governance_state === 'OBJECTIVE_CREATION_READY') await loadPreview(proposalId)
  } catch (error) {
    if (error instanceof ConsoleApiError) errorMessage.value = error.message
  }
}

async function loadPreview(proposalId = selectedProposalId.value) {
  if (!proposalId) return
  try {
    preview.value = await consoleApi.proposalPreview(proposalId)
  } catch (error) {
    preview.value = null
    if (error instanceof ConsoleApiError) errorMessage.value = error.message
  }
}

async function submitReview(action: 'approve' | 'reject') {
  if (!selectedProposalId.value || !reviewer.value.trim()) {
    errorMessage.value = '请先填写审核人，再提交人工审核。'
    return
  }
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result: ProposalGovernanceReviewResult = await consoleApi.reviewProposal(selectedProposalId.value, {
      action,
      reviewer: reviewer.value.trim(),
      reason: reviewReason.value.trim() || undefined,
      proposal_hash: activeProposal.value?.proposal_hash,
      research_direction: action === 'approve' ? (directions.value[0] || undefined) : undefined,
    })
    selectedProposal.value = result.proposal
    mergeProposal(result.proposal)
    preview.value = result.objective_creation_preview
    successMessage.value = action === 'approve' ? '审核已通过；Objective Creation Preview 已生成，等待第二次人工确认。' : 'Proposal 已拒绝，研究历史未被改写。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '审核操作未完成。'
  } finally {
    busy.value = false
  }
}

async function closeRejectedProposal() {
  if (!selectedProposalId.value || !reviewer.value.trim()) {
    errorMessage.value = '请先填写审核人，再关闭已拒绝 Proposal。'
    return
  }
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    selectedProposal.value = await consoleApi.closeProposal(selectedProposalId.value, {
      reviewer: reviewer.value.trim(),
      reason: reviewReason.value.trim() || undefined,
    })
    mergeProposal(selectedProposal.value)
    successMessage.value = 'Proposal 已关闭，未创建新的研究目标。'
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '关闭操作未完成。'
  } finally {
    busy.value = false
  }
}

function openConfirmation() {
  confirmChecked.value = false
  confirmOpen.value = true
}

async function confirmObjectiveCreation() {
  if (!preview.value || !reviewer.value.trim() || !confirmChecked.value) return
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await consoleApi.confirmProposal(selectedProposalId.value, {
      confirmed: true,
      action: 'CREATE_OBJECTIVE',
      confirmer: reviewer.value.trim(),
      proposal_hash: preview.value.proposal_hash,
      preview_hash: preview.value.preview_hash,
      confirmation_token: preview.value.confirmation_token,
      idempotency_key: `CONSOLE_CONFIRM_${preview.value.preview_hash.slice(0, 24).toUpperCase()}`,
    })
    confirmOpen.value = false
    confirmChecked.value = false
    successMessage.value = `新研究目标已创建：${display(result.objective_id)}。系统已停止，未自动启动 Candidate 或 Trial。`
    await loadProposal(selectedProposalId.value)
  } catch (error) {
    errorMessage.value = error instanceof ConsoleApiError ? error.message : '创建确认未完成。'
  } finally {
    busy.value = false
  }
}

function goToEvolution() {
  emit('navigate', `/research/evolution?objective_id=${encodeURIComponent(props.objectiveId)}`)
}

function goToAIDesign() {
  const createdObjectiveId = String(activeProposal.value?.created_objective_id || '')
  if (createdObjectiveId) emit('navigate', `/research/evolution/ai-design?objective_id=${encodeURIComponent(createdObjectiveId)}`)
}

watch(() => [props.objectiveId, statusFilter.value], loadList, { immediate: true })
watch(() => props.model, seedFromModel)
</script>

<template>
  <div class="proposal-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">研究建议治理</span>
        <h1>Research Proposal Governance</h1>
        <p>在研究建议与新研究目标之间保留两道人工作业闸门：审核通过只生成创建方案，确认后才创建 Objective。</p>
      </div>
      <select v-model="statusFilter" class="proposal-status-filter" data-testid="proposal-status-filter" aria-label="Proposal 状态筛选">
        <option v-for="option in statusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </div>

    <div v-if="errorMessage" class="proposal-alert danger-alert">{{ errorMessage }}</div>
    <div v-if="successMessage" class="proposal-alert success-alert">{{ successMessage }}</div>

    <section v-if="proposalItems.length" class="proposal-layout">
      <aside class="surface proposal-list-panel">
        <div class="section-heading"><div><span class="eyebrow">Proposal 队列</span><h2>人工治理清单</h2></div><span class="status-chip tone-muted">{{ loading ? '读取中' : `${proposalItems.length} 条` }}</span></div>
        <button v-for="item in proposalItems" :key="String(item.proposal_id)" type="button" class="proposal-list-row" :class="{ selected: String(item.proposal_id) === selectedProposalId }" @click="loadProposal(String(item.proposal_id))">
          <span class="proposal-list-state" :class="`state-${String(item.governance_state || '').toLowerCase()}`">{{ statusLabel(item.objective_created ? 'OBJECTIVE_CREATED' : item.governance_state) }}</span>
          <strong>{{ display(item.failed_mechanism) }}</strong>
          <small>{{ display(item.parent_objective_id) }}</small>
          <small>{{ display(item.source_failure_report) }}</small>
        </button>
      </aside>

      <main v-if="activeProposal" class="proposal-main">
        <section class="surface proposal-boundary">
          <div>
            <span class="eyebrow">当前治理状态</span>
            <h2>{{ statusLabel(currentStateDisplay) }}</h2>
            <p v-if="objectiveCreated">该 Proposal 已完成一次人工确认并创建新 Objective；系统已停止在人工研究设计边界。</p>
            <p v-else-if="currentState === 'HUMAN_REVIEW_REQUIRED' || currentState === 'CREATED'">Proposal 已停止在人工审核边界。审核通过不会创建 Candidate、启动 Trial、调用 AI 或消耗预算。</p>
            <p v-else-if="currentState === 'OBJECTIVE_CREATION_READY'">审核已通过，创建方案已准备好；只有下方第二次人工确认才会创建新 Objective。</p>
            <p v-else-if="currentState === 'REJECTED' || currentState === 'CLOSED'">本 Proposal 不再进入研究目标创建流程，原有研究历史保持不变。</p>
            <p v-else>当前 Proposal 治理状态已记录，页面不会自动推进研究。</p>
          </div>
          <span class="status-chip" :class="objectiveCreated || currentState === 'OBJECTIVE_CREATION_READY' ? 'tone-success' : currentState === 'REJECTED' || currentState === 'CLOSED' ? 'tone-muted' : 'tone-governance'">{{ statusLabel(currentStateDisplay) }}</span>
        </section>

        <section class="proposal-grid">
          <article class="surface padded proposal-card">
            <span class="eyebrow">Proposal 来源</span>
            <h2>失败分析依据</h2>
            <dl class="detail-list">
              <div><dt>Proposal ID</dt><dd><code>{{ display(activeProposal.proposal_id) }}</code></dd></div>
              <div><dt>父研究目标</dt><dd><code>{{ display(activeProposal.parent_objective_id) }}</code></dd></div>
              <div><dt>失败分析报告</dt><dd><code>{{ display(activeProposal.source_failure_report) }}</code></dd></div>
              <div><dt>Proposal hash</dt><dd><code>{{ display(activeProposal.proposal_hash) }}</code></dd></div>
            </dl>
          </article>

          <article class="surface padded proposal-card">
            <span class="eyebrow">失败机制</span>
            <h2>本次建议的边界</h2>
            <dl class="detail-list">
              <div><dt>失败机制</dt><dd><code>{{ display(activeProposal.failed_mechanism) }}</code></dd></div>
              <div><dt>失败机制族</dt><dd><code>{{ display(activeProposal.failed_mechanism_family) }}</code></dd></div>
              <div><dt>失败分类</dt><dd><span v-for="item in failureSummary" :key="String(item)" class="proposal-pill danger-pill">{{ item }}</span><small v-if="!failureSummary.length">未提供</small></dd></div>
            </dl>
          </article>

          <article class="surface padded proposal-card proposal-card-wide">
            <span class="eyebrow">建议方向</span>
            <h2>只提出研究方向，不生成交易规则</h2>
            <div class="proposal-direction-list"><span v-for="item in directions" :key="String(item)" class="proposal-pill direction-pill">{{ item }}</span><small v-if="!directions.length">暂无可确认方向</small></div>
          </article>

          <article class="surface padded proposal-card proposal-card-wide">
            <span class="eyebrow">机制覆盖</span>
            <h2>父 Proposal 的研究空间</h2>
            <div class="coverage-columns">
              <div><strong>已覆盖机制</strong><span v-for="item in covered" :key="`covered-${String(item)}`">{{ item }}</span><small v-if="!covered.length">暂无记录</small></div>
              <div><strong>待探索方向</strong><span v-for="item in unexplored" :key="`open-${String(item)}`">{{ item }}</span><small v-if="!unexplored.length">暂无记录</small></div>
            </div>
          </article>
        </section>

        <section class="surface padded governance-history">
          <div class="section-heading"><div><span class="eyebrow">治理历史</span><h2>可追溯审核记录</h2></div><span class="status-chip tone-muted">追加式记录</span></div>
          <div v-if="history.length" class="history-list">
            <div v-for="record in history" :key="String(record.review_id || record.record_id || `${record.action}-${record.timestamp}`)" class="history-row">
              <span class="history-action">{{ record.action === 'approve' ? '审核通过' : record.action === 'reject' ? '拒绝' : record.action === 'close' ? '关闭' : display(record.action) }}</span>
               <span>{{ historyActor(record) }}</span>
               <span>{{ historyTimestamp(record) }}</span>
               <code>{{ historyRecordId(record) }}</code>
            </div>
          </div>
          <p v-else class="plain-note">尚无人工审核记录。</p>
        </section>

        <section v-if="canReview" class="surface padded review-panel">
          <div class="section-heading"><div><span class="eyebrow">第一道人工闸门</span><h2>审核 Proposal</h2><p>审核动作只改变 Proposal 治理状态，并在批准时生成不可执行的创建方案。</p></div><span class="status-chip tone-governance">需人工操作</span></div>
          <div class="review-fields">
            <label>审核人<input v-model="reviewer" data-testid="proposal-reviewer" autocomplete="off" placeholder="填写审核人" /></label>
            <label>审核意见（可选）<textarea v-model="reviewReason" rows="2" placeholder="记录本次审核依据"></textarea></label>
          </div>
          <div class="review-actions"><button class="button-primary" type="button" data-testid="approve-proposal" :disabled="busy || !reviewer.trim()" @click="submitReview('approve')">审核通过</button><button class="button-danger" type="button" data-testid="reject-proposal" :disabled="busy || !reviewer.trim()" @click="submitReview('reject')">拒绝</button></div>
        </section>

        <section v-if="currentState === 'REJECTED'" class="surface padded review-panel">
          <div class="section-heading"><div><span class="eyebrow">拒绝流程收口</span><h2>关闭 Proposal</h2><p>关闭只记录治理收口，不会创建 Objective 或改变原有研究历史。</p></div><span class="status-chip tone-muted">可选操作</span></div>
          <div class="review-fields"><label>审核人<input v-model="reviewer" data-testid="proposal-close-reviewer" autocomplete="off" placeholder="填写审核人" /></label><label>关闭说明（可选）<textarea v-model="reviewReason" rows="2" placeholder="记录关闭原因"></textarea></label></div>
          <div class="review-actions"><button class="button-secondary" type="button" data-testid="close-proposal" :disabled="busy || !reviewer.trim()" @click="closeRejectedProposal">关闭 Proposal</button></div>
        </section>

        <section v-if="preview" class="surface padded preview-panel">
          <div class="section-heading"><div><span class="eyebrow">Objective Creation Preview</span><h2>下一研究目标创建方案</h2><p>方案仅供人工确认，不创建 Objective，不登记或消耗预算。</p></div><span class="status-chip tone-success">{{ statusLabel(preview.status) }}</span></div>
          <dl class="preview-grid">
            <div><dt>目标名称</dt><dd>{{ display(preview.target_name) }}</dd></div>
            <div><dt>研究方向</dt><dd><code>{{ display(preview.research_direction) }}</code></dd></div>
            <div><dt>父 Proposal</dt><dd><code>{{ display(preview.parent_proposal?.proposal_id) }}</code></dd></div>
            <div><dt>预算建议</dt><dd>{{ display(preview.budget_suggestion?.proposed_total_predictive_budget) }} 个预注册上限</dd></div>
            <div><dt>检验家族</dt><dd><code>{{ display(preview.multiple_testing_family_suggestion?.family_id) }}</code></dd></div>
            <div><dt>预算状态</dt><dd>{{ preview.budget_consumed ? '已消费（异常）' : '未消费' }}</dd></div>
          </dl>
          <div class="preview-safety"><span>安全边界</span><p>Candidate：未创建　·　Trial：未启动　·　AI：未调用　·　最终确认前预算：未消费</p></div>
          <button v-if="canConfirm" class="button-primary confirm-button" type="button" data-testid="confirm-create-objective" @click="openConfirmation">确认创建下一研究目标</button>
          <div v-else-if="activeProposal?.objective_created" class="success-note"><p>该方案已完成一次人工确认，创建回执已记录；系统停在新 Objective 的人工研究设计边界。</p><button class="button-secondary" type="button" data-testid="open-ai-design" @click="goToAIDesign">查看 AI 研究设计</button></div>
        </section>
      </main>
    </section>

    <section v-else class="surface padded proposal-empty">
      <span class="eyebrow">当前目标数据边界</span>
      <h2>{{ props.model?.display?.title_zh || '尚未生成研究演进建议' }}</h2>
      <p>{{ props.model?.display?.message_zh || '请先由明确绑定的失败分析生成 Proposal。' }}</p>
      <button class="button-secondary" type="button" @click="goToEvolution">查看研究演进分析</button>
    </section>

    <div v-if="confirmOpen && preview" class="confirm-backdrop" role="presentation">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-objective-title">
        <span class="eyebrow">第二道人工闸门</span>
        <h2 id="confirm-objective-title">确认创建下一研究目标</h2>
        <p>即将创建 <strong>{{ preview.target_name }}</strong>。这一步会写入 immutable objective id、lineage、budget registry 和 governance record；不会自动创建 Candidate、启动 Trial 或调用 AI。</p>
        <label class="confirm-check"><input v-model="confirmChecked" data-testid="confirm-create-objective-final" type="checkbox" /> 我已查看创建方案，并明确确认创建。</label>
        <div class="review-actions"><button class="button-secondary" type="button" @click="confirmOpen = false">取消</button><button class="button-primary" type="button" :disabled="busy || !confirmChecked" @click="confirmObjectiveCreation">确认创建</button></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.proposal-page { display: grid; gap: 18px; }
.proposal-status-filter { min-width: 132px; align-self: center; }
.proposal-alert { padding: 12px 16px; border-radius: 9px; line-height: 1.5; }
.danger-alert { color: #9d3f48; background: #fceeee; border: 1px solid #f1cacc; }
.success-alert { color: #2c9169; background: #e9f7f0; border: 1px solid #c6ead9; }
.proposal-layout { display: grid; grid-template-columns: minmax(220px, 0.32fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.proposal-list-panel { padding: 16px; position: sticky; top: 16px; }
.proposal-list-row { display: grid; width: 100%; gap: 5px; margin-top: 8px; padding: 12px; text-align: left; color: var(--qc-text); background: var(--qc-surface-subtle); border: 1px solid var(--qc-border); border-radius: 9px; cursor: pointer; }
.proposal-list-row:hover, .proposal-list-row.selected { border-color: #9d8bd3; background: #f8f5ff; }
.proposal-list-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.proposal-list-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--qc-muted); font: 11px "Cascadia Mono", Consolas, monospace; }
.proposal-list-state { width: fit-content; padding: 3px 7px; border-radius: 999px; color: #725f9d; background: #eeeafa; font-size: 11px; }
.state-rejected, .state-closed { color: #9d3f48; background: #fceeee; }
.state-objective_creation_ready, .state-approved { color: #2c9169; background: #e9f7f0; }
.proposal-main { display: grid; gap: 16px; min-width: 0; }
.proposal-boundary { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 22px 26px; border: 1px solid #d9d0f1; background: linear-gradient(135deg, #fff, #f8f5ff); }
.proposal-boundary h2, .proposal-card h2, .governance-history h2, .review-panel h2, .preview-panel h2, .proposal-empty h2 { margin: 5px 0 8px; font-size: 20px; color: var(--qc-text); }
.proposal-boundary p, .proposal-empty p, .review-panel p, .preview-panel p { color: var(--qc-muted); line-height: 1.65; }
.proposal-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.proposal-card-wide { grid-column: 1 / -1; }
.proposal-card .detail-list { margin-top: 14px; }
.proposal-pill { display: inline-flex; margin: 2px 5px 2px 0; padding: 4px 8px; border-radius: 999px; font: 12px "Cascadia Mono", Consolas, monospace; }
.danger-pill { color: #9d3f48; background: #fceeee; }
.direction-pill { color: #2f5d9f; background: #eaf2ff; }
.proposal-direction-list { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 15px; }
.coverage-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 15px; }
.coverage-columns > div { display: grid; gap: 8px; padding: 14px; border: 1px solid var(--qc-border); border-radius: 10px; background: var(--qc-surface-subtle); }
.coverage-columns strong { font-size: 13px; }
.coverage-columns span, .coverage-columns small { color: var(--qc-muted); font: 12px "Cascadia Mono", Consolas, monospace; }
.history-list { display: grid; gap: 8px; }
.history-row { display: grid; grid-template-columns: 90px 120px minmax(160px, 1fr) minmax(150px, 0.9fr); gap: 10px; align-items: center; padding: 10px 12px; border-radius: 8px; background: var(--qc-surface-subtle); color: var(--qc-muted); font-size: 12px; }
.history-action { color: var(--qc-text); font-weight: 600; }
.review-panel, .preview-panel { display: grid; gap: 16px; }
.review-fields { display: grid; grid-template-columns: minmax(180px, 0.5fr) minmax(280px, 1fr); gap: 14px; }
.review-fields label { display: grid; gap: 7px; color: var(--qc-muted); font-size: 12px; }
.review-fields input, .review-fields textarea { width: 100%; box-sizing: border-box; padding: 10px 11px; color: var(--qc-text); background: #fff; border: 1px solid var(--qc-border); border-radius: 8px; font: inherit; resize: vertical; }
.review-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 9px; }
.button-danger { padding: 9px 15px; color: #9d3f48; background: #fff; border: 1px solid #e2aab0; border-radius: 8px; cursor: pointer; }
.button-danger:hover { background: #fceeee; }
button:disabled { cursor: not-allowed; opacity: 0.55; }
.preview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 0; }
.preview-grid > div { display: grid; gap: 7px; padding: 13px; border: 1px solid var(--qc-border); border-radius: 9px; background: var(--qc-surface-subtle); }
.preview-grid dt { color: var(--qc-muted); font-size: 12px; }
.preview-grid dd { margin: 0; color: var(--qc-text); line-height: 1.45; overflow-wrap: anywhere; }
.preview-safety { padding: 12px 14px; color: #2c9169; background: #e9f7f0; border-radius: 8px; }
.preview-safety span { font-weight: 600; }
.preview-safety p { margin: 5px 0 0; color: inherit; font-size: 12px; }
.confirm-button { justify-self: end; }
.success-note { margin: 0; padding: 12px; color: #2c9169 !important; background: #e9f7f0; border-radius: 8px; }
.proposal-empty { display: grid; gap: 10px; }
.confirm-backdrop { position: fixed; inset: 0; z-index: 10; display: grid; place-items: center; padding: 20px; background: rgba(28, 26, 40, 0.42); }
.confirm-dialog { width: min(520px, 100%); display: grid; gap: 14px; padding: 24px; background: #fff; border: 1px solid var(--qc-border); border-radius: 12px; box-shadow: 0 20px 70px rgba(35, 27, 61, 0.22); }
.confirm-dialog h2 { margin: 0; color: var(--qc-text); }
.confirm-dialog p { margin: 0; color: var(--qc-muted); line-height: 1.65; }
.confirm-check { display: flex; gap: 9px; align-items: flex-start; color: var(--qc-text); font-size: 13px; line-height: 1.5; }
@media (max-width: 980px) {
  .proposal-layout { grid-template-columns: 1fr; }
  .proposal-list-panel { position: static; }
  .preview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .page-heading, .proposal-boundary { align-items: flex-start; flex-direction: column; }
  .proposal-grid, .coverage-columns, .review-fields, .preview-grid { grid-template-columns: 1fr; }
  .proposal-card-wide { grid-column: auto; }
  .history-row { grid-template-columns: 1fr 1fr; }
  .confirm-button { justify-self: stretch; }
}
</style>
