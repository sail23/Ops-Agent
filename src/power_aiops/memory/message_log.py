from __future__ import annotations

from threading import RLock
from typing import Protocol, runtime_checkable

from power_aiops.models.messages import AgentMessage


@runtime_checkable
class MessageLog(Protocol):
    """消息持久化抽象：生产可换 Redis Stream / Kafka / DB。"""

    def append(self, msg: AgentMessage) -> None:
        ...

    def list_by_trace_id(self, trace_id: str) -> list[AgentMessage]:
        ...


class InMemoryMessageLog:
    """进程内实现，便于开发与单测。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: list[AgentMessage] = []

    def append(self, msg: AgentMessage) -> None:
        with self._lock:
            self._items.append(msg)

    def list_by_trace_id(self, trace_id: str) -> list[AgentMessage]:
        with self._lock:
            return [m for m in self._items if m.trace_id == trace_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
