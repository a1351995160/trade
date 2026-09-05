"""Append-only ResearchArtifactGraphV1."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
import json
import os

from .common import now_timestamp, stable_hash
from .durability import canonical_frozen_contract_identity_hash


ARTIFACT_NODES = {"Objective", "Batch", "Hypothesis", "Factor", "Candidate", "FrozenCandidateContract", "StructuralPreflight", "Trial", "ValidationResult", "FinalResearchDecision", "FailureKnowledge", "Strategy"}
ARTIFACT_EDGES = {"GENERATED_FROM", "PLANNED_BY", "USES_FACTOR", "COMPILED_TO", "HAS_DURABLE_CONTRACT", "VALIDATED_BY", "RECONCILED_AS", "FAILED_BECAUSE", "CLASSIFIED_AS", "FINAL_ADJUDICATED_AS", "FINAL_ADJUDICATION_COMMITTED", "SUPERSEDES", "PROMOTED_TO"}


@dataclass(frozen=True)
class ArtifactNodeV1:
    node_id: str
    node_type: str
    payload_hash: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ArtifactEdgeV1:
    source_id: str
    edge_type: str
    target_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ResearchArtifactGraphV1:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.nodes: dict[str, ArtifactNodeV1] = {}
        self.edges: list[ArtifactEdgeV1] = []
        self._load()

    def add_node(self, node_id: str, node_type: str, payload: Any) -> ArtifactNodeV1:
        if node_type not in ARTIFACT_NODES:
            raise ValueError(f"unsupported artifact node type: {node_type}")
        node = ArtifactNodeV1(node_id, node_type, stable_hash(payload), now_timestamp())
        existing = self.nodes.get(node_id)
        if existing is not None and existing != node:
            if existing.payload_hash != node.payload_hash or existing.node_type != node.node_type:
                raise ValueError(f"artifact node identity conflict: {node_id}")
            return existing
        self.nodes[node_id] = node
        self._persist()
        return node

    def add_edge(self, source_id: str, edge_type: str, target_id: str) -> ArtifactEdgeV1:
        if edge_type not in ARTIFACT_EDGES:
            raise ValueError(f"unsupported artifact edge type: {edge_type}")
        if source_id not in self.nodes or target_id not in self.nodes:
            raise KeyError("artifact edge endpoints must already exist")
        edge = ArtifactEdgeV1(source_id, edge_type, target_id, now_timestamp())
        for existing in self.edges:
            if existing.source_id == source_id and existing.edge_type == edge_type and existing.target_id == target_id:
                return existing
        self.edges.append(edge)
        self._persist()
        return edge

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research-artifact-graph-v1",
            "nodes": [item.to_dict() for item in sorted(self.nodes.values(), key=lambda item: item.node_id)],
            "edges": [item.to_dict() for item in self.edges],
        }

    def integrity(self) -> dict[str, Any]:
        dangling = [edge.to_dict() for edge in self.edges if edge.source_id not in self.nodes or edge.target_id not in self.nodes]
        return {"status": "PASS" if not dangling else "FAIL", "dangling_edges": dangling, "node_count": len(self.nodes), "edge_count": len(self.edges)}

    @property
    def graph_hash(self) -> str:
        return stable_hash(self.to_dict())

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.nodes = {str(item["node_id"]): ArtifactNodeV1(**item) for item in payload.get("nodes", ())}
        self.edges = [ArtifactEdgeV1(**item) for item in payload.get("edges", ())]

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def add_frozen_contract_node_idempotent(
    graph: ResearchArtifactGraphV1,
    node_id: str,
    payload: Mapping[str, Any],
    canonical_payloads: Iterable[Mapping[str, Any]] = (),
) -> ArtifactNodeV1:
    """Reuse a legacy canonical node when only freeze metadata differs.

    The graph stores the historical payload hash, so callers provide the
    already-persisted canonical payloads that are allowed to match it.  An
    unknown hash or a semantic mismatch still fails closed through
    ``ResearchArtifactGraphV1.add_node``.
    """

    try:
        return graph.add_node(node_id, "FrozenCandidateContract", payload)
    except ValueError:
        existing = graph.nodes.get(node_id)
        if existing is None or existing.node_type != "FrozenCandidateContract":
            raise
        proposed_identity_hash = canonical_frozen_contract_identity_hash(payload)
        for canonical_payload in canonical_payloads:
            if stable_hash(canonical_payload) != existing.payload_hash:
                continue
            if canonical_frozen_contract_identity_hash(canonical_payload) == proposed_identity_hash:
                return existing
        raise
