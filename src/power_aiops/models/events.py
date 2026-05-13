from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventSource(str, Enum):
    PROMETHEUS = "prometheus"
    ZABBIX = "zabbix"
    ELK = "elk"
    SNMP = "snmp"
    APM = "apm"
    MANUAL = "manual"


class EventObject(BaseModel):
    """Normalized alert/metric/log event (doc: 感知与告警)."""

    timestamp: datetime
    device_id: str = Field(..., description="Host / asset id")
    metric_type: str = Field(..., description="e.g. cpu_usage, log_error_rate")
    value: str | float | int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    source: EventSource = EventSource.MANUAL
