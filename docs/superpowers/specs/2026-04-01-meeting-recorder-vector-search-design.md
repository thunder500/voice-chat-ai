# Meeting Recorder + ChromaDB Vector Search

## Overview

Add meeting recording with live transcription, auto-summarization, and semantic search powered by ChromaDB. Two-phase build: Phase 1 upgrades the knowledge base with vector search, Phase 2 adds the meeting recorder on top.

## Target

- Record Zoom/Google Meet calls via browser tab audio capture
- Live transcript during recording
- AI-generated summaries with title, key points, and action items
- Semantic search across meetings and knowledge base
- "Save to Knowledge Base" toggle per meeting

## Phase 1: ChromaDB + Vector Search

### Docker

Add `chromadb/chroma:latest` container:
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

### Embedding Pipeline

- When knowledge is added (text, PDF, or meeting summary), split content into ~500 character chunks with overlap
- Embed each chunk using:
  - **Primary:** OpenAI `text-embedding-3-small` if user has an OpenAI key
  - **Fallback:** Local `all-MiniLM-L6-v2` via sentence-transformers (bundled in Docker image)
- Store embeddings in ChromaDB with metadata: `user_id`, `source_id`, `source_type` ("knowledge" or "meeting"), `title`, `date`

### Search

- `search_knowledge()` upgraded from keyword matching to vector similarity search
- Query gets embedded with the same model used for storage
- ChromaDB returns top N most similar chunks with similarity scores
- Falls back to keyword search if ChromaDB is unavailable
- Results include source metadata (title, date, type) for context

### Data Isolation

Each user gets their own ChromaDB collection named `user_{uuid}`. Users never see each other's data.

### Python Dependencies

```
chromadb-client>=0.5.0
sentence-transformers>=3.0.0
```

## Phase 2: Meeting Recorder

### Audio Capture (Browser)

1. User clicks "Record Meeting" button in the header
2. Browser calls `getDisplayMedia({ audio: true, video: true })` to capture a shared Zoom/Meet tab
3. Simultaneously captures mic via `getUserMedia({ audio: true })`
4. Both streams combined using Web Audio API `MediaStreamAudioDestinationNode`
5. Audio recorded in 10-second chunks via `MediaRecorder` API
6. Each chunk sent to backend as binary data over WebSocket
7. Backend transcribes with Whisper, returns text
8. Frontend shows live scrolling transcript

Browser compatibility: Chrome and Edge support tab audio via getDisplayMedia. Firefox is inconsistent. Safari does not support tab audio capture.

### Chunked Transcription

- 10-second audio chunks (short enough for near-real-time, long enough for Whisper accuracy)
- Each chunk transcribed independently by Whisper
- Transcript segments labeled [Tab] (remote audio) and [Mic] (user audio) when stereo separation is possible
- Full transcript assembled from all chunks when meeting ends

### Meeting UI — Floating Modal

**During recording:**
- Floating panel (bottom-right corner, draggable, minimizable)
- Shows: red recording dot, elapsed timer, live waveform visualization
- Live transcript scrolls as chunks are transcribed
- Buttons: Pause, Stop, Minimize
- Voice chat remains functional underneath (modal can be minimized)

**When meeting ends (Stop clicked):**
- Modal expands to show summary generation screen
- User picks LLM model from dropdown (same model list as voice chat)
- AI generates: title, summary (3-5 bullet points), action items
- Full transcript displayed below summary
- Toggle: "Save to Knowledge Base" (default ON)
- Button: "Save Meeting"

### Meeting History

- New "Meetings" tab in the sidebar (alongside Conversations)
- List shows: title, date, duration, action item count
- Click a meeting to view transcript + summary
- Search bar with semantic search via ChromaDB
- AI can answer meeting questions from voice chat (e.g., "What did we discuss about pricing last Tuesday?")

### Database Schema

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

CREATE INDEX IF NOT EXISTS idx_meetings_user ON meetings(user_id);
```

### Data Flow

```
Recording:
  Tab Audio + Mic → MediaRecorder (10s chunks) → WebSocket binary
  → Backend Whisper → transcript text → WebSocket JSON → frontend live view

Meeting End:
  Full transcript → LLM (user-selected model) → title + summary + action items
  → PostgreSQL (meetings table)

If "Save to KB" = ON:
  Summary + key transcript sections → split into ~500 char chunks
  → embed (OpenAI or sentence-transformers) → ChromaDB (user collection)

Voice Chat Query:
  User asks "What did we discuss about pricing?"
  → embed query → ChromaDB similarity search
  → returns relevant chunks with dates and meeting titles
  → AI answers with full context
```

### Meeting Summary Prompt

```
You are analyzing a meeting transcript. Generate:
1. A concise title (under 60 chars)
2. A summary in 3-5 bullet points covering key decisions and discussions
3. A list of action items with who is responsible (if mentioned)

Format as JSON:
{
  "title": "...",
  "summary": ["bullet 1", "bullet 2", ...],
  "action_items": ["item 1", "item 2", ...]
}
```

### WebSocket Meeting Protocol

New message types for meeting recording:

```
Client → Server:
  { type: "meeting_start" }           — begin recording session
  binary data                          — 10-second audio chunk
  { type: "meeting_stop" }            — end recording
  { type: "meeting_summarize", model: "gpt-4o-mini" }  — generate summary

Server → Client:
  { type: "meeting_transcript", text: "...", chunk_index: 0 }  — live transcript
  { type: "meeting_summary", title: "...", summary: [...], action_items: [...] }
  { type: "meeting_saved", id: 123 }
  { type: "meeting_error", message: "..." }
```

### API Endpoints

```
GET    /api/meetings              — list user's meetings (title, date, duration)
GET    /api/meetings/:id          — get full meeting (transcript + summary)
DELETE /api/meetings/:id          — delete meeting + remove from ChromaDB
PATCH  /api/meetings/:id          — update title or toggle knowledge base
GET    /api/meetings/search?q=... — semantic search across meetings
```

## Implementation Order

1. ChromaDB Docker container + connection module
2. Embedding module (OpenAI + sentence-transformers fallback)
3. Upgrade knowledge base to use vector search
4. Meetings database table + model
5. Meeting WebSocket protocol (audio chunks + transcription)
6. Meeting recorder frontend (floating modal, live transcript)
7. Meeting summary generation
8. Meeting history UI (sidebar tab, list, detail view)
9. Semantic search across meetings
10. "Save to Knowledge Base" toggle with ChromaDB sync
