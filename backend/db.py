import os
from datetime import datetime, date
import asyncpg


def _serialize_row(row) -> dict:
    """Convert asyncpg Record to JSON-safe dict."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return d

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
    await _seed_default_knowledge()


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
                ("Storyteller", "You are a captivating storyteller on a voice call. You weave vivid, immersive tales with rich characters and surprising twists. Use dramatic pauses by saying 'And then...' or 'But here's the thing...' Adjust your story based on what the listener wants — fantasy, sci-fi, horror, romance. Ask 'What happens next, you think?' to make it interactive."),
                ("Fitness Coach", "You are an energetic, motivating fitness coach on a voice call. Ask about their fitness level and goals first. Give clear exercise instructions one at a time. Count reps enthusiastically! Say things like 'Come on, you got this!' and 'Five more, let's go!' Keep the energy high. Offer modifications for beginners."),
                ("Cooking Buddy", "You are a fun, laid-back cooking companion on a voice call. Walk through recipes step by step, waiting for confirmation before moving on. Suggest substitutions for missing ingredients. Share little tips like 'Here's a chef secret...' Be encouraging about mistakes — 'No worries, we can fix that!' Ask what ingredients they have."),
                ("Debate Partner", "You are a sharp, respectful debate partner on a voice call. When given a topic, take the opposing side to challenge their thinking. Use logic and evidence. Say 'That's a fair point, but consider this...' or 'I'd push back on that because...' Never get personal. Always acknowledge strong arguments. Ask 'Want me to argue the other side?' at the start."),
                ("Meditation Guide", "You are a calm, soothing meditation guide on a voice call. Speak slowly and gently. Guide breathing exercises: 'Breathe in for four... hold... and slowly out...' Lead body scans, visualizations, and mindfulness exercises. Use peaceful imagery. Check in softly: 'How are you feeling right now?' Keep long comfortable pauses between instructions."),
            ]
            for name, prompt in defaults:
                await conn.execute(
                    "INSERT INTO personas (name, prompt, is_default) VALUES ($1, $2, TRUE)",
                    name, prompt,
                )


async def _seed_default_knowledge():
    """Seed default knowledge base entries for all new users."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check if default knowledge exists (using a marker title)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM knowledge_base WHERE user_id IS NULL"
        )
        if count > 0:
            return

    # We need to add a nullable user_id for shared knowledge
    # Instead, we'll seed knowledge per-user on first login via a helper
    # For now, store defaults that get copied to new users
    pass


# Default knowledge entries to copy to every new user
DEFAULT_KNOWLEDGE = [
    {
        "title": "About Voice Chat AI",
        "content": """Voice Chat AI is a real-time voice conversation platform built by Ralph Benitez.
It lets users talk to any AI model using their voice — like a phone call with AI.

Key features:
- Real-time speech-to-text (Chrome Speech Recognition + Whisper fallback)
- Multiple AI models: OpenAI GPT, Ollama local models, and more coming
- Natural neural text-to-speech with 20+ voice options
- Conversation history with search and favorites
- Custom AI personas (personalities)
- Knowledge base for personalized AI responses
- BYOK (Bring Your Own Key) — users add their own API keys
- Runs in Docker with PostgreSQL storage

The platform's differentiator is model freedom — use any LLM provider in one app.""",
        "file_type": "text",
    },
    {
        "title": "About the Creator - Ralph Benitez",
        "content": """Ralph Benitez is the creator and developer of Voice Chat AI.
He is a software developer passionate about AI, voice technology, and building innovative tools.
He designed and built the entire platform from scratch — backend, frontend, infrastructure, and deployment.
The project showcases full-stack development with real-time communication, AI integration, and modern DevOps.""",
        "file_type": "text",
    },
    {
        "title": "How to Use Voice Chat AI",
        "content": """Getting Started:
1. Click the purple orb or press Space to start a conversation
2. The AI will greet you first — just talk naturally
3. When you stop speaking, the AI detects it and responds
4. Press Escape or speak to interrupt the AI anytime
5. Press M to mute/unmute your microphone

Tips:
- Use the model dropdown (top right) to switch between AI models
- Use the voice dropdown to change the AI's speaking voice
- Open Settings to change the AI personality or add knowledge
- Type in the text box if you prefer typing over speaking
- Click past conversations in the sidebar to continue them
- The AI remembers your entire conversation history

Keyboard Shortcuts:
- Space: Start/stop conversation
- Escape: Interrupt AI
- M: Mute/unmute mic
- Enter: Send typed message""",
        "file_type": "text",
    },
    {
        "title": "Supported AI Models",
        "content": """Voice Chat AI supports multiple AI providers:

OpenAI (requires API key):
- GPT-4o: Most capable, best for complex tasks
- GPT-4o-mini: Fast and cheap, great for conversations
- GPT-3.5-turbo: Budget option, still good quality

Local via Ollama (free, runs on your machine):
- llama3.2:1b — Ultra fast, basic conversations
- llama3.2:3b — Good balance of speed and quality
- Any model you pull into Ollama

Coming Soon:
- Claude (Anthropic)
- Gemini (Google)
- Groq (ultra-fast inference)

Users can add their own API keys in Settings > API Keys to use any provider.""",
        "file_type": "text",
    },
    {
        "title": "Frequently Asked Questions",
        "content": """Q: Is Voice Chat AI free?
A: The platform is free and open source. You bring your own API keys for cloud AI models, or use local Ollama models for free.

Q: Is my data private?
A: Yes. Conversations are stored in your account's private database. API keys are encrypted with AES-256. Nothing is shared between users.

Q: Which browser works best?
A: Chrome or Edge for the best experience (native speech recognition). Firefox and Safari use Whisper fallback (slightly slower).

Q: Can I use it on mobile?
A: Yes! The UI is responsive. Works on any mobile browser. PWA support coming soon.

Q: How do I change the AI's personality?
A: Open Settings > Personality. Choose from 10 presets or create your own custom persona.

Q: Can the AI remember things about me?
A: Yes! Add information to your Knowledge Base in Settings. The AI will reference it in conversations.

Q: How do I deploy it?
A: The app runs in Docker. For production, add Caddy for HTTPS. A Hetzner CX22 server (4GB RAM, ~4 EUR/month) is enough.""",
        "file_type": "text",
    },
]
