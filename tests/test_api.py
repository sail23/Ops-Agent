from fastapi.testclient import TestClient

from power_aiops.api.app import create_app


def test_health():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_post_incidents_demo():
    client = TestClient(create_app())
    r = client.post("/incidents/demo")
    assert r.status_code == 200
    data = r.json()
    assert data["incident_id"] == "INC-DEMO"
    assert data["trace_id"] == "trace-demo"
    assert data["code_blocked"] is False
    assert "shared_board" in data
    assert "ops_output" in data["shared_board"]


def test_post_incidents_run_with_fence():
    client = TestClient(create_app())
    r = client.post(
        "/incidents/run",
        json={
            "device_id": "h1",
            "metric_type": "x",
            "metadata": {"code_draft": "drop table users"},
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["code_blocked"] is True
    assert data["fence_matched"]
