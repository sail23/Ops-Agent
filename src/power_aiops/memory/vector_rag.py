"""Chroma Vector RAG for semantic similarity search."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from power_aiops.config import get_settings
from power_aiops.llm.embedding import ZhipuEmbeddingClient, _hash_embedding_fallback

logger = logging.getLogger(__name__)


@dataclass
class IncidentDocument:
    """A document for vector storage representing an incident."""

    incident_id: str
    title: str
    symptoms: list[str]
    root_cause: str
    solution: str
    service_name: str | None = None
    severity: str | None = None
    occurred_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChromaVectorRAG:
    """Vector RAG using Chroma for semantic similarity search.

    This class provides vector-based retrieval for incident cases,
    complementing the Graph RAG which handles structured relationships.

    Usage:
        rag = ChromaVectorRAG()
        rag.initialize()

        # Store an incident
        doc = IncidentDocument(
            incident_id="INC-001",
            title="支付服务超时",
            symptoms=["支付失败", "响应时间 > 5s"],
            root_cause="数据库连接池耗尽",
            solution="扩容连接池"
        )
        rag.store_incident(doc)

        # Search by semantic similarity
        results = rag.semantic_search("支付超时问题", top_k=5)
    """

    _COLLECTION_NAME = "incident_cases"

    def __init__(
        self,
        embedding_client: ZhipuEmbeddingClient | None = None,
        persist_directory: str | None = None,
    ):
        """Initialize Chroma Vector RAG.

        Args:
            embedding_client: Embedding client for text vectorization.
                              Uses ZhipuEmbeddingClient if not provided.
            persist_directory: Directory for Chroma persistence.
                              Defaults to ./chroma_data in project root.
        """
        settings = get_settings()
        self._embedding_client = embedding_client
        self._persist_directory = persist_directory or "./chroma_data"
        self._embedding_dim = settings.zhipu_embedding_dim
        self._client: chromadb.PersistentClient | None = None
        self._collection: chromadb.Collection | None = None

    @property
    def embedding_client(self) -> ZhipuEmbeddingClient:
        """Lazy initialization of embedding client."""
        if self._embedding_client is None:
            self._embedding_client = ZhipuEmbeddingClient()
        return self._embedding_client

    def initialize(self) -> None:
        """Initialize Chroma client and collection."""
        if self._client is not None:
            return

        self._client = chromadb.PersistentClient(
            path=self._persist_directory,
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )

        self._collection = self._client.get_or_create_collection(
            name=self._COLLECTION_NAME,
            metadata={"description": "Incident case knowledge base"},
        )

        logger.info(
            f"ChromaVectorRAG initialized with {self._collection.count()} documents"
        )

    def health_check(self) -> bool:
        """Check if Chroma is healthy."""
        try:
            if self._client is None:
                return False
            self._client.get_collection(self._COLLECTION_NAME)
            return True
        except Exception as e:
            logger.error(f"Chroma health check failed: {e}")
            return False

    def store_incident(self, incident: IncidentDocument) -> str:
        """Store or update an incident document in the vector database.

        Args:
            incident: Incident document to store

        Returns:
            Document ID
        """
        self._ensure_initialized()

        doc_id = incident.incident_id or f"inc-{uuid.uuid4().hex[:8]}"

        combined_text = self._combine_text(incident)
        embedding = self.embedding_client.embed_single(combined_text)

        metadata = {
            "title": incident.title,
            "service_name": incident.service_name or "",
            "severity": incident.severity or "",
            "occurred_at": incident.occurred_at or "",
            "symptoms": "|".join(incident.symptoms),
            "root_cause": incident.root_cause,
        }

        # Use upsert to avoid errors on duplicate IDs
        try:
            existing = self._collection.get(ids=[doc_id])
            if existing and existing["ids"]:
                self._collection.update(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[combined_text],
                    metadatas=[metadata],
                )
            else:
                self._collection.add(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[combined_text],
                    metadatas=[metadata],
                )
        except Exception:
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[combined_text],
                metadatas=[metadata],
            )

        logger.info(f"Stored incident {doc_id} in Chroma")
        return doc_id

    def store_incidents_batch(self, incidents: list[IncidentDocument]) -> list[str]:
        """Store multiple incidents in batch.

        Args:
            incidents: List of incident documents

        Returns:
            List of document IDs
        """
        self._ensure_initialized()

        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        # Collect all texts, embed in one batch call
        texts_to_embed = [self._combine_text(inc) for inc in incidents]
        embeddings = self.embedding_client.embed_batch_with_fallback(texts_to_embed)

        for incident, embedding, combined_text in zip(incidents, embeddings, texts_to_embed):
            doc_id = incident.incident_id or f"inc-{uuid.uuid4().hex[:8]}"
            ids.append(doc_id)
            documents.append(combined_text)

            metadatas.append({
                "title": incident.title,
                "service_name": incident.service_name or "",
                "severity": incident.severity or "",
                "occurred_at": incident.occurred_at or "",
                "symptoms": "|".join(incident.symptoms),
                "root_cause": incident.root_cause,
            })

        self._collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        logger.info(f"Stored {len(incidents)} incidents in Chroma")
        return ids

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar incidents by semantic similarity.

        Args:
            query: Search query text
            top_k: Number of results to return
            filter_metadata: Optional metadata filters

        Returns:
            List of matching incidents with scores
        """
        self._ensure_initialized()

        query_embedding = self.embedding_client.embed_single(query)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=filter_metadata,
            include=["distances", "metadatas", "documents"],
        )

        matches: list[dict[str, Any]] = []
        if results["ids"] and len(results["ids"]) > 0:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                # 1/(1+d) maps [0, inf] → [1, 0], safe for both L2 and cosine distance
                similarity = 1.0 / (1.0 + distance)

                matches.append({
                    "incident_id": doc_id,
                    "similarity": similarity,
                    "distance": distance,
                    "title": results["metadatas"][0][i].get("title", ""),
                    "service_name": results["metadatas"][0][i].get("service_name", ""),
                    "severity": results["metadatas"][0][i].get("severity", ""),
                    "symptoms": results["metadatas"][0][i].get("symptoms", "").split("|"),
                    "root_cause": results["metadatas"][0][i].get("root_cause", ""),
                    "document": results["documents"][0][i],
                })

        return matches

    def search_by_service(
        self,
        service_name: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search incidents by service name.

        Args:
            service_name: Service name to filter by
            top_k: Number of results to return

        Returns:
            List of matching incidents
        """
        return self.semantic_search(
            query=service_name,
            top_k=top_k,
            filter_metadata={"service_name": service_name},
        )

    def search_by_symptom(
        self,
        symptom: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search incidents by symptom text.

        Args:
            symptom: Symptom description
            top_k: Number of results to return

        Returns:
            List of matching incidents
        """
        return self.semantic_search(query=symptom, top_k=top_k)

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Get a specific incident by ID.

        Args:
            incident_id: Incident ID

        Returns:
            Incident data or None if not found
        """
        self._ensure_initialized()

        results = self._collection.get(
            ids=[incident_id],
            include=["metadatas", "documents"],
        )

        if not results["ids"] or len(results["ids"]) == 0:
            return None

        metadata = results["metadatas"][0]
        return {
            "incident_id": incident_id,
            "title": metadata.get("title", ""),
            "service_name": metadata.get("service_name", ""),
            "severity": metadata.get("severity", ""),
            "occurred_at": metadata.get("occurred_at", ""),
            "symptoms": metadata.get("symptoms", "").split("|"),
            "root_cause": metadata.get("root_cause", ""),
            "document": results["documents"][0],
        }

    def delete_incident(self, incident_id: str) -> bool:
        """Delete an incident from the vector database.

        Args:
            incident_id: Incident ID to delete

        Returns:
            True if deleted, False if not found
        """
        self._ensure_initialized()

        try:
            self._collection.delete(ids=[incident_id])
            logger.info(f"Deleted incident {incident_id} from Chroma")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete incident {incident_id}: {e}")
            return False

    def get_collection_stats(self) -> dict[str, Any]:
        """Get collection statistics.

        Returns:
            Statistics about the collection
        """
        self._ensure_initialized()

        return {
            "total_documents": self._collection.count(),
            "collection_name": self._COLLECTION_NAME,
            "embedding_dimension": self._embedding_dim,
            "persist_directory": self._persist_directory,
        }

    def reset(self) -> None:
        """Reset the collection (delete all documents)."""
        self._ensure_initialized()
        self._collection.delete()
        logger.info("Chroma collection reset")

    def close(self) -> None:
        """Close connections (no-op for PersistentClient)."""
        self._client = None
        self._collection = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Ensure client is initialized."""
        if self._client is None:
            self.initialize()

    def _combine_text(self, incident: IncidentDocument) -> str:
        """Combine incident fields into a searchable text.

        Args:
            incident: Incident document

        Returns:
            Combined text for embedding
        """
        parts = [
            f"标题: {incident.title}",
            f"服务: {incident.service_name or '未知'}",
            f"严重程度: {incident.severity or '未知'}",
            f"症状: {'; '.join(incident.symptoms)}",
            f"根因: {incident.root_cause}",
            f"解决方案: {incident.solution}",
        ]
        return " | ".join(parts)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
