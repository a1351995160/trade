"""在实际读文件前限制外部证据字段可引用的目录。"""
from pathlib import Path
import os


def within_root(path, root):
    root = Path(root).resolve()
    target_name = os.path.normcase(os.path.abspath(path))
    root_name = os.path.normcase(str(root))
    if not target_name.startswith(root_name + os.sep):
        raise PermissionError('EVIDENCE_PATH_OUTSIDE_APPROVED_ROOT')
    target = Path(target_name).resolve()
    if not target.is_relative_to(root):
        raise PermissionError('EVIDENCE_PATH_OUTSIDE_APPROVED_ROOT')
    return target
