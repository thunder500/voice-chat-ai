from db import get_pool, _serialize_row
import json


async def create_meeting(user_id: str, title: str = "Untitled Meeting",
                         transcript: str = "", duration_seconds: int = 0) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO meetings (user_id, title, transcript, duration_seconds)
               VALUES ($1::uuid, $2, $3, $4) RETURNING id""",
            user_id, title, transcript, duration_seconds,
        )
        return row["id"]


async def update_meeting_summary(meeting_id: int, title: str, summary: str,
                                  action_items: list, model_used: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE meetings SET title = $1, summary = $2, action_items = $3::jsonb,
               model_used = $4 WHERE id = $5""",
            title, summary, json.dumps(action_items), model_used, meeting_id,
        )


async def get_meeting(meeting_id: int, user_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM meetings WHERE id = $1 AND user_id = $2::uuid",
            meeting_id, user_id,
        )
        if not row:
            return None
        d = _serialize_row(row)
        if isinstance(d.get("action_items"), str):
            d["action_items"] = json.loads(d["action_items"])
        return d


async def get_meetings(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, summary, action_items, duration_seconds, model_used,
                      in_knowledge_base, created_at
               FROM meetings WHERE user_id = $1::uuid ORDER BY created_at DESC""",
            user_id,
        )
        results = []
        for r in rows:
            d = _serialize_row(r)
            if isinstance(d.get("action_items"), str):
                d["action_items"] = json.loads(d["action_items"])
            results.append(d)
        return results


async def delete_meeting(meeting_id: int, user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM meetings WHERE id = $1 AND user_id = $2::uuid",
            meeting_id, user_id,
        )


async def update_meeting_kb_toggle(meeting_id: int, user_id: str, in_kb: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET in_knowledge_base = $1 WHERE id = $2 AND user_id = $3::uuid",
            in_kb, meeting_id, user_id,
        )
