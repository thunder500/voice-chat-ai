from db import get_pool, _serialize_row


async def add_knowledge(user_id: str, title: str, content: str, file_type: str = "text") -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO knowledge_base (user_id, title, content, file_type) VALUES ($1::uuid, $2, $3, $4) RETURNING id",
            user_id, title, content, file_type,
        )
        return row["id"]


async def get_all_knowledge(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content, file_type, created_at FROM knowledge_base WHERE user_id = $1::uuid ORDER BY created_at DESC",
            user_id,
        )
        return [_serialize_row(r) for r in rows]


async def delete_knowledge(user_id: str, kid: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM knowledge_base WHERE id = $1 AND user_id = $2::uuid", kid, user_id)


async def search_knowledge(user_id: str, query: str, limit: int = 5, openai_key: str = None) -> list[dict]:
    """Semantic search via ChromaDB, falls back to keyword search."""
    try:
        from embeddings import embed_query
        from vectorstore import search
        query_emb = await embed_query(query, openai_key)
        results = await search(user_id, query_emb, n_results=limit)
        if results:
            return results
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Vector search failed, using keyword fallback: {e}")

    # Keyword fallback
    words = query.lower().split()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content, file_type FROM knowledge_base WHERE user_id = $1::uuid",
            user_id,
        )
    scored = []
    for row in rows:
        content_lower = row["content"].lower()
        score = sum(1 for w in words if w in content_lower)
        if score > 0:
            scored.append((score, _serialize_row(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


async def vectorize_knowledge(user_id: str, kid: int, title: str, content: str, openai_key: str = None):
    try:
        from embeddings import chunk_text, embed_texts
        from vectorstore import add_chunks
        from datetime import datetime
        chunks = chunk_text(content)
        embeddings = await embed_texts(chunks, openai_key)
        await add_chunks(
            user_id=user_id, source_id=kid, source_type="knowledge",
            title=title, date=datetime.now().isoformat()[:10], chunks=chunks, embeddings=embeddings,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to vectorize knowledge {kid}: {e}")


async def devectorize_knowledge(user_id: str, kid: int):
    try:
        from vectorstore import delete_source
        await delete_source(user_id, kid, "knowledge")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to devectorize knowledge {kid}: {e}")
