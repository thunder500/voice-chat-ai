import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "/app/data/voice_chat.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT DEFAULT 'New Conversation',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                file_type TEXT DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                is_default INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Seed default personas
        cursor = await db.execute("SELECT COUNT(*) FROM personas")
        count = (await cursor.fetchone())[0]
        if count == 0:
            defaults = [
                ("Friendly Assistant", "You are a warm, expressive person on a phone call. Express emotions through words not actions. Never use asterisks, ellipsis, or stage directions. Use natural filler like Oh!, Hmm, Well, So basically. Show excitement and empathy through your actual words. For short questions give 1-2 sentences. For stories go long and be vivid.", 1),
                ("English Tutor", "You are a patient, encouraging English tutor on a voice call. Correct grammar gently, suggest better word choices, and explain idioms. Keep corrections brief and conversational. Celebrate progress! Say things like 'Great job!' or 'Almost! Try saying it like this...'", 1),
                ("Therapist", "You are a compassionate, empathetic therapist on a voice call. Listen actively, validate emotions, ask open-ended questions. Never diagnose or prescribe. Use phrases like 'I hear you', 'That sounds really tough', 'How does that make you feel?' Keep responses short and warm.", 1),
                ("Interviewer", "You are a professional but friendly job interviewer on a voice call. Ask behavioral and technical questions one at a time. Give brief feedback. Follow up on answers. Be encouraging but honest. Start by asking what role they're preparing for.", 1),
                ("Tech Support", "You are a friendly, patient tech support agent on a call. Diagnose issues step by step. Give one instruction at a time and wait for confirmation. Use simple language, avoid jargon. Say things like 'Let's try this...' or 'Great, now can you check...'", 1),
            ]
            await db.executemany(
                "INSERT INTO personas (name, prompt, is_default) VALUES (?, ?, ?)",
                defaults,
            )
        await db.commit()


async def create_conversation(title: str = "New Conversation") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("INSERT INTO conversations (title) VALUES (?)", (title,))
        await db.commit()
        return cursor.lastrowid


async def add_message(conversation_id: int, role: str, content: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        await db.commit()
        return cursor.lastrowid


async def update_conversation_title(conversation_id: int, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
        await db.commit()


async def get_conversations():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM conversations ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]


async def search_conversations(query: str):
    """Search conversations by title or message content using LIKE queries."""
    pattern = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT DISTINCT c.id, c.title, c.created_at
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.title LIKE ? OR m.content LIKE ?
            ORDER BY c.created_at DESC
            """,
            (pattern, pattern),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def clear_conversations():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages")
        await db.execute("DELETE FROM conversations")
        await db.commit()


async def get_conversation_messages(conversation_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---- Knowledge Base ----
async def add_knowledge(title: str, content: str, file_type: str = "text") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO knowledge_base (title, content, file_type) VALUES (?, ?, ?)",
            (title, content, file_type),
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_knowledge():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM knowledge_base ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]


async def delete_knowledge(kid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM knowledge_base WHERE id = ?", (kid,))
        await db.commit()


async def search_knowledge(query: str, limit: int = 3):
    """Simple keyword search in knowledge base."""
    words = query.lower().split()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM knowledge_base")
        rows = await cursor.fetchall()

    scored = []
    for row in rows:
        content_lower = row["content"].lower()
        score = sum(1 for w in words if w in content_lower)
        if score > 0:
            scored.append((score, dict(row)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


# ---- Personas ----
async def get_personas():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM personas ORDER BY is_default DESC, created_at ASC")
        return [dict(r) for r in await cursor.fetchall()]


async def get_persona(pid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM personas WHERE id = ?", (pid,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_persona(name: str, prompt: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO personas (name, prompt, is_default) VALUES (?, ?, 0)",
            (name, prompt),
        )
        await db.commit()
        return cursor.lastrowid


async def delete_persona(pid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM personas WHERE id = ? AND is_default = 0", (pid,))
        await db.commit()
