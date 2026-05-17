"""Centralized env + Cognee bootstrap. Import this BEFORE any cognee submodules."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Cognee's BaseConfig defaults resolve ".cognee_system" relative to the *cognee package*
# (inside site-packages). Force repo-local paths so Claude/agents + CLI share one DB and
# installs stay writable.
for _env_key, _rel in (
    ("SYSTEM_ROOT_DIRECTORY", ".cognee_system"),
    ("DATA_ROOT_DIRECTORY", ".data_storage"),
    ("CACHE_ROOT_DIRECTORY", ".cognee_cache"),
):
    _desired = str((ROOT / _rel).resolve())
    _current = (os.environ.get(_env_key) or "").strip()
    if (
        not _current
        or not Path(_current).expanduser().is_absolute()
        or "/site-packages/cognee/" in _current
    ):
        os.environ[_env_key] = _desired

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# The hackathon brief issues `LLM_API_KEY` at kickoff — our direct
# google.generativeai SDK calls need `GEMINI_API_KEY`. Accept either; if
# only LLM_API_KEY is set (the brief's convention), use it.
GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("LLM_API_KEY")
    or ""
).strip()
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY (or LLM_API_KEY) is missing or empty in .env. "
        "Set one of them to a valid Gemini key and re-run."
    )

# LiteLLM (Cognee) vs google.generativeai use different model strings:
#   LLM_MODEL=gemini/<api-model-id>  →  GEMINI_NATIVE_MODEL=<api-model-id>
# Override GEMINI_NATIVE_MODEL if you use a LiteLLM alias that doesn't match the GenAI API id.
_LLM_MODEL = (os.environ.get("LLM_MODEL") or "gemini/gemini-3-pro-preview").strip()
GEMINI_NATIVE_MODEL = (os.environ.get("GEMINI_NATIVE_MODEL") or "").strip()
if not GEMINI_NATIVE_MODEL:
    if _LLM_MODEL.startswith("gemini/"):
        GEMINI_NATIVE_MODEL = _LLM_MODEL.split("/", 1)[1]
    else:
        GEMINI_NATIVE_MODEL = "gemini-2.5-pro"

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
EXPLORATIONS_DIR = WIKI_DIR / "explorations"
LOG_FILE = WIKI_DIR / "log.md"
CANNED_DIR = ROOT / "data" / "canned"
SNAPSHOT_DIR = ROOT / "snapshot"

for d in (CONCEPTS_DIR, REPORTS_DIR, EXPLORATIONS_DIR, SNAPSHOT_DIR):
    d.mkdir(parents=True, exist_ok=True)


def patch_litellm_for_gemini() -> None:
    """Inject current LLM_MODEL into LiteLLM's model_cost map if missing.

    Why: Cognee uses LiteLLM; bleeding-edge model strings sometimes raise
    KeyError: 'max_tokens' on lookup. One-line patch from research.
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        return
    name = _LLM_MODEL
    if not name.startswith("gemini/"):
        return
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


patch_litellm_for_gemini()


def configure_cognee_from_env() -> None:
    """Force cognee to use the embedding model/provider/dims we set in .env.

    NOTE: cognee 1.1.0 has its own BaseConfig instance that the runtime reads
    directly from environment variables at pipeline-execution time. Calling
    cognee.config.set_* mutates a different singleton and the override does
    NOT propagate to BaseConfig. The .env vars (EMBEDDING_MODEL etc.) DO
    propagate because cognee's BaseConfig reads them via pydantic-settings.
    So this function is effectively a no-op unless cognee changes that
    behaviour upstream; we keep it as a stub + audit log so future cognee
    versions that DO honour the set_* path Just Work.

    In Cognee Cloud mode (COGNEE_SERVICE_URL set), embeddings happen on the
    cloud tenant's side, so this is fully moot.
    """
    return  # no-op (see docstring)


configure_cognee_from_env()
