# Auth + BYOK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-user authentication (email/password + Google OAuth) and BYOK encrypted API key management, migrating from SQLite to PostgreSQL.

**Architecture:** Split the monolithic `database.py` into focused modules: `db.py` (connection pool), `models/` (user, conversation, knowledge, persona, apikey). Add `auth.py` for JWT + password hashing, `crypto.py` for AES-256 encryption, `oauth.py` for Google flow. Frontend gets a login page overlay and API keys settings tab.

**Tech Stack:** FastAPI, asyncpg, PostgreSQL 16, python-jose (JWT), passlib[bcrypt], cryptography (AES-256-GCM), httpx (OAuth)

---

## File Structure

### New Files
- `backend/db.py` — asyncpg connection pool (init, get_pool, close)
- `backend/auth.py` — JWT creation/validation, password hashing, auth middleware
- `backend/crypto.py` — AES-256-GCM encrypt/decrypt for API keys
- `backend/oauth.py` — Google OAuth flow (redirect URL, callback, user info)
- `backend/models/users.py` — create_user, get_user_by_email, get_user_by_google_id, update_last_login
- `backend/models/apikeys.py` — save_key, get_keys, delete_key, get_decrypted_key
- `backend/models/conversations.py` — all conversation/message functions (migrated from database.py, add user_id)
- `backend/models/knowledge.py` — all knowledge base functions (migrated, add user_id)
- `backend/models/personas.py` — all persona functions (migrated, add user_id for custom)
- `backend/models/__init__.py` — re-export all model functions
- `backend/migrate.py` — SQLite → PostgreSQL one-time migration script
- `backend/schema.sql` — full PostgreSQL schema (all CREATE TABLE statements)
- `tests/test_auth.py` — auth endpoint tests
- `tests/test_crypto.py` — encryption round-trip tests
- `tests/test_apikeys.py` — BYOK endpoint tests
- `tests/conftest.py` — test fixtures (test DB, test client)

### Modified Files
- `backend/requirements.txt` — add asyncpg, python-jose, passlib[bcrypt], cryptography, pytest, httpx[test]
- `backend/Dockerfile` — no changes needed (dependencies auto-install)
- `backend/app.py` — add auth routes, protect existing routes, use user_id, WebSocket JWT auth
- `backend/templates/index.html` — login page overlay, user menu, API keys settings tab
- `docker-compose.yml` — add postgres service, new env vars
- `.env.example` — add new env vars

### Deleted Files
- `backend/database.py` — replaced by `backend/db.py` + `backend/models/*`

---

### Task 1: Add PostgreSQL to Docker + Dependencies

**Files:**
- Modify: `docker-compose.yml`
- Modify: `backend/requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Update docker-compose.yml to add PostgreSQL**

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    container_name: voice-chat-ollama
    volumes:
      - ollama_data:/root/.ollama
    ports:
      - "11434:11434"
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: voice-chat-postgres
    environment:
      POSTGRES_DB: voicechat
      POSTGRES_USER: voice
      POSTGRES_PASSWORD: voice
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U voice"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: voice-chat-app
    ports:
      - "8000:8000"
    volumes:
      - app_data:/app/data
      - whisper_cache:/root/.cache
    environment:
      - OLLAMA_URL=http://ollama:11434
      - OLLAMA_MODEL=llama3.2:1b
      - WHISPER_MODEL_SIZE=base
      - DB_PATH=/app/data/voice_chat.db
      - DATABASE_URL=postgresql://voice:voice@postgres:5432/voicechat
      - ENCRYPTION_KEY=${ENCRYPTION_KEY:-}
      - JWT_SECRET=${JWT_SECRET:-}
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
      - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}
      - ADMIN_EMAIL=${ADMIN_EMAIL:-admin@localhost}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - OPENAI_MODEL=gpt-4o-mini
    depends_on:
      postgres:
        condition: service_healthy
      ollama:
        condition: service_started
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

volumes:
  ollama_data:
    name: voice-chat-ollama-data
  app_data:
    name: voice-chat-app-data
  whisper_cache:
    name: voice-chat-whisper-cache
  pgdata:
    name: voice-chat-pgdata
```

- [ ] **Step 2: Update requirements.txt**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
websockets==14.1
python-multipart==0.0.20
faster-whisper==1.1.0
edge-tts>=7.0.0
httpx==0.28.1
aiosqlite==0.20.0
jinja2==3.1.5
requests==2.32.3
pdfplumber==0.11.4
openai>=1.0.0
asyncpg==0.30.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
cryptography>=43.0.0
pytest==8.3.4
pytest-asyncio==0.24.0
```

- [ ] **Step 3: Update .env.example**

```env
# Copy this file to .env and fill in your values

# OpenAI (optional — leave empty for local Ollama only)
OPENAI_API_KEY=sk-proj-your-key-here

# PostgreSQL (required)
DATABASE_URL=postgresql://voice:voice@postgres:5432/voicechat

# Security (required — generate random values)
ENCRYPTION_KEY=your-64-char-hex-string-here
JWT_SECRET=your-random-secret-here

# Google OAuth (optional — leave empty to disable Google sign-in)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Migration (optional — email for the admin user created from existing SQLite data)
ADMIN_EMAIL=admin@localhost
```

- [ ] **Step 4: Verify PostgreSQL starts**

```bash
docker compose up -d postgres
docker compose exec postgres pg_isready -U voice
```

Expected: `/var/run/postgresql:5432 - accepting connections`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml backend/requirements.txt .env.example
git commit -m "infra: add PostgreSQL container and new dependencies"
```

---

### Task 2: Database Connection Pool + Schema

**Files:**
- Create: `backend/db.py`
- Create: `backend/schema.sql`

- [ ] **Step 1: Create schema.sql with all tables**

File: `backend/schema.sql`

```sql
-- Users
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    avatar_url TEXT,
    auth_provider VARCHAR(20) DEFAULT 'local',
    google_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP DEFAULT NOW()
);

-- Encrypted API keys
CREATE TABLE IF NOT EXISTS user_api_keys (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    encrypted_key BYTEA NOT NULL,
    iv BYTEA NOT NULL,
    model_preference VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, provider)
);

-- Conversations (with user isolation)
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT DEFAULT 'New Conversation',
    starred BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(10) NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Knowledge base (with user isolation)
CREATE TABLE IF NOT EXISTS knowledge_base (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    file_type VARCHAR(50) DEFAULT 'text',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Personas (user_id NULL = shared default)
CREATE TABLE IF NOT EXISTS personas (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_user ON knowledge_base(user_id);
CREATE INDEX IF NOT EXISTS idx_personas_user ON personas(user_id);
CREATE INDEX IF NOT EXISTS idx_apikeys_user ON user_api_keys(user_id);
```

- [ ] **Step 2: Create db.py with connection pool**

File: `backend/db.py`

```python
import os
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://voice:voice@postgres:5432/voicechat")

_pool: asyncpg.Pool | None = None


async def init_db():
    """Create connection pool and run schema."""
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)

    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()

    async with _pool.acquire() as conn:
        await conn.execute(schema_sql)

    await _seed_default_personas()


async def get_pool() -> asyncpg.Pool:
    """Return the connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _pool


async def close_db():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _seed_default_personas():
    """Insert default personas if none exist."""
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
```

- [ ] **Step 3: Verify schema applies cleanly**

```bash
docker compose up -d postgres app
docker compose exec app python -c "import asyncio; from db import init_db; asyncio.run(init_db()); print('Schema OK')"
```

Expected: `Schema OK`

- [ ] **Step 4: Commit**

```bash
git add backend/db.py backend/schema.sql
git commit -m "feat: add PostgreSQL connection pool and schema"
```

---

### Task 3: Crypto Module (AES-256-GCM)

**Files:**
- Create: `backend/crypto.py`
- Create: `tests/test_crypto.py`

- [ ] **Step 1: Write the failing test**

File: `tests/test_crypto.py`

```python
import os
import pytest

# Set test encryption key before importing crypto
os.environ["ENCRYPTION_KEY"] = "a" * 64

from crypto import encrypt_key, decrypt_key


def test_encrypt_decrypt_roundtrip():
    original = "sk-proj-abc123xyz"
    encrypted, iv = encrypt_key(original)
    assert isinstance(encrypted, bytes)
    assert isinstance(iv, bytes)
    assert len(iv) == 12
    decrypted = decrypt_key(encrypted, iv)
    assert decrypted == original


def test_different_plaintexts_produce_different_ciphertexts():
    enc1, iv1 = encrypt_key("key-one")
    enc2, iv2 = encrypt_key("key-two")
    assert enc1 != enc2


def test_same_plaintext_different_iv():
    enc1, iv1 = encrypt_key("same-key")
    enc2, iv2 = encrypt_key("same-key")
    assert iv1 != iv2  # Random IV each time
    assert enc1 != enc2


def test_decrypt_with_wrong_key_fails():
    original = "sk-proj-secret"
    encrypted, iv = encrypt_key(original)
    # Change the encryption key
    os.environ["ENCRYPTION_KEY"] = "b" * 64
    # Re-import won't work, so test via direct manipulation
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    wrong_key = bytes.fromhex("b" * 64)
    aesgcm = AESGCM(wrong_key)
    with pytest.raises(Exception):
        aesgcm.decrypt(iv, encrypted, None)
    # Restore
    os.environ["ENCRYPTION_KEY"] = "a" * 64
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_crypto.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'crypto'`

- [ ] **Step 3: Write crypto.py implementation**

File: `backend/crypto.py`

```python
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_encryption_key() -> bytes:
    """Load the 32-byte encryption key from env var (64 hex chars)."""
    hex_key = os.environ.get("ENCRYPTION_KEY", "")
    if not hex_key or len(hex_key) != 64:
        raise ValueError(
            "ENCRYPTION_KEY must be a 64-character hex string (32 bytes). "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return bytes.fromhex(hex_key)


def encrypt_key(plaintext: str) -> tuple[bytes, bytes]:
    """Encrypt an API key with AES-256-GCM. Returns (ciphertext, iv)."""
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return ciphertext, iv


def decrypt_key(ciphertext: bytes, iv: bytes) -> str:
    """Decrypt an API key with AES-256-GCM. Returns plaintext string."""
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_crypto.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crypto.py tests/test_crypto.py
git commit -m "feat: add AES-256-GCM encryption for API keys"
```

---

### Task 4: Auth Module (JWT + Password Hashing)

**Files:**
- Create: `backend/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

File: `tests/test_auth.py`

```python
import os
import pytest

os.environ["JWT_SECRET"] = "test-secret-for-jwt-signing"

from auth import hash_password, verify_password, create_access_token, create_refresh_token, decode_token


def test_password_hash_and_verify():
    password = "my-secure-password"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)


def test_wrong_password_fails():
    hashed = hash_password("correct-password")
    assert not verify_password("wrong-password", hashed)


def test_create_and_decode_access_token():
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_access_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "access"


def test_create_and_decode_refresh_token():
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_refresh_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"


def test_expired_token_fails():
    from datetime import timedelta
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_access_token(user_id, expires_delta=timedelta(seconds=-1))
    with pytest.raises(Exception):
        decode_token(token)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Write auth.py implementation**

File: `backend/auth.py`

```python
import os
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError, ExpiredSignatureError
from passlib.context import CryptContext
from fastapi import Request, WebSocket

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=7)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or ACCESS_TOKEN_EXPIRE)
    payload = {"sub": user_id, "type": "access", "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or REFRESH_TOKEN_EXPIRE)
    payload = {"sub": user_id, "type": "refresh", "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises on invalid/expired tokens."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except ExpiredSignatureError:
        raise ValueError("Token has expired")
    except JWTError:
        raise ValueError("Invalid token")


def get_user_id_from_request(request: Request) -> str | None:
    """Extract user_id from Authorization header. Returns None if missing/invalid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        return payload["sub"]
    except (ValueError, KeyError):
        return None


async def get_user_id_from_ws(ws: WebSocket) -> str | None:
    """Extract user_id from first WebSocket message (JWT auth)."""
    # Check query params first (ws://host/ws?token=xxx)
    token = ws.query_params.get("token")
    if token:
        try:
            payload = decode_token(token)
            if payload.get("type") != "access":
                return None
            return payload["sub"]
        except (ValueError, KeyError):
            return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py tests/test_auth.py
git commit -m "feat: add JWT auth and password hashing module"
```

---

### Task 5: User Model

**Files:**
- Create: `backend/models/__init__.py`
- Create: `backend/models/users.py`

- [ ] **Step 1: Create models/__init__.py**

File: `backend/models/__init__.py`

```python
from models.users import (
    create_user, get_user_by_email, get_user_by_id,
    get_user_by_google_id, update_last_login, link_google_account,
)
from models.apikeys import (
    save_api_key, get_api_keys, delete_api_key, get_decrypted_key,
)
from models.conversations import (
    create_conversation, add_message, update_conversation_title,
    get_conversations, get_conversation_messages, clear_conversations,
    search_conversations, toggle_star_conversation,
)
from models.knowledge import (
    add_knowledge, get_all_knowledge, delete_knowledge, search_knowledge,
)
from models.personas import (
    get_personas, get_persona, add_persona, delete_persona,
)
```

- [ ] **Step 2: Create models/users.py**

File: `backend/models/users.py`

```python
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
        await conn.execute(
            "UPDATE users SET last_login = NOW() WHERE id = $1::uuid", user_id
        )


async def link_google_account(user_id: str, google_id: str, avatar_url: str | None = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET google_id = $1, avatar_url = COALESCE($2, avatar_url),
               auth_provider = 'google' WHERE id = $3::uuid""",
            google_id, avatar_url, user_id,
        )
```

- [ ] **Step 3: Commit**

```bash
git add backend/models/__init__.py backend/models/users.py
git commit -m "feat: add user model with CRUD operations"
```

---

### Task 6: API Keys Model

**Files:**
- Create: `backend/models/apikeys.py`
- Create: `tests/test_apikeys.py`

- [ ] **Step 1: Write the failing test**

File: `tests/test_apikeys.py`

```python
import os
import pytest

os.environ["ENCRYPTION_KEY"] = "a" * 64

from crypto import encrypt_key, decrypt_key


def test_encrypt_and_mask_key():
    """Verify we can encrypt a key and extract last 4 chars for masking."""
    original = "sk-proj-abc123xyz"
    encrypted, iv = encrypt_key(original)
    decrypted = decrypt_key(encrypted, iv)
    assert decrypted == original
    # Mask: show only last 4 chars
    masked = "..." + original[-4:]
    assert masked == "...3xyz"


def test_empty_key_encrypts():
    encrypted, iv = encrypt_key("")
    decrypted = decrypt_key(encrypted, iv)
    assert decrypted == ""


def test_long_key_encrypts():
    long_key = "sk-" + "a" * 200
    encrypted, iv = encrypt_key(long_key)
    decrypted = decrypt_key(encrypted, iv)
    assert decrypted == long_key
```

- [ ] **Step 2: Run test to verify it fails (if crypto not yet installed) or passes**

```bash
cd backend && python -m pytest tests/test_apikeys.py -v
```

- [ ] **Step 3: Create models/apikeys.py**

File: `backend/models/apikeys.py`

```python
from db import get_pool
from crypto import encrypt_key, decrypt_key


async def save_api_key(user_id: str, provider: str, api_key: str,
                       model_preference: str | None = None) -> int:
    """Encrypt and store an API key. Upserts if provider already exists."""
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
    """Get all API keys for a user (masked — never returns actual keys)."""
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
            # Decrypt just to get last 4 chars for masking
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
    """Get a decrypted API key for a specific provider. Used internally for API calls."""
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
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/apikeys.py tests/test_apikeys.py
git commit -m "feat: add encrypted API key storage model"
```

---

### Task 7: Conversation + Message Models (migrated from database.py)

**Files:**
- Create: `backend/models/conversations.py`

- [ ] **Step 1: Create models/conversations.py**

All functions now take `user_id` as first parameter and use asyncpg instead of aiosqlite.

File: `backend/models/conversations.py`

```python
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
        await conn.execute(
            "UPDATE conversations SET title = $1 WHERE id = $2", title, conversation_id
        )


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
        # Messages cascade-delete via FK
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
```

- [ ] **Step 2: Commit**

```bash
git add backend/models/conversations.py
git commit -m "feat: migrate conversation model to asyncpg with user isolation"
```

---

### Task 8: Knowledge + Persona Models (migrated from database.py)

**Files:**
- Create: `backend/models/knowledge.py`
- Create: `backend/models/personas.py`

- [ ] **Step 1: Create models/knowledge.py**

File: `backend/models/knowledge.py`

```python
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
        await conn.execute(
            "DELETE FROM knowledge_base WHERE id = $1 AND user_id = $2::uuid", kid, user_id
        )


async def search_knowledge(user_id: str, query: str, limit: int = 3) -> list[dict]:
    """Keyword search in user's knowledge base."""
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
```

- [ ] **Step 2: Create models/personas.py**

File: `backend/models/personas.py`

```python
from db import get_pool


async def get_personas(user_id: str) -> list[dict]:
    """Get shared defaults + user's custom personas."""
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
    """Delete a persona — only if it belongs to the user and is not a default."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM personas WHERE id = $1 AND user_id = $2::uuid AND is_default = FALSE",
            pid, user_id,
        )
```

- [ ] **Step 3: Commit**

```bash
git add backend/models/knowledge.py backend/models/personas.py
git commit -m "feat: migrate knowledge and persona models to asyncpg with user isolation"
```

---

### Task 9: Google OAuth Module

**Files:**
- Create: `backend/oauth.py`

- [ ] **Step 1: Create oauth.py**

File: `backend/oauth.py`

```python
import os
from urllib.parse import urlencode

import httpx

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")


def get_google_auth_url() -> str:
    """Build the Google OAuth consent screen URL."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_google_code(code: str) -> dict:
    """Exchange authorization code for tokens and user info."""
    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT_URI,
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        # Get user info
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()
```

- [ ] **Step 2: Commit**

```bash
git add backend/oauth.py
git commit -m "feat: add Google OAuth module"
```

---

### Task 10: SQLite → PostgreSQL Migration Script

**Files:**
- Create: `backend/migrate.py`

- [ ] **Step 1: Create migrate.py**

File: `backend/migrate.py`

```python
"""One-time migration from SQLite to PostgreSQL.

Runs automatically on startup if SQLite database exists and PostgreSQL users table is empty.
Creates an admin user and copies all existing data.
"""
import os
import logging

import aiosqlite
import asyncpg

from auth import hash_password
from db import get_pool

logger = logging.getLogger(__name__)

SQLITE_PATH = os.environ.get("DB_PATH", "/app/data/voice_chat.db")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@localhost")


async def should_migrate() -> bool:
    """Check if migration is needed: SQLite exists and PG has no users."""
    if not os.path.exists(SQLITE_PATH):
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        return count == 0


async def run_migration():
    """Migrate all data from SQLite to PostgreSQL under an admin user."""
    if not await should_migrate():
        logger.info("No migration needed (no SQLite file or PG already has data)")
        return

    logger.info(f"Migrating SQLite data to PostgreSQL (admin: {ADMIN_EMAIL})...")
    pool = await get_pool()

    # Create admin user
    async with pool.acquire() as conn:
        admin_row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, auth_provider)
               VALUES ($1, $2, $3, 'local') RETURNING id""",
            ADMIN_EMAIL, hash_password("change-me-on-first-login"), "Admin",
        )
        admin_id = str(admin_row["id"])

    async with aiosqlite.connect(SQLITE_PATH) as sqlite:
        sqlite.row_factory = aiosqlite.Row

        # Migrate conversations + messages
        cursor = await sqlite.execute("SELECT * FROM conversations ORDER BY id")
        convs = await cursor.fetchall()
        conv_id_map = {}  # old_id -> new_id

        async with pool.acquire() as conn:
            for c in convs:
                row = await conn.fetchrow(
                    "INSERT INTO conversations (user_id, title, starred, created_at) VALUES ($1::uuid, $2, $3, $4) RETURNING id",
                    admin_id, c["title"], bool(c.get("starred", 0)), c["created_at"],
                )
                conv_id_map[c["id"]] = row["id"]

            # Migrate messages
            cursor = await sqlite.execute("SELECT * FROM messages ORDER BY id")
            msgs = await cursor.fetchall()
            for m in msgs:
                new_conv_id = conv_id_map.get(m["conversation_id"])
                if new_conv_id:
                    await conn.execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                        new_conv_id, m["role"], m["content"], m["created_at"],
                    )

        # Migrate knowledge base
        cursor = await sqlite.execute("SELECT * FROM knowledge_base ORDER BY id")
        knowledge = await cursor.fetchall()
        async with pool.acquire() as conn:
            for k in knowledge:
                await conn.execute(
                    "INSERT INTO knowledge_base (user_id, title, content, file_type, created_at) VALUES ($1::uuid, $2, $3, $4, $5)",
                    admin_id, k["title"], k["content"], k["file_type"], k["created_at"],
                )

        # Migrate custom personas (skip defaults — they're seeded by db.py)
        cursor = await sqlite.execute("SELECT * FROM personas WHERE is_default = 0 ORDER BY id")
        personas = await cursor.fetchall()
        async with pool.acquire() as conn:
            for p in personas:
                await conn.execute(
                    "INSERT INTO personas (user_id, name, prompt, created_at) VALUES ($1::uuid, $2, $3, $4)",
                    admin_id, p["name"], p["prompt"], p["created_at"],
                )

    logger.info(f"Migration complete: {len(convs)} conversations, {len(knowledge)} knowledge entries, {len(personas)} custom personas")
```

- [ ] **Step 2: Commit**

```bash
git add backend/migrate.py
git commit -m "feat: add SQLite to PostgreSQL migration script"
```

---

### Task 11: Wire Auth Routes into app.py

**Files:**
- Modify: `backend/app.py`

This is the biggest task — we replace all `database.py` imports with `models` imports, add auth endpoints, and protect existing routes with JWT.

- [ ] **Step 1: Rewrite app.py imports and startup**

Replace the top of `backend/app.py`. Change these imports:

Old:
```python
from database import (
    init_db, create_conversation, add_message, update_conversation_title,
    get_conversations, get_conversation_messages, clear_conversations,
    search_conversations, toggle_star_conversation,
    add_knowledge, get_all_knowledge, delete_knowledge, search_knowledge,
    get_personas, get_persona, add_persona, delete_persona,
)
```

New:
```python
from db import init_db, close_db
from models import (
    create_user, get_user_by_email, get_user_by_id,
    get_user_by_google_id, update_last_login, link_google_account,
    save_api_key, get_api_keys, delete_api_key, get_decrypted_key,
    create_conversation, add_message, update_conversation_title,
    get_conversations, get_conversation_messages, clear_conversations,
    search_conversations, toggle_star_conversation,
    add_knowledge, get_all_knowledge, delete_knowledge, search_knowledge,
    get_personas, get_persona, add_persona, delete_persona,
)
from auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    decode_token, get_user_id_from_request, get_user_id_from_ws,
)
from oauth import get_google_auth_url, exchange_google_code, GOOGLE_CLIENT_ID
from migrate import run_migration
```

Update the startup function:

```python
@app.on_event("startup")
async def startup():
    global whisper_model
    logger.info("Initializing database...")
    await init_db()
    await run_migration()
    logger.info(f"Loading Whisper model ({WHISPER_MODEL_SIZE})...")
    whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    logger.info("Whisper model loaded.")
    asyncio.create_task(warmup_ollama())


@app.on_event("shutdown")
async def shutdown():
    await close_db()
```

- [ ] **Step 2: Add auth endpoints**

Add these routes to `backend/app.py` after the health check:

```python
from fastapi import Depends
from fastapi.responses import RedirectResponse

# ---- Auth API ----
@app.post("/api/auth/register")
async def register(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "").strip()
    if not email or not password or not name:
        return JSONResponse(content={"error": "Email, password, and name required"}, status_code=400)
    if len(password) < 6:
        return JSONResponse(content={"error": "Password must be at least 6 characters"}, status_code=400)
    existing = await get_user_by_email(email)
    if existing:
        return JSONResponse(content={"error": "Email already registered"}, status_code=409)
    user = await create_user(email=email, name=name, password_hash=hash_password(password))
    token = create_access_token(str(user["id"]))
    refresh = create_refresh_token(str(user["id"]))
    resp = JSONResponse(content={"access_token": token, "user": {"id": str(user["id"]), "email": email, "name": name}})
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=7*86400)
    return resp


@app.post("/api/auth/login")
async def login(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = await get_user_by_email(email)
    if not user or not user.get("password_hash"):
        return JSONResponse(content={"error": "Invalid email or password"}, status_code=401)
    if not verify_password(password, user["password_hash"]):
        return JSONResponse(content={"error": "Invalid email or password"}, status_code=401)
    await update_last_login(str(user["id"]))
    token = create_access_token(str(user["id"]))
    refresh = create_refresh_token(str(user["id"]))
    resp = JSONResponse(content={
        "access_token": token,
        "user": {"id": str(user["id"]), "email": user["email"], "name": user["name"], "avatar_url": user.get("avatar_url")}
    })
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=7*86400)
    return resp


@app.post("/api/auth/refresh")
async def refresh_token(request: Request):
    token = request.cookies.get("refresh_token")
    if not token:
        return JSONResponse(content={"error": "No refresh token"}, status_code=401)
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return JSONResponse(content={"error": "Invalid token type"}, status_code=401)
        user_id = payload["sub"]
        new_access = create_access_token(user_id)
        return JSONResponse(content={"access_token": new_access})
    except ValueError:
        return JSONResponse(content={"error": "Token expired"}, status_code=401)


@app.get("/api/auth/me")
async def get_me(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    user = await get_user_by_id(user_id)
    if not user:
        return JSONResponse(content={"error": "User not found"}, status_code=404)
    return JSONResponse(content={
        "id": str(user["id"]), "email": user["email"], "name": user["name"],
        "avatar_url": user.get("avatar_url"), "auth_provider": user.get("auth_provider"),
    })


@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie("refresh_token")
    return resp


# ---- Google OAuth ----
@app.get("/api/auth/google")
async def google_login():
    if not GOOGLE_CLIENT_ID:
        return JSONResponse(content={"error": "Google OAuth not configured"}, status_code=501)
    return RedirectResponse(get_google_auth_url())


@app.get("/api/auth/google/callback")
async def google_callback(code: str = ""):
    if not code:
        return JSONResponse(content={"error": "No code provided"}, status_code=400)
    try:
        google_user = await exchange_google_code(code)
    except Exception as e:
        return JSONResponse(content={"error": f"Google auth failed: {str(e)}"}, status_code=400)

    google_id = google_user["id"]
    email = google_user["email"]
    name = google_user.get("name", email.split("@")[0])
    avatar = google_user.get("picture")

    # Check if user exists by google_id
    user = await get_user_by_google_id(google_id)
    if not user:
        # Check by email (account linking)
        user = await get_user_by_email(email)
        if user:
            await link_google_account(str(user["id"]), google_id, avatar)
        else:
            user = await create_user(email=email, name=name, auth_provider="google",
                                     google_id=google_id, avatar_url=avatar)

    await update_last_login(str(user["id"]))
    token = create_access_token(str(user["id"]))
    refresh = create_refresh_token(str(user["id"]))

    # Redirect to frontend with token (frontend reads from URL hash)
    resp = RedirectResponse(f"/?token={token}")
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=7*86400)
    return resp
```

- [ ] **Step 3: Add BYOK API key endpoints**

```python
# ---- BYOK API Keys ----
@app.get("/api/keys")
async def list_keys(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    keys = await get_api_keys(user_id)
    # Convert datetime objects to strings
    for k in keys:
        if k.get("created_at"):
            k["created_at"] = str(k["created_at"])
    return JSONResponse(content=keys)


@app.post("/api/keys")
async def add_key(request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    provider = data.get("provider", "").strip()
    key = data.get("key", "").strip()
    model_pref = data.get("model_preference")
    if not provider or not key:
        return JSONResponse(content={"error": "Provider and key required"}, status_code=400)
    kid = await save_api_key(user_id, provider, key, model_pref)
    return JSONResponse(content={"id": kid})


@app.delete("/api/keys/{provider}")
async def remove_key(provider: str, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await delete_api_key(user_id, provider)
    return JSONResponse(content={"ok": True})


@app.post("/api/keys/test")
async def test_key(request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    provider = data.get("provider", "")
    key = data.get("key", "")
    if provider == "openai":
        try:
            client = AsyncOpenAI(api_key=key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return JSONResponse(content={"valid": True})
        except Exception as e:
            return JSONResponse(content={"valid": False, "error": str(e)})
    # Other providers: just store (testing deferred to multi-provider feature)
    return JSONResponse(content={"valid": True, "note": "Key saved without verification"})
```

- [ ] **Step 4: Update existing routes to require auth and pass user_id**

Modify all existing data routes to extract `user_id` from the JWT and pass it to model functions. For each route, add:

```python
user_id = get_user_id_from_request(request)
if not user_id:
    return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
```

Then pass `user_id` to model calls. Example changes:

```python
# Conversations
@app.get("/api/conversations")
async def list_conversations(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_conversations(user_id))


@app.get("/api/conversations/search")
async def search_convs(request: Request, q: str = ""):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    if not q.strip():
        return JSONResponse(content=await get_conversations(user_id))
    return JSONResponse(content=await search_conversations(user_id, q.strip()))


@app.delete("/api/conversations")
async def clear_all_conversations(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await clear_conversations(user_id)
    return JSONResponse(content={"ok": True})


# Knowledge
@app.get("/api/knowledge")
async def list_knowledge(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_all_knowledge(user_id))


@app.post("/api/knowledge")
async def upload_knowledge(
    request: Request,
    title: str = Form(...),
    content: str = Form(None),
    file: UploadFile = File(None),
):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    # ... existing file parsing logic ...
    kid = await add_knowledge(user_id, title, text, file_type)
    return JSONResponse(content={"id": kid})


@app.delete("/api/knowledge/{kid}")
async def remove_knowledge(kid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await delete_knowledge(user_id, kid)
    return JSONResponse(content={"ok": True})


# Personas
@app.get("/api/personas")
async def list_personas(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_personas(user_id))


@app.post("/api/personas")
async def create_persona(request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    pid = await add_persona(user_id, data["name"], data["prompt"])
    return JSONResponse(content={"id": pid})


@app.delete("/api/personas/{pid}")
async def remove_persona(pid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await delete_persona(user_id, pid)
    return JSONResponse(content={"ok": True})
```

- [ ] **Step 5: Update WebSocket to authenticate via JWT**

Modify the WebSocket handler to extract user_id from the token query param:

```python
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    user_id = await get_user_id_from_ws(ws)
    if not user_id:
        await ws.send_json({"type": "error", "message": "Not authenticated"})
        await ws.close()
        return

    conversation_id = None
    # Use user's own OpenAI key if they have one saved
    user_openai_key = await get_decrypted_key(user_id, "openai")
    user_openai_client = AsyncOpenAI(api_key=user_openai_key) if user_openai_key else openai_client

    current_model = OPENAI_MODEL if (user_openai_key or OPENAI_API_KEY) else OLLAMA_MODEL
    # ... rest of handler, using user_id for create_conversation(user_id, ...) etc.
```

Inside the WebSocket handler, update all `create_conversation()` calls to pass `user_id`:

```python
conversation_id = await create_conversation(user_id, "New conversation")
```

And `search_knowledge()`:
```python
kb_results = await search_knowledge(user_id, user_text)
```

- [ ] **Step 6: Remove database.py import, delete file**

Delete `backend/database.py` — all functionality is now in `backend/db.py` + `backend/models/*`.

- [ ] **Step 7: Commit**

```bash
git add backend/app.py
git rm backend/database.py
git commit -m "feat: wire auth, BYOK, and user isolation into app routes"
```

---

### Task 12: Frontend — Login/Register Page + Auth State

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: Add login overlay HTML**

Add this right after `<body>` in index.html:

```html
<!-- Auth Overlay -->
<div id="authOverlay" class="auth-overlay">
  <div class="auth-box">
    <h2>Voice Chat AI</h2>
    <p class="auth-sub">Talk to any AI model with your voice</p>
    <div class="auth-tabs">
      <button class="auth-tab active" onclick="showAuthTab('login')">Login</button>
      <button class="auth-tab" onclick="showAuthTab('register')">Register</button>
    </div>
    <form id="loginForm" class="auth-form" onsubmit="return handleLogin(event)">
      <input type="email" id="loginEmail" placeholder="Email" required>
      <input type="password" id="loginPassword" placeholder="Password" required minlength="6">
      <button type="submit" class="auth-submit">Log In</button>
    </form>
    <form id="registerForm" class="auth-form" style="display:none" onsubmit="return handleRegister(event)">
      <input type="text" id="regName" placeholder="Your name" required>
      <input type="email" id="regEmail" placeholder="Email" required>
      <input type="password" id="regPassword" placeholder="Password (min 6 chars)" required minlength="6">
      <button type="submit" class="auth-submit">Create Account</button>
    </form>
    <div id="googleAuthBtn" class="google-btn-wrap" style="display:none">
      <div class="auth-divider"><span>or</span></div>
      <a href="/api/auth/google" class="google-btn">Sign in with Google</a>
    </div>
    <div id="authError" class="auth-error"></div>
  </div>
</div>
```

- [ ] **Step 2: Add auth CSS**

```css
.auth-overlay{position:fixed;inset:0;background:var(--bg);z-index:9999;display:flex;align-items:center;justify-content:center}
.auth-overlay.hidden{display:none}
.auth-box{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:40px;width:100%;max-width:400px;text-align:center}
.auth-box h2{color:#8b5cf6;margin-bottom:4px;font-size:24px}
.auth-sub{color:var(--text5);font-size:13px;margin-bottom:24px}
.auth-tabs{display:flex;gap:0;margin-bottom:20px;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.auth-tab{flex:1;padding:8px;background:transparent;border:none;color:var(--text4);cursor:pointer;font-size:14px}
.auth-tab.active{background:#8b5cf6;color:#fff}
.auth-form{display:flex;flex-direction:column;gap:12px}
.auth-form input{background:var(--input-bg);border:1px solid var(--border);color:var(--text);padding:12px 16px;border-radius:8px;font-size:14px;outline:none}
.auth-form input:focus{border-color:#8b5cf6}
.auth-submit{background:#8b5cf6;color:#fff;border:none;padding:12px;border-radius:8px;font-size:14px;cursor:pointer;font-weight:600}
.auth-submit:hover{background:#7c3aed}
.auth-divider{display:flex;align-items:center;gap:12px;margin:16px 0;color:var(--text6);font-size:12px}
.auth-divider::before,.auth-divider::after{content:'';flex:1;height:1px;background:var(--border)}
.google-btn{display:block;padding:12px;border:1px solid var(--border);border-radius:8px;color:var(--text);text-decoration:none;font-size:14px;transition:all .2s}
.google-btn:hover{border-color:#8b5cf6;background:var(--surface-hover)}
.auth-error{color:#ef4444;font-size:13px;margin-top:12px;min-height:20px}
```

- [ ] **Step 3: Add user menu to navbar**

Replace the existing Settings button area in the header-right with:

```html
<div id="userMenu" class="user-menu" style="display:none">
  <button class="user-avatar" onclick="toggleUserDropdown()">
    <img id="userAvatar" src="" alt="" style="display:none">
    <span id="userInitials"></span>
  </button>
  <div id="userDropdown" class="user-dropdown" style="display:none">
    <div class="user-dropdown-name" id="userDisplayName"></div>
    <div class="user-dropdown-email" id="userDisplayEmail"></div>
    <hr>
    <button onclick="openSettings()">Settings</button>
    <button onclick="handleLogout()">Logout</button>
  </div>
</div>
```

- [ ] **Step 4: Add auth JavaScript**

```javascript
let accessToken = null;
let currentUser = null;

// Check for token in URL (Google OAuth redirect)
const urlParams = new URLSearchParams(window.location.search);
const urlToken = urlParams.get('token');
if (urlToken) {
    accessToken = urlToken;
    window.history.replaceState({}, '', '/');
}

async function authFetch(url, options = {}) {
    if (!options.headers) options.headers = {};
    if (accessToken) options.headers['Authorization'] = `Bearer ${accessToken}`;
    const resp = await fetch(url, options);
    if (resp.status === 401) {
        // Try refresh
        const refreshResp = await fetch('/api/auth/refresh', { method: 'POST' });
        if (refreshResp.ok) {
            const data = await refreshResp.json();
            accessToken = data.access_token;
            options.headers['Authorization'] = `Bearer ${accessToken}`;
            return fetch(url, options);
        }
        showAuthOverlay();
        throw new Error('Not authenticated');
    }
    return resp;
}

async function checkAuth() {
    try {
        // Try refresh first if no access token
        if (!accessToken) {
            const refreshResp = await fetch('/api/auth/refresh', { method: 'POST' });
            if (refreshResp.ok) {
                const data = await refreshResp.json();
                accessToken = data.access_token;
            }
        }
        if (!accessToken) { showAuthOverlay(); return; }
        const resp = await authFetch('/api/auth/me');
        if (resp.ok) {
            currentUser = await resp.json();
            hideAuthOverlay();
            updateUserMenu();
            // Check if Google OAuth is available
            fetch('/api/auth/google').then(r => {
                if (r.status !== 501) document.getElementById('googleAuthBtn').style.display = 'block';
            }).catch(() => {});
        } else {
            showAuthOverlay();
        }
    } catch { showAuthOverlay(); }
}

function showAuthOverlay() {
    document.getElementById('authOverlay').classList.remove('hidden');
}
function hideAuthOverlay() {
    document.getElementById('authOverlay').classList.add('hidden');
}

function showAuthTab(tab) {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('loginForm').style.display = tab === 'login' ? 'flex' : 'none';
    document.getElementById('registerForm').style.display = tab === 'register' ? 'flex' : 'none';
    document.getElementById('authError').textContent = '';
}

async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    try {
        const resp = await fetch('/api/auth/login', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ email, password })
        });
        const data = await resp.json();
        if (resp.ok) {
            accessToken = data.access_token;
            currentUser = data.user;
            hideAuthOverlay();
            updateUserMenu();
            initApp();
        } else {
            document.getElementById('authError').textContent = data.error;
        }
    } catch (err) {
        document.getElementById('authError').textContent = 'Network error';
    }
    return false;
}

async function handleRegister(e) {
    e.preventDefault();
    const name = document.getElementById('regName').value;
    const email = document.getElementById('regEmail').value;
    const password = document.getElementById('regPassword').value;
    try {
        const resp = await fetch('/api/auth/register', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, email, password })
        });
        const data = await resp.json();
        if (resp.ok) {
            accessToken = data.access_token;
            currentUser = data.user;
            hideAuthOverlay();
            updateUserMenu();
            initApp();
        } else {
            document.getElementById('authError').textContent = data.error;
        }
    } catch (err) {
        document.getElementById('authError').textContent = 'Network error';
    }
    return false;
}

async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    accessToken = null;
    currentUser = null;
    showAuthOverlay();
}

function updateUserMenu() {
    if (!currentUser) return;
    document.getElementById('userMenu').style.display = 'flex';
    document.getElementById('userDisplayName').textContent = currentUser.name;
    document.getElementById('userDisplayEmail').textContent = currentUser.email;
    const initials = currentUser.name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    document.getElementById('userInitials').textContent = initials;
    if (currentUser.avatar_url) {
        const img = document.getElementById('userAvatar');
        img.src = currentUser.avatar_url;
        img.style.display = 'block';
        document.getElementById('userInitials').style.display = 'none';
    }
}

function toggleUserDropdown() {
    const dd = document.getElementById('userDropdown');
    dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
}

// Update WebSocket connection to include token
function connectWS() {
    const wsUrl = `ws://${location.host}/ws?token=${accessToken}`;
    ws = new WebSocket(wsUrl);
    // ... existing WebSocket setup
}

// Replace all fetch() calls with authFetch() throughout the existing JS
// On page load:
checkAuth();
```

- [ ] **Step 5: Add API Keys tab to Settings modal**

Add inside the settings modal content:

```html
<div class="settings-section">
  <h3>API Keys</h3>
  <p class="settings-hint">Add your own API keys to use different AI providers. Keys are encrypted and stored securely.</p>
  <div id="apiKeysContainer"></div>
  <div class="add-key-form">
    <select id="keyProvider">
      <option value="openai">OpenAI</option>
      <option value="anthropic">Anthropic</option>
      <option value="google">Google AI</option>
      <option value="groq">Groq</option>
    </select>
    <input type="password" id="keyInput" placeholder="Paste your API key">
    <button onclick="saveApiKey()">Test & Save</button>
  </div>
</div>
```

JavaScript for API keys:

```javascript
async function loadApiKeys() {
    const resp = await authFetch('/api/keys');
    const keys = await resp.json();
    const container = document.getElementById('apiKeysContainer');
    container.innerHTML = keys.map(k => `
        <div class="key-card">
            <span class="key-provider">${k.provider}</span>
            <span class="key-masked">${k.masked_key}</span>
            <button class="key-delete" onclick="deleteApiKey('${k.provider}')">Remove</button>
        </div>
    `).join('') || '<p class="no-keys">No API keys saved. Add one below.</p>';
}

async function saveApiKey() {
    const provider = document.getElementById('keyProvider').value;
    const key = document.getElementById('keyInput').value;
    if (!key) return;
    // Test first
    const testResp = await authFetch('/api/keys/test', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ provider, key })
    });
    const testResult = await testResp.json();
    if (!testResult.valid) {
        alert('Key invalid: ' + (testResult.error || 'Unknown error'));
        return;
    }
    // Save
    await authFetch('/api/keys', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ provider, key })
    });
    document.getElementById('keyInput').value = '';
    loadApiKeys();
}

async function deleteApiKey(provider) {
    await authFetch(`/api/keys/${provider}`, { method: 'DELETE' });
    loadApiKeys();
}
```

- [ ] **Step 6: Commit**

```bash
git add backend/templates/index.html
git commit -m "feat: add login/register UI, user menu, and API keys settings"
```

---

### Task 13: Integration Test — Full Auth Flow

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

File: `tests/test_integration.py`

```python
import os
import pytest
import asyncio

os.environ["JWT_SECRET"] = "test-secret"
os.environ["ENCRYPTION_KEY"] = "a" * 64
os.environ["DATABASE_URL"] = "postgresql://voice:voice@localhost:5432/voicechat_test"

from httpx import AsyncClient, ASGITransport
from app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_register_login_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        resp = await client.post("/api/auth/register", json={
            "email": "test@example.com", "password": "test123", "name": "Test User"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["email"] == "test@example.com"

        token = data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Get profile
        resp = await client.get("/api/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test User"

        # Duplicate register fails
        resp = await client.post("/api/auth/register", json={
            "email": "test@example.com", "password": "test123", "name": "Dupe"
        })
        assert resp.status_code == 409

        # Login
        resp = await client.post("/api/auth/login", json={
            "email": "test@example.com", "password": "test123"
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

        # Wrong password
        resp = await client.post("/api/auth/login", json={
            "email": "test@example.com", "password": "wrong"
        })
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_access_blocked():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/conversations")
        assert resp.status_code == 401
        resp = await client.get("/api/knowledge")
        assert resp.status_code == 401
        resp = await client.get("/api/keys")
        assert resp.status_code == 401
```

- [ ] **Step 2: Run integration test**

```bash
cd backend && python -m pytest tests/test_integration.py -v
```

Expected: All tests PASS (requires PostgreSQL running)

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add auth integration tests"
```

---

### Task 14: Update .env and Final Docker Rebuild

**Files:**
- Modify: `.env`

- [ ] **Step 1: Generate encryption key and JWT secret**

```bash
python -c "import secrets; print('ENCRYPTION_KEY=' + secrets.token_hex(32)); print('JWT_SECRET=' + secrets.token_hex(32))"
```

Copy the output and add to `.env`:

```env
OPENAI_API_KEY=sk-proj-your-key
DATABASE_URL=postgresql://voice:voice@postgres:5432/voicechat
ENCRYPTION_KEY=<generated-64-char-hex>
JWT_SECRET=<generated-64-char-hex>
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
ADMIN_EMAIL=ralph@example.com
```

- [ ] **Step 2: Full Docker rebuild and test**

```bash
docker compose down
docker compose build app
docker compose up -d
```

Wait for startup, then verify:

```bash
docker compose logs app --tail 20
```

Expected: "Initializing database... Schema OK... Migration complete... Whisper model loaded."

- [ ] **Step 3: Manual verification**

1. Open http://localhost:8000 — should see login page
2. Register a new account
3. Verify you see the voice chat UI after login
4. Open Settings → API Keys tab
5. Add your OpenAI key → verify "Test & Save" works
6. Start a voice conversation → verify it works with your saved key
7. Logout → verify you see the login page again
8. Login → verify your conversations are still there

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete auth + BYOK implementation"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Docker + dependencies | docker-compose.yml, requirements.txt, .env.example |
| 2 | DB pool + schema | db.py, schema.sql |
| 3 | AES-256 crypto | crypto.py, test_crypto.py |
| 4 | JWT + passwords | auth.py, test_auth.py |
| 5 | User model | models/users.py |
| 6 | API keys model | models/apikeys.py |
| 7 | Conversations model | models/conversations.py |
| 8 | Knowledge + persona models | models/knowledge.py, models/personas.py |
| 9 | Google OAuth | oauth.py |
| 10 | SQLite migration | migrate.py |
| 11 | Wire into app.py | app.py (major rewrite) |
| 12 | Frontend auth UI | index.html |
| 13 | Integration tests | test_integration.py |
| 14 | Final rebuild + verify | .env, Docker |
