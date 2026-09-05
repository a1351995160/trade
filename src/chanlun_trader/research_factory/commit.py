"""Durable logical commit markers and identity-conflict protection."""
from __future__ import annotations

from pathlib import Path
import json
import os
from typing import Any, Mapping

from .common import now_timestamp, stable_hash


class CommitIdentityConflictError(RuntimeError):
    code = "COMMIT_IDENTITY_CONFLICT"


class DurableCommitLedgerV1:
    """Append-only logical commit marker ledger.

    A marker is keyed by ``marker_type + logical_identity``.  Replaying the
    same identity and payload returns ``ALREADY_COMMITTED``; different content
    under the same identity fails closed.
    """

    schema_version = "durable-commit-ledger-v1"
    code = CommitIdentityConflictError.code

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._markers: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _key(marker_type: str, logical_identity: str) -> str:
        return f"{marker_type}:{logical_identity}"

    def ensure(self, marker_type: str, logical_identity: str, payload: Mapping[str, Any] | None = None) -> str:
        marker_type = str(marker_type)
        logical_identity = str(logical_identity)
        payload_dict = dict(payload or {})
        key = self._key(marker_type, logical_identity)
        payload_hash = stable_hash(payload_dict)
        existing = self._markers.get(key)
        if existing is not None:
            if existing.get("payload_hash") != payload_hash:
                raise CommitIdentityConflictError(f"{self.code}: {key}")
            return "ALREADY_COMMITTED"
        self._markers[key] = {
            "marker_type": marker_type,
            "logical_identity": logical_identity,
            "payload_hash": payload_hash,
            "payload": payload_dict,
            "created_at": now_timestamp(),
        }
        self._persist()
        return "COMMITTED"

    def has(self, marker_type: str, logical_identity: str) -> bool:
        return self._key(str(marker_type), str(logical_identity)) in self._markers

    def marker(self, marker_type: str, logical_identity: str) -> Mapping[str, Any] | None:
        return self._markers.get(self._key(str(marker_type), str(logical_identity)))

    def markers(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._markers[key] for key in sorted(self._markers))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "markers": list(self.markers()), "ledger_hash": stable_hash(list(self.markers()))}

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for marker in payload.get("markers", ()):
            if isinstance(marker, Mapping):
                key = self._key(str(marker.get("marker_type")), str(marker.get("logical_identity")))
                self._markers[key] = dict(marker)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
