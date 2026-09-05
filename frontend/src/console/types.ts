export type JsonRecord = Record<string, any>

export interface Provenance {
  source_id: string
  source_generated_at: string | null
  observed_at: string
  freshness_state: string
  stale: boolean
  conflict: boolean
  source_version?: string | null
}

export interface DashboardView extends Provenance {
  objective_id: string
  research_running: boolean
  daemon_state: string
  orchestrator_state: string
  orchestrator_state_zh: string
  stage: string
  current_candidate_id: string | null
  remaining_frozen_candidates: number | null
  budget_used: number | null
  budget_total: number | null
  budget_remaining: number | null
  budget_conflict: boolean
  research_passed_count: number
  promising_count: number
  required_human_action: string | null
  engineering_blocked: boolean
  resource_health: string
  shadow_latest_state: string
  data_health_state: string
  predictive_trial_count: number
  safety_counters: JsonRecord
  display: JsonRecord
  structural?: JsonRecord
  governance_readiness?: JsonRecord
  predictive_trial_recovery?: PredictiveTrialResumePreview
}

export interface DaemonStatusView extends Provenance {
  objective_id: string
  daemon_state: string
  stage: string
  current_candidate_id: string | null
  remaining_frozen_candidates: number | null
  required_human_ai_action: string | null
  process_pid: number | null
  process_rss_bytes: number | null
  system_available_memory_bytes: number | null
  last_checkpoint_time: string | null
  last_error: string | null
  retry_safe: boolean
  daemon_run_id: string | null
  state_display_zh: string
  action_display_zh: string
  budget: JsonRecord
  handoff_stale: boolean
  handoff_conflict: boolean
}

export interface DaemonHealthView extends Provenance {
  objective_id: string
  health_state: string
  health_display_zh: string
  daemon_state: string
  process_alive_observed: boolean | null
  process_pid: number | null
  process_rss_bytes: number | null
  system_available_memory_bytes: number | null
  checkpoint_age_seconds: number | null
  telemetry_tail: JsonRecord[]
  last_error: string | null
}

export interface ResearchPipelineView extends Provenance {
  objective_id: string
  current_stage: string
  current_state: string
  current_state_zh: string
  next_action: string
  next_action_zh: string
  current_candidate_id: string | null
  remaining_frozen_candidates: number | null
  last_error: string | null
  state_source: string
  stages: { stage: string; state_display_zh?: string }[]
  batch_ids: string[]
  candidate_count: number
  trial_count: number
  recent_events: JsonRecord[]
  execution: {
    status: string
    status_zh: string
    process_alive: boolean
    process_pid: number | null
    process_state: string
    process_state_zh: string
    candidate_id: string | null
    candidate_completed: boolean
    structural_status: string
    structural_status_zh: string
    structural_reason_code: string | null
    structural_reason_zh: string | null
    structural_reason_codes: string[]
    predictive_status: string
    predictive_status_zh: string
    predictive_classification: string | null
    predictive_classification_zh: string | null
    predictive_reason_codes: string[]
    predictive_reason_zh: string | null
    completed_at: string | null
  }
  structural_reconciliation: {
    status: string
    status_zh: string
    available: boolean
    candidate_id: string | null
    requires_confirmation: boolean
    scope: string
    reason_zh: string
    action_zh: string
  }
  predictive_trial_start?: PredictiveTrialStartPreview
  predictive_trial_recovery?: PredictiveTrialResumePreview
  predictive_authorization: {
    status: string
    status_zh: string
    available: boolean
    candidate_id: string | null
    requires_confirmation: boolean
    scope: string
    reason_zh: string
    action_zh: string
    candidate_hash?: string | null
    next_action?: string
    trial_created?: boolean
    budget_reserved?: number
    budget_used_delta?: number
    decision_id?: string | null
    reconciliation_id?: string | null
    preview_endpoint?: string | null
  }
  governance_readiness?: {
    status: string
    status_zh: string
    available: boolean
    decision_mode: string | null
    candidate_id: string | null
    candidate_hash?: string | null
    structural: JsonRecord
    choices: JsonRecord[]
    next_action_zh: string
    human_explanation_zh: string
    final_test_access: JsonRecord
    prospective: string
    real_order: string
    predictive_trials_created: number
    performance_access: number
    reconciliation_ref?: string | null
    reconciliation_id?: string | null
    next_action?: string
    budget_snapshot?: JsonRecord
    trial_created?: boolean
    budget_reserved?: number
    budget_used_delta?: number
    latest_decision?: JsonRecord | null
  }
}

export interface PredictiveAuthorizationPreview {
  schema_version: string
  status: string
  status_zh: string
  available: boolean
  objective_id: string
  decision_id: string | null
  decision_mode: string
  decision_type: string
  candidate_id: string
  candidate_hash: string
  reconciliation_id: string
  structural: JsonRecord
  choice: JsonRecord
  preview: JsonRecord
  preview_hash: string
  confirmation_token: string
  final_test_access: JsonRecord
  prospective: string
  real_order: string
  generated_at: string
}

export interface PredictiveTrialStartPreview {
  schema_version: string
  status: string
  status_zh: string
  available: boolean
  objective_id: string
  action: string
  action_zh: string
  candidate_id: string
  candidate_hash: string
  candidate_state: string
  candidate_contract_ref?: string | null
  candidate_contract_content_hash?: string | null
  authorization_decision_id?: string | null
  authorization_decision_hash?: string | null
  structural_reconciliation_id?: string | null
  structural: JsonRecord
  trial_number: number
  budget: JsonRecord
  preview: JsonRecord
  preview_hash: string
  confirmation_token: string
  confirmation_required: boolean
  final_test_access: JsonRecord
  final_test_boundary: JsonRecord
  prospective: string
  real_order: string
  reason_zh?: string
  reasons?: string[]
  generated_at?: string
}

export interface PredictiveTrialResumePreview {
  schema_version: string
  status: string
  status_zh: string
  available: boolean
  objective_id: string
  action: string
  action_zh: string
  candidate_id: string
  candidate_hash: string
  trial_id: string
  start_intent_id: string
  trial_number: number
  budget: JsonRecord
  preview: JsonRecord
  preview_hash: string
  confirmation_token: string
  confirmation_required: boolean
  reason_zh?: string
  reasons?: string[]
  generated_at?: string
}

export interface ResearchObjectiveSummaryView {
  objective_id: string
  objective_name: string | null
  lifecycle_state: string
  lifecycle_state_zh: string
  orchestrator_state: string
  orchestrator_state_zh: string
  state_source: string
  orchestrator_source_available: boolean
  orchestrator_error_code: string | null
  orchestrator_error_message_zh: string | null
  orchestrator_source_generated_at: string | null
  governance_pending: boolean
  terminal: boolean
  created_at: string | null
  updated_at: string | null
  max_total_trials: number | null
  max_batches: number | null
}

export interface ResearchObjectiveListView extends Provenance {
  objectives: ResearchObjectiveSummaryView[]
}

export interface CandidateSummaryView extends Provenance {
  candidate_id: string
  candidate_hash: string
  explanation_zh: string
  mechanism_family: string
  pipeline_state: string
  frozen_state: string
  structural_status: string
  predictive_trial_status: string
  effective_classification: string | null
  holding_horizon: number | null
  factor_ids: string[]
  created_batch_id: string | null
  created_run_id: string | null
  budget_consumed: boolean
  reasons: { reason_code: string; reason_display_zh?: string; explanation_zh?: string }[]
  scope_status: string
  scope_status_zh: string
  scope_reason_zh: string
  is_current_followup: boolean
}

export interface CandidateListResponse {
  objective_id: string
  page: number
  page_size: number
  total: number
  items: CandidateSummaryView[]
  scope_summary?: {
    current_followup_candidate_count: number
    historical_ai_record_count: number
    parent_promising_reference_count: number
    planned_confirmation_candidate_limit: number
    historical_ai_candidate_ids: string[]
    parent_candidate_refs: Record<string, unknown>[]
  }
}

export interface CandidateDetailView extends Provenance {
  candidate_id: string
  identity: JsonRecord
  semantics: JsonRecord
  structural: JsonRecord
  predictive: JsonRecord
  governance: JsonRecord
  artifact_lineage: JsonRecord
  shadow: JsonRecord
}

export interface ContractCorrectionPreview {
  schema_version: string
  status: string
  available: boolean
  objective_id: string
  candidate_id: string
  candidate_hash: string
  contract_ref: string
  reason_code: string
  reason_zh: string
  requires_confirmation: boolean
  preview_hash?: string
  confirmation_token?: string
  safety_evidence?: {
    trial_records: number
    performance_accessed: boolean
    candidate_active_reservations: number
    budget_used?: number
    budget_reserved?: number
  }
}

export interface StructuralPreflightView extends Provenance {
  candidate_id: string
  candidate_hash: string
  status: string
  qualified_observations: number | null
  selected_opportunities: number | null
  portfolio_feasible_count: number | null
  lower_bound: number | null
  upper_bound: number | null
  minimum_required: number | null
  reason_codes: string[]
  provider_completeness: JsonRecord
  research_period: JsonRecord
  pit_status: string
  execution_feasibility: string
  outcome_blind: boolean
  performance_data_loaded: boolean
}

export interface TrialSummaryView extends Provenance {
  trial_id: string
  objective_id: string
  batch_id: string
  candidate_id: string
  candidate_hash: string
  family_id: string
  status: string
  lifecycle_state: string
  registered: boolean
  reserved: boolean
  performance_accessed: boolean
  performance_complete: boolean
  completed: boolean
  reconciled: boolean
  blocked: boolean
  failed_or_rejected: boolean
  classification: string | null
  reason_codes: string[]
  budget_consumed: boolean
  budget_reservation_identity: string | null
  state_display_zh?: string
  classification_display_zh?: string | null
  stage?: string
  created_at?: string | null
  started_at?: string | null
  last_activity_at?: string | null
  finished_at?: string | null
  error_code?: string | null
  error_message?: string | null
  recovery_status?: string | null
  start_intent_id?: string | null
}

export interface TrialListResponse {
  objective_id: string
  page: number
  page_size: number
  total: number
  items: TrialSummaryView[]
}

export interface TrialDetailView extends Provenance {
  trial_id: string
  summary: JsonRecord
  human_authorized: boolean
  performance: JsonRecord
  statistical: JsonRecord
  effective_decision: JsonRecord
  artifact_lineage: JsonRecord
}

export interface TrialReconciliationPreview {
  schema_version: string
  status: string
  available: boolean
  objective_id: string
  trial_id: string
  candidate_id?: string
  candidate_hash?: string
  trial_status?: string
  performance_accessed?: boolean
  performance_complete?: boolean
  budget_reservation_status?: string
  reason_code: string
  reason_zh: string
  requires_confirmation: boolean
  preview_hash?: string
  confirmation_token?: string
  safety_evidence?: {
    performance_rerun: boolean
    new_trial: boolean
    provisional_evidence_present: boolean
    ledger_ref: string
    budget_ref: string
  }
}

export interface SearchBudgetView extends Provenance {
  objective_id: string
  used: number
  total: number
  remaining: number
  reserved: number
  registry_identity: string
  registry_head_hash: string
  buckets: JsonRecord[]
  trial_consumption: JsonRecord[]
}

export interface ShadowDailyView extends Provenance {
  objective_id: string
  available: boolean
  objective_scoped: boolean
  availability_reason_zh: string
  trade_date: string | number | null
  readiness: string
  scan_status: string
  strategies_scanned: number
  raw_signal_count: number
  unique_candidate_count: number
  observation_pool: JsonRecord[]
  pit_state: string
  tradability_state: string
  next_legal_session: string | number | null
  machine_marker: string
  warning_zh: string
}

export interface DataHealthView extends Provenance {
  objective_id: string
  overall_status: string
  sources: JsonRecord[]
}

export interface ReportIndexView extends Provenance {
  objective_id: string
  reports: JsonRecord[]
}

export interface ResearchEvolutionView extends Provenance {
  objective_id: string
  available: boolean
  report: JsonRecord | null
  context: JsonRecord | null
  landscape: JsonRecord | null
  display: JsonRecord
}

export interface ResearchEvolutionProposalView extends Provenance {
  objective_id: string
  available: boolean
  proposal: JsonRecord | null
  coverage: JsonRecord | null
  proposals?: JsonRecord[]
  display: JsonRecord
}

export interface ResearchEvolutionAIDesignView extends Provenance {
  objective_id: string
  available: boolean
  status: string
  input: JsonRecord
  design: JsonRecord | null
  governance: JsonRecord
  approval: JsonRecord
  source_refs: JsonRecord
  display: JsonRecord
  output_path?: string | null
  outcome_blind?: boolean
  performance_data_loaded?: boolean
  outcome_fields_available?: boolean
}

export interface CandidateProposalView extends Provenance {
  objective_id: string
  available: boolean
  status: string
  proposal: JsonRecord | null
  proposals: JsonRecord[]
  governance: JsonRecord
  freeze_preview: JsonRecord | null
  freeze_record?: JsonRecord | null
  candidate_registry?: JsonRecord | null
  materialization?: JsonRecord
  display: JsonRecord
  output_path?: string | null
  outcome_blind?: boolean
  performance_data_loaded?: boolean
  outcome_fields_available?: boolean
}

export interface CandidateProposalReviewResult extends JsonRecord {
  schema_version: string
  review: JsonRecord
  proposal: JsonRecord
  freeze_preview: JsonRecord | null
  idempotent: boolean
  message_zh?: string
}

export interface CandidateProposalFreezeResult extends JsonRecord {
  schema_version: string
  freeze: JsonRecord
  candidate: JsonRecord
  proposal: JsonRecord
  idempotent: boolean
  message_zh?: string
}

export interface CandidateExecutableMaterializationResult extends JsonRecord {
  materialization_state?: string
  effective_state?: string
  required_action?: string | null
  structural_preflight_ready?: boolean
  preview?: JsonRecord | null
  confirmation?: JsonRecord
  contract?: JsonRecord
  idempotent?: boolean
  message_zh?: string
}

export interface ObjectiveCreationPreview extends JsonRecord {
  proposal_id: string
  proposal_hash: string
  status: string
  target_objective_id: string
  target_name: string
  research_direction: string
  parent_proposal: JsonRecord
  parent_lineage: JsonRecord[]
  mechanism_coverage: JsonRecord
  budget_suggestion: JsonRecord
  multiple_testing_family_suggestion: JsonRecord
  generated_at: string
  preview_hash: string
  confirmation_token: string
  budget_consumed: boolean
  can_create_objective: boolean
  requires_human_confirmation: boolean
}

export interface ProposalGovernanceReviewResult extends JsonRecord {
  schema_version: string
  review: JsonRecord
  proposal: JsonRecord
  objective_creation_preview: ObjectiveCreationPreview | null
  idempotent: boolean
  message_zh?: string
}

export interface NoOutcomeHandoffView extends Provenance {
  objective_id: string
  handoff_type: string
  current_state: string
  reason_code: string | null
  reason_display_zh: string
  artifact_refs: string[]
  allowed_next_actions: string[]
  forbidden_actions: string[]
  current_candidate: JsonRecord
  remaining_frozen_candidates: number | null
  global_search_exhausted: boolean
  budget: JsonRecord
  performance_values_exposed: boolean
  outcome_blind: boolean
  display: JsonRecord
}

export interface OrchestratorStatusView {
  schema_version: string
  objective_id: string
  orchestrator_state: string
  orchestrator_state_zh: string
  daemon_state: string
  daemon_state_zh: string
  ai_status: string
  ai_status_zh: string
  current_candidate: JsonRecord | null
  current_trial: JsonRecord | null
  current_handoff: JsonRecord | null
  last_ai_invocation: JsonRecord | null
  retry_state: JsonRecord
  current_handoff_id: string | null
  current_ai_invocation_id: string | null
  terminal_reason: string | null
  terminal_reason_zh: string
  closeout_state: string
  governance_decision_state: string
  closeout_complete: boolean
  waiting_for_governance: boolean
  next_action: string
  next_action_zh?: string
  budget: JsonRecord
  research_counts: Record<string, number>
  last_transition: JsonRecord | null
  source_generated_at: string | null
  observed_at: string
  freshness_state: string
  stale?: boolean
}

export interface OrchestratorEventView {
  event_id: string
  event_type: string
  timestamp: string | null
  state?: string | null
  new_state?: string | null
  previous_state?: string | null
  reason_code?: string | null
  candidate_id?: string | null
  trial_id?: string | null
  closeout_id?: string | null
  submission_id?: string | null
  source: string
  objective_id: string
}

export interface OrchestratorEventsView {
  schema_version: string
  objective_id: string
  events: OrchestratorEventView[]
  limit: number
  source_generated_at: string | null
  observed_at: string
  freshness_state: string
  stale: boolean
  conflict: boolean
}

export interface AIStatusView {
  schema_version: string
  objective_id: string
  automatic_invocation_enabled: boolean
  ai_invocation_mode: string
  ai_invocation_mode_zh: string
  manual_handoff: JsonRecord
  background_ai_token_consumption: number
  state: string
  state_zh: string
  last_invocation: JsonRecord | null
  current_handoff: JsonRecord | null
  invocation_id: string | null
  handoff_id: string | null
  attempt: number
  max_attempts: number
  retry_state: JsonRecord
  recent_result: string
  candidate_count: number
  batch_validation_status: string
  auto_resume_status: string
  validation: {
    status: string
    stage: string | null
    started_at: string | null
    last_activity_at: string | null
    finished_at: string | null
    error: string | null
    stale: boolean
    process_alive: boolean
    process_id: number | null
  }
  runtime_progress: {
    status: string
    status_zh: string
    activity_zh: string
    process_id: number | null
    started_at: string | null
    last_stdout_at: string | null
    last_stderr_at: string | null
    last_progress_at: string | null
    manifest_detected_at: string | null
    process_exit_at: string | null
    timeout_type: string | null
    output_mode: string | null
    elapsed_seconds: number | null
    diagnostics_available: boolean
  }
  exact_once: JsonRecord
  no_outcome_isolation: JsonRecord
  real_codex_validation: JsonRecord
  freshness_state: string
  source_generated_at: string | null
  observed_at: string
}

export interface ManualAIHandoffView {
  schema_version: string
  objective_id: string
  mode: string
  mode_zh: string
  state: string
  state_zh: string
  handoff_id: string | null
  invocation_id: string | null
  task_dir: string | null
  prompt_path: string | null
  instructions_path: string | null
  allowed_info_path: string | null
  result_path: string | null
  expected_filename: string
  task_created_at: string | null
  prompt_character_count: number
  prompt_token_estimate: number
  context_mode: string
  result_status: string
  result_status_zh: string
  result_reason_zh: string
  background_ai_token_consumption: number
  output_contract: JsonRecord
  allowed_actions_zh: string[]
  forbidden_actions_zh: string[]
  prompt_text: string
  instructions_text: string
  allowed_info: JsonRecord
  validation_status: string
  validation_status_zh: string
  validation_stage: string | null
  validation_stage_zh: string
  validation_started_at: string | null
  validation_last_activity_at: string | null
  validation_finished_at: string | null
  validation_error: string | null
  validation_stale: boolean
  validation_process_alive: boolean
  manual_handoff: JsonRecord
  manual_handoff_id?: string | null
  task_purpose?: string
  current_round?: string
  ai_design_policy?: JsonRecord
  planned_confirmation_candidate_limit?: number
  required_human_action?: string
  source_generated_at: string | null
  observed_at: string
  freshness_state: string
}

export interface AIResearchTaskSummary {
  handoff_id: string
  objective_id: string
  invocation_id: string | null
  task_type: string
  mode: string
  mode_zh: string
  task_dir: string
  result_path: string
  expected_filename: string
  created_at: string
  updated_at: string
  prompt_character_count: number
  prompt_token_estimate: number
  candidate_count: number
  result_status: string
  result_status_zh: string
  result_reason_zh: string
  ingestion_status: string
  ingestion_status_zh: string
  background_ai_token_consumption: number
  manual_handoff_id?: string
  task_purpose?: string
  current_round?: string
  ai_design_policy?: JsonRecord
  planned_confirmation_candidate_limit?: number
  validation_status?: string
  validation_stage?: string | null
  validation_started_at?: string | null
  validation_last_activity_at?: string | null
  validation_finished_at?: string | null
  validation_error?: string | null
  validation_stale?: boolean
}

export interface AIResearchTaskListResponse {
  schema_version: string
  objective_id: string
  page: number
  page_size: number
  total: number
  items: AIResearchTaskSummary[]
  source_generated_at: string
  observed_at: string
  freshness_state: string
}

export interface AIInvocationModeView {
  schema_version: string
  ai_invocation_mode: string
  ai_invocation_mode_zh: string
  source: string
  config_path: string
  background_ai_token_consumption: number | null
  message_zh?: string
}

export interface CloseoutView {
  schema_version: string
  objective_id: string
  title_zh: string
  closeout_id: string | null
  terminal_reason: string | null
  terminal_reason_zh: string
  terminal_state_before: string | null
  terminal_state_after: string | null
  budget: JsonRecord
  trial_inventory: JsonRecord
  research_counts: Record<string, number>
  promising_candidates: JsonRecord[]
  unevaluated_candidates: { count: number; candidate_ids: string[]; classification: string; explanation_zh: string }
  robust_alpha_established: boolean
  robust_alpha_display_zh: string
  multiple_testing: JsonRecord
  exact_once: JsonRecord
  automatic_reconciliation: JsonRecord
  final_test_access: JsonRecord
  prospective: string
  real_order: string
  governance_required: boolean
  report: JsonRecord
  source_generated_at: string
  observed_at: string
  freshness_state: string
  stale: boolean
  conflict: boolean
}

export interface GovernanceChoice {
  choice: string
  label_zh: string
  consequence_zh: string
  creates_new_objective: boolean
  requires_new_budget: boolean
  final_test_access_zh: string
  real_order_zh: string
}

export interface GovernanceDecisionView {
  schema_version: string
  objective_id: string
  decision_id: string | null
  decision_mode?: string | null
  reason: string | null
  choices: GovernanceChoice[]
  structural_governance?: {
    status: string
    status_zh: string
    decision_mode: string
    candidate_id: string
    candidate_hash: string
    structural: JsonRecord
    choices: JsonRecord[]
    next_action_zh: string
    human_explanation_zh: string
    final_test_access: JsonRecord
    prospective: string
    real_order: string
    predictive_trials_created: number
    performance_access: number
    reconciliation_ref?: string | null
    budget_snapshot?: JsonRecord
    trial_created?: boolean
    budget_reserved?: boolean
    budget_used_delta?: number
    next_action?: string
    available?: boolean
    latest_decision?: JsonRecord | null
  } | null
  current_terminal_summary: JsonRecord
  forbidden_automatic_actions: string[]
  backend_level: string
  execution_capability_zh: string
  submission: JsonRecord | null
  new_objective_created: boolean
  new_budget_created: boolean
  final_test_access: JsonRecord
  prospective: string
  real_order: string
  source_generated_at: string
  observed_at: string
  freshness_state: string
  stale: boolean
  conflict: boolean
}

export interface GovernanceActionChoice {
  action: string
  choice: string
  label_zh: string
  creates_new_objective: boolean
  requires_new_budget: boolean
  requires_explicit_confirmation: boolean
  description_zh: string
}

export interface GovernancePreviewCatalog {
  schema_version: string
  objective_id: string
  decision_id: string | null
  governance_execution_level: string
  backend_level: string
  execution_capability_zh: string
  source_state: string
  source_budget: JsonRecord
  choices: GovernanceActionChoice[]
  eligible_parent_candidates: ParentCandidateIdentityRef[]
  current_terminal_summary: JsonRecord
  final_test_status: JsonRecord
  prospective_status: string
  real_order_status: string
  current_real_objective_untouched: boolean
  created_at: string
}

export interface ParentCandidateIdentityRef {
  candidate_id: string
  candidate_hash: string
  family_id: string
  mechanism: string
}

export interface GovernancePreviewRequest extends Record<string, unknown> {
  action: string
  execution_mode: string
  selected_parent_candidate_id?: string
  selected_parent_candidate_hash?: string
}

export interface GovernanceExecutionPreview {
  schema_version: string
  decision_id: string
  source_objective_id: string
  source_objective_hash: string
  source_governance_hash: string
  source_budget_head_hash: string
  source_state: string
  governance_action: string
  execution_mode: string
  selected_parent_candidate_id: string | null
  selected_parent_candidate_hash: string | null
  proposed_new_objective_id: string | null
  proposed_new_objective_identity: JsonRecord
  parent_lineage: JsonRecord[]
  parent_candidate_identity_refs: ParentCandidateIdentityRef[]
  research_purpose: string
  mechanism_scope: string[]
  allowed_factor_scope: string[]
  no_outcome_policy: JsonRecord
  candidate_eligibility: JsonRecord
  budget_proposal: JsonRecord
  multiple_testing_family: JsonRecord
  stop_conditions: JsonRecord
  final_test_status: JsonRecord
  prospective_status: string
  real_order_status: string
  generated_at: string
  preview_hash: string
  confirmation_token: string
}

export interface GovernanceExecutionReceipt {
  schema_version: string
  execution_id: string
  decision_id: string
  preview_hash: string
  source_objective_id: string
  new_objective_id: string | null
  new_budget_id: string | null
  multiple_testing_family_id: string | null
  lineage_refs: string[]
  artifact_graph_refs: string[]
  created_at: string
  result_state: string
  execution_mode: string
  idempotency_key: string
  idempotent?: boolean
  message_zh?: string
  orchestrator_activation?: JsonRecord
}

export interface OperationAction {
  action: string
  available: boolean
  requires_confirmation: boolean
  label_zh: string
  description_zh: string
}

export interface OperationsView {
  schema_version: string
  objective_id: string
  state: string
  state_zh: string
  terminal: boolean
  terminal_reason: string | null
  terminal_reason_zh: string
  actions: OperationAction[]
  localhost_only: boolean
  shell_execution_exposed: boolean
  source_generated_at: string | null
  observed_at: string
}

export interface OperationResult extends OperationsView {
  action: string
  acknowledged: boolean
  idempotent: boolean
  message_zh: string
}
