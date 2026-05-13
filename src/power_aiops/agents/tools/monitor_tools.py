"""
监控查询工具集。

提供 Prometheus、监控系统等查询能力。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class PrometheusQueryTool(Tool):
    """Prometheus 查询工具.

    查询 Prometheus 指标数据。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        timeout: int = 10,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_query",
            description="查询 Prometheus 指标数据，支持即时查询和范围查询。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL 查询语句",
                    },
                    "time": {
                        "type": "string",
                        "description": "查询时间点（ISO 格式或时间戳），不指定则查询最新值",
                    },
                    "start": {
                        "type": "string",
                        "description": "范围查询开始时间",
                    },
                    "end": {
                        "type": "string",
                        "description": "范围查询结束时间",
                    },
                    "step": {
                        "type": "string",
                        "description": "查询步长（如 15s, 1m, 5m）",
                    },
                    "base_url": {
                        "type": "string",
                        "description": f"Prometheus 地址（默认 {self._base_url}）",
                    },
                },
                "required": ["query"],
            },
            examples=[
                'prometheus_query(query="up{job=\\"prometheus\\"}")',
                'prometheus_query(query="rate(http_requests_total[5m])", start="2024-01-01T00:00:00Z", end="2024-01-01T01:00:00Z", step="1m")',
            ],
            tags=["prometheus", "monitoring", "metrics", "query"],
        )

    def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query")
        time_param = kwargs.get("time")
        start = kwargs.get("start")
        end = kwargs.get("end")
        step = kwargs.get("step")
        base_url = kwargs.get("base_url", self._base_url)

        if not query:
            return ToolResult(success=False, error="query is required")

        try:
            import urllib.request
            import urllib.parse
            import urllib.error
            import json

            # 判断是即时查询还是范围查询
            if start and end:
                # 范围查询
                params = {
                    "query": query,
                    "start": self._parse_time(start),
                    "end": self._parse_time(end),
                    "step": step or "15s",
                }
                endpoint = "/api/v1/query_range"
            else:
                # 即时查询
                params = {"query": query}
                if time_param:
                    params["time"] = self._parse_time(time_param)
                endpoint = "/api/v1/query"

            url = f"{base_url}{endpoint}?{urllib.parse.urlencode(params)}"

            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

            if data.get("status") == "success":
                return ToolResult(
                    success=True,
                    data={
                        "status": "success",
                        "query": query,
                        "result_type": data.get("data", {}).get("resultType"),
                        "results": self._format_results(data.get("data", {}).get("result", [])),
                    },
                )
            else:
                return ToolResult(
                    success=False,
                    error=data.get("error", "Unknown error"),
                    data={"status": data.get("status")},
                )

        except ImportError:
            return ToolResult(success=False, error="urllib not available")
        except urllib.error.URLError as e:
            return ToolResult(success=False, error=f"Connection error: {e.reason}")
        except Exception as e:
            return ToolResult(success=False, error=f"Query failed: {str(e)}")

    def _parse_time(self, time_str: str) -> float:
        """解析时间字符串为时间戳."""
        try:
            # 如果是数字，直接返回
            return float(time_str)
        except ValueError:
            # 尝试解析 ISO 格式
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            return dt.timestamp()

    def _format_results(self, results: list) -> list[dict]:
        """格式化查询结果."""
        formatted = []
        for item in results[:100]:  # 限制返回数量
            if isinstance(item, dict):
                metric = item.get("metric", {})
                if "value" in item:
                    # 即时查询结果
                    formatted.append({
                        "metric": metric,
                        "value": item["value"][1],
                        "timestamp": datetime.fromtimestamp(item["value"][0]).isoformat(),
                    })
                elif "values" in item:
                    # 范围查询结果
                    formatted.append({
                        "metric": metric,
                        "values": [
                            {"timestamp": datetime.fromtimestamp(v[0]).isoformat(), "value": v[1]}
                            for v in item["values"]
                        ],
                    })
        return formatted


class PrometheusRulesTool(Tool):
    """Prometheus 告警规则查询工具.

    查询当前触发的告警和告警规则。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        timeout: int = 10,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="prometheus_alerts",
            description="查询 Prometheus 当前触发的告警和告警规则状态。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "alert_state": {
                        "type": "string",
                        "description": "告警状态过滤: firing, pending, inactive",
                    },
                    "base_url": {
                        "type": "string",
                        "description": f"Prometheus 地址（默认 {self._base_url}）",
                    },
                },
                "required": [],
            },
            examples=[
                'prometheus_alerts()',
                'prometheus_alerts(alert_state="firing")',
            ],
            tags=["prometheus", "alerts", "monitoring"],
        )

    def execute(self, **kwargs) -> ToolResult:
        alert_state = kwargs.get("alert_state")
        base_url = kwargs.get("base_url", self._base_url)

        try:
            import urllib.request
            import urllib.error
            import json

            url = f"{base_url}/api/v1/alerts"

            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

            if data.get("status") == "success":
                alerts = data.get("data", {}).get("alerts", [])

                # 按状态过滤
                if alert_state:
                    alerts = [a for a in alerts if a.get("state") == alert_state]

                # 格式化结果
                formatted_alerts = []
                for alert in alerts[:50]:  # 限制数量
                    formatted_alerts.append({
                        "name": alert.get("name"),
                        "state": alert.get("state"),
                        "labels": alert.get("labels", {}),
                        "annotations": alert.get("annotations", {}),
                        "starts_at": alert.get("startsAt"),
                        "health": alert.get("health"),
                    })

                return ToolResult(
                    success=True,
                    data={
                        "total": len(formatted_alerts),
                        "alerts": formatted_alerts,
                    },
                )
            else:
                return ToolResult(
                    success=False,
                    error=data.get("error", "Unknown error"),
                )

        except urllib.error.URLError as e:
            return ToolResult(success=False, error=f"Connection error: {e.reason}")
        except Exception as e:
            return ToolResult(success=False, error=f"Query failed: {str(e)}")


class MetricsSummaryTool(Tool):
    """指标摘要工具.

    生成常见指标的 PromQL 查询语句。
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="metrics_summary",
            description="生成常见监控指标的 PromQL 查询语句，如 CPU、内存、网络等。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "metric_type": {
                        "type": "string",
                        "description": "指标类型: cpu, memory, disk, network, request, error, latency",
                    },
                    "instance": {
                        "type": "string",
                        "description": "目标实例（可选，默认为所有）",
                    },
                    "job": {
                        "type": "string",
                        "description": "目标 job（可选）",
                    },
                },
                "required": ["metric_type"],
            },
            examples=[
                'metrics_summary(metric_type="cpu")',
                'metrics_summary(metric_type="memory", instance="server-01:9100")',
            ],
            tags=["metrics", "promql", "template", "summary"],
        )

    def execute(self, **kwargs) -> ToolResult:
        metric_type = kwargs.get("metric_type")
        instance = kwargs.get("instance")
        job = kwargs.get("job")

        # 构造标签过滤
        labels = []
        if instance:
            labels.append(f'instance="{instance}"')
        if job:
            labels.append(f'job="{job}"')
        label_filter = "," + ",".join(labels) if labels else ""

        templates = {
            "cpu": {
                "description": "CPU 使用率",
                "queries": [
                    ("CPU 使用率 (总)", f'100 - (avg by{label_filter} (rate(node_cpu_seconds_total{{mode="idle"{label_filter}}}[5m])) * 100)'),
                    ("CPU 使用率 (用户)", f'avg by{label_filter} (rate(node_cpu_seconds_total{{mode="user"{label_filter}}}[5m])) * 100'),
                    ("CPU 使用率 (系统)", f'avg by{label_filter} (rate(node_cpu_seconds_total{{mode="system"{label_filter}}}[5m])) * 100'),
                ],
            },
            "memory": {
                "description": "内存使用情况",
                "queries": [
                    ("内存使用率", f'(1 - (node_memory_MemAvailable_bytes{{{{}}}} / node_memory_MemTotal_bytes{{{{}}}})) * 100'.format(label_filter, label_filter)),
                    ("内存已用 (GB)", f'(node_memory_MemTotal_bytes{{}} - node_memory_MemAvailable_bytes{{}}) / 1024 / 1024 / 1024'.format(label_filter)),
                    ("Swap 使用率", f'(node_memory_SwapTotal_bytes{{}} - node_memory_SwapFree_bytes{{}}) / node_memory_SwapTotal_bytes{{}} * 100'.format(label_filter, label_filter, label_filter) if '{' in label_filter else '...'),
                ],
            },
            "disk": {
                "description": "磁盘使用情况",
                "queries": [
                    ("磁盘使用率", f'100 - (node_filesystem_avail_bytes{{{{}}}} / node_filesystem_size_bytes{{{{}}}} * 100)'.format(label_filter, label_filter)),
                    ("磁盘 IOPS", f'rate(node_disk_io_time_seconds_total{{{{}}}}[5m]) * 100'.format(label_filter)),
                    ("磁盘吞吐量", f'rate(node_disk_read_bytes_seconds_total{{{{}}}}[5m]) + rate(node_disk_written_bytes_total{{{{}}}}[5m])'.format(label_filter, label_filter)),
                ],
            },
            "network": {
                "description": "网络流量",
                "queries": [
                    ("入站流量 (Mbps)", f'rate(node_network_receive_bytes_total{{{{}}}}[5m]) * 8 / 1024 / 1024'.format(label_filter)),
                    ("出站流量 (Mbps)", f'rate(node_network_transmit_bytes_total{{{{}}}}[5m]) * 8 / 1024 / 1024'.format(label_filter)),
                    ("入站包速率", f'rate(node_network_receive_packets_total{{{{}}}}[5m])'.format(label_filter)),
                ],
            },
            "request": {
                "description": "请求相关指标",
                "queries": [
                    ("QPS", f'rate(http_requests_total{{{{}}}}[5m])'.format(label_filter)),
                    ("请求总数", f'increase(http_requests_total{{{{}}}}[1h])'.format(label_filter)),
                    ("请求方法分布", f'increase(http_requests_total{{{{}}}}[1h]) by (method)'.format(label_filter)),
                ],
            },
            "error": {
                "description": "错误相关指标",
                "queries": [
                    ("错误率", f'rate(http_requests_total{{status=~"5.."{{}}}}[5m])'.format(label_filter)),
                    ("4xx 错误率", f'rate(http_requests_total{{status=~"4.."{{}}}}[5m])'.format(label_filter)),
                    ("错误总数", f'increase(http_requests_total{{status=~"5.."{{}}}}[1h])'.format(label_filter)),
                ],
            },
            "latency": {
                "description": "延迟相关指标",
                "queries": [
                    ("平均延迟 (P50)", f'rate(http_request_duration_seconds_sum{{{{}}}}[5m]) / rate(http_request_duration_seconds_count{{{{}}}}[5m])'.format(label_filter, label_filter)),
                    ("P99 延迟", f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{{{}}}}[5m]))'.format(label_filter)),
                    ("P95 延迟", f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{{{}}}}[5m]))'.format(label_filter)),
                ],
            },
        }

        if metric_type not in templates:
            return ToolResult(
                success=False,
                error=f"Unknown metric type: {metric_type}. Available: {list(templates.keys())}",
            )

        template = templates[metric_type]

        # 清理查询语句中的多余大括号
        queries = []
        for name, query in template["queries"]:
            # 移除空的 {{{{}}}} 模式
            cleaned_query = query.replace("{{{{}}}}", label_filter)
            queries.append({"name": name, "query": cleaned_query})

        return ToolResult(
            success=True,
            data={
                "metric_type": metric_type,
                "description": template["description"],
                "queries": queries,
                "labels": {"instance": instance, "job": job} if instance or job else {},
            },
        )


class ConfigQueryTool(Tool):
    """配置查询工具.

    模拟查询服务配置（在真实环境中可对接配置中心）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._configs: dict[str, dict] = {}

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="config_query",
            description="查询服务配置信息（模拟，可对接配置中心）。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "服务名称",
                    },
                    "key": {
                        "type": "string",
                        "description": "配置项 key（可选，不指定则返回所有）",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "配置命名空间（默认 default）",
                    },
                },
                "required": ["service"],
            },
            examples=[
                'config_query(service="user-service")',
                'config_query(service="api-gateway", key="max_connections")',
            ],
            tags=["config", "service", "settings"],
        )

    def execute(self, **kwargs) -> ToolResult:
        service = kwargs.get("service")
        key = kwargs.get("key")
        namespace = kwargs.get("namespace", "default")

        if not service:
            return ToolResult(success=False, error="service is required")

        try:
            # 模拟配置数据（实际应对接配置中心）
            config = self._get_mock_config(service, namespace)

            if key:
                if key in config:
                    return ToolResult(
                        success=True,
                        data={
                            "service": service,
                            "namespace": namespace,
                            "key": key,
                            "value": config[key],
                        },
                    )
                else:
                    return ToolResult(
                        success=False,
                        error=f"Config key not found: {key}",
                    )

            return ToolResult(
                success=True,
                data={
                    "service": service,
                    "namespace": namespace,
                    "configs": config,
                },
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Query failed: {str(e)}")

    def _get_mock_config(self, service: str, namespace: str) -> dict:
        """获取模拟配置."""
        mock_configs = {
            "user-service": {
                "max_connections": 1000,
                "timeout_ms": 5000,
                "retry_count": 3,
                "circuit_breaker_threshold": 50,
            },
            "api-gateway": {
                "rate_limit": 10000,
                "max_request_size_mb": 10,
                "upstream_timeout_ms": 30000,
            },
            "payment-service": {
                "max_concurrent": 500,
                "payment_timeout_ms": 60000,
                "enable_retry": True,
            },
        }
        return mock_configs.get(service, {
            "status": "unknown",
            "note": f"No config found for {service}",
        })
