"""Historical 5m data foundation."""
from .base import BAR_TIMESTAMP_SEMANTICS, BarTimestampSemantics, NORMALIZED_COLUMNS, normalize_symbol
from .baostock_provider import BaoStock5MinProvider
from .downloader import Historical5mDownloader
from .manifest import ImmutableRawStore, ManifestStore, Normalized5mStore, sha256_file
from .performance import (
    EVENT_WINDOW_CACHE_VERSION,
    FAST_VERIFY,
    FULL_CRYPTO_VERIFY,
    INDEX_VERSION,
    SESSION_SUMMARY_VERSION,
    DatasetIdentityError,
    CheckpointIdentityError,
    EventWindowCache,
    EventWindowKey,
    IncrementalManifestVerifier,
    IncrementalVerificationError,
    MinuteDatasetIndex,
    ResearchMinuteReader,
    ResearchCheckpoint,
    SessionSummaryStore,
)
from .normalizer import normalize_5m_frame
from .gap_resolution import GapEvidence, build_gap_evidence, classify_gap
from .pit_security_master import PITSecurityMaster, PITSecurityRecord, SecurityState, SecurityStateAsOf
from .tdx_raw_hq_provider import TdxRawHQProvider
from .universe import STAGE1_SYMBOLS, STAGE2_UNIVERSE, stage2_manifest, stage2_symbols
from .validator import DataQualityError, QualityReport, audit_missing_bars, validate_5m

__all__ = [
    "BAR_TIMESTAMP_SEMANTICS", "BarTimestampSemantics", "NORMALIZED_COLUMNS",
    "normalize_symbol", "BaoStock5MinProvider", "Historical5mDownloader",
    "ImmutableRawStore", "ManifestStore", "Normalized5mStore", "sha256_file",
    "INDEX_VERSION", "SESSION_SUMMARY_VERSION", "EVENT_WINDOW_CACHE_VERSION",
    "FAST_VERIFY", "FULL_CRYPTO_VERIFY", "DatasetIdentityError",
    "IncrementalVerificationError", "IncrementalManifestVerifier", "CheckpointIdentityError", "ResearchCheckpoint",
    "MinuteDatasetIndex", "SessionSummaryStore", "ResearchMinuteReader",
    "EventWindowCache", "EventWindowKey",
    "normalize_5m_frame", "TdxRawHQProvider", "DataQualityError", "QualityReport",
    "validate_5m", "audit_missing_bars", "STAGE1_SYMBOLS", "STAGE2_UNIVERSE",
    "stage2_manifest", "stage2_symbols", "GapEvidence", "build_gap_evidence",
    "classify_gap", "PITSecurityMaster", "PITSecurityRecord", "SecurityState",
    "SecurityStateAsOf",
]
