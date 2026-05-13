from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from power_aiops.models.events import EventObject, EventSource
from power_aiops.models.incident import IncidentContext


class EventIn(BaseModel):
    """请求体中的单条事件；时间戳缺省为当前 UTC。"""

    timestamp: datetime | None = None
    device_id: str = Field(..., description="主机或资产 ID")
    metric_type: str = Field(..., description="指标或告警类型")
    value: str | float | int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    source: EventSource = EventSource.MANUAL


class IncidentRunRequest(BaseModel):
    """运行 pipeline 的请求体。"""

    incident_id: str | None = Field(default=None, description="缺省自动生成")
    trace_id: str | None = Field(default=None, description="缺省自动生成")
    summary: str = ""
    events: list[EventIn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 无 events 时可用简写生成一条事件
    device_id: str | None = None
    metric_type: str | None = None
    value: str | float | int | None = None
    source: EventSource | None = None
    # 报告导出选项（可选，流水线完成后由人工确认）
    export_after_completion: bool = Field(
        default=False,
        description="完成后是否自动导出报告（不推荐，服务端直接返回报告内容，前端自行决定导出格式）",
    )
    suggested_export_format: str | None = Field(
        default=None,
        description="建议的导出格式 (docx/pdf/none)，仅作为提示，报告内容始终在响应中返回",
    )


def build_incident_context(req: IncidentRunRequest) -> IncidentContext:
    """由请求构造 IncidentContext。"""
    incident_id = req.incident_id or f"INC-{uuid4().hex[:12]}"
    trace_id = req.trace_id or f"trace-{uuid4().hex}"

    if req.events:
        events: list[EventObject] = []
        for e in req.events:
            ts = e.timestamp or datetime.now(timezone.utc)
            events.append(
                EventObject(
                    timestamp=ts,
                    device_id=e.device_id,
                    metric_type=e.metric_type,
                    value=e.value,
                    raw_payload=e.raw_payload,
                    source=e.source,
                )
            )
    elif req.device_id and req.metric_type:
        src = req.source or EventSource.MANUAL
        events = [
            EventObject(
                timestamp=datetime.now(timezone.utc),
                device_id=req.device_id,
                metric_type=req.metric_type,
                value=req.value,
                source=src,
            )
        ]
    else:
        events = [
            EventObject(
                timestamp=datetime.now(timezone.utc),
                device_id="unspecified-host",
                metric_type="placeholder",
                value=None,
                source=EventSource.MANUAL,
            )
        ]

    return IncidentContext(
        incident_id=incident_id,
        trace_id=trace_id,
        summary=req.summary,
        events=events,
        metadata=dict(req.metadata),
    )


class IncidentRunResponse(BaseModel):
    incident_id: str
    trace_id: str
    code_blocked: bool
    fence_matched: str | None = None
    completed_steps: list[str]
    agent_outputs: dict[str, str]
    shared_board: dict[str, Any]
    # 报告内容（供前端保存，用于人工选择导出格式）
    report_content: str = ""
    # 导出建议（供前端显示导出选项）
    export_suggestions: list[str] = Field(
        default_factory=list,
        description="可选的导出格式建议: docx, pdf, html",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fault Case (Knowledge Base) Schemas
# ─────────────────────────────────────────────────────────────────────────────

class FaultCaseCreate(BaseModel):
    """创建故障案例的请求体."""

    case_id: str = Field(..., description="唯一案例ID，如 INC-2024-001")
    title: str = Field(..., description="案例标题")
    summary: str = Field(default="", description="故障概述")
    symptoms: list[str] = Field(default_factory=list, description="症状列表")
    services: list[str] = Field(default_factory=list, description="影响的服务")
    hosts: list[str] = Field(default_factory=list, description="涉及的主机")
    root_cause: str = Field(default="", description="根因分析")
    resolution: str = Field(default="", description="解决方案")
    severity: str = Field(default="P2", description="严重程度: P1/P2/P3/P4")
    duration_minutes: int = Field(default=0, description="持续时长(分钟)")
    tags: list[str] = Field(default_factory=list, description="标签")
    metadata: dict[str, Any] = Field(default_factory=dict)


class FaultCaseUpdate(BaseModel):
    """更新故障案例的请求体."""

    title: str | None = None
    summary: str | None = None
    symptoms: list[str] | None = None
    services: list[str] | None = None
    hosts: list[str] | None = None
    root_cause: str | None = None
    resolution: str | None = None
    severity: str | None = None
    duration_minutes: int | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


class FaultCaseResponse(BaseModel):
    """故障案例响应."""

    case_id: str
    title: str
    summary: str
    symptoms: list[str]
    services: list[str]
    hosts: list[str]
    root_cause: str
    resolution: str
    severity: str
    duration_minutes: int
    created_at: str
    tags: list[str]


class SimilarCaseResponse(BaseModel):
    """相似案例搜索结果."""

    case_id: str
    title: str
    severity: str
    matched_symptoms: list[dict]
    resolution: str | None = None
    similarity_score: float = 0.0
    created_at: str | None = None


class KnowledgeBaseStats(BaseModel):
    """知识库统计."""

    total_cases: int
    total_symptoms: int
    total_root_causes: int
    total_services: int


# ─────────────────────────────────────────────────────────────────────────────
# Debate Schemas (Stage 1: Ops + SRE × 2 rounds + Report)
# ─────────────────────────────────────────────────────────────────────────────


class DebateRunRequest(BaseModel):
    """辩论模式请求体（与 IncidentRunRequest 结构一致）。"""

    incident_id: str | None = Field(default=None, description="缺省自动生成")
    trace_id: str | None = Field(default=None, description="缺省自动生成")
    summary: str = ""
    events: list[EventIn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    device_id: str | None = None
    metric_type: str | None = None
    value: str | float | int | None = None
    source: EventSource | None = None
    # 辩论参数（覆盖配置默认值）
    max_rounds: int | None = Field(default=None, description="最大辩论轮次")
    max_turns: int | None = Field(default=None, description="最大总发言次数")


class DebateTurnItem(BaseModel):
    """辩论中一轮发言的摘要."""

    turn_id: int
    round: str
    role: str
    agent_id: str
    content_preview: str = ""
    reasoning_preview: str = ""
    msg_type: str
    timestamp: str
    success: bool = True
    error: str = ""


class DebateRunResponse(BaseModel):
    """辩论执行结果响应."""

    incident_id: str
    trace_id: str = ""
    # 辩论统计
    total_turns: int = 0
    total_rounds: int = 0
    llm_calls: int = 0
    # 结论
    conclusion: str = ""
    report_text: str = ""
    # Code Agent 输出
    code_script: str = ""
    code_executed: bool = False
    code_output: str = ""
    # 争议点
    disputed_points: list[str] = Field(default_factory=list)
    # 辩论历史
    turns: list[DebateTurnItem] = Field(default_factory=list)
    # 状态
    terminated: bool = False
    termination_reason: str = ""
    convergence_score: float = 0.0
    human_approved: bool = False
    # 报告导出建议（供前端显示导出选项）
    export_suggestions: list[str] = Field(
        default_factory=list,
        description="可选的导出格式建议: docx, pdf, html",
    )


class DebateReportExportRequest(BaseModel):
    """辩论报告导出请求"""

    incident_id: str | None = Field(default=None)
    format: str = Field(default="docx")
    output_path: str | None = Field(default=None)


class DebateReportExportResponse(BaseModel):
    """辩论报告导出响应"""

    success: bool
    incident_id: str
    format: str
    path: str | None = None
    message: str
    content_type: str = ""
