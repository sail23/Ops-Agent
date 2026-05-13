"""
OpenAI 兼容 Chat Completions（POST `/v1/chat/completions`）。

支持流式输出(stream=True)和非流式输出。
未配置 `OPENAI_API_KEY` 时，`chat` 返回占位文本，不发起网络请求。
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
import time
from typing import Any, AsyncGenerator

import httpx

from power_aiops.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_OPENAI_V1_BASE = "https://api.openai.com/v1"
_MAX_RETRIES = 3
_INITIAL_RETRY_DELAY = 3


def _normalize_base_url(raw: str) -> str:
    return raw.strip().rstrip("/")


def chat_completion_stub(*, system: str, user: str) -> str:
    """无 API Key 或未启用时的占位输出。"""
    return (
        "[LLM stub] 未配置 OPENAI_API_KEY 或未启用云端调用。\n"
        f"--- user prompt ({len(user)} chars) ---\n"
        f"{user[:2000]}{'…' if len(user) > 2000 else ''}"
    )


class OpenAICompatibleClient:
    """基于 httpx 的 OpenAI 兼容客户端（支持自定义 `OPENAI_API_BASE`）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.openai_api_key.strip())

    def _base_url(self) -> str:
        b = _normalize_base_url(self._settings.openai_api_base)
        return b if b else _DEFAULT_OPENAI_V1_BASE

    def chat(self, *, system: str, user: str) -> str:
        if not self.is_configured():
            return chat_completion_stub(system=system, user=user)
        return self._post_with_retry(system=system, user=user)

    def _post_with_retry(self, *, system: str, user: str) -> str:
        url = f"{self._base_url()}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.openai_chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        read_timeout = max(self._settings.openai_timeout_seconds, 300.0)
        timeout = httpx.Timeout(read_timeout, connect=30.0)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            retry_delay = _INITIAL_RETRY_DELAY * (2 ** attempt)
            try:
                with httpx.Client(timeout=timeout) as client:
                    r = client.post(url, headers=headers, json=payload)
                    r.raise_for_status()
                    data = r.json()
                try:
                    return str(data["choices"][0]["message"]["content"])
                except (KeyError, IndexError, TypeError) as e:
                    logger.warning("unexpected chat response shape: %s", data)
                    raise RuntimeError("invalid chat completions response") from e
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                logger.warning(
                    "Attempt %d/%d failed (%s): %s. Retrying in %ds...",
                    attempt + 1, _MAX_RETRIES, exc.__class__.__name__, exc, retry_delay,
                )
                time.sleep(retry_delay)
                continue

        raise RuntimeError(f"All {_MAX_RETRIES} retries exhausted (last error: {last_error})") from last_error

    # ─── 流式输出 ───────────────────────────────────────────────

    async def chat_stream(
        self, *, system: str, user: str
    ) -> AsyncGenerator[str, None]:
        """异步流式调用 LLM，yield 每个 token/chunk。"""
        if not self.is_configured():
            stub_text = chat_completion_stub(system=system, user=user)
            for char in stub_text:
                yield char
                await asyncio.sleep(0.01)
            return

        url = f"{self._base_url()}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.openai_chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
        }
        read_timeout = max(self._settings.openai_timeout_seconds, 300.0)
        timeout = httpx.Timeout(read_timeout, connect=30.0)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            retry_delay = _INITIAL_RETRY_DELAY * (2 ** attempt)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str in ("[DONE]", ""):
                                break
                            try:
                                data = __import__("json").loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if delta:
                                    yield delta
                            except Exception:
                                continue
                return
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                logger.warning(
                    "Stream attempt %d/%d failed (%s): %s. Retrying in %ds...",
                    attempt + 1, _MAX_RETRIES, exc.__class__.__name__, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                continue

        raise RuntimeError(f"All {_MAX_RETRIES} stream retries exhausted (last error: {last_error})") from last_error

    async def chat_stream_to_callback(
        self,
        *,
        system: str,
        user: str,
        on_token: collections.abc.Callable[[str], None],
    ) -> str:
        """流式调用 LLM，每收到一个 token 就调用 on_token 回调，最终返回完整文本。

        用于需要实时感知 token 的场景（如辩论的 SSE 流式输出）。
        on_token 回调在调用者的同步上下文中执行（不切换到其他协程）。
        """
        if not self.is_configured():
            stub_text = chat_completion_stub(system=system, user=user)
            for char in stub_text:
                on_token(char)
                await asyncio.sleep(0.01)
            return stub_text

        parts: list[str] = []
        async for token in self.chat_stream(system=system, user=user):
            parts.append(token)
            on_token(token)
        return "".join(parts)
