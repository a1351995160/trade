"""TDX 原生数据层：TQClient + Providers + Parquet 缓存。

统一入口：
    from chanlun_trader.data.tdx import TQClient, TDXProviders
"""
from .tq_client import TQClient
from .providers import TDXProviders

__all__ = ["TQClient", "TDXProviders"]
