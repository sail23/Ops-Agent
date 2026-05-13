"""Long-term memory interface combining Graph RAG and Vector RAG."""

from __future__ import annotations

from typing import Any

from power_aiops.models.incident import IncidentContext
from power_aiops.memory.graph_rag import Neo4jGraphRAG
from power_aiops.memory.vector_rag import (
    ChromaVectorRAG,
    IncidentDocument,
)


class LongTermMemory:
    """Long-term memory interface using hybrid Graph + Vector RAG.

    This class combines:
    - Graph RAG (Neo4j): Structured relationship queries
    - Vector RAG (Chroma): Semantic similarity search

    Together they provide comprehensive incident retrieval:
    - Graph: Service dependency, fault propagation paths
    - Vector: Natural language symptom matching
    """

    def __init__(
        self,
        graph_rag: Neo4jGraphRAG | None = None,
        vector_rag: ChromaVectorRAG | None = None,
    ):
        self._graph_rag = graph_rag or Neo4jGraphRAG()
        self._vector_rag = vector_rag or ChromaVectorRAG()

    @property
    def graph_rag(self) -> Neo4jGraphRAG:
        """Graph RAG instance for structured queries."""
        return self._graph_rag

    @property
    def vector_rag(self) -> ChromaVectorRAG:
        """Vector RAG instance for semantic search."""
        return self._vector_rag

    # ------------------------------------------------------------------
    # Hybrid Search (Core Feature)
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        incident: IncidentContext | str,
        top_k: int = 5,
        graph_weight: float = 0.5,
        vector_weight: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Perform hybrid search combining Graph and Vector RAG.

        Uses Reciprocal Rank Fusion (RRF) to combine results from both sources.

        Args:
            incident: Incident context or query text
            top_k: Number of results to return
            graph_weight: Weight for graph search results
            vector_weight: Weight for vector search results

        Returns:
            List of incidents ranked by combined score
        """
        if isinstance(incident, str):
            query_text = incident
        else:
            query_text = self._incident_to_query(incident)

        graph_results = self._graph_search(query_text, top_k)
        vector_results = self._vector_search(query_text, top_k)

        fused = self._reciprocal_rank_fusion(
            graph_results,
            vector_results,
            graph_weight,
            vector_weight,
            top_k,
        )

        return fused

    def similar_incidents(
        self,
        incident: IncidentContext,
        top_k: int = 5,
        use_hybrid: bool = True,
    ) -> list[dict]:
        """Find similar past incidents.

        Args:
            incident: Current incident context
            top_k: Number of results to return
            use_hybrid: Use hybrid search (True) or graph only (False)

        Returns:
            List of similar incidents with scores
        """
        if use_hybrid:
            return self.hybrid_search(incident, top_k)
        return self._graph_search(self._incident_to_query(incident), top_k)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_incident(self, incident: IncidentContext) -> None:
        """Persist an incident to both Graph and Vector stores.

        Args:
            incident: Incident to persist
        """
        self._graph_rag.store_incident(incident)

        doc = self._incident_to_document(incident)
        self._vector_rag.store_incident(doc)

    # ------------------------------------------------------------------
    # Vector-specific operations
    # ------------------------------------------------------------------

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search incidents by semantic similarity.

        Args:
            query: Search query text
            top_k: Number of results to return

        Returns:
            List of matching incidents
        """
        return self._vector_search(query, top_k)

    def search_by_symptom(
        self,
        symptom: str,
        top_k: int = 5,
    ) -> list[dict]:
        """Search incidents by symptom pattern (Vector RAG).

        Args:
            symptom: Symptom description
            top_k: Number of results to return

        Returns:
            List of matching incidents
        """
        return self._vector_search(symptom, top_k)

    def search_by_service(
        self,
        service_name: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search incidents by service name (Vector RAG).

        Args:
            service_name: Service name to filter by
            top_k: Number of results to return

        Returns:
            List of matching incidents
        """
        return self._vector_rag.search_by_service(service_name, top_k)

    # ------------------------------------------------------------------
    # Graph-specific operations
    # ------------------------------------------------------------------

    def get_graph(self, incident_id: str) -> dict:
        """Get the subgraph for a specific incident (Graph RAG).

        Args:
            incident_id: Incident ID

        Returns:
            Subgraph data
        """
        return self._graph_rag.get_case_details(incident_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize both Graph and Vector stores."""
        self._graph_rag.initialize_schema()
        self._vector_rag.initialize()

    def health_check(self) -> bool:
        """Check if both stores are healthy.

        Returns:
            True if both Graph and Vector RAG are healthy
        """
        graph_ok = self._graph_rag.health_check()
        vector_ok = self._vector_rag.health_check()
        return graph_ok and vector_ok

    def get_stats(self) -> dict[str, Any]:
        """Get statistics from both stores.

        Returns:
            Combined statistics
        """
        try:
            vector_stats = self._vector_rag.get_collection_stats()
        except Exception:
            vector_stats = {"error": "unavailable"}

        return {
            "graph_rag": {
                "status": "healthy" if self._graph_rag.health_check() else "unhealthy",
            },
            "vector_rag": vector_stats,
        }

    def close(self) -> None:
        """Close connections to both stores."""
        self._graph_rag.close()
        self._vector_rag.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _incident_to_query(self, incident: IncidentContext) -> str:
        """Convert incident context to search query text."""
        symptoms = " ".join(incident.symptoms) if incident.symptoms else ""
        query_parts = [
            incident.title or "",
            symptoms,
            incident.description or "",
        ]
        return " ".join(filter(None, query_parts))

    def _incident_to_document(self, incident: IncidentContext) -> IncidentDocument:
        """Convert IncidentContext to IncidentDocument for Vector RAG."""
        return IncidentDocument(
            incident_id=incident.incident_id or "",
            title=incident.title or "",
            symptoms=incident.symptoms or [],
            root_cause=incident.root_cause or "",
            solution=incident.resolution or "",
            service_name=incident.service_name,
            severity=incident.severity,
            occurred_at=incident.occurred_at.isoformat() if incident.occurred_at else None,
        )

    def _graph_search(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform graph-based search."""
        results = self._graph_rag.search_similar_cases(query, top_k)
        formatted = []
        for r in results:
            # Defensive: normalize result dict regardless of source shape
            r_data = dict(r) if hasattr(r, "keys") else {}
            # Handle both case_id and incident_id from different sources
            case_id = r_data.get("case_id") or r_data.get("incident_id") or ""
            score = r_data.get("score") or r_data.get("similarity") or 0.0
            title = r_data.get("title") or r_data.get("matched_symptoms", [{}])[0].get("name", "") if isinstance(r_data.get("matched_symptoms"), list) else ""
            root_cause = r_data.get("root_cause", "")
            symptoms_raw = r_data.get("symptoms", [])
            # Normalize symptoms to list
            if isinstance(symptoms_raw, str):
                symptoms = [s.strip() for s in symptoms_raw.split("|") if s.strip()]
            elif isinstance(symptoms_raw, list):
                symptoms = [s.get("name") if isinstance(s, dict) else str(s) for s in symptoms_raw]
            else:
                symptoms = []
            formatted.append({
                "incident_id": case_id,
                "source": "graph",
                "score": score,
                "title": title,
                "root_cause": root_cause,
                "symptoms": symptoms,
            })
        return formatted

    def _vector_search(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform vector-based search."""
        results = self._vector_rag.semantic_search(query, top_k)
        formatted = []
        for r in results:
            # Ensure symptoms is a list
            symptoms = r.get("symptoms", [])
            if isinstance(symptoms, str):
                symptoms = [s.strip() for s in symptoms.split("|") if s.strip()]
            formatted.append({
                "incident_id": r.get("incident_id", ""),
                "source": "vector",
                "score": r.get("similarity", 0.0),
                "title": r.get("title", ""),
                "root_cause": r.get("root_cause", ""),
                "symptoms": symptoms,
            })
        return formatted

    def _reciprocal_rank_fusion(
        self,
        graph_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        graph_weight: float,
        vector_weight: float,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Combine results using Reciprocal Rank Fusion (RRF).

        RRF formula: score = weight * 1 / (k + rank)
        where k is a constant (typically 60) and rank starts at 1.
        """
        k = 60  # RRF constant

        graph_ranks: dict[str, float] = {}
        for rank, result in enumerate(graph_results, start=1):
            incident_id = result.get("incident_id", "")
            graph_ranks[incident_id] = graph_weight / (k + rank)

        vector_ranks: dict[str, float] = {}
        for rank, result in enumerate(vector_results, start=1):
            incident_id = result.get("incident_id", "")
            vector_ranks[incident_id] = vector_weight / (k + rank)

        all_ids = set(graph_ranks.keys()) | set(vector_ranks.keys())

        combined_scores: dict[str, dict[str, Any]] = {}
        for incident_id in all_ids:
            graph_score = graph_ranks.get(incident_id, 0.0)
            vector_score = vector_ranks.get(incident_id, 0.0)
            combined_scores[incident_id] = {
                "incident_id": incident_id,
                "graph_score": graph_score,
                "vector_score": vector_score,
                "total_score": graph_score + vector_score,
            }

        for i, result in enumerate(graph_results):
            incident_id = result.get("incident_id", "")
            if incident_id in combined_scores:
                combined_scores[incident_id]["graph_details"] = result

        for i, result in enumerate(vector_results):
            incident_id = result.get("incident_id", "")
            if incident_id in combined_scores:
                combined_scores[incident_id]["vector_details"] = result

        sorted_results = sorted(
            combined_scores.values(),
            key=lambda x: x["total_score"],
            reverse=True,
        )

        return sorted_results[:top_k]


class StubLongTermMemory:
    """Stub implementation for testing without Neo4j."""

    def similar_incidents(self, incident: IncidentContext, top_k: int = 5) -> list[str]:
        return [f"stub-case-{i} for {incident.incident_id}" for i in range(min(3, top_k))]

    def persist_incident(self, incident: IncidentContext) -> None:
        return None