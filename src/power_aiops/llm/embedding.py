"""Zhipu AI Embedding client for vector search."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any

import httpx

from power_aiops.config import get_settings

logger = logging.getLogger(__name__)

_ZHIPU_EMBEDDING_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds multiplier


def _hash_embedding_fallback(text: str, dim: int = 256) -> list[float]:
    """Deterministic hash-based embedding (fallback only when API unavailable)."""
    hash_bytes = hashlib.sha256(text.encode("utf-8")).digest()
    vector = []
    for i in range(dim):
        byte_idx = i % len(hash_bytes)
        value = (hash_bytes[byte_idx] / 255.0) * 2.0 - 1.0
        vector.append(value)
    return _normalize_vector(vector)


def _normalize_vector(vector: list[float]) -> list[float]:
    """Normalize vector to unit length (L2)."""
    magnitude = math.sqrt(sum(x * x for x in vector))
    if magnitude > 0:
        return [x / magnitude for x in vector]
    return vector


class ZhipuEmbeddingClient:
    """Zhipu AI embedding client with retry, fallback, and connection pooling."""

    _fallback_warned: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        verify_ssl: bool | None = None,
    ):
        settings = get_settings()
        self._api_key = api_key or settings.zhipu_api_key
        self._model = model or settings.zhipu_embedding_model
        self._dimensions = dimensions or settings.zhipu_embedding_dim
        self._verify_ssl = (
            verify_ssl if verify_ssl is not None else settings.zhipu_verify_ssl
        )
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(60.0),
                verify=self._verify_ssl,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def is_configured(self) -> bool:
        return bool(self._api_key.strip())

    def get_dimension(self) -> int:
        return self._dimensions

    # ── retry helper ──────────────────────────────────────────────────────

    def _post_with_retry(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = self._get_client()
                response = client.post(
                    _ZHIPU_EMBEDDING_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF ** (attempt + 1)
                    logger.debug(f"Embedding retry {attempt+1}/{_MAX_RETRIES} after {wait:.1f}s")
                    time.sleep(wait)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # rate limit
                    last_exc = e
                    if attempt < _MAX_RETRIES - 1:
                        wait = _RETRY_BACKOFF ** (attempt + 1) * 2
                        logger.debug(f"Rate-limited, retry {attempt+1}/{_MAX_RETRIES} after {wait:.1f}s")
                        time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"Embedding API failed after {_MAX_RETRIES} retries") from last_exc

    # ── public API ────────────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        if not self.is_configured():
            raise RuntimeError("Zhipu API key not configured")

        payload: dict[str, Any] = {
            "model": self._model,
            "input": text,
            "dimensions": self._dimensions,
        }
        data = self._post_with_retry(payload, timeout=60.0)
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Invalid embedding response: {data}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in a single API call."""
        if not texts:
            return []
        if not self.is_configured():
            raise RuntimeError("Zhipu API key not configured")

        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
            "dimensions": self._dimensions,
        }
        data = self._post_with_retry(payload, timeout=120.0)
        try:
            embeddings = sorted(data["data"], key=lambda x: x["index"])
            return [e["embedding"] for e in embeddings]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Invalid embedding response: {data}") from e

    def embed_single(self, text: str) -> list[float]:
        """Embed with fallback: API → hash fallback, always returns normalized vector.

        When Zhipu API key is unconfigured or the API call fails, falls back to a
        deterministic SHA-256 hash (NOT semantic).  A one-time warning is emitted
        so operators know semantic search results are unreliable.
        """
        if not self.is_configured():
            ZhipuEmbeddingClient._warn_fallback_once("single")
            return _hash_embedding_fallback(text, self._dimensions)

        try:
            return _normalize_vector(self.embed(text))
        except Exception as e:
            ZhipuEmbeddingClient._warn_fallback_once(f"single ({e})")
            return _hash_embedding_fallback(text, self._dimensions)

    def embed_batch_with_fallback(self, texts: list[str]) -> list[list[float]]:
        """Batch embed with fallback: API → per-text hash fallback."""
        if not texts:
            return []
        if not self.is_configured():
            ZhipuEmbeddingClient._warn_fallback_once("batch")
            return [_hash_embedding_fallback(t, self._dimensions) for t in texts]

        try:
            raw = self.embed_batch(texts)
            return [_normalize_vector(v) for v in raw]
        except Exception as e:
            ZhipuEmbeddingClient._warn_fallback_once(f"batch ({e})")
            return [_hash_embedding_fallback(t, self._dimensions) for t in texts]

    @classmethod
    def _warn_fallback_once(cls, detail: str) -> None:
        """Emit a prominent warning about hash fallback (once per process)."""
        if not cls._fallback_warned:
            cls._fallback_warned = True
            logger.warning(
                "Zhipu embedding API not configured or unavailable — "
                "FALLING BACK TO DETERMINISTIC HASH (non-semantic). "
                "Vector/Graph RAG similarity search will return meaningless results. "
                "Set ZHIPU_API_KEY in .env to enable semantic embeddings. "
                "(detail: %s)", detail
            )
