# User Authentication + BYOK API Key Management

## Overview

Add multi-user authentication and Bring Your Own Key (BYOK) API key management to the voice chat AI system. This is the foundation for turning the app into a multi-user SaaS product where users can talk to any AI model using their own API keys.

## Target Product Direction

- **Audience:** General consumers (ChatGPT voice mode competitor)
- **Differentiator:** Model freedom — use any LLM (OpenAI, Claude, Gemini, Groq, local Ollama) in one app
- **Business model:** BYOK — users bring their own API keys, zero LLM cost for us

## Architecture

### New Components (Docker)

- **PostgreSQL 16** container — replaces SQLite for all data storage
- **Backend changes** — JWT auth, Google OAuth, AES-256 encryption layer

### Auth Flow

1. User visits app → sees login/register page
2. Signs up with email+password OR "Sign in with Google"
3. Gets JWT token (access + refresh)
4. All API calls include JWT → backend identifies user
5. Each user has isolated conversations, knowledge base, personas, and API keys

### Data Isolation

Every table gets a `user_id` column. User A never sees User B's data.

## Database Schema

### New Tables

```sql
-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255),  -- NULL for OAuth-only users
    name VARCHAR(255) NOT NULL,
    avatar_url TEXT,
    auth_provider VARCHAR(20) DEFAULT 'local',  -- 'local' | 'google'
    google_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP DEFAULT NOW()
);

-- Encrypted API keys
CREATE TABLE user_api_keys (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,  -- 'openai' | 'anthropic' | 'google' | 'groq' | 'custom'
    encrypted_key BYTEA NOT NULL,   -- AES-256-GCM encrypted
    iv BYTEA NOT NULL,              -- Random IV per key
    model_preference VARCHAR(100),  -- Default model for this provider
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, provider)
);
```

### Modified Tables (add user_id)

```sql
-- Conversations: add user_id
ALTER TABLE conversations ADD COLUMN user_id UUID NOT NULL REFERENCES users(id);

-- Knowledge base: add user_id
ALTER TABLE knowledge_base ADD COLUMN user_id UUID NOT NULL REFERENCES users(id);

-- Personas: add user_id (NULL = shared default, set = user-created)
ALTER TABLE personas ADD COLUMN user_id UUID REFERENCES users(id);
```

### Encryption

- Algorithm: AES-256-GCM
- Each key gets its own random 12-byte IV
- Encryption key stored as `ENCRYPTION_KEY` env var (32-byte hex string)
- Keys are decrypted on-the-fly when making API calls, never cached in plaintext

## API Design

### Auth Endpoints

```
POST /api/auth/register        — {email, password, name} → {access_token}
POST /api/auth/login           — {email, password} → {access_token}
GET  /api/auth/google          — redirect to Google OAuth consent screen
GET  /api/auth/google/callback — exchange auth code → {access_token}
POST /api/auth/refresh         — refresh token (httpOnly cookie) → {access_token}
GET  /api/auth/me              — get current user profile
```

### JWT Structure

- **Access token:** 15 min expiry, stored in browser memory (not localStorage)
- **Refresh token:** 7 days expiry, stored in httpOnly cookie
- **WebSocket auth:** send JWT as first message after connect

### BYOK Endpoints

```
GET    /api/keys             — list providers with masked keys (last 4 chars only)
POST   /api/keys             — {provider, key, model_preference} → encrypt & save
DELETE /api/keys/:provider   — remove a saved key
POST   /api/keys/test        — {provider, key} → make 1-token test call → valid/invalid
```

### Key Test Flow

When a user saves an API key, make a minimal API call to verify:
- OpenAI: 1-token completion with gpt-4o-mini
- Anthropic: 1-token completion with claude-haiku
- Google: 1-token completion with gemini-flash
- Groq: 1-token completion with llama-3-8b

Return immediate feedback: "Key valid" or "Key invalid: {error}"

Note: For this feature, only OpenAI key testing is implemented (it's the only provider currently wired up). Other providers (Anthropic, Google, Groq) can store keys but testing is deferred until multi-provider LLM support is built.

## Google OAuth Flow

### Setup

- Google Cloud Console → OAuth 2.0 credentials
- Redirect URI: `https://yourdomain.com/api/auth/google/callback`
- Env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`

### Flow

1. User clicks "Sign in with Google"
2. Backend redirects to Google consent screen
3. Google redirects back with auth code
4. Backend exchanges code → gets user info (email, name, avatar)
5. If email exists in DB → login
6. If new email → create account
7. Return JWT

### Account Linking

If a user registers with email/password first, then later signs in with Google using the same email — accounts merge automatically. The `google_id` and `avatar_url` are added to the existing account.

## UI Changes

### Login/Register Page

- Shown when user is not authenticated
- Two tabs: Login / Register
- Google OAuth button: "Sign in with Google"
- Email + password form
- Clean, minimal design matching existing dark theme

### Settings → API Keys Tab

- Cards for each supported provider (OpenAI, Anthropic, Google, Groq)
- Each card shows: provider logo, masked key (sk-...xxxx), test status
- "Add Key" button → input field + "Test & Save" button
- "Delete" button per key
- Visual indicator: green check (valid), red x (invalid), gray (not set)

### Navbar Changes

- Show user avatar (from Google) or initials
- User name
- Dropdown: Settings, Logout

## Migration: SQLite → PostgreSQL

### Strategy

1. On first startup, check if SQLite database exists
2. If yes, run migration script:
   - Create a default admin user (from env var `ADMIN_EMAIL`)
   - Copy all conversations, messages, knowledge, personas to PostgreSQL with admin user_id
3. Keep SQLite file as backup, stop using it
4. All new data goes to PostgreSQL

### Docker Compose Changes

```yaml
services:
  app:
    # existing config
    environment:
      - DATABASE_URL=postgresql://voice:voice@postgres:5432/voicechat
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
      - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
      - JWT_SECRET=${JWT_SECRET}
    depends_on:
      - postgres
      - ollama

  ollama:
    # unchanged

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: voicechat
      POSTGRES_USER: voice
      POSTGRES_PASSWORD: voice
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U voice"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### New Environment Variables

```env
# .env additions
DATABASE_URL=postgresql://voice:voice@postgres:5432/voicechat
ENCRYPTION_KEY=<32-byte-hex-string>
JWT_SECRET=<random-secret>
GOOGLE_CLIENT_ID=<from-google-console>
GOOGLE_CLIENT_SECRET=<from-google-console>
ADMIN_EMAIL=ralph@example.com
```

## Python Dependencies (additions)

```
asyncpg           # async PostgreSQL driver
python-jose       # JWT encoding/decoding
passlib[bcrypt]   # password hashing
cryptography      # AES-256-GCM encryption
httpx             # already present, used for Google OAuth token exchange
```

## Security Considerations

- Passwords hashed with bcrypt (12 rounds)
- API keys encrypted with AES-256-GCM, unique IV per key
- JWT access tokens short-lived (15 min)
- Refresh tokens in httpOnly cookies (not accessible to JS)
- CORS restricted to app domain in production
- Rate limiting on auth endpoints (future — Redis)
- HTTPS required in production (Caddy handles this)
