import json
import logging
import os

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")

SUMMARY_PROMPT = """You are analyzing a meeting transcript. The transcript may be in any language.
ALWAYS respond in English regardless of the transcript language.
If the transcript is not in English, translate the key points to English.

Generate:
1. A concise title in English (under 60 chars)
2. A summary in 3-5 bullet points in English covering key decisions and discussions
3. A list of action items in English with who is responsible (if mentioned)
4. If speaker names are mentioned in the conversation, map "Speaker 0", "Speaker 1" etc to their actual names

Format as JSON:
{
  "title": "...",
  "summary": ["bullet 1", "bullet 2"],
  "action_items": ["item 1", "item 2"]
}

Respond ONLY with valid JSON, no other text."""

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
            model=model, max_tokens=1000, system=SUMMARY_PROMPT,
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
    else:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
                "model": model, "messages": messages, "stream": False,
                "options": {"temperature": 0.3},
            })
            resp.raise_for_status()
            raw_text = resp.json()["message"]["content"]

    try:
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
        return {"title": "Untitled Meeting", "summary": [raw_text[:200]], "action_items": []}
