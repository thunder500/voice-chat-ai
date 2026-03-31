from db import get_pool


async def create_user(email: str, name: str, password_hash: str | None = None,
                      auth_provider: str = "local", google_id: str | None = None,
                      avatar_url: str | None = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, auth_provider, google_id, avatar_url)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id, email, name, avatar_url, auth_provider, created_at""",
            email, password_hash, name, auth_provider, google_id, avatar_url,
        )
        return dict(row) if row else None


async def get_user_by_email(email: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        return dict(row) if row else None


async def get_user_by_id(user_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1::uuid", user_id)
        return dict(row) if row else None


async def get_user_by_google_id(google_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE google_id = $1", google_id)
        return dict(row) if row else None


async def update_last_login(user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_login = NOW() WHERE id = $1::uuid", user_id)


async def link_google_account(user_id: str, google_id: str, avatar_url: str | None = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET google_id = $1, avatar_url = COALESCE($2, avatar_url),
               auth_provider = 'google' WHERE id = $3::uuid""",
            google_id, avatar_url, user_id,
        )
