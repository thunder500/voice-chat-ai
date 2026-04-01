import asyncio
import io
import json
import os
import tempfile
import logging

import base64
import edge_tts
import httpx
from openai import AsyncOpenAI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from faster_whisper import WhisperModel

from db import init_db, close_db, DEFAULT_KNOWLEDGE
from models import (
    create_user, get_user_by_email, get_user_by_id,
    get_user_by_google_id, update_last_login, link_google_account,
    save_api_key, get_api_keys, delete_api_key, get_decrypted_key,
    create_conversation, add_message, update_conversation_title,
    get_conversations, get_conversation_messages, clear_conversations,
    search_conversations, toggle_star_conversation,
    add_knowledge, get_all_knowledge, delete_knowledge, search_knowledge,
    get_personas, get_persona, add_persona, delete_persona,
    create_meeting, update_meeting_summary, get_meeting,
    get_meetings, delete_meeting, update_meeting_kb_toggle,
)
from meeting_summarizer import summarize_meeting
from auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    decode_token, get_user_id_from_request, get_user_id_from_ws,
)
from oauth import get_google_auth_url, exchange_google_code, GOOGLE_CLIENT_ID
from migrate import run_migration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Voice Chat AI")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8000")

# OpenAI model name prefixes/names — extend as new models are released
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
GROQ_MODEL_PREFIXES = ("llama", "mixtral", "gemma", "whisper")
GEMINI_MODEL_PREFIXES = ("gemini-",)

# Provider detection: given a model name, determine which provider it belongs to
def detect_provider(model_name: str) -> str:
    if any(model_name.startswith(p) for p in ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if any(model_name.startswith(p) for p in GEMINI_MODEL_PREFIXES):
        return "google"
    if any(model_name.startswith(p) for p in OPENAI_MODEL_PREFIXES):
        return "openai"
    # Groq models have specific names
    if model_name in GROQ_MODELS:
        return "groq"
    return "ollama"

GROQ_MODELS = {
    "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
    "mixtral-8x7b-32768", "gemma2-9b-it",
}

# OpenAI client (initialized only if key is set)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

whisper_model = None

DEFAULT_SYSTEM_PROMPT = """You are on a live phone call. Your words are spoken aloud. Be fast and concise.

NEVER DO THESE — they will be heard literally by the user:
- No parentheses actions: (laughs) (pauses) (serious tone) — BANNED
- No asterisk actions: *smiles* *nods* — BANNED
- No ellipsis: ... — BANNED
- No emojis

INSTEAD express emotion with words:
- Laughing: "Ha!" or "That's hilarious!"
- Thinking: "Hmm, let me think" or "Well"
- Surprised: "Oh wow!" or "No way!"
- Empathy: "Aw man" or "Yeah that's tough"

CODE RESPONSES:
- When sharing code, use markdown code blocks (```language) — they'll be rendered visually in the chat.
- In your SPOKEN words, only describe what the code does. NEVER read code aloud line by line.
- Example: "Here's a function that sorts an array. I've put the code in the chat for you."
- Keep the spoken explanation brief — the user can read the code in the chat.

RESPONSE LENGTH:
- Default: 1-2 sentences. Be quick like a real conversation.
- Only go longer if the user asks for a story or detailed explanation.
- If you ask a question, end with [WAIT]"""

GREETING_TEXT = "Hey! Great to hear from you. What's on your mind?"
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")

import re


def clean_for_speech(text: str) -> str:
    """Strip code blocks, stage directions, and non-speech text."""
    # Remove code blocks entirely (don't read code aloud)
    text = re.sub(r'```[\s\S]*?```', ' I\'ve included the code in the chat. ', text)
    # Remove inline code
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\*[^*]+\*', '', text)        # *actions*
    text = re.sub(r'\([^)]*\)', '', text)         # (actions)
    text = re.sub(r'\[[^\]]*\]', '', text)        # [tags] like [WAIT]
    text = re.sub(r'\.{2,}', ' ', text)           # ... → space
    text = re.sub(r'[#*_~>]', '', text)           # markdown chars (keep backtick already removed)
    text = re.sub(r'\s{2,}', ' ', text)           # collapse spaces
    return text.strip()


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


async def warmup_ollama():
    """Ping Ollama to keep model loaded in memory."""
    await asyncio.sleep(2)
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await c.post(f"{OLLAMA_URL}/api/chat", json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                    "options": {"num_predict": 1},
                })
            logger.info("Ollama model warmed up")
        except Exception:
            pass
        await asyncio.sleep(120)  # re-warm every 2 minutes


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("/app/templates/index.html", "r") as f:
        return HTMLResponse(content=f.read())


# ---- Health Check ----
@app.get("/api/health")
async def health():
    status = {"whisper": whisper_model is not None, "ollama": False, "openai": bool(OPENAI_API_KEY)}
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            status["ollama"] = r.status_code == 200
    except Exception:
        pass
    return JSONResponse(content=status)


# ---- Admin Dashboard ----
@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    user = await get_user_by_id(user_id)
    if not user or not user.get("is_admin"):
        return JSONResponse(content={"error": "Admin access required"}, status_code=403)

    from db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        # Active = logged in within last 24h
        active_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE last_login > NOW() - INTERVAL '24 hours'"
        )
        # Active = logged in within last 7d
        active_7d = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE last_login > NOW() - INTERVAL '7 days'"
        )
        total_conversations = await conn.fetchval("SELECT COUNT(*) FROM conversations")
        total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")
        total_knowledge = await conn.fetchval("SELECT COUNT(*) FROM knowledge_base")

        # User list with stats
        users = await conn.fetch("""
            SELECT u.id, u.email, u.name, u.auth_provider, u.is_admin, u.created_at, u.last_login,
                   (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) as conv_count,
                   (SELECT COUNT(*) FROM messages m JOIN conversations c ON m.conversation_id = c.id WHERE c.user_id = u.id) as msg_count
            FROM users u ORDER BY u.last_login DESC
        """)

    from db import _serialize_row
    return JSONResponse(content={
        "total_users": total_users,
        "active_24h": active_24h,
        "active_7d": active_7d,
        "total_conversations": total_conversations,
        "total_messages": total_messages,
        "total_knowledge": total_knowledge,
        "users": [_serialize_row(r) for r in users],
    })


# ---- Auth Endpoints ----
@app.post("/api/auth/register")
async def register(data: dict):
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()

    if not email or not password or not name:
        return JSONResponse(content={"error": "email, password, and name are required"}, status_code=400)
    if len(password) < 8:
        return JSONResponse(content={"error": "Password must be at least 8 characters"}, status_code=400)

    existing = await get_user_by_email(email)
    if existing:
        return JSONResponse(content={"error": "Email already registered"}, status_code=409)

    user = await create_user(email=email, name=name, password_hash=hash_password(password))
    user_id = str(user["id"])

    # Seed default knowledge base for new user
    for kb in DEFAULT_KNOWLEDGE:
        await add_knowledge(user_id, kb["title"], kb["content"], kb["file_type"])

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    response = JSONResponse(content={
        "access_token": access_token,
        "token_type": "bearer",
        "user": {"id": user_id, "email": user["email"], "name": user["name"]},
    })
    response.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, secure=False, samesite="lax", path="/", max_age=7 * 24 * 3600,
    )
    return response


@app.post("/api/auth/login")
async def login(data: dict):
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return JSONResponse(content={"error": "email and password are required"}, status_code=400)

    user = await get_user_by_email(email)
    if not user or not user.get("password_hash"):
        return JSONResponse(content={"error": "Invalid credentials"}, status_code=401)
    if not verify_password(password, user["password_hash"]):
        return JSONResponse(content={"error": "Invalid credentials"}, status_code=401)

    user_id = str(user["id"])
    await update_last_login(user_id)

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    response = JSONResponse(content={
        "access_token": access_token,
        "token_type": "bearer",
        "user": {"id": user_id, "email": user["email"], "name": user["name"]},
    })
    response.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, secure=False, samesite="lax", path="/", max_age=7 * 24 * 3600,
    )
    return response


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
    except (ValueError, KeyError):
        return JSONResponse(content={"error": "Invalid or expired refresh token"}, status_code=401)

    access_token = create_access_token(user_id)
    return JSONResponse(content={"access_token": access_token, "token_type": "bearer"})


@app.get("/api/auth/meeting-token")
async def get_meeting_token(request: Request):
    """Generate a long-lived token (24h) for the Chrome extension."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    from datetime import timedelta
    token = create_access_token(user_id, expires_delta=timedelta(hours=24))
    return JSONResponse(content={"token": token})


@app.get("/api/auth/me")
async def get_me(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    user = await get_user_by_id(user_id)
    if not user:
        return JSONResponse(content={"error": "User not found"}, status_code=404)
    return JSONResponse(content={
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "avatar_url": user.get("avatar_url"),
        "auth_provider": user.get("auth_provider"),
        "is_admin": bool(user.get("is_admin")),
    })


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse(content={"ok": True})
    response.delete_cookie("refresh_token")
    return response


@app.get("/api/auth/google")
async def google_login():
    url = get_google_auth_url()
    return RedirectResponse(url=url)


@app.get("/api/auth/google/callback")
async def google_callback(code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse(url=f"{FRONTEND_URL}/?error=google_auth_failed")

    try:
        userinfo = await exchange_google_code(code)
    except Exception as e:
        logger.error(f"Google OAuth error: {e}")
        return RedirectResponse(url=f"{FRONTEND_URL}/?error=google_auth_failed")

    google_id = userinfo.get("id") or userinfo.get("sub")
    email = userinfo.get("email", "").lower()
    name = userinfo.get("name", "")
    avatar_url = userinfo.get("picture")

    if not google_id or not email:
        return RedirectResponse(url=f"{FRONTEND_URL}/?error=google_missing_info")

    # Find or create user
    user = await get_user_by_google_id(google_id)
    if not user:
        user = await get_user_by_email(email)
        if user:
            # Link Google to existing account
            await link_google_account(str(user["id"]), google_id, avatar_url)
            user = await get_user_by_id(str(user["id"]))
        else:
            # Create new user via Google
            user = await create_user(
                email=email, name=name,
                auth_provider="google", google_id=google_id, avatar_url=avatar_url,
            )
            # Seed default knowledge for new Google user
            new_uid = str(user["id"])
            for kb in DEFAULT_KNOWLEDGE:
                await add_knowledge(new_uid, kb["title"], kb["content"], kb["file_type"])

    user_id = str(user["id"])
    await update_last_login(user_id)

    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)

    response = RedirectResponse(url=f"{FRONTEND_URL}/?token={access_token}")
    response.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, secure=False, samesite="lax", path="/", max_age=7 * 24 * 3600,
    )
    return response


# ---- BYOK (Bring Your Own Key) Endpoints ----
@app.get("/api/keys")
async def list_keys(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    keys = await get_api_keys(user_id)
    # Return only safe fields (masked key, no raw encrypted bytes)
    safe = [
        {
            "id": k["id"],
            "provider": k["provider"],
            "masked_key": k.get("masked_key", "...****"),
            "model_preference": k.get("model_preference"),
        }
        for k in keys
    ]
    return JSONResponse(content=safe)


@app.post("/api/keys")
async def add_key(request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    provider = (data.get("provider") or "").strip().lower()
    api_key = (data.get("api_key") or "").strip()
    model_preference = data.get("model_preference")
    if not provider or not api_key:
        return JSONResponse(content={"error": "provider and api_key are required"}, status_code=400)
    kid = await save_api_key(user_id, provider, api_key, model_preference)
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
    provider = (data.get("provider") or "openai").strip()
    api_key = (data.get("api_key") or data.get("key") or "").strip()
    if not api_key:
        return JSONResponse(content={"error": "api_key is required"}, status_code=400)
    try:
        if provider == "openai":
            test_client = AsyncOpenAI(api_key=api_key)
            await test_client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], max_tokens=1,
            )
        elif provider == "anthropic":
            import anthropic
            test_client = anthropic.AsyncAnthropic(api_key=api_key)
            await test_client.messages.create(
                model="claude-3-5-haiku-20241022", max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        elif provider == "groq":
            test_client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
            await test_client.chat.completions.create(
                model="llama-3.1-8b-instant", messages=[{"role": "user", "content": "hi"}], max_tokens=1,
            )
        elif provider == "google":
            test_client = AsyncOpenAI(api_key=api_key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
            await test_client.chat.completions.create(
                model="gemini-2.0-flash", messages=[{"role": "user", "content": "hi"}], max_tokens=1,
            )
        else:
            return JSONResponse(content={"ok": True, "note": "Key saved without verification"})
        return JSONResponse(content={"ok": True})
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=400)


# ---- Conversation API ----
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


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_conversation_messages(cid))


@app.delete("/api/conversations")
async def clear_all_conversations(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await clear_conversations(user_id)
    return JSONResponse(content={"ok": True})


@app.get("/api/conversations/{cid}/export")
async def export_conversation(cid: int, request: Request):
    """Export a conversation as a plain-text transcript."""
    from fastapi.responses import PlainTextResponse
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    msgs = await get_conversation_messages(cid)
    if not msgs:
        return JSONResponse(content={"error": "Conversation not found"}, status_code=404)
    lines = []
    for m in msgs:
        speaker = "You" if m["role"] == "user" else "AI"
        lines.append(f"[{speaker}]\n{m['content']}\n")
    text = "\n".join(lines)
    return PlainTextResponse(content=text, headers={
        "Content-Disposition": f'attachment; filename="conversation-{cid}.txt"'
    })


@app.patch("/api/conversations/{cid}")
async def rename_conversation(cid: int, request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    await update_conversation_title(cid, data.get("title", "Untitled"))
    return JSONResponse(content={"ok": True})


@app.patch("/api/conversations/{cid}/star")
async def star_conversation(cid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    starred = await toggle_star_conversation(cid)
    return JSONResponse(content={"ok": True, "starred": starred})


# ---- Voices API ----
VOICE_LIST = [
    # OpenAI TTS voices (natural, requires OpenAI key)
    {"id": "alloy", "name": "Alloy", "gender": "Neutral", "accent": "OpenAI"},
    {"id": "nova", "name": "Nova", "gender": "Female", "accent": "OpenAI"},
    {"id": "shimmer", "name": "Shimmer", "gender": "Female", "accent": "OpenAI"},
    {"id": "echo", "name": "Echo", "gender": "Male", "accent": "OpenAI"},
    {"id": "onyx", "name": "Onyx", "gender": "Male", "accent": "OpenAI"},
    {"id": "fable", "name": "Fable", "gender": "Neutral", "accent": "OpenAI"},
    # Edge-TTS voices (free, neural)
    {"id": "en-US-AriaNeural", "name": "Aria", "gender": "Female", "accent": "US"},
    {"id": "en-US-AvaNeural", "name": "Ava", "gender": "Female", "accent": "US"},
    {"id": "en-US-EmmaNeural", "name": "Emma", "gender": "Female", "accent": "US"},
    {"id": "en-US-JennyNeural", "name": "Jenny", "gender": "Female", "accent": "US"},
    {"id": "en-US-MichelleNeural", "name": "Michelle", "gender": "Female", "accent": "US"},
    {"id": "en-US-AndrewNeural", "name": "Andrew", "gender": "Male", "accent": "US"},
    {"id": "en-US-BrianNeural", "name": "Brian", "gender": "Male", "accent": "US"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher", "gender": "Male", "accent": "US"},
    {"id": "en-US-EricNeural", "name": "Eric", "gender": "Male", "accent": "US"},
    {"id": "en-US-GuyNeural", "name": "Guy", "gender": "Male", "accent": "US"},
    {"id": "en-US-RogerNeural", "name": "Roger", "gender": "Male", "accent": "US"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia", "gender": "Female", "accent": "UK"},
    {"id": "en-GB-RyanNeural", "name": "Ryan", "gender": "Male", "accent": "UK"},
    {"id": "en-GB-LibbyNeural", "name": "Libby", "gender": "Female", "accent": "UK"},
    {"id": "en-GB-ThomasNeural", "name": "Thomas", "gender": "Male", "accent": "UK"},
    {"id": "en-AU-NatashaNeural", "name": "Natasha", "gender": "Female", "accent": "AU"},
    {"id": "en-AU-WilliamMultilingualNeural", "name": "William", "gender": "Male", "accent": "AU"},
    {"id": "en-IN-NeerjaExpressiveNeural", "name": "Neerja", "gender": "Female", "accent": "India"},
    {"id": "en-IN-PrabhatNeural", "name": "Prabhat", "gender": "Male", "accent": "India"},
    {"id": "en-IE-EmilyNeural", "name": "Emily", "gender": "Female", "accent": "Irish"},
    {"id": "en-IE-ConnorNeural", "name": "Connor", "gender": "Male", "accent": "Irish"},
]


@app.get("/api/voices")
async def list_voices():
    return JSONResponse(content=VOICE_LIST)


# ---- Models API ----
@app.get("/api/models")
async def list_models(request: Request):
    user_id = get_user_id_from_request(request)
    models = []

    # Check which provider keys the user has
    has_openai = bool(OPENAI_API_KEY)
    has_anthropic = False
    has_groq = False
    has_google = False
    if user_id:
        for provider in ["openai", "anthropic", "groq", "google"]:
            key = await get_decrypted_key(user_id, provider)
            if key:
                if provider == "openai": has_openai = True
                elif provider == "anthropic": has_anthropic = True
                elif provider == "groq": has_groq = True
                elif provider == "google": has_google = True

    # OpenAI models
    if has_openai:
        models.extend([
            {"name": "gpt-4o-mini", "size": 0, "provider": "openai"},
            {"name": "gpt-4o", "size": 0, "provider": "openai"},
            {"name": "gpt-3.5-turbo", "size": 0, "provider": "openai"},
        ])

    # Anthropic (Claude) models
    if has_anthropic:
        models.extend([
            {"name": "claude-sonnet-4-20250514", "size": 0, "provider": "anthropic"},
            {"name": "claude-3-5-haiku-20241022", "size": 0, "provider": "anthropic"},
        ])

    # Groq models (ultra-fast)
    if has_groq:
        models.extend([
            {"name": "llama-3.3-70b-versatile", "size": 0, "provider": "groq"},
            {"name": "llama-3.1-8b-instant", "size": 0, "provider": "groq"},
            {"name": "mixtral-8x7b-32768", "size": 0, "provider": "groq"},
        ])

    # Google Gemini models
    if has_google:
        models.extend([
            {"name": "gemini-2.0-flash", "size": 0, "provider": "google"},
            {"name": "gemini-1.5-flash", "size": 0, "provider": "google"},
        ])

    # Local Ollama models (always available)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("models", []):
                models.append({"name": m["name"], "size": m.get("size", 0), "provider": "ollama"})
    except Exception as e:
        logger.error(f"Ollama API error: {e}")
    return JSONResponse(content=models)


# ---- Knowledge Base API ----
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
    if file and file.filename:
        raw = await file.read()
        if file.filename.endswith(".pdf"):
            try:
                import pdfplumber
                pdf_io = io.BytesIO(raw)
                with pdfplumber.open(pdf_io) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception as e:
                return JSONResponse(content={"error": f"PDF parse error: {str(e)}"}, status_code=400)
        else:
            text = raw.decode("utf-8", errors="ignore")
        kid = await add_knowledge(user_id, title or file.filename, text, file.content_type or "file")
    elif content:
        kid = await add_knowledge(user_id, title, content, "text")
    else:
        return JSONResponse(content={"error": "No content or file provided"}, status_code=400)

    # Vectorize into ChromaDB
    from models.knowledge import vectorize_knowledge
    user_openai_key_embed = await get_decrypted_key(user_id, "openai")
    asyncio.create_task(vectorize_knowledge(user_id, kid, title or (file.filename if file else ""), text if 'text' in dir() else content, user_openai_key_embed))

    return JSONResponse(content={"id": kid})


@app.delete("/api/knowledge/{kid}")
async def remove_knowledge(kid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    from models.knowledge import devectorize_knowledge
    asyncio.create_task(devectorize_knowledge(user_id, kid))
    await delete_knowledge(user_id, kid)
    return JSONResponse(content={"ok": True})


# ---- Personas API ----
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


# ---- Meetings API ----
@app.post("/api/meetings/upload")
async def upload_meeting_audio(request: Request, audio: UploadFile = File(...), duration: str = Form("0")):
    """Upload full meeting audio, transcribe with Whisper, return transcript."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)

    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        return JSONResponse(content={"error": "Audio file too small"}, status_code=400)

    logger.info(f"Meeting upload: {len(audio_bytes)} bytes, {duration}s")

    # Transcribe the full audio
    text = await asyncio.to_thread(transcribe_audio_sync, audio_bytes, True)
    if not text:
        # Try without meeting mode
        text = await asyncio.to_thread(transcribe_audio_sync, audio_bytes, False)
    if not text:
        text = "(No speech detected in audio)"

    logger.info(f"Meeting transcript: {len(text)} chars")

    # Save to database
    dur = int(duration) if duration.isdigit() else 0
    mid = await create_meeting(user_id, "Untitled Meeting", text, dur)

    return JSONResponse(content={"id": mid, "transcript": text, "duration": dur})


@app.post("/api/meetings/{mid}/summarize")
async def summarize_meeting_endpoint(mid: int, request: Request, data: dict):
    """Generate summary for an existing meeting."""
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)

    meeting = await get_meeting(mid, user_id)
    if not meeting:
        return JSONResponse(content={"error": "Meeting not found"}, status_code=404)

    transcript = meeting.get("transcript", "")
    if not transcript or transcript == "(No speech detected in audio)":
        return JSONResponse(content={"error": "No transcript to summarize"}, status_code=400)

    model = data.get("model", "gpt-4o-mini")

    # Get user keys
    user_keys = {}
    for prov in ["openai", "anthropic", "groq", "google"]:
        k = await get_decrypted_key(user_id, prov)
        if k:
            user_keys[prov] = k

    try:
        result = await summarize_meeting(transcript, model, user_keys)
        await update_meeting_summary(mid, result["title"], "\n".join(result["summary"]),
                                     result["action_items"], model)

        # Vectorize
        try:
            from embeddings import chunk_text, embed_texts
            from vectorstore import add_chunks
            openai_key = user_keys.get("openai")
            summary_text = f"{result['title']}\n\n{chr(10).join(result['summary'])}\n\nAction Items: {json.dumps(result['action_items'])}"
            chunks = chunk_text(summary_text)
            embeddings = await embed_texts(chunks, openai_key)
            await add_chunks(user_id, mid, "meeting", result["title"], "", chunks, embeddings)
        except Exception as e:
            logger.warning(f"Vectorize failed: {e}")

        return JSONResponse(content={
            "id": mid, "title": result["title"],
            "summary": result["summary"], "action_items": result["action_items"],
        })
    except Exception as e:
        logger.error(f"Summary error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/meetings")
async def list_meetings(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_meetings(user_id))


@app.get("/api/meetings/search")
async def search_meetings_endpoint(request: Request, q: str = ""):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    if not q.strip():
        return JSONResponse(content=await get_meetings(user_id))
    from embeddings import embed_query
    from vectorstore import search as vector_search
    openai_key = await get_decrypted_key(user_id, "openai")
    query_emb = await embed_query(q, openai_key)
    results = await vector_search(user_id, query_emb, n_results=10, source_type="meeting")
    return JSONResponse(content=results)


@app.get("/api/meetings/{mid}")
async def get_meeting_detail(mid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    meeting = await get_meeting(mid, user_id)
    if not meeting:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return JSONResponse(content=meeting)


@app.delete("/api/meetings/{mid}")
async def remove_meeting(mid: int, request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    from vectorstore import delete_source
    await delete_source(user_id, mid, "meeting")
    await delete_meeting(mid, user_id)
    return JSONResponse(content={"ok": True})


@app.patch("/api/meetings/{mid}")
async def patch_meeting(mid: int, request: Request, data: dict):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    if "in_knowledge_base" in data:
        in_kb = bool(data["in_knowledge_base"])
        await update_meeting_kb_toggle(mid, user_id, in_kb)
        meeting = await get_meeting(mid, user_id)
        if meeting:
            from vectorstore import delete_source
            if in_kb and meeting.get("summary"):
                from embeddings import chunk_text, embed_texts
                from vectorstore import add_chunks
                openai_key = await get_decrypted_key(user_id, "openai")
                text = f"{meeting['title']}\n\n{meeting['summary']}\n\nAction Items: {json.dumps(meeting.get('action_items', []))}"
                chunks = chunk_text(text)
                embeddings = await embed_texts(chunks, openai_key)
                await add_chunks(user_id, mid, "meeting", meeting["title"],
                                 str(meeting.get("created_at", ""))[:10], chunks, embeddings)
            else:
                await delete_source(user_id, mid, "meeting")
    return JSONResponse(content={"ok": True})


# ---- Whisper STT (for WebRTC fallback) ----
HALLUCINATION_FILTER = {
    "", "you", "thank you", "thanks", "bye", "the end", "thanks for watching",
    "thank you for watching", "subscribe", "like and subscribe",
    "music", "applause", "laughter", "silence", "...",
    "i'm sorry", "i'm sorry.", "sorry",
}

# Patterns that indicate Whisper hallucination (repeated phrases)
import re
HALLUCINATION_REPEAT_PATTERN = re.compile(r'(.{5,}?)\1{3,}', re.IGNORECASE)


def transcribe_audio_sync(audio_bytes: bytes, meeting_mode: bool = False) -> str:
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        # Meeting mode: more lenient thresholds for lower quality tab audio
        if meeting_mode:
            segments, info = whisper_model.transcribe(
                tmp_path, beam_size=3, best_of=2, temperature=0,
                language="en", condition_on_previous_text=False,
                no_speech_threshold=0.8, log_prob_threshold=-1.5,
            )
        else:
            segments, info = whisper_model.transcribe(
                tmp_path, beam_size=1, best_of=1, temperature=0,
                language="en", condition_on_previous_text=False,
                no_speech_threshold=0.5, log_prob_threshold=-0.8,
            )
        text = " ".join(s.text for s in segments).strip()
        text_lower = text.lower().strip(".!?, ")
        if text_lower in HALLUCINATION_FILTER or len(text) < 3:
            logger.info(f"Filtered hallucination: {text[:50]}")
            return ""
        # Filter repetitive hallucinations like "I'm sorry, I'm sorry, I'm sorry..."
        if HALLUCINATION_REPEAT_PATTERN.search(text):
            logger.info(f"Filtered repeated hallucination: {text[:50]}")
            return ""
        logger.info(f"Transcribed: {text[:100]}")
        return text
    finally:
        os.unlink(tmp_path)


# ---- LLM Streaming (cancellable) ----
async def stream_llm(messages: list[dict], ws: WebSocket, model: str = None, cancel_event: asyncio.Event = None, voice: str = None, llm_client: AsyncOpenAI = None, tts_client: AsyncOpenAI = None, user_keys: dict = None) -> str:
    """Stream LLM + TTS with minimal latency. Routes to correct provider."""
    use_model = model or OLLAMA_MODEL
    provider = detect_provider(use_model)
    user_keys = user_keys or {}

    if provider == "anthropic":
        return await _stream_anthropic(messages, ws, use_model, cancel_event, voice, user_keys.get("anthropic"), tts_client)
    elif provider == "groq":
        groq_client = AsyncOpenAI(api_key=user_keys.get("groq"), base_url="https://api.groq.com/openai/v1")
        return await _stream_openai(messages, ws, use_model, cancel_event, voice, groq_client, tts_client)
    elif provider == "google":
        gemini_client = AsyncOpenAI(api_key=user_keys.get("google"), base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
        return await _stream_openai(messages, ws, use_model, cancel_event, voice, gemini_client, tts_client)
    elif provider == "openai":
        active_client = llm_client or openai_client
        if active_client:
            return await _stream_openai(messages, ws, use_model, cancel_event, voice, active_client, tts_client)

    return await _stream_ollama(messages, ws, use_model, cancel_event, voice, tts_client)


OPENAI_TTS_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


async def _send_tts(ws, text, voice=None, openai_client_override=None):
    """Generate audio: OpenAI TTS > edge-tts > browser TTS."""
    cleaned = clean_for_speech(text)
    if not cleaned or len(cleaned) < 2:
        return
    tts_voice = voice or TTS_VOICE

    # Try OpenAI TTS first if user has a key (natural, fast)
    if openai_client_override and tts_voice in OPENAI_TTS_VOICES:
        try:
            response = await openai_client_override.audio.speech.create(
                model="tts-1", voice=tts_voice, input=cleaned,
                response_format="mp3", speed=1.0,
            )
            audio_data = response.content
            if audio_data:
                b64 = base64.b64encode(audio_data).decode()
                await ws.send_json({"type": "tts_audio", "audio": b64})
                return
        except Exception as e:
            logger.warning(f"OpenAI TTS failed: {e}")

    # Try edge-tts (free neural voices)
    try:
        comm = edge_tts.Communicate(cleaned, tts_voice)
        audio_data = b""
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        if audio_data:
            b64 = base64.b64encode(audio_data).decode()
            await ws.send_json({"type": "tts_audio", "audio": b64})
            return
    except Exception as e:
        logger.warning(f"Edge-TTS failed, falling back to browser: {e}")

    # Fallback to browser TTS
    await ws.send_json({"type": "tts_chunk", "text": cleaned})


async def _stream_openai(messages, ws, model, cancel_event, voice, client: AsyncOpenAI, tts_client=None):
    """Stream from OpenAI API — ~200ms first token."""
    full_response = ""
    tts_buffer = ""
    chunk_count = 0
    sentence_enders = {'.', '!', '?', '\n'}
    in_code_block = False

    try:
        # Signal start of streaming
        await ws.send_json({"type": "stream_start"})

        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True,
            max_tokens=500, temperature=0.7,
        )
        async for chunk in stream:
            if cancel_event and cancel_event.is_set():
                break
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta or not delta.content:
                continue

            token = delta.content
            full_response += token

            # Send each token for typing animation
            await ws.send_json({"type": "text_delta", "delta": token})

            # Track code blocks for TTS (don't read code)
            if '```' in token:
                in_code_block = not in_code_block
                if in_code_block:
                    # Flush TTS buffer before code
                    if tts_buffer.strip():
                        await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
                        chunk_count += 1
                    tts_buffer = ""
                else:
                    # Code block ended, add spoken hint
                    tts_buffer = " I've included the code in the chat. "
                continue

            if in_code_block:
                continue  # Don't add code to TTS buffer

            tts_buffer += token

            if "[WAIT]" in tts_buffer:
                parts = tts_buffer.split("[WAIT]")
                before = parts[0].strip()
                if before:
                    await _send_tts(ws, before, voice, tts_client)
                    chunk_count += 1
                await ws.send_json({"type": "tts_done"})
                tts_buffer = parts[1] if len(parts) > 1 else ""
                continue

            min_len = 3 if chunk_count == 0 else 5
            last_break = -1
            for i, ch in enumerate(tts_buffer):
                if ch in sentence_enders and i >= min_len:
                    last_break = i
                    break
                elif ch == ',' and i > 12:
                    last_break = i
                    break

            if last_break >= min_len:
                c = tts_buffer[:last_break + 1].strip()
                tts_buffer = tts_buffer[last_break + 1:]
                if c:
                    await _send_tts(ws, c, voice, tts_client)
                    chunk_count += 1

        # Flush remaining TTS buffer
        if tts_buffer.strip() and not (cancel_event and cancel_event.is_set()):
            await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
        await ws.send_json({"type": "stream_end"})
        await ws.send_json({"type": "tts_done"})
    except Exception as e:
        logger.error(f"OpenAI stream error: {e}")
        await ws.send_json({"type": "error", "message": str(e)})

    return full_response.strip()


async def _stream_ollama(messages, ws, model, cancel_event, voice, tts_client=None):
    """Stream from local Ollama with typing animation."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"num_predict": 500, "temperature": 0.7},
    }

    full_response = ""
    tts_buffer = ""
    chunk_count = 0
    sentence_enders = {'.', '!', '?', '\n'}
    in_code_block = False

    await ws.send_json({"type": "stream_start"})

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if cancel_event and cancel_event.is_set():
                    logger.info("LLM stream cancelled by user")
                    break

                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("done"):
                    if tts_buffer.strip():
                        await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
                    await ws.send_json({"type": "stream_end"})
                    await ws.send_json({"type": "tts_done"})
                    break

                token = data.get("message", {}).get("content", "")
                if not token:
                    continue

                full_response += token
                await ws.send_json({"type": "text_delta", "delta": token})

                # Track code blocks for TTS
                if '```' in token:
                    in_code_block = not in_code_block
                    if in_code_block:
                        if tts_buffer.strip():
                            await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
                            chunk_count += 1
                        tts_buffer = ""
                    else:
                        tts_buffer = " I've included the code in the chat. "
                    continue

                if in_code_block:
                    continue

                tts_buffer += token

                if "[WAIT]" in tts_buffer:
                    parts = tts_buffer.split("[WAIT]")
                    before = parts[0].strip()
                    if before:
                        await _send_tts(ws, before, voice, tts_client)
                        chunk_count += 1
                    await ws.send_json({"type": "tts_done"})
                    tts_buffer = parts[1] if len(parts) > 1 else ""
                    continue

                min_len = 3 if chunk_count == 0 else 5
                last_break = -1
                for i, ch in enumerate(tts_buffer):
                    if ch in sentence_enders and i >= min_len:
                        last_break = i
                        break
                    elif ch == ',' and i > 12:
                        last_break = i
                        break

                if last_break >= min_len:
                    chunk = tts_buffer[:last_break + 1].strip()
                    tts_buffer = tts_buffer[last_break + 1:]
                    if chunk:
                        await _send_tts(ws, chunk, voice, tts_client)
                        chunk_count += 1

    return full_response.strip()


async def _stream_anthropic(messages, ws, model, cancel_event, voice, api_key, tts_client=None):
    """Stream from Anthropic Claude API."""
    import anthropic

    if not api_key:
        await ws.send_json({"type": "error", "message": "No Anthropic API key configured"})
        return ""

    system_prompt = ""
    chat_msgs = []
    for m in messages:
        if m["role"] == "system":
            system_prompt += m["content"] + "\n"
        else:
            chat_msgs.append({"role": m["role"], "content": m["content"]})

    full_response = ""
    tts_buffer = ""
    chunk_count = 0
    sentence_enders = {'.', '!', '?', '\n'}
    in_code_block = False

    await ws.send_json({"type": "stream_start"})

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model=model, max_tokens=500, system=system_prompt.strip(), messages=chat_msgs,
        ) as stream:
            async for text in stream.text_stream:
                if cancel_event and cancel_event.is_set():
                    break

                full_response += text
                await ws.send_json({"type": "text_delta", "delta": text})

                if '```' in text:
                    in_code_block = not in_code_block
                    if in_code_block:
                        if tts_buffer.strip():
                            await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
                            chunk_count += 1
                        tts_buffer = ""
                    else:
                        tts_buffer = " I've included the code in the chat. "
                    continue

                if in_code_block:
                    continue

                tts_buffer += text

                if "[WAIT]" in tts_buffer:
                    parts = tts_buffer.split("[WAIT]")
                    before = parts[0].strip()
                    if before:
                        await _send_tts(ws, before, voice, tts_client)
                        chunk_count += 1
                    await ws.send_json({"type": "tts_done"})
                    tts_buffer = parts[1] if len(parts) > 1 else ""
                    continue

                min_len = 3 if chunk_count == 0 else 5
                last_break = -1
                for i, ch in enumerate(tts_buffer):
                    if ch in sentence_enders and i >= min_len:
                        last_break = i
                        break
                    elif ch == ',' and i > 12:
                        last_break = i
                        break

                if last_break >= min_len:
                    c = tts_buffer[:last_break + 1].strip()
                    tts_buffer = tts_buffer[last_break + 1:]
                    if c:
                        await _send_tts(ws, c, voice, tts_client)
                        chunk_count += 1

        if tts_buffer.strip() and not (cancel_event and cancel_event.is_set()):
            await _send_tts(ws, tts_buffer.strip(), voice, tts_client)
        await ws.send_json({"type": "stream_end"})
        await ws.send_json({"type": "tts_done"})
    except Exception as e:
        logger.error(f"Anthropic stream error: {e}")
        await ws.send_json({"type": "error", "message": str(e)})

    return full_response.strip()


# ---- Meeting Extension WebSocket ----
@app.websocket("/ws-meeting")
async def meeting_ws_endpoint(ws: WebSocket):
    """Dedicated WebSocket for the Chrome extension meeting recorder."""
    await ws.accept()
    user_id = await get_user_id_from_ws(ws)
    if not user_id:
        await ws.send_json({"type": "error", "message": "Not authenticated"})
        await ws.close()
        return

    meeting_transcript_chunks = []
    meeting_start_time = 0
    meeting_active = False

    # Get user keys for summarization
    user_keys = {}
    for prov in ["openai", "anthropic", "groq", "google"]:
        k = await get_decrypted_key(user_id, prov)
        if k:
            user_keys[prov] = k

    logger.info(f"Meeting extension connected for user {user_id}")

    try:
        while True:
            message = await ws.receive()

            # Binary data = audio chunk
            if "bytes" in message:
                data = message["bytes"]
                if data and meeting_active and len(data) > 500:
                    logger.info(f"Meeting ext: received {len(data)} bytes")
                    text = await asyncio.to_thread(transcribe_audio_sync, data, True)
                    if text:
                        meeting_transcript_chunks.append(text)
                        await ws.send_json({
                            "type": "meeting_transcript",
                            "text": text,
                            "chunk_index": len(meeting_transcript_chunks) - 1,
                        })
                continue

            if "text" not in message:
                continue

            try:
                msg = json.loads(message["text"])
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "meeting_start":
                meeting_transcript_chunks = []
                meeting_start_time = asyncio.get_event_loop().time()
                meeting_active = True
                logger.info("Meeting extension: recording started")
                await ws.send_json({"type": "meeting_started"})

            elif msg.get("type") == "meeting_stop":
                meeting_active = False
                full_transcript = "\n".join(meeting_transcript_chunks)
                duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                logger.info(f"Meeting extension: stopped. {duration}s, {len(meeting_transcript_chunks)} chunks")
                await ws.send_json({"type": "meeting_stopped", "transcript": full_transcript, "duration": duration})

            elif msg.get("type") == "meeting_summarize":
                summary_model = msg.get("model", "gpt-4o-mini")
                full_transcript = "\n".join(meeting_transcript_chunks)
                duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                if not full_transcript.strip():
                    await ws.send_json({"type": "meeting_error", "message": "No transcript to summarize"})
                    continue
                try:
                    result = await summarize_meeting(full_transcript, summary_model, user_keys)
                    mid = await create_meeting(user_id, result["title"], full_transcript, duration)
                    await update_meeting_summary(mid, result["title"], "\n".join(result["summary"]),
                                                 result["action_items"], summary_model)
                    await ws.send_json({
                        "type": "meeting_summary", "id": mid, "title": result["title"],
                        "summary": result["summary"], "action_items": result["action_items"],
                    })
                except Exception as e:
                    logger.error(f"Meeting summary error: {e}")
                    await ws.send_json({"type": "meeting_error", "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Meeting extension disconnected")
    except Exception as e:
        logger.error(f"Meeting WS error: {e}")


# ---- WebSocket ----
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    user_id = await get_user_id_from_ws(ws)
    if not user_id:
        await ws.send_json({"type": "error", "message": "Not authenticated"})
        await ws.close()
        return

    # Load all user API keys for multi-provider support
    user_openai_key = await get_decrypted_key(user_id, "openai")
    user_openai_client = AsyncOpenAI(api_key=user_openai_key) if user_openai_key else openai_client
    user_keys = {}
    for prov in ["openai", "anthropic", "groq", "google"]:
        k = await get_decrypted_key(user_id, prov)
        if k:
            user_keys[prov] = k

    conversation_id = None
    current_model = OPENAI_MODEL if (user_openai_client is not None) else OLLAMA_MODEL
    current_voice = TTS_VOICE
    current_persona_prompt = DEFAULT_SYSTEM_PROMPT
    chat_history = [{"role": "system", "content": current_persona_prompt}]
    greeted = False
    llm_task = None
    cancel_event = asyncio.Event()
    meeting_active = False
    meeting_transcript_chunks = []
    meeting_start_time = 0
    meeting_bot_stop = None
    meeting_bot_task = None

    async def cancel_llm():
        """Cancel any running LLM stream and clean up chat history."""
        nonlocal llm_task
        cancel_event.set()
        if llm_task and not llm_task.done():
            llm_task.cancel()
            try:
                await llm_task
            except (asyncio.CancelledError, Exception):
                pass
        llm_task = None
        # Remove any trailing user message that didn't get a response
        # (the interrupted topic — don't let it pollute context)
        while chat_history and chat_history[-1]["role"] == "user":
            chat_history.pop()
        cancel_event.clear()

    try:
        while True:
            message = await ws.receive()
            user_text = None

            if "text" not in message:
                data = message.get("bytes", b"")
                if not data:
                    continue
                logger.info(f"Received {len(data)} bytes of audio (WebRTC fallback)")

                if data and meeting_active:
                    chunk_text_result = await asyncio.to_thread(transcribe_audio_sync, data, True)
                    if chunk_text_result:
                        meeting_transcript_chunks.append(chunk_text_result)
                        await ws.send_json({
                            "type": "meeting_transcript",
                            "text": chunk_text_result,
                            "chunk_index": len(meeting_transcript_chunks) - 1,
                        })
                    continue

                # Cancel any running LLM stream — user is interrupting
                await cancel_llm()
                user_text = await asyncio.to_thread(transcribe_audio_sync, data)
                if not user_text:
                    await ws.send_json({"type": "ready"})
                    continue
                await ws.send_json({"type": "transcript", "role": "user", "content": user_text})
            else:
                raw = message["text"]

                if raw == "start" and not greeted:
                    greeted = True
                    logger.info("Sending AI greeting...")
                    conversation_id = await create_conversation(user_id, "New conversation")
                    await ws.send_json({"type": "conversation_id", "id": conversation_id})
                    await add_message(conversation_id, "assistant", GREETING_TEXT)
                    chat_history.append({"role": "assistant", "content": GREETING_TEXT})
                    # Greeting with edge-tts natural voice
                    await _send_tts(ws, GREETING_TEXT, current_voice, user_openai_client)
                    await ws.send_json({"type": "tts_done"})
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "set_model":
                    current_model = msg["model"]
                    logger.info(f"Switched model to: {current_model}")
                    await ws.send_json({"type": "model_changed", "model": current_model})
                    continue

                if msg.get("type") == "set_voice":
                    current_voice = msg["voice"]
                    logger.info(f"Switched voice to: {current_voice}")
                    await ws.send_json({"type": "voice_changed", "voice": current_voice})
                    continue

                if msg.get("type") == "set_persona":
                    persona = await get_persona(msg["id"])
                    if persona:
                        current_persona_prompt = persona["prompt"]
                        chat_history[0] = {"role": "system", "content": current_persona_prompt}
                        logger.info(f"Switched persona to: {persona['name']}")
                        await ws.send_json({"type": "persona_changed", "name": persona["name"]})
                    continue

                if msg.get("type") == "interrupt":
                    logger.info("User interrupt signal")
                    await cancel_llm()
                    await ws.send_json({"type": "ready"})
                    continue

                # Load past conversation into AI memory (with smart summarization)
                if msg.get("type") == "load_conversation":
                    await cancel_llm()
                    cid = msg["id"]
                    conversation_id = cid
                    greeted = True
                    past_msgs = await get_conversation_messages(cid)
                    chat_history = [{"role": "system", "content": current_persona_prompt}]

                    RECENT_COUNT = 10
                    if len(past_msgs) > RECENT_COUNT:
                        # Summarize older messages, keep recent ones in full
                        older = past_msgs[:-RECENT_COUNT]
                        recent = past_msgs[-RECENT_COUNT:]

                        # Build a quick summary of the older conversation
                        older_text = " | ".join(
                            f"{m['role']}: {m['content'][:80]}" for m in older
                        )
                        summary = f"Summary of earlier conversation: {older_text}"
                        chat_history.append({"role": "system", "content": summary})

                        # Also ask the LLM for a proper summary (async, non-blocking)
                        try:
                            summary_msgs = [
                                {"role": "system", "content": "Summarize this conversation in 2-3 sentences. Be concise."},
                                {"role": "user", "content": older_text[:1500]}
                            ]
                            is_openai_model = any(current_model.startswith(p) for p in OPENAI_MODEL_PREFIXES)
                            if is_openai_model and user_openai_client:
                                resp = await user_openai_client.chat.completions.create(
                                    model=current_model, messages=summary_msgs,
                                    stream=False, max_tokens=100,
                                )
                                ai_summary = resp.choices[0].message.content or ""
                            else:
                                async with httpx.AsyncClient(timeout=30.0) as sc:
                                    sr = await sc.post(f"{OLLAMA_URL}/api/chat", json={
                                        "model": current_model, "messages": summary_msgs, "stream": False,
                                        "options": {"num_predict": 100}
                                    })
                                    sr.raise_for_status()
                                    ai_summary = sr.json()["message"]["content"]
                            if ai_summary:
                                chat_history[1] = {"role": "system", "content": f"Earlier conversation summary: {ai_summary}"}
                                logger.info(f"Generated summary: {ai_summary[:100]}")
                        except Exception as e:
                            logger.error(f"Summary generation error: {e}")

                        for m in recent:
                            chat_history.append({"role": m["role"], "content": m["content"]})
                    else:
                        for m in past_msgs:
                            chat_history.append({"role": m["role"], "content": m["content"]})

                    logger.info(f"Loaded conversation {cid}: {len(past_msgs)} msgs, {len(chat_history)} in context")
                    await ws.send_json({"type": "ready"})
                    continue

                if msg.get("type") == "meeting_start":
                    meeting_transcript_chunks = []
                    meeting_start_time = asyncio.get_event_loop().time()
                    meeting_active = True
                    logger.info("Meeting recording started (tab capture mode)")
                    await ws.send_json({"type": "meeting_started"})
                    continue

                if msg.get("type") == "meeting_join":
                    meet_url = msg.get("url", "").strip()
                    if not meet_url or "meet.google.com" not in meet_url:
                        await ws.send_json({"type": "meeting_error", "message": "Invalid Google Meet URL"})
                        continue
                    meeting_transcript_chunks = []
                    meeting_start_time = asyncio.get_event_loop().time()
                    meeting_active = True
                    meeting_bot_stop = asyncio.Event()
                    logger.info(f"Meeting bot joining: {meet_url}")
                    await ws.send_json({"type": "meeting_started", "mode": "bot"})

                    # Audio chunk callback — transcribe and send to client
                    async def on_meeting_audio(audio_bytes):
                        if not meeting_active:
                            return
                        text = await asyncio.to_thread(transcribe_audio_sync, audio_bytes, True)
                        if text:
                            meeting_transcript_chunks.append(text)
                            await ws.send_json({
                                "type": "meeting_transcript",
                                "text": text,
                                "chunk_index": len(meeting_transcript_chunks) - 1,
                            })

                    # Run bot in background
                    from meet_bot import join_meet
                    async def run_meet_bot():
                        nonlocal meeting_active
                        try:
                            await join_meet(
                                meet_url=meet_url,
                                bot_name="AI Recorder",
                                on_audio_chunk=on_meeting_audio,
                                stop_event=meeting_bot_stop,
                            )
                        except Exception as e:
                            logger.error(f"Meet bot error: {e}")
                            try:
                                await ws.send_json({"type": "meeting_error", "message": f"Bot error: {str(e)}"})
                            except Exception:
                                pass
                        finally:
                            if meeting_active:
                                meeting_active = False
                                try:
                                    await ws.send_json({"type": "meeting_stopped",
                                        "transcript": "\n".join(meeting_transcript_chunks),
                                        "duration": int(asyncio.get_event_loop().time() - meeting_start_time)})
                                except Exception:
                                    pass

                    meeting_bot_task = asyncio.create_task(run_meet_bot())
                    continue

                if msg.get("type") == "meeting_stop":
                    meeting_active = False
                    # Stop the bot if running
                    if 'meeting_bot_stop' in dir() and meeting_bot_stop:
                        meeting_bot_stop.set()
                    full_transcript = "\n".join(meeting_transcript_chunks)
                    duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                    logger.info(f"Meeting stopped: {duration}s, {len(meeting_transcript_chunks)} chunks")
                    await ws.send_json({"type": "meeting_stopped", "transcript": full_transcript, "duration": duration})
                    continue

                if msg.get("type") == "meeting_summarize":
                    summary_model = msg.get("model", current_model)
                    full_transcript = "\n".join(meeting_transcript_chunks)
                    duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                    if not full_transcript.strip():
                        await ws.send_json({"type": "meeting_error", "message": "No transcript to summarize. Make sure audio was captured."})
                        continue
                    logger.info(f"Summarizing {len(full_transcript)} chars with {summary_model}")
                    try:
                        result = await summarize_meeting(full_transcript, summary_model, user_keys)
                        mid = await create_meeting(user_id, result["title"], full_transcript, duration)
                        await update_meeting_summary(mid, result["title"], "\n".join(result["summary"]),
                                                     result["action_items"], summary_model)
                        # Auto-vectorize
                        from embeddings import chunk_text, embed_texts
                        from vectorstore import add_chunks
                        openai_key = user_keys.get("openai")
                        text = f"{result['title']}\n\n{chr(10).join(result['summary'])}\n\nAction Items: {json.dumps(result['action_items'])}"
                        chunks = chunk_text(text)
                        embeddings = await embed_texts(chunks, openai_key)
                        await add_chunks(user_id, mid, "meeting", result["title"], "", chunks, embeddings)
                        await ws.send_json({
                            "type": "meeting_summary", "id": mid, "title": result["title"],
                            "summary": result["summary"], "action_items": result["action_items"],
                        })
                    except Exception as e:
                        logger.error(f"Meeting summary error: {e}")
                        await ws.send_json({"type": "meeting_error", "message": str(e)})
                    continue

                if msg.get("type") != "text":
                    continue

                user_text = msg.get("content", "").strip()
                if not user_text:
                    continue

                # Cancel any running LLM stream — user is speaking
                await cancel_llm()

            logger.info(f"User said: {user_text}")

            # Create conversation if needed
            is_first = False
            if conversation_id is None:
                is_first = True
                conversation_id = await create_conversation(user_id, "New conversation")
                await ws.send_json({"type": "conversation_id", "id": conversation_id})

            await add_message(conversation_id, "user", user_text)

            # Search knowledge base for context
            kb_results = await search_knowledge(user_id, user_text, openai_key=user_keys.get("openai"))
            if kb_results:
                kb_context = "\n\n".join(f"[{r['title']}]: {r['content'][:500]}" for r in kb_results)
                kb_msg = {"role": "system", "content": f"Relevant knowledge:\n{kb_context}\n\nUse this info if relevant to answer the user."}
                messages_with_kb = [chat_history[0], kb_msg] + chat_history[1:] + [{"role": "user", "content": user_text}]
            else:
                messages_with_kb = chat_history + [{"role": "user", "content": user_text}]

            chat_history.append({"role": "user", "content": user_text})

            # Set title immediately from first user message (fast, no LLM call)
            if is_first:
                quick_title = user_text[:45].strip()
                if len(user_text) > 45:
                    quick_title += "..."
                await update_conversation_title(conversation_id, quick_title)
                await ws.send_json({"type": "title_updated", "id": conversation_id, "title": quick_title})

            # Run LLM as background task so we can still receive user messages (interrupts)
            _ut = user_text  # capture for closure
            _cid = conversation_id
            _client = user_openai_client  # capture per-user client for closure

            async def run_llm(msgs, conv_id, captured_text, llm_client, tts_cl, keys):
                try:
                    ai_text = await stream_llm(msgs, ws, current_model, cancel_event, current_voice, llm_client, tts_cl, keys)
                    if ai_text and not cancel_event.is_set():
                        logger.info(f"AI response: {ai_text}")
                        chat_history.append({"role": "assistant", "content": ai_text})
                        # Don't send transcript — frontend built it from text_delta stream
                        await add_message(conv_id, "assistant", ai_text)
                except asyncio.CancelledError:
                    logger.info("LLM task cancelled")
                except Exception as e:
                    logger.error(f"LLM error: {e}")
                    try:
                        await ws.send_json({"type": "error", "message": f"LLM error: {str(e)}"})
                        await ws.send_json({"type": "ready"})
                    except Exception:
                        pass

            llm_task = asyncio.create_task(run_llm(messages_with_kb, _cid, _ut, _client, _client, user_keys))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
