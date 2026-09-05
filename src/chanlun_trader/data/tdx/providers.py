"""TDX 数据 Provider：统一语义接口，策略层不感知 GP/SC/FN/HTTP 细节。"""
from __future__ import annotations

import pandas as pd

from .cache import save_clean, save_raw
from .manifest import record_manifest
from .models import (
    Availability,
    DailyBar,
    FinanceSnapshot,
    LHBEvent,
    LimitUpEvent,
)
from .tq_client import TQClient

SC_FIELD_MAP = {
    "limit_up": "SC03", "limit_down": "SC04",
    "board_seal_money": "SC15", "lhb_total": "SC16",
    "lhb_inst": "SC17", "lhb_broker": "SC18", "lhb_hgt": "SC19",
    "streak": "SC23", "limit_ex_st": "SC24", "height": "SC30",
    "updown_count": "SC31", "total_seal": "SC33",
    "board_change": "SC35", "once_limit_ex_st": "SC36", "pct5_count": "SC39",
}
GP_FIELD_MAP = {
    "lhb": "GP02", "lhb_inst_sell": "GP08", "lhb_inst_buy": "GP09",
    "limit_seal": "GP14", "limit_status": "GP15", "lhb_broker": "GP17",
    "lhb_hgt": "GP18", "limit_ratio": "GP22", "limit_first": "GP24",
    "limit_down": "GP33", "limit_down_first": "GP34", "lhb_streak": "GP37",
    "lhb_pro_net": "GP42",
}
FIN_FIELD_MAP = {
    "eps": "FN1", "roe": "FN6", "bps": "FN4", "ocfps": "FN7",
    "revenue": "FN230", "oper_profit": "FN231", "net_profit": "FN232",
    "total_assets": "FN40", "total_liab": "FN63", "equity": "FN72",
    "ocf": "FN234", "announce_date": "FN314",
}


class TDXProvider:
    """Provider 基类。"""

    def __init__(self, client: TQClient) -> None:
        self.client = client

    def _record(self, dataset: str, fields: str, start: str, end: str,
                actual_start: object, actual_end: object, rows: int, symbols: int,
                checksum: str, errors: str = "") -> None:
        try:
            record_manifest({
                "source": "tdx-tq-local", "dataset": dataset, "field": fields,
                "start_date": start, "end_date": end,
                "actual_start": str(actual_start), "actual_end": str(actual_end),
                "rows": rows, "symbols": symbols,
                "checksum": checksum, "errors": errors,
            })
        except Exception:
            pass


class TDXDailyProvider(TDXProvider):
    """日线 Provider（TQ 原生，不复权/前复权）。"""

    def get_daily(self, codes: list[str], start: str, end: str, dividend_type: str = "none") -> pd.DataFrame:
        raw = self.client.request("get_market_data", {
            "stock_list": codes, "period": "1d",
            "start_time": start, "end_time": end,
            "dividend_type": dividend_type,
        })
        save_raw("daily", start, end, raw, symbols=codes)
        rows = []
        for code, tab in (raw or {}).items():
            if not isinstance(tab, dict):
                continue
            dates = tab.get("Date") or []
            opens = tab.get("Open") or []
            highs = tab.get("High") or []
            lows = tab.get("Low") or []
            closes = tab.get("Close") or []
            vols = tab.get("Volume") or []
            amts = tab.get("Amount") or []
            for i, d in enumerate(dates):
                def f(arr, j):
                    try:
                        return float(arr[j])
                    except Exception:
                        return None
                rows.append(DailyBar(
                    code=code, date=int(d),
                    open=f(opens, i), high=f(highs, i), low=f(lows, i), close=f(closes, i),
                    volume=f(vols, i), amount=f(amts, i),
                    available_at=int(d) + 1,
                ))
        df = pd.DataFrame([b.__dict__ for b in rows])
        if not df.empty:
            save_clean("daily", df)
        return df

    def capability(self) -> dict:
        return {"daily": Availability.READY.value}


class TDX5MinProvider(TDXProvider):
    """5 分钟 Provider。本地 5 分钟数据未就绪时保持 NOT_AVAILABLE。"""

    def get_5m(self, codes: list[str], start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("5分钟数据尚未 READY，本地 vipdoc/minline 为空且未下载 TQ 5m 历史")

    def capability(self) -> dict:
        return {"5m": Availability.UNAVAILABLE.value}


class TDXProDataProvider(TDXProvider):
    """专业数据低层 Provider（股票/板块/市场三类）。上层语义 Provider 基于此构建。"""

    def get_stock_pro(self, codes: list[str], fields: list[str], start: str, end: str) -> dict:
        return self.client.request_paged("get_gpjy_value", {
            "stock_list": codes, "field_list": fields, "start_time": start, "end_time": end,
        })

    def get_market_pro(self, fields: list[str], start: str, end: str) -> dict:
        return self.client.request("get_scjy_value", {
            "field_list": fields, "start_time": start, "end_time": end,
        })

    def get_sector_pro(self, codes: list[str], fields: list[str], start: str, end: str) -> dict:
        return self.client.request_paged("get_bkjy_value", {
            "stock_list": codes, "field_list": fields, "start_time": start, "end_time": end,
        })


class TDXMarketSentimentProvider(TDXProvider):
    """市场情绪：涨停/跌停/连板/高度/封单/涨跌家数。"""

    def __init__(self, client: TQClient, pro: TDXProDataProvider) -> None:
        super().__init__(client)
        self.pro = pro

    def get_sentiment(self, start: str, end: str) -> pd.DataFrame:
        fields = list(SC_FIELD_MAP.values())
        raw = self.pro.get_market_pro(fields, start, end)
        save_raw("scjy", start, end, raw)
        rows: list[dict] = []
        for field, items in (raw or {}).items():
            semantic = field
            for inv_name, inv_field in SC_FIELD_MAP.items():
                if inv_field == field:
                    semantic = inv_name
                    break
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                vals = it.get("Value") or []
                d = int(it.get("Date"))
                for i, x in enumerate(vals):
                    try:
                        v = float(x)
                    except Exception:
                        v = None
                    rows.append({"date": d, "field": field, "semantic": semantic, "pos": i, "value": v})
        df = pd.DataFrame(rows)
        if not df.empty:
            save_clean("market_sentiment", df)
            self._record("market_sentiment", ",".join(fields), start, end,
                         df["date"].min(), df["date"].max(), len(df), 1, "raw:" + str(len(raw or {})))
        return df

    def capability(self) -> dict:
        return {"market_sentiment": Availability.READY.value}


class TDXLHBProvider(TDXProvider):
    """龙虎榜：个股上榜金额、机构/营业部/沪深股通/专业机构净额。"""

    FIELDS = ["GP02", "GP08", "GP09", "GP17", "GP18", "GP37", "GP42"]

    def __init__(self, client: TQClient, pro: TDXProDataProvider) -> None:
        super().__init__(client)
        self.pro = pro

    def get_stock_events(self, code: str, start: str, end: str) -> list[LHBEvent]:
        raw = self.pro.get_stock_pro([code], self.FIELDS, start, end)
        return self._tab_to_events(code, (raw or {}).get(code) or {})

    def get_events_batch(self, codes: list[str], start: str, end: str) -> list[LHBEvent]:
        raw = self.pro.get_stock_pro(codes, self.FIELDS, start, end)
        save_raw("lhb", start, end, raw, symbols=codes)
        events: list[LHBEvent] = []
        for code, tab in (raw or {}).items():
            if isinstance(tab, dict) and tab.get("GP02"):
                events.extend(self._tab_to_events(code, tab))
        self._record("lhb", ",".join(self.FIELDS), start, end,
                     start, end, len(events), len(codes), "batch")
        return events

    @staticmethod
    def _tab_to_events(code: str, tab: dict) -> list[LHBEvent]:
        gp02 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP02") or [])}
        gp08 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP08") or [])}
        gp09 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP09") or [])}
        gp17 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP17") or [])}
        gp18 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP18") or [])}
        gp42 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP42") or [])}
        events: list[LHBEvent] = []
        for d, v in gp02.items():
            def f(vv, i):
                try:
                    return float(vv[i])
                except Exception:
                    return None
            buy = f(v, 0); sell = f(v, 1)
            events.append(LHBEvent(
                code=code, event_date=d, buy_amount=buy, sell_amount=sell,
                net_amount=round(buy - sell, 4) if buy is not None and sell is not None else None,
                inst_buy_amount=f(gp09.get(d), 1), inst_sell_amount=f(gp08.get(d), 1),
                broker_buy_amount=f(gp17.get(d), 0), broker_sell_amount=f(gp17.get(d), 1),
                hgt_buy_amount=f(gp18.get(d), 0), hgt_sell_amount=f(gp18.get(d), 1),
                pro_net_buy=f(gp42.get(d), 0),
                available_at=d + 1, earliest_tradable_time=d + 1,
            ))
        return events

    def capability(self) -> dict:
        return {"lhb": Availability.READY.value}


class TDXLimitUpProvider(TDXProvider):
    """涨停/炸板/连板/封单：个股维度 GP14/15/22/24/33/34。"""

    FIELDS = ["GP14", "GP15", "GP22", "GP24", "GP33", "GP34"]

    def __init__(self, client: TQClient, pro: TDXProDataProvider) -> None:
        super().__init__(client)
        self.pro = pro

    def get_stock_events(self, code: str, start: str, end: str) -> list[LimitUpEvent]:
        raw = self.pro.get_stock_pro([code], self.FIELDS, start, end)
        return self._tab_to_events(code, (raw or {}).get(code) or {})

    def get_events_batch(self, codes: list[str], start: str, end: str) -> list[LimitUpEvent]:
        raw = self.pro.get_stock_pro(codes, self.FIELDS, start, end)
        save_raw("limit_up", start, end, raw, symbols=codes)
        events: list[LimitUpEvent] = []
        for code, tab in (raw or {}).items():
            if isinstance(tab, dict) and tab.get("GP15"):
                events.extend(self._tab_to_events(code, tab))
        self._record("limit_up", ",".join(self.FIELDS), start, end,
                     start, end, len(events), len(codes), "batch")
        return events

    @staticmethod
    def _tab_to_events(code: str, tab: dict) -> list[LimitUpEvent]:
        gp15 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP15") or [])}
        gp14 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP14") or [])}
        gp22 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP22") or [])}
        gp24 = {int(it.get("Date")): it.get("Value") for it in (tab.get("GP24") or [])}
        events: list[LimitUpEvent] = []
        for d, v in gp15.items():
            def f(vv, i):
                try:
                    return float(vv[i])
                except Exception:
                    return None
            status = int(float(v[0])) if v else 0
            if status == 0:
                continue
            events.append(LimitUpEvent(
                code=code, event_date=d, status=status,
                seal_amount=f(v, 1),
                first_limit_time=str(gp24.get(d, [""])[0]) if gp24.get(d) else None,
                open_count=int(f(gp14.get(d), 1)) if gp14.get(d) and f(gp14.get(d), 1) is not None else None,
                max_seal_amount=f(gp24.get(d), 1),
                seal_ratio=f(gp22.get(d), 0),
                available_at=d + 1, earliest_tradable_time=d + 1,
            ))
        return events

    def capability(self) -> dict:
        return {"limit_up": Availability.READY.value}


class TDXFinanceProvider(TDXProvider):
    """财务数据 Point-In-Time：report_type=announce_time。"""

    def __init__(self, client: TQClient) -> None:
        super().__init__(client)

    def get_finance(self, codes: list[str], start: str, end: str,
                    fields: list[str] | None = None) -> pd.DataFrame:
        if fields is None:
            fields = list(FIN_FIELD_MAP.values())
        raw = self.client.request("get_financial_data", {
            "stock_list": codes, "field_list": fields,
            "start_time": start, "end_time": end, "report_type": "announce_time",
        })
        save_raw("finance", start, end, raw, symbols=codes)
        rows = []
        for code, tab in (raw or {}).items():
            if not isinstance(tab, dict):
                continue
            anns = tab.get("announce_time") or []
            tags = tab.get("tag_time") or []
            for i, ann in enumerate(anns):
                row = {
                    "code": code,
                    "report_period": int(tags[i]) if i < len(tags) and tags[i] else None,
                    "announce_date": int(ann),
                }
                for fld, arr in tab.items():
                    if fld in ("announce_time", "tag_time"):
                        continue
                    try:
                        row[fld] = float(arr[i]) if arr and arr[i] is not None else None
                    except Exception:
                        row[fld] = None
                rows.append(row)
        df = pd.DataFrame(rows)
        if not df.empty:
            save_clean("finance", df)
            self._record("finance", ",".join(fields), start, end,
                         df["announce_date"].min(), df["announce_date"].max(), len(df), len(codes), "raw:" + str(len(raw or {})))
        return df

    def get_snapshot(self, code: str, as_of_date: int) -> FinanceSnapshot | None:
        """严格 PIT：as_of_date 盘后可知的最近公告财务。"""
        from datetime import datetime, timedelta
        d0 = datetime.strptime(str(as_of_date), "%Y%m%d")
        start = (d0 - timedelta(days=400)).strftime("%Y%m%d")
        end = d0.strftime("%Y%m%d")
        df = self.get_finance([code], start, end)
        if df.empty:
            return None
        df = df[df["announce_date"] <= as_of_date].sort_values("announce_date")
        if df.empty:
            return None
        last = df.iloc[-1]
        fv = {}
        for k in last.index:
            if k.startswith("FN") and last[k] == last[k]:
                fv[k] = float(last[k])
        return FinanceSnapshot(
            code=code, report_period=int(last["report_period"]), announce_date=int(last["announce_date"]),
            field_values=fv, available_at=int(last["announce_date"]) + 1,
            earliest_tradable_time=int(last["announce_date"]) + 1,
        )

    def capability(self) -> dict:
        return {"finance": Availability.READY.value}


class TDXIndustryProvider(TDXProvider):
    """行业/板块：当前快照。历史成分不可得 -> 历史回测 PIT_UNSAFE。"""

    def get_industry_current(self, code: str) -> dict:
        r = self.client.request("get_stock_info", {"stock_code": code})
        return {"industry_code": r.get("rs_hycode_sim"), "industry_name": r.get("rs_hyname"),
                "block_index": r.get("blockzscode")}

    def get_sector_list(self) -> list[str]:
        return self.client.request("get_sector_list", {})

    def get_sector_members(self, block_code: str) -> list[str]:
        return self.client.request("get_stock_list_in_sector", {"block_code": block_code, "list_type": 1})

    def capability(self) -> dict:
        return {"industry_current": Availability.READY.value,
                "industry_history": Availability.UNAVAILABLE.value}


class TDXProviders:
    """统一门面：策略层只使用此类。"""

    def __init__(self, client: TQClient | None = None) -> None:
        self.client = client or TQClient()
        self.pro = TDXProDataProvider(self.client)
        self.daily = TDXDailyProvider(self.client)
        self.min5 = TDX5MinProvider(self.client)
        self.sentiment = TDXMarketSentimentProvider(self.client, self.pro)
        self.lhb = TDXLHBProvider(self.client, self.pro)
        self.limit = TDXLimitUpProvider(self.client, self.pro)
        self.finance = TDXFinanceProvider(self.client)
        self.industry = TDXIndustryProvider(self.client)

    def health_check(self) -> dict:
        return self.client.health_check()
