"""Vector store abstraction. Phase 1: ChromaDB (persistent). Future: PostgreSQL + pgvector (same interface)."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

import chromadb
from chromadb.config import Settings as ChromaSettings


@dataclass
class Doc:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float  # cosine similarity, 1.0 = identical


class VectorStore(Protocol):
    def upsert(self, docs: list[Doc], embeddings: list[list[float]]) -> None: ...

    def query(self, embedding: list[float], k: int, where: dict[str, Any] | None = None) -> list[Hit]: ...

    def delete(self, where: dict[str, Any]) -> None: ...

    def count(self, where: dict[str, Any] | None = None) -> int: ...

    def get(self, where: dict[str, Any], limit: int = 100) -> list[Doc]: ...


_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _clean(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma metadata values must be str/int/float/bool."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


class ChromaStore:
    def __init__(self, path: str | None, collection: str):
        settings = ChromaSettings(anonymized_telemetry=False, allow_reset=False)
        self._client = (
            chromadb.PersistentClient(path=path, settings=settings)
            if path
            else chromadb.EphemeralClient(settings=settings)
        )
        self._lock = threading.Lock()
        self._col = self._client.get_or_create_collection(
            _NAME_SAFE.sub("-", collection).strip("-._")[:200] or "finops",
            configuration={"hnsw": {"space": "cosine"}},
            embedding_function=None,
        )

    def upsert(self, docs: list[Doc], embeddings: list[list[float]]) -> None:
        if not docs:
            return
        with self._lock:
            for i in range(0, len(docs), 256):
                chunk = docs[i : i + 256]
                self._col.upsert(
                    ids=[d.id for d in chunk],
                    documents=[d.text for d in chunk],
                    embeddings=embeddings[i : i + 256],
                    metadatas=[_clean(d.metadata) for d in chunk],
                )

    def query(self, embedding: list[float], k: int, where: dict[str, Any] | None = None) -> list[Hit]:
        total = self.count()
        if total == 0:
            return []
        res = self._col.query(
            query_embeddings=[embedding],
            n_results=min(k, total),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for i, doc_id in enumerate(res["ids"][0]):
            hits.append(
                Hit(doc_id, res["documents"][0][i], res["metadatas"][0][i] or {}, 1.0 - float(res["distances"][0][i]))
            )
        return hits

    def delete(self, where: dict[str, Any]) -> None:
        with self._lock:
            self._col.delete(where=where)

    def count(self, where: dict[str, Any] | None = None) -> int:
        if where is None:
            return self._col.count()
        return len(self._col.get(where=where, include=[])["ids"])

    def get(self, where: dict[str, Any], limit: int = 100) -> list[Doc]:
        r = self._col.get(where=where, limit=limit, include=["documents", "metadatas"])
        return [Doc(i, d, m or {}) for i, d, m in zip(r["ids"], r["documents"], r["metadatas"], strict=True)]
