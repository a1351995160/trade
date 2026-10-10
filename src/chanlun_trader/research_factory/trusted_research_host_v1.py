"""原生命周期任务的单宿主；不自行创建授权或重放未知账户作业。"""
from datetime import datetime, timezone
import importlib.util
import json
import math
import re
from pathlib import Path
import time
import threading
import uuid

from ..research_daemon_state import DaemonInstanceLockV1
from .common import stable_hash


def environment_check():
    import platform
    dependencies = ('pandas', 'numpy', 'pyarrow', 'yaml', 'pytz')
    missing = [name for name in dependencies if importlib.util.find_spec(name) is None]
    return {'ready': not missing, 'missing': missing, 'python': platform.python_version(),
            'model_required': False}


class TrustedResearchHostV1:
    def __init__(self, service, *, interval=5):
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 0 < interval <= 60:
            raise ValueError('HOST_INTERVAL_INVALID')
        self.service, self.interval = service, interval
        self.root = service.root / 'lifecycle_host'
        self.lock = DaemonInstanceLockV1(self.root / 'HOST.lock', 'LIFECYCLE_HOST')

    def status(self):
        path = self.root / 'HOST.lock'
        if not path.exists():
            return {'status': 'OFFLINE', 'background_enabled': False}
        if path.resolve() != path:
            return {'status': 'BLOCKED', 'background_enabled': False, 'reason': 'HOST_LOCK_REDIRECTED'}
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if (type(record['pid']) is not int or record['pid'] <= 0 or not isinstance(record['owner_id'], str)
                    or not re.fullmatch(r'[0-9a-f]{32}', record['owner_id'])):
                raise ValueError('HOST_LOCK_INVALID')
            datetime.fromisoformat(record['heartbeat_at'].replace('Z', '+00:00'))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {'status': 'BLOCKED', 'background_enabled': False, 'reason': 'HOST_LOCK_UNREADABLE'}
        live = self.lock._pid_alive(int(record['pid']))
        heartbeat = datetime.fromisoformat(record['heartbeat_at'].replace('Z', '+00:00'))
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        return {'status': 'OFFLINE' if not live else 'RUNNING' if 0 <= age < 120 else 'HEARTBEAT_STALE',
                'background_enabled': live and 0 <= age < 120, 'pid': record['pid'],
                'heartbeat_at': record['heartbeat_at'], 'owner_id': record['owner_id']}

    def stop(self):
        current = self.status()
        if current['status'] in {'OFFLINE', 'BLOCKED'}:
            return current
        # 绑定本次宿主，旧停止指令不能停止下一次进程。
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / ('STOP_' + current['owner_id'] + '.json')
        if not target.exists():
            with target.open('x', encoding='utf-8') as stream:
                json.dump({'owner_id': current['owner_id']}, stream)
        return {'status': 'STOP_REQUESTED', 'after_active_operation': True}

    def tick(self):
        result = {}
        for config in sorted(self.service.jobs.root.glob('*/CONFIG.json')):
            owner = getattr(self.lock, 'owner_id', None)
            if (owner and (self.root / ('STOP_' + owner + '.json')).exists()) or (getattr(self, '_halt', None) is not None and self._halt.is_set()):
                break
            name = config.parent.name
            try:
                result[name] = self.service.jobs.tick(name)
            except (ValueError, OSError, RuntimeError) as exc:
                result[name] = {'status': 'BLOCKED', 'reason': str(exc)}
        continuous = getattr(self.service, 'continuous', None)
        if continuous is not None:
            for name in continuous.researches:
                owner = getattr(self.lock, 'owner_id', None)
                if ((owner and (self.root / ('STOP_' + owner + '.json')).exists())
                        or (getattr(self, '_halt', None) is not None and self._halt.is_set())):
                    break
                try:
                    status = continuous.status(name)
                    if status.get('started') is True and status.get('status') not in {
                            'PAUSED', 'REVOKED', 'GOAL_MET', 'BUSINESS_GOAL_MET', 'COMPLETED', 'OWNER_APPROVAL_REQUIRED'}:
                        result['continuous:' + name] = continuous.perform(name, 'advance')
                    else:
                        result['continuous:' + name] = status
                except (ValueError, OSError, KeyError, PermissionError, RuntimeError) as exc:
                    result['continuous:' + name] = {'status': 'BLOCKED', 'reason': str(exc)}
        return result

    def run(self, *, once=False):
        environment = environment_check()
        if not environment['ready']:
            raise ValueError('HOST_DEPENDENCIES_MISSING:' + ','.join(environment['missing']))
        if self.root.resolve() != self.root:
            raise ValueError('HOST_ROOT_REDIRECTED')
        self.lock.acquire(run_id=uuid.uuid4().hex)
        stop = self.root / ('STOP_' + self.lock.owner_id + '.json')
        result = {}
        halt = threading.Event()
        self._halt = halt
        failures = []
        def heartbeat():
            while not halt.wait(min(self.interval, 30)):
                try:
                    self.lock.heartbeat()
                except Exception as exc:
                    failures.append(exc)
                    halt.set()
        thread = threading.Thread(target=heartbeat, name='trusted-research-heartbeat', daemon=True)
        thread.start()
        try:
            while not stop.exists() and not halt.is_set():
                result = self.tick()
                if failures:
                    raise RuntimeError('HOST_HEARTBEAT_FAILED') from failures[0]
                if once:
                    break
                halt.wait(self.interval)
        finally:
            halt.set()
            thread.join(timeout=35)
            self.lock.release()
        return {'status': 'STOPPED', 'jobs': result, 'environment': environment}
