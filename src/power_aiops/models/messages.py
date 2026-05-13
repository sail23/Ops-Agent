from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MessagePriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class RoleKind(str, Enum):
    COORDINATOR = "Coordinator"
    EXPERT = "Expert"
    EXECUTOR = "Executor"
    AUDITOR = "Auditor"


class AgentRef(BaseModel):
    id: str
    role: RoleKind


class MessagePayload(BaseModel):
    task_description: str = ""
    constraints: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """Table 2: inter-agent message envelope."""

    msg_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: AgentRef
    receiver: AgentRef
    priority: MessagePriority = MessagePriority.NORMAL
    context_ref: str = Field(..., description="Incident id, e.g. INCIDENT-20260223-001")
    trace_id: str = Field(..., description="End-to-end fault handling trace")
    payload: MessagePayload = Field(default_factory=MessagePayload)
    deadline_ms: int | None = Field(default=None, description="Optional SLA for response")


def agent_message_to_json_dict(msg: AgentMessage) -> dict[str, Any]:
    """JSON 可序列化字典（datetime → ISO 字符串）。"""
    return msg.model_dump(mode="json")


def agent_message_from_json_dict(data: dict[str, Any]) -> AgentMessage:
    """从 `agent_message_to_json_dict` 或等价 JSON 反序列化。"""
    return AgentMessage.model_validate(data)
