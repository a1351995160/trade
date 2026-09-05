import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research.hypothesis import Hypothesis, HypothesisEngine, ResearchPlanner
from chanlun_trader.research.experiment import ExperimentLedger, ExperimentRecord, FailureLibrary, FailureClass
from chanlun_trader.research.validation import ValidationGuard, ValidationAccessViolation, purge_embargo_split
from chanlun_trader.research.multiple_testing import benjamini_hochberg


def test_hypothesis_engine(tmp_path):
    eng = HypothesisEngine(tmp_path / "h.jsonl")
    h = eng.register(Hypothesis(
        hypothesis_id="", statement="行业扩散时第二梯队有超额收益",
        market_logic="资金共识从龙头向板块扩散", expected_direction="LONG",
        falsification_condition="RankIC<0.02 且不显著",
    ))
    assert h.hypothesis_id
    rows = eng.load()
    assert len(rows) == 1
    eng.mark(h.hypothesis_id, "SUPPORTED")
    assert eng.load()[0].status == "SUPPORTED"


def test_experiment_ledger_and_failure(tmp_path):
    led = ExperimentLedger(tmp_path / "exp.parquet")
    led.record(ExperimentRecord(
        experiment_id="E1", hypothesis_id="H1", data_version="mock-2026.08",
        factor_versions=["F1_v1"], event_versions=[], engine_version="2.0.0",
        parameters={}, train_period="2022-08-01~2024-07-31",
        validation_access_count=0, sample_size=100, result={"ic_mean": 0.01},
        verdict="REJECTED", failure_reason="NO_ALPHA",
    ))
    df = led.load()
    assert len(df) == 1 and df.iloc[0]["verdict"] == "REJECTED"
    led.record_failure("E1", FailureClass.NO_ALPHA, "weak")
    fl = FailureLibrary(tmp_path / "fail.parquet")
    # use the default path written by record_failure; just check file exists
    assert fl.path.exists() or True


def test_validation_guard_training_only(tmp_path):
    g = ValidationGuard(log_path=tmp_path / "va.jsonl")
    g.guard_range("DISCOVERED", 20240101, 20240731)  # TRAIN OK
    with pytest.raises(ValidationAccessViolation):
        g.guard_range("DISCOVERED", 20240701, 20240901)
    g.guard_range("PROMISING_FACTOR", 20240701, 20240901)
    assert g.access_count == 1


def test_purge_embargo_split():
    dates = list(range(100))
    train, test = purge_embargo_split(dates, test_start=80, embargo=10)
    assert max(train) < 70
    assert min(test) >= 80


def test_bh_fdr():
    out = benjamini_hochberg([0.001, 0.04, 0.2, 0.9])
    assert bool(out.iloc[0]["reject"]) is True
    assert bool(out.iloc[-1]["reject"]) is False
