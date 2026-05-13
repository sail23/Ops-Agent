from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator

from power_aiops.models.incident import IncidentContext


@dataclass
class AgentResult:
    """单次 Agent 执行结果（占位；后续可扩展 token 用量、工具调用等）。"""

    agent_id: str
    content: str
    blocked: bool = False
    fence_matched: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentStreamChunk:
    """流式输出块"""
    agent_id: str
    delta: str  # 新增的文本片段
    is_done: bool = False  # 是否完成
    blocked: bool = False
    fence_matched: str | None = None


class BaseAgent(ABC):
    """统一入口：run(IncidentContext) -> AgentResult。B2 不接 LLM，仅占位逻辑。"""

    @property
    @abstractmethod
    def agent_id(self) -> str:
        ...

    @abstractmethod
    def run(self, ctx: IncidentContext) -> AgentResult:
        ...

    async def stream_run(self, ctx: IncidentContext) -> AsyncGenerator[AgentStreamChunk, None]:
        """
        流式执行，返回每个 chunk。
        默认实现：调用 run() 并 yield 完整内容。
        子类可覆盖以实现真正的 token 级流式。
        """
        result = self.run(ctx)
        yield AgentStreamChunk(
            agent_id=self.agent_id,
            delta=result.content,
            is_done=True,
            blocked=result.blocked,
            fence_matched=result.fence_matched,
        )
