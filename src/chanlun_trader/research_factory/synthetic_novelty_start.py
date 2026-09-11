"""显式合成新流程：在既有启动门禁之外绑定已确认的新颖性快照。"""
from dataclasses import replace

from .predictive_trial_start import PredictiveTrialStartServiceV1
from .synthetic_novelty import SCHEMA, SyntheticNoveltyBindingServiceV1
from .common import stable_hash


class SyntheticNoveltyTrialStartServiceV1(PredictiveTrialStartServiceV1):
    _supports_synthetic_novelty = True

    def __init__(self, root, policy, confirmation_id, **kwargs):
        self.novelty = SyntheticNoveltyBindingServiceV1(root)
        self.novelty._authorize(policy)
        self.confirmation_id = self.novelty._identifier(confirmation_id)
        self.novelty.historical_confirmation(self.confirmation_id)
        self.execution_policy = policy
        kwargs.setdefault("auto_run", False)
        super().__init__(root, **kwargs)

    def _load_intents(self, objective_id):
        intents = super()._load_intents(objective_id)
        for intent in intents.values():
            if (intent.get("synthetic_flow_version") != SCHEMA
                    or intent.get("synthetic_novelty_confirmation") != self.confirmation_id
                    or intent.get("test_confirmation") is not True):
                raise ValueError("NOVELTY_LEGACY_OR_DIFFERENT_INTENT")
            if intent.get("intent_hash") != stable_hash({k: v for k, v in intent.items() if k != "intent_hash"}):
                raise ValueError("NOVELTY_INTENT_HASH_INVALID")
        return intents

    def _preview_plan(self, snapshot):
        plan = super()._preview_plan(snapshot)
        if snapshot.candidate is None:
            raise ValueError("NOVELTY_FROZEN_CANDIDATE_REQUIRED")
        with self.novelty.performance_boundary(self.confirmation_id, snapshot.candidate_id,
                                               snapshot.candidate.content_hash) as evidence:
            plan["synthetic_novelty"] = evidence
            plan["synthetic_novelty_snapshot"] = self.novelty.historical_confirmation(self.confirmation_id)[0]["snapshot"]
        plan["synthetic_flow_version"] = SCHEMA
        return plan

    def _preview_payload(self, snapshot, *, require_eligible):
        result = super()._preview_payload(snapshot, require_eligible=require_eligible)
        result["schema_version"] = "synthetic-novelty-trial-start-preview-v1"
        result["test_confirmation_required"] = True
        return result

    def _put_intent(self, objective_id, intents, intent):
        existing = intents.get(str(intent["intent_id"]))
        if existing is not None and existing.get("synthetic_novelty_confirmation") != self.confirmation_id:
            raise ValueError("NOVELTY_LEGACY_OR_DIFFERENT_INTENT")
        if intent.get("synthetic_novelty_confirmation", self.confirmation_id) != self.confirmation_id:
            raise ValueError("NOVELTY_INTENT_BINDING_CONFLICT")
        return super()._put_intent(objective_id, intents, {**intent,
            "synthetic_flow_version": SCHEMA, "synthetic_novelty_confirmation": self.confirmation_id,
            "test_confirmation": True})

    def _validate_existing_intent(self, intent, **kwargs):
        super()._validate_existing_intent(intent, **kwargs)
        if intent.get("synthetic_flow_version") != SCHEMA or intent.get("synthetic_novelty_confirmation") != self.confirmation_id:
            raise ValueError("NOVELTY_LEGACY_OR_DIFFERENT_INTENT")

    def _candidate_work(self, snapshot, intent):
        if intent.get("synthetic_flow_version") != SCHEMA:
            raise ValueError("NOVELTY_LEGACY_INTENT_NOT_SUPPORTED")
        candidate = super()._candidate_work(snapshot, intent)
        return replace(candidate, metadata={**candidate.metadata,
            "synthetic_flow_version": SCHEMA,
            "synthetic_novelty_confirmation": intent["synthetic_novelty_confirmation"]})

    def confirm(self, objective_id, body):
        self.novelty._authorize(self.execution_policy)
        return super().confirm(objective_id, body)

    def confirm_resume(self, objective_id, body):
        self.novelty._authorize(self.execution_policy)
        return super().confirm_resume(objective_id, body)
