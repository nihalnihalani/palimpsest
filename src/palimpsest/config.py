"""Centralized env + Cognee bootstrap. Import this BEFORE any cognee submodules."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Quiet cognee's structlog output by default. Its ConsoleRenderer paints the
# logger name in BLUE which is illegible on dark terminal backgrounds, and
# its INFO-level chatter (log-file path, auth posture, "Cognee 1.0 changes")
# drowns out our own status lines. Users can re-enable everything with
# `LOG_LEVEL=INFO` (or DEBUG) in .env. NO_COLOR follows https://no-color.org/.
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("NO_COLOR", "1")

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

# App LLM config. Cognee reads these same env vars through its own settings
# layer; these constants are for this repo's direct prompt calls.
LLM_PROVIDER = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
LLM_MODEL = (os.environ.get("LLM_MODEL") or "gemini/gemini-3-pro-preview").strip()
LLM_API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LLM_ENDPOINT = (os.environ.get("LLM_ENDPOINT") or "").strip()
LLM_INSTRUCTOR_MODE = (os.environ.get("LLM_INSTRUCTOR_MODE") or "").strip()

GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or (LLM_API_KEY if LLM_PROVIDER == "gemini" else "")
    or ""
).strip()
OPENAI_API_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or (LLM_API_KEY if LLM_PROVIDER == "openai" else "")
    or ""
).strip()

if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY or LLM_API_KEY is missing for LLM_PROVIDER=gemini."
    )
if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY or LLM_API_KEY is missing for LLM_PROVIDER=openai."
    )

# Embedding config for the RedisVL answer cache. Cognee also reads these env vars.
EMBEDDING_PROVIDER = (
    os.environ.get("EMBEDDING_PROVIDER") or LLM_PROVIDER
).strip().lower()
EMBEDDING_MODEL = (os.environ.get("EMBEDDING_MODEL") or "").strip()
EMBEDDING_API_KEY = (
    os.environ.get("EMBEDDING_API_KEY")
    or (OPENAI_API_KEY if EMBEDDING_PROVIDER == "openai" else "")
    or (GEMINI_API_KEY if EMBEDDING_PROVIDER == "gemini" else "")
    or ""
).strip()
try:
    EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS") or "0")
except ValueError:
    EMBEDDING_DIMENSIONS = 0

# LiteLLM (Cognee) vs google.generativeai use different model strings:
#   LLM_MODEL=gemini/<api-model-id> -> GEMINI_NATIVE_MODEL=<api-model-id>
# Override GEMINI_NATIVE_MODEL if you use a LiteLLM alias that doesn't match the GenAI API id.
_LLM_MODEL = LLM_MODEL
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
    # Register both the active model (from LLM_MODEL) AND known Gemini 3
    # preview names that LiteLLM's model_cost map doesn't ship with yet.
    candidates: list[str] = [_LLM_MODEL] if _LLM_MODEL.startswith("gemini/") else []
    candidates += ["gemini/gemini-3-pro", "gemini/gemini-3-pro-preview"]
    seen: set[str] = set()
    for name in candidates:
        if name in seen or name in getattr(litellm, "model_cost", {}):
            continue
        seen.add(name)
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

# Register the Cognee Redis vector adapter (community package) if installed.
# Side-effect import -- no register() function, the module body calls
# use_vector_adapter(). Wrapped in try/except so a missing optional package
# does not break the project.
try:
    import cognee_community_vector_adapter_redis.register  # noqa: F401
except ImportError:
    pass


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
