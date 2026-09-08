"""Web 应用执行策略；模式不替代领域确认或身份校验。"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: str = "READ_ONLY"
    workspace_kind: str = "EXTERNAL"
    allow_structural: bool = False
    allow_process_start: bool = False

    @property
    def governance_allowed(self) -> bool:
        return self.mode == "GOVERNED" and self.workspace_kind == "SYNTHETIC"


def validate_research_root(root: str | Path | None, policy: ExecutionPolicy) -> Path | None:
    if policy.workspace_kind not in {"SYNTHETIC", "EXTERNAL"}:
        raise ValueError("INVALID_WORKSPACE_KIND")
    if root is None:
        if policy.governance_allowed or policy.workspace_kind == "SYNTHETIC":
            raise ValueError("RESEARCH_WORKSPACE_REQUIRED")
        return None
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts or not path.is_dir():
        raise ValueError("INVALID_RESEARCH_ROOT")

    def reject_link(item: Path) -> None:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("LINKED_RESEARCH_ROOT")

    for item in (path, *path.parents):
        reject_link(item)
    if policy.workspace_kind == "SYNTHETIC":
        # 不跟随链接；同时在请求前检查已落盘的子目录入口。
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                reject_link(Path(directory) / name)
    return path.resolve(strict=True)
