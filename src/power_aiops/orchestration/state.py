from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PipelineState(BaseModel):
    """线性编排一次运行后的可序列化状态（不含不可 JSON 的运行时对象）。"""

    incident_id: str
    trace_id: str
    code_blocked: bool = False
    fence_matched: str | None = None
    """若 Code 被围栏拦截，对应匹配片段。"""
    agent_outputs: dict[str, str] = Field(
        default_factory=dict,
        description="agent_id -> 输出全文",
    )
    completed_steps: list[str] = Field(
        default_factory=list,
        description="已执行步骤标签，顺序与 pipeline 一致",
    )
    board_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Pipeline 结束时 SharedBoard 快照",
    )
