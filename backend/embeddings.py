import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def embed_texts(texts: list[str], openai_key: Optional[str] = None) -> list[list[float]]:
    """Embed texts using OpenAI if key available. Returns None if no embedding available."""
    if openai_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"OpenAI embeddings failed: {e}")
    return None


async def embed_query(query: str, openai_key: Optional[str] = None) -> Optional[list[float]]:
    """Embed a single query. Returns None if no embedding available."""
    results = await embed_texts([query], openai_key)
    return results[0] if results else None


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks."""
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
