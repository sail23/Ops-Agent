"""
Prometheus（或兼容 VictoriaMetrics、Mimir 等）指标接入桩。

生产环境请将 HTTP 客户端配置为可访问内网 Prometheus，并处理 TLS、代理与凭据；
本模块仅提供固定函数签名与 **空列表** 占位实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from power_aiops.models.events import EventObject, EventSource


@dataclass
class PrometheusClientConfig:
    """
    生产环境连接参数（示例字段，可按贵司规范扩展）。

    - **base_url**：如 `https://prom.internal:9090`，不含末尾 `/`。
    - **bearer_token**：若使用 Bearer；也可改为 mTLS（在实现层扩展 `httpx` 客户端）。
    - **extra_headers**：网关或租户头。
    """

    base_url: str = "http://127.0.0.1:9090"
    bearer_token: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    verify_tls: bool = True


def fetch_events_stub(
    config: PrometheusClientConfig | None = None,
    *,
    query: str = "",
    end: datetime | None = None,
) -> list[EventObject]:
    """
    占位：返回空列表，不发起网络请求。

    生产实现建议：

    1. 使用 `GET /api/v1/query` 或 `query_range`（`httpx`/`requests`），`query` 为 PromQL。
    2. 解析 JSON `data.result[]`：每条含 `metric`（标签字典）与 `value` `[ts, "v"]` 或 `values`。
    3. 映射到 `EventObject`：
       - `timestamp`：秒级时间戳转 UTC `datetime`；
       - `device_id`：优先 `metric["instance"]`，其次 `host`、`pod`、`job` 等业务约定；
       - `metric_type`：`metric.get("__name__")` 或拼接标签区分；
       - `value`：解析数值；
       - `raw_payload`：保留原始 `metric` + 样本；
       - `source`：`EventSource.PROMETHEUS`。
    """
    _ = (config, query, end)
    return []


def map_prometheus_sample_to_event(
    *,
    metric_labels: dict[str, str],
    sample_value_str: str,
    timestamp_unix: float,
    raw: dict[str, Any] | None = None,
) -> EventObject:
    """
    将单条 Prom 查询结果映射为 `EventObject`（供生产实现复用，与 `fetch_events_stub` 独立）。

    `sample_value_str` 为 API 返回的字符串形式数值；解析失败时可置 `value=None`。
    """
    ts = datetime.fromtimestamp(timestamp_unix, tz=timezone.utc)
    device_id = (
        metric_labels.get("instance")
        or metric_labels.get("host")
        or metric_labels.get("pod")
        or metric_labels.get("job")
        or "unknown"
    )
    metric_type = metric_labels.get("__name__", "prom_metric")
    val: str | float | int | None
    try:
        if "." in sample_value_str:
            val = float(sample_value_str)
        else:
            val = int(sample_value_str)
    except ValueError:
        val = sample_value_str

    payload = {"metric": metric_labels, "raw": raw or {}}
    return EventObject(
        timestamp=ts,
        device_id=device_id,
        metric_type=metric_type,
        value=val,
        raw_payload=payload,
        source=EventSource.PROMETHEUS,
    )
