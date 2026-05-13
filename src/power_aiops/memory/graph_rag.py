"""Neo4j Graph RAG implementation for fault case knowledge management.

Graph Structure:
    (FaultCase)-
        [:AFFECTS]->(Service)
        [:HAS_SYMPTOM]->(Symptom {embedding})  <- 向量索引
        [:CAUSED_BY]->(RootCause {embedding})  <- 向量索引
        [:AFFECTED_HOST]->(Host)
        [:RESOLVED_BY]->(Resolution)
        [:RELATED_TO*1..2]->(FaultCase)

    (Trace)-
        [:CONTAINS]->(Span)
        [:RELATED_TO]->(FaultCase)
    (Span)-
        [:CALLS]->(Span)  <- parent-child 关系
        [:BELONGS_TO]->(Service)

Usage:
    rag = GraphRAG()
    rag.initialize_schema()
    rag.store_case(case_data)
    rag.store_trace(trace_id, spans)
    results = rag.vector_search("数据库连接超时", top_k=5)
    trace_tree = rag.get_trace_tree(trace_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from neo4j import GraphDatabase

from power_aiops.config import get_settings
from power_aiops.llm.embedding import ZhipuEmbeddingClient, _normalize_vector, _hash_embedding_fallback

logger = logging.getLogger(__name__)


@dataclass
class FaultCase:
    """故障案例数据结构."""

    case_id: str
    title: str
    summary: str
    symptoms: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    root_cause: str = ""
    resolution: str = ""
    severity: str = "P2"  # P1/P2/P3/P4
    duration_minutes: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    related_case_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceSpan:
    """链路追踪跨度数据."""

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    service: str = ""
    operation: str = ""
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0
    status: str = "OK"  # OK / ERROR / TIMEOUT
    error_message: str = ""
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class TraceContext:
    """完整链路上下文."""

    trace_id: str
    spans: list[TraceSpan] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: float = 0.0
    total_spans: int = 0
    error_spans: int = 0


class GraphRAG:
    """Neo4j Graph RAG for fault case knowledge base."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        embedding_client: ZhipuEmbeddingClient | None = None,
    ):
        settings = get_settings()
        self._uri = uri or settings.neo4j_uri
        self._user = user or settings.neo4j_user
        self._password = password or settings.neo4j_password
        self._embedding_client = embedding_client
        self._driver = None

    @property
    def embedding_client(self) -> ZhipuEmbeddingClient | None:
        return self._embedding_client

    def _get_driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
            )
        return self._driver

    def _get_session(self, database: str = "neo4j"):
        """Get a Neo4j session with specified database."""
        return self._get_driver().session(database=database)

    def _ensure_embedding_client(self) -> ZhipuEmbeddingClient:
        """Lazy initialization of embedding client."""
        if self._embedding_client is None:
            self._embedding_client = ZhipuEmbeddingClient()
        return self._embedding_client

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def health_check(self) -> bool:
        """Check if Neo4j connection is healthy."""
        try:
            with self._get_session() as session:
                result = session.run("RETURN 1 AS health")
                return result.single()["health"] == 1
        except Exception as e:
            logger.error(f"Neo4j health check failed: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Schema Initialization
    # ─────────────────────────────────────────────────────────────────────────

    def initialize_schema(self) -> None:
        """Initialize Neo4j schema with constraints and vector indexes."""
        with self._get_session() as session:
            # Node property constraints
            session.run("""
                CREATE CONSTRAINT fault_case_id IF NOT EXISTS
                FOR (c:FaultCase) REQUIRE c.case_id IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT symptom_name IF NOT EXISTS
                FOR (s:Symptom) REQUIRE s.name IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT service_name IF NOT EXISTS
                FOR (s:Service) REQUIRE s.name IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT host_name IF NOT EXISTS
                FOR (h:Host) REQUIRE h.name IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT root_cause_text IF NOT EXISTS
                FOR (r:RootCause) REQUIRE r.text IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT resolution_text IF NOT EXISTS
                FOR (r:Resolution) REQUIRE r.text IS UNIQUE
            """)

            # Vector indexes for semantic search (Neo4j 5.x syntax)
            session.run("""
                CREATE VECTOR INDEX symptom_embedding IF NOT EXISTS
                FOR (s:Symptom) ON (s.embedding)
            """)

            session.run("""
                CREATE VECTOR INDEX rootcause_embedding IF NOT EXISTS
                FOR (r:RootCause) ON (r.embedding)
            """)

            # Composite indexes for common queries
            session.run("""
                CREATE INDEX fault_case_severity IF NOT EXISTS
                FOR (c:FaultCase) ON (c.severity)
            """)
            session.run("""
                CREATE INDEX fault_case_created_at IF NOT EXISTS
                FOR (c:FaultCase) ON (c.created_at)
            """)

            # Trace/Span indexes
            session.run("""
                CREATE CONSTRAINT trace_id IF NOT EXISTS
                FOR (t:Trace) REQUIRE t.trace_id IS UNIQUE
            """)
            session.run("""
                CREATE CONSTRAINT span_id IF NOT EXISTS
                FOR (s:Span) REQUIRE s.span_id IS UNIQUE
            """)
            session.run("""
                CREATE INDEX trace_start_time IF NOT EXISTS
                FOR (t:Trace) ON (t.start_time)
            """)
            session.run("""
                CREATE INDEX span_service IF NOT EXISTS
                FOR (s:Span) ON (s.service)
            """)
            session.run("""
                CREATE INDEX span_status IF NOT EXISTS
                FOR (s:Span) ON (s.status)
            """)

            logger.info("Graph RAG schema initialized successfully")

    # ─────────────────────────────────────────────────────────────────────────
    # Text Embedding (Zhipu AI)
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # Case Storage
    # ─────────────────────────────────────────────────────────────────────────

    def store_case(self, case: FaultCase) -> None:
        """Store a fault case and all related entities in the graph.

        Uses batch embedding (single API call for all symptoms + root cause).
        """
        # Collect all texts that need embeddings, generate in one batch call
        embed_texts: list[str] = []
        embed_indices: dict[str, list[int]] = {"symptom": [], "root_cause": []}

        for i, symptom_text in enumerate(case.symptoms):
            embed_texts.append(symptom_text)
            embed_indices["symptom"].append(i)

        rc_idx = -1
        if case.root_cause:
            rc_idx = len(embed_texts)
            embed_texts.append(case.root_cause)
            embed_indices["root_cause"].append(rc_idx)

        # Batch generate all embeddings
        embeddings: list[list[float]] = []
        if embed_texts:
            client = self._ensure_embedding_client()
            embeddings = client.embed_batch_with_fallback(embed_texts)

        # Map embeddings back to symptoms/root_cause
        symptom_embeddings: dict[int, list[float]] = {}
        for idx in embed_indices["symptom"]:
            symptom_embeddings[idx] = embeddings[idx] if idx < len(embeddings) else _hash_embedding_fallback(case.symptoms[idx])

        root_cause_embedding: list[float] | None = None
        for idx in embed_indices["root_cause"]:
            root_cause_embedding = embeddings[idx] if idx < len(embeddings) else _hash_embedding_fallback(case.root_cause)

        with self._get_session() as session:
            # Create FaultCase node
            session.run("""
                MERGE (c:FaultCase {case_id: $case_id})
                SET c.title = $title,
                    c.summary = $summary,
                    c.severity = $severity,
                    c.duration_minutes = $duration_minutes,
                    c.created_at = datetime($created_at),
                    c.resolved_at = CASE WHEN $resolved_at IS NOT NULL THEN datetime($resolved_at) ELSE null END,
                    c.tags = $tags,
                    c.metadata = $metadata
            """,
                case_id=case.case_id,
                title=case.title,
                summary=case.summary,
                severity=case.severity,
                duration_minutes=case.duration_minutes,
                created_at=case.created_at.isoformat(),
                resolved_at=case.resolved_at.isoformat() if case.resolved_at else None,
                tags=case.tags,
                metadata=str(case.metadata),
            )

            # UNWIND batch for symptoms
            if case.symptoms:
                symptom_params = []
                for i, symptom_text in enumerate(case.symptoms):
                    symptom_params.append({
                        "symptom": symptom_text[:500],
                        "embedding": symptom_embeddings.get(i, []),
                    })
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    UNWIND $symptoms AS sym
                    MERGE (s:Symptom {name: sym.symptom})
                    SET s.embedding = sym.embedding
                    MERGE (c)-[:HAS_SYMPTOM]->(s)
                """, case_id=case.case_id, symptoms=symptom_params)

            # UNWIND batch for services
            if case.services:
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    UNWIND $services AS svc
                    MERGE (s:Service {name: svc})
                    MERGE (c)-[:AFFECTS]->(s)
                """, case_id=case.case_id, services=case.services)

            # UNWIND batch for hosts
            if case.hosts:
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    UNWIND $hosts AS h
                    MERGE (host:Host {name: h})
                    MERGE (c)-[:AFFECTED_HOST]->(host)
                """, case_id=case.case_id, hosts=case.hosts)

            # Root cause with embedding
            if case.root_cause and root_cause_embedding is not None:
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    MERGE (r:RootCause {text: $root_cause})
                    SET r.embedding = $embedding
                    MERGE (c)-[:CAUSED_BY]->(r)
                """,
                    case_id=case.case_id,
                    root_cause=case.root_cause[:500],
                    embedding=root_cause_embedding,
                )

            # Resolution
            if case.resolution:
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    MERGE (r:Resolution {text: $resolution})
                    MERGE (c)-[:RESOLVED_BY]->(r)
                """,
                    case_id=case.case_id,
                    resolution=case.resolution[:1000],
                )

            # UNWIND batch for related cases
            if case.related_case_ids:
                session.run("""
                    MATCH (c:FaultCase {case_id: $case_id})
                    UNWIND $related_ids AS rid
                    MATCH (r:FaultCase {case_id: rid})
                    MERGE (c)-[:RELATED_TO]->(r)
                """, case_id=case.case_id, related_ids=case.related_case_ids)

        logger.info(f"Stored fault case: {case.case_id} - {case.title}")

    def store_case_dict(self, case_data: dict) -> None:
        """Store a fault case from dictionary format."""
        case = FaultCase(
            case_id=case_data["case_id"],
            title=case_data["title"],
            summary=case_data["summary"],
            symptoms=case_data.get("symptoms", []),
            services=case_data.get("services", []),
            hosts=case_data.get("hosts", []),
            root_cause=case_data.get("root_cause", ""),
            resolution=case_data.get("resolution", ""),
            severity=case_data.get("severity", "P2"),
            duration_minutes=case_data.get("duration_minutes", 0),
            tags=case_data.get("tags", []),
            metadata=case_data.get("metadata", {}),
        )
        self.store_case(case)

    def store_incident(self, incident: Any) -> None:
        """Store an incident from IncidentContext object.

        Args:
            incident: IncidentContext or compatible object with incident fields
        """
        # Extract fields from incident
        case_data = {
            "case_id": getattr(incident, "incident_id", ""),
            "title": getattr(incident, "title", "") or getattr(incident, "summary", "")[:100],
            "summary": getattr(incident, "summary", "") or getattr(incident, "description", ""),
            "symptoms": getattr(incident, "symptoms", []) or [],
            "services": [getattr(incident, "service_name", "")] if getattr(incident, "service_name", "") else [],
            "hosts": [],
            "root_cause": getattr(incident, "root_cause", ""),
            "resolution": getattr(incident, "resolution", ""),
            "severity": getattr(incident, "severity", "P2") or "P2",
            "duration_minutes": 0,
            "tags": getattr(incident, "metadata", {}).get("tags", []) if isinstance(getattr(incident, "metadata", {}), dict) else [],
            "metadata": getattr(incident, "metadata", {}) if isinstance(getattr(incident, "metadata", {}), dict) else {},
        }
        self.store_case_dict(case_data)

    # ─────────────────────────────────────────────────────────────────────────
    # Vector Similarity Search
    # ─────────────────────────────────────────────────────────────────────────

    def vector_search(
        self,
        query_text: str,
        search_type: str = "symptom",
        top_k: int = 5,
    ) -> list[dict]:
        """Search for similar fault cases using vector similarity.

        Args:
            query_text: The symptom/root cause text to search for
            search_type: "symptom" or "root_cause"
            top_k: Number of results to return
        """
        client = self._ensure_embedding_client()
        query_vector = client.embed_single(query_text)

        with self._get_session() as session:
            if search_type == "symptom":
                result = session.run("""
                    CALL db.index.vector.queryNodes('symptom_embedding', $top_k, $query_vector)
                    YIELD node AS symptom, score AS similarity
                    MATCH (c:FaultCase)-[:HAS_SYMPTOM]->(symptom)
                    WITH c, symptom, similarity
                    ORDER BY similarity DESC
                    WITH c, collect({symptom: symptom.name, score: similarity}) AS symptom_matches,
                           max(similarity) AS top_similarity
                    OPTIONAL MATCH (c)-[:RESOLVED_BY]->(r:Resolution)
                    RETURN c.case_id AS case_id,
                           c.title AS title,
                           c.summary AS summary,
                           c.severity AS severity,
                           symptom_matches AS matched_symptoms,
                           r.text AS resolution,
                           c.created_at AS created_at,
                           top_similarity AS similarity
                    LIMIT $top_k
                """, query_vector=query_vector, top_k=top_k)

            else:  # root_cause
                result = session.run("""
                    CALL db.index.vector.queryNodes('rootcause_embedding', $top_k, $query_vector)
                    YIELD node AS rootcause, score AS similarity
                    MATCH (c:FaultCase)-[:CAUSED_BY]->(rootcause)
                    WITH c, rootcause, similarity
                    ORDER BY similarity DESC
                    WITH c, collect({root_cause: rootcause.text, score: similarity}) AS cause_matches,
                           max(similarity) AS top_similarity
                    OPTIONAL MATCH (c)-[:HAS_SYMPTOM]->(s:Symptom)
                    WITH c, cause_matches, top_similarity, collect(s.name)[0] AS primary_symptom
                    OPTIONAL MATCH (c)-[:RESOLVED_BY]->(r:Resolution)
                    RETURN c.case_id AS case_id,
                           c.title AS title,
                           c.summary AS summary,
                           c.severity AS severity,
                           cause_matches AS matched_causes,
                           primary_symptom AS main_symptom,
                           r.text AS resolution,
                           c.created_at AS created_at,
                           top_similarity AS similarity
                    LIMIT $top_k
                """, query_vector=query_vector, top_k=top_k)

            return [dict(record) for record in result]

    def search_similar_cases(
        self,
        case: FaultCase | dict,
        top_k: int = 5,
    ) -> list[dict]:
        """Search for similar fault cases based on a case's symptoms and services.

        Args:
            case: FaultCase object or dict with symptoms/services
            top_k: Number of results to return
        """
        if isinstance(case, str):
            # Fallback: treat the string as a search query directly
            results = self.vector_search(case, search_type="symptom", top_k=top_k)
            return [{
                "incident_id": r.get("case_id", ""),
                "source": "graph",
                "score": r.get("similarity", 0.0),
                "title": r.get("title", ""),
                "root_cause": r.get("matched_causes", [{}])[0].get("root_cause", "") if isinstance(r.get("matched_causes"), list) else "",
                "symptoms": r.get("matched_symptoms", []) if isinstance(r.get("matched_symptoms"), list) else [],
            } for r in results]

        if isinstance(case, dict):
            symptoms = case.get("symptoms", [])
            services = case.get("services", [])
        else:
            symptoms = case.symptoms
            services = case.services

        all_results = {}

        # Search by each symptom
        for symptom in symptoms[:3]:
            results = self.vector_search(symptom, search_type="symptom", top_k=top_k)
            for r in results:
                case_id = r["case_id"]
                if case_id not in all_results:
                    all_results[case_id] = r
                    all_results[case_id]["match_score"] = 0
                all_results[case_id]["match_score"] += 1

        # Sort by match score
        sorted_results = sorted(
            all_results.values(),
            key=lambda x: (x.get("match_score", 0), x.get("matched_symptoms", [{}])[0].get("score", 0)),
            reverse=True,
        )

        return sorted_results[:top_k]

    # ─────────────────────────────────────────────────────────────────────────
    # Graph Traversal Queries
    # ─────────────────────────────────────────────────────────────────────────

    def get_case_details(self, case_id: str) -> dict | None:
        """Get full details of a fault case including related entities."""
        with self._get_session() as session:
            result = session.run("""
                MATCH (c:FaultCase {case_id: $case_id})
                OPTIONAL MATCH (c)-[:HAS_SYMPTOM]->(s:Symptom)
                OPTIONAL MATCH (c)-[:AFFECTS]->(svc:Service)
                OPTIONAL MATCH (c)-[:AFFECTED_HOST]->(h:Host)
                OPTIONAL MATCH (c)-[:CAUSED_BY]->(r:RootCause)
                OPTIONAL MATCH (c)-[:RESOLVED_BY]->(res:Resolution)
                OPTIONAL MATCH (c)-[:RELATED_TO]->(related:FaultCase)
                RETURN c,
                       collect(DISTINCT s.name) AS symptoms,
                       collect(DISTINCT svc.name) AS services,
                       collect(DISTINCT h.name) AS hosts,
                       r.text AS root_cause,
                       res.text AS resolution,
                       collect(DISTINCT related.case_id) AS related_cases
            """, case_id=case_id)

            record = result.single()
            if record:
                c = record["c"]
                return {
                    "case_id": c["case_id"],
                    "title": c.get("title", ""),
                    "summary": c.get("summary", ""),
                    "severity": c.get("severity", ""),
                    "duration_minutes": c.get("duration_minutes", 0),
                    "created_at": str(c.get("created_at", "")),
                    "symptoms": record["symptoms"] or [],
                    "services": record["services"] or [],
                    "hosts": record["hosts"] or [],
                    "root_cause": record["root_cause"] or "",
                    "resolution": record["resolution"] or "",
                    "related_cases": record["related_cases"] or [],
                }
            return None

    def get_resolution_for_symptom(self, symptom: str) -> list[dict]:
        """Find historical resolutions for similar symptoms."""
        results = self.vector_search(symptom, search_type="symptom", top_k=5)
        resolutions = []
        for r in results:
            if r.get("resolution"):
                resolutions.append({
                    "case_id": r["case_id"],
                    "title": r.get("title", ""),
                    "symptom": symptom,
                    "resolution": r["resolution"],
                    "severity": r.get("severity", ""),
                })
        return resolutions

    def get_incident_context(self, case_id: str) -> str:
        """Generate a formatted context string for an incident case.
        
        Used to inject historical case context into agent prompts.
        """
        case = self.get_case_details(case_id)
        if not case:
            return ""

        lines = [
            f"## 历史案例参考: {case['title']}",
            f"- 案例ID: {case['case_id']}",
            f"- 严重程度: {case['severity']}",
            f"- 影响时长: {case['duration_minutes']} 分钟",
        ]

        if case["symptoms"]:
            lines.append(f"- 相似症状: {', '.join(case['symptoms'][:5])}")
        if case["services"]:
            lines.append(f"- 影响服务: {', '.join(case['services'][:5])}")
        if case["hosts"]:
            lines.append(f"- 涉及主机: {', '.join(case['hosts'][:5])}")
        if case["root_cause"]:
            lines.append(f"- 根因: {case['root_cause']}")
        if case["resolution"]:
            lines.append(f"- 解决方案: {case['resolution']}")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Trace / Span Storage (链路追踪)
    # ─────────────────────────────────────────────────────────────────────────

    def store_span(self, span: TraceSpan) -> None:
        """Store a single trace span."""
        with self._get_session() as session:
            # Create/merge Span node
            session.run("""
                MERGE (s:Span {span_id: $span_id})
                SET s.trace_id = $trace_id,
                    s.service = $service,
                    s.operation = $operation,
                    s.start_time = datetime($start_time),
                    s.duration_ms = $duration_ms,
                    s.status = $status,
                    s.error_message = $error_message,
                    s.tags = $tags
            """,
                span_id=span.span_id,
                trace_id=span.trace_id,
                service=span.service,
                operation=span.operation,
                start_time=span.start_time.isoformat() if span.start_time else None,
                duration_ms=span.duration_ms,
                status=span.status,
                error_message=span.error_message[:500] if span.error_message else "",
                tags=str(span.tags),
            )

            # Link to parent span (if exists)
            if span.parent_span_id:
                session.run("""
                    MATCH (child:Span {span_id: $span_id})
                    MERGE (parent:Span {span_id: $parent_id})
                    MERGE (parent)-[:CALLS]->(child)
                """,
                    span_id=span.span_id,
                    parent_id=span.parent_span_id,
                )

            # Link span to service
            if span.service:
                session.run("""
                    MATCH (s:Span {span_id: $span_id})
                    MERGE (svc:Service {name: $service})
                    MERGE (s)-[:BELONGS_TO]->(svc)
                """,
                    span_id=span.span_id,
                    service=span.service,
                )

    def store_trace(
        self,
        trace_id: str,
        spans: list[TraceSpan],
        related_case_id: str | None = None,
    ) -> None:
        """Store a complete trace with all its spans.

        Args:
            trace_id: Unique trace identifier
            spans: List of TraceSpan objects
            related_case_id: Optional fault case to link this trace to
        """
        if not spans:
            return

        # Calculate trace statistics
        start_time = min(s.start_time for s in spans)
        end_time = max(s.start_time for s in spans)
        duration_ms = (end_time - start_time).total_seconds() * 1000 if end_time > start_time else 0
        error_spans = sum(1 for s in spans if s.status in ("ERROR", "TIMEOUT"))

        with self._get_session() as session:
            # Create/merge Trace node
            session.run("""
                MERGE (t:Trace {trace_id: $trace_id})
                SET t.start_time = datetime($start_time),
                    t.end_time = datetime($end_time),
                    t.duration_ms = $duration_ms,
                    t.total_spans = $total_spans,
                    t.error_spans = $error_spans
            """,
                trace_id=trace_id,
                start_time=start_time.isoformat() if start_time else None,
                end_time=end_time.isoformat() if end_time else None,
                duration_ms=duration_ms,
                total_spans=len(spans),
                error_spans=error_spans,
            )

            # Link to fault case if provided
            if related_case_id:
                session.run("""
                    MATCH (t:Trace {trace_id: $trace_id})
                    MATCH (c:FaultCase {case_id: $case_id})
                    MERGE (t)-[:RELATED_TO]->(c)
                """,
                    trace_id=trace_id,
                    case_id=related_case_id,
                )

        # Store each span
        for span in spans:
            self.store_span(span)

        logger.info(f"Stored trace {trace_id} with {len(spans)} spans")

    def store_trace_from_dict(self, trace_data: dict) -> None:
        """Store trace from dictionary format (from OpenRCA)."""
        trace_id = trace_data["trace_id"]
        spans_data = trace_data.get("spans", [])

        spans = []
        for s in spans_data:
            span = TraceSpan(
                span_id=s.get("span_id", ""),
                trace_id=trace_id,
                parent_span_id=s.get("parent_span_id"),
                service=s.get("service", ""),
                operation=s.get("operation", ""),
                start_time=s.get("start_time", datetime.now(timezone.utc)),
                duration_ms=float(s.get("duration_ms", 0)),
                status=s.get("status", "OK"),
                error_message=s.get("error_message", ""),
                tags=s.get("tags", {}),
            )
            spans.append(span)

        related_case_id = trace_data.get("related_case_id")
        self.store_trace(trace_id, spans, related_case_id)

    def get_trace_tree(self, trace_id: str) -> TraceContext | None:
        """Get complete trace tree with hierarchical span relationships.

        Returns:
            TraceContext with spans organized as a tree
        """
        with self._get_session() as session:
            # Get trace info
            trace_result = session.run("""
                MATCH (t:Trace {trace_id: $trace_id})
                RETURN t
            """, trace_id=trace_id)

            trace_record = trace_result.single()
            if not trace_record:
                return None

            t = trace_record["t"]

            # Get all spans
            spans_result = session.run("""
                MATCH (t:Trace {trace_id: $trace_id})-[:CONTAINS]->(s:Span)
                RETURN s
                ORDER BY s.start_time
            """, trace_id=trace_id)

            spans = []
            span_map = {}

            for record in spans_result:
                s = record["s"]
                span = TraceSpan(
                    span_id=s.get("span_id", ""),
                    trace_id=trace_id,
                    parent_span_id=s.get("parent_span_id"),
                    service=s.get("service", ""),
                    operation=s.get("operation", ""),
                    start_time=s.get("start_time"),
                    duration_ms=s.get("duration_ms", 0),
                    status=s.get("status", "OK"),
                    error_message=s.get("error_message", ""),
                    tags=eval(s.get("tags", "{}")) if isinstance(s.get("tags"), str) else s.get("tags", {}),
                )
                spans.append(span)
                span_map[span.span_id] = span

            # Build tree structure
            root_spans = []
            for span in spans:
                if span.parent_span_id and span.parent_span_id in span_map:
                    pass  # Has parent
                else:
                    root_spans.append(span)

            return TraceContext(
                trace_id=trace_id,
                spans=spans,
                start_time=t.get("start_time"),
                end_time=t.get("end_time"),
                duration_ms=t.get("duration_ms", 0),
                total_spans=t.get("total_spans", len(spans)),
                error_spans=t.get("error_spans", 0),
            )

    def get_slow_traces(self, min_duration_ms: float = 1000, limit: int = 10) -> list[dict]:
        """Find slow traces based on duration threshold.

        Args:
            min_duration_ms: Minimum duration in milliseconds
            limit: Maximum number of results

        Returns:
            List of trace summaries with duration info
        """
        with self._get_session() as session:
            result = session.run("""
                MATCH (t:Trace)
                WHERE t.duration_ms >= $min_duration
                RETURN t.trace_id AS trace_id,
                       t.start_time AS start_time,
                       t.duration_ms AS duration_ms,
                       t.total_spans AS total_spans,
                       t.error_spans AS error_spans
                ORDER BY t.duration_ms DESC
                LIMIT $limit
            """, min_duration=min_duration_ms, limit=limit)

            return [dict(record) for record in result]

    def get_error_traces(self, limit: int = 10) -> list[dict]:
        """Find traces with errors.

        Returns:
            List of traces that have error spans
        """
        with self._get_session() as session:
            result = session.run("""
                MATCH (t:Trace)
                WHERE t.error_spans > 0
                RETURN t.trace_id AS trace_id,
                       t.start_time AS start_time,
                       t.duration_ms AS duration_ms,
                       t.total_spans AS total_spans,
                       t.error_spans AS error_spans
                ORDER BY t.start_time DESC
                LIMIT $limit
            """, limit=limit)

            return [dict(record) for record in result]

    def get_service_call_graph(self, service: str, depth: int = 2) -> list[dict]:
        """Get service call graph starting from a specific service.

        Args:
            service: Starting service name
            depth: Maximum traversal depth

        Returns:
            List of (caller, callee) relationships
        """
        with self._get_session() as session:
            result = session.run("""
                MATCH path = (s1:Service)-[r:CALLS*1..""" + str(depth) + """]->(s2:Service)
                WHERE s1.name = $service
                WITH s1.name AS caller, s2.name AS callee, length(r) AS depth
                RETURN caller, callee, depth
                ORDER BY depth, caller
            """, service=service)

            return [dict(record) for record in result]

    def find_similar_traces(
        self,
        error_services: list[str],
        time_range_hours: int = 1,
    ) -> list[dict]:
        """Find traces with similar error patterns.

        Args:
            error_services: List of services involved in errors
            time_range_hours: Time window to search

        Returns:
            List of similar traces with error spans
        """
        with self._get_session() as session:
            # Build service filter
            service_pattern = "|".join(error_services)

            result = session.run("""
                MATCH (t:Trace)-[:CONTAINS]->(s:Span)
                WHERE s.service =~ '(?i).*""" + service_pattern + """'
                  AND s.status IN ['ERROR', 'TIMEOUT']
                WITH t, collect(DISTINCT s.service) AS error_services,
                     collect(DISTINCT s.span_id) AS error_span_ids,
                     count(s) AS error_count
                WHERE error_count >= size($error_services)
                RETURN t.trace_id AS trace_id,
                       t.start_time AS start_time,
                       t.duration_ms AS duration_ms,
                       error_services,
                       error_count
                ORDER BY error_count DESC, t.start_time DESC
                LIMIT 20
            """, error_services=error_services)

            return [dict(record) for record in result]

    def get_fault_propagation_path(self, trace_id: str) -> list[dict]:
        """Analyze fault propagation path in a trace.

        Returns:
            Ordered list of spans showing how error propagated
        """
        with self._get_session() as session:
            # Get error spans ordered by time
            result = session.run("""
                MATCH (t:Trace {trace_id: $trace_id})-[:CONTAINS]->(s:Span)
                WHERE s.status IN ['ERROR', 'TIMEOUT']
                WITH s
                ORDER BY s.start_time
                RETURN s.span_id AS span_id,
                       s.service AS service,
                       s.operation AS operation,
                       s.start_time AS start_time,
                       s.error_message AS error_message
            """, trace_id=trace_id)

            return [dict(record) for record in result]

    def export_trace_for_visualization(self, trace_id: str) -> dict:
        """Export trace data in visualization-friendly format.

        Returns:
            D3.js compatible hierarchical structure
        """
        trace_ctx = self.get_trace_tree(trace_id)
        if not trace_ctx:
            return {}

        # Build hierarchical structure
        nodes = []
        links = []

        span_map = {}
        for span in trace_ctx.spans:
            node = {
                "id": span.span_id,
                "name": f"{span.service}/{span.operation}",
                "service": span.service,
                "operation": span.operation,
                "duration": span.duration_ms,
                "status": span.status,
                "startTime": span.start_time.isoformat() if span.start_time else None,
            }
            nodes.append(node)
            span_map[span.span_id] = node

            if span.parent_span_id and span.parent_span_id in span_map:
                links.append({
                    "source": span.parent_span_id,
                    "target": span.span_id,
                })

        return {
            "traceId": trace_id,
            "duration": trace_ctx.duration_ms,
            "totalSpans": trace_ctx.total_spans,
            "errorSpans": trace_ctx.error_spans,
            "nodes": nodes,
            "links": links,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Statistics & Maintenance
    # ─────────────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get knowledge base statistics."""
        with self._get_session() as session:
            stats = {}

            result = session.run("MATCH (c:FaultCase) RETURN count(c) AS count")
            stats["total_cases"] = result.single()["count"]

            result = session.run("MATCH (s:Symptom) RETURN count(s) AS count")
            stats["total_symptoms"] = result.single()["count"]

            result = session.run("MATCH (r:RootCause) RETURN count(r) AS count")
            stats["total_root_causes"] = result.single()["count"]

            result = session.run("MATCH (svc:Service) RETURN count(svc) AS count")
            stats["total_services"] = result.single()["count"]

            # Trace statistics
            try:
                result = session.run("MATCH (t:Trace) RETURN count(t) AS count")
                stats["total_traces"] = result.single()["count"]
            except Exception:
                stats["total_traces"] = 0

            try:
                result = session.run("MATCH (s:Span) RETURN count(s) AS count")
                stats["total_spans"] = result.single()["count"]
            except Exception:
                stats["total_spans"] = 0

            try:
                result = session.run("MATCH (s:Span) WHERE s.status IN ['ERROR', 'TIMEOUT'] RETURN count(s) AS count")
                stats["error_spans"] = result.single()["count"]
            except Exception:
                stats["error_spans"] = 0

            return stats

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 向后兼容别名
Neo4jGraphRAG = GraphRAG
