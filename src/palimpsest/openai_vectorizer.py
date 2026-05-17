"""RedisVL vectorizer backed by OpenAI embeddings.

RedisVL ships an OpenAI vectorizer, but it makes a live dimension-check call at
construction time. The answer cache is lazy, so this wrapper keeps construction
offline and trusts EMBEDDING_DIMENSIONS from .env.
"""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict
from redisvl.utils.vectorize.base import BaseVectorizer

_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIMS = 1536


def normalize_openai_embedding_model(model: str | None) -> str:
    value = (model or _DEFAULT_MODEL).strip()
    if value.startswith("openai/"):
        value = value.split("/", 1)[1]
    return value or _DEFAULT_MODEL


class OpenAIEmbeddingVectorizer(BaseVectorizer):
    """RedisVL text vectorizer using OpenAI's embeddings endpoint."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str = "",
        base_url: str = "",
        dims: int = _DEFAULT_DIMS,
        dtype: str = "float32",
        cache: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=normalize_openai_embedding_model(model),
            dtype=dtype,
            cache=cache,
        )
        if not api_key:
            raise ValueError("OpenAI embedding API key is missing")
        self._api_key = api_key
        self._base_url = base_url or ""
        self.dims = int(dims or _DEFAULT_DIMS)
        self._client: Any = None

    @property
    def type(self) -> str:
        return "openai"

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    @staticmethod
    def _extract_many(response: Any) -> list[list[float]]:
        data = getattr(response, "data", None)
        if not data:
            raise RuntimeError("OpenAI returned an empty embedding response")
        return [list(item.embedding) for item in data]

    def _embed(
        self,
        content: str = "",
        text: str = "",
        **kwargs: Any,
    ) -> list[float]:
        content = content or text
        if not isinstance(content, str):
            raise TypeError("OpenAIEmbeddingVectorizer._embed expects a str.")
        response = self._get_client().embeddings.create(
            model=self.model,
            input=[content],
            **kwargs,
        )
        return self._extract_many(response)[0]

    def _embed_many(
        self,
        contents: list[str] | None = None,
        texts: list[str] | None = None,
        batch_size: int = 10,
        **kwargs: Any,
    ) -> list[list[float]]:
        contents = contents or texts
        if not isinstance(contents, list):
            raise TypeError("OpenAIEmbeddingVectorizer._embed_many expects a list[str].")
        if not contents:
            return []

        out: list[list[float]] = []
        for batch in self.batchify(contents, batch_size):
            response = self._get_client().embeddings.create(
                model=self.model,
                input=batch,
                **kwargs,
            )
            out.extend(self._extract_many(response))
        return out

    async def _aembed(
        self,
        content: str = "",
        text: str = "",
        **kwargs: Any,
    ) -> list[float]:
        return self._embed(content=content or text, **kwargs)

    async def _aembed_many(
        self,
        contents: list[str] | None = None,
        texts: list[str] | None = None,
        batch_size: int = 10,
        **kwargs: Any,
    ) -> list[list[float]]:
        return self._embed_many(
            contents=contents or texts,
            batch_size=batch_size,
            **kwargs,
        )
