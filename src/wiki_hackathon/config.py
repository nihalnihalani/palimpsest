"""Centralized env + Cognee bootstrap. Import this BEFORE any cognee submodules."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Constants used by every module
STREAM = "firehose:items"
GROUP = "ingestors"
CONSUMER = "worker-1"
EVOLUTION_STREAM = "wiki:evolution"
PUBSUB_CHANNEL = "wiki:events"
DEDUP_PREFIX = "seen:"
JSON_KEY_PREFIX = "wiki:concept:"
VERDICT_PREFIX = "verdict:"
DATASET = "wiki"

WIKI_DIR = ROOT / "wiki"
CONCEPTS_DIR = WIKI_DIR / "concepts"
REPORTS_DIR = WIKI_DIR / "reports"
LOG_FILE = WIKI_DIR / "log.md"
CANNED_DIR = ROOT / "data" / "canned"
SNAPSHOT_DIR = ROOT / "snapshot"

for d in (CONCEPTS_DIR, REPORTS_DIR, SNAPSHOT_DIR):
    d.mkdir(parents=True, exist_ok=True)


def patch_litellm_for_gemini3() -> None:
    """Inject gemini-3-pro into LiteLLM's model_cost map if missing.

    Why: Cognee uses LiteLLM; bleeding-edge model strings sometimes raise
    KeyError: 'max_tokens' on lookup. One-line patch from research.
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        return
    name = "gemini/gemini-3-pro"
    if name not in getattr(litellm, "model_cost", {}):
        litellm.register_model(  # type: ignore[attr-defined]
            {
                name: {
                    "max_tokens": 8192,
                    "max_input_tokens": 1_000_000,
                    "max_output_tokens": 8192,
                    "input_cost_per_token": 0.0,
                    "output_cost_per_token": 0.0,
                    "litellm_provider": "gemini",
                    "mode": "chat",
                    "supports_function_calling": True,
                    "supports_vision": True,
                }
            }
        )


patch_litellm_for_gemini3()
