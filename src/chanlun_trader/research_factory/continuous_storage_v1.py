"""派发前检查冻结的原件存储边界；保留超额原件，不自动删审计资料。"""
from pathlib import Path
import shutil

from .exploration_governance import read_json


def storage_status(research):
    view = research.campaign.peek_status()
    limits = view['base_authorization']['scope_policy']['summary']['storage_limits']
    roots = {research.campaign.directory.resolve()}
    for path in research.root.glob('**/TASK.json'):
        value = read_json(path)
        task_id = value.get('task_id')
        if isinstance(task_id, str) and len(task_id) == 64 and all(c in '0123456789abcdef' for c in task_id):
            task_root = Path(research.submission.root).absolute() / task_id
            if task_root.is_dir():
                roots.add(task_root)
            intent_path = Path(research.submission.root).absolute() / 'continuous_intents' / (task_id + '.json')
            if intent_path.exists():
                intent = read_json(intent_path)
                scan_id = intent.get('scan_id')
                if (intent.get('task_id') != task_id or not isinstance(scan_id, str)
                        or len(scan_id) != 64 or any(c not in '0123456789abcdef' for c in scan_id)):
                    raise PermissionError('CONTINUOUS_STORAGE_SCAN_REFERENCE_INVALID')
                scan_root = Path(research.submission.root).absolute() / 'signal-scans' / scan_id
                if scan_root.is_dir():
                    roots.add(scan_root)
    # 配置允许账户目录位于任务目录之内，嵌套原件只计一次。
    roots = {root for root in roots if not any(parent != root and parent in root.parents for parent in roots)}
    total = 0
    for root in roots:
        if root.resolve() != root:
            raise PermissionError('CONTINUOUS_STORAGE_ROOT_REDIRECTED')
        for path in root.rglob('*'):
            if path.is_symlink():
                raise PermissionError('CONTINUOUS_STORAGE_LINK_NOT_ALLOWED')
            if path.is_file():
                total += path.stat().st_size
    free = min(shutil.disk_usage(root).free for root in roots)
    reasons = []
    if total >= limits['maximum_artifact_bytes']:
        reasons.append('CONTINUOUS_ARTIFACT_STORAGE_LIMIT')
    if free < limits['minimum_free_bytes']:
        reasons.append('CONTINUOUS_DISK_FREE_INSUFFICIENT')
    return {'status': 'WAITING_STORAGE' if reasons else 'READY', 'artifact_bytes': total,
            'free_bytes': free, 'limits': limits, 'waiting_reasons': reasons,
            'enforcement': 'BEFORE_NEW_DISPATCH_RETAIN_ALREADY_PRODUCED_AUDIT_ARTIFACTS'}
