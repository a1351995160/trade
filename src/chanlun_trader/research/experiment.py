"""ExperimentLedger / FailureLibrary。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path

import pandas as pd

DEFAULT_EXPERIMENT_PATH = Path("data/research/experiment_ledger.parquet")
DEFAULT_FAILURE_PATH = Path("data/research/failure_library.parquet")


class FailureClass(str, Enum):
    NO_ALPHA = "NO_ALPHA"
    VALIDATION_FAIL = "VALIDATION_FAIL"
    BAD_REGIME = "BAD_REGIME"
    HIGH_TURNOVER = "HIGH_TURNOVER"
    COST_SENSITIVE = "COST_SENSITIVE"
    LOW_SAMPLE = "LOW_SAMPLE"
    WINNER_CONCENTRATION = "WINNER_CONCENTRATION"
    SECTOR_CONCENTRATION = "SECTOR_CONCENTRATION"
    EXTREME_PERIOD_DEPENDENT = "EXTREME_PERIOD_DEPENDENT"
    PARAMETER_FRAGILE = "PARAMETER_FRAGILE"
    PIT_UNSAFE = "PIT_UNSAFE"
    LOOKAHEAD = "LOOKAHEAD"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DUPLICATE_ALPHA = "DUPLICATE_ALPHA"
    CORPORATE_ACTION_UNSUPPORTED = "CORPORATE_ACTION_UNSUPPORTED"


@dataclass
class ExperimentRecord:
    experiment_id: str
    hypothesis_id: str
    data_version: str
    factor_versions: list
    event_versions: list
    engine_version: str
    parameters: dict
    train_period: str
    validation_access_count: int
    sample_size: int
    result: dict
    verdict: str                    # SUPPORTED / REJECTED / ERROR / INVALID
    failure_reason: str = ""
    recorded_at: str = ""

    def __post_init__(self):
        if not self.experiment_id:
            self.experiment_id = f"E-{uuid.uuid4().hex[:8].upper()}"
        if not self.recorded_at:
            self.recorded_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return asdict(self)


class ExperimentLedger:
    def __init__(self, path: Path = DEFAULT_EXPERIMENT_PATH):
        self.path = Path(path)

    def record(self, rec: ExperimentRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        d = rec.to_dict()
        for k in ("factor_versions", "event_versions", "parameters", "result"):
            d[k] = json.dumps(d[k], ensure_ascii=False, default=str)
        row = pd.DataFrame([d])
        if self.path.exists():
            old = pd.read_parquet(self.path)
            df = pd.concat([old, row], ignore_index=True)
        else:
            df = row
        df.to_parquet(self.path, index=False)

    def load(self) -> pd.DataFrame:
        if self.path.exists():
            return pd.read_parquet(self.path)
        return pd.DataFrame()

    def record_failure(self, experiment_id: str, failure_class: FailureClass, note: str = "") -> None:
        fpath = DEFAULT_FAILURE_PATH
        fpath.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame([{
            "experiment_id": experiment_id, "failure_class": failure_class.value,
            "note": note, "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }])
        if fpath.exists():
            old = pd.read_parquet(fpath)
            df = pd.concat([old, row], ignore_index=True)
        else:
            df = row
        df.to_parquet(fpath, index=False)


class FailureLibrary:
    def __init__(self, path: Path = DEFAULT_FAILURE_PATH):
        self.path = Path(path)

    def add(self, experiment_id: str, failure_class: FailureClass, note: str = "") -> None:
        ExperimentLedger(DEFAULT_EXPERIMENT_PATH).record_failure(experiment_id, failure_class, note)

    def load(self) -> pd.DataFrame:
        if self.path.exists():
            return pd.read_parquet(self.path)
        return pd.DataFrame(columns=["experiment_id", "failure_class", "note", "recorded_at"])

    def family_failure_count(self, df: pd.DataFrame | None = None) -> dict:
        df = self.load() if df is None else df
        return df.groupby("failure_class").size().to_dict()
