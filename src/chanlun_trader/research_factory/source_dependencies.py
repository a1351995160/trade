"""正式源码 checkout 的依赖定位；研究数据目录不提供执行代码。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3]
CORRECTED_SCRIPT = SOURCE_ROOT / "scripts/run_engine_corrected_phase4_v3.py"


def load_corrected_module():
    """只接受部署源码中的原实现；缺失时保留明确的源码阻断。"""
    if not CORRECTED_SCRIPT.is_file():
        raise ModuleNotFoundError(
            f"BLOCKED_MISSING_SOURCE: {CORRECTED_SCRIPT}; "
            "required corrected runner and transitive legacy helpers are absent"
        )
    if CORRECTED_SCRIPT.is_symlink() or CORRECTED_SCRIPT.resolve() != CORRECTED_SCRIPT:
        raise ImportError("SOURCE_PATH_CONFLICT: corrected runner")
    spec = importlib.util.spec_from_file_location("run_engine_corrected_phase4_v3", CORRECTED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
