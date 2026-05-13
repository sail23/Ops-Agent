import power_aiops
from power_aiops.models import EventObject, EventSource
from power_aiops.security import fence_check_text


def test_version():
    assert power_aiops.__version__ == "0.1.0"


def test_fence_blocks_rm_rf():
    r = fence_check_text("please run rm -rf / on db")
    assert r.allowed is False


def test_event_object():
    from datetime import datetime, timezone

    e = EventObject(
        timestamp=datetime.now(timezone.utc),
        device_id="host-1",
        metric_type="cpu",
        value=99.0,
        source=EventSource.PROMETHEUS,
    )
    assert e.device_id == "host-1"
