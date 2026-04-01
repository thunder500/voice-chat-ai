import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded local embedding model: all-MiniLM-L6-v2")
    return _local_model


async def embed_texts(texts: list[str], openai_key: Optional[str] = None) -> list[list[float]]:
    if openai_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"OpenAI embeddings failed, using local: {e}")
    import asyncio
    model = _get_local_model()
    embeddings = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


async def embed_query(query: str, openai_key: Optional[str] = None) -> list[float]:
    results = await embed_texts([query], openai_key)
    return results[0]


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for i in range(min(end, len(text) - 1), max(start + chunk_size // 2, start), -1):
                if text[i] in '.!?\n':
                    end = i + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks
