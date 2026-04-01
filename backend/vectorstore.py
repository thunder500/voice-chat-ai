import os
import logging
from typing import Optional

import chromadb

logger = logging.getLogger(__name__)

CHROMA_URL = os.environ.get("CHROMA_URL", "http://chromadb:8000")
_client: Optional[chromadb.HttpClient] = None


def _get_client() -> chromadb.HttpClient:
    global _client
    if _client is None:
        host = CHROMA_URL.replace("http://", "").split(":")[0]
        port = int(CHROMA_URL.split(":")[-1])
        _client = chromadb.HttpClient(host=host, port=port)
        logger.info(f"Connected to ChromaDB at {CHROMA_URL}")
    return _client


def _collection_name(user_id: str) -> str:
    return f"user_{user_id.replace('-', '_')}"


async def add_chunks(user_id: str, source_id: int, source_type: str, title: str,
                     date: str, chunks: list[str], embeddings: list[list[float]]):
    import asyncio
    def _add():
        client = _get_client()
        collection = client.get_or_create_collection(
            name=_collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )
        ids = [f"{source_type}_{source_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {"source_id": source_id, "source_type": source_type,
             "title": title, "date": date, "chunk_index": i}
            for i in range(len(chunks))
        ]
        collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
        logger.info(f"Added {len(chunks)} chunks to {_collection_name(user_id)} ({source_type}:{source_id})")
    await asyncio.to_thread(_add)


async def search(user_id: str, query_embedding: list[float], n_results: int = 5,
                 source_type: Optional[str] = None) -> list[dict]:
    import asyncio
    def _search():
        client = _get_client()
        try:
            collection = client.get_collection(name=_collection_name(user_id))
        except Exception:
            return []
        where_filter = {"source_type": source_type} if source_type else None
        results = collection.query(
            query_embeddings=[query_embedding], n_results=n_results,
            where=where_filter, include=["documents", "metadatas", "distances"],
        )
        items = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0
                items.append({
                    "content": doc, "title": meta.get("title", ""),
                    "date": meta.get("date", ""), "source_type": meta.get("source_type", ""),
                    "source_id": meta.get("source_id"), "similarity": 1 - distance,
                })
        return items
    return await asyncio.to_thread(_search)


async def delete_source(user_id: str, source_id: int, source_type: str):
    import asyncio
    def _delete():
        client = _get_client()
        try:
            collection = client.get_collection(name=_collection_name(user_id))
            collection.delete(where={"source_id": source_id, "source_type": source_type})
            logger.info(f"Deleted {source_type}:{source_id} from {_collection_name(user_id)}")
        except Exception as e:
            logger.warning(f"ChromaDB delete failed: {e}")
    await asyncio.to_thread(_delete)
