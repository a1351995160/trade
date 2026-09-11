"""Canonical single-candidate predictive executor for the headless daemon.

This module is an adapter over the existing corrected engine and governance
components. It does not introduce a second TrialLedger, SearchBudget, or
validation policy. A candidate with an existing terminal canonical trial is
reconciled from that trial and is never rerun.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping

from .artifact_graph import ResearchArtifactGraphV1
from .baseline import BaselineControlRegistryV1
from .budget import SearchBudgetRegistryV1
from .common import jsonable, now_timestamp, stable_hash
from .durability import DurableFrozenCandidateContractRegistryV1
from .failure_adapter import FailureKnowledgeAdapterV1
from .execution_evidence import audit_microstructure, audit_budget_registration
from .real_runtime import _dataset_hash, _engine_hash, _load_cumulative_private_p_values
from .strategy_adapter import ResearchStrategyRegistryFacadeV1
from .trial_adapter import ResearchFactoryTrialLedgerFacadeV1
from .source_dependencies import SOURCE_ROOT, load_corrected_module
from chanlun_trader.research.strategy_validation import (
    FinalResearchAdjudicatorV1,
    PerformanceAccessGate,
    TrialRegistryV1,
    benjamini_hochberg,
    load_frozen_policy,
    small_capital_contract,
)
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2


LEGAL_CLASSIFICATIONS = {"RESEARCH_PASSED", "PROMISING", "WEAK", "REJECTED"}


class CanonicalPredictiveExecutorV1:
    """Bind daemon predictive execution to the existing canonical path."""

    def __init__(self, root: str | Path, objective_id: str, *, output_dir: str | Path | None = None):
        self.root = Path(root).resolve()
        self.objective_id = str(objective_id)
        self.output_dir = Path(output_dir) if output_dir else self.root / "reports/research_daemon" / self.objective_id / "predictive"

    def __call__(self, candidate: Any, *, recovery: bool = False) -> Any:
        return self.execute(candidate, recovery=recovery)

    def _json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _budget_path(self) -> Path:
        root = self.root / "data/research/research_factory/batches"
        matching = [
            path for path in sorted(root.glob("*/search_budget_registry.json"))
            if self._json(path, {}).get("objective_id") == self.objective_id
        ]
        if not matching:
            raise FileNotFoundError(f"canonical search budget not found for {self.objective_id}")
        return max(matching, key=lambda path: str(self._json(path, {}).get("updated_at", "")))

    def _contract_and_record(self, candidate: Any) -> tuple[Any, Any]:
        contract_path = self.root / str(candidate.contract_ref)
        registry = DurableFrozenCandidateContractRegistryV1.read(contract_path)
        matches = [item for item in registry.items() if item.candidate_id == str(candidate.candidate_id)]
        if len(matches) != 1:
            raise RuntimeError(f"CANONICAL_FROZEN_CONTRACT_NOT_UNIQUE:{candidate.candidate_id}")
        contract = matches[0]
        if contract.candidate_hash != str(candidate.candidate_hash):
            raise RuntimeError(f"CANONICAL_CANDIDATE_IDENTITY_MISMATCH:{candidate.candidate_id}")
        record = contract.reconstruct_candidate()
        if record.candidate.candidate_id != str(candidate.candidate_id) or record.preregistration_hash != str(candidate.candidate_hash):
            raise RuntimeError(f"CANONICAL_CANDIDATE_ROUND_TRIP_MISMATCH:{candidate.candidate_id}")
        if not record.phase4_eligible:
            raise RuntimeError(f"CANONICAL_CANDIDATE_NOT_VALIDATION_ELIGIBLE:{candidate.candidate_id}")
        return contract, record

    def _canonical_paths(self, budget_path: Path) -> dict[str, Path]:
        canonical_dir = budget_path.parent
        return {
            "budget": budget_path,
            "trial_registry": canonical_dir / "trial_registry.json",
            "factory_ledger": canonical_dir / "factory_trial_ledger.json",
            "strategy_registry": canonical_dir / "strategy_registry.json",
        }

    @staticmethod
    def _active_reservation(budget: SearchBudgetRegistryV1, reservation_id: str) -> bool:
        return reservation_id in budget.snapshot().get("active_reservations", {})

    @staticmethod
    def _result_from_record(record: Any, *, artifact_refs: tuple[str, ...] = ()) -> Any:
        from chanlun_trader.research_daemon import PredictiveResult

        return PredictiveResult(
            trial_id=str(record.trial_id),
            status=str(record.status),
            performance_accessed=bool(record.performance_accessed),
            classification=record.classification,
            reason_codes=tuple(record.reason_codes),
            artifact_refs=artifact_refs,
            details={"canonical_trial_reconciled": True, "performance_rerun": False},
        )

    def _existing_trial(self, ledger: ResearchFactoryTrialLedgerFacadeV1, candidate: Any) -> Any | None:
        matches = [item for item in ledger.latest().values() if item.candidate_id == str(candidate.candidate_id)]
        for item in matches:
            if item.candidate_hash != str(candidate.candidate_hash):
                raise RuntimeError(f"CANONICAL_TRIAL_CANDIDATE_HASH_CONFLICT:{candidate.candidate_id}")
        if len(matches) > 1:
            raise RuntimeError(f"CANONICAL_TRIAL_IDENTITY_AMBIGUOUS:{candidate.candidate_id}")
        return matches[0] if matches else None

    @staticmethod
    def _new_trial_id(batch_id: str, candidate: Any, trial_number: int = 1) -> str:
        """Derive a deterministic Trial identity scoped to one frozen Candidate."""
        if int(trial_number) < 1:
            raise ValueError("trial_number must be positive")
        return f"{batch_id}_{str(candidate.candidate_hash)}_T{int(trial_number):03d}"

    @staticmethod
    def _requested_trial_identity(candidate: Any) -> tuple[str | None, int | None]:
        metadata = candidate.metadata if isinstance(getattr(candidate, "metadata", None), Mapping) else {}
        trial_id = str(metadata.get("trial_id") or "").strip() or None
        raw_number = metadata.get("trial_number")
        trial_number: int | None = None
        if raw_number is not None:
            trial_number = int(raw_number)
            if trial_number < 1:
                raise RuntimeError("CANONICAL_TRIAL_NUMBER_INVALID")
        if trial_id and trial_number is None:
            suffix = trial_id.rsplit("_T", 1)[-1] if "_T" in trial_id else ""
            if suffix.isdigit():
                trial_number = int(suffix)
        return trial_id, trial_number

    @staticmethod
    def _budget_binding(snapshot: Mapping[str, Any], kind: str, preferred: str) -> str:
        keys = [str(item["key"]) for item in snapshot.get("buckets", ()) if str(item.get("kind")) == kind]
        if preferred in keys:
            return preferred
        if len(keys) == 1:
            return keys[0]
        raise RuntimeError(f"CANONICAL_BUDGET_BINDING_AMBIGUOUS:{kind}:{preferred}")

    def _artifact_refs_for_existing(self, candidate: Any) -> tuple[str, ...]:
        refs: list[str] = [str(candidate.contract_ref)]
        for path in sorted((self.root / "reports").glob("**/trial_manifest.json")):
            try:
                payload = self._json(path, {}) or {}
            except (OSError, ValueError):
                continue
            trials = payload.get("trials", ()) if isinstance(payload, Mapping) else ()
            if any(str(item.get("candidate_id")) == str(candidate.candidate_id) for item in trials if isinstance(item, Mapping)):
                refs.append(str(path.relative_to(self.root)).replace("\\", "/"))
        return tuple(dict.fromkeys(refs))

    def _load_policy(self, contract: Any) -> tuple[Any, str, Path]:
        identity = dict(contract.policy_identity)
        if str(identity.get("objective_id")) != self.objective_id:
            raise RuntimeError("CANONICAL_POLICY_OBJECTIVE_MISMATCH")
        if identity == {"objective_id": self.objective_id}:
            runtime_dir = self.root / "reports/research_daemon" / self.objective_id
            checkpoint_path = runtime_dir / "daemon_checkpoint.json"
            checkpoint = self._json(checkpoint_path, {}) or {}
            canonical_refs = checkpoint.get("canonical_refs") if isinstance(checkpoint.get("canonical_refs"), Mapping) else {}
            reconciliation_ref = canonical_refs.get("structural_reconciliation", {}).get("report_ref") if isinstance(canonical_refs.get("structural_reconciliation"), Mapping) else None
            reconciliation_path = self.root / str(reconciliation_ref) if reconciliation_ref else runtime_dir / "structural_preflight_reconciliation.json"
            if not reconciliation_path.is_file():
                reconciliation_path = runtime_dir / "structural_preflight_reconciliation.json"
            reconciliation = self._json(reconciliation_path, {}) or {}
            repaired = dict(reconciliation.get("repaired_structural_result") or {})
            details = dict(repaired.get("details") or {})
            v1 = dict(details.get("v1_result") or {})
            if (
                reconciliation.get("status") != "PASS"
                or reconciliation.get("candidate_id") != contract.candidate_id
                or reconciliation.get("candidate_hash") != contract.candidate_hash
                or repaired.get("status") != "PASS"
                or v1.get("policy_id") != "VALIDATION_DECISION_POLICY_V2"
            ):
                raise RuntimeError("CANONICAL_VALIDATION_POLICY_PIN_MISSING")
            policy_v2_path = self.root / "data/research/strategy_validation/validation_decision_policy_v2.json"
            policy, policy_hash = load_validation_decision_policy_v2(policy_v2_path)
            if policy_hash != str(v1.get("policy_hash")) or policy.policy_version != str(v1.get("policy_version")):
                raise RuntimeError("CANONICAL_VALIDATION_POLICY_PIN_MISMATCH")
            return policy, policy_hash, policy_v2_path
        policy_v2_path = self.root / "data/research/strategy_validation/validation_decision_policy_v2.json"
        if str(identity.get("policy_id")) == "VALIDATION_DECISION_POLICY_V2":
            policy, policy_hash = load_validation_decision_policy_v2(policy_v2_path)
            if policy_hash != str(identity.get("policy_hash")) or policy.policy_version != str(identity.get("policy_version")):
                raise RuntimeError("CANONICAL_VALIDATION_POLICY_PIN_MISMATCH")
            return policy, policy_hash, policy_v2_path
        policy_path = self.root / "data/research/strategy_validation/validation_policy.json"
        policy, policy_hash = load_frozen_policy(policy_path)
        if policy_hash != str(identity.get("policy_hash")) or policy.policy_version != str(identity.get("policy_version")):
            raise RuntimeError("CANONICAL_VALIDATION_POLICY_PIN_MISMATCH")
        return policy, policy_hash, policy_path

    def _prepare_inputs(self, policy: Any, record: Any, corrected: Any, factor_cache: Path, *, contract: Any | None = None) -> dict[str, Any]:
        from .caller_inputs import prepare_inputs

        if self.root.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(self.root):
            raise ValueError("R1_CALLER_SOURCE_INPUT_ROOT_OVERLAP")
        if contract is None:
            raise ValueError("R1_CALLER_FROZEN_CONTRACT_REQUIRED")
        if str(policy.policy_id) != "VALIDATION_DECISION_POLICY_V2":
            raise ValueError("R1_CALLER_POLICY_VERSION_NOT_VERIFIED")
        frozen_policy, _, _ = self._load_policy(contract)
        if frozen_policy.to_dict() != policy.to_dict():
            raise ValueError("R1_CALLER_POLICY_IDENTITY_MISMATCH")
        return prepare_inputs(self.root, policy, record, corrected, factor_cache, contract)

    def _invoke_runner(self, policy: Any, record: Any, trial_id: str, inputs: Mapping[str, Any],
                       *, portfolio_name: str, evidence_root: Path | None = None) -> dict[str, Any]:
        if evidence_root is not None and (not evidence_root.is_absolute() or any(
                evidence_root.resolve().is_relative_to(root) or root.is_relative_to(evidence_root.resolve())
                for root in (self.root, SOURCE_ROOT))):
            raise ValueError("R1_SEPARATE_EVIDENCE_ROOT_REQUIRED")
        corrected = self._corrected_module()
        result = corrected.run_corrected_candidate(self.root, record, trial_id,
            inputs["factor_values"], inputs["store"], inputs["exec_calendar"], inputs["universe"],
            inputs["status_map"], inputs["regimes"], inputs["events"], inputs["index_close"], policy,
            portfolio_name=portfolio_name, write_evidence=evidence_root is not None, evidence_root=evidence_root)
        return self._adapt_result(result, record, trial_id, portfolio_name, inputs, evidence_root=evidence_root)

    @staticmethod
    def _adapt_result(result: Any, record: Any, trial_id: str, portfolio_name: str,
                      inputs: Mapping[str, Any], *, evidence_root: Path | None = None) -> dict[str, Any]:
        if not isinstance(result, tuple) or len(result) != 3:
            raise ValueError("R1_CALLER_RUNNER_RESULT_INCOMPLETE")
        engine, metrics, diagnostics = result
        required = {"candidate_id", "candidate_preregistration_hash", "trial_id", "portfolio_id",
            "closed_trade_count", "net_return", "profit_factor", "certification_status", "invariant_errors",
            "signal_diagnostics", "source_identity", "order_count", "filled_order_count"}
        if not isinstance(metrics, Mapping) or not required.issubset(metrics):
            raise ValueError("R1_CALLER_RUNNER_METRICS_INCOMPLETE")
        if not isinstance(diagnostics, Mapping) or not {"entry_signals", "qualified_rows"}.issubset(diagnostics):
            raise ValueError("R1_CALLER_RUNNER_DIAGNOSTICS_INCOMPLETE")
        if (any(not isinstance(diagnostics[key], list) for key in ("entry_signals", "qualified_rows"))
                or not isinstance(metrics["signal_diagnostics"], Mapping)):
            raise ValueError("R1_CALLER_RUNNER_DIAGNOSTICS_INCOMPATIBLE")
        expected = {"candidate_id": record.candidate.candidate_id,
            "candidate_preregistration_hash": record.preregistration_hash,
            "trial_id": trial_id, "portfolio_id": portfolio_name}
        if any(metrics[key] != value for key, value in expected.items()):
            raise ValueError("R1_CALLER_RUNNER_IDENTITY_MISMATCH")
        if (not hasattr(engine, "ledger") or not hasattr(engine, "orders")
                or metrics["order_count"] != len(engine.orders.orders)
                or metrics["invariant_errors"] != engine.ledger.check_invariants()
                or metrics["certification_status"] != engine.ledger.certification_status
                or engine.context.strategy_hash != record.preregistration_hash):
            raise ValueError("R1_CALLER_ENGINE_RESULT_MISMATCH")
        recomputed = load_corrected_module().compute_metrics_v3(engine, engine.ledger.initial_cash,
            inputs["regimes"], inputs["exec_calendar"], int(record.candidate.holding_period))
        if any(key not in metrics or metrics[key] != value for key, value in recomputed.items()):
            raise ValueError("R1_CALLER_METRICS_ENGINE_MISMATCH")
        source = metrics["source_identity"]
        corrected = load_corrected_module()
        if (not isinstance(source, Mapping)
                or source.get("corrected_sha256") != corrected.legacy.sha256(Path(corrected.__file__))
                or source.get("helper_sha256") != corrected.legacy.sha256(Path(corrected.legacy.__file__))):
            raise ValueError("R1_CALLER_RESULT_SOURCE_MISMATCH")
        evidence_ref = None
        if evidence_root is None:
            if "evidence_dir" in metrics:
                raise ValueError("R1_CALLER_UNEXPECTED_EVIDENCE_REFERENCE")
        else:
            evidence_ref = evidence_root / "metrics.json"
            if metrics.get("evidence_dir") != str(evidence_root) or not evidence_ref.is_file():
                raise ValueError("R1_CALLER_EVIDENCE_NOT_MATERIALIZED")
            saved = json.loads(evidence_ref.read_text(encoding="utf-8"))
            if saved != {key: value for key, value in metrics.items() if key != "evidence_dir"}:
                raise ValueError("R1_CALLER_EVIDENCE_IDENTITY_MISMATCH")
        input_diagnostics = inputs["input_diagnostics"]
        status = "DIAGNOSTIC_ONLY"
        if not engine.ledger.valid_trades:
            status = "INSUFFICIENT_EXECUTION_EVIDENCE"
        if metrics["invariant_errors"] or metrics["certification_status"] == "INVALID":
            status = "INVALID_ENGINE_RESULT"
        return {"engine": engine, "metrics": metrics, "diagnostics": diagnostics,
            "input_diagnostics": input_diagnostics, "status": status,
            "evidence_status": "MATERIALIZED" if evidence_ref else "UNMATERIALIZED",
            "metrics_ref": str(evidence_ref) if evidence_ref else None,
            "ready_for_real_trial": False}

    def _write_gate(self, gate_path: Path, policy: Any, policy_hash: str, policy_path: Path) -> None:
        if str(policy.policy_id) == "VALIDATION_DECISION_POLICY_V2":
            self._write(gate_path, {"schema_version": "performance-access-gate-v2", "enabled": True, "policy_id": policy.policy_id, "policy_version": policy.policy_version, "policy_hash": policy_hash, "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}})
            return
        gate = PerformanceAccessGate(gate_path)
        gate.enable(policy_path)
        gate.assert_enabled(policy_hash)

    def _write(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    def _trial_contract_identity(self, candidate: Any, existing: Any | None, trial_id: str) -> tuple[str | None, str | None]:
        metadata = candidate.metadata if isinstance(getattr(candidate, "metadata", None), Mapping) else {}
        lineage = existing.lineage if existing is not None and isinstance(getattr(existing, "lineage", None), Mapping) else {}
        reference = str(metadata.get("trial_contract_ref") or lineage.get("trial_contract_ref") or "")
        contract_hash = str(metadata.get("trial_contract_hash") or lineage.get("trial_contract_hash") or "")
        if not reference:
            candidate_path = self.root / "reports/research_daemon" / self.objective_id / "predictive" / "trial_contracts" / f"{trial_id}.json"
            if candidate_path.is_file():
                reference = str(candidate_path.relative_to(self.root)).replace("\\", "/")
        if reference and not contract_hash:
            payload = self._json(self.root / reference, {}) or {}
            contract_hash = str(payload.get("trial_contract_hash") or "") if isinstance(payload, Mapping) else ""
        return (reference or None, contract_hash or None)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        prefix = str(exc).split(":", 1)[0].strip()
        if prefix.startswith(("CANONICAL_", "PREDICTIVE_", "ENGINEERING_")):
            return prefix
        return type(exc).__name__

    @staticmethod
    def _budget_settlement(budget: SearchBudgetRegistryV1, reservation_id: str) -> dict[str, Any]:
        snapshot = budget.snapshot()
        settled = snapshot.get("settled_reservations", {})
        if reservation_id in settled:
            status = str(settled[reservation_id])
        elif reservation_id in snapshot.get("active_reservations", {}):
            status = "RESERVED"
        else:
            status = "RELEASED_OR_UNKNOWN"
        return {"reservation_id": reservation_id, "status": status}

    def _final_status_payload(
        self,
        *,
        trial_id: str,
        trial_contract_hash: str | None,
        reservation_id: str,
        budget: SearchBudgetRegistryV1,
        status: str,
        trial_status: str,
        stage: str,
        finished_at: str,
        performance_accessed: bool,
        performance_completed: bool,
        recovery: bool,
        error: Exception | None = None,
        classification: str | None = None,
        result_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        error_code = self._error_code(error) if error is not None else None
        error_message = str(error) if error is not None else None
        budget_settlement = self._budget_settlement(budget, reservation_id)
        return {
            "status": status,
            "trial_status": trial_status,
            "trial_id": trial_id,
            "stage": stage,
            "finished_at": finished_at,
            "error_code": error_code,
            "error_message": error_message,
            "error": error_message,
            "contract_hash": trial_contract_hash,
            "budget_reservation_identity": reservation_id,
            "budget_settlement": budget_settlement,
            "budget_consumed": str(budget_settlement.get("status")) == "CONSUMED",
            "result_artifact_id": result_artifact_id,
            "classification": classification,
            "performance_accessed": performance_accessed,
            "performance_completed": performance_completed,
            "performance_rerun": False,
            "recovery_of_trial": recovery,
            "final_test_access": {"physical": 0, "analytical": 0, "decision": 0},
            "prospective": 0,
            "real_order": "DISABLED",
        }

    def execute(self, candidate: Any, *, recovery: bool = False) -> Any:
        from chanlun_trader.research_daemon import PredictiveResult

        contract, record = self._contract_and_record(candidate)
        policy, policy_hash, policy_path = self._load_policy(contract)
        budget_path = self._budget_path()
        paths = self._canonical_paths(budget_path)
        ledger = ResearchFactoryTrialLedgerFacadeV1(trial_registry=TrialRegistryV1(paths["trial_registry"]), path=paths["factory_ledger"])
        requested_trial_id, requested_trial_number = self._requested_trial_identity(candidate)
        if requested_trial_id is not None:
            existing = ledger.latest().get(requested_trial_id)
            if existing is None:
                raise RuntimeError(f"CANONICAL_TRIAL_IDENTITY_NOT_FOUND:{requested_trial_id}")
            if existing.candidate_id != str(candidate.candidate_id) or existing.candidate_hash != str(candidate.candidate_hash):
                raise RuntimeError(f"CANONICAL_TRIAL_IDENTITY_MISMATCH:{requested_trial_id}")
        else:
            existing = self._existing_trial(ledger, candidate)
        if existing is not None and not recovery:
            if existing.status in ResearchFactoryTrialLedgerFacadeV1.TERMINAL and existing.performance_accessed:
                return self._result_from_record(existing, artifact_refs=self._artifact_refs_for_existing(candidate))
            if existing.performance_accessed:
                raise RuntimeError(f"CANONICAL_TRIAL_RECONCILIATION_REQUIRED:{existing.trial_id}")
        if recovery and (existing is None or existing.status != "PERFORMANCE_ACCESSED" or not existing.performance_accessed):
            raise RuntimeError(f"CANONICAL_TRIAL_RECOVERY_PRECONDITION_FAILED:{getattr(existing, 'trial_id', '')}")

        budget = SearchBudgetRegistryV1(self.objective_id, paths["budget"])
        budget_snapshot = budget.snapshot()
        batch_id = self._budget_binding(budget_snapshot, "batch", str(candidate.batch_id or contract.source_provenance.get("batch_id") or ""))
        family_id = self._budget_binding(budget_snapshot, "family", str(candidate.mechanism or contract.mechanism))
        trial_number = requested_trial_number or (int(str(existing.trial_id).rsplit("_T", 1)[-1]) if existing is not None and str(existing.trial_id).rsplit("_T", 1)[-1].isdigit() else 1)
        trial_id = str(existing.trial_id) if existing is not None else self._new_trial_id(batch_id, candidate, trial_number)
        bucket_keys = {("objective", self.objective_id), ("batch", batch_id), ("family", family_id)}
        persisted_keys = {(str(item["kind"]), str(item["key"])) for item in budget_snapshot.get("buckets", ())}
        missing_buckets = sorted(bucket_keys - persisted_keys)
        if missing_buckets:
            raise RuntimeError(f"CANONICAL_BUDGET_BUCKET_MISSING:{missing_buckets}")

        trial_contract_ref, trial_contract_hash = self._trial_contract_identity(candidate, existing, trial_id)

        report_dir = self.output_dir / batch_id / str(candidate.candidate_id)
        if trial_number > 1:
            report_dir = report_dir / trial_id
        factor_cache = self.root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet"
        corrected = self._corrected_module()
        helper_path = Path(corrected.__file__)
        engine_hash = _engine_hash(SOURCE_ROOT, helper_path)
        event_ids = tuple(str(condition["event_id"]) for condition in record.signal_predicate.event_conditions)
        inline_event_definitions = {str(condition["event_id"]): dict(condition) for condition in record.signal_predicate.event_conditions}
        dataset_hash = _dataset_hash(
            self.root,
            factor_cache,
            event_ids,
            corrected.legacy,
            inline_event_definitions=inline_event_definitions,
            contract_event_identities=contract.factor_event_registry_identities,
        )
        seed = int(policy.bootstrap_seed) + 1
        decision_family_id = policy.family_id(self.objective_id, batch_id)
        baseline_registry = BaselineControlRegistryV1()
        baseline = baseline_registry.register(batch_id=batch_id, mechanism=record.candidate.mechanism, controls=("PIT_UNIVERSE", "NEXT_SESSION_OPEN", "T_PLUS_1", "FIXED_HOLDING_SESSIONS", "NO_LEVERAGE", "NO_PARAMETER_SEARCH"))
        strategy_registry = ResearchStrategyRegistryFacadeV1(path=paths["strategy_registry"])
        strategy = strategy_registry.register(record.candidate.candidate_id, record.preregistration_hash, record.candidate.mechanism, durable_contract_hash=contract.content_hash, durable_contract_ref=str(Path(candidate.contract_ref)).replace("\\", "/"))
        if strategy.research_state == "DRAFT":
            strategy = strategy_registry.transition(record.candidate.candidate_id, "SEMANTIC_READY")
        if strategy.research_state == "SEMANTIC_READY":
            strategy_registry.transition(record.candidate.candidate_id, "VALIDATION_ELIGIBLE")

        reservation_id = str(existing.budget_reservation_identity) if existing is not None else budget.reserve_trial(batch_id=batch_id, family_id=family_id, candidate_id=str(candidate.candidate_id), trial_number=trial_number if trial_number > 1 else None)
        if recovery:
            settled = budget.snapshot().get("settled_reservations", {})
            if str(settled.get(reservation_id)) != "CONSUMED":
                raise RuntimeError(f"CANONICAL_TRIAL_RECOVERY_BUDGET_NOT_CONSUMED:{reservation_id}")
        elif not self._active_reservation(budget, reservation_id):
            raise RuntimeError(f"CANONICAL_BUDGET_RESERVATION_NOT_ACTIVE:{reservation_id}")
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            gate_path = report_dir / "performance_access_gate.json"
            self._write(report_dir / "baseline_preregistration.json", {"registrations": [baseline.to_dict()], "performance_accessed": False})
            self._write(report_dir / "preregistration.json", {"candidate": {"candidate_id": record.candidate.candidate_id, "candidate_hash": record.preregistration_hash, "semantic_fingerprint": record.semantic_fingerprint, "content_hash": contract.content_hash}, "trial_id": trial_id, "dataset_hash": dataset_hash, "engine_hash": engine_hash, "validation_policy_hash": policy_hash, "family_id": family_id, "budget_reservation_identity": reservation_id, "performance_accessed": False, "generated_at": now_timestamp()})
            self._write_gate(gate_path, policy, policy_hash, policy_path)
            if existing is None and not recovery:
                ledger.register_before_performance(trial_id=trial_id, objective_id=self.objective_id, batch_id=batch_id, family_id=family_id, hypothesis_id=contract.hypothesis_id, candidate_id=record.candidate.candidate_id, candidate_hash=record.preregistration_hash, dataset_hash=dataset_hash, validation_policy_hash=policy_hash, engine_hash=engine_hash, seed=seed, lineage={"source": "HEADLESS_AUTONOMOUS_RESEARCH_DAEMON_V1", "canonical_runner": "BacktestEngineV2+PortfolioExitEvaluatorV1", "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}}, budget_reservation_identity=reservation_id)
        except Exception:
            if existing is None and self._active_reservation(budget, reservation_id):
                budget.release(reservation_id)
            raise

        performance_accessed = bool(recovery)
        budget_consumed = bool(recovery)
        try:
            budget_registration = audit_budget_registration(ledger, budget, trial_id=trial_id,
                reservation_id=reservation_id, candidate_id=record.candidate.candidate_id,
                candidate_hash=record.preregistration_hash, recovery=recovery)
            if not budget_registration["passed"]:
                raise RuntimeError("CANONICAL_BUDGET_PREREGISTRATION_NOT_VERIFIED")
            from .synthetic_novelty import canonical_novelty_boundary
            from .synthetic_batch_delegation import batch_performance_boundary
            with canonical_novelty_boundary(self.root, self.objective_id, candidate, contract) as novelty_evidence:
                with batch_performance_boundary(self.root, self.objective_id, candidate):
                    if not recovery:
                        ledger.mark_performance_accessed(trial_id)
                        performance_accessed = True
                    # Capture in-memory inputs within the source boundary; run both engines outside it.
                    inputs = self._prepare_inputs(policy, record, corrected, factor_cache, contract=contract)
            small_policy = replace(policy, initial_cash=policy.small_capital_cash, max_positions=policy.small_capital_slots, lot_size=policy.small_capital_lot_size)
            base_result = self._invoke_runner(policy, record, trial_id, inputs, portfolio_name="BASE_RESEARCH")
            ten_result = self._invoke_runner(small_policy, record, trial_id, inputs, portfolio_name="SMALL_CAPITAL_10K")
            base_engine, base_metrics = base_result["engine"], base_result["metrics"]
            ten_engine, ten_metrics = ten_result["engine"], ten_result["metrics"]
            if base_result["status"] != "DIAGNOSTIC_ONLY" or ten_result["status"] != "DIAGNOSTIC_ONLY":
                raise ValueError("R1_CALLER_EXECUTION_EVIDENCE_INSUFFICIENT_OR_INVALID")
            pnl = [float(trade.realized_pnl) for trade in base_engine.ledger.valid_trades if trade.side == corrected.Side.SELL]
            bootstrap = corrected.bootstrap_result(pnl, int(policy.bootstrap_iterations), seed)
            concentration = corrected.concentration(base_engine, float(policy.initial_cash))
            robustness = {"subperiods": corrected.subperiods(base_engine, float(policy.initial_cash)), "regime_split": corrected.regime_split(base_engine, inputs["regimes"])}
            cost_stress = {name: corrected.recompute_cost_stress_metrics(base_engine, base_metrics, policy, float(policy.initial_cash), fee_mult=values[0], stamp_mult=values[1], slip_mult=values[2]) for name, values in {"BASE": (1.0, 1.0, 1.0), "FEES_X2": (2.0, 2.0, 1.0), "SLIPPAGE_X2": (1.0, 1.0, 2.0), "COMBINED_X2": (2.0, 2.0, 2.0)}.items()}
            validator_classification = corrected.classification(base_metrics, bootstrap, cost_stress)
            reason_codes = ("VALIDATOR_INVALID_INVARIANT",) if validator_classification == "INVALID" else (() if validator_classification in LEGAL_CLASSIFICATIONS else ("VALIDATOR_INSUFFICIENT_EVIDENCE",))
            small_contract = small_capital_contract(policy)
            engine_integrity = {"certification_status": base_metrics.get("certification_status"), "invariant_errors": base_metrics.get("invariant_errors", [])}
            gates = {"engine_integrity": {"passed": not engine_integrity["invariant_errors"] and engine_integrity["certification_status"] != "INVALID"}, "data_validity": {"passed": inputs["input_diagnostics"]["data_validity"] == "VALIDATED_DAILY_RAW"}, "pit_validity": {"passed": inputs["input_diagnostics"]["pit_validity"] == "EXPLICIT_DUAL_SOURCE_NORMAL_TRADING"}, "sample_adequacy": {"passed": int(base_metrics.get("closed_trade_count", 0)) >= 30 and bootstrap.get("status") == "COMPLETE"}, "local_base_return": {"passed": float(base_metrics.get("net_return", 0.0)) > 0}, "local_profit_factor": {"passed": base_metrics.get("profit_factor") is None or float(base_metrics.get("profit_factor")) > 1.0}, "raw_bootstrap_support": {"passed": float(bootstrap.get("p_value", 1.0)) < 0.05}, "cost_stress_combined_x2": {"passed": cost_stress["COMBINED_X2"].get("net_return") is not None and float(cost_stress["COMBINED_X2"]["net_return"]) > 0}, "small_capital_execution_feasibility": {"passed": bool(small_contract.get("fixed_contract")) and not ten_metrics.get("invariant_errors") and ten_metrics.get("certification_status") != "INVALID"}, "baseline_preregistration": {"passed": bool(baseline_registry.items())}, "intended_holding_contract": {"passed": 2 <= int(record.candidate.holding_period) <= 10}, "microstructure_realism": audit_microstructure(base_engine, inputs), "candidate_similarity_control": novelty_evidence, "search_budget_reservation": budget_registration}
            validation_row = {"trial_id": trial_id, "candidate_id": record.candidate.candidate_id, "candidate_hash": record.preregistration_hash, "validator_classification": validator_classification, "local_classification": validator_classification, "classification": None, "final_adjudication_pending": True, "base_metrics": base_metrics, "small_capital_10k_metrics": ten_metrics, "small_capital_contract": small_contract, "bootstrap": bootstrap, "concentration": concentration, "robustness": robustness, "cost_stress": cost_stress, "engine_integrity": engine_integrity, "gates": gates, "gate_roles": {key: value.get("role", "") for key, value in policy.gates.items()}, "soft_evidence": {"small_capital_economic_performance": {"status": "REPORT_ONLY"}, "concentration_diagnostics": {"status": "AVAILABLE", "warning": "NONE"}, "subperiod_robustness": {"status": "AVAILABLE", "warning": "NONE"}, "regime_robustness": {"status": inputs["input_diagnostics"]["benchmark"], "warning": "BENCHMARK_NOT_VERIFIED"}, "baseline_result_comparison": {"status": "REPORT_ONLY", "warning": "BASELINE_COMPARISON_WARNING"}}, "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}, "performance_completed": True}
            validation_row["input_diagnostics"] = inputs["input_diagnostics"]
            validation_row["runner_diagnostics"] = {name: {"status": result["status"],
                "evidence_status": result["evidence_status"], "metrics_ref": result["metrics_ref"],
                "entry_signal_count": len(result["diagnostics"]["entry_signals"])}
                for name, result in (("BASE_RESEARCH", base_result), ("SMALL_CAPITAL_10K", ten_result))}
            provisional_path = report_dir / "provisional_validation_evidence" / f"{trial_id}.json"
            self._write(provisional_path, {**validation_row, "p_value": float(bootstrap.get("p_value", 1.0))})
            ledger.mark_provisional(trial_id, local_classification=validator_classification, evidence_ref=str(provisional_path.relative_to(self.root)).replace("\\", "/"), result={"validator_classification": validator_classification, "performance_completed": True})
            if not recovery:
                budget.consume(reservation_id)
                budget_consumed = True
            prior_p_values = _load_cumulative_private_p_values(self.root, self.root / "reports/RUN_AUTONOMOUS_ALPHA_RESEARCH_NEW_BATCH_V1/runtime_factor_v1")
            p_values = {**prior_p_values, record.candidate.candidate_id: float(bootstrap.get("p_value", 1.0))}
            multiple_testing = benjamini_hochberg(p_values, q=policy.fdr_q)
            history_snapshot_hash = stable_hash({"validation_policy_hash": policy_hash, "historical_trial_ids": sorted(prior_p_values), "current_batch_candidate_ids": [record.candidate.candidate_id], "family_frozen_before_performance": True})
            multiple_testing.update({"decision_family_id": decision_family_id, "decision_denominator": int(multiple_testing["hypothesis_count"]), "current_batch_hypothesis_count": 1, "cumulative_legal_history_denominator": int(multiple_testing["hypothesis_count"]), "history_snapshot_hash": history_snapshot_hash, "family_contract_hash": getattr(policy, "multiple_testing_contract_hash", None), "historical_outcomes_loaded_only_after_performance_gate": True, "design_context_received_exact_outcomes": False})
            self._write(report_dir / "multiple_testing.json", multiple_testing)
            adjudicator = FinalResearchAdjudicatorV1(policy, policy_hash=policy_hash)
            decision = adjudicator.adjudicate(candidate_id=record.candidate.candidate_id, candidate_hash=record.preregistration_hash, trial_id=trial_id, local_classification=validator_classification, multiple_testing=multiple_testing, decision_family_id=decision_family_id, decision_denominator=int(multiple_testing["decision_denominator"]), history_snapshot_hash=history_snapshot_hash, metrics_ref=str(provisional_path.relative_to(self.root)).replace("\\", "/"), evidence={"engine_integrity": engine_integrity, "gates": gates, "soft_evidence": validation_row["soft_evidence"], "gate_roles": validation_row["gate_roles"], "small_capital_evidence_status": "AVAILABLE_SOFT_EVIDENCE", "small_capital_contract_valid": bool(small_contract.get("fixed_contract"))})
            decision_payload = decision.to_dict()
            validation_row["classification"] = decision.effective_classification
            validation_row["final_adjudication_pending"] = False
            validation_row["final_decision"] = decision_payload
            ledger.mark_final_adjudication(trial_id, decision.effective_classification, decision_id=decision.decision_id, reason_codes=tuple(reason_codes) + ((decision.reason,) if decision.reason else ()), result={"adjusted_support": decision.adjusted_support})
            if decision.effective_classification in LEGAL_CLASSIFICATIONS:
                strategy_registry.apply_classification(record.candidate.candidate_id, decision.effective_classification, evidence_ref=decision.decision_id)
            else:
                strategy_registry.transition(record.candidate.candidate_id, "VALIDATION_BLOCKED", evidence_ref=decision.decision_id)
            ledger.mark_registry_committed(trial_id)
            trial_record = ledger.latest()[trial_id].to_dict()
            trial_record.update({"final_decision": decision_payload, "validation_row": validation_row})
            failure_snapshot = FailureKnowledgeAdapterV1().snapshot_from_trials([trial_record], snapshot_id=f"{batch_id}_FAILURE_SNAPSHOT_DAEMON_V1", parent_snapshot_id=None)
            self._write(report_dir / "validation_results.json", {"rows": [validation_row], "final_adjudicator": "FinalResearchAdjudicatorV1", "performance_rerun": False, "recovery_of_trial": bool(recovery), "final_test_access": {"physical": 0, "analytical": 0, "decision": 0}})
            self._write(report_dir / "trial_manifest.json", {"trials": [trial_record], "performance_accessed_before_metrics": True, "performance_rerun": False, "recovery_of_trial": bool(recovery)})
            self._write(report_dir / "failure_extraction.json", failure_snapshot.to_dict())
            graph = ResearchArtifactGraphV1(report_dir / "artifact_graph.json")
            graph.add_node(f"candidate:{record.candidate.candidate_id}", "Candidate", {"candidate_id": record.candidate.candidate_id, "candidate_hash": record.preregistration_hash})
            graph.add_node(f"frozen_contract:{record.candidate.candidate_id}", "FrozenCandidateContract", contract.to_dict())
            graph.add_node(f"trial:{trial_id}", "Trial", trial_record)
            graph.add_node(f"validation:{trial_id}", "ValidationResult", validation_row)
            graph.add_node(f"final-decision:{decision.decision_id}", "FinalResearchDecision", decision_payload)
            graph.add_node(f"failure:{failure_snapshot.snapshot_id}", "FailureKnowledge", failure_snapshot.to_dict())
            graph.add_node(f"strategy:{record.candidate.candidate_id}", "Strategy", strategy_registry._records[record.candidate.candidate_id].to_dict())
            graph.add_edge(f"candidate:{record.candidate.candidate_id}", "HAS_DURABLE_CONTRACT", f"frozen_contract:{record.candidate.candidate_id}")
            graph.add_edge(f"trial:{trial_id}", "VALIDATED_BY", f"validation:{trial_id}")
            graph.add_edge(f"validation:{trial_id}", "FINAL_ADJUDICATED_AS", f"final-decision:{decision.decision_id}")
            if failure_snapshot.entries:
                graph.add_edge(f"trial:{trial_id}", "FAILED_BECAUSE", f"failure:{failure_snapshot.snapshot_id}")
            graph.add_edge(f"strategy:{record.candidate.candidate_id}", "FINAL_ADJUDICATION_COMMITTED", f"candidate:{record.candidate.candidate_id}")
            artifact_refs = tuple(str(path.relative_to(self.root)).replace("\\", "/") for path in (report_dir / "trial_manifest.json", report_dir / "validation_results.json", report_dir / "failure_extraction.json", report_dir / "artifact_graph.json"))
            finished_at = now_timestamp()
            self._write(report_dir / "final_status.json", self._final_status_payload(
                trial_id=trial_id,
                trial_contract_hash=trial_contract_hash,
                reservation_id=reservation_id,
                budget=budget,
                status="TRIAL_COMPLETED",
                trial_status="COMPLETED",
                stage="TRIAL_COMPLETED",
                finished_at=finished_at,
                performance_accessed=True,
                performance_completed=True,
                recovery=bool(recovery),
                classification=decision.effective_classification,
                result_artifact_id=next((ref for ref in artifact_refs if ref.endswith("trial_manifest.json")), artifact_refs[0] if artifact_refs else None),
            ))
            del base_engine, ten_engine
            return PredictiveResult(trial_id, "COMPLETED", True, decision.effective_classification, tuple(reason_codes) + ((decision.reason,) if decision.reason else ()), artifact_refs, {"canonical_executor": "CanonicalPredictiveExecutorV1", "performance_rerun": False, "recovery_of_trial": bool(recovery), "budget_reused": bool(recovery)})
        except Exception as exc:
            current = ledger.latest().get(trial_id)
            if performance_accessed and not budget_consumed:
                if current is not None and current.status == "PERFORMANCE_ACCESSED":
                    ledger.mark_engineering_interrupted(
                        trial_id,
                        budget_reservation_identity=reservation_id,
                        error_code=self._error_code(exc),
                        error_message=str(exc),
                    )
                if self._active_reservation(budget, reservation_id):
                    budget.consume(reservation_id)
                budget_consumed = True
            elif not performance_accessed and self._active_reservation(budget, reservation_id):
                budget.release(reservation_id)
            current = ledger.latest().get(trial_id)
            self._write(report_dir / "final_status.json", {
                **self._final_status_payload(
                    trial_id=trial_id,
                    trial_contract_hash=trial_contract_hash,
                    reservation_id=reservation_id,
                    budget=budget,
                    status="TRIAL_FAILED",
                    trial_status="INVALIDATED" if performance_accessed else "BLOCKED",
                    stage="TRIAL_FAILED",
                    finished_at=now_timestamp(),
                    performance_accessed=performance_accessed,
                    performance_completed=False,
                    recovery=bool(recovery),
                    error=exc,
                    classification=current.classification if current is not None else None,
                ),
                "error_type": type(exc).__name__,
            })
            raise

    def _corrected_module(self) -> Any:
        return load_corrected_module()
