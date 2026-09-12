"""在实际读文件前限制外部证据字段可引用的目录。"""
from pathlib import Path


def within_root(path, root):
    root = Path(root).resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root):
        raise PermissionError('EVIDENCE_PATH_OUTSIDE_APPROVED_ROOT')
    return target
