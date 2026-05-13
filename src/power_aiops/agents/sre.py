"""SRE Agent with Hybrid RAG (Graph + Vector) for historical case retrieval."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from power_aiops.agents.base import AgentResult, AgentStreamChunk, BaseAgent
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.long_term import LongTermMemory
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.models.incident import IncidentContext
from power_aiops.prompts import SYSTEM_PROMPT_SRE_AGENT

if TYPE_CHECKING:
    from power_aiops.memory.long_term import LongTermMemory

logger = logging.getLogger(__name__)

# SharedBoard keys
BOARD_KEY_OPS = "ops_output"
BOARD_KEY_SRE = "sre_output"
BOARD_KEY_CODE = "code_output"
BOARD_KEY_REPORT = "report_output"
BOARD_KEY_GRAPH_CONTEXT = "graph_rag_context"


class SREAgent(BaseAgent):
    """SRE 架构 Agent：方案与推演；集成混合 RAG 检索历史案例。

    在生成方案前，会检索相似的历史故障案例作为参考。
    支持 Graph RAG（Neo4j）+ Vector RAG（Chroma）混合检索。
    """

    def __init__(
        self,
        board: SharedBoard,
        *,
        use_llm: bool = True,
        llm: OpenAICompatibleClient | None = None,
        long_term_memory: LongTermMemory | None = None,
        enable_rag: bool = True,
        top_k: int = 3,
    ) -> None:
        self._use_llm = use_llm
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._board = board
        self._long_term_memory = long_term_memory
        self._enable_rag = enable_rag
        self._top_k = top_k

    @property
    def agent_id(self) -> str:
        return "SRE-Agent-01"

    @property
    def long_term_memory(self) -> LongTermMemory | None:
        return self._long_term_memory

    def _retrieve_similar_cases(self, ctx: IncidentContext) -> str:
        """Retrieve similar historical cases using hybrid search.

        Uses LongTermMemory.hybrid_search() to combine Graph RAG and Vector RAG results.

        Returns:
            Formatted context string for injection into prompt
        """
        if not self._enable_rag:
            return ""

        if self._long_term_memory is None:
            try:
                self._long_term_memory = LongTermMemory()
                self._long_term_memory.initialize()
            except Exception as e:
                logger.warning(f"Failed to initialize LongTermMemory: {e}")
                return ""

        try:
            # Build search query from incident context
            search_text = self._build_search_query(ctx)
            logger.info(f"Hybrid searching similar cases with: {search_text[:100]}...")

            # Perform hybrid search (Graph + Vector RAG with RRF fusion)
            results = self._long_term_memory.hybrid_search(
                incident=ctx,
                top_k=self._top_k,
                graph_weight=0.4,
                vector_weight=0.6,
            )

            if not results:
                logger.info("No similar cases found in hybrid search")
                return ""

            # Format results for prompt injection
            context_parts = [
                "\n\n## 历史相似故障案例参考",
                "(以下案例来自知识库（Graph + Vector 混合检索），可作为处置参考)",
            ]

            for i, r in enumerate(results, 1):
                incident_id = r.get("incident_id", "N/A")
                title = r.get("title", f"案例 {incident_id}")

                context_parts.append(f"\n### 案例 {i}: {title}")
                context_parts.append(f"- 案例ID: {incident_id}")

                # Source info
                sources = []
                if r.get("graph_details"):
                    sources.append("Graph RAG")
                if r.get("vector_details"):
                    sources.append("Vector RAG")
                if sources:
                    context_parts.append(f"- 检索来源: {', '.join(sources)}")

                # Scores
                graph_score = r.get("graph_score", 0)
                vector_score = r.get("vector_score", 0)
                total_score = r.get("total_score", 0)
                if total_score > 0:
                    context_parts.append(f"- 相似度: Graph {graph_score:.3f}, Vector {vector_score:.3f}, 综合 {total_score:.3f}")

                # Root cause
                root_cause = r.get("root_cause", "")
                if root_cause:
                    context_parts.append(f"- 根因: {root_cause[:150]}...")

                # Symptoms (from vector details)
                if r.get("vector_details"):
                    symptoms = r.get("vector_details", {}).get("symptoms", [])
                    # Handle both list and string types
                    if isinstance(symptoms, str):
                        symptoms = [s.strip() for s in symptoms.split("|") if s.strip()]
                    if symptoms:
                        context_parts.append(f"- 相似症状: {', '.join(symptoms[:3])}")

            context_str = "\n".join(context_parts)

            # Store in board for other agents to access
            self._board.set(BOARD_KEY_GRAPH_CONTEXT, context_str)

            logger.info(f"Retrieved {len(results)} similar cases from hybrid search")
            return context_str

        except Exception as e:
            logger.warning(f"Hybrid search retrieval failed: {e}")
            return ""

    def _build_search_query(self, ctx: IncidentContext) -> str:
        """Build search query from incident context."""
        parts = []

        # Add summary if available
        if ctx.summary:
            parts.append(ctx.summary)

        # Add title if available
        if ctx.title:
            parts.append(ctx.title)

        # Add symptom descriptions from events
        for event in ctx.events[:5]:
            if hasattr(event, "description") and event.description:
                parts.append(event.description)
            elif hasattr(event, "metric_type"):
                parts.append(f"{event.metric_type}: {event.value}")

        # Add explicit symptoms
        if ctx.symptoms:
            parts.extend(ctx.symptoms)

        return " ".join(parts) if parts else ctx.summary or ctx.incident_id

    def run(self, ctx: IncidentContext) -> AgentResult:
        # Retrieve similar cases before analysis
        rag_context = self._retrieve_similar_cases(ctx)

        # Build context with incident + rag context
        user = self._user_prompt(ctx, rag_context)

        if self._use_llm:
            text = self._llm.chat(system=SYSTEM_PROMPT_SRE_AGENT, user=user)
            meta = {"llm": "openai-compatible" if self._llm.is_configured() else "stub"}
        else:
            text = self._placeholder(ctx)
            meta = {"llm": "stub"}

        if rag_context:
            meta["rag_used"] = True
            meta["rag_type"] = "hybrid"
            meta["similar_cases_count"] = len(rag_context.split("### 案例")) - 1 if rag_context else 0

        # Write to board for downstream agents
        self._board.set(BOARD_KEY_SRE, text)

        return AgentResult(agent_id=self.agent_id, content=text, meta=meta)

    def _user_prompt(self, ctx: IncidentContext, rag_context: str = "") -> str:
        lines = [
            "## 故障信息",
            f"- incident_id: {ctx.incident_id}",
            f"- trace_id: {ctx.trace_id}",
            f"- summary: {ctx.summary or '待确认'}",
            f"- 关联告警数: {len(ctx.events)}",
        ]

        # Append Ops-Agent output if available
        ops_output = self._board.get(BOARD_KEY_OPS)
        if ops_output:
            lines.append("\n## Ops-Agent 初步分析（已阅）")
            lines.append(f"{ops_output[:3000]}")

        # Inject RAG context if available
        if rag_context:
            lines.append(rag_context)

        if ctx.events:
            lines.append("\n## 关联告警详情")
            for ev in ctx.events[:10]:
                lines.append(
                    f"- [{ev.source.value}] {ev.metric_type}: {ev.value} "
                    f"(@ {ev.timestamp.strftime('%Y-%m-%d %H:%M:%S')})"
                )

        return "\n".join(lines)

    def _placeholder(self, ctx: IncidentContext) -> str:
        ops_output = self._board.get(BOARD_KEY_OPS, "")
        rag_context = self._board.get(BOARD_KEY_GRAPH_CONTEXT, "")

        lines = [
            "[SRE-Agent] 基于 Ops-Agent 分析与历史案例，制定架构方案。",
            "## 处置思路草案",
            "主路径：隔离影响面 → 验证备机 → 按窗口变更。",
            "回滚：恢复上一版本配置并观察指标。",
            "## 方案前置条件",
            "1. 确认备机可用性",
            "2. 评估业务影响窗口",
            "3. 准备回滚方案",
        ]

        if rag_context:
            lines.insert(1, f"\n参考历史案例:\n{rag_context[:500]}...")

        return "\n".join(lines)

    async def stream_run(self, ctx: IncidentContext) -> AsyncGenerator[AgentStreamChunk, None]:
        """流式执行 SRE-Agent，实时 yield 每个 token/chunk。"""
        # Retrieve similar cases before analysis
        rag_context = self._retrieve_similar_cases(ctx)

        user = self._user_prompt(ctx, rag_context)
        self._board.set(BOARD_KEY_SRE, "")

        if self._use_llm and self._llm.is_configured():
            parts = []
            async for delta in self._llm.chat_stream(system=SYSTEM_PROMPT_SRE_AGENT, user=user):
                parts.append(delta)
                self._board.set(BOARD_KEY_SRE, "".join(parts))
                yield AgentStreamChunk(agent_id=self.agent_id, delta=delta, is_done=False)

            yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True)
        else:
            text = self._placeholder(ctx)
            import asyncio

            for char in text:
                await asyncio.sleep(0.015)
                yield AgentStreamChunk(agent_id=self.agent_id, delta=char, is_done=False)

            self._board.set(BOARD_KEY_SRE, text)
            yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True)
