from power_aiops.models import (
    AgentMessage,
    AgentRef,
    MessagePayload,
    MessagePriority,
    RoleKind,
    agent_message_from_json_dict,
    agent_message_to_json_dict,
)


def test_agent_message_json_roundtrip():
    m = AgentMessage(
        sender=AgentRef(id="a", role=RoleKind.EXPERT),
        receiver=AgentRef(id="b", role=RoleKind.EXECUTOR),
        context_ref="INC-1",
        trace_id="tr-1",
        priority=MessagePriority.HIGH,
        payload=MessagePayload(task_description="t", constraints="c", extra={"k": 1}),
    )
    d = agent_message_to_json_dict(m)
    assert isinstance(d["timestamp"], str)
    m2 = agent_message_from_json_dict(d)
    assert m2.msg_id == m.msg_id
    assert m2.trace_id == "tr-1"
    assert m2.payload.extra["k"] == 1
