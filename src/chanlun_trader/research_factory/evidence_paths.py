"""在实际读文件前限制外部证据字段可引用的目录。"""
from pathlib import Path
import os


def within_root(path, root):
    root = Path(root).resolve()
    target = Path(os.path.abspath(path))
    if not target.is_relative_to(root):
        raise PermissionError('EVIDENCE_PATH_OUTSIDE_APPROVED_ROOT')
    target = target.resolve()
    if not target.is_relative_to(root):
        raise PermissionError('EVIDENCE_PATH_OUTSIDE_APPROVED_ROOT')
    return target
