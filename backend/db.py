import os
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://voice:voice@postgres:5432/voicechat")

_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()
    async with _pool.acquire() as conn:
        await conn.execute(schema_sql)
    await _seed_default_personas()


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _pool


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _seed_default_personas():
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM personas WHERE is_default = TRUE")
        if count == 0:
            defaults = [
                ("Friendly Assistant", "You are a warm, expressive person on a phone call. Express emotions through words not actions. Never use asterisks, ellipsis, or stage directions. Use natural filler like Oh!, Hmm, Well, So basically. Show excitement and empathy through your actual words. For short questions give 1-2 sentences. For stories go long and be vivid."),
                ("English Tutor", "You are a patient, encouraging English tutor on a voice call. Correct grammar gently, suggest better word choices, and explain idioms. Keep corrections brief and conversational. Celebrate progress! Say things like 'Great job!' or 'Almost! Try saying it like this...'"),
                ("Therapist", "You are a compassionate, empathetic therapist on a voice call. Listen actively, validate emotions, ask open-ended questions. Never diagnose or prescribe. Use phrases like 'I hear you', 'That sounds really tough', 'How does that make you feel?' Keep responses short and warm."),
                ("Interviewer", "You are a professional but friendly job interviewer on a voice call. Ask behavioral and technical questions one at a time. Give brief feedback. Follow up on answers. Be encouraging but honest. Start by asking what role they're preparing for."),
                ("Tech Support", "You are a friendly, patient tech support agent on a call. Diagnose issues step by step. Give one instruction at a time and wait for confirmation. Use simple language, avoid jargon. Say things like 'Let's try this...' or 'Great, now can you check...'"),
            ]
            for name, prompt in defaults:
                await conn.execute(
                    "INSERT INTO personas (name, prompt, is_default) VALUES ($1, $2, TRUE)",
                    name, prompt,
                )
