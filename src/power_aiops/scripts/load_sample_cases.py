"""Sample fault case data for Graph RAG demonstration.

Usage:
    python -m power_aiops.scripts.load_sample_cases
"""

from datetime import datetime, timedelta, timezone

from power_aiops.memory.graph_rag import FaultCase, GraphRAG

SAMPLE_CASES = [
    {
        "case_id": "INC-2024-001",
        "title": "支付服务数据库连接池耗尽",
        "summary": "晚高峰期间，支付服务数据库连接池达到最大限制，导致大量支付请求超时",
        "symptoms": [
            "数据库连接超时",
            "连接池耗尽",
            "支付成功率下降",
            "数据库 CPU 使用率 95%",
            "慢查询堆积",
        ],
        "services": ["payment-service", "order-service", "user-service"],
        "hosts": ["payment-db-01", "payment-db-02"],
        "root_cause": "慢查询未设置超时，积累导致连接池耗尽",
        "resolution": "1. 紧急扩容连接池 2. 杀掉慢查询 3. 添加查询超时配置 4. 优化慢查询索引",
        "severity": "P1",
        "duration_minutes": 45,
        "tags": ["database", "connection-pool", "payment"],
    },
    {
        "case_id": "INC-2024-002",
        "title": "API 网关证书过期导致服务不可用",
        "summary": "API 网关 SSL 证书过期，所有 HTTPS 请求返回 502 错误",
        "symptoms": [
            "HTTPS 502 错误",
            "证书过期告警",
            "网关连接拒绝",
            "SSL handshake failed",
            "所有外部请求失败",
        ],
        "services": ["api-gateway", "user-service", "payment-service"],
        "hosts": ["gateway-01", "gateway-02"],
        "root_cause": "证书续期自动化脚本失效，人工未及时发现",
        "resolution": "1. 紧急更新证书 2. 修复证书续期脚本 3. 添加证书过期监控",
        "severity": "P1",
        "duration_minutes": 30,
        "tags": ["ssl", "certificate", "gateway"],
    },
    {
        "case_id": "INC-2024-003",
        "title": "Redis 缓存雪崩",
        "summary": "大量缓存同时过期，导致数据库瞬时压力过大，服务响应变慢",
        "symptoms": [
            "Redis 缓存失效",
            "数据库 QPS 暴涨",
            "服务响应超时",
            "大量缓存穿透",
            "数据库连接等待",
        ],
        "services": ["user-service", "product-service", "order-service"],
        "hosts": ["redis-master", "redis-slave-1", "redis-slave-2"],
        "root_cause": "缓存 key 设置了相同的过期时间，缺乏随机化",
        "resolution": "1. 为缓存过期时间添加随机偏移 2. 实现缓存预热 3. 添加熔断机制",
        "severity": "P2",
        "duration_minutes": 20,
        "tags": ["redis", "cache", "avalanche"],
    },
    {
        "case_id": "INC-2024-004",
        "title": "Kafka 消息堆积",
        "summary": "订单消息处理消费者崩溃，导致消息在 Kafka 中堆积，用户无法查看订单状态",
        "symptoms": [
            "Kafka 消息堆积",
            "消费 lag 持续增长",
            "订单状态更新延迟",
            "消费者连接失败",
            "分区 rebalance 频繁",
        ],
        "services": ["order-service", "notification-service", "kafka-cluster"],
        "hosts": ["kafka-broker-1", "kafka-broker-2", "consumer-worker-1"],
        "root_cause": "消费者内存泄漏导致 OOM，被 K8s 重启后配置丢失",
        "resolution": "1. 重启消费者实例 2. 修复内存泄漏 3. 添加消费者健康监控 4. 优化资源限制配置",
        "severity": "P2",
        "duration_minutes": 60,
        "tags": ["kafka", "message-queue", "consumer"],
    },
    {
        "case_id": "INC-2024-005",
        "title": "K8s 节点驱逐导致服务中断",
        "summary": "多个 K8s 节点因内存压力被驱逐，运行在其上的微服务实例全部重启",
        "symptoms": [
            "Pod 被驱逐",
            "节点 NotReady",
            "服务实例全部重启",
            "内存使用率 100%",
            "OOMKilled 告警",
        ],
        "services": ["inventory-service", "warehouse-service", "shipping-service"],
        "hosts": ["k8s-node-05", "k8s-node-06", "k8s-node-07"],
        "root_cause": "资源配额设置过小，单个命名空间耗尽节点资源",
        "resolution": "1. 调整资源配额 2. 增加节点 3. 优化内存泄漏 4. 设置 Pod 打散策略",
        "severity": "P1",
        "duration_minutes": 35,
        "tags": ["kubernetes", "oom", "resource"],
    },
    {
        "case_id": "INC-2024-006",
        "title": "DNS 解析故障",
        "summary": "内部 DNS 服务故障，导致微服务间调用全部失败",
        "symptoms": [
            "DNS 解析超时",
            "服务间调用失败",
            "Could not resolve host",
            "DNS Pod CrashLoopBackOff",
            "CoreDNS 不健康",
        ],
        "services": ["core-dns", "all-microservices"],
        "hosts": ["k8s-master-1", "k8s-master-2"],
        "root_cause": "DNS Pod 配置文件被错误修改， upstream DNS 配置丢失",
        "resolution": "1. 恢复 DNS 配置 2. 重启 CoreDNS Pod 3. 验证解析正常 4. 添加配置变更审批",
        "severity": "P1",
        "duration_minutes": 25,
        "tags": ["dns", "kubernetes", "network"],
    },
    {
        "case_id": "INC-2024-007",
        "title": "Elasticsearch 集群脑裂",
        "summary": "ES 集群网络分区导致脑裂，多个主节点同时存在，数据写入异常",
        "symptoms": [
            "ES 集群脑裂",
            "多个主节点",
            "写入失败",
            "分片分配失败",
            "集群状态 yellow/red",
        ],
        "services": ["search-service", "logging-service", "elasticsearch-cluster"],
        "hosts": ["es-node-1", "es-node-2", "es-node-3"],
        "root_cause": "网络抖动导致节点间通信超时，Zen Discovery 判定逻辑触发脑裂",
        "resolution": "1. 手动杀掉多余主节点 2. 等待分片重新分配 3. 调整 discovery 参数 4. 优化网络监控",
        "severity": "P1",
        "duration_minutes": 90,
        "tags": ["elasticsearch", "split-brain", "cluster"],
    },
    {
        "case_id": "INC-2024-008",
        "title": "配置中心推送故障",
        "summary": "Apollo 配置中心推送延迟，导致服务使用过期配置运行",
        "symptoms": [
            "配置推送延迟",
            "服务配置不一致",
            "灰度发布失败",
            "配置监听超时",
            "配置中心 CPU 高",
        ],
        "services": ["apollo-config-server", "user-service", "payment-service"],
        "hosts": ["apollo-admin-1", "apollo-portal-1"],
        "root_cause": "配置发布量大时，推送队列积压，处理能力不足",
        "resolution": "1. 扩容配置推送服务 2. 优化推送批处理 3. 添加推送延迟监控",
        "severity": "P2",
        "duration_minutes": 40,
        "tags": ["apollo", "config-center", "configuration"],
    },
]


def load_sample_cases(rag: GraphRAG | None = None) -> int:
    """Load sample fault cases into the knowledge base.
    
    Returns:
        Number of cases loaded
    """
    if rag is None:
        rag = GraphRAG()

    try:
        # Initialize schema if needed
        rag.initialize_schema()
        
        now = datetime.now(timezone.utc)
        
        for i, case_data in enumerate(SAMPLE_CASES):
            # Set created_at with some spread
            case_data["created_at"] = now - timedelta(days=len(SAMPLE_CASES) - i)
            rag.store_case_dict(case_data)
            
        print(f"Successfully loaded {len(SAMPLE_CASES)} sample cases")
        
        # Print stats
        stats = rag.get_stats()
        print(f"\nKnowledge Base Statistics:")
        print(f"  Total Cases: {stats['total_cases']}")
        print(f"  Total Symptoms: {stats['total_symptoms']}")
        print(f"  Total Root Causes: {stats['total_root_causes']}")
        print(f"  Total Services: {stats['total_services']}")
        
        return len(SAMPLE_CASES)
        
    finally:
        rag.close()


def search_demo(rag: GraphRAG | None = None) -> None:
    """Demonstrate similarity search."""
    if rag is None:
        rag = GraphRAG()

    try:
        print("\n" + "=" * 60)
        print("Graph RAG Similarity Search Demo")
        print("=" * 60)
        
        # Demo search queries
        queries = [
            "数据库连接超时，连接池耗尽",
            "服务之间调用失败，DNS无法解析",
            "服务响应超时，缓存失效",
        ]
        
        for query in queries:
            print(f"\n[Query]: {query}")
            results = rag.vector_search(query, search_type="symptom", top_k=3)
            for i, r in enumerate(results, 1):
                print(f"  {i}. {r['case_id']} - {r['title']}")
                print(f"     Severity: {r['severity']}, Resolution: {r.get('resolution', 'N/A')[:50]}...")
                
    finally:
        rag.close()


if __name__ == "__main__":
    load_sample_cases()
    search_demo()
