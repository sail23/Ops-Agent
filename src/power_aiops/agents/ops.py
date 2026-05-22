from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from power_aiops.agents.base import AgentResult, AgentStreamChunk, BaseAgent
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.shared_board import (
    BOARD_KEY_OPS,
    SharedBoard,
)
from power_aiops.models.incident import IncidentContext
from power_aiops.prompts import SYSTEM_PROMPT_OPS_AGENT


class OpsAgent(BaseAgent):
    """运维专家：初判与协调；默认启用 LLM，结果写入 SharedBoard。"""

    def __init__(
        self,
        board: SharedBoard,
        *,
        use_llm: bool = True,
        llm: OpenAICompatibleClient | None = None,
    ) -> None:
        self._use_llm = use_llm
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._board = board

    @property
    def agent_id(self) -> str:
        return "Ops-Agent-01"

    def run(self, ctx: IncidentContext) -> AgentResult:
        # Build context from incident
        user = self._user_prompt(ctx)

        if self._use_llm:
            text = self._llm.chat(system=SYSTEM_PROMPT_OPS_AGENT, user=user)
            meta = {"llm": "openai-compatible" if self._llm.is_configured() else "stub"}
        else:
            text = self._placeholder(ctx)
            meta = {"llm": "stub"}

        # Write to board for downstream agents
        self._board.set(BOARD_KEY_OPS, text)

        return AgentResult(agent_id=self.agent_id, content=text, meta=meta)

    def _user_prompt(self, ctx: IncidentContext) -> str:
        lines = [
            "## 故障信息",
            f"- incident_id: {ctx.incident_id}",
            f"- trace_id: {ctx.trace_id}",
            f"- summary: {ctx.summary or '待确认'}",
            f"- 关联告警数: {len(ctx.events)}",
        ]

        if ctx.events:
            lines.append("\n## 关联告警详情")
            for ev in ctx.events[:10]:
                lines.append(
                    f"- [{ev.source.value}] {ev.metric_type}: {ev.value} "
                    f"(@ {ev.timestamp.strftime('%Y-%m-%d %H:%M:%S')})"
                )

        if ctx.shared_notes:
            lines.append("\n## 已有备注")
            for note in ctx.shared_notes[:5]:
                lines.append(f"- {note}")

        return "\n".join(lines)

    def _placeholder(self, ctx: IncidentContext) -> str:
        lines = [
            f"[Ops-Agent] 故障 {ctx.incident_id}，关联事件 {len(ctx.events)} 个。",
            "## 初步假设",
            "1. 需结合监控数据做进一步分析",
            "2. 建议召集 SRE-Agent 进行深度诊断",
            "3. 同步通知值班人员",
            "## 任务分发",
            "- SRE-Agent: 架构层面分析",
            "- Code-Agent: 准备诊断脚本",
        ]
        return "\n".join(lines)

    async def stream_run(self, ctx: IncidentContext) -> AsyncGenerator[AgentStreamChunk, None]:
        """
        流式执行 Ops-Agent，实时 yield 每个 token 片段。
        """
        user = self._user_prompt(ctx)
        self._board.set(BOARD_KEY_OPS, "")  # 初始化

        if self._use_llm and self._llm.is_configured():
            # 真正的流式输出
            parts = []
            async for delta in self._llm.chat_stream(system=SYSTEM_PROMPT_OPS_AGENT, user=user):
                parts.append(delta)
                self._board.set(BOARD_KEY_OPS, "".join(parts))  # 实时更新 board
                yield AgentStreamChunk(
                    agent_id=self.agent_id,
                    delta=delta,
                    is_done=False,
                )

            yield AgentStreamChunk(
                agent_id=self.agent_id,
                delta="",
                is_done=True,
                blocked=False,
                fence_matched=None,
            )
        else:
            # Stub 模式：模拟打字效果
            text = self._placeholder(ctx)
            for char in text:
                await asyncio.sleep(0.015)  # 模拟打字速度
                yield AgentStreamChunk(
                    agent_id=self.agent_id,
                    delta=char,
                    is_done=False,
                )
            self._board.set(BOARD_KEY_OPS, text)
            yield AgentStreamChunk(
                agent_id=self.agent_id,
                delta="",
                is_done=True,
            )
