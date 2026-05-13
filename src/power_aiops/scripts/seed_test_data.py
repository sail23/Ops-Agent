"""填充 Neo4j 测试数据 - 覆盖所有节点类型和关系。

Usage:
    python -m power_aiops.scripts.seed_test_data

数据规模：
  - 10 个服务节点
  - 12 个主机节点
  - 8 个故障案例（含症状、根因、解决方案）
  - 30 条链路追踪（含正常 / 慢 / 错误三种类型）
  - 故障案例之间的关联关系 [:RELATED_TO]
  - 链路与故障案例的关联 [:RELATED_TO]
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from random import choice, randint, uniform

from power_aiops.memory.graph_rag import FaultCase, GraphRAG, TraceSpan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 测试数据定义
# ─────────────────────────────────────────────────────────────────────────────

SERVICES = [
    "order-service",
    "payment-service",
    "product-service",
    "user-service",
    "inventory-service",
    "notification-service",
    "gateway-service",
    "auth-service",
    "search-service",
    "analytics-service",
]

HOSTS = [
    "prod-web-01",
    "prod-web-02",
    "prod-api-01",
    "prod-api-02",
    "prod-db-master",
    "prod-db-replica",
    "prod-cache-01",
    "prod-cache-02",
    "prod-mq-01",
    "prod-mq-02",
    "prod-search-01",
    "prod-analytics-01",
]

OPERATIONS = [
    "http.GET./api/orders",
    "http.POST./api/orders",
    "http.GET./api/payments",
    "http.POST./api/payments",
    "http.GET./api/products",
    "http.GET./api/users",
    "http.POST./api/auth/login",
    "mysql.query",
    "mysql.insert",
    "redis.GET",
    "redis.SET",
    "redis.DEL",
    "kafka.send",
    "kafka.consume",
    "elasticsearch.search",
    "grpc.Call",
]


# ─────────────────────────────────────────────────────────────────────────────
# 故障案例
# ─────────────────────────────────────────────────────────────────────────────

FAULT_CASES = [
    {
        "case_id": "CASE-001",
        "title": "订单服务数据库连接池耗尽",
        "summary": "MySQL 连接池达到上限，新请求全部超时，订单服务不可用约 25 分钟。",
        "severity": "P1",
        "duration_minutes": 25,
        "tags": ["数据库", "连接池", "订单服务"],
        "symptoms": [
            "Connection timeout",
            "Service unavailable",
            "数据库连接失败",
            "响应时间超过 10 秒",
            "Hystrix 熔断触发",
        ],
        "services": ["order-service", "payment-service", "gateway-service"],
        "hosts": ["prod-api-01", "prod-api-02", "prod-db-master"],
        "root_cause": "MySQL max_connections 配置为 100，业务高峰期实际需要 150+ 连接",
        "resolution": "1. 临时调高 max_connections 至 500；2. 重启订单服务释放现有连接；3. 后续优化连接池复用率和慢查询",
        "metadata": {"mttd_minutes": 3, "mttr_minutes": 25, "affected_users": 12500},
    },
    {
        "case_id": "CASE-002",
        "title": "Redis 缓存雪崩导致服务雪崩",
        "summary": "大量缓存 key 同时过期，数据库被打满，所有依赖服务响应超时。",
        "severity": "P1",
        "duration_minutes": 18,
        "tags": ["缓存", "Redis", "雪崩"],
        "symptoms": [
            "Cache miss",
            "Response timeout",
            "数据库 CPU 飙升",
            "缓存命中率从 95% 跌至 0%",
            "服务雪崩",
        ],
        "services": ["user-service", "product-service", "order-service"],
        "hosts": ["prod-cache-01", "prod-cache-02", "prod-db-master"],
        "root_cause": "业务代码设置 TTL 时使用了固定值，导致数万个 key 同时过期",
        "resolution": "1. 紧急刷新缓存；2. 实现随机 TTL（基础 TTL + 随机偏移量）；3. 增加缓存预热机制",
        "metadata": {"mttd_minutes": 2, "mttr_minutes": 18, "affected_users": 35000},
    },
    {
        "case_id": "CASE-003",
        "title": "Kafka 消费者滞后导致消息堆积",
        "summary": "订单通知消息在 Kafka 中堆积超过 5 万条，用户无法收到支付成功通知。",
        "severity": "P2",
        "duration_minutes": 42,
        "tags": ["Kafka", "消息队列", "消费者"],
        "symptoms": [
            "Message lag",
            "Processing delay",
            "消费吞吐量严重下降",
            "消息堆积超过 5 万条",
        ],
        "services": ["notification-service", "order-service"],
        "hosts": ["prod-mq-01", "prod-mq-02"],
        "root_cause": "消费者处理逻辑中存在 N+1 数据库查询，单条消息处理耗时从 5ms 飙升至 200ms",
        "resolution": "1. 增加消费者实例从 2 个到 8 个；2. 优化数据库查询，使用批量查询替代 N+1；3. 增加消息处理超时熔断",
        "metadata": {"mttd_minutes": 8, "mttr_minutes": 42, "affected_users": 8200},
    },
    {
        "case_id": "CASE-004",
        "title": "Elasticsearch 搜索服务 OOM",
        "summary": "搜索服务内存溢出，Pod 被 Kubernetes 重启，搜索功能不可用 15 分钟。",
        "severity": "P2",
        "duration_minutes": 15,
        "tags": ["ES", "OOM", "搜索"],
        "symptoms": [
            "OutOfMemoryError",
            "Pod restart",
            "搜索请求全部返回 500",
            "JVM heap 使用率 100%",
        ],
        "services": ["search-service", "product-service"],
        "hosts": ["prod-search-01"],
        "root_cause": "查询条件缺少 size 限制，导致返回全量数据；且 Elasticsearch JVM heap 仅设置了 2GB",
        "resolution": "1. 紧急重启 ES 节点；2. 代码层增加查询 size 上限（默认 10000）；3. JVM heap 调大至 8GB；4. 增加查询超时配置",
        "metadata": {"mttd_minutes": 5, "mttr_minutes": 15, "affected_users": 15000},
    },
    {
        "case_id": "CASE-005",
        "title": "JWT Token 签名密钥轮换导致全员登出",
        "summary": "运维误将 JWT 签名密钥替换，新 token 无法被旧版本服务验证，约 10 万用户被迫重新登录。",
        "severity": "P2",
        "duration_minutes": 35,
        "tags": ["JWT", "认证", "密钥轮换"],
        "symptoms": [
            "401 Unauthorized",
            "Token validation failed",
            "大量用户被迫重新登录",
            "登录 QPS 暴涨 20 倍",
        ],
        "services": ["auth-service", "gateway-service", "user-service"],
        "hosts": ["prod-api-01", "prod-api-02"],
        "root_cause": "JWT 签名密钥在多个环境间不一致，轮换时未做灰度验证",
        "resolution": "1. 立即回滚 JWT 密钥；2. 建立密钥轮换 SOP，增加灰度验证步骤；3. 引入 key version 机制支持多版本共存",
        "metadata": {"mttd_minutes": 10, "mttr_minutes": 35, "affected_users": 100000},
    },
    {
        "case_id": "CASE-006",
        "title": "库存服务分布式锁竞争导致超卖",
        "summary": "Redis 分布式锁粒度过粗，多个服务实例同时持有锁，导致商品超卖 200+ 单。",
        "severity": "P1",
        "duration_minutes": 8,
        "tags": ["分布式锁", "Redis", "库存", "超卖"],
        "symptoms": [
            "商品超卖",
            "锁竞争激烈",
            "库存扣减失败",
            "Redis CPU 飙升",
            "分布式锁获取超时",
        ],
        "services": ["inventory-service", "order-service", "payment-service"],
        "hosts": ["prod-api-01", "prod-api-02", "prod-cache-01"],
        "root_cause": "锁的粒度按商品维度加锁，但实现中使用了错误的 Lua 脚本，锁被错误释放",
        "resolution": "1. 紧急冻结相关订单；2. 修复 Lua 脚本添加 owner token 校验；3. 人工补偿超卖订单；4. 增加锁超时配置",
        "metadata": {"mttd_minutes": 2, "mttr_minutes": 8, "affected_users": 200},
    },
    {
        "case_id": "CASE-007",
        "title": "数据分析服务计算集群 OOM",
        "summary": "Spark 任务申请了过大内存，集群资源被占满，其他报表任务全部排队等待。",
        "severity": "P3",
        "duration_minutes": 120,
        "tags": ["Spark", "大数据", "OOM", "资源竞争"],
        "symptoms": [
            "Spark executor OOM",
            "任务队列积压",
            "报表生成延迟",
            "集群内存使用率 95%",
        ],
        "services": ["analytics-service"],
        "hosts": ["prod-analytics-01"],
        "root_cause": "数据分析师提交了全量数据扫描任务，executor 申请了 100GB 内存",
        "resolution": "1. Kill 超大内存任务；2. 增加集群调度器的内存配额限制；3. 引入 SQL 审核流程",
        "metadata": {"mttd_minutes": 30, "mttr_minutes": 120, "affected_users": 500},
    },
    {
        "case_id": "CASE-008",
        "title": "网关服务证书过期导致 HTTPS 不可用",
        "summary": "SSL 证书过期，所有外部请求返回证书错误，业务中断约 1 小时。",
        "severity": "P1",
        "duration_minutes": 65,
        "tags": ["SSL", "证书", "网关", "HTTPS"],
        "symptoms": [
            "SSL handshake failed",
            "证书过期",
            "HTTPS 请求全部失败",
            "证书告警未处理",
        ],
        "services": ["gateway-service", "order-service", "payment-service", "user-service"],
        "hosts": ["prod-web-01", "prod-web-02"],
        "root_cause": "证书续期告警被静默，未纳入值班告警；SSL 证书有效期 1 年，遗忘手动更新",
        "resolution": "1. 紧急更新 SSL 证书；2. 配置 cert-manager 自动续期；3. 将证书到期告警纳入关键告警",
        "metadata": {"mttd_minutes": 15, "mttr_minutes": 65, "affected_users": 50000},
    },
]

# 故障案例之间的关联关系（同一类根因 / 同一类症状）
FAULT_CASE_LINKS = [
    ("CASE-001", "CASE-006"),  # 都涉及 Redis/MySQL 连接问题
    ("CASE-002", "CASE-004"),  # 都涉及缓存/ES 雪崩问题
    ("CASE-005", "CASE-008"),  # 都涉及基础设施配置问题
    ("CASE-001", "CASE-003"),  # 都涉及数据库连接资源问题
    ("CASE-002", "CASE-003"),  # 都涉及消息处理延迟
]


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_vector(vector: list[float]) -> None:
    """Normalize vector in-place to unit length."""
    magnitude = math.sqrt(sum(x * x for x in vector))
    if magnitude > 0:
        for i in range(len(vector)):
            vector[i] /= magnitude


def _sha256_fallback(text: str, dim: int = 256) -> list[float]:
    """Generate a deterministic hash-based vector for text."""
    text_bytes = text.encode("utf-8")
    hash_digest = hashlib.sha256(text_bytes).digest()
    vector = []
    for i in range(dim):
        byte_idx = i % len(hash_digest)
        value = float(hash_digest[byte_idx]) / 255.0 * 2.0 - 1.0
        vector.append(value)
    _normalize_vector(vector)
    return vector


def _generate_spans_for_trace(
    trace_id: str,
    service: str,
    base_time: datetime,
    is_slow: bool = False,
    has_error: bool = False,
    error_msg: str = "",
) -> list[TraceSpan]:
    """Generate spans for a single trace."""
    num_spans = randint(4, 8)
    spans = []

    # Root span
    root_duration = uniform(3000, 8000) if is_slow else uniform(80, 400)
    spans.append(
        TraceSpan(
            span_id=f"{trace_id}-root",
            trace_id=trace_id,
            parent_span_id=None,
            service=service,
            operation="http.GET./api/orders",
            start_time=base_time,
            duration_ms=root_duration,
            status="ERROR" if has_error else "OK",
            error_message=error_msg if has_error else "",
            tags={"http.status_code": 500 if has_error else 200, "span.kind": "server"},
        )
    )

    # Child spans
    prev_span_id = f"{trace_id}-root"
    for i in range(num_spans):
        svc_name = choice(SERVICES)
        op = choice(OPERATIONS)
        is_error_span = has_error and i == num_spans - 1
        duration = (
            uniform(2000, 6000) if is_slow else uniform(20, 300)
        )
        spans.append(
            TraceSpan(
                span_id=f"{trace_id}-{i:03d}",
                trace_id=trace_id,
                parent_span_id=prev_span_id,
                service=svc_name,
                operation=op,
                start_time=base_time + timedelta(milliseconds=i * 40),
                duration_ms=duration,
                status="ERROR" if is_error_span else ("TIMEOUT" if is_slow and i % 2 == 0 else "OK"),
                error_message=error_msg if is_error_span else "",
                tags={
                    "span.kind": "client" if i % 2 == 0 else "server",
                    "db.system": "mysql" if "mysql" in op else "redis",
                },
            )
        )
        prev_span_id = f"{trace_id}-{i:03d}"

    return spans


# ─────────────────────────────────────────────────────────────────────────────
# 主填充逻辑
# ─────────────────────────────────────────────────────────────────────────────

def seed_all(rag: GraphRAG) -> dict:
    """Fill the database with comprehensive test data."""
    stats = {
        "services": 0,
        "hosts": 0,
        "fault_cases": 0,
        "traces": 0,
        "spans": 0,
        "case_links": 0,
    }

    # ── 1. 初始化 Schema（约束 + 索引）───────────────────────────────────────
    logger.info("初始化 Neo4j Schema...")
    rag.initialize_schema()

    # ── 2. 写入服务节点 ──────────────────────────────────────────────────────
    logger.info("写入服务节点...")
    with rag._get_session() as session:
        for svc in SERVICES:
            session.run(
                "MERGE (s:Service {name: $name}) SET s.status = $status, s.metadata = $meta",
                name=svc, status="active", meta=str({"region": "us-east-1", "version": "v2.1.0"}),
            )
            stats["services"] += 1
    logger.info(f"  已写入 {stats['services']} 个服务节点")

    # ── 3. 写入主机节点 ──────────────────────────────────────────────────────
    logger.info("写入主机节点...")
    with rag._get_session() as session:
        for host in HOSTS:
            session.run(
                "MERGE (h:Host {name: $name}) SET h.ip = $ip, h.env = $env",
                name=host,
                ip=f"10.0.{randint(1, 255)}.{randint(1, 254)}",
                env="production",
            )
            stats["hosts"] += 1
    logger.info(f"  已写入 {stats['hosts']} 个主机节点")

    # ── 4. 写入故障案例（含症状、根因、解决方案、关联主机）───────────────────
    logger.info("写入故障案例...")
    for fc in FAULT_CASES:
        case = FaultCase(
            case_id=fc["case_id"],
            title=fc["title"],
            summary=fc["summary"],
            severity=fc["severity"],
            duration_minutes=fc["duration_minutes"],
            created_at=datetime.now(timezone.utc) - timedelta(days=randint(1, 60)),
            resolved_at=datetime.now(timezone.utc) - timedelta(days=randint(1, 30)),
            symptoms=fc["symptoms"],
            services=fc["services"],
            hosts=fc["hosts"],
            root_cause=fc["root_cause"],
            resolution=fc["resolution"],
            tags=fc["tags"],
            metadata=fc["metadata"],
        )
        rag.store_case(case)
        stats["fault_cases"] += 1
    logger.info(f"  已写入 {stats['fault_cases']} 个故障案例")

    # ── 5. 关联故障案例（[:RELATED_TO]）──────────────────────────────────────
    logger.info("建立故障案例关联关系...")
    for (cid1, cid2) in FAULT_CASE_LINKS:
        with rag._get_session() as session:
            session.run(
                """
                MATCH (a:FaultCase {case_id: $c1}), (b:FaultCase {case_id: $c2})
                MERGE (a)-[:RELATED_TO]->(b)
                """,
                c1=cid1, c2=cid2,
            )
        stats["case_links"] += 1
    logger.info(f"  已建立 {stats['case_links']} 条关联关系")

    # ── 6. 生成链路追踪 ──────────────────────────────────────────────────────
    logger.info("生成链路追踪数据...")

    now = datetime.now(timezone.utc)

    # 6a. 正常链路 (20 条)
    for i in range(20):
        trace_id = f"trace-normal-{now.strftime('%Y%m%d')}-{i:04d}"
        service = choice(SERVICES)
        base_time = now - timedelta(hours=randint(1, 72), minutes=randint(0, 59))
        spans = _generate_spans_for_trace(trace_id, service, base_time, is_slow=False, has_error=False)
        rag.store_trace(trace_id, spans)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    # 6b. 慢链路 (8 条，耗时 > 5s)
    for i in range(8):
        trace_id = f"trace-slow-{now.strftime('%Y%m%d')}-{i:04d}"
        service = choice(SERVICES)
        base_time = now - timedelta(hours=randint(1, 48), minutes=randint(0, 59))
        spans = _generate_spans_for_trace(trace_id, service, base_time, is_slow=True, has_error=False)
        rag.store_trace(trace_id, spans)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    # 6c. 错误链路 (5 条)
    error_messages = [
        "Database connection pool exhausted",
        "Redis timeout: connection refused",
        "HTTP 503 Service Unavailable",
        "Kafka consumer poll timeout",
        "Elasticsearch circuit breaker open",
    ]
    for i in range(5):
        trace_id = f"trace-error-{now.strftime('%Y%m%d')}-{i:04d}"
        service = choice(SERVICES)
        base_time = now - timedelta(hours=randint(1, 24), minutes=randint(0, 59))
        error_msg = error_messages[i % len(error_messages)]
        spans = _generate_spans_for_trace(trace_id, service, base_time, is_slow=False, has_error=True, error_msg=error_msg)
        # 关联到最近的故障案例
        related_case = FAULT_CASES[i % len(FAULT_CASES)]["case_id"]
        rag.store_trace(trace_id, spans, related_case_id=related_case)
        stats["traces"] += 1
        stats["spans"] += len(spans)

    logger.info(f"  已写入 {stats['traces']} 条链路 + {stats['spans']} 个 Span")

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("开始填充 Neo4j 测试数据")
    logger.info("=" * 60)

    rag = GraphRAG()

    # 先清空已有数据（可选，防止重复）
    logger.info("清空现有数据...")
    try:
        with rag._get_session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.info("数据已清空")
    except Exception as e:
        logger.warning(f"清空数据时出错（可能数据库为空）: {e}")

    # 填充数据
    stats = seed_all(rag)

    # 验证
    logger.info("=" * 60)
    logger.info("验证数据库状态:")
    final = rag.get_stats()
    for key, val in final.items():
        logger.info(f"  {key:20s}: {val}")

    rag.close()

    logger.info("=" * 60)
    logger.info("✅ 填充完成!")
    logger.info(f"   服务节点    : {stats['services']}")
    logger.info(f"   主机节点    : {stats['hosts']}")
    logger.info(f"   故障案例    : {stats['fault_cases']}")
    logger.info(f"   案例关联    : {stats['case_links']}")
    logger.info(f"   链路追踪    : {stats['traces']}")
    logger.info(f"   Span 总数   : {stats['spans']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
