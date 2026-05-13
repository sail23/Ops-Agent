"""
Prometheus HTTP client with retry, connection pooling, and mock fallback.

Real mode: calls live Prometheus API (configure via .env).
Mock mode: returns realistic alert/metric data for local development/testing.

Usage:
    from power_aiops.integrations.prometheus import get_prometheus_client

    client = get_prometheus_client()  # auto-detects mock mode from settings
    alerts = client.fetch_alerts()
    metrics = client.query("up")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from power_aiops.config import get_settings
from power_aiops.models.events import EventObject, EventSource

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0


@dataclass
class PrometheusClientConfig:
    """Prometheus connection configuration (built from Settings)."""

    base_url: str = "http://127.0.0.1:9090"
    bearer_token: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    verify_tls: bool = True
    timeout_seconds: int = 10
    max_retries: int = _MAX_RETRIES

    @classmethod
    def from_settings(cls) -> "PrometheusClientConfig":
        s = get_settings()
        return cls(
            base_url=s.prometheus_base_url.rstrip("/"),
            bearer_token=s.prometheus_bearer_token or None,
            verify_tls=s.prometheus_verify_tls,
            timeout_seconds=s.prometheus_timeout_seconds,
        )


# ── shared HTTP retry helper ──────────────────────────────────────────────

def _post_with_retry(
    client: httpx.Client,
    url: str,
    timeout: float,
    max_retries: int = _MAX_RETRIES,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = _RETRY_BACKOFF ** (attempt + 1)
                logger.debug(f"Prometheus retry {attempt+1}/{max_retries} after {wait:.1f}s")
                time.sleep(wait)
    raise RuntimeError(f"Prometheus API failed after {max_retries} retries") from last_exc


# ── mapping utility ───────────────────────────────────────────────────────

def map_prometheus_sample_to_event(
    *,
    metric_labels: dict[str, str],
    sample_value_str: str,
    timestamp_unix: float,
    raw: dict[str, Any] | None = None,
) -> EventObject:
    """Map a single Prometheus metric sample to an EventObject."""
    ts = datetime.fromtimestamp(timestamp_unix, tz=timezone.utc)
    device_id = (
        metric_labels.get("instance")
        or metric_labels.get("host")
        or metric_labels.get("pod")
        or metric_labels.get("job")
        or "unknown"
    )
    metric_type = metric_labels.get("__name__", "prom_metric")
    try:
        val = float(sample_value_str) if "." in sample_value_str else int(sample_value_str)
    except ValueError:
        val = sample_value_str

    return EventObject(
        timestamp=ts,
        device_id=device_id,
        metric_type=metric_type,
        value=val,
        raw_payload={"metric": metric_labels, "raw": raw or {}},
        source=EventSource.PROMETHEUS,
    )


# ── alert mapping ─────────────────────────────────────────────────────────

def map_alert_to_event(alert: dict[str, Any]) -> EventObject:
    """Map a Prometheus firing alert to an EventObject."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    ts_str = alert.get("startsAt", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)

    device_id = (
        labels.get("instance")
        or labels.get("host")
        or labels.get("pod")
        or labels.get("alertname")
        or "unknown"
    )
    summary = annotations.get("summary", "") or annotations.get("description", "") or alert.get("name", "")

    return EventObject(
        timestamp=ts,
        device_id=device_id,
        metric_type=f"alert.{labels.get('severity', alert.get('state', 'unknown'))}",
        value=summary[:200],
        raw_payload={
            "labels": labels,
            "annotations": annotations,
            "state": alert.get("state"),
            "starts_at": alert.get("startsAt"),
        },
        source=EventSource.PROMETHEUS,
    )


# ── real client ───────────────────────────────────────────────────────────

class PrometheusClient:
    """HTTP client for Prometheus API with retry and connection pooling."""

    def __init__(self, config: PrometheusClientConfig | None = None):
        self._config = config or PrometheusClientConfig.from_settings()
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            headers: dict[str, str] = {}
            if self._config.bearer_token:
                headers["Authorization"] = f"Bearer {self._config.bearer_token}"
            headers.update(self._config.extra_headers)
            self._client = httpx.Client(
                headers=headers,
                verify=self._config.verify_tls,
                timeout=httpx.Timeout(self._config.timeout_seconds),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, path: str, **params: str) -> dict[str, Any]:
        client = self._get_client()
        url = f"{self._config.base_url}{path}"
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        return _post_with_retry(client, url, timeout=self._config.timeout_seconds,
                                max_retries=self._config.max_retries)

    # ── metric queries ────────────────────────────────────────────────

    def query(self, promql: str, time_str: str | None = None) -> dict[str, Any]:
        """Instant query."""
        params = {"query": promql}
        if time_str:
            params["time"] = time_str
        return self._get("/api/v1/query", **params)

    def query_range(self, promql: str, start: str, end: str, step: str = "15s") -> dict[str, Any]:
        """Range query."""
        return self._get("/api/v1/query_range", query=promql, start=start, end=end, step=step)

    # ── alerts / rules ────────────────────────────────────────────────

    def fetch_alerts(self, state: str | None = None) -> list[dict[str, Any]]:
        """Fetch active alerts from /api/v1/alerts."""
        data = self._get("/api/v1/alerts")
        alerts = data.get("data", {}).get("alerts", [])
        if state:
            alerts = [a for a in alerts if a.get("state") == state]
        return [
            {
                "name": a.get("labels", {}).get("alertname", ""),
                "state": a.get("state"),
                "severity": a.get("labels", {}).get("severity", ""),
                "labels": a.get("labels", {}),
                "annotations": a.get("annotations", {}),
                "starts_at": a.get("startsAt"),
                "health": a.get("health"),
            }
            for a in alerts
        ]

    def fetch_rules(self) -> dict[str, Any]:
        """Fetch alerting and recording rules from /api/v1/rules."""
        data = self._get("/api/v1/rules")
        return data.get("data", {}).get("groups", [])

    def fetch_alerts_as_events(self) -> list[EventObject]:
        """Fetch all firing alerts and map to EventObject list."""
        try:
            alerts_raw = self._get("/api/v1/alerts")
            alerts = alerts_raw.get("data", {}).get("alerts", [])
            return [map_alert_to_event(a) for a in alerts if a.get("state") == "firing"]
        except Exception as e:
            logger.warning(f"Failed to fetch alerts: {e}")
            return []

    def health_check(self) -> bool:
        """Check if Prometheus is reachable."""
        try:
            self._get("/api/v1/status/buildinfo")
            return True
        except Exception:
            return False


# ── mock client ───────────────────────────────────────────────────────────

class MockPrometheusClient(PrometheusClient):
    """Mock Prometheus client returning realistic alert/metric data for testing."""

    def __init__(self):
        super().__init__(PrometheusClientConfig(base_url="mock://prometheus", timeout_seconds=5))

    def _get(self, path: str, **params: str) -> dict[str, Any]:
        logger.debug(f"Mock Prometheus: {path} {params}")
        return self._generate_mock_response(path, params)

    def fetch_alerts(self, state: str | None = None) -> list[dict[str, Any]]:
        alerts = [
            {
                "name": "HighCPUUsage",
                "state": "firing",
                "severity": "warning",
                "labels": {"alertname": "HighCPUUsage", "instance": "server-01:9100", "job": "node-exporter", "severity": "warning"},
                "annotations": {"summary": "CPU usage above 90% on server-01", "description": "CPU has been above 90% for 5 minutes"},
                "starts_at": "2026-05-13T10:30:00Z",
                "health": "ok",
            },
            {
                "name": "MemoryUsageHigh",
                "state": "firing",
                "severity": "critical",
                "labels": {"alertname": "MemoryUsageHigh", "instance": "server-02:9100", "job": "node-exporter", "severity": "critical"},
                "annotations": {"summary": "Memory usage above 95% on server-02", "description": "Memory pressure detected, possible OOM"},
                "starts_at": "2026-05-13T10:25:00Z",
                "health": "ok",
            },
            {
                "name": "DiskSpaceLow",
                "state": "firing",
                "severity": "warning",
                "labels": {"alertname": "DiskSpaceLow", "instance": "server-03:9100", "job": "node-exporter", "severity": "warning", "mountpoint": "/data"},
                "annotations": {"summary": "Disk space below 20% on server-03:/data", "description": "/data partition has only 15% free"},
                "starts_at": "2026-05-13T10:20:00Z",
                "health": "ok",
            },
            {
                "name": "HttpErrorRateHigh",
                "state": "firing",
                "severity": "critical",
                "labels": {"alertname": "HttpErrorRateHigh", "instance": "api-gateway:8080", "job": "api-gateway", "severity": "critical"},
                "annotations": {"summary": "HTTP 5xx error rate above 5% on api-gateway", "description": "Error rate spiked to 12% in the last 5 minutes"},
                "starts_at": "2026-05-13T10:15:00Z",
                "health": "ok",
            },
            {
                "name": "ServiceDown",
                "state": "pending",
                "severity": "warning",
                "labels": {"alertname": "ServiceDown", "instance": "payment-service:9090", "job": "payment-service", "severity": "warning"},
                "annotations": {"summary": "payment-service health check failing intermittently"},
                "starts_at": "2026-05-13T10:28:00Z",
                "health": "ok",
            },
        ]
        if state:
            return [a for a in alerts if a["state"] == state]
        return alerts

    def query(self, promql: str, time_str: str | None = None) -> dict[str, Any]:
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"__name__": "cpu_usage", "instance": "server-01:9100"}, "value": [1715596200, "0.92"]},
                    {"metric": {"__name__": "cpu_usage", "instance": "server-02:9100"}, "value": [1715596200, "0.45"]},
                ],
            },
        }

    def query_range(self, promql: str, start: str, end: str, step: str = "15s") -> dict[str, Any]:
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "cpu_usage", "instance": "server-01:9100"},
                        "values": [[1715596200, "0.91"], [1715596215, "0.93"], [1715596230, "0.95"]],
                    }
                ],
            },
        }

    def fetch_rules(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "node-exporter-rules",
                "rules": [
                    {"name": "HighCPUUsage", "type": "alerting", "query": "node_cpu_usage > 0.9", "state": "firing"},
                    {"name": "MemoryUsageHigh", "type": "alerting", "query": "node_memory_usage > 0.95", "state": "firing"},
                ],
            }
        ]

    def health_check(self) -> bool:
        return True

    def close(self) -> None:
        pass


# ── factory ───────────────────────────────────────────────────────────────

_client: PrometheusClient | None = None


def get_prometheus_client() -> PrometheusClient:
    """Get or create the global Prometheus client (real or mock based on settings)."""
    global _client
    if _client is None:
        settings = get_settings()
        if settings.prometheus_mock_mode:
            logger.info("Prometheus mock mode enabled")
            _client = MockPrometheusClient()
        else:
            _client = PrometheusClient(PrometheusClientConfig.from_settings())
    return _client


# ── backward compatibility stub ───────────────────────────────────────────

def fetch_events_stub(
    config: PrometheusClientConfig | None = None,
    *,
    query: str = "",
    end: datetime | None = None,
) -> list[EventObject]:
    """Fetch alert events from Prometheus (replaces old empty stub)."""
    try:
        client = get_prometheus_client()
        if isinstance(client, MockPrometheusClient):
            alerts = client.fetch_alerts("firing")
            return [map_alert_to_event(a) for a in alerts]
        return client.fetch_alerts_as_events()
    except Exception as e:
        logger.warning(f"Failed to fetch Prometheus events: {e}")
        return []
