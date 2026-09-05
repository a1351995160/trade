"""配置加载模块。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> dict:
    """读取 config.yaml，并做必要的路径展开。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 把输出目录转成绝对路径
    report = cfg.get("report", {})
    out_dir = Path(report.get("output_dir", "data/output"))
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    report["output_dir"] = str(out_dir)
    tdx = cfg.get("tdx", {})
    cache_dir = Path(tdx.get("cache_dir", "data/cache"))
    if not cache_dir.is_absolute():
        cache_dir = PROJECT_ROOT / cache_dir
    tdx["cache_dir"] = str(cache_dir)
    return cfg
