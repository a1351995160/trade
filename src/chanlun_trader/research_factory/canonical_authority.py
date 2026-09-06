"""Canonical authority contract for Research Factory reconciliation.

This module is deliberately declarative.  It does not load, repair, or
advance any research artifact.  The reconciliation service uses this contract
to keep authority facts separate from governance facts and runtime projections.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CANONICAL_AUTHORITY_SCHEMA_VERSION = "canonical-authority-contract-v1"


@dataclass(frozen=True)
class CanonicalAuthorityEntryV1:
    """One artifact family and its authority/projection boundary."""

    artifact: str
    authority_role: str
    authority_source: str
    projections: tuple[str, ...]
    identity_keys: tuple[str, ...]
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "authority_role": self.authority_role,
            "authority_source": self.authority_source,
            "projections": list(self.projections),
            "identity_keys": list(self.identity_keys),
            "rule": self.rule,
        }


@dataclass(frozen=True)
class CanonicalAuthorityContractV1:
    """Versioned, machine-readable authority map.

    ``entries`` is the authoritative representation of this Python contract;
    ``to_dict`` also exposes a name-indexed view for reports and callers that
    need to inspect one boundary without knowing the tuple layout.
    """

    schema_version: str = CANONICAL_AUTHORITY_SCHEMA_VERSION
    contract_id: str = "CANONICAL_AUTHORITY_AND_OBJECTIVE_RECONCILIATION_V1"
    principles: tuple[str, ...] = (
        "Objective JSON is an objective definition/creation fact; lifecycle_state and next_action are not final lifecycle truth by themselves.",
        "AI_RESEARCH_DESIGN_PROPOSAL.json is the AI design fact; AI_DESIGN_READY does not prove approval, and only the immutable AI Design Approval Receipt can authorize Candidate Proposal generation.",
        "Objective lifecycle fields and Daemon/Orchestrator projections are never AI Design approval authority.",
        "Candidate Proposal is a governance fact, Candidate Freeze Receipt is governance authority, and Candidate Registry is inventory evidence; none is executable contract authority.",
        "DurableFrozenCandidateContractV1 is the durable execution artifact, but the contract alone is insufficient executable authority; executable authority requires one valid contract plus one valid, identity-matching human materialization confirmation receipt.",
        "factory_trial_ledger.json is the canonical Trial lifecycle authority; daemon and orchestrator state are projections.",
        "SearchBudgetRegistryV1 is the canonical Budget authority; when multiple registries exist, authority requires an immutable or explicit canonical reference.",
        "ArtifactGraph is lineage/index evidence; a missing edge is repairable index drift and an identity/hash mismatch is a canonical conflict.",
        "Structural provider output is execution evidence; only the reconciled Structural Result is a research fact and Structural authority.",
        "Canonical facts flow one way into Daemon, Orchestrator, and Console projections; a stale projection never rewrites a canonical fact.",
        "Daemon and Orchestrator checkpoints are runtime projections and never replace canonical facts.",
    )
    entries: tuple[CanonicalAuthorityEntryV1, ...] = (
        CanonicalAuthorityEntryV1(
            "Objective Definition",
            "OBJECTIVE_DEFINITION_FACT",
            "data/research/research_factory/objectives/<objective_id>.json",
            ("objective.lifecycle_state", "objective.next_action", "daemon checkpoint"),
            ("objective_id", "objective identity hash", "creation receipt"),
            "Use the JSON for definition and creation facts only; reconcile dynamic lifecycle from downstream canonical evidence.",
        ),
        CanonicalAuthorityEntryV1(
            "AI Research Design",
            "AI_DESIGN_FACT",
            "reports/research_evolution/ai_design/<objective_id>/AI_RESEARCH_DESIGN_PROPOSAL.json",
            ("AI_RESEARCH_DESIGN_STATE.json", "console AI design view"),
            ("objective_id", "design_id", "design_hash"),
            "AI_DESIGN_READY means a durable proposal exists, not that a human approved it.",
        ),
        CanonicalAuthorityEntryV1(
            "AI Design Approval",
            "AI_DESIGN_APPROVAL_AUTHORITY",
            "reports/research_evolution/ai_design/<objective_id>/AI_DESIGN_APPROVAL_RECEIPT.json",
            ("AI_RESEARCH_DESIGN_STATE.json", "Objective lifecycle", "daemon/orchestrator state", "console AI design view"),
            ("objective_id", "design_id", "design_hash", "approval_id"),
            "Only an immutable, integrity-checked receipt bound to the current design hash and source context can authorize Candidate Proposal generation; lifecycle fields and runtime projections cannot replace it.",
        ),
        CanonicalAuthorityEntryV1(
            "Candidate Proposal Governance",
            "CANDIDATE_PROPOSAL_FACT",
            "CANDIDATE_PROPOSAL.json plus reviews/freeze preview",
            ("candidate proposal state", "console candidate view"),
            ("objective_id", "proposal_id", "candidate_id", "candidate_hash", "proposal_hash"),
            "Proposal and review records prove governance progress only.",
        ),
        CanonicalAuthorityEntryV1(
            "Candidate Governance Freeze",
            "CANDIDATE_GOVERNANCE_AUTHORITY",
            "CANDIDATE_FREEZE_RECEIPT.json plus CANDIDATE_REGISTRY.json",
            ("candidate governance state", "console candidate view"),
            ("objective_id", "candidate_id", "candidate_hash", "freeze_id"),
            "A governance freeze is separate from executable contract materialization.",
        ),
        CanonicalAuthorityEntryV1(
            "Candidate Registry",
            "CANDIDATE_INVENTORY_EVIDENCE",
            "data/research/research_factory/candidates/<objective_id>/CANDIDATE_REGISTRY.json",
            ("candidate inventory", "console candidate view"),
            ("objective_id", "candidate_id", "candidate_hash", "entry_hash"),
            "The light Registry records governance/inventory evidence only; it is never a Structural provider input.",
        ),
        CanonicalAuthorityEntryV1(
            "Executable Frozen Candidate Contract",
            "EXECUTABLE_CANDIDATE_AUTHORITY",
            "durable_frozen_candidate_contracts.json / DurableFrozenCandidateContractV1",
            ("materialization confirmation", "daemon contract cache", "orchestrator snapshot", "Structural provider input"),
            ("objective_id", "candidate_id", "candidate_hash", "contract identity", "materialization confirmation identity"),
            "Durable Contract alone is not executable authority. Require one unique matching DurableFrozenCandidateContractV1 that passes from_dict and provider_candidate_payload, plus one valid identity-matching Executable Materialization Confirmation Receipt.",
        ),
        CanonicalAuthorityEntryV1(
            "Executable Materialization Confirmation",
            "HUMAN_EXECUTABLE_MATERIALIZATION_APPROVAL_AUTHORITY",
            "reports/research_candidates/proposals/<objective_id>/EXECUTABLE_MATERIALIZATION_CONFIRMATION.json",
            ("executable candidate state", "Objective reconciliation", "Structural readiness"),
            ("objective_id", "proposal_id", "preview_id", "preview_hash", "candidate_id", "candidate_hash", "durable_contract_hash", "ai_design_id", "ai_design_hash", "ai_design_approval_hash", "source_context_id", "source_context_hash", "reviewer", "confirmed_at", "idempotency_key", "receipt_hash"),
            "An immutable receipt is human confirmation evidence, not a contract substitute. It becomes executable authority only when its identity and hashes match the current immutable Preview and Durable Contract.",
        ),
        CanonicalAuthorityEntryV1(
            "Structural Provider Raw Result",
            "STRUCTURAL_EXECUTION_EVIDENCE",
            "explicit Structural provider execution evidence",
            ("canonical Structural Result", "daemon structural state", "orchestrator readiness"),
            ("objective_id", "candidate_id", "candidate_hash", "provider payload identity", "data identity", "manifest identity", "policy identity"),
            "Provider output records what was executed; it is not the reconciled research fact and cannot authorize Predictive validation.",
        ),
        CanonicalAuthorityEntryV1(
            "Structural Reconciliation",
            "STRUCTURAL_RESULT_AUTHORITY",
            "reports/research_daemon/<objective_id>/structural_preflight_reconciliation_canonical_v1.json",
            ("daemon structural state", "orchestrator readiness", "Console read model"),
            ("objective_id", "candidate_id", "candidate_hash", "durable_contract_hash", "provider payload identity", "data identity", "manifest identity", "policy identity", "result_hash"),
            "Only the identity-bound canonical reconciled Structural Result can establish PASS, STRUCTURAL_BLOCKED, or ENGINEERING_BLOCKED.",
        ),
        CanonicalAuthorityEntryV1(
            "Predictive Authorization",
            "PREDICTIVE_AUTHORIZATION_AUTHORITY",
            "durable predictive authorization receipt",
            ("daemon predictive state", "orchestrator governance view"),
            ("objective_id", "candidate_id", "candidate_hash", "authorization_id"),
            "Do not infer authorization from structural PASS or a runtime checkpoint.",
        ),
        CanonicalAuthorityEntryV1(
            "Trial Lifecycle",
            "TRIAL_LIFECYCLE_AUTHORITY",
            "data/research/research_factory/batches/*/factory_trial_ledger.json",
            ("daemon checkpoint", "orchestrator checkpoint", "trial registry"),
            ("objective_id", "trial_id", "candidate_id", "candidate_hash", "event_type", "terminal status"),
            "Reconcile append-only events by identity and terminal status; never overwrite by timestamp.",
        ),
        CanonicalAuthorityEntryV1(
            "Budget",
            "SEARCH_BUDGET_AUTHORITY",
            "data/research/research_factory/batches/*/search_budget_registry.json / SearchBudgetRegistryV1",
            ("daemon budget view", "orchestrator budget view"),
            ("objective_id", "registry head hash", "reservation identity", "bucket counters"),
            "Enumerate every matching registry and select only through an immutable governance/objective receipt or an explicit canonical reference.",
        ),
        CanonicalAuthorityEntryV1(
            "Validation/Final Adjudication",
            "VALIDATION_FINAL_ADJUDICATION_FACT",
            "durable validation and final adjudication artifacts",
            ("trial record", "daemon/orchestrator status"),
            ("objective_id", "trial_id", "candidate_id", "decision_id", "decision hash"),
            "Validation and final decisions are evidence; runtime state cannot replace them.",
        ),
        CanonicalAuthorityEntryV1(
            "Lineage",
            "LINEAGE_INDEX_EVIDENCE",
            "ArtifactGraph plus immutable proposal/objective lineage",
            ("console lineage view", "daemon canonical_refs", "orchestrator snapshot"),
            ("objective_id", "candidate_id", "trial_id", "payload hash", "edge identity"),
            "Missing edges are repairable index drift; identity/hash conflict is a canonical conflict. Never clean or delete orphan artifacts here.",
        ),
        CanonicalAuthorityEntryV1(
            "Daemon",
            "RUNTIME_PROJECTION",
            "reports/research_daemon/<objective_id>/daemon_checkpoint.json and daemon_status.json",
            (),
            ("objective_id", "checkpoint hash", "state"),
            "Checkpoint and status are projections used for observability and recovery hints, not canonical lifecycle truth.",
        ),
        CanonicalAuthorityEntryV1(
            "Orchestrator",
            "RUNTIME_PROJECTION",
            "reports/research_orchestrator_v2/<objective_id>/orchestrator_checkpoint.json",
            (),
            ("objective_id", "checkpoint hash", "state"),
            "Orchestrator state is a runtime projection and must be reconciled against canonical Trial, Budget, and Contract evidence.",
        ),
        CanonicalAuthorityEntryV1(
            "Console",
            "READ_MODEL / PROJECTION",
            "ObjectiveReconciliationServiceV1 plus canonical authority artifacts",
            (),
            ("objective_id", "canonical effective state hash", "projection freshness"),
            "Console reads reconciled canonical state and may display projection drift; it never promotes a checkpoint into authority.",
        ),
    )

    def entry(self, artifact: str) -> CanonicalAuthorityEntryV1:
        for item in self.entries:
            if item.artifact == artifact:
                return item
        raise KeyError(artifact)

    def to_dict(self) -> dict[str, Any]:
        entries = [item.to_dict() for item in self.entries]
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "principles": list(self.principles),
            "authorities": entries,
            "authority_by_artifact": {item["artifact"]: item for item in entries},
            "read_only": True,
        }


CANONICAL_AUTHORITY_CONTRACT_V1 = CanonicalAuthorityContractV1()
CANONICAL_AUTHORITY_CONTRACT = CANONICAL_AUTHORITY_CONTRACT_V1


def canonical_authority_contract() -> dict[str, Any]:
    """Return a serializable copy of the authority contract."""

    return CANONICAL_AUTHORITY_CONTRACT_V1.to_dict()


__all__ = [
    "CANONICAL_AUTHORITY_CONTRACT",
    "CANONICAL_AUTHORITY_CONTRACT_V1",
    "CANONICAL_AUTHORITY_SCHEMA_VERSION",
    "CanonicalAuthorityContractV1",
    "CanonicalAuthorityEntryV1",
    "canonical_authority_contract",
]
