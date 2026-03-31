from db import get_pool
from crypto import encrypt_key, decrypt_key


async def save_api_key(user_id: str, provider: str, api_key: str,
                       model_preference: str | None = None) -> int:
    encrypted, iv = encrypt_key(api_key)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO user_api_keys (user_id, provider, encrypted_key, iv, model_preference)
               VALUES ($1::uuid, $2, $3, $4, $5)
               ON CONFLICT (user_id, provider)
               DO UPDATE SET encrypted_key = $3, iv = $4, model_preference = $5
               RETURNING id""",
            user_id, provider, encrypted, iv, model_preference,
        )
        return row["id"]


async def get_api_keys(user_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, provider, model_preference, created_at
               FROM user_api_keys WHERE user_id = $1::uuid ORDER BY provider""",
            user_id,
        )
        results = []
        for row in rows:
            r = dict(row)
            full_row = await conn.fetchrow(
                "SELECT encrypted_key, iv FROM user_api_keys WHERE id = $1", row["id"]
            )
            try:
                plain = decrypt_key(bytes(full_row["encrypted_key"]), bytes(full_row["iv"]))
                r["masked_key"] = "..." + plain[-4:] if len(plain) >= 4 else "...****"
            except Exception:
                r["masked_key"] = "...error"
            results.append(r)
        return results


async def get_decrypted_key(user_id: str, provider: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT encrypted_key, iv FROM user_api_keys WHERE user_id = $1::uuid AND provider = $2",
            user_id, provider,
        )
        if not row:
            return None
        return decrypt_key(bytes(row["encrypted_key"]), bytes(row["iv"]))


async def delete_api_key(user_id: str, provider: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_api_keys WHERE user_id = $1::uuid AND provider = $2",
            user_id, provider,
        )
