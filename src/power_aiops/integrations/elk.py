"""
ELK（Elasticsearch + Logstash + Kibana）或 OpenSearch 日志接入桩。

生产环境通常通过 Elasticsearch `_search` API 或 Kibana 相关代理访问；
本模块仅提供固定函数签名与 **空列表** 占位实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from power_aiops.models.events import EventObject, EventSource


@dataclass
class ElkClientConfig:
    """
    生产环境连接参数（示例）。

    - **base_url**：集群地址，如 `https://es.internal:9200`。
    - **username / password**：基本认证；生产可换 API Key 或 OIDC（实现层处理）。
    - **index_pattern**：查询用索引或别名，如 `filebeat-*`、`logs-*`。
    """

    base_url: str = "http://127.0.0.1:9200"
    username: str | None = None
    password: str | None = None
    index_pattern: str = "logs-*"
    verify_tls: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)


def fetch_events_stub(
    config: ElkClientConfig | None = None,
    *,
    query_body: dict[str, Any] | None = None,
    time_range_end: datetime | None = None,
) -> list[EventObject]:
    """
    占位：返回空列表，不发起网络请求。

    生产实现建议：

    1. `POST /{index}/_search`（或 `_async_search`），请求体含 `query`、`sort`、`size`。
    2. 解析 `hits.hits[]`：`_source` 中取 `@timestamp`、主机名、日志级别、消息体。
    3. 映射到 `EventObject`：
       - `timestamp`：解析 ISO-8601 或 epoch；
       - `device_id`：`host.name`、`kubernetes.pod.name`、或 `_source.agent.hostname` 等字段；
       - `metric_type`：如 `log.error.count` 或 `log.level:ERROR` 聚合键；
       - `value`：可为错误条数、或日志摘要 hash（按需）；
       - `raw_payload`：单条 hit 或 `_source`；
       - `source`：`EventSource.ELK`。
    """
    _ = (config, query_body, time_range_end)
    return []


def map_elk_hit_to_event(
    *,
    source: dict[str, Any],
    index: str | None = None,
    hit_id: str | None = None,
) -> EventObject:
    """
    将单条 ES/OS `hit._source` 映射为 `EventObject`（字段名按实际索引模板调整）。
    """
    ts_raw = source.get("@timestamp") or source.get("timestamp")
    if isinstance(ts_raw, str):
        # 简化：仅作示例；生产请用 dateutil 或 fromisoformat
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    else:
        ts = datetime.now(timezone.utc)

    host_obj = source.get("host")
    host_from_host = host_obj.get("name") if isinstance(host_obj, dict) else None
    agent = source.get("agent")
    host_from_agent = agent.get("hostname") if isinstance(agent, dict) else None
    device_id = str(
        host_from_host or source.get("hostname") or host_from_agent or "unknown-host"
    )

    log_obj = source.get("log")
    level = str(log_obj.get("level") if isinstance(log_obj, dict) else source.get("level") or "log")
    msg = str(source.get("message") or source.get("msg") or "")[:200]
    metric_type = f"log.{level}"

    raw: dict[str, Any] = {"_source": source}
    if index:
        raw["_index"] = index
    if hit_id:
        raw["_id"] = hit_id

    return EventObject(
        timestamp=ts,
        device_id=device_id,
        metric_type=metric_type,
        value=msg or None,
        raw_payload=raw,
        source=EventSource.ELK,
    )
