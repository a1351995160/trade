"""正式研究/回测 run identity。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REQUIRED_RUN_FIELDS = (
    "code_commit", "dirty_worktree", "config_hash", "strategy_hash",
    "data_manifest_hash", "universe_version_hash", "feature_version_hash",
    "label_version_hash", "execution_model_version", "fee_model_version",
    "slippage_model_version", "calendar_version",
)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git(args: list[str], default: str) -> str:
    try:
        # Git paths may contain the required Chinese human-report filenames;
        # decode the subprocess boundary as UTF-8 instead of the Windows code page.
        return subprocess.check_output(
            ["git", *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip() or default
    except (OSError, subprocess.CalledProcessError):
        return default


def build_run_manifest(*, config_hash: str, strategy_hash: str,
                       data_manifest_hash: str = "UNSPECIFIED",
                       universe_version_hash: str = "UNSPECIFIED",
                       feature_version_hash: str = "UNSPECIFIED",
                       label_version_hash: str = "UNSPECIFIED",
                       execution_model_version: str = "UNSPECIFIED",
                       fee_model_version: str = "UNSPECIFIED",
                       slippage_model_version: str = "UNSPECIFIED",
                       calendar_version: str = "UNSPECIFIED") -> dict[str, Any]:
    commit = _git(["rev-parse", "HEAD"], "UNKNOWN")
    dirty = bool(_git(["status", "--porcelain"], "UNKNOWN"))
    manifest = {
        "code_commit": commit,
        "dirty_worktree": dirty,
        "config_hash": config_hash,
        "strategy_hash": strategy_hash,
        "data_manifest_hash": data_manifest_hash,
        "universe_version_hash": universe_version_hash,
        "feature_version_hash": feature_version_hash,
        "label_version_hash": label_version_hash,
        "execution_model_version": execution_model_version,
        "fee_model_version": fee_model_version,
        "slippage_model_version": slippage_model_version,
        "calendar_version": calendar_version,
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    manifest["reproducibility_status"] = (
        "PASS" if all(manifest[k] not in ("UNKNOWN", "UNSPECIFIED", "") for k in REQUIRED_RUN_FIELDS)
        else "PARTIAL"
    )
    return manifest


def validate_run_manifest(manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [key for key in REQUIRED_RUN_FIELDS if key not in manifest]
    return not missing, missing


def write_immutable_run_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    path = Path(path)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old != manifest:
            raise FileExistsError(f"immutable run manifest conflict: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
