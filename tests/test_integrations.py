from power_aiops.integrations import (
    fetch_elk_events_stub,
    fetch_prometheus_events_stub,
    map_elk_hit_to_event,
    map_prometheus_sample_to_event,
)
from power_aiops.models.events import EventSource


def test_stubs_return_empty():
    assert fetch_prometheus_events_stub() == []
    assert fetch_elk_events_stub() == []


def test_map_prometheus_sample():
    ev = map_prometheus_sample_to_event(
        metric_labels={"__name__": "up", "instance": "10.0.0.1:9100", "job": "node"},
        sample_value_str="1",
        timestamp_unix=1700000000.0,
    )
    assert ev.source == EventSource.PROMETHEUS
    assert ev.device_id == "10.0.0.1:9100"
    assert ev.metric_type == "up"
    assert ev.value == 1


def test_map_elk_hit():
    ev = map_elk_hit_to_event(
        source={
            "@timestamp": "2026-03-30T12:00:00.000Z",
            "host": {"name": "db-1"},
            "log": {"level": "error"},
            "message": "connection timeout",
        },
    )
    assert ev.source == EventSource.ELK
    assert ev.device_id == "db-1"
    assert "error" in ev.metric_type
    assert ev.timestamp.tzinfo is not None
