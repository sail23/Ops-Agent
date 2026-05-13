from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from power_aiops.models.events import EventObject


class IncidentContext(BaseModel):
    """Incident context with all fields populated during pipeline execution."""

    incident_id: str
    trace_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[EventObject] = Field(default_factory=list)
    summary: str = ""

    # Pipeline-filled fields
    title: str = ""                    # 简短标题，ops 或 sre 生成
    description: str = ""             # 详细描述
    symptoms: list[str] = Field(default_factory=list)      # 症状列表
    root_cause: str = ""              # 根因分析
    resolution: str = ""             # 解决方案/处置方案
    severity: str = ""                # 严重程度 (critical/high/medium/low)

    # Service info (optional)
    service_name: str = ""            # 关联服务
    occurred_at: datetime | None = None  # 故障发生时间

    # Metadata
    shared_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def update_from_pipeline(
        self,
        title: str | None = None,
        description: str | None = None,
        symptoms: list[str] | None = None,
        root_cause: str | None = None,
        resolution: str | None = None,
        severity: str | None = None,
        service_name: str | None = None,
    ) -> None:
        """Update fields from pipeline output.

        Call this after each agent completes to progressively fill
        in the incident context for persistence.
        """
        if title is not None:
            self.title = title
        if description is not None:
            self.description = description
        if symptoms is not None:
            self.symptoms = symptoms
        if root_cause is not None:
            self.root_cause = root_cause
        if resolution is not None:
            self.resolution = resolution
        if severity is not None:
            self.severity = severity
        if service_name is not None:
            self.service_name = service_name
