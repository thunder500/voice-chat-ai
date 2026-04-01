# Meeting Recorder + ChromaDB Vector Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ChromaDB vector search to the knowledge base, then build a meeting recorder with live transcription, auto-summary, and semantic search.

**Architecture:** Phase 1 adds a ChromaDB container and embedding module that upgrades the existing keyword-based knowledge search to semantic vector search. Phase 2 adds meeting recording via browser tab audio capture, chunked Whisper transcription over WebSocket, LLM-powered summarization, and a meeting history UI with semantic search.

**Tech Stack:** ChromaDB, sentence-transformers (all-MiniLM-L6-v2), OpenAI embeddings (fallback), MediaRecorder API, getDisplayMedia API, FastAPI WebSocket, faster-whisper

---

## File Structure

### New Files
- `backend/embeddings.py` — embedding module (OpenAI + sentence-transformers fallback), text chunking
- `backend/vectorstore.py` — ChromaDB client wrapper (connect, add, search, delete per user collection)
- `backend/models/meetings.py` — meetings CRUD (create, get, list, delete, update, search)
- `backend/meeting_summarizer.py` — LLM-powered meeting summary generation

### Modified Files
- `docker-compose.yml` — add chromadb container + volume
- `backend/requirements.txt` — add chromadb-client, sentence-transformers
- `backend/Dockerfile` — pre-download sentence-transformers model
- `backend/schema.sql` — add meetings table
- `backend/models/__init__.py` — re-export meeting model functions
- `backend/models/knowledge.py` — upgrade search_knowledge to use vector search
- `backend/app.py` — add meeting API endpoints, meeting WebSocket protocol, wire vector search
- `backend/templates/index.html` — meeting recorder UI (floating modal, meeting history tab, summary screen)

---

## PHASE 1: ChromaDB + Vector Search

---

### Task 1: Add ChromaDB to Docker + Dependencies

**Files:**
- Modify: `docker-compose.yml`
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile`

- [ ] **Step 1: Add ChromaDB service to docker-compose.yml**

Add before the `volumes:` section:

```yaml
  chromadb:
    image: chromadb/chroma:latest
    container_name: voice-chat-chromadb
    volumes:
      - chroma_data:/chroma/chroma
    ports:
      - "8100:8000"
    restart: unless-stopped
```

Add `chroma_data` to volumes:

```yaml
volumes:
  chroma_data:
    name: voice-chat-chroma-data
```

Add to app service `depends_on`:

```yaml
      chromadb:
        condition: service_started
```

Add env var to app service:

```yaml
      - CHROMA_URL=http://chromadb:8000
```

- [ ] **Step 2: Update requirements.txt**

Add these lines:

```
chromadb-client>=0.5.0
sentence-transformers>=3.0.0
```

- [ ] **Step 3: Update Dockerfile to pre-download embedding model**

Add after the whisper model download line:

```dockerfile
# Pre-download sentence-transformers embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

- [ ] **Step 4: Verify ChromaDB starts**

```bash
docker compose up -d chromadb
curl http://localhost:8100/api/v1/heartbeat
```

Expected: `{"nanosecond heartbeat":...}`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml backend/requirements.txt backend/Dockerfile
git commit -m "infra: add ChromaDB container and embedding dependencies"
```

---

### Task 2: Embedding Module

**Files:**
- Create: `backend/embeddings.py`

- [ ] **Step 1: Create embeddings.py**

```python
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_local_model = None


def _get_local_model():
    """Lazy-load sentence-transformers model."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded local embedding model: all-MiniLM-L6-v2")
    return _local_model


async def embed_texts(texts: list[str], openai_key: Optional[str] = None) -> list[list[float]]:
    """Embed a list of texts. Uses OpenAI if key provided, else local model."""
    if openai_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            resp = await client.embeddings.create(
                model="text-embedding-3-small",
                input=texts,
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"OpenAI embeddings failed, using local: {e}")

    # Fallback: local sentence-transformers
    import asyncio
    model = _get_local_model()
    embeddings = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


async def embed_query(query: str, openai_key: Optional[str] = None) -> list[float]:
    """Embed a single query string."""
    results = await embed_texts([query], openai_key)
    return results[0]


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size characters."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # Try to break at a sentence boundary
        if end < len(text):
            # Look for sentence enders near the boundary
            for i in range(min(end, len(text) - 1), max(start + chunk_size // 2, start), -1):
                if text[i] in '.!?\n':
                    end = i + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks
```

- [ ] **Step 2: Commit**

```bash
git add backend/embeddings.py
git commit -m "feat: add embedding module with OpenAI + local fallback"
```

---

### Task 3: Vector Store Module

**Files:**
- Create: `backend/vectorstore.py`

- [ ] **Step 1: Create vectorstore.py**

```python
import os
import logging
from typing import Optional

import chromadb

logger = logging.getLogger(__name__)

CHROMA_URL = os.environ.get("CHROMA_URL", "http://chromadb:8000")

_client: Optional[chromadb.HttpClient] = None


def _get_client() -> chromadb.HttpClient:
    """Get or create ChromaDB HTTP client."""
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=CHROMA_URL.replace("http://", "").split(":")[0],
                                       port=int(CHROMA_URL.split(":")[-1]))
        logger.info(f"Connected to ChromaDB at {CHROMA_URL}")
    return _client


def _collection_name(user_id: str) -> str:
    """Generate collection name for a user."""
    return f"user_{user_id.replace('-', '_')}"


async def add_chunks(user_id: str, source_id: int, source_type: str, title: str,
                     date: str, chunks: list[str], embeddings: list[list[float]]):
    """Add text chunks with embeddings to user's collection."""
    import asyncio
    def _add():
        client = _get_client()
        collection = client.get_or_create_collection(
            name=_collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )
        ids = [f"{source_type}_{source_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {"source_id": source_id, "source_type": source_type,
             "title": title, "date": date, "chunk_index": i}
            for i in range(len(chunks))
        ]
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(chunks)} chunks to {_collection_name(user_id)} ({source_type}:{source_id})")

    await asyncio.to_thread(_add)


async def search(user_id: str, query_embedding: list[float], n_results: int = 5,
                 source_type: Optional[str] = None) -> list[dict]:
    """Search user's collection for similar chunks."""
    import asyncio
    def _search():
        client = _get_client()
        try:
            collection = client.get_collection(name=_collection_name(user_id))
        except Exception:
            return []

        where_filter = {"source_type": source_type} if source_type else None
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        items = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0
                items.append({
                    "content": doc,
                    "title": meta.get("title", ""),
                    "date": meta.get("date", ""),
                    "source_type": meta.get("source_type", ""),
                    "source_id": meta.get("source_id"),
                    "similarity": 1 - distance,  # cosine distance to similarity
                })
        return items

    return await asyncio.to_thread(_search)


async def delete_source(user_id: str, source_id: int, source_type: str):
    """Delete all chunks for a specific source from user's collection."""
    import asyncio
    def _delete():
        client = _get_client()
        try:
            collection = client.get_collection(name=_collection_name(user_id))
            collection.delete(where={"source_id": source_id, "source_type": source_type})
            logger.info(f"Deleted {source_type}:{source_id} from {_collection_name(user_id)}")
        except Exception as e:
            logger.warning(f"ChromaDB delete failed: {e}")

    await asyncio.to_thread(_delete)
```

- [ ] **Step 2: Commit**

```bash
git add backend/vectorstore.py
git commit -m "feat: add ChromaDB vector store wrapper"
```

---

### Task 4: Upgrade Knowledge Base to Vector Search

**Files:**
- Modify: `backend/models/knowledge.py`
- Modify: `backend/app.py`

- [ ] **Step 1: Update knowledge.py — add vectorize/devectorize functions**

Add these functions to the end of `backend/models/knowledge.py`:

```python
async def vectorize_knowledge(user_id: str, kid: int, title: str, content: str, openai_key: str = None):
    """Chunk and embed a knowledge entry into ChromaDB."""
    try:
        from embeddings import chunk_text, embed_texts
        from vectorstore import add_chunks
        from datetime import datetime

        chunks = chunk_text(content)
        embeddings = await embed_texts(chunks, openai_key)
        await add_chunks(
            user_id=user_id, source_id=kid, source_type="knowledge",
            title=title, date=datetime.now().isoformat()[:10], chunks=chunks, embeddings=embeddings,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to vectorize knowledge {kid}: {e}")


async def devectorize_knowledge(user_id: str, kid: int):
    """Remove a knowledge entry from ChromaDB."""
    try:
        from vectorstore import delete_source
        await delete_source(user_id, kid, "knowledge")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to devectorize knowledge {kid}: {e}")
```

- [ ] **Step 2: Update search_knowledge to use vector search with keyword fallback**

Replace the existing `search_knowledge` function in `backend/models/knowledge.py`:

```python
async def search_knowledge(user_id: str, query: str, limit: int = 5, openai_key: str = None) -> list[dict]:
    """Semantic search via ChromaDB, falls back to keyword search."""
    # Try vector search first
    try:
        from embeddings import embed_query
        from vectorstore import search
        query_emb = await embed_query(query, openai_key)
        results = await search(user_id, query_emb, n_results=limit)
        if results:
            return results
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Vector search failed, using keyword fallback: {e}")

    # Keyword fallback
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
            scored.append((score, _serialize_row(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]
```

- [ ] **Step 3: Update app.py — vectorize on knowledge upload, devectorize on delete**

In `backend/app.py`, update the `upload_knowledge` endpoint. After `kid = await add_knowledge(...)`, add:

```python
        # Vectorize into ChromaDB
        from models.knowledge import vectorize_knowledge
        user_openai_key = await get_decrypted_key(user_id, "openai")
        asyncio.create_task(vectorize_knowledge(user_id, kid, title or file.filename, text, user_openai_key))
```

Update the `remove_knowledge` endpoint. Before `await delete_knowledge(...)`, add:

```python
        from models.knowledge import devectorize_knowledge
        asyncio.create_task(devectorize_knowledge(user_id, kid))
```

Update `search_knowledge` calls in the WebSocket handler to pass `openai_key`:

Change:
```python
kb_results = await search_knowledge(user_id, user_text)
```
To:
```python
user_openai_key_for_search = user_keys.get("openai")
kb_results = await search_knowledge(user_id, user_text, openai_key=user_openai_key_for_search)
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/knowledge.py backend/app.py
git commit -m "feat: upgrade knowledge base to ChromaDB vector search with keyword fallback"
```

---

## PHASE 2: Meeting Recorder

---

### Task 5: Meetings Database Table + Model

**Files:**
- Modify: `backend/schema.sql`
- Create: `backend/models/meetings.py`
- Modify: `backend/models/__init__.py`

- [ ] **Step 1: Add meetings table to schema.sql**

Add before the indexes section:

```sql
CREATE TABLE IF NOT EXISTS meetings (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT DEFAULT 'Untitled Meeting',
    summary TEXT,
    action_items JSONB DEFAULT '[]',
    transcript TEXT,
    duration_seconds INTEGER,
    model_used VARCHAR(100),
    in_knowledge_base BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

Add index:

```sql
CREATE INDEX IF NOT EXISTS idx_meetings_user ON meetings(user_id);
```

- [ ] **Step 2: Create models/meetings.py**

```python
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
```

- [ ] **Step 3: Update models/__init__.py**

Add to `backend/models/__init__.py`:

```python
from models.meetings import (
    create_meeting, update_meeting_summary, get_meeting,
    get_meetings, delete_meeting, update_meeting_kb_toggle,
)
```

- [ ] **Step 4: Commit**

```bash
git add backend/schema.sql backend/models/meetings.py backend/models/__init__.py
git commit -m "feat: add meetings database table and model"
```

---

### Task 6: Meeting Summarizer

**Files:**
- Create: `backend/meeting_summarizer.py`

- [ ] **Step 1: Create meeting_summarizer.py**

```python
import json
import logging
import os

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")

SUMMARY_PROMPT = """You are analyzing a meeting transcript. Generate:
1. A concise title (under 60 chars)
2. A summary in 3-5 bullet points covering key decisions and discussions
3. A list of action items with who is responsible (if mentioned)

Format as JSON:
{
  "title": "...",
  "summary": ["bullet 1", "bullet 2"],
  "action_items": ["item 1", "item 2"]
}

Respond ONLY with valid JSON, no other text."""

# Provider detection (same as app.py)
OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
ANTHROPIC_MODEL_PREFIXES = ("claude-",)
GEMINI_MODEL_PREFIXES = ("gemini-",)
GROQ_MODELS = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"}


def _detect_provider(model: str) -> str:
    if any(model.startswith(p) for p in ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if any(model.startswith(p) for p in GEMINI_MODEL_PREFIXES):
        return "google"
    if any(model.startswith(p) for p in OPENAI_MODEL_PREFIXES):
        return "openai"
    if model in GROQ_MODELS:
        return "groq"
    return "ollama"


async def summarize_meeting(transcript: str, model: str, user_keys: dict) -> dict:
    """Generate meeting summary using the specified model. Returns {title, summary, action_items}."""
    provider = _detect_provider(model)
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": f"Transcript:\n\n{transcript[:8000]}"},
    ]

    raw_text = ""

    if provider == "anthropic":
        import anthropic
        key = user_keys.get("anthropic")
        if not key:
            raise ValueError("No Anthropic API key")
        client = anthropic.AsyncAnthropic(api_key=key)
        resp = await client.messages.create(
            model=model, max_tokens=1000,
            system=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": f"Transcript:\n\n{transcript[:8000]}"}],
        )
        raw_text = resp.content[0].text

    elif provider in ("openai", "groq", "google"):
        key = user_keys.get(provider)
        if not key:
            raise ValueError(f"No {provider} API key")
        base_url = None
        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "google":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        client = AsyncOpenAI(api_key=key, base_url=base_url)
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=1000, temperature=0.3,
        )
        raw_text = resp.choices[0].message.content

    else:  # ollama
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
                "model": model, "messages": messages, "stream": False,
                "options": {"temperature": 0.3},
            })
            resp.raise_for_status()
            raw_text = resp.json()["message"]["content"]

    # Parse JSON from response
    try:
        # Extract JSON if wrapped in markdown code block
        if "```" in raw_text:
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        result = json.loads(raw_text.strip())
        return {
            "title": result.get("title", "Untitled Meeting")[:60],
            "summary": result.get("summary", []),
            "action_items": result.get("action_items", []),
        }
    except json.JSONDecodeError:
        logger.error(f"Failed to parse summary JSON: {raw_text[:200]}")
        return {
            "title": "Untitled Meeting",
            "summary": [raw_text[:200]],
            "action_items": [],
        }
```

- [ ] **Step 2: Commit**

```bash
git add backend/meeting_summarizer.py
git commit -m "feat: add meeting summary generator with multi-provider support"
```

---

### Task 7: Meeting API Endpoints + WebSocket Protocol

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Add meeting imports to app.py**

Add to the imports from models:

```python
from models import (
    # ... existing imports ...
    create_meeting, update_meeting_summary, get_meeting,
    get_meetings, delete_meeting, update_meeting_kb_toggle,
)
from meeting_summarizer import summarize_meeting
```

- [ ] **Step 2: Add meeting REST endpoints**

Add after the personas endpoints in `backend/app.py`:

```python
# ---- Meetings API ----
@app.get("/api/meetings")
async def list_meetings(request: Request):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    return JSONResponse(content=await get_meetings(user_id))


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
    # Remove from ChromaDB
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
                from models.apikeys import get_decrypted_key
                openai_key = await get_decrypted_key(user_id, "openai")
                text = f"{meeting['title']}\n\n{meeting['summary']}\n\nAction Items: {json.dumps(meeting.get('action_items', []))}"
                chunks = chunk_text(text)
                embeddings = await embed_texts(chunks, openai_key)
                await add_chunks(user_id, mid, "meeting", meeting["title"],
                                 str(meeting.get("created_at", ""))[:10], chunks, embeddings)
            else:
                await delete_source(user_id, mid, "meeting")
    return JSONResponse(content={"ok": True})


@app.get("/api/meetings/search")
async def search_meetings(request: Request, q: str = ""):
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(content={"error": "Not authenticated"}, status_code=401)
    if not q.strip():
        return JSONResponse(content=await get_meetings(user_id))
    from embeddings import embed_query
    from vectorstore import search
    from models.apikeys import get_decrypted_key
    openai_key = await get_decrypted_key(user_id, "openai")
    query_emb = await embed_query(q, openai_key)
    results = await search(user_id, query_emb, n_results=10, source_type="meeting")
    return JSONResponse(content=results)
```

- [ ] **Step 3: Add meeting WebSocket protocol to the WebSocket handler**

Inside the WebSocket handler's message loop, add handlers for meeting messages:

```python
                if msg.get("type") == "meeting_start":
                    meeting_transcript_chunks = []
                    meeting_start_time = asyncio.get_event_loop().time()
                    meeting_active = True
                    logger.info("Meeting recording started")
                    await ws.send_json({"type": "meeting_started"})
                    continue

                if msg.get("type") == "meeting_stop":
                    meeting_active = False
                    full_transcript = "\n".join(meeting_transcript_chunks)
                    duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                    logger.info(f"Meeting stopped: {duration}s, {len(meeting_transcript_chunks)} chunks")
                    await ws.send_json({"type": "meeting_stopped", "transcript": full_transcript, "duration": duration})
                    continue

                if msg.get("type") == "meeting_summarize":
                    summary_model = msg.get("model", current_model)
                    full_transcript = "\n".join(meeting_transcript_chunks)
                    duration = int(asyncio.get_event_loop().time() - meeting_start_time)
                    try:
                        result = await summarize_meeting(full_transcript, summary_model, user_keys)
                        mid = await create_meeting(user_id, result["title"], full_transcript, duration)
                        await update_meeting_summary(mid, result["title"], "\n".join(result["summary"]),
                                                     result["action_items"], summary_model)
                        # Auto-vectorize if in_knowledge_base is on (default)
                        from embeddings import chunk_text, embed_texts
                        from vectorstore import add_chunks
                        openai_key = user_keys.get("openai")
                        text = f"{result['title']}\n\n{chr(10).join(result['summary'])}\n\nAction Items: {json.dumps(result['action_items'])}"
                        chunks = chunk_text(text)
                        embeddings = await embed_texts(chunks, openai_key)
                        await add_chunks(user_id, mid, "meeting", result["title"],
                                         "", chunks, embeddings)
                        await ws.send_json({
                            "type": "meeting_summary",
                            "id": mid, "title": result["title"],
                            "summary": result["summary"],
                            "action_items": result["action_items"],
                        })
                    except Exception as e:
                        logger.error(f"Meeting summary error: {e}")
                        await ws.send_json({"type": "meeting_error", "message": str(e)})
                    continue
```

Also add meeting binary audio handling — when the WebSocket receives binary data AND `meeting_active` is True, transcribe the chunk:

In the binary data handler section, add a check:

```python
                if data and meeting_active:
                    # Meeting audio chunk — transcribe and return
                    chunk_text_result = await asyncio.to_thread(transcribe_audio_sync, data)
                    if chunk_text_result:
                        meeting_transcript_chunks.append(chunk_text_result)
                        await ws.send_json({
                            "type": "meeting_transcript",
                            "text": chunk_text_result,
                            "chunk_index": len(meeting_transcript_chunks) - 1,
                        })
                    continue
```

Initialize meeting state variables at the top of the WebSocket handler:

```python
    meeting_active = False
    meeting_transcript_chunks = []
    meeting_start_time = 0
```

- [ ] **Step 4: Commit**

```bash
git add backend/app.py
git commit -m "feat: add meeting API endpoints and WebSocket protocol"
```

---

### Task 8: Meeting Recorder Frontend — Floating Modal

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: Add "Record Meeting" button to header**

Add in the header-right div:

```html
<button class="icon-btn" id="recordMeetingBtn" onclick="startMeetingRecording()">Record Meeting</button>
```

- [ ] **Step 2: Add floating meeting modal HTML**

Add before the `<!-- Controls -->` section:

```html
<!-- Meeting Recorder Modal -->
<div id="meetingModal" class="meeting-modal" style="display:none">
  <div class="meeting-header">
    <div class="meeting-status">
      <span class="rec-dot" id="recDot"></span>
      <span id="meetingTimer">00:00</span>
    </div>
    <div class="meeting-actions">
      <button class="meeting-btn" id="meetingPauseBtn" onclick="pauseMeeting()">Pause</button>
      <button class="meeting-btn stop" onclick="stopMeeting()">Stop</button>
      <button class="meeting-btn" onclick="minimizeMeeting()">_</button>
    </div>
  </div>
  <div class="meeting-transcript" id="meetingTranscript">
    <p class="meeting-placeholder">Waiting for audio...</p>
  </div>
  <!-- Summary Screen (shown after stop) -->
  <div id="meetingSummaryScreen" style="display:none">
    <h3>Meeting Summary</h3>
    <div class="meeting-model-select">
      <label>Summarize with:</label>
      <select id="meetingSummaryModel"></select>
      <button onclick="generateMeetingSummary()">Generate Summary</button>
    </div>
    <div id="meetingSummaryContent"></div>
    <div class="meeting-kb-toggle">
      <label><input type="checkbox" id="meetingKBToggle" checked> Save to Knowledge Base</label>
    </div>
    <button class="meeting-btn save" id="meetingSaveBtn" onclick="saveMeeting()">Save Meeting</button>
  </div>
</div>
```

- [ ] **Step 3: Add meeting CSS**

```css
.meeting-modal{position:fixed;bottom:20px;right:20px;width:400px;max-height:500px;background:var(--surface);border:1px solid var(--border);border-radius:12px;z-index:90;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,.4);overflow:hidden}
.meeting-modal.minimized{max-height:44px;overflow:hidden}
.meeting-header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg-elev)}
.meeting-status{display:flex;align-items:center;gap:8px}
.rec-dot{width:10px;height:10px;border-radius:50%;background:#ef4444;animation:pulse-rec 1s infinite}
@keyframes pulse-rec{0%,100%{opacity:1}50%{opacity:.3}}
.rec-dot.paused{background:#f59e0b;animation:none}
#meetingTimer{font-family:monospace;font-size:14px;color:var(--text2)}
.meeting-actions{display:flex;gap:6px}
.meeting-btn{background:var(--surface);border:1px solid var(--border);color:var(--text3);padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px}
.meeting-btn:hover{border-color:#8b5cf6;color:#8b5cf6}
.meeting-btn.stop{border-color:#ef4444;color:#ef4444}
.meeting-btn.stop:hover{background:#ef4444;color:#fff}
.meeting-btn.save{background:#8b5cf6;color:#fff;border:none;padding:8px 16px;width:100%;margin-top:8px;border-radius:8px}
.meeting-transcript{flex:1;overflow-y:auto;padding:12px;max-height:250px;font-size:13px;line-height:1.5;color:var(--text2)}
.meeting-placeholder{color:var(--text6);font-style:italic}
.meeting-model-select{display:flex;align-items:center;gap:8px;margin:12px;flex-wrap:wrap}
.meeting-model-select label{font-size:12px;color:var(--text4)}
.meeting-model-select select{background:var(--input-bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:6px;font-size:12px}
.meeting-model-select button{background:#8b5cf6;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}
#meetingSummaryContent{padding:12px;font-size:13px;line-height:1.5;max-height:200px;overflow-y:auto}
.meeting-kb-toggle{padding:0 12px;font-size:13px;color:var(--text3)}
#meetingSummaryScreen{padding:12px}
```

- [ ] **Step 4: Add meeting JavaScript**

```javascript
// ---- Meeting Recorder ----
let meetingStream = null;
let meetingRecorder = null;
let meetingInterval = null;
let meetingSeconds = 0;
let meetingPaused = false;

async function startMeetingRecording() {
    try {
        // Capture tab audio
        const displayStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
        // Capture mic
        const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // Combine both audio streams
        const audioCtx = new AudioContext();
        const dest = audioCtx.createMediaStreamDestination();
        const tabSource = audioCtx.createMediaStreamSource(displayStream);
        const micSource = audioCtx.createMediaStreamSource(micStream);
        tabSource.connect(dest);
        micSource.connect(dest);

        meetingStream = { display: displayStream, mic: micStream, ctx: audioCtx };

        // Start MediaRecorder with combined audio
        meetingRecorder = new MediaRecorder(dest.stream, { mimeType: 'audio/webm;codecs=opus' });
        meetingRecorder.ondataavailable = (e) => {
            if (e.data.size > 0 && ws && ws.readyState === 1) {
                e.data.arrayBuffer().then(buf => ws.send(buf));
            }
        };
        meetingRecorder.start(10000); // 10-second chunks

        // Tell backend
        ws.send(JSON.stringify({ type: 'meeting_start' }));

        // Show modal + start timer
        document.getElementById('meetingModal').style.display = 'flex';
        document.getElementById('meetingSummaryScreen').style.display = 'none';
        document.getElementById('meetingTranscript').innerHTML = '<p class="meeting-placeholder">Listening...</p>';
        meetingSeconds = 0;
        meetingInterval = setInterval(() => {
            if (!meetingPaused) {
                meetingSeconds++;
                const m = String(Math.floor(meetingSeconds / 60)).padStart(2, '0');
                const s = String(meetingSeconds % 60).padStart(2, '0');
                document.getElementById('meetingTimer').textContent = m + ':' + s;
            }
        }, 1000);

        // Stop when tab share ends
        displayStream.getVideoTracks()[0].onended = () => stopMeeting();

    } catch (e) {
        console.error('Meeting recording failed:', e);
        alert('Could not start recording. Make sure to share a browser tab with audio.');
    }
}

function pauseMeeting() {
    meetingPaused = !meetingPaused;
    document.getElementById('meetingPauseBtn').textContent = meetingPaused ? 'Resume' : 'Pause';
    document.getElementById('recDot').classList.toggle('paused', meetingPaused);
    if (meetingPaused) {
        meetingRecorder.pause();
    } else {
        meetingRecorder.resume();
    }
}

function stopMeeting() {
    if (meetingRecorder && meetingRecorder.state !== 'inactive') {
        meetingRecorder.stop();
    }
    if (meetingStream) {
        meetingStream.display.getTracks().forEach(t => t.stop());
        meetingStream.mic.getTracks().forEach(t => t.stop());
        meetingStream.ctx.close();
        meetingStream = null;
    }
    clearInterval(meetingInterval);
    ws.send(JSON.stringify({ type: 'meeting_stop' }));

    // Show summary screen
    document.getElementById('recDot').style.animation = 'none';
    document.getElementById('recDot').style.background = 'var(--text6)';
    document.getElementById('meetingSummaryScreen').style.display = 'block';

    // Populate model dropdown
    const sel = document.getElementById('meetingSummaryModel');
    const mainSel = document.getElementById('modelSelect');
    sel.innerHTML = mainSel.innerHTML;
    sel.value = mainSel.value;
}

function minimizeMeeting() {
    document.getElementById('meetingModal').classList.toggle('minimized');
}

function generateMeetingSummary() {
    const model = document.getElementById('meetingSummaryModel').value;
    document.getElementById('meetingSummaryContent').innerHTML = '<p style="color:var(--text5)">Generating summary...</p>';
    ws.send(JSON.stringify({ type: 'meeting_summarize', model: model }));
}

function saveMeeting() {
    document.getElementById('meetingModal').style.display = 'none';
    loadMeetings();
}

// Handle meeting WebSocket messages (add to ws.onmessage handler)
// Inside the existing ws.onmessage, add:
//   if (m.type === 'meeting_transcript') handleMeetingTranscript(m);
//   if (m.type === 'meeting_summary') handleMeetingSummary(m);
//   if (m.type === 'meeting_error') handleMeetingError(m);

function handleMeetingTranscript(m) {
    const el = document.getElementById('meetingTranscript');
    if (el.querySelector('.meeting-placeholder')) el.innerHTML = '';
    const p = document.createElement('p');
    p.textContent = m.text;
    p.style.borderBottom = '1px solid var(--border)';
    p.style.paddingBottom = '6px';
    p.style.marginBottom = '6px';
    el.appendChild(p);
    el.scrollTop = el.scrollHeight;
}

function handleMeetingSummary(m) {
    const html = `
        <h4 style="color:#8b5cf6;margin-bottom:8px">${m.title}</h4>
        <ul style="margin:0 0 12px 16px">${m.summary.map(s => '<li>' + s + '</li>').join('')}</ul>
        ${m.action_items.length ? '<h4 style="margin-bottom:6px">Action Items</h4><ul style="margin:0 0 0 16px">' + m.action_items.map(a => '<li>' + a + '</li>').join('') + '</ul>' : ''}
    `;
    document.getElementById('meetingSummaryContent').innerHTML = html;
    document.getElementById('meetingSaveBtn').style.display = 'block';
}

function handleMeetingError(m) {
    document.getElementById('meetingSummaryContent').innerHTML = '<p style="color:#ef4444">' + m.message + '</p>';
}
```

- [ ] **Step 5: Wire meeting messages into existing WebSocket onmessage handler**

Find the `ws.onmessage` handler and add these lines:

```javascript
if(m.type==='meeting_transcript') handleMeetingTranscript(m);
else if(m.type==='meeting_summary') handleMeetingSummary(m);
else if(m.type==='meeting_error') handleMeetingError(m);
else if(m.type==='meeting_started') console.log('Meeting recording started');
else if(m.type==='meeting_stopped') console.log('Meeting stopped');
```

- [ ] **Step 6: Commit**

```bash
git add backend/templates/index.html
git commit -m "feat: add meeting recorder floating modal with live transcript UI"
```

---

### Task 9: Meeting History UI — Sidebar Tab

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: Add Meetings tab to sidebar**

Find the sidebar HTML and add a tab switcher between Conversations and Meetings:

```html
<div class="sidebar-tabs">
  <button class="sidebar-tab active" onclick="switchSidebarTab('conversations',this)">Conversations</button>
  <button class="sidebar-tab" onclick="switchSidebarTab('meetings',this)">Meetings</button>
</div>
<div id="conversationsTab">
  <!-- existing conversation list -->
</div>
<div id="meetingsTab" style="display:none">
  <input type="text" class="search-input" placeholder="Search meetings..." oninput="searchMeetings(this.value)">
  <div id="meetingsList"></div>
</div>
```

- [ ] **Step 2: Add sidebar tab CSS**

```css
.sidebar-tabs{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:8px}
.sidebar-tab{flex:1;padding:8px;background:none;border:none;color:var(--text5);cursor:pointer;font-size:12px;border-bottom:2px solid transparent}
.sidebar-tab.active{color:#8b5cf6;border-bottom-color:#8b5cf6}
```

- [ ] **Step 3: Add meetings list JavaScript**

```javascript
function switchSidebarTab(tab, btn) {
    document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('conversationsTab').style.display = tab === 'conversations' ? 'block' : 'none';
    document.getElementById('meetingsTab').style.display = tab === 'meetings' ? 'block' : 'none';
    if (tab === 'meetings') loadMeetings();
}

async function loadMeetings() {
    try {
        const resp = await authFetch('/api/meetings');
        const meetings = await resp.json();
        document.getElementById('meetingsList').innerHTML = meetings.map(m => {
            const date = new Date(m.created_at).toLocaleDateString();
            const mins = Math.floor((m.duration_seconds || 0) / 60);
            const actions = (m.action_items || []).length;
            return `<div class="conv-item" onclick="viewMeeting(${m.id})">
                <div style="font-weight:600;font-size:13px">${m.title || 'Untitled'}</div>
                <div style="font-size:11px;color:var(--text5)">${date} · ${mins}min · ${actions} actions</div>
            </div>`;
        }).join('') || '<p style="color:var(--text6);padding:12px;font-size:13px">No meetings recorded yet</p>';
    } catch (e) {}
}

async function searchMeetings(query) {
    if (!query.trim()) { loadMeetings(); return; }
    try {
        const resp = await authFetch('/api/meetings/search?q=' + encodeURIComponent(query));
        const results = await resp.json();
        document.getElementById('meetingsList').innerHTML = results.map(r =>
            `<div class="conv-item" onclick="viewMeeting(${r.source_id})">
                <div style="font-weight:600;font-size:13px">${r.title || 'Match'}</div>
                <div style="font-size:11px;color:var(--text5)">${r.date} · ${Math.round(r.similarity * 100)}% match</div>
                <div style="font-size:12px;color:var(--text4);margin-top:4px">${r.content.substring(0, 100)}...</div>
            </div>`
        ).join('') || '<p style="color:var(--text6);padding:12px;font-size:13px">No results</p>';
    } catch (e) {}
}

async function viewMeeting(id) {
    try {
        const resp = await authFetch('/api/meetings/' + id);
        const m = await resp.json();
        const chatBox = document.getElementById('chatBox');
        chatBox.innerHTML = `
            <div style="padding:20px;max-width:700px;margin:0 auto">
                <h2 style="color:#8b5cf6;margin-bottom:4px">${m.title}</h2>
                <p style="color:var(--text5);font-size:12px;margin-bottom:16px">${new Date(m.created_at).toLocaleString()} · ${Math.floor((m.duration_seconds||0)/60)} min · ${m.model_used||'N/A'}</p>
                ${m.summary ? '<h3 style="margin-bottom:8px">Summary</h3><div style="margin-bottom:16px;line-height:1.6">' + m.summary + '</div>' : ''}
                ${(m.action_items||[]).length ? '<h3 style="margin-bottom:8px">Action Items</h3><ul style="margin-bottom:16px">' + m.action_items.map(a=>'<li>'+a+'</li>').join('') + '</ul>' : ''}
                <h3 style="margin-bottom:8px">Transcript</h3>
                <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:12px;font-size:13px;line-height:1.6;max-height:400px;overflow-y:auto;white-space:pre-wrap">${m.transcript||'No transcript'}</div>
            </div>
        `;
    } catch (e) {}
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/templates/index.html
git commit -m "feat: add meeting history tab with search and detail view"
```

---

### Task 10: Final Integration + Docker Rebuild

**Files:**
- Modify: `backend/app.py` (wire search_knowledge to include meetings context in voice chat)

- [ ] **Step 1: Update voice chat knowledge search to include meeting context**

In the WebSocket handler, where knowledge base is searched before LLM call, update the context building to mention meeting sources:

```python
            kb_results = await search_knowledge(user_id, user_text, openai_key=user_openai_key_for_search)
            if kb_results:
                kb_context = "\n\n".join(
                    f"[{r.get('title', 'Info')}" +
                    (f" - {r['date']}" if r.get('date') else "") +
                    (f" ({r['source_type']})" if r.get('source_type') else "") +
                    f"]: {r.get('content', '')[:500]}"
                    for r in kb_results
                )
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
curl http://localhost:8100/api/v1/heartbeat
```

- [ ] **Step 3: Manual verification**

1. Open http://localhost:8000
2. Add a knowledge entry → verify it gets vectorized (check logs for "Added X chunks")
3. Search knowledge with different words → verify semantic search works
4. Click "Record Meeting" → share a tab → verify live transcript appears
5. Stop meeting → generate summary → verify it saves
6. Check Meetings tab in sidebar → verify meeting appears
7. Search meetings → verify semantic search works
8. Ask AI in voice chat "What was discussed in the meeting?" → verify it finds the meeting

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete meeting recorder and vector search integration"
```

---

## Summary

| Task | Phase | Description | Files |
|------|-------|-------------|-------|
| 1 | P1 | Docker + dependencies | docker-compose.yml, requirements.txt, Dockerfile |
| 2 | P1 | Embedding module | embeddings.py |
| 3 | P1 | Vector store module | vectorstore.py |
| 4 | P1 | Upgrade knowledge search | models/knowledge.py, app.py |
| 5 | P2 | Meetings DB + model | schema.sql, models/meetings.py |
| 6 | P2 | Meeting summarizer | meeting_summarizer.py |
| 7 | P2 | Meeting API + WebSocket | app.py |
| 8 | P2 | Meeting recorder UI | index.html |
| 9 | P2 | Meeting history UI | index.html |
| 10 | P2 | Final integration + rebuild | app.py, Docker |
