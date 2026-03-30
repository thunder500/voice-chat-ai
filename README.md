# Voice Chat AI

Real-time AI voice chat in the browser. Talk naturally — speech is transcribed, sent to an LLM, and spoken back using neural TTS.

## Features

- Real-time voice conversation via Web Speech API (Chrome) or Whisper fallback
- Neural text-to-speech via edge-tts (20+ English voices)
- Streaming LLM responses with typing animation
- Conversation history with sidebar navigation and transcript export
- Knowledge base — upload PDFs or paste text for the AI to reference
- Custom personas (system prompts)
- Works with **local Ollama models** or **OpenAI GPT/o-series models**

## Quick Start

**Requirements:** Docker Desktop

```bash
# Clone the repo
git clone https://github.com/thunder500/voice-chat-ai
cd voice-chat-ai

# (Optional) Set an OpenAI API key for GPT models
cp .env.example .env
# Edit .env and add: OPENAI_API_KEY=sk-...

# Start everything
chmod +x start.sh && ./start.sh
```

Then open **http://localhost:8000** in Chrome.

On first run, `start.sh` pulls the `llama3.2` model (~2 GB). Subsequent starts are fast.

## Architecture

```
Browser ──WebSocket──► FastAPI (app.py)
                           ├── faster-whisper  (speech-to-text, CPU)
                           ├── Ollama          (local LLM)
                           ├── OpenAI API      (optional cloud LLM)
                           ├── edge-tts        (neural TTS)
                           └── SQLite          (conversations, KB, personas)
```

| Service | Port | Description |
|---------|------|-------------|
| Voice Chat app | 8000 | FastAPI + frontend |
| Ollama | 11434 | Local LLM server |

## Configuration

All settings are environment variables (set in `.env` or `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | *(empty)* | OpenAI key — enables GPT/o-series models |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default OpenAI model |
| `OLLAMA_MODEL` | `llama3.2:1b` | Local model to use |
| `WHISPER_MODEL_SIZE` | `base` | Whisper model: `tiny`, `base`, `small`, `medium` |
| `TTS_VOICE` | `en-US-AriaNeural` | Default TTS voice |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Service health status |
| GET | `/api/conversations` | List conversations |
| GET | `/api/conversations/{id}` | Get conversation messages |
| GET | `/api/conversations/{id}/export` | Download transcript as `.txt` |
| PATCH | `/api/conversations/{id}` | Rename conversation |
| DELETE | `/api/conversations` | Delete all conversations |
| GET | `/api/models` | List available LLM models |
| GET | `/api/voices` | List available TTS voices |
| GET | `/api/knowledge` | List knowledge base entries |
| POST | `/api/knowledge` | Add text or file to knowledge base |
| DELETE | `/api/knowledge/{id}` | Remove knowledge base entry |
| GET | `/api/personas` | List personas |
| POST | `/api/personas` | Create custom persona |
| DELETE | `/api/personas/{id}` | Delete custom persona |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | Start / stop conversation |
| `Esc` | Interrupt AI while speaking |
| `M` | Mute / unmute microphone |
| `Enter` | Send typed message |

## GPU Acceleration (optional)

Uncomment the `deploy` block in `docker-compose.yml` under the `ollama` service to enable NVIDIA GPU passthrough.

## Pulling a different Ollama model

```bash
docker compose exec ollama ollama pull mistral
```

Then select it from the model dropdown in the UI.
