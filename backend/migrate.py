"""One-time migration from SQLite to PostgreSQL."""
import os
import logging

import aiosqlite

from auth import hash_password
from db import get_pool

logger = logging.getLogger(__name__)

SQLITE_PATH = os.environ.get("DB_PATH", "/app/data/voice_chat.db")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@localhost")


async def should_migrate() -> bool:
    if not os.path.exists(SQLITE_PATH):
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        return count == 0


async def run_migration():
    if not await should_migrate():
        logger.info("No migration needed")
        return

    logger.info(f"Migrating SQLite data to PostgreSQL (admin: {ADMIN_EMAIL})...")
    pool = await get_pool()

    async with pool.acquire() as conn:
        admin_row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, auth_provider)
               VALUES ($1, $2, $3, 'local') RETURNING id""",
            ADMIN_EMAIL, hash_password("change-me-on-first-login"), "Admin",
        )
        admin_id = str(admin_row["id"])

    async with aiosqlite.connect(SQLITE_PATH) as sqlite:
        sqlite.row_factory = aiosqlite.Row

        cursor = await sqlite.execute("SELECT * FROM conversations ORDER BY id")
        convs = await cursor.fetchall()
        conv_id_map = {}

        async with pool.acquire() as conn:
            for c in convs:
                row = await conn.fetchrow(
                    "INSERT INTO conversations (user_id, title, starred, created_at) VALUES ($1::uuid, $2, $3, $4) RETURNING id",
                    admin_id, c["title"], bool(c.get("starred", 0)), c["created_at"],
                )
                conv_id_map[c["id"]] = row["id"]

            cursor = await sqlite.execute("SELECT * FROM messages ORDER BY id")
            msgs = await cursor.fetchall()
            for m in msgs:
                new_conv_id = conv_id_map.get(m["conversation_id"])
                if new_conv_id:
                    await conn.execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                        new_conv_id, m["role"], m["content"], m["created_at"],
                    )

        cursor = await sqlite.execute("SELECT * FROM knowledge_base ORDER BY id")
        knowledge = await cursor.fetchall()
        async with pool.acquire() as conn:
            for k in knowledge:
                await conn.execute(
                    "INSERT INTO knowledge_base (user_id, title, content, file_type, created_at) VALUES ($1::uuid, $2, $3, $4, $5)",
                    admin_id, k["title"], k["content"], k["file_type"], k["created_at"],
                )

        cursor = await sqlite.execute("SELECT * FROM personas WHERE is_default = 0 ORDER BY id")
        personas = await cursor.fetchall()
        async with pool.acquire() as conn:
            for p in personas:
                await conn.execute(
                    "INSERT INTO personas (user_id, name, prompt, created_at) VALUES ($1::uuid, $2, $3, $4)",
                    admin_id, p["name"], p["prompt"], p["created_at"],
                )

    logger.info(f"Migration complete: {len(convs)} conversations, {len(knowledge)} knowledge entries, {len(personas)} custom personas")
