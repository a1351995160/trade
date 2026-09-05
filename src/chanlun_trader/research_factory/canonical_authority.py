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
        "AI_RESEARCH_DESIGN_PROPOSAL.json is an AI design fact; AI_DESIGN_READY does not prove AI design approval.",
        "Candidate proposal, freeze receipts, and candidate registry are governance/inventory facts, not executable frozen contract authority.",
        "Only one identity-matching DurableFrozenCandidateContractV1 that passes from_dict and provider_candidate_payload is executable frozen Candidate authority.",
        "factory_trial_ledger.json is the canonical Trial lifecycle authority; daemon and orchestrator state are projections.",
        "SearchBudgetRegistryV1 is the canonical Budget authority; when multiple registries exist, authority requires an immutable or explicit canonical reference.",
        "ArtifactGraph is lineage/index evidence; a missing edge is repairable index drift and an identity/hash mismatch is a canonical conflict.",
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
            "AI_DESIGN_APPROVAL_FACT",
            "durable AI design approval evidence/receipt",
            ("AI_RESEARCH_DESIGN_STATE.json", "daemon/orchestrator state"),
            ("objective_id", "design_id", "design_hash", "approval_id"),
            "Without durable approval evidence report AI_DESIGN_APPROVAL_EVIDENCE_MISSING and do not infer approval.",
        ),
        CanonicalAuthorityEntryV1(
            "Candidate Proposal Governance",
            "CANDIDATE_PROPOSAL_GOVERNANCE_FACT",
            "CANDIDATE_PROPOSAL.json plus reviews/freeze preview",
            ("candidate proposal state", "console candidate view"),
            ("objective_id", "proposal_id", "candidate_id", "candidate_hash", "proposal_hash"),
            "Proposal and review records prove governance progress only.",
        ),
        CanonicalAuthorityEntryV1(
            "Candidate Governance Freeze",
            "CANDIDATE_GOVERNANCE_FREEZE_FACT",
            "CANDIDATE_FREEZE_RECEIPT.json plus CANDIDATE_REGISTRY.json",
            ("candidate state", "daemon candidate state"),
            ("objective_id", "candidate_id", "candidate_hash", "freeze_id"),
            "A governance freeze is separate from executable contract materialization.",
        ),
        CanonicalAuthorityEntryV1(
            "Executable Frozen Candidate Contract",
            "EXECUTABLE_FROZEN_CANDIDATE_AUTHORITY",
            "durable_frozen_candidate_contracts.json / DurableFrozenCandidateContractV1",
            ("candidate registry", "daemon contract cache", "orchestrator snapshot"),
            ("objective_id", "candidate_id", "candidate_hash", "contract identity"),
            "Require one unique matching identity, DurableFrozenCandidateContractV1.from_dict PASS, and provider_candidate_payload PASS.",
        ),
        CanonicalAuthorityEntryV1(
            "Structural Preflight",
            "STRUCTURAL_PREFLIGHT_FACT",
            "canonical structural preflight reconciliation artifact",
            ("daemon structural state", "orchestrator readiness"),
            ("objective_id", "candidate_id", "candidate_hash", "reconciliation_id"),
            "A daemon state of STRUCTURAL_* is only a projection until the canonical reconciliation artifact is present.",
        ),
        CanonicalAuthorityEntryV1(
            "Predictive Authorization",
            "PREDICTIVE_AUTHORIZATION_FACT",
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
