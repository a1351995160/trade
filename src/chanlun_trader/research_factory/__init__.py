"""AI Research Factory control-plane contracts."""

from .artifact_graph import ResearchArtifactGraphV1, add_frozen_contract_node_idempotent
from .canonical_authority import (
    CANONICAL_AUTHORITY_CONTRACT,
    CANONICAL_AUTHORITY_CONTRACT_V1,
    CanonicalAuthorityContractV1,
    CanonicalAuthorityEntryV1,
    canonical_authority_contract,
)
from .agent_backend import (
    AgentBackendError,
    AgentCallEstimateV1,
    AgentCallAuditLedgerV1,
    AgentCallBudgetV1,
    AgentCostGovernanceV1,
    AgentCostMetadataV1,
    AgentGovernancePolicyV1,
    AgentUsageMetadataV1,
    GovernedResearchAgentBackendV1,
    ResearchAgentBackendV1,
    ResearchAgentInputBuilderV1,
    ResearchAgentInputV1,
    ResearchProposalBatchV1,
    ResearchProposalV1,
    SyntheticResearchAgentBackendV1,
    TemplateResearchAgentBackendV1,
)
from .codex_backend import (
    CODEX_BACKEND_TYPE,
    CODEX_BACKEND_VERSION,
    CODEX_RESEARCH_AGENT_ENABLED,
    DEFAULT_RESEARCH_AGENT_BACKEND,
    CodexAgentCallEstimatorV1,
    CodexInvocationAdapterV1,
    CodexInvocationError,
    CodexInvocationRequestV1,
    CodexInvocationResultV1,
    CodexProposalParserV1,
    CodexResearchAgentBackendV1,
    CodexResearchPromptV1,
    ResearchAgentBackendConfigV1,
    SubprocessCodexExecutorV1,
    audit_codex_runtime,
    discover_codex_executable,
)
from .autonomous import AUTONOMOUS_RESEARCH_ENABLED, AutonomousResearchModeV1, AutonomousResearchRunnerV2, AutonomousRunExecutionV2
from .batch import FactoryResearchPlannerAdapterV1, ResearchBatchPlanV1
from .baseline import BaselineControlRegistryV1, BaselineRegistrationV1
from .budget import BudgetExhaustedError, BudgetLedgerMismatchError, SearchBudgetRegistryV1
from .context import NoOutcomePolicyDesignContextV1, NoOutcomeResearchContextV1, OutcomeBlindFieldPolicyV1, PerformanceBlindGuard, PerformanceLeakError
from .classification_integration import ClassificationCorrectionEventV1
from .failure_adapter import FailureKnowledgeAdapterV1, FailureKnowledgeSnapshotV1, FailureKnowledgeViewV1
from .history import CumulativeResearchHistoryV1
from .history import CumulativeResearchHistoryStoreV1
from .commit import CommitIdentityConflictError, DurableCommitLedgerV1
from .durability import (
    canonical_frozen_contract_identity,
    canonical_frozen_contract_identity_hash,
    classify_frozen_contract_identity,
    DurableFrozenCandidateContractRegistryV1,
    DurableFrozenCandidateContractV1,
    DurableTrialLedgerRecordV2,
    DurableTrialLedgerViewV2,
    reconstruct_cumulative_trial_view,
)
from .objective import RESEARCH_PRIORITY, ResearchObjectiveV1
from .orchestrator import (
    AIResearchFactoryOrchestratorV1,
    FactoryRunResultV1,
    RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1,
    SyntheticFactoryRuntimeV1,
    canonical_dependency_manifest,
)
from .state_machine import ResearchBatchState, ResearchBatchStateMachineV1
from .status import AutonomousResearchRunStatusV2, ResearchFactoryStatusV1
from .strategy_adapter import CandidateSimilarityV1, ResearchStrategyRegistryFacadeV1
from .trial_adapter import FactoryTrialRecordV1, ResearchFactoryTrialLedgerFacadeV1
from .diversity import DiversityFreezeResultV1, FamilyDiversityEnforcerV1, FamilyDiversityPolicyV1, InsufficientDiverseCandidatesError
from .novelty import CandidateNeighborhoodIndexV1, CandidateNoveltyGateV2, NoveltyDecisionV2
from .sample_feasibility import (
    BLOCKED_INSUFFICIENT_FEASIBILITY,
    CandidateSampleFeasibilityInputV1,
    CandidateSampleFeasibilityPreflightV1,
    CandidateSampleFeasibilityResultV1,
    ObservationPartitionStoreV1,
    FORBIDDEN_PERFORMANCE_SOURCES,
    PASS,
    PREFLIGHT_VERSION,
    SampleFeasibilityContractError,
    UNKNOWN,
    minimum_required_sample_count,
    performance_file_access_audit,
)
from .sample_feasibility_v2 import (
    BLOCKING,
    CandidateSampleFeasibilityPolicyV2,
    INFORMATIONAL,
    LOWER_BOUND_INTEGRITY_VERSION,
    LowerBoundIntegrityAuditV2,
    NON_BLOCKING_UNCERTAINTY,
    POLICY_ID as SAMPLE_FEASIBILITY_POLICY_V2_ID,
    POLICY_VERSION as SAMPLE_FEASIBILITY_POLICY_V2_VERSION,
    V1_PREDECESSOR_HASH,
    V1_PREDECESSOR_ID,
    audit_lower_bound_integrity_v2,
    classify_reason_codes,
)
from .real_sample_feasibility import RealSampleFeasibilityProviderV1
from .predictive_executor import CanonicalPredictiveExecutorV1
from .run_budget import AutonomousRunBudgetV1, RunBudgetContract, RunBudgetContractV1, RunBudgetUsageState, RunBudgetUsageStateV1, trial_budget_identity
from .run_state import AutonomousResearchRunV2, AutonomousRunState, AutonomousRunStoreV1, AutonomousRunTransitionError, deterministic_batch_id
from .autonomous_orchestrator_v2 import (
    AI_ALLOWED_TRIGGERS,
    AI_DENIED_TRIGGERS,
    AIBatchValidationError,
    AIBatchValidatorV2,
    AIInvocationError,
    AutonomousResearchOrchestratorV2,
    CanonicalOrchestratorRuntimeV2,
    CanonicalResearchSnapshotV2,
    CanonicalResearchStateReaderV2,
    CodexExecBatchInvokerV2,
    OrchestratorConfigV2,
    OrchestratorState,
    ResearchOrchestratorStatusView,
    SyntheticAutonomousResearchRuntimeV2,
    SyntheticCodexBatchInvokerV2,
    TerminalCloseoutServiceV2,
    build_codex_prompt_v2,
)
from .governance_execution import (
    EXECUTION_MODES,
    GovernanceExecutionError,
    ResearchGovernanceExecutionPreviewV1,
    ResearchGovernanceExecutionReceiptV1,
    ResearchGovernanceExecutionServiceV1,
    SUPPORTED_GOVERNANCE_ACTIONS,
)
from .research_evolution_manager import FAILURE_CATEGORIES, ResearchEvolutionError, ResearchEvolutionManager, classify_failures
from .research_evolution_proposal import (
    APPROVED,
    ALLOWED_RESEARCH_DIRECTIONS,
    CLOSED,
    CREATE_OBJECTIVE,
    CREATE_NEW_OBJECTIVE,
    CREATED,
    DEFAULT_RESEARCH_DIRECTIONS,
    EVOLUTION_ANALYZED,
    HUMAN_REVIEW_REQUIRED,
    MechanismCoverageRegistry,
    MechanismCoverageRegistryV1,
    OBJECTIVE_CREATION_READY,
    PROPOSAL_FILENAME,
    PROPOSAL_SCHEMA_VERSION,
    PROPOSAL_CREATED,
    READY_FOR_CONFIRMATION,
    REJECTED,
    ResearchEvolutionProposalError,
    ResearchEvolutionProposalManager,
    ResearchEvolutionProposalManagerV1,
)
from .research_proposal_governance import (
    BUDGET_POLICY_VERSION,
    ObjectiveCreationPreview,
    ObjectiveCreationPreviewV1,
    ProposalGovernanceError,
    ProposalGovernanceServiceV1,
    ResearchProposalGovernanceError,
    ResearchProposalGovernanceFlowV1,
    ResearchProposalGovernanceServiceV1,
)
from .research_evolution_ai_design import (
    AI_DESIGN_READY,
    AI_RESEARCH_DESIGN_FILENAME,
    AI_RESEARCH_DESIGN_INPUT_FILENAME,
    AI_RESEARCH_DESIGN_INPUT_SCHEMA_VERSION,
    AI_RESEARCH_DESIGN_SCHEMA_VERSION,
    AI_RESEARCH_DESIGN_STATE_FILENAME,
    AI_RESEARCH_DESIGN_STATE_SCHEMA_VERSION,
    AI_RESEARCH_DESIGN_VIEW_SCHEMA_VERSION,
    HUMAN_CONFIRM_AI_RESEARCH_DESIGN,
    NEED_AI_RESEARCH_DESIGN,
    OBJECTIVE_CREATED,
    EvolutionAIDesignInputV1,
    ResearchEvolutionAIDesignBackendV1,
    ResearchEvolutionAIDesignError,
    ResearchEvolutionAIDesignManager,
    ResearchEvolutionAIDesignManagerV1,
    ResearchEvolutionAIDesignService,
    ResearchEvolutionAIDesignServiceV1,
    TemplateEvolutionAIDesignBackendV1,
)
from .ai_design_approval import (
    AI_DESIGN_APPROVAL_APPROVED,
    AI_DESIGN_APPROVAL_REJECTED,
    AI_DESIGN_APPROVAL_RECEIPT_FILENAME,
    AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION,
    AI_DESIGN_APPROVAL_SCHEMA_VERSION,
    AI_DESIGN_APPROVED,
    AI_DESIGN_REJECTED,
    AIDesignApprovalError,
    AIDesignApprovalManagerV1,
    AIDesignApprovalReceiptV1,
    AIDesignApprovalService,
    AIDesignApprovalServiceV1,
    CANDIDATE_GENERATION_ALLOWED,
    GENERATE_AI_RESEARCH_DESIGN,
    GENERATE_CANDIDATE_PROPOSAL,
)
from .safe_runtime_context import (
    BUDGET_AUTHORITY_AMBIGUOUS,
    CONTEXT_BUILD_BLOCKED,
    DEFAULT_FRESHNESS_TTL_SECONDS,
    OUTCOME_FIELD_BLOCKED,
    SAFE_RUNTIME_CONTEXT_SCHEMA_VERSION,
    SAFE_RUNTIME_CONTEXT_VERSION,
    STALE_RUNTIME_CONTEXT,
    SafeRuntimeContextBuilderV1,
    SafeRuntimeContextError,
    SafeRuntimeContextV1,
    validate_context_freshness,
)
from .candidate_generation import (
    APPROVED as CANDIDATE_APPROVED,
    CANDIDATE_FREEZE_GOVERNANCE_FILENAME,
    CANDIDATE_FREEZE_PREVIEW_FILENAME,
    CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION,
    CANDIDATE_FREEZE_RECEIPT_FILENAME,
    CANDIDATE_FREEZE_SCHEMA_VERSION,
    CANDIDATE_FREEZE_READY,
    CANDIDATE_REGISTRY_FILENAME,
    CANDIDATE_REGISTRY_SCHEMA_VERSION,
    CANDIDATE_PROPOSAL_FILENAME,
    CANDIDATE_PROPOSAL_INPUT_FILENAME,
    CANDIDATE_PROPOSAL_INPUT_SCHEMA_VERSION,
    CANDIDATE_PROPOSAL_READY,
    CANDIDATE_PROPOSAL_SCHEMA_VERSION,
    CANDIDATE_PROPOSAL_STATE_FILENAME,
    CANDIDATE_PROPOSAL_STATE_SCHEMA_VERSION,
    CANDIDATE_PROPOSAL_VIEW_SCHEMA_VERSION,
    CANDIDATE_REVIEW_SCHEMA_VERSION,
    CANDIDATE_REVIEWS_FILENAME,
    CLOSED as CANDIDATE_CLOSED,
    CandidateFreezePreview,
    CandidateFreezePreviewV1,
    CandidateGenerationError,
    CandidateGenerationGovernanceServiceV1,
    CandidateGenerationInputV1,
    CandidateGenerationManager,
    CandidateGenerationManagerV1,
    CandidateGenerationServiceV1,
    CandidateProposalInputV1,
    CANDIDATE_GOVERNANCE_FROZEN,
    CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW,
    DUPLICATE_MECHANISM_REJECTED,
    GENERATED as CANDIDATE_GENERATED,
    HUMAN_CONFIRM_CANDIDATE_FREEZE,
    HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION,
    HUMAN_REVIEW_REQUIRED as CANDIDATE_HUMAN_REVIEW_REQUIRED,
    NEED_CANDIDATE_PROPOSAL,
    FREEZE_PREVIEW_READY,
    FROZEN,
    NEW_CANDIDATE,
    READY_FOR_STRUCTURAL_PREFLIGHT,
    REJECTED as CANDIDATE_REJECTED,
)
from .candidate_executable_materialization import (
    CANONICAL_CANDIDATE_IDENTITY_CONFLICT,
    CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION,
    CANDIDATE_SEMANTIC_DRIFT,
    CandidateExecutableMaterializationBridgeV1,
    CandidateExecutableMaterializationError,
    CandidateExecutableMaterializationManager,
    CandidateExecutableMaterializationManagerV1,
    CandidateExecutableMaterializationServiceV1,
    EXECUTABLE_CONTRACT_INVALID,
    EXECUTABLE_CANDIDATE_FROZEN,
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME,
    EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION,
    EXECUTABLE_MATERIALIZATION_INCOMPLETE,
    EXECUTABLE_MATERIALIZATION_PREVIEW_FILENAME,
    EXECUTABLE_MATERIALIZATION_PREVIEW_READY,
    EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION,
    EXECUTABLE_MATERIALIZATION_RECEIPT_FILENAME,
    EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION,
    INTEGRITY_FAILURE,
    MATERIALIZATION_IDEMPOTENCY_CONFLICT,
    RUN_STRUCTURAL_PREFLIGHT,
    STALE_EXECUTABLE_MATERIALIZATION_PREVIEW,
)
__all__ = [
    "AIResearchFactoryOrchestratorV1", "AgentBackendError", "AgentCallEstimateV1", "AgentCallAuditLedgerV1", "AgentCallBudgetV1", "AgentCostGovernanceV1", "AgentCostMetadataV1", "AgentGovernancePolicyV1", "AgentUsageMetadataV1", "GovernedResearchAgentBackendV1", "AUTONOMOUS_RESEARCH_ENABLED", "AutonomousResearchModeV1", "AutonomousResearchRunnerV2", "AutonomousRunExecutionV2", "AutonomousRunBudgetV1", "AutonomousResearchRunV2", "AutonomousResearchRunStatusV2", "AutonomousRunState", "AutonomousRunStoreV1", "AutonomousRunTransitionError", "BudgetExhaustedError", "BudgetLedgerMismatchError",
    "BaselineControlRegistryV1", "BaselineRegistrationV1", "DurableFrozenCandidateContractRegistryV1", "DurableFrozenCandidateContractV1", "canonical_frozen_contract_identity", "canonical_frozen_contract_identity_hash", "classify_frozen_contract_identity",
    "CandidateSimilarityV1", "CumulativeResearchHistoryV1", "CumulativeResearchHistoryStoreV1", "CommitIdentityConflictError", "DurableCommitLedgerV1", "DurableTrialLedgerRecordV2", "DurableTrialLedgerViewV2", "reconstruct_cumulative_trial_view", "FactoryResearchPlannerAdapterV1",
    "ClassificationCorrectionEventV1",
    "FactoryRunResultV1", "FactoryTrialRecordV1", "FailureKnowledgeAdapterV1",
    "FailureKnowledgeSnapshotV1", "FailureKnowledgeViewV1", "NoOutcomePolicyDesignContextV1", "NoOutcomeResearchContextV1", "OutcomeBlindFieldPolicyV1", "PerformanceBlindGuard",
    "PerformanceLeakError", "RESEARCH_FACTORY_CANONICAL_DEPENDENCIES_V1", "RESEARCH_PRIORITY",
    "ResearchArtifactGraphV1", "add_frozen_contract_node_idempotent", "CanonicalAuthorityContractV1", "CanonicalAuthorityEntryV1", "CANONICAL_AUTHORITY_CONTRACT", "CANONICAL_AUTHORITY_CONTRACT_V1", "canonical_authority_contract", "ResearchBatchPlanV1", "ResearchBatchState", "ResearchBatchStateMachineV1",
    "ResearchFactoryStatusV1", "ResearchFactoryTrialLedgerFacadeV1", "ResearchObjectiveV1", "ResearchAgentBackendV1", "ResearchAgentInputBuilderV1", "ResearchAgentInputV1", "ResearchProposalBatchV1", "ResearchProposalV1", "TemplateResearchAgentBackendV1", "SyntheticResearchAgentBackendV1",
    "ResearchStrategyRegistryFacadeV1", "SearchBudgetRegistryV1", "SyntheticFactoryRuntimeV1", "RunBudgetContract", "RunBudgetContractV1", "RunBudgetUsageState", "RunBudgetUsageStateV1", "trial_budget_identity",
    "CandidateNeighborhoodIndexV1", "CandidateNoveltyGateV2", "NoveltyDecisionV2", "FamilyDiversityPolicyV1", "FamilyDiversityEnforcerV1", "DiversityFreezeResultV1", "InsufficientDiverseCandidatesError", "deterministic_batch_id", "canonical_dependency_manifest",
    "BLOCKED_INSUFFICIENT_FEASIBILITY", "CandidateSampleFeasibilityInputV1", "CandidateSampleFeasibilityPreflightV1", "CandidateSampleFeasibilityResultV1", "FORBIDDEN_PERFORMANCE_SOURCES", "ObservationPartitionStoreV1", "PASS", "PREFLIGHT_VERSION", "SampleFeasibilityContractError", "UNKNOWN", "minimum_required_sample_count", "performance_file_access_audit", "RealSampleFeasibilityProviderV1", "CanonicalPredictiveExecutorV1", "BLOCKING", "CandidateSampleFeasibilityPolicyV2", "INFORMATIONAL", "LOWER_BOUND_INTEGRITY_VERSION", "LowerBoundIntegrityAuditV2", "NON_BLOCKING_UNCERTAINTY", "SAMPLE_FEASIBILITY_POLICY_V2_ID", "SAMPLE_FEASIBILITY_POLICY_V2_VERSION", "V1_PREDECESSOR_HASH", "V1_PREDECESSOR_ID", "audit_lower_bound_integrity_v2", "classify_reason_codes",
    "CODEX_BACKEND_TYPE", "CODEX_BACKEND_VERSION", "CODEX_RESEARCH_AGENT_ENABLED", "DEFAULT_RESEARCH_AGENT_BACKEND", "CodexAgentCallEstimatorV1", "CodexInvocationAdapterV1", "CodexInvocationError", "CodexInvocationRequestV1", "CodexInvocationResultV1", "CodexProposalParserV1", "CodexResearchAgentBackendV1", "CodexResearchPromptV1", "ResearchAgentBackendConfigV1", "SubprocessCodexExecutorV1", "audit_codex_runtime", "discover_codex_executable",
    "AI_ALLOWED_TRIGGERS", "AI_DENIED_TRIGGERS", "AIBatchValidationError", "AIBatchValidatorV2", "AIInvocationError", "AutonomousResearchOrchestratorV2", "CanonicalOrchestratorRuntimeV2", "CanonicalResearchSnapshotV2", "CanonicalResearchStateReaderV2", "CodexExecBatchInvokerV2", "OrchestratorConfigV2", "OrchestratorState", "ResearchOrchestratorStatusView", "SyntheticAutonomousResearchRuntimeV2", "SyntheticCodexBatchInvokerV2", "TerminalCloseoutServiceV2", "build_codex_prompt_v2", "EXECUTION_MODES", "GovernanceExecutionError", "ResearchGovernanceExecutionPreviewV1", "ResearchGovernanceExecutionReceiptV1", "ResearchGovernanceExecutionServiceV1", "SUPPORTED_GOVERNANCE_ACTIONS",
    "FAILURE_CATEGORIES", "ResearchEvolutionError", "ResearchEvolutionManager", "classify_failures",
    "APPROVED", "ALLOWED_RESEARCH_DIRECTIONS", "CLOSED", "CREATE_OBJECTIVE", "CREATE_NEW_OBJECTIVE", "CREATED", "DEFAULT_RESEARCH_DIRECTIONS", "EVOLUTION_ANALYZED", "HUMAN_REVIEW_REQUIRED", "MechanismCoverageRegistry", "MechanismCoverageRegistryV1", "OBJECTIVE_CREATION_READY", "PROPOSAL_FILENAME", "PROPOSAL_SCHEMA_VERSION", "PROPOSAL_CREATED", "READY_FOR_CONFIRMATION", "REJECTED", "ResearchEvolutionProposalError", "ResearchEvolutionProposalManager", "ResearchEvolutionProposalManagerV1",
    "BUDGET_POLICY_VERSION", "ObjectiveCreationPreview", "ObjectiveCreationPreviewV1", "ProposalGovernanceError", "ProposalGovernanceServiceV1", "ResearchProposalGovernanceError", "ResearchProposalGovernanceFlowV1", "ResearchProposalGovernanceServiceV1",
    "AI_DESIGN_READY", "AI_RESEARCH_DESIGN_FILENAME", "AI_RESEARCH_DESIGN_INPUT_FILENAME", "AI_RESEARCH_DESIGN_INPUT_SCHEMA_VERSION", "AI_RESEARCH_DESIGN_SCHEMA_VERSION", "AI_RESEARCH_DESIGN_STATE_FILENAME", "AI_RESEARCH_DESIGN_STATE_SCHEMA_VERSION", "AI_RESEARCH_DESIGN_VIEW_SCHEMA_VERSION", "HUMAN_CONFIRM_AI_RESEARCH_DESIGN", "NEED_AI_RESEARCH_DESIGN", "OBJECTIVE_CREATED", "EvolutionAIDesignInputV1", "ResearchEvolutionAIDesignBackendV1", "ResearchEvolutionAIDesignError", "ResearchEvolutionAIDesignManager", "ResearchEvolutionAIDesignManagerV1", "ResearchEvolutionAIDesignService", "ResearchEvolutionAIDesignServiceV1", "TemplateEvolutionAIDesignBackendV1", "AI_DESIGN_APPROVAL_APPROVED", "AI_DESIGN_APPROVAL_REJECTED", "AI_DESIGN_APPROVAL_RECEIPT_FILENAME", "AI_DESIGN_APPROVAL_RESULT_SCHEMA_VERSION", "AI_DESIGN_APPROVAL_SCHEMA_VERSION", "AI_DESIGN_APPROVED", "AI_DESIGN_REJECTED", "AIDesignApprovalError", "AIDesignApprovalManagerV1", "AIDesignApprovalReceiptV1", "AIDesignApprovalService", "AIDesignApprovalServiceV1", "CANDIDATE_GENERATION_ALLOWED", "GENERATE_AI_RESEARCH_DESIGN", "GENERATE_CANDIDATE_PROPOSAL", "BUDGET_AUTHORITY_AMBIGUOUS", "CONTEXT_BUILD_BLOCKED", "DEFAULT_FRESHNESS_TTL_SECONDS", "OUTCOME_FIELD_BLOCKED", "SAFE_RUNTIME_CONTEXT_SCHEMA_VERSION", "SAFE_RUNTIME_CONTEXT_VERSION", "STALE_RUNTIME_CONTEXT", "SafeRuntimeContextBuilderV1", "SafeRuntimeContextError", "SafeRuntimeContextV1", "validate_context_freshness",
    "CANDIDATE_APPROVED", "CANDIDATE_CLOSED", "CANDIDATE_FREEZE_GOVERNANCE_FILENAME", "CANDIDATE_FREEZE_PREVIEW_FILENAME", "CANDIDATE_FREEZE_PREVIEW_SCHEMA_VERSION", "CANDIDATE_FREEZE_RECEIPT_FILENAME", "CANDIDATE_FREEZE_SCHEMA_VERSION", "CANDIDATE_FREEZE_READY", "CANDIDATE_GOVERNANCE_FROZEN", "CANDIDATE_REGISTRY_FILENAME", "CANDIDATE_REGISTRY_SCHEMA_VERSION", "CANDIDATE_GENERATED", "CANDIDATE_HUMAN_REVIEW_REQUIRED", "CANDIDATE_PROPOSAL_FILENAME", "CANDIDATE_PROPOSAL_INPUT_FILENAME", "CANDIDATE_PROPOSAL_INPUT_SCHEMA_VERSION", "CANDIDATE_PROPOSAL_READY", "CANDIDATE_PROPOSAL_SCHEMA_VERSION", "CANDIDATE_PROPOSAL_STATE_FILENAME", "CANDIDATE_PROPOSAL_STATE_SCHEMA_VERSION", "CANDIDATE_PROPOSAL_VIEW_SCHEMA_VERSION", "CANDIDATE_REJECTED", "CANDIDATE_REVIEW_SCHEMA_VERSION", "CANDIDATE_REVIEWS_FILENAME", "CandidateFreezePreview", "CandidateFreezePreviewV1", "CandidateGenerationError", "CandidateGenerationGovernanceServiceV1", "CandidateGenerationInputV1", "CandidateGenerationManager", "CandidateGenerationManagerV1", "CandidateGenerationServiceV1", "CandidateProposalInputV1", "CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW", "DUPLICATE_MECHANISM_REJECTED", "FREEZE_PREVIEW_READY", "FROZEN", "HUMAN_CONFIRM_CANDIDATE_FREEZE", "HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION", "NEED_CANDIDATE_PROPOSAL", "NEW_CANDIDATE", "EXECUTABLE_CANDIDATE_FROZEN", "READY_FOR_STRUCTURAL_PREFLIGHT", "RUN_STRUCTURAL_PREFLIGHT", "CANONICAL_CANDIDATE_IDENTITY_CONFLICT", "CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION", "CANDIDATE_SEMANTIC_DRIFT", "CandidateExecutableMaterializationBridgeV1", "CandidateExecutableMaterializationError", "CandidateExecutableMaterializationManager", "CandidateExecutableMaterializationManagerV1", "CandidateExecutableMaterializationServiceV1", "EXECUTABLE_CONTRACT_INVALID", "EXECUTABLE_MATERIALIZATION_CONFIRMATION_FILENAME", "EXECUTABLE_MATERIALIZATION_CONFIRMATION_SCHEMA_VERSION", "EXECUTABLE_MATERIALIZATION_INCOMPLETE", "EXECUTABLE_MATERIALIZATION_PREVIEW_FILENAME", "EXECUTABLE_MATERIALIZATION_PREVIEW_READY", "EXECUTABLE_MATERIALIZATION_PREVIEW_SCHEMA_VERSION", "EXECUTABLE_MATERIALIZATION_RECEIPT_FILENAME", "EXECUTABLE_MATERIALIZATION_SCHEMA_VERSION", "INTEGRITY_FAILURE", "MATERIALIZATION_IDEMPOTENCY_CONFLICT", "STALE_EXECUTABLE_MATERIALIZATION_PREVIEW",
    "STRUCTURAL_ENTRY_GATE_BLOCKED", "STRUCTURAL_EXECUTION_EVIDENCE_FILENAME", "STRUCTURAL_IDEMPOTENCY_CONFLICT", "STRUCTURAL_START_CONFIRMATION_REQUIRED", "StructuralEntryError", "StructuralEntryGateV1", "StructuralEntryServiceV1", "ProjectionReconciliationError", "ProjectionReconciliationServiceV1",
]


_LAZY_EXPORTS = {
    "STRUCTURAL_ENTRY_GATE_BLOCKED": ("structural_entry", "STRUCTURAL_ENTRY_GATE_BLOCKED"),
    "STRUCTURAL_EXECUTION_EVIDENCE_FILENAME": ("structural_entry", "STRUCTURAL_EXECUTION_EVIDENCE_FILENAME"),
    "STRUCTURAL_IDEMPOTENCY_CONFLICT": ("structural_entry", "STRUCTURAL_IDEMPOTENCY_CONFLICT"),
    "STRUCTURAL_START_CONFIRMATION_REQUIRED": ("structural_entry", "STRUCTURAL_START_CONFIRMATION_REQUIRED"),
    "StructuralEntryError": ("structural_entry", "StructuralEntryError"),
    "StructuralEntryGateV1": ("structural_entry", "StructuralEntryGateV1"),
    "StructuralEntryServiceV1": ("structural_entry", "StructuralEntryServiceV1"),
    "ProjectionReconciliationError": ("projection_reconciliation", "ProjectionReconciliationError"),
    "ProjectionReconciliationServiceV1": ("projection_reconciliation", "ProjectionReconciliationServiceV1"),
}


def __getattr__(name: str):
    lazy = _LAZY_EXPORTS.get(name)
    if lazy is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = lazy
    module = __import__(f"{__name__}.{module_name}", fromlist=[attribute_name])
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
