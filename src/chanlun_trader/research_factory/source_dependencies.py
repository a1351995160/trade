"""正式源码 checkout 的依赖定位；研究数据目录不提供执行代码。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3]
CORRECTED_SCRIPT = SOURCE_ROOT / "scripts/run_engine_corrected_phase4_v3.py"
LEGACY_SCRIPT = SOURCE_ROOT / "scripts/run_automated_strategy_validation_v1_rerun_v2.py"


def load_corrected_module():
    """加载审核快照的受控部署副本；缺失时保留明确的源码阻断。"""
    return _load_script(CORRECTED_SCRIPT)


def load_legacy_module():
    return _load_script(LEGACY_SCRIPT)


def _load_script(path):
    if not path.is_file():
        raise ModuleNotFoundError(
            f"BLOCKED_MISSING_SOURCE: {path}; "
            "required corrected runner and transitive legacy helpers are absent"
        )
    if path.is_symlink() or path.resolve() != path:
        raise ImportError(f"SOURCE_PATH_CONFLICT: {path.name}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
