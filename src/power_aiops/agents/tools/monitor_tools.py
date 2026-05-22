"""
Monitoring tools backed by Prometheus HTTP client.

These tools are exposed to agents (Ops, SRE) so they can query Prometheus
alerts and metrics on demand during incident investigation.

Real mode: calls live Prometheus API configured via .env
Mock mode: returns realistic mock data for local development (PROMETHEUS_MOCK_MODE=true)
"""

from __future__ import annotations

import logging

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────

def _get_client():
    """Lazy-import Prometheus client (real or mock based on settings)."""
    from power_aiops.integrations.prometheus import get_prometheus_client
    return get_prometheus_client()


def _format_alerts(alerts: list[dict]) -> str:
    """Format alerts into human-readable summary for agent consumption."""
    if not alerts:
        return "No firing alerts found."
    lines = [f"**{len(alerts)} active alert(s):**", ""]
    for a in alerts:
        severity = a.get("severity", "unknown")
        name = a.get("name", "unknown")
        summary = a.get("annotations", {}).get("summary", "")
        started = a.get("starts_at", "")
        instance = a.get("labels", {}).get("instance", "")
        lines.append(f"- [{severity.upper()}] **{name}** on `{instance}` — {summary} (since {started})")
    return "\n".join(lines)


# ── alert tools ────────────────────────────────────────────────────────────

class PrometheusAlertsTool(Tool):
    """Query currently firing alerts from Prometheus Alertmanager.

    Call this when:
    - OpsAgent: first triage — check what alerts are currently firing
    - SREAgent: assess blast radius — which services/hosts are alerting
    - CodeAgent: correlate alert timeline with error traces
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_alerts",
            description=(
                "Query Prometheus for currently firing alerts. Returns alert name, "
                "severity, labels (instance, job), annotations (summary, description), "
                "and start time. Use this when you need to know what alerts are active "
                "right now across the infrastructure."
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Alert state filter: firing, pending, or omit for all",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Severity filter: warning, critical, info (applied client-side after fetch)",
                    },
                },
                "required": [],
            },
            examples=[
                'prometheus_alerts()',
                'prometheus_alerts(state="firing")',
                'prometheus_alerts(state="firing", severity="critical")',
            ],
            tags=["prometheus", "alerts", "monitoring"],
        )

    def execute(self, **kwargs) -> ToolResult:
        state = kwargs.get("state", "firing")
        severity_filter = kwargs.get("severity")

        try:
            client = _get_client()
            alerts = client.fetch_alerts(state=state)

            if severity_filter:
                alerts = [a for a in alerts if a.get("severity") == severity_filter]

            return ToolResult(
                success=True,
                data={
                    "total": len(alerts),
                    "alerts": alerts,
                    "summary": _format_alerts(alerts),
                },
            )
        except Exception as e:
            logger.warning(f"prometheus_alerts failed: {e}")
            return ToolResult(success=False, error=f"Failed to fetch alerts: {str(e)}")


class PrometheusRulesTool(Tool):
    """Query alerting and recording rules from Prometheus.

    Call this when:
    - SREAgent: understand what thresholds trigger specific alerts
    - CodeAgent: check if a generated fix would violate any alert rules
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_rules",
            description=(
                "Query Prometheus for alerting and recording rules. Returns rule name, "
                "type (alerting/recording), PromQL expression, and current state. "
                "Use this to understand what conditions trigger specific alerts."
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {},
                "required": [],
            },
            examples=['prometheus_rules()'],
            tags=["prometheus", "rules", "monitoring"],
        )

    def execute(self, **kwargs) -> ToolResult:
        try:
            client = _get_client()
            rules = client.fetch_rules()
            return ToolResult(
                success=True,
                data={"groups": rules, "total_groups": len(rules)},
            )
        except Exception as e:
            logger.warning(f"prometheus_rules failed: {e}")
            return ToolResult(success=False, error=f"Failed to fetch rules: {str(e)}")


# ── metric query tools ─────────────────────────────────────────────────────

class PrometheusQueryTool(Tool):
    """Execute a PromQL instant query against Prometheus.

    Call this when:
    - OpsAgent: check current metric value (CPU, memory, QPS) for a specific instance
    - SREAgent: verify metric baseline before/after an incident timeline
    - CodeAgent: query trace-related metrics to correlate with spans
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_query",
            description=(
                "Execute an instant PromQL query against Prometheus. Returns current "
                "metric values with labels. Supports any valid PromQL expression. "
                "Common patterns: `up`, `node_cpu_seconds_total`, `rate(http_requests_total[5m])`"
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL query expression",
                    },
                    "time": {
                        "type": "string",
                        "description": "Evaluation time in ISO format (optional, defaults to now)",
                    },
                },
                "required": ["query"],
            },
            examples=[
                'prometheus_query(query="up")',
                'prometheus_query(query="rate(http_requests_total{job=\\"api-gateway\\"}[5m])")',
            ],
            tags=["prometheus", "metrics", "promql"],
        )

    def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query")
        time_str = kwargs.get("time")

        if not query:
            return ToolResult(success=False, error="query is required")

        try:
            client = _get_client()
            data = client.query(query, time_str=time_str)
            results = data.get("data", {}).get("result", [])

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "result_type": data.get("data", {}).get("resultType"),
                    "result_count": len(results),
                    "results": results[:50],
                },
            )
        except Exception as e:
            logger.warning(f"prometheus_query failed: {e}")
            return ToolResult(success=False, error=f"Query failed: {str(e)}")


class PrometheusQueryRangeTool(Tool):
    """Execute a PromQL range query for time-series data.

    Call this when:
    - SREAgent: analyze metric trends over the incident window
    - CodeAgent: correlate metric anomalies with trace error spans over time
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_query_range",
            description=(
                "Execute a PromQL range query to get time-series metric data. "
                "Returns values over a time window with configurable step interval. "
                "Use this to see how metrics changed during an incident window."
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL query expression",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start time in ISO format or Unix timestamp",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time in ISO format or Unix timestamp",
                    },
                    "step": {
                        "type": "string",
                        "description": "Query resolution step (e.g. 15s, 1m, 5m), default 15s",
                    },
                },
                "required": ["query", "start", "end"],
            },
            examples=[
                'prometheus_query_range(query="rate(node_cpu_seconds_total[5m])", start="2026-05-13T10:00:00Z", end="2026-05-13T11:00:00Z", step="1m")',
            ],
            tags=["prometheus", "metrics", "promql", "range"],
        )

    def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query")
        start = kwargs.get("start")
        end = kwargs.get("end")
        step = kwargs.get("step", "15s")

        if not query:
            return ToolResult(success=False, error="query is required")
        if not start or not end:
            return ToolResult(success=False, error="start and end are required for range query")

        try:
            client = _get_client()
            data = client.query_range(query, start=start, end=end, step=step)
            results = data.get("data", {}).get("result", [])

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "start": start,
                    "end": end,
                    "step": step,
                    "result_type": data.get("data", {}).get("resultType"),
                    "result_count": len(results),
                    "results": results[:50],
                },
            )
        except Exception as e:
            logger.warning(f"prometheus_query_range failed: {e}")
            return ToolResult(success=False, error=f"Range query failed: {str(e)}")


# ── promql template tool ───────────────────────────────────────────────────

class MetricsSummaryTool(Tool):
    """Generate PromQL query templates for common infrastructure metrics.

    This does NOT call Prometheus — it returns query strings that can be
    passed to prometheus_query or prometheus_query_range.
    """

    _TEMPLATES: dict[str, dict] = {
        "cpu": {
            "description": "CPU usage",
            "queries": [
                ("CPU usage (overall)", '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'),
                ("CPU per instance", '100 - (rate(node_cpu_seconds_total{mode="idle"}[5m]) * 100)'),
            ],
        },
        "memory": {
            "description": "Memory usage",
            "queries": [
                ("Memory usage %", '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'),
                ("Memory used GB", '(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / 1024^3'),
            ],
        },
        "disk": {
            "description": "Disk usage",
            "queries": [
                ("Disk usage %", '100 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} * 100)'),
            ],
        },
        "network": {
            "description": "Network traffic",
            "queries": [
                ("Ingress Mbps", 'rate(node_network_receive_bytes_total[5m]) * 8 / 1024^2'),
                ("Egress Mbps", 'rate(node_network_transmit_bytes_total[5m]) * 8 / 1024^2'),
            ],
        },
        "http_requests": {
            "description": "HTTP request metrics",
            "queries": [
                ("Request rate (QPS)", 'rate(http_requests_total[5m])'),
                ("Error rate", 'rate(http_requests_total{status=~"5.."}[5m])'),
                ("P99 latency", 'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))'),
            ],
        },
        "error": {
            "description": "Error-related metrics",
            "queries": [
                ("5xx rate", 'rate(http_requests_total{status=~"5.."}[5m])'),
                ("4xx rate", 'rate(http_requests_total{status=~"4.."}[5m])'),
            ],
        },
    }

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="metrics_summary",
            description=(
                "Generate common PromQL query templates for infrastructure metrics "
                "(CPU, memory, disk, network, HTTP requests, errors). "
                "Returns query strings — use prometheus_query or prometheus_query_range "
                "to execute them. Does NOT call the Prometheus API."
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {
                    "metric_type": {
                        "type": "string",
                        "description": f"Metric type: {', '.join(sorted(self._TEMPLATES.keys()))}",
                    },
                    "instance": {
                        "type": "string",
                        "description": "Optional instance filter (e.g., server-01:9100)",
                    },
                },
                "required": ["metric_type"],
            },
            examples=[
                'metrics_summary(metric_type="cpu")',
                'metrics_summary(metric_type="http_requests")',
            ],
            tags=["metrics", "promql", "template"],
        )

    def execute(self, **kwargs) -> ToolResult:
        metric_type = kwargs.get("metric_type")
        instance = kwargs.get("instance")

        if metric_type not in self._TEMPLATES:
            return ToolResult(
                success=False,
                error=f"Unknown metric_type: {metric_type}. Available: {list(self._TEMPLATES.keys())}",
            )

        template = self._TEMPLATES[metric_type]
        queries = [
            {"name": name, "query": query.replace("{instance=\"\"", f'{{instance="{instance}"') if instance else query}
            for name, query in template["queries"]
        ]

        return ToolResult(
            success=True,
            data={
                "metric_type": metric_type,
                "description": template["description"],
                "queries": queries,
            },
        )


# ── config query (kept from original) ──────────────────────────────────────

class ConfigQueryTool(Tool):
    """Query service configuration (mock — connect to config center in production)."""

    _MOCK_CONFIGS: dict[str, dict] = {
        "user-service": {"max_connections": 1000, "timeout_ms": 5000, "retry_count": 3},
        "api-gateway": {"rate_limit": 10000, "max_request_size_mb": 10, "upstream_timeout_ms": 30000},
        "payment-service": {"max_concurrent": 500, "payment_timeout_ms": 60000, "enable_retry": True},
    }

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="config_query",
            description=(
                "Query service configuration parameters. Returns config key-value pairs. "
                "Mock implementation — connect to a config center for production use."
            ),
            category=ToolCategory.MONITOR,
            parameters={
                "properties": {
                    "service": {"type": "string", "description": "Service name"},
                    "key": {"type": "string", "description": "Specific config key (optional)"},
                },
                "required": ["service"],
            },
            examples=[
                'config_query(service="user-service")',
                'config_query(service="api-gateway", key="rate_limit")',
            ],
            tags=["config", "service"],
        )

    def execute(self, **kwargs) -> ToolResult:
        service = kwargs.get("service")
        key = kwargs.get("key")

        if not service:
            return ToolResult(success=False, error="service is required")

        config = self._MOCK_CONFIGS.get(service, {"status": "unknown", "note": f"No config for {service}"})

        if key:
            if key in config:
                return ToolResult(success=True, data={"service": service, "key": key, "value": config[key]})
            return ToolResult(success=False, error=f"Config key not found: {key}")

        return ToolResult(success=True, data={"service": service, "configs": config})
