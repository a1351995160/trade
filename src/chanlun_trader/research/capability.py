"""DataCapabilityRegistry: 记录系统真实拥有的数据。

每个 Dataset 必须有明确的时间语义与 PIT 状态；status 非 READY 的数据禁止进入正式研究。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .guard import RESEARCH_END

DEFAULT_REGISTRY_PATH = Path("data/research/data_capability.json")


@dataclass
class DataCapability:
    dataset_id: str
    source: str
    provider: str
    fields: list
    frequency: str
    earliest_date: int
    latest_date: int
    event_time_semantics: str
    available_at_semantics: str
    PIT_safe: bool
    missing_rate: Optional[float]
    coverage: Optional[float]
    data_version: str
    status: str = "READY"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DataCapability":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class DataCapabilityRegistry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY_PATH):
        self.path = Path(path)
        self._items: dict = {}

    def register(self, cap: DataCapability) -> None:
        self._items[cap.dataset_id] = cap

    def get(self, dataset_id: str) -> Optional[DataCapability]:
        return self._items.get(dataset_id)

    def items(self) -> list:
        return list(self._items.values())

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "research_end": RESEARCH_END,
            "datasets": [c.to_dict() for c in sorted(self._items.values(), key=lambda x: x.dataset_id)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REGISTRY_PATH) -> "DataCapabilityRegistry":
        reg = cls(path)
        p = Path(path)
        if p.exists():
            payload = json.loads(p.read_text(encoding="utf-8"))
            for d in payload.get("datasets", []):
                reg.register(DataCapability.from_dict(d))
        return reg

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([c.to_dict() for c in self.items()])

    def ready_datasets(self) -> list:
        return [c.dataset_id for c in self._items.values() if c.status == "READY"]


def build_local_mock_registry(vipdoc, gbbq_path, has_5m=False):
    """审计本地 TDX mock 数据并生成 DataCapabilityRegistry。"""
    from chanlun_trader.tdx_data import TdxData, list_a_stocks, read_day_file

    reg = DataCapabilityRegistry()
    vipdoc = Path(vipdoc)
    stocks = list_a_stocks(vipdoc)
    n_symbols = len(stocks)

    dmin, dmax, total = 99_999_999, 0, 0
    for st in stocks[:200]:
        try:
            df = read_day_file(st["path"])
            if df.empty:
                continue
            dmin = min(dmin, int(df["date"].min()))
            dmax = max(dmax, int(df["date"].max()))
            total += 1
        except Exception:
            pass
    reg.register(DataCapability(
        dataset_id="daily_ohlcva_raw",
        source="tdx_local_vipdoc",
        provider="TdxData(read_day_file)",
        fields=["date", "open", "high", "low", "close", "volume", "amount"],
        frequency="DAILY",
        earliest_date=dmin,
        latest_date=dmax,
        event_time_semantics="TRADE_DATE",
        available_at_semantics="T_CLOSE",
        PIT_safe=True,
        missing_rate=None,
        coverage=None,
        data_version="mock-tdx-2026.08",
        status="READY",
        notes=f"本地 .day 文件，抽样 {total}/{n_symbols} 只。FINAL TEST 封存由 ResearchDataAccessGuard 强制。",
    ))

    tdx = TdxData(str(vipdoc), str(gbbq_path), cache_dir="data/cache")
    gbbq = tdx._load_gbbq()
    gmin, gmax = int(gbbq["datetime"].min()), int(gbbq["datetime"].max())
    reg.register(DataCapability(
        dataset_id="corporate_action_gbbq",
        source="tdx_local_gbbq",
        provider="TdxData(gbbq)",
        fields=["code", "datetime", "category", "hongli_panqianliutong",
                "peigujia_qianzongguben", "songgu_qianzongguben", "peigu_houzongguben"],
        frequency="IRREGULAR",
        earliest_date=gmin,
        latest_date=gmax,
        event_time_semantics="EX_DATE",
        available_at_semantics="EX_DATE_CLOSE",
        PIT_safe=True,
        missing_rate=None,
        coverage=None,
        data_version="mock-tdx-2026.08",
        status="READY",
        notes="除权除息事件（含未来事件，研究时必须按 as_of 过滤；参见 qfq_pit）。",
    ))

    reg.register(DataCapability(
        dataset_id="benchmark_index_daily",
        source="tdx_local_vipdoc",
        provider="TdxData(get_benchmark)",
        fields=["date", "open", "high", "low", "close", "volume", "amount"],
        frequency="DAILY",
        earliest_date=dmin,
        latest_date=dmax,
        event_time_semantics="TRADE_DATE",
        available_at_semantics="T_CLOSE",
        PIT_safe=True,
        missing_rate=None,
        coverage=None,
        data_version="mock-tdx-2026.08",
        status="READY",
        notes="sh000300 / sh000001 指数日线。",
    ))

    if has_5m:
        reg.register(DataCapability(
            dataset_id="minute_5_ohlcva",
            source="tdx_local_vipdoc",
            provider="TdxData(read_lc5_file)",
            fields=["date", "minute", "open", "high", "low", "close", "amount", "volume"],
            frequency="5M",
            earliest_date=dmin,
            latest_date=dmax,
            event_time_semantics="BAR_CLOSE",
            available_at_semantics="BAR_CLOSE",
            PIT_safe=True,
            missing_rate=None,
            coverage=None,
            data_version="mock-tdx-2026.08",
            status="READY",
            notes="本地 .lc5 文件。",
        ))
    else:
        reg.register(DataCapability(
            dataset_id="minute_5_ohlcva",
            source="tdx_local_vipdoc",
            provider="TdxData(read_lc5_file)",
            fields=["date", "minute", "open", "high", "low", "close", "amount", "volume"],
            frequency="5M",
            earliest_date=0,
            latest_date=0,
            event_time_semantics="BAR_CLOSE",
            available_at_semantics="BAR_CLOSE",
            PIT_safe=True,
            missing_rate=None,
            coverage=None,
            data_version="",
            status="WAITING",
            notes="本地 mock 无 .lc5；5 分钟合成路径在引擎内可用。",
        ))

    for dsid, fields, freq in (
        ("lhb_professional", ["GP02/GP08/GP09/GP17/GP18/GP37/GP42"], "EVENT"),
        ("limit_up_ecology", ["GP14/GP15/GP22/GP24/GP33/GP34/SC03/SC04/SC23/SC30"], "EVENT"),
        ("money_flow", ["Zjl/Zjl_HB/SC20/SC40"], "EVENT"),
        ("financial_series", ["FN1/FN4/FN6/FN7/FN230/FN231/FN232/FN314"], "IRREGULAR"),
    ):
        reg.register(DataCapability(
            dataset_id=dsid,
            source="tdx_tq_local",
            provider="TQClient",
            fields=fields,
            frequency=freq,
            earliest_date=0,
            latest_date=0,
            event_time_semantics="PUBLISH_DATE",
            available_at_semantics="T_CLOSE_OR_ANNOUNCE_CLOSE",
            PIT_safe=True,
            missing_rate=None,
            coverage=None,
            data_version="",
            status="ONLINE_ONLY",
            notes="需通达信客户端在线（http://127.0.0.1:17709/）；未批量落盘前不得用于正式研究。",
        ))

    return reg
