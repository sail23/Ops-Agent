"""
OpenRCA 数据集接入模块。

基于 Microsoft OpenRCA benchmark 的遥测数据，支持：
- 加载 query.csv（根因信息）
- 加载 record.csv（故障记录）
- 解析 telemetry/log/*.csv（日志）
- 解析 telemetry/metric/*.csv（指标）
- 解析 telemetry/trace/*.csv（链路追踪）

数据集下载：
    gdown https://drive.google.com/uc?id=1cyOKpqyAP4fy-QiJ6a_cKuwR7D46zyVe

目录结构：
    dataset/{SYSTEM}/
    ├── query.csv
    ├── record.csv
    └── telemetry/{YYYY_MM_DD}/
        ├── log/log_*.csv
        ├── metric/metric_*.csv
        └── trace/trace_*.csv

Usage:
    from power_aiops.integrations.openrca import OpenRCAClient

    client = OpenRCAClient("dataset/Bank")
    events = client.fetch_log_events("2021_03_04")
    metrics = client.fetch_metrics("2021_03_04")
    traces = client.fetch_traces("2021_03_04")
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from power_aiops.models.events import EventObject, EventSource

logger = logging.getLogger(__name__)


@dataclass
class OpenRCAConfig:
    """OpenRCA 数据集配置."""

    dataset_path: str = "dataset"
    system: str = "Bank"  # Bank / Telecom / Market
    timezone: str = "UTC+8"

    @property
    def root_dir(self) -> Path:
        return Path(self.dataset_path) / self.system


@dataclass
class QueryRecord:
    """query.csv 中的根因查询记录 (OpenRCA benchmark format)."""

    task_index: str
    instruction: str
    scoring_points: str


@dataclass
class RecordFault:
    """record.csv 中的故障记录 (OpenRCA fault injection log)."""

    level: str
    component: str
    timestamp: str
    datetime: str
    reason: str


@dataclass
class LogEvent:
    """日志事件."""

    timestamp: datetime
    level: str
    component: str
    message: str
    service: str = ""
    trace_id: str = ""
    span_id: str = ""


@dataclass
class MetricPoint:
    """指标数据点."""

    timestamp: datetime
    metric_name: str
    value: float
    unit: str
    component: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class TraceSpan:
    """链路追踪跨度."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    service: str
    operation: str
    start_time: datetime
    duration_ms: float
    status: str
    tags: dict[str, str] = field(default_factory=dict)


class OpenRCAClient:
    """OpenRCA 数据集客户端."""

    def __init__(self, dataset_path: str = "dataset", system: str = "Bank"):
        self.config = OpenRCAConfig(dataset_path=dataset_path, system=system)

    @property
    def root_dir(self) -> Path:
        return Path(self.config.dataset_path) / self.config.system

    def _parse_datetime(self, dt_str: str) -> datetime:
        """解析 datetime 字符串."""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y_%m_%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y_%m_%dT%H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(dt_str.strip(), fmt)
            except ValueError:
                continue
        # Fallback
        return datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────────────────────────
    # Query CSV
    # ─────────────────────────────────────────────────────────────────────────

    def load_queries(self) -> list[QueryRecord]:
        """加载 query.csv 中的根因查询."""
        query_file = self.root_dir / "query.csv"
        if not query_file.exists():
            logger.warning(f"Query file not found: {query_file}")
            return []

        queries = []
        with open(query_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                queries.append(QueryRecord(
                    task_index=row.get("task_index", ""),
                    instruction=row.get("instruction", ""),
                    scoring_points=row.get("scoring_points", ""),
                ))
        logger.info(f"Loaded {len(queries)} queries from {query_file}")
        return queries

    # ─────────────────────────────────────────────────────────────────────────
    # Record CSV
    # ─────────────────────────────────────────────────────────────────────────

    def load_records(self) -> list[RecordFault]:
        """加载 record.csv 中的故障记录."""
        record_file = self.root_dir / "record.csv"
        if not record_file.exists():
            logger.warning(f"Record file not found: {record_file}")
            return []

        records = []
        with open(record_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(RecordFault(
                    level=row.get("level", ""),
                    component=row.get("component", ""),
                    timestamp=row.get("timestamp", ""),
                    datetime=row.get("datetime", ""),
                    reason=row.get("reason", ""),
                ))
        logger.info(f"Loaded {len(records)} fault records from {record_file}")
        return records

    # ─────────────────────────────────────────────────────────────────────────
    # Log Events
    # ─────────────────────────────────────────────────────────────────────────

    def _iter_log_files(self, date: str) -> Iterator[Path]:
        """遍历指定日期的日志文件."""
        log_dir = self.root_dir / "telemetry" / date / "log"
        if not log_dir.exists():
            return
        for f in log_dir.glob("log_*.csv"):
            yield f

    def iter_log_events(self, date: str) -> Iterator[LogEvent]:
        """迭代指定日期的日志事件."""
        for log_file in self._iter_log_files(date):
            with open(log_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = self._parse_datetime(row.get("timestamp", ""))
                        level = row.get("level", "INFO")
                        component = row.get("component", "")
                        message = row.get("message", "")
                        service = row.get("service", component.split(".")[0] if component else "")
                        trace_id = row.get("trace_id", "")
                        span_id = row.get("span_id", "")

                        yield LogEvent(
                            timestamp=ts,
                            level=level,
                            component=component,
                            message=message[:500],
                            service=service,
                            trace_id=trace_id,
                            span_id=span_id,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to parse log row: {e}")

    def fetch_log_events(self, date: str, level_filter: str | None = None) -> list[EventObject]:
        """获取指定日期的日志事件，转换为 EventObject."""
        events = []
        for log in self.iter_log_events(date):
            if level_filter and log.level.upper() != level_filter.upper():
                continue

            raw_payload = {
                "component": log.component,
                "service": log.service,
                "message": log.message,
                "trace_id": log.trace_id,
                "span_id": log.span_id,
            }

            events.append(EventObject(
                timestamp=log.timestamp,
                device_id=log.component,
                metric_type=f"log.{log.level.lower()}",
                value=log.message[:200],
                raw_payload=raw_payload,
                source=EventSource.ELK,  # 复用 ELK 作为日志源
            ))
        return events

    def fetch_error_logs(self, date: str) -> list[EventObject]:
        """获取指定日期的错误日志."""
        return self.fetch_log_events(date, level_filter="ERROR")

    # ─────────────────────────────────────────────────────────────────────────
    # Metrics
    # ─────────────────────────────────────────────────────────────────────────

    def _iter_metric_files(self, date: str) -> Iterator[Path]:
        """遍历指定日期的指标文件."""
        metric_dir = self.root_dir / "telemetry" / date / "metric"
        if not metric_dir.exists():
            return
        for f in metric_dir.glob("metric_*.csv"):
            yield f

    def iter_metric_points(self, date: str) -> Iterator[MetricPoint]:
        """迭代指定日期的指标数据点."""
        for metric_file in self._iter_metric_files(date):
            with open(metric_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = self._parse_datetime(row.get("timestamp", ""))
                        metric_name = row.get("metric_name", "")
                        value = float(row.get("value", 0))
                        unit = row.get("unit", "")
                        component = row.get("component", "")

                        labels = {}
                        for key in row:
                            if key not in ("timestamp", "metric_name", "value", "unit", "component"):
                                labels[key] = str(row[key])

                        yield MetricPoint(
                            timestamp=ts,
                            metric_name=metric_name,
                            value=value,
                            unit=unit,
                            component=component,
                            labels=labels,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to parse metric row: {e}")

    def fetch_metrics(self, date: str, metric_name: str | None = None) -> list[MetricPoint]:
        """获取指定日期的指标数据."""
        points = []
        for point in self.iter_metric_points(date):
            if metric_name and point.metric_name != metric_name:
                continue
            points.append(point)
        return points

    # ─────────────────────────────────────────────────────────────────────────
    # Traces
    # ─────────────────────────────────────────────────────────────────────────

    def _iter_trace_files(self, date: str) -> Iterator[Path]:
        """遍历指定日期的链路文��."""
        trace_dir = self.root_dir / "telemetry" / date / "trace"
        if not trace_dir.exists():
            return
        for f in trace_dir.glob("trace_*.csv"):
            yield f

    def iter_trace_spans(self, date: str) -> Iterator[TraceSpan]:
        """迭代指定日期的链路追踪跨度."""
        for trace_file in self._iter_trace_files(date):
            with open(trace_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        trace_id = row.get("trace_id", "")
                        span_id = row.get("span_id", "")
                        parent_span_id = row.get("parent_span_id") or None
                        service = row.get("service", "")
                        operation = row.get("operation", "")
                        start_time = self._parse_datetime(row.get("start_time", ""))
                        duration_ms = float(row.get("duration_ms", 0))
                        status = row.get("status", "")

                        tags = {}
                        for key in row:
                            if key not in ("trace_id", "span_id", "parent_span_id",
                                          "service", "operation", "start_time",
                                          "duration_ms", "status"):
                                tags[key] = str(row[key])

                        yield TraceSpan(
                            trace_id=trace_id,
                            span_id=span_id,
                            parent_span_id=parent_span_id,
                            service=service,
                            operation=operation,
                            start_time=start_time,
                            duration_ms=duration_ms,
                            status=status,
                            tags=tags,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to parse trace row: {e}")

    def fetch_traces(self, date: str) -> list[TraceSpan]:
        """获取指定日期的链路追踪数据."""
        return list(self.iter_trace_spans(date))

    def build_trace_tree(self, date: str) -> dict[str, TraceSpan]:
        """构建链路追踪树，以 trace_id 为根分组."""
        spans_by_trace: dict[str, list[TraceSpan]] = {}

        for span in self.iter_trace_spans(date):
            if span.trace_id not in spans_by_trace:
                spans_by_trace[span.trace_id] = []
            spans_by_trace[span.trace_id].append(span)

        return spans_by_trace

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def list_available_dates(self) -> list[str]:
        """列出可用的日期目录."""
        telemetry_dir = self.root_dir / "telemetry"
        if not telemetry_dir.exists():
            return []
        dates = sorted([d.name for d in telemetry_dir.iterdir() if d.is_dir()])
        return dates

    def get_dataset_info(self) -> dict[str, Any]:
        """获取数据集信息摘要."""
        queries = self.load_queries()
        records = self.load_records()
        dates = self.list_available_dates()

        return {
            "system": self.config.system,
            "dataset_path": str(self.root_dir),
            "num_queries": len(queries),
            "num_records": len(records),
            "available_dates": dates,
            "num_dates": len(dates),
        }
