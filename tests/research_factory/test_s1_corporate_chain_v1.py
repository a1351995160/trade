"""Frozen S1 action coverage must fail closed if the source snapshot changes."""
from copy import deepcopy
import json

import pytest

from scripts.verify_s1_corporate_chain_v1 import ACTION_REPORT, checked_events


def test_frozen_dividend_terms_and_source_snapshot_are_checked():
    snapshot = json.loads(ACTION_REPORT.read_text(encoding="utf-8"))
    events = checked_events(snapshot)
    assert [(event["symbol"], event["record_date"], event["terms"]["cash_per_share"])
            for event in events] == [
                ("000001.SZ", 20240613, 0.719),
                ("600000.SH", 20240717, 0.321),
            ]

    changed = deepcopy(snapshot)
    changed["records"][0]["dividCashPsBeforeTax"] = "9.999"
    with pytest.raises(ValueError, match="S1_ACTION_SOURCE_RECORDS_CONFLICT"):
        checked_events(changed)

    changed = deepcopy(snapshot)
    changed["account_window_events"].pop()
    with pytest.raises(ValueError, match="S1_ACTION_WINDOW_SELECTION_CONFLICT"):
        checked_events(changed)

    changed = deepcopy(snapshot)
    changed["query_counts"]["600000.SH:2024"] = 0
    with pytest.raises(ValueError, match="S1_ACTION_SOURCE_COUNTS_CONFLICT"):
        checked_events(changed)
