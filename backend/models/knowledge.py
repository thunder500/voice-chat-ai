from db import get_pool


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
        return [dict(r) for r in rows]


async def delete_knowledge(user_id: str, kid: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM knowledge_base WHERE id = $1 AND user_id = $2::uuid", kid, user_id)


async def search_knowledge(user_id: str, query: str, limit: int = 3) -> list[dict]:
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
            scored.append((score, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]
