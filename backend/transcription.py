"""Multi-provider transcription with speaker diarization.

Priority: Deepgram (best diarization) > AssemblyAI > Groq Whisper > OpenAI Whisper > Local Whisper
"""
import asyncio
import io
import logging
import tempfile
import os

import httpx

logger = logging.getLogger(__name__)


async def transcribe_meeting(audio_bytes: bytes, user_keys: dict, whisper_model=None) -> str:
    """Transcribe meeting audio with the best available provider.
    Returns formatted transcript with speaker labels when available."""

    # Try Deepgram first (best diarization)
    deepgram_key = user_keys.get("deepgram")
    if deepgram_key:
        logger.info(f"Trying Deepgram ({len(audio_bytes)} bytes)...")
        result = await _transcribe_deepgram(audio_bytes, deepgram_key)
        if result:
            logger.info("Transcribed with Deepgram (speaker diarization)")
            return result

    # Try Groq Whisper (fast, free)
    groq_key = user_keys.get("groq")
    if groq_key:
        result = await _transcribe_groq(audio_bytes, groq_key)
        if result:
            return result

    # Try OpenAI Whisper API
    openai_key = user_keys.get("openai")
    if openai_key:
        result = await _transcribe_openai(audio_bytes, openai_key)
        if result:
            return result

    # Fallback: local Whisper
    if whisper_model:
        result = await _transcribe_local(audio_bytes, whisper_model)
        if result:
            return result

    return "(No transcription available)"


async def _transcribe_deepgram(audio_bytes: bytes, api_key: str) -> str | None:
    """Transcribe with Deepgram — includes speaker diarization."""
    try:
        # Use Deepgram REST API directly (more reliable than SDK version changes)
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/listen",
                params={
                    "model": "nova-2",
                    "smart_format": "true",
                    "diarize": "true",
                    "punctuate": "true",
                    "utterances": "true",
                    "detect_language": "true",
                },
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "audio/webm",
                },
                content=audio_bytes,
            )
            resp.raise_for_status()
            result = resp.json()

        # Detect language
        detected_lang = result.get("results", {}).get("channels", [{}])[0].get("detected_language", "en")
        logger.info(f"Deepgram detected language: {detected_lang}")

        utterances = result.get("results", {}).get("utterances", [])

        if utterances:
            lines = []
            if detected_lang != "en":
                lines.append(f"[Language detected: {detected_lang}]")
            for u in utterances:
                speaker = f"Speaker {u.get('speaker', '?')}"
                text = u.get("transcript", "")
                if text.strip():
                    lines.append(f"[{speaker}]: {text}")
            transcript = "\n".join(lines)
            logger.info(f"Deepgram: {len(utterances)} utterances, {len(transcript)} chars")
            return transcript

        # Fallback to paragraphs if no utterances
        paragraphs = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("paragraphs", {}).get("paragraphs", [])
        if paragraphs:
            lines = []
            for p in paragraphs:
                speaker = f"Speaker {p.get('speaker', '?')}"
                sentences = " ".join(s.get("text", "") for s in p.get("sentences", []))
                if sentences.strip():
                    lines.append(f"[{speaker}]: {sentences}")
            return "\n".join(lines)

        # Final fallback to plain transcript
        plain = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
        return plain if plain.strip() else None

    except Exception as e:
        logger.error(f"Deepgram transcription failed: {e}")
        return None


async def _transcribe_groq(audio_bytes: bytes, api_key: str) -> str | None:
    """Transcribe with Groq's Whisper API (fast, free)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(tmp_path, "rb") as f:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("meeting.webm", f, "audio/webm")},
                    data={"model": "whisper-large-v3", "response_format": "verbose_json"},
                )
                resp.raise_for_status()
                data = resp.json()

        os.unlink(tmp_path)

        # Format segments with timestamps
        segments = data.get("segments", [])
        if segments:
            lines = []
            for s in segments:
                start = int(s.get("start", 0))
                mm, ss = divmod(start, 60)
                text = s.get("text", "").strip()
                if text:
                    lines.append(f"[{mm:02d}:{ss:02d}] {text}")
            return "\n".join(lines)

        return data.get("text", "")

    except Exception as e:
        logger.error(f"Groq transcription failed: {e}")
        return None


async def _transcribe_openai(audio_bytes: bytes, api_key: str) -> str | None:
    """Transcribe with OpenAI Whisper API."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        os.unlink(tmp_path)

        # Format with timestamps
        segments = resp.segments if hasattr(resp, 'segments') and resp.segments else []
        if segments:
            lines = []
            for s in segments:
                start = int(s.start) if hasattr(s, 'start') else 0
                mm, ss = divmod(start, 60)
                text = s.text.strip() if hasattr(s, 'text') else ""
                if text:
                    lines.append(f"[{mm:02d}:{ss:02d}] {text}")
            return "\n".join(lines)

        return resp.text if hasattr(resp, 'text') else str(resp)

    except Exception as e:
        logger.error(f"OpenAI transcription failed: {e}")
        return None


async def _transcribe_local(audio_bytes: bytes, whisper_model) -> str | None:
    """Transcribe with local faster-whisper."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        def _do():
            segments, info = whisper_model.transcribe(
                tmp_path, beam_size=3, best_of=2, temperature=0,
                condition_on_previous_text=True,
                no_speech_threshold=0.8, log_prob_threshold=-1.5,
            )
            lines = []
            for s in segments:
                mm, ss = divmod(int(s.start), 60)
                text = s.text.strip()
                if text and text.lower() not in {"", "you", "thank you", "thanks", "bye"}:
                    lines.append(f"[{mm:02d}:{ss:02d}] {text}")
            return "\n".join(lines)

        result = await asyncio.to_thread(_do)
        os.unlink(tmp_path)
        return result if result.strip() else None

    except Exception as e:
        logger.error(f"Local whisper failed: {e}")
        return None
