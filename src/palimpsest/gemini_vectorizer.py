"""Custom redisvl vectorizer that calls Gemini's text-embedding-004 (dim=768).

Why this exists: `redisvl.extensions.cache.llm.SemanticCache` defaults its
vectorizer to `HFTextVectorizer`, which imports `sentence_transformers` and
drags in ~800 MB of torch. We already have Gemini wired up, so we use that
instead and skip the torch dependency entirely.

Import-time contract: module-level imports must NOT call genai.configure or
hit the network. The Gemini SDK is imported lazily inside `_embed*` methods,
mirroring `gemini_io.py`.
"""
from __future__ import annotations
from typing import Any

from pydantic import ConfigDict

from redisvl.utils.vectorize.base import BaseVectorizer

# `text-embedding-004` returns 768-dim float vectors.
# Source: https://ai.google.dev/gemini-api/docs/embeddings
_DEFAULT_MODEL = "text-embedding-004"
_DEFAULT_DIMS = 768


class GeminiTextVectorizer(BaseVectorizer):
    """RedisVL vectorizer backed by Google Gemini embeddings.

    Uses `google.generativeai.embed_content(model="models/<model>", ...)`.
    Lazy imports the genai SDK so this class is safe to construct in tests
    that monkeypatch the API calls.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        dtype: str = "float32",
        cache: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, dtype=dtype, cache=cache)
        # `text-embedding-004` is fixed at 768 dims. We hardcode rather than
        # making a live test call so __init__ is import-safe without an API key.
        self.dims = _DEFAULT_DIMS
        self._configured = False

    @property
    def type(self) -> str:
        return "gemini"

    # ---- internal helpers ----

    def _ensure_configured(self) -> None:
        """Lazy-init genai client. Mirrors gemini_io._model()."""
        if self._configured:
            return
        import google.generativeai as genai  # local import: avoid eager dep
        from .config import GEMINI_API_KEY
        genai.configure(api_key=GEMINI_API_KEY)
        self._configured = True

    def _model_name(self) -> str:
        """Gemini SDK requires the `models/` prefix."""
        if self.model.startswith("models/"):
            return self.model
        return f"models/{self.model}"

    @staticmethod
    def _extract_embedding(response: Any) -> list[float]:
        """Pull a single embedding list out of the genai response dict."""
        if not isinstance(response, dict):
            raise RuntimeError(
                f"Unexpected Gemini embed_content response type: {type(response)}"
            )
        emb = response.get("embedding")
        if not emb:
            raise RuntimeError("Gemini returned an empty embedding result")
        # When `content` is a list, embedding is list[list[float]]; when single,
        # it's list[float]. Caller handles each case explicitly.
        return emb

    # ---- required overrides ----

    def _embed(
        self,
        content: str = "",
        text: str = "",
        task_type: str = "retrieval_document",
        **kwargs: Any,
    ) -> list[float]:
        content = content or text
        if not isinstance(content, str):
            raise TypeError("GeminiTextVectorizer._embed expects a str.")
        self._ensure_configured()
        import google.generativeai as genai  # local import
        response = genai.embed_content(
            model=self._model_name(),
            content=content,
            task_type=task_type,
        )
        emb = self._extract_embedding(response)
        # Single-content shape is list[float] (not list[list[float]]).
        if emb and isinstance(emb[0], list):
            # Defensive: some SDK versions wrap singletons.
            emb = emb[0]
        return list(emb)

    def _embed_many(
        self,
        contents: list[str] | None = None,
        texts: list[str] | None = None,
        batch_size: int = 10,
        task_type: str = "retrieval_document",
        **kwargs: Any,
    ) -> list[list[float]]:
        contents = contents or texts
        if not isinstance(contents, list):
            raise TypeError("GeminiTextVectorizer._embed_many expects a list[str].")
        if not contents:
            return []
        self._ensure_configured()
        import google.generativeai as genai  # local import

        out: list[list[float]] = []
        for batch in self.batchify(contents, batch_size):
            response = genai.embed_content(
                model=self._model_name(),
                content=batch,
                task_type=task_type,
            )
            emb = self._extract_embedding(response)
            # Batch shape: list[list[float]]
            for vec in emb:
                out.append(list(vec))
        return out

    async def _aembed(
        self,
        content: str = "",
        text: str = "",
        task_type: str = "retrieval_query",
        **kwargs: Any,
    ) -> list[float]:
        # No native async on the legacy SDK; fall through to sync. Use
        # retrieval_query as the default for ad-hoc lookups.
        return self._embed(content=content or text, task_type=task_type, **kwargs)

    async def _aembed_many(
        self,
        contents: list[str] | None = None,
        texts: list[str] | None = None,
        batch_size: int = 10,
        task_type: str = "retrieval_document",
        **kwargs: Any,
    ) -> list[list[float]]:
        return self._embed_many(
            contents=contents or texts,
            batch_size=batch_size,
            task_type=task_type,
            **kwargs,
        )
