from datetime import datetime, timezone

from power_aiops.agents import (
    CodeAgent,
    DynamicCodeAgent,
    OpsAgent,
    ReportAgent,
    SREAgent,
)
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.models import EventObject, EventSource, IncidentContext


def _minimal_ctx(**metadata) -> IncidentContext:
    return IncidentContext(
        incident_id="INC-001",
        trace_id="trace-test",
        events=[
            EventObject(
                timestamp=datetime.now(timezone.utc),
                device_id="host-a",
                metric_type="cpu",
                value=95,
                source=EventSource.PROMETHEUS,
            )
        ],
        metadata=dict(metadata),
    )


def test_run_all_agents_sequential():
    board = SharedBoard()
    ctx = _minimal_ctx()
    for agent in (OpsAgent(board), SREAgent(board), CodeAgent(board), ReportAgent(board)):
        r = agent.run(ctx)
        assert r.agent_id
        assert r.content
        assert r.blocked is False


def test_code_agent_fence_blocks_rm_rf():
    board = SharedBoard()
    ctx = _minimal_ctx(code_draft="sudo rm -rf /var/tmp")
    r = CodeAgent(board).run(ctx)
    assert r.blocked is True
    assert r.fence_matched


def test_code_agent_allows_safe_script():
    board = SharedBoard()
    ctx = _minimal_ctx()
    r = CodeAgent(board).run(ctx)
    assert r.blocked is False
    assert "sandbox" in r.content


def test_dynamic_code_agent_generates_code():
    """Test that DynamicCodeAgent generates Python analysis code."""
    board = SharedBoard()
    ctx = _minimal_ctx()
    agent = DynamicCodeAgent(board, use_llm=False)  # Use fallback code
    r = agent.run(ctx)
    assert r.agent_id == "DynamicCode-Agent-01"
    assert r.content
    # Should contain generated code
    assert "故障分析报告" in r.content or "python" in r.content.lower()


def test_dynamic_code_agent_fence_blocks():
    """Test that DynamicCodeAgent also respects fence checks."""
    board = SharedBoard()
    ctx = _minimal_ctx(code_draft="rm -rf /important")
    agent = DynamicCodeAgent(board, use_llm=False)
    r = agent.run(ctx)
    # Even with fallback code, fence should be checked
    assert r.blocked is True or r.fence_matched is not None


def test_dynamic_code_agent_execution_disabled_by_default():
    """Test that code execution is disabled by default."""
    board = SharedBoard()
    agent = DynamicCodeAgent(board)
    assert agent._enable_execution is False
    # Default should not execute code
    ctx = _minimal_ctx()
    r = agent.run(ctx)
    assert "执行" not in r.content or "执行结果" not in r.content


def test_dynamic_code_agent_execution_enabled():
    """Test DynamicCodeAgent with execution enabled."""
    board = SharedBoard()
    agent = DynamicCodeAgent(board, enable_execution=True, execution_timeout=5)
    ctx = _minimal_ctx()
    r = agent.run(ctx)
    assert r.agent_id == "DynamicCode-Agent-01"
    assert r.content
    # Should have execution metadata
    assert r.meta.get("execution_enabled") is True
