from db import get_pool


async def create_conversation(user_id: str, title: str = "New Conversation") -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO conversations (user_id, title) VALUES ($1::uuid, $2) RETURNING id",
            user_id, title,
        )
        return row["id"]


async def add_message(conversation_id: int, role: str, content: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3) RETURNING id",
            conversation_id, role, content,
        )
        return row["id"]


async def update_conversation_title(conversation_id: int, title: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE conversations SET title = $1 WHERE id = $2", title, conversation_id)


async def get_conversations(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, starred, created_at FROM conversations
               WHERE user_id = $1::uuid ORDER BY starred DESC, created_at DESC""",
            user_id,
        )
        return [dict(r) for r in rows]


async def get_conversation_messages(conversation_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = $1 ORDER BY created_at ASC",
            conversation_id,
        )
        return [dict(r) for r in rows]


async def clear_conversations(user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE user_id = $1::uuid", user_id)


async def search_conversations(user_id: str, query: str) -> list[dict]:
    pattern = f"%{query}%"
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT c.id, c.title, c.starred, c.created_at
               FROM conversations c
               LEFT JOIN messages m ON m.conversation_id = c.id
               WHERE c.user_id = $1::uuid AND (c.title ILIKE $2 OR m.content ILIKE $2)
               ORDER BY c.starred DESC, c.created_at DESC""",
            user_id, pattern,
        )
        return [dict(r) for r in rows]


async def toggle_star_conversation(conversation_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE conversations SET starred = NOT starred WHERE id = $1 RETURNING starred",
            conversation_id,
        )
        return bool(row["starred"]) if row else False
