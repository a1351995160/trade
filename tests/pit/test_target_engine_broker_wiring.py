"""目标 Engine ↔ Broker 接线一致性（防止供体参数不兼容复发）。

本测试证明：目标仓库的 BacktestEngineV2 与 BrokerSimulator **参数兼容**，
不需要 corporate_action 参数即可正常工作；且不因 PIT 开关默认值改变
既有调用行为。

导入来源必须指向**当前 checkout**（以解析后的绝对路径判定，不依赖目录名）。
"""
import inspect
from pathlib import Path

import chanlun_trader
from src.chanlun_trader.engine.broker import BrokerSimulator
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig

from tests.pit.test_pit_eligibility_contract import CAL, SYM, _run, _daily
from src.chanlun_trader.engine.historical_eligibility import HistoricalEligibilityTable

# 从**本测试文件位置**推导当前 checkout 根，而不是依赖目录名。
# tests/pit/<this file> -> parents[2] == repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PKG_INIT = (REPO_ROOT / "src" / "chanlun_trader" / "__init__.py").resolve()
EXPECTED_ENGINE = (REPO_ROOT / "src" / "chanlun_trader" / "engine" / "engine.py").resolve()
EXPECTED_BROKER = (REPO_ROOT / "src" / "chanlun_trader" / "engine" / "broker.py").resolve()


def test_engine_and_broker_are_from_this_repository():
    """关键模块必须来自**当前 checkout**，不混入旧目录或审计供体。

    身份以「解析后的绝对路径是否等于本仓库内的确切预期文件」判定，
    **不使用目录关键字**——换名 checkout、CI 上 clone 为 trade 等都必须通过。
    """
    pkg = Path(chanlun_trader.__file__).resolve()
    engine = Path(inspect.getfile(BacktestEngineV2)).resolve()
    broker = Path(inspect.getfile(BrokerSimulator)).resolve()

    assert pkg == EXPECTED_PKG_INIT, (
        "chanlun_trader 必须来自当前 checkout: %s != %s" % (pkg, EXPECTED_PKG_INIT))
    assert engine == EXPECTED_ENGINE, (
        "BacktestEngineV2 必须来自当前 checkout: %s != %s" % (engine, EXPECTED_ENGINE))
    assert broker == EXPECTED_BROKER, (
        "BrokerSimulator 必须来自当前 checkout: %s != %s" % (broker, EXPECTED_BROKER))

    # 反向检查：不得来自当前 checkout 之外（例如旧工作区或审计供体）
    for path in (pkg, engine, broker):
        assert REPO_ROOT in path.parents, (
            "模块位于当前 checkout 之外: %s (root=%s)" % (path, REPO_ROOT))


def test_broker_signature_has_no_corporate_action_parameter():
    """目标 Broker 不接受 corporate_action；供体的该参数不得被搬入。"""
    params = inspect.signature(BrokerSimulator.__init__).parameters
    assert "corporate_action" not in params, "不得移植供体的公司行动构造参数"
    assert "price_limit_model" in params


def test_engine_config_has_pit_switch_defaulting_off():
    """PIT 开关默认关闭，保持既有调用方行为不变（非仅改默认值让旧调用全拒）。"""
    assert "pit_eligibility_enforced" in EngineConfig.__dataclass_fields__
    assert EngineConfig().pit_eligibility_enforced is False


def test_engine_broker_construct_and_run_without_corporate_action():
    """目标 Engine+Broker 能无 corporate_action 参数构造并跑通（正对照）。"""
    t = HistoricalEligibilityTable()
    for d in (20240801, 20240802):
        _daily(t, d, "NORMAL")
    buys, statuses, res = _run(t, 20240801)
    assert len(buys) == 1
    assert statuses[0] == ("FILLED", "ENTRY_SIGNAL")
    assert res.ledger is not None


def test_pit_disabled_preserves_legacy_behavior():
    """PIT 关闭时，未提供资格的证券仍按既有行为处理（不因新门禁全拒）。"""
    from src.chanlun_trader.engine.asof import MarketDataStore
    from src.chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal
    from src.chanlun_trader.engine.time_types import tz_aware
    import pandas as pd

    store = MarketDataStore()
    bars = pd.DataFrame(
        [{"date": d, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
          "volume": 1000000.0, "amount": 10000000.0, "prev_close": 10.0}
         for d in CAL]).set_index("date")
    store.add_daily_raw(SYM, bars.copy())
    store.add_daily_qfq(SYM, bars[["open", "high", "low", "close",
                                   "volume", "amount"]].copy())
    cfg = EngineConfig(initial_cash=10000.0, max_positions=1, max_position_weight=1.0,
                       max_holding_days=999, persist_run_manifest=False,
                       enable_index_filter=False, index_filter_enabled=False)
    # 不传 security_master、不启用 PIT
    eng = BacktestEngineV2(store, CAL, config=cfg, source_identity=("UNKNOWN", True))
    eng.add_signal(Signal(strategy_id="T", signal_id="s", symbol=SYM,
                          generated_at=tz_aware(2024, 8, 1, 15, 0), direction=Side.BUY,
                          score=1.0, execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
    res = eng.run()
    buys = [x for x in res.ledger.trades if x.side == Side.BUY]
    assert len(buys) == 1, "PIT 关闭时既有行为必须保持（可成交）"