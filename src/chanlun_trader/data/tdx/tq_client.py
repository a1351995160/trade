"""TQ HTTP 统一客户端。

- 所有 TQ 请求必须通过 TQClient.request()，策略代码不得直接 requests/urllib 调用 TQ。
- 支持 health_check / request / retry / timeout / rate_limit / cache。
- 通达信专业数据接口（get_*_value 系列）经实测 HTTP 服务要求字段名
  `table_list`（而非 SKILL 文档中的 field_list），本客户端在 request() 中自动
  将 `field_list` 翻译为 `table_list`，因此调用方统一使用 `field_list` 语义。
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:17709/"
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 3
DEFAULT_RATE_LIMIT_INTERVAL = 0.2
DEFAULT_CACHE_DIR = Path("data/tdx/raw")


class TQError(RuntimeError):
    def __init__(self, method: str, code: Any, message: str):
        super().__init__(f"TQ {method} failed: ErrorId={code} {message}")
        self.method = method
        self.code = code
        self.message = message


class TQClient:
    """JSON-RPC over HTTP 的 TQ 客户端。"""

    _PRO_METHODS_WITH_TABLE_LIST = {
        "get_gpjy_value", "get_gpjy_value_by_date",
        "get_bkjy_value", "get_bkjy_value_by_date",
        "get_scjy_value", "get_scjy_value_by_date",
        "get_financial_data", "get_financial_data_by_date",
        "get_gp_one_data",
    }

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        rate_limit_interval: float = DEFAULT_RATE_LIMIT_INTERVAL,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        use_cache: bool = True,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.rate_limit_interval = rate_limit_interval
        self.cache_dir = Path(cache_dir)
        self.use_cache = use_cache
        self._last_request_ts = 0.0
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 基础能力 ----------------

    def health_check(self) -> dict:
        """连通性检查。通过 get_trading_calendar 小请求验证。"""
        return self.request("get_trading_calendar", {
            "market": "SH", "start_time": "20250101", "end_time": "20250110"
        }, use_cache=False)

    def request(self, method: str, params: dict | None = None, *,
                timeout: float | None = None,
                retries: int | None = None,
                use_cache: bool | None = None) -> Any:
        """统一请求入口。返回 result 中的业务数据（已去除 ErrorId 包装）。"""
        params = self._translate_params(method, params or {})
        use_cache = self.use_cache if use_cache is None else use_cache
        if use_cache:
            cached = self._read_cache(method, params)
            if cached is not None:
                return cached
        self._rate_limit()
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        payload = {"id": 1, "method": method, "params": params}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    self.base_url, data=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                result = self._parse_result(method, data)
                if use_cache:
                    self._write_cache(method, params, result)
                return result
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, TQError) as e:
                last_exc = e
                if attempt + 1 < retries:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    break
        raise RuntimeError(f"TQ {method} request failed after {retries} attempts: {last_exc}")

    def request_full(self, method: str, params: dict | None = None, *,
                     timeout: float | None = None,
                     retries: int | None = None) -> dict:
        """返回完整 result dict（含 ProDataPaged/stock_page_index 等元信息）。"""
        params = self._translate_params(method, params or {})
        self._rate_limit()
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        payload = {"id": 1, "method": method, "params": params}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    self.base_url, data=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise TQError(method, "HTTP", f"非 JSON 对象响应: {data!r}")
                if "error" in data:
                    err = data["error"]
                    raise TQError(method, err.get("code"), err.get("message", "JSON-RPC error"))
                result = data.get("result")
                if not isinstance(result, dict):
                    return result
                err_id = result.get("ErrorId", "0")
                if err_id != "0":
                    raise TQError(method, err_id, result.get("Error", ""))
                return result
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, TQError) as e:
                last_exc = e
                if attempt + 1 < retries:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"TQ {method} request_full failed after {retries} attempts: {last_exc}")

    def request_paged(self, method: str, params: dict | None = None, *,
                      timeout: float | None = None,
                      retries: int | None = None) -> Any:
        """分页请求专业数据：自动遍历 stock_total_pages 并合并 Value。"""
        params = self._translate_params(method, params or {})
        merged: dict = {}
        page = 0
        while True:
            page_params = dict(params)
            page_params["stock_page_index"] = page
            res = self.request_full(method, page_params, timeout=timeout, retries=retries)
            val = res.get("Value") if isinstance(res, dict) else None
            if isinstance(val, dict):
                merged.update(val)
            total_pages = res.get("stock_total_pages", 1) if isinstance(res, dict) else 1
            page += 1
            if page >= int(total_pages):
                break
        return merged

    # ---------------- 内部 ----------------

    @staticmethod
    def _translate_params(method: str, params: dict) -> dict:
        """统一语义：调用方传 field_list；专业数据方法自动翻译为 table_list。"""
        out = dict(params)
        if method in TQClient._PRO_METHODS_WITH_TABLE_LIST:
            if "field_list" in out and "table_list" not in out:
                out["table_list"] = out.pop("field_list")
        return out

    def _parse_result(self, method: str, data: dict) -> Any:
        if not isinstance(data, dict):
            raise TQError(method, "HTTP", f"非 JSON 对象响应: {data!r}")
        if "error" in data:
            err = data["error"]
            raise TQError(method, err.get("code"), err.get("message", "JSON-RPC error"))
        result = data.get("result")
        if not isinstance(result, dict):
            return result
        err_id = result.get("ErrorId", "0")
        if err_id != "0":
            raise TQError(method, err_id, result.get("Error", ""))
        if "Value" in result:
            return result["Value"]
        # get_stock_info / get_trading_calendar 等接口直接平铺在 result 中
        return result

    def _rate_limit(self) -> None:
        if self.rate_limit_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.rate_limit_interval:
            time.sleep(self.rate_limit_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _cache_key(self, method: str, params: dict) -> str:
        raw = json.dumps({"m": method, "p": params}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _cache_path(self, method: str, params: dict) -> Path:
        return self.cache_dir / f"{method}_{self._cache_key(method, params)}.json"

    def _read_cache(self, method: str, params: dict) -> Any | None:
        p = self._cache_path(method, params)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _write_cache(self, method: str, params: dict, result: Any) -> None:
        try:
            p = self._cache_path(method, params)
            p.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass


def check_windows() -> bool:
    return platform.system() == "Windows"
