from datetime import datetime, timezone

from power_aiops.memory import InMemoryMessageLog, SharedBoard, ShortTermMemory
from power_aiops.models import EventObject, EventSource, IncidentContext
from power_aiops.orchestration import (
    BOARD_KEY_CODE,
    BOARD_KEY_CODE_BLOCKED,
    BOARD_KEY_OPS,
    BOARD_KEY_REPORT,
    BOARD_KEY_SRE,
    run_pipeline,
)


def _ctx(**metadata) -> IncidentContext:
    return IncidentContext(
        incident_id="INC-P-1",
        trace_id="trace-p-1",
        events=[
            EventObject(
                timestamp=datetime.now(timezone.utc),
                device_id="db-1",
                metric_type="latency_ms",
                value=800,
                source=EventSource.PROMETHEUS,
            )
        ],
        metadata=dict(metadata),
    )


def test_pipeline_writes_board_keys():
    board = SharedBoard()
    mem = ShortTermMemory()
    state = run_pipeline(_ctx(), board=board, memory=mem)

    snap = board.snapshot()
    assert BOARD_KEY_OPS in snap
    assert BOARD_KEY_SRE in snap
    assert BOARD_KEY_CODE in snap
    assert BOARD_KEY_REPORT in snap
    assert snap[BOARD_KEY_CODE_BLOCKED] is False
    assert state.code_blocked is False
    assert len(mem.recent()) == 4


def test_pipeline_marks_blocked_when_code_draft_unsafe():
    board = SharedBoard()
    state = run_pipeline(
        _ctx(code_draft="rm -rf /data"),
        board=board,
    )
    assert state.code_blocked is True
    assert state.fence_matched
    assert board.get(BOARD_KEY_CODE_BLOCKED) is True
    # Report 仍执行，board 上应有 report 输出
    assert BOARD_KEY_REPORT in board.snapshot()


def test_pipeline_optional_message_log():
    log = InMemoryMessageLog()
    run_pipeline(_ctx(), message_log=log)
    msgs = log.list_by_trace_id("trace-p-1")
    assert len(msgs) == 4
    assert msgs[0].trace_id == "trace-p-1"
    assert msgs[-1].receiver.id == "pipeline-terminal"
