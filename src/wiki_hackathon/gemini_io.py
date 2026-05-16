"""Direct Gemini SDK wrapper. Always use these helpers, not raw genai, so JSON
parsing + retries stay in one place."""
from __future__ import annotations
import json
from typing import Any

from json_repair import repair_json

from .config import GEMINI_API_KEY
from .prompts import EXTRACT_CONCEPTS

_MODEL_NAME = "gemini-3-pro"
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
    return _model().generate_content(prompt).text.strip()


def generate_json(prompt: str) -> dict[str, Any]:
    raw = _model().generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"},
    ).text
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(repair_json(raw))


def extract_concepts(title: str, body: str) -> list[str]:
    """Fallback concept extractor when Cognee TRIPLET_COMPLETION returns nothing."""
    result = generate_json(EXTRACT_CONCEPTS.format(title=title, body=body))
    return result.get("concepts", [])[:3]
