"""Small immutable-record helpers used by the research factory control plane."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def copy_mapping(value: Any) -> dict[str, Any]:
    return json.loads(canonical_json(dict(value or {})))
