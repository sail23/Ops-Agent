from __future__ import annotations

from typing import AsyncGenerator

from power_aiops.agents import (
    DynamicCodeAgent,
    OpsAgent,
    ReportAgent,
    SREAgent,
)
from power_aiops.agents.base import AgentStreamChunk, BaseAgent
from power_aiops.memory.long_term import LongTermMemory
from power_aiops.memory.message_log import MessageLog
from power_aiops.memory.shared_board import (
    BOARD_KEY_CODE_BLOCKED,
    BOARD_KEY_FENCE_MATCHED,
    SharedBoard,
)
from power_aiops.memory.short_term import ShortTermMemory
from power_aiops.models.incident import IncidentContext
from power_aiops.models.messages import (
    AgentMessage,
    AgentRef,
    MessagePayload,
    MessagePriority,
    RoleKind,
)
from power_aiops.orchestration.state import PipelineState


def _create_default_agents(
    board: SharedBoard,
    long_term_memory: LongTermMemory | None = None,
) -> tuple[BaseAgent, BaseAgent, BaseAgent, BaseAgent]:
    """Create the default 4-agent pipeline tuple.

    Initializes LongTermMemory if not provided; SREAgent gets it for hybrid RAG.
    CodeAgent uses DynamicCodeAgent.
    """
    if long_term_memory is None:
        long_term_memory = LongTermMemory()
    return (
        OpsAgent(board=board),
        SREAgent(board=board, long_term_memory=long_term_memory),
        DynamicCodeAgent(board=board),
        ReportAgent(board=board),
    )


def run_pipeline(
    ctx: IncidentContext,
    *,
    board: SharedBoard | None = None,
    memory: ShortTermMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    message_log: MessageLog | None = None,
    agents: tuple[BaseAgent, BaseAgent, BaseAgent, BaseAgent] | None = None,
) -> PipelineState:
    """
    Ops → SRE → Code → Report；每步写入 ShortTermMemory 与 SharedBoard。
    SharedBoard 在 Agent 间共享上下文，实现协作推理。
    Code 被围栏拦截时，state.code_blocked=True，仍继续 Report。
    若传入 `message_log`，每步追加一条 `AgentMessage`（handoff 语义）。

    LongTermMemory (混合 RAG) 在 SREAgent 内部使用，自动检索相似历史案例。
    使用 DynamicCodeAgent 执行代码分析步骤。
    """
    board = board or SharedBoard()
    memory = memory or ShortTermMemory()

    if agents is None:
        agents = _create_default_agents(board, long_term_memory)

    agent_list = list(agents)
    step_labels = ("ops", "sre", "code", "report")
    state = PipelineState(incident_id=ctx.incident_id, trace_id=ctx.trace_id)

    for i, (label, agent) in enumerate(zip(step_labels, agent_list, strict=True)):
        result = agent.run(ctx)
        state.agent_outputs[result.agent_id] = result.content
        state.completed_steps.append(label)
        memory.append(result.agent_id, result.content)

        if label == "code":
            state.code_blocked = result.blocked
            state.fence_matched = result.fence_matched
            board.set(BOARD_KEY_CODE_BLOCKED, result.blocked)
            board.set(BOARD_KEY_FENCE_MATCHED, result.fence_matched)

        if message_log is not None:
            next_id = agent_list[i + 1].agent_id if i + 1 < len(agent_list) else "pipeline-terminal"
            pri = MessagePriority.HIGH if label == "code" and result.blocked else MessagePriority.NORMAL
            message_log.append(
                AgentMessage(
                    sender=AgentRef(id=result.agent_id, role=RoleKind.EXPERT),
                    receiver=AgentRef(id=next_id, role=RoleKind.EXECUTOR),
                    context_ref=ctx.incident_id,
                    trace_id=ctx.trace_id,
                    priority=pri,
                    payload=MessagePayload(
                        task_description=f"pipeline_step:{label}",
                        constraints="",
                        extra={
                            "output_excerpt": result.content[:4000],
                            "blocked": str(result.blocked),
                        },
                    ),
                )
            )

    # Final board snapshot in state
    state.board_snapshot = board.snapshot()
    return state


async def stream_pipeline(
    ctx: IncidentContext,
    *,
    board: SharedBoard | None = None,
    memory: ShortTermMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    agents: tuple[BaseAgent, BaseAgent, BaseAgent, BaseAgent] | None = None,
) -> AsyncGenerator[AgentStreamChunk, None]:
    """
    流式执行 Pipeline，yield 每个 Agent 的每个 token/chunk。
    每个 Agent 完成时 is_done=True。

    LongTermMemory (混合 RAG) 在 SREAgent 内部使用，自动检索相似历史案例。
    使用 DynamicCodeAgent 执行代码分析步骤。
    """
    board = board or SharedBoard()
    memory = memory or ShortTermMemory()

    if agents is None:
        agents = _create_default_agents(board, long_term_memory)

    agent_list = list(agents)
    step_labels = ("ops", "sre", "code", "report")
    code_blocked = False
    fence_matched = None

    for i, (label, agent) in enumerate(zip(step_labels, agent_list, strict=True)):
        # 使用流式方法
        async for chunk in agent.stream_run(ctx):
            # 最后一个 chunk 表示完成
            if chunk.is_done:
                memory.append(chunk.agent_id, board.get(f"{label}_output", ""))
                if label == "code":
                    code_blocked = chunk.blocked
                    fence_matched = chunk.fence_matched
                    board.set(BOARD_KEY_CODE_BLOCKED, code_blocked)
                    board.set(BOARD_KEY_FENCE_MATCHED, fence_matched)
            else:
                yield chunk
