"""实际状态 reader 对执行窗口的投影；较长源历史不等于窗口缺失。"""
import json

import pytest

from chanlun_trader.research_factory.source_dependencies import load_legacy_module


@pytest.mark.parametrize("missing", [False, True])
def test_longer_history_keeps_window_coverage_and_fail_closed(tmp_path, missing):
    for folder, normal in (("st_state", "NORMAL"), ("suspension_state", "TRADING")):
        directory = tmp_path / folder
        directory.mkdir()
        rows = [{"trade_date": day, "status": normal if day == 20240103 else "UNKNOWN"}
                for day in (20240102, 20240103, 20240104) if not (missing and day == 20240103 and folder == "st_state")]
        (directory / "symbol=000001_SZ.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    reader = load_legacy_module().PITStateMap(tmp_path, [20240103])
    assert reader.tradable("000001.SZ", 20240103)[0] is (not missing)
    assert reader.tradable("000001.SZ", 20240102)[0] is False
    assert reader.tradable("000001.SZ", 20240104)[0] is False
