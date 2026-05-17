"""Direct LLM wrapper. The module name is historical; it honors LLM_PROVIDER."""
from __future__ import annotations
import json
import os
import time
from typing import Any

from json_repair import repair_json

from .config import (
    GEMINI_API_KEY,
    GEMINI_NATIVE_MODEL,
    LLM_ENDPOINT,
    LLM_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
)
from .logs import get_logger, event
from .prompts import EXTRACT_CONCEPTS

logger = get_logger(__name__)

_GEMINI_MODEL_NAME = os.environ.get("GEMINI_TEXT_MODEL") or GEMINI_NATIVE_MODEL
_gemini_configured = False
_openai_client: Any = None


def _strip_provider(model: str, provider: str) -> str:
    prefix = f"{provider}/"
    return model.split("/", 1)[1] if model.startswith(prefix) else model


def _gemini_model():
    """Lazy-init the SDK so module import doesn't require credentials."""
    global _gemini_configured
    import google.generativeai as genai  # local import: avoid eager dep at top
    if not _gemini_configured:
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_configured = True
    return genai.GenerativeModel(_GEMINI_MODEL_NAME)


def _openai() -> Any:
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
        if LLM_ENDPOINT:
            kwargs["base_url"] = LLM_ENDPOINT
        _openai_client = OpenAI(**kwargs)
    return _openai_client


def _openai_text(prompt: str, *, json_mode: bool = False) -> str:
    model = _strip_provider(LLM_MODEL, "openai")
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise wiki-maintenance assistant. "
                    "Follow the user's output format exactly."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _openai().chat.completions.create(**kwargs)
    except Exception:
        if not json_mode:
            raise
        kwargs.pop("response_format", None)
        resp = _openai().chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def generate_text(prompt: str) -> str:
    t0 = time.time()
    if LLM_PROVIDER == "openai":
        text = _openai_text(prompt)
    else:
        text = _gemini_model().generate_content(prompt).text.strip()
    event(logger, "llm.text", provider=LLM_PROVIDER, chars=len(text),
          ms=int((time.time() - t0) * 1000))
    return text


def generate_json(prompt: str) -> dict[str, Any]:
    t0 = time.time()
    if LLM_PROVIDER == "openai":
        raw = _openai_text(prompt, json_mode=True)
    else:
        raw = _gemini_model().generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        ).text
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        out = json.loads(repair_json(raw))
    event(logger, "llm.json", provider=LLM_PROVIDER,
          keys=list(out.keys())[:5] if isinstance(out, dict) else [],
          ms=int((time.time() - t0) * 1000))
    return out


def extract_concepts(title: str, body: str) -> list[str]:
    """Fallback concept extractor when Cognee TRIPLET_COMPLETION returns nothing."""
    result = generate_json(EXTRACT_CONCEPTS.format(title=title, body=body))
    return result.get("concepts", [])[:3]
