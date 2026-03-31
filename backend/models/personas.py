from db import get_pool


async def get_personas(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, prompt, is_default, created_at FROM personas
               WHERE user_id IS NULL OR user_id = $1::uuid
               ORDER BY is_default DESC, created_at ASC""",
            user_id,
        )
        return [dict(r) for r in rows]


async def get_persona(persona_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM personas WHERE id = $1", persona_id)
        return dict(row) if row else None


async def add_persona(user_id: str, name: str, prompt: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO personas (user_id, name, prompt) VALUES ($1::uuid, $2, $3) RETURNING id",
            user_id, name, prompt,
        )
        return row["id"]


async def delete_persona(user_id: str, pid: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM personas WHERE id = $1 AND user_id = $2::uuid AND is_default = FALSE",
            pid, user_id,
        )
