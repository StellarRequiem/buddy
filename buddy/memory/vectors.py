"""
Chroma vector store wrapper.
Embeds text with nomic-embed-text via Ollama, stores in BuddyVault/chroma/.
Used for semantic memory retrieval — find relevant past conversations.
"""
from __future__ import annotations

import hashlib
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from buddy.config import settings as cfg


def _embed(texts: list[str]) -> list[list[float]]:
    """Get embeddings from Ollama nomic-embed-text."""
    import httpx
    resp = httpx.post(
        f"{cfg.ollama_host}/api/embed",
        json={"model": cfg.embed_model, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _client() -> chromadb.Client:
    return chromadb.PersistentClient(
        path=str(cfg.chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _collection(name: str = "buddy_memory"):
    return _client().get_or_create_collection(name)


def upsert_memory(text: str, metadata: dict | None = None) -> str:
    """Store a text chunk. Returns the generated doc_id."""
    doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]
    embedding = _embed([text])[0]
    _collection().upsert(
        ids=[doc_id],
        documents=[text],
        embeddings=[embedding],
        metadatas=[metadata or {"source": "buddy"}],
    )
    return doc_id


def search_memory(query: str, n_results: int = 5) -> list[dict[str, Any]]:
    """Return top-n semantically similar memory chunks."""
    embedding = _embed([query])[0]
    results = _collection().query(
        query_embeddings=[embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    out = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        out.append({"text": doc, "metadata": meta, "distance": dist})
    return out


def memory_count() -> int:
    return _collection().count()
