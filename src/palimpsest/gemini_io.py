"""Direct Gemini SDK wrapper. Always use these helpers, not raw genai, so JSON
parsing + retries stay in one place."""
from __future__ import annotations
import json
import time
from typing import Any

from json_repair import repair_json

from .config import GEMINI_API_KEY
from .logs import get_logger, event
from .prompts import EXTRACT_CONCEPTS

logger = get_logger(__name__)

import os
_MODEL_NAME = os.environ.get("GEMINI_TEXT_MODEL", "gemini-3-pro-preview")
_configured = False


def _model():
    """Lazy-init the SDK so module import doesn't require credentials."""
    global _configured
    import google.generativeai as genai  # local import: avoid eager dep at top
    if not _configured:
        genai.configure(api_key=GEMINI_API_KEY)
        _configured = True
    return genai.GenerativeModel(_MODEL_NAME)


def generate_text(prompt: str) -> str:
    t0 = time.time()
    text = _model().generate_content(prompt).text.strip()
    event(logger, "gemini.text", chars=len(text),
          ms=int((time.time() - t0) * 1000))
    return text


def generate_json(prompt: str) -> dict[str, Any]:
    t0 = time.time()
    raw = _model().generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"},
    ).text
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        out = json.loads(repair_json(raw))
    event(logger, "gemini.json",
          keys=list(out.keys())[:5] if isinstance(out, dict) else [],
          ms=int((time.time() - t0) * 1000))
    return out


def extract_concepts(title: str, body: str) -> list[str]:
    """Fallback concept extractor when Cognee TRIPLET_COMPLETION returns nothing."""
    result = generate_json(EXTRACT_CONCEPTS.format(title=title, body=body))
    return result.get("concepts", [])[:3]
