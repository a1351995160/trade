"""行业归属数据读取。

优先读取 tdx-research-cli 生成的 SQLite 快照库（industry_membership 表）；
若快照库不存在或为空，则回退到通达信本地 `tdxhy.cfg` 文件。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def load_industry_map(tdx_cfg: dict) -> dict[str, str]:
    """返回 {股票代码: 行业代码}。

    同时读取 tdxhy.cfg 与 tdx-research-cli 快照库：快照库数据优先，
    tdxhy.cfg 补齐快照库未覆盖的股票，避免部分快照（如 limit 300）导致缺失。
    """
    merged: dict[str, str] = {}
    industry_cfg = tdx_cfg.get("industry_cfg")
    if industry_cfg:
        path = Path(industry_cfg)
        if path.exists():
            merged.update(_load_from_tdxhy_cfg(path))
    research_db = tdx_cfg.get("research_db")
    if research_db:
        path = Path(research_db)
        if path.exists():
            merged.update(_load_from_research_db(path))
    return merged


def _load_from_research_db(path: Path) -> dict[str, str]:
    """从 tdx-research-cli 快照库读取行业映射。"""
    try:
        with sqlite3.connect(str(path)) as con:
            cur = con.cursor()
            rows = cur.execute("SELECT payload_json FROM industry_membership").fetchall()
    except (sqlite3.Error, OSError):
        return {}
    mapping: dict[str, str] = {}
    for (payload_json,) in rows:
        try:
            obj = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        code = str(obj.get("code", "")).strip()
        industry = str(obj.get("industry_code", "")).strip()
        if code and industry:
            mapping[code] = industry
    return mapping


def _load_from_tdxhy_cfg(path: Path) -> dict[str, str]:
    """解析通达信 `tdxhy.cfg`（格式：0|code|industry_code|||X...）。"""
    mapping: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="gbk", errors="ignore").splitlines()
    except OSError:
        return {}
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 3:
            code = parts[1].strip()
            industry = parts[2].strip()
            if code and industry:
                mapping[code] = industry
    return mapping


def load_security_master(tdx_cfg: dict) -> dict[str, dict]:
    """读取 tdx-research-cli 快照库的 security_master，返回 {代码: 字段}。"""
    research_db = tdx_cfg.get("research_db")
    if not research_db:
        return {}
    path = Path(research_db)
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(str(path)) as con:
            rows = con.execute("SELECT payload_json FROM security_master").fetchall()
    except (sqlite3.Error, OSError):
        return {}
    out: dict[str, dict] = {}
    for (payload_json,) in rows:
        try:
            obj = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        code = str(obj.get("code", "")).strip()
        if code:
            out[code] = obj
    return out


def eligible_codes(tdx_cfg: dict) -> set[str] | None:
    """返回可交易 A 股代码集合；没有快照库时返回 None，表示不启用该过滤。

    过滤口径与 tdx-research-cli 正式股票池对齐：只保留 security_master 中
    eligible=true，且名称不含 ST/退 的证券。
    """
    master = load_security_master(tdx_cfg)
    if not master:
        return None
    codes = set()
    for code, obj in master.items():
        name = str(obj.get("name", ""))
        if obj.get("eligible") and "ST" not in name.upper() and "退" not in name:
            codes.add(code)
    return codes
