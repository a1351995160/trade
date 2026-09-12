"""Persistent, append-only search budget accounting."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os

from .common import now_timestamp, stable_hash


class BudgetExhaustedError(RuntimeError):
    pass


class BudgetLedgerMismatchError(RuntimeError):
    """Persisted budget state cannot be reconciled safely."""

    code = "BUDGET_LEDGER_MISMATCH"

    def __init__(self, message: str, *, reason: str = code):
        super().__init__(message)
        self.reason = reason


@dataclass
class _Bucket:
    kind: str
    key: str
    limit: int
    used: int = 0
    reserved: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used - self.reserved)

    @property
    def exhausted(self) -> bool:
        return self.used + self.reserved >= self.limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "key": self.key, "limit": self.limit,
            "used": self.used, "remaining": self.remaining,
            "reserved": self.reserved, "exhausted": self.exhausted,
        }


class SearchBudgetRegistryV1:
    """Budget registry with explicit reserve-before-performance semantics."""

    def __init__(self, objective_id: str, path: str | Path | None = None):
        self.objective_id = objective_id
        self.path = Path(path) if path else None
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._reservations: dict[str, list[tuple[str, str, int]]] = {}
        self._settled_reservations: dict[str, str] = {}
        self._reservation_counter = 0
        self._load()

    def register(self, kind: str, key: str, limit: int) -> None:
        if limit < 0:
            raise ValueError("budget limit cannot be negative")
        bucket_key = (str(kind), str(key))
        current = self._buckets.get(bucket_key)
        if current is not None and current.limit != limit:
            raise ValueError("budget expansion or mutation is forbidden")
        self._buckets.setdefault(bucket_key, _Bucket(str(kind), str(key), int(limit)))
        self._persist()

    def register_objective(self, limit: int) -> None:
        self.register("objective", self.objective_id, limit)

    def register_exploration_increment(self, grant_id: str, contract_ids: list[str]) -> None:
        """前瞻用途增量，不注册或改写旧objective桶；调用方须持有绑定确认回执。"""
        if len(contract_ids) != 4 or len(set(contract_ids)) != 4:
            raise ValueError("EXPLORATION_REQUIRES_FOUR_FIXED_CONTRACTS")
        self.register("exploration_increment", grant_id, 6)
        self.register("exploration_planned", grant_id, 4)
        self.register("exploration_repair", grant_id, 2)
        for contract in contract_ids:
            self.register("exploration_contract", grant_id + ":" + contract, 1)

    def reserve_exploration(self, grant_id: str, contract_id: str, *, repair_id: str | None = None) -> str:
        """只扣新用途桶；修复证据由治理入口验证，不能挪用其他Objective余额。"""
        self._bucket("exploration_contract", grant_id + ":" + contract_id)
        reservation = "EXP-" + stable_hash([grant_id, contract_id, repair_id])[:24]
        if reservation in self._reservations or reservation in self._settled_reservations:
            return reservation
        refs = [("exploration_increment", grant_id, 1),
                ("exploration_repair" if repair_id else "exploration_planned", grant_id, 1)]
        if not repair_id:
            refs.append(("exploration_contract", grant_id + ":" + contract_id, 1))
        if any(self._bucket(kind, key).remaining < amount for kind, key, amount in refs):
            raise BudgetExhaustedError("EXPLORATION_INCREMENT_EXHAUSTED")
        for kind, key, amount in refs:
            self._bucket(kind, key).reserved += amount
        self._reservations[reservation] = refs
        self._persist()
        return reservation

    def register_batch(self, batch_id: str, limit: int) -> None:
        self.register("batch", batch_id, limit)

    def register_train_execution_increment(self, grant_id: str, contract_id: str) -> None:
        """当前用途固定1主试验+1确证修复；不修改旧Objective或探索桶。"""
        self.register("train_execution_increment", grant_id, 2)
        self.register("train_execution_main", grant_id, 1)
        self.register("train_execution_repair", grant_id, 1)
        self.register("train_execution_contract", grant_id+":"+contract_id, 1)

    def reserve_train_execution(self, grant_id: str, contract_id: str, *, repair_id: str | None = None) -> str:
        self._bucket("train_execution_contract", grant_id+":"+contract_id)
        reservation="TRAIN-"+stable_hash([grant_id,contract_id,repair_id])[:24]
        if reservation in self._reservations or reservation in self._settled_reservations:
            return reservation
        refs=[("train_execution_increment",grant_id,1),
              ("train_execution_repair" if repair_id else "train_execution_main",grant_id,1)]
        if not repair_id:refs.append(("train_execution_contract",grant_id+":"+contract_id,1))
        if any(self._bucket(kind,key).remaining<amount for kind,key,amount in refs):
            raise BudgetExhaustedError("TRAIN_EXECUTION_INCREMENT_EXHAUSTED")
        for kind,key,amount in refs:self._bucket(kind,key).reserved+=amount
        self._reservations[reservation]=refs
        self._persist()
        return reservation

    def register_family(self, family_id: str, limit: int) -> None:
        self.register("family", family_id, limit)

    def register_candidate(self, candidate_id: str, limit: int = 1) -> None:
        self.register("candidate", candidate_id, limit)

    def _bucket(self, kind: str, key: str) -> _Bucket:
        try:
            return self._buckets[(kind, key)]
        except KeyError as exc:
            raise KeyError(f"unregistered budget bucket: {kind}:{key}") from exc

    def reserve(self, kind: str, key: str, amount: int = 1) -> str:
        if amount <= 0:
            raise ValueError("reservation amount must be positive")
        bucket = self._bucket(kind, key)
        if bucket.remaining < amount:
            raise BudgetExhaustedError(f"{kind}:{key} budget exhausted")
        bucket.reserved += amount
        self._reservation_counter += 1
        reservation_id = f"RES-{self._reservation_counter:06d}"
        self._reservations[reservation_id] = [(kind, key, amount)]
        self._persist()
        return reservation_id

    def reserve_trial(self, *, batch_id: str, family_id: str, candidate_id: str, trial_number: int | None = None) -> str:
        reservation_identity = {"objective_id": self.objective_id, "batch_id": batch_id, "family_id": family_id, "candidate_id": candidate_id}
        if trial_number is not None:
            trial_number = int(trial_number)
            if trial_number < 1:
                raise ValueError("trial_number must be positive")
            reservation_identity["trial_number"] = trial_number
        reservation_id = f"TRIAL-{stable_hash(reservation_identity)[:20]}"
        if reservation_id in self._reservations or self._settled_reservations.get(reservation_id) == "CONSUMED":
            return reservation_id
        if self._settled_reservations.get(reservation_id) == "RELEASED":
            del self._settled_reservations[reservation_id]
        refs = [("objective", self.objective_id, 1), ("batch", batch_id, 1), ("family", family_id, 1)]
        if ("candidate", candidate_id) in self._buckets and self._bucket("candidate", candidate_id).remaining >= 1:
            refs.append(("candidate", candidate_id, 1))
        for kind, key, amount in refs:
            if self._bucket(kind, key).remaining < amount:
                raise BudgetExhaustedError(f"{kind}:{key} budget exhausted")
        for kind, key, amount in refs:
            self._bucket(kind, key).reserved += amount
        self._reservations[reservation_id] = refs
        self._persist()
        return reservation_id

    def consume(self, reservation_id: str) -> None:
        refs = self._reservations.pop(reservation_id, None)
        if refs is None:
            if self._settled_reservations.get(reservation_id) == "CONSUMED":
                return
            raise KeyError(f"unknown reservation: {reservation_id}")
        for kind, key, amount in refs:
            bucket = self._bucket(kind, key)
            bucket.reserved -= amount
            bucket.used += amount
        self._settled_reservations[reservation_id] = "CONSUMED"
        self._persist()

    def release(self, reservation_id: str) -> None:
        refs = self._reservations.pop(reservation_id, None)
        if refs is None:
            if self._settled_reservations.get(reservation_id) == "RELEASED":
                return
            raise KeyError(f"unknown reservation: {reservation_id}")
        for kind, key, amount in refs:
            self._bucket(kind, key).reserved -= amount
        self._settled_reservations[reservation_id] = "RELEASED"
        self._persist()

    def snapshot(self) -> dict[str, Any]:
        buckets = [bucket.to_dict() for bucket in sorted(self._buckets.values(), key=lambda item: (item.kind, item.key))]
        return {
            "schema_version": "search-budget-registry-v1",
            "objective_id": self.objective_id,
            "buckets": buckets,
            "active_reservations": {key: list(value) for key, value in sorted(self._reservations.items())},
            "settled_reservations": dict(sorted(self._settled_reservations.items())),
            "reservation_counter": self._reservation_counter,
            "updated_at": now_timestamp(),
        }

    @property
    def head_hash(self) -> str:
        return stable_hash({
            "buckets": self.snapshot()["buckets"],
            "reservations": self._reservations,
            "settled_reservations": self._settled_reservations,
        })

    def used(self, kind: str, key: str) -> int:
        return self._bucket(kind, key).used

    def remaining(self, kind: str, key: str) -> int:
        return self._bucket(kind, key).remaining

    def reserved(self, kind: str, key: str) -> int:
        return self._bucket(kind, key).reserved

    def exhausted(self, kind: str, key: str) -> bool:
        return self._bucket(kind, key).exhausted

    def reconcile(self, *, expected_used: dict[tuple[str, str], int] | None = None, expected_reserved: dict[tuple[str, str], int] | None = None) -> None:
        """Fail closed when persisted counts disagree with durable evidence."""
        for (kind, key), expected in (expected_used or {}).items():
            if self.used(kind, key) != int(expected):
                raise BudgetLedgerMismatchError(f"{kind}:{key} used={self.used(kind, key)} expected={expected}")
        for (kind, key), expected in (expected_reserved or {}).items():
            if self.reserved(kind, key) != int(expected):
                raise BudgetLedgerMismatchError(f"{kind}:{key} reserved={self.reserved(kind, key)} expected={expected}")

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BudgetLedgerMismatchError(f"cannot load persisted budget: {self.path}") from exc
        if str(payload.get("objective_id")) != self.objective_id:
            raise BudgetLedgerMismatchError("persisted budget objective_id mismatch")
        for item in payload.get("buckets", ()):
            kind = str(item["kind"])
            key = str(item["key"])
            limit = int(item["limit"])
            used = int(item.get("used", 0))
            reserved = int(item.get("reserved", 0))
            if min(limit, used, reserved) < 0 or used + reserved > limit:
                raise BudgetLedgerMismatchError(f"invalid persisted bucket: {kind}:{key}")
            self._buckets[(kind, key)] = _Bucket(kind, key, limit, used, reserved)
        for reservation_id, refs in payload.get("active_reservations", {}).items():
            normalized = [(str(item[0]), str(item[1]), int(item[2])) for item in refs]
            for kind, key, amount in normalized:
                if (kind, key) not in self._buckets or amount <= 0:
                    raise BudgetLedgerMismatchError(f"invalid persisted reservation: {reservation_id}")
            self._reservations[str(reservation_id)] = normalized
        self._settled_reservations = {
            str(key): str(value) for key, value in payload.get("settled_reservations", {}).items()
        }
        counter = int(payload.get("reservation_counter", 0) or 0)
        for reservation_id in (*self._reservations, *self._settled_reservations):
            try:
                counter = max(counter, int(str(reservation_id).rsplit("-", 1)[-1]))
            except ValueError:
                continue
        self._reservation_counter = counter

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
