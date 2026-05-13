from power_aiops.memory import InMemoryMessageLog
from power_aiops.models import (
    AgentMessage,
    AgentRef,
    MessagePriority,
    RoleKind,
)


def test_in_memory_filter_by_trace():
    log = InMemoryMessageLog()
    log.append(
        AgentMessage(
            sender=AgentRef(id="x", role=RoleKind.EXPERT),
            receiver=AgentRef(id="y", role=RoleKind.EXECUTOR),
            context_ref="c1",
            trace_id="t-a",
            priority=MessagePriority.NORMAL,
        )
    )
    log.append(
        AgentMessage(
            sender=AgentRef(id="x", role=RoleKind.EXPERT),
            receiver=AgentRef(id="y", role=RoleKind.EXECUTOR),
            context_ref="c2",
            trace_id="t-b",
            priority=MessagePriority.NORMAL,
        )
    )
    assert len(log.list_by_trace_id("t-a")) == 1
    assert len(log.list_by_trace_id("t-b")) == 1
    assert len(log) == 2
