from __future__ import annotations

import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GROQ_URL = "https://api.groq.com/openai/v1"


def _key() -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise ValueError("Groq audio is not configured.")
    return key


def transcribe(audio: bytes, content_type: str) -> str:
    if not audio:
        raise ValueError("Recorded audio is empty.")
    boundary = f"oraclis-{uuid.uuid4().hex}"
    extension = {"audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/mp4": "mp4"}.get(content_type, "webm")
    filename = f"recording.{extension}"
    fields = {
        "model": os.getenv("GROQ_STT_MODEL", "").strip() or "whisper-large-v3-turbo",
        "response_format": "json",
    }
    if language := os.getenv("GROQ_STT_LANGUAGE", "").strip():
        fields["language"] = language
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode())
    body.extend(audio)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = Request(f"{GROQ_URL}/audio/transcriptions", data=bytes(body), headers={"Authorization": f"Bearer {_key()}", "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "ORACLIS/1.0"}, method="POST")
    try:
        with urlopen(request, timeout=45) as response:
            text = str(json.loads(response.read().decode())["text"]).strip()
    except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("Groq could not transcribe that recording.") from exc
    if not text:
        raise ValueError("No speech was detected.")
    return text


def synthesize(text: str) -> bytes:
    text = text.strip()
    if not text:
        raise ValueError("Speech text is required.")
    if len(text) > 4000:
        raise ValueError("Speech text must be 4000 characters or fewer.")
    payload = json.dumps({
        "model": os.getenv("GROQ_TTS_MODEL", "").strip() or "canopylabs/orpheus-v1-english",
        "voice": os.getenv("GROQ_TTS_VOICE", "").strip() or "troy",
        "input": text,
        "response_format": "wav",
    }).encode()
    request = Request(f"{GROQ_URL}/audio/speech", data=payload, headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json", "User-Agent": "ORACLIS/1.0"}, method="POST")
    try:
        with urlopen(request, timeout=45) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("Groq could not generate speech.") from exc
