"""Seed Neo4j with sample trace and fault data for testing.

Usage:
    python -m power_aiops.scripts.seed_graph_data

This script populates the Neo4j database with sample:
- Trace/Span data (for visualization)
- Service nodes
- Fault cases
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from random import choice, randint, uniform

from power_aiops.memory.graph_rag import GraphRAG, TraceSpan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def generate_sample_spans(trace_id: str, service: str) -> list[TraceSpan]:
    """Generate sample spans for a trace."""
    operations = [
        "http.GET./api/orders",
        "mysql.query",
        "redis.GET",
        "http.POST./api/payments",
        "kafka.send",
        "http.GET./api/products",
    ]

    spans = []
    base_time = datetime.now(timezone.utc) - timedelta(minutes=randint(1, 60))

    # Root span
    spans.append(TraceSpan(
        span_id=f"{trace_id}-span-0",
        trace_id=trace_id,
        parent_span_id=None,
        service=service,
        operation="http.GET./api/orders",
        start_time=base_time,
        duration_ms=uniform(100, 500),
        status="OK",
        error_message="",
        tags={"http.status_code": 200},
    ))

    # Child spans
    for i in range(1, randint(3, 6)):
        parent_id = f"{trace_id}-span-{i-1}"
        op = choice(operations)
        duration = uniform(20, 200) if i % 2 == 0 else uniform(50, 300)
        status = "ERROR" if randint(1, 10) == 1 else "OK"
        error_msg = "Connection timeout" if status == "ERROR" else ""

        spans.append(TraceSpan(
            span_id=f"{trace_id}-span-{i}",
            trace_id=trace_id,
            parent_span_id=parent_id,
            service=f"{service}-{i}" if i > 2 else service,
            operation=op,
            start_time=base_time + timedelta(milliseconds=i * 50),
            duration_ms=duration,
            status=status,
            error_message=error_msg,
            tags={"span.kind": "client" if i % 2 == 0 else "server"},
        ))

    return spans


def seed_data(rag: GraphRAG) -> dict:
    """Seed the database with sample data."""
    stats = {"traces": 0, "spans": 0, "services": 0, "fault_cases": 0}

    services = [
        "order-service",
        "payment-service",
        "product-service",
        "user-service",
        "inventory-service",
        "notification-service",
    ]

    # Store services
    for svc in services:
        rag.add_service(svc, "active", {"region": "us-east-1"})
        stats["services"] += 1

    # Generate traces with varying characteristics
    logger.info("Generating sample traces...")

    for i in range(20):
        trace_id = f"trace-{datetime.now().strftime('%Y%m%d')}-{i:04d}"
        service = choice(services)
        spans = generate_sample_spans(trace_id, service)

        rag.store_trace(trace_id, spans)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    # Add some slow traces (>5s)
    for i in range(5):
        trace_id = f"trace-slow-{datetime.now().strftime('%Y%m%d')}-{i:04d}"
        service = choice(services)
        spans = generate_sample_spans(trace_id, service)

        # Make it slow
        for span in spans:
            span.duration_ms = uniform(2000, 8000)

        rag.store_trace(trace_id, spans)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    # Add some error traces
    for i in range(3):
        trace_id = f"trace-error-{datetime.now().strftime('%Y%m%d')}-{i:04d}"
        service = choice(services)
        spans = generate_sample_spans(trace_id, service)

        # Make last span error
        spans[-1].status = "ERROR"
        spans[-1].error_message = "Database connection pool exhausted"

        rag.store_trace(trace_id, spans)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    # Add fault cases
    fault_summaries = [
        {
            "case_id": "CASE-001",
            "summary": "数据库连接池耗尽导致订单服务不可用",
            "root_cause": "MySQL max_connections 配置过小",
            "affected_services": ["order-service", "payment-service"],
            "resolution": "增加 max_connections 至 500，重启服务",
            "symptoms": ["Connection timeout", "Service unavailable"],
            "metrics": {"db.pool.used": 100, "db.pool.max": 100},
        },
        {
            "case_id": "CASE-002",
            "summary": "Redis 缓存雪崩导致服务响应超时",
            "root_cause": "大量 key 同时过期",
            "affected_services": ["user-service", "product-service"],
            "resolution": "实现随机 TTL，避免同时过期",
            "symptoms": ["Cache miss", "Response timeout"],
            "metrics": {"cache.hit_rate": 0.1, "cache.eviction": 10000},
        },
        {
            "case_id": "CASE-003",
            "summary": "Kafka 消费者滞后导致消息堆积",
            "root_cause": "消费者处理速度低于生产速度",
            "affected_services": ["notification-service"],
            "resolution": "增加消费者实例，优化处理逻辑",
            "symptoms": ["Message lag", "Processing delay"],
            "metrics": {"kafka.lag": 50000, "consumer.lag": 50000},
        },
    ]

    for case in fault_summaries:
        rag.store_fault_case(
            case_id=case["case_id"],
            summary=case["summary"],
            root_cause=case["root_cause"],
            affected_services=case["affected_services"],
            resolution=case["resolution"],
            symptoms=case["symptoms"],
            metrics=case["metrics"],
        )
        stats["fault_cases"] += 1

    return stats


def main():
    """Main entry point."""
    logger.info("Seeding Neo4j with sample data...")

    rag = GraphRAG()

    # Initialize schema
    rag.initialize_schema()

    # Seed data
    stats = seed_data(rag)

    logger.info("Seeding complete!")
    logger.info(f"  Traces: {stats['traces']}")
    logger.info(f"  Spans: {stats['spans']}")
    logger.info(f"  Services: {stats['services']}")
    logger.info(f"  Fault Cases: {stats['fault_cases']}")

    # Verify
    final_stats = rag.get_stats()
    logger.info(f"\nDatabase verification:")
    logger.info(f"  Total Traces: {final_stats.get('total_traces', 0)}")
    logger.info(f"  Total Spans: {final_stats.get('total_spans', 0)}")
    logger.info(f"  Error Spans: {final_stats.get('error_spans', 0)}")
    logger.info(f"  Total Services: {final_stats.get('total_services', 0)}")
    logger.info(f"  Total Cases: {final_stats.get('total_cases', 0)}")

    rag.close()


if __name__ == "__main__":
    main()
