"""Quant research platform: data foundation, factor/event registries, governance.

M0: ResearchDataAccessGuard / DataCapabilityRegistry / QFQ PIT Safety.
"""
from .guard import (ResearchDataAccessGuard, FinalTestAccessViolation,
                    ResearchDataAccessGuardConfig, UnsafeLegacyQfqAccessError,
                    research_context)
from .capability import DataCapability, DataCapabilityRegistry, build_local_mock_registry
from .factor import FactorDefinition, FactorRegistry, FactorStore, FactorLineage
from .event import EventDefinition, EventRegistry, EventStore
from .label import LabelStore
from .screener import ScreenerRule, ScreenerFieldSpec, FieldClass, DynamicGroup
from .evaluation import FactorEvaluator, FactorEvalResult
from .event_study import EventStudy, EventStudyResult
from .strategy import StrategyDefinition, StrategyLibrary, PromotionRecord, PromotionLedger
from .selector import DailyStockSelector, Candidate
from .run_manifest import build_run_manifest, validate_run_manifest, write_immutable_run_manifest
from .acceptance import IndependentAcceptanceValidator, IndependentAcceptanceResult

__all__ = [
    "ResearchDataAccessGuard",
    "FinalTestAccessViolation",
    "ResearchDataAccessGuardConfig",
    "UnsafeLegacyQfqAccessError",
    "research_context",
    "DataCapability",
    "DataCapabilityRegistry",
    "build_local_mock_registry",
    "FactorDefinition",
    "FactorRegistry",
    "FactorStore",
    "FactorLineage",
    "EventDefinition",
    "EventRegistry",
    "EventStore",
    "LabelStore",
    "ScreenerRule",
    "ScreenerFieldSpec",
    "FieldClass",
    "DynamicGroup",
    "FactorEvaluator",
    "FactorEvalResult",
    "EventStudy",
    "EventStudyResult",
    "StrategyDefinition",
    "StrategyLibrary",
    "PromotionRecord",
    "PromotionLedger",
    "DailyStockSelector",
    "Candidate",
    "build_run_manifest",
    "validate_run_manifest",
    "write_immutable_run_manifest",
    "IndependentAcceptanceValidator",
    "IndependentAcceptanceResult",
]

# 数据/系统状态常量
REAL_TDX_5M_ADAPTER = "READY"
CORPORATE_ACTION_STATUS = "GUARDED"
FINAL_TEST_STATUS = "SEALED"
