"""FAISS-based vector store with SQLite metadata for scalability."""

import os
import threading
from pathlib import Path
from typing import Any, List, Optional, Dict
import logging
import numpy as np
import faiss

from models.schemas import ChunkMetadata, SearchResult
from services.metadata_store import MetadataStore
from services.bm25_service import BM25Index
from services.structured_store import StructuredStore

logger = logging.getLogger(__name__)


class VectorStore:
    """
    FAISS-based vector store with SQLite metadata storage.

    Uses SQLite for metadata, providing:
    - Constant memory usage regardless of document count
    - Faster lookups with indexed queries
    - Better scalability for large collections
    """

    def __init__(self, index_dir: Path, embedding_dim: int = 384, discard_mismatched_index: bool = False):
        """
        Initialize the vector store.

        Args:
            index_dir: Directory to store FAISS index and metadata
            embedding_dim: Dimension of embedding vectors
            discard_mismatched_index: Start from an empty index instead of
                refusing when the saved one has a different dimension. Only
                a re-index, which is about to clear and rebuild everything,
                should pass this.
        """
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_dim = embedding_dim
        self.discard_mismatched_index = discard_mismatched_index
        # describe_embedding() of the service writing into this store; set by
        # whoever builds the store, recorded in metadata.db on save().
        self.embedding_info: Optional[Dict[str, Any]] = None

        self.index_path = self.index_dir / "faiss.index"
        self.metadata_db_path = self.index_dir / "metadata.db"
        # Legacy shadow copy of all vectors — superseded by IndexIDMap2's
        # reconstruct support; only read once during migration, then removed.
        self.embeddings_path = self.index_dir / "embeddings.npy"
        self.bm25_db_path = self.index_dir / "bm25.db"

        # FAISS index: IndexIDMap2 over IndexFlatIP, keyed by the chunk's
        # SQLite row id. Stable ids mean deletes are in-place remove_ids
        # calls instead of full rebuilds, and search hits map straight to
        # primary-key lookups.
        self.index: Optional[faiss.Index] = None

        # SQLite metadata store
        self.metadata_store = MetadataStore(self.metadata_db_path)
        # v3.5 entity backfill: existing collections get their chunk_entities
        # rows on first open after the schema migration. Synchronous and
        # usually fast (regex-only extraction); a large corpus pays this once.
        try:
            self.metadata_store.backfill_entities_if_empty()
        except Exception as exc:
            logger.warning(f"Entity backfill skipped: {exc}")

        # Structured table store (lives in the same sqlite db as metadata)
        # Used to answer numeric/aggregation questions over CSV/XLSX sources.
        self.structured_store = StructuredStore(self.metadata_db_path)

        # BM25 keyword search index
        self.bm25_index = BM25Index(self.bm25_db_path)

        # FAISS indexes are NOT thread-safe under concurrent mutation: an
        # upload batch adding vectors while a delete removes them (or a search
        # scans them) is a native crash, not an exception. One reentrant lock
        # serializes every touch of self.index — adds, removes, searches,
        # saves. Discovered live: concurrent upload+delete segfaulted the
        # server twice during a bulk cleanup.
        self._index_lock = threading.RLock()

        self._load_or_create_index()

    def _new_index(self) -> faiss.Index:
        """Create an empty id-mapped index (cosine similarity via normalized IP)."""
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.embedding_dim))

    def _load_or_create_index(self):
        """Load existing index from disk (migrating legacy formats) or create a new one."""
        if self.index_path.exists():
            logger.info("Loading existing FAISS index")
            loaded = faiss.read_index(str(self.index_path))

            # Refuse a dimension mismatch up front. Without this check, an
            # embedding-model change fails deep inside add_with_ids — after
            # the chunk metadata has already been committed to SQLite.
            if loaded.d != self.embedding_dim and self.discard_mismatched_index:
                logger.warning(
                    f"Discarding {loaded.d}-dim index at {self.index_path}; "
                    f"re-indexing at {self.embedding_dim} dims"
                )
                self.index = self._new_index()
                return
            if loaded.d != self.embedding_dim:
                raise RuntimeError(
                    f"FAISS index at {self.index_path} has dimension {loaded.d}, "
                    f"but the configured embedding model produces {self.embedding_dim}. "
                    f"Either restore the previous embedding model or re-index the "
                    f"collection to rebuild the index."
                )

            if isinstance(faiss.downcast_index(loaded), faiss.IndexIDMap2):
                self.index = loaded
            else:
                self.index = self._migrate_legacy_index(loaded)

            total_chunks = self.metadata_store.get_total_chunks()
            logger.info(f"Loaded index with {total_chunks} chunks")
        else:
            logger.info("Creating new FAISS index")
            self.index = self._new_index()
            logger.info("Created new index")

    def _migrate_legacy_index(self, legacy: faiss.Index) -> faiss.Index:
        """Wrap a legacy positional IndexFlatIP into an id-mapped index.

        Legacy indexes stored vectors in chunk-insertion order with a parallel
        embeddings.npy shadow copy. Re-add every vector under its chunk's
        SQLite row id, persist, and drop the now-redundant shadow file.
        """
        logger.info("Migrating legacy FAISS index to id-mapped format")
        row_ids = self.metadata_store.get_all_rowids_ordered()

        if self.embeddings_path.exists():
            vectors = np.load(str(self.embeddings_path)).astype(np.float32)
        elif legacy.ntotal > 0:
            vectors = legacy.reconstruct_n(0, legacy.ntotal)
        else:
            vectors = np.zeros((0, self.embedding_dim), dtype=np.float32)

        n = min(len(row_ids), len(vectors), legacy.ntotal) if legacy.ntotal else min(len(row_ids), len(vectors))
        if n != legacy.ntotal or n != len(row_ids):
            logger.error(
                f"Legacy index migration: index/metadata drift detected "
                f"(index={legacy.ntotal} vectors, metadata={len(row_ids)} chunks, "
                f"embeddings={len(vectors)}). The positional mapping was already "
                f"unreliable for this collection; keeping first {n} as a placeholder. "
                f"Run DocumentIndexer.rebuild_vector_index() (or a collection "
                f"re-index) to restore correct semantic search."
            )

        new_index = self._new_index()
        if n > 0:
            new_index.add_with_ids(vectors[:n], np.asarray(row_ids[:n], dtype=np.int64))

        self._write_index_atomic(new_index)
        if self.embeddings_path.exists():
            try:
                self.embeddings_path.unlink()
                logger.info("Removed legacy embeddings.npy shadow copy")
            except OSError as e:
                logger.warning(f"Could not remove embeddings.npy: {e}")

        logger.info(f"Migrated {n} vectors to id-mapped FAISS index")
        return new_index

    def load(self):
        """Reload the index from disk (public method for external reload)."""
        logger.info("Reloading index from disk...")
        self._load_or_create_index()

    def close(self):
        """Close the vector store and release resources."""
        logger.info("Closing vector store...")
        # Clear references to allow garbage collection
        self.index = None
        self.metadata_store = None

    def add_chunks(self, chunks: List[ChunkMetadata], embeddings: np.ndarray):
        """
        Add chunks and their embeddings to the index.

        Args:
            chunks: List of ChunkMetadata objects
            embeddings: NumPy array of shape (len(chunks), embedding_dim)
        """
        if len(chunks) != len(embeddings):
            raise ValueError("Number of chunks must match number of embeddings")

        if len(chunks) == 0:
            return

        # Normalize embeddings for cosine similarity
        embeddings_normalized = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

        # Re-indexed chunk_ids get a fresh row id from INSERT OR REPLACE, so
        # evict any existing vectors for them first to avoid orphaned ids.
        with self._index_lock:
            stale = self.metadata_store.get_ids_for_chunk_ids([c.chunk_id for c in chunks])
            if stale:
                self.index.remove_ids(np.asarray(list(stale.values()), dtype=np.int64))

            # Add metadata to SQLite first — its assigned row ids become the FAISS ids
            chunk_dicts = [chunk.model_dump() for chunk in chunks]
            self.metadata_store.add_chunks(chunk_dicts)

            id_by_chunk = self.metadata_store.get_ids_for_chunk_ids(
                [chunk.chunk_id for chunk in chunks]
            )
            ids = np.asarray([id_by_chunk[chunk.chunk_id] for chunk in chunks], dtype=np.int64)
            self.index.add_with_ids(embeddings_normalized.astype(np.float32), ids)

        # Add to BM25 index for keyword search
        bm25_docs = [(chunk.chunk_id, chunk.text) for chunk in chunks]
        self.bm25_index.add_documents_batch(bm25_docs)

        # Entity-graph boost: record which entities each chunk mentions so the
        # retriever can pull cross-document matches at query time. Extraction
        # is heuristic (no LLM), cheap, and idempotent — a re-index of the
        # same chunk_id replaces its rows via INSERT OR REPLACE.
        try:
            from services.entity_graph import extract_entities
            chunk_entities = {
                chunk.chunk_id: extract_entities(chunk.text)
                for chunk in chunks
            }
            chunk_entities = {cid: ents for cid, ents in chunk_entities.items() if ents}
            if chunk_entities:
                self.metadata_store.add_chunk_entities(chunk_entities)
        except Exception as exc:  # never let indexing fail over the boost
            logger.warning(f"Entity extraction skipped for batch: {exc}")

        logger.info(f"Added {len(chunks)} chunks to index")

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        allowed_chunk_ids: Optional[set] = None,
    ) -> List[SearchResult]:
        """
        Search for similar chunks.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return
            allowed_chunk_ids: Optional set of chunk_ids to restrict results to
                (metadata pre-filter). When provided, the whole index is scanned
                so the top_k *filtered* results are exact rather than whatever
                survives a narrow candidate window.

        Returns:
            List of SearchResult objects, sorted by similarity (highest first)
        """
        if self.index.ntotal == 0:
            logger.warning("Index is empty, returning no results")
            return []

        # An explicit empty filter set means "nothing matches" — don't search.
        if allowed_chunk_ids is not None and len(allowed_chunk_ids) == 0:
            return []

        # Normalize query for cosine similarity
        query_normalized = query_embedding / np.linalg.norm(query_embedding)
        query_normalized = query_normalized.reshape(1, -1).astype(np.float32)

        # Search. When filtering, scan the full index so post-filtering still
        # yields the true top_k; otherwise just fetch top_k. FAISS labels are
        # the chunks' SQLite row ids.
        filtering = allowed_chunk_ids is not None
        allowed_ids: Optional[set] = None
        if filtering:
            allowed_ids = set(
                self.metadata_store.get_ids_for_chunk_ids(allowed_chunk_ids).values()
            )
            if not allowed_ids:
                return []

        with self._index_lock:
            k = self.index.ntotal if filtering else min(top_k, self.index.ntotal)
            similarities, labels = self.index.search(query_normalized, k)

        # Keep the top_k hits (post-filter) before touching SQLite, then
        # batch-fetch metadata for just those.
        hits: List[tuple] = []
        for similarity, row_id in zip(similarities[0], labels[0]):
            if row_id == -1:  # FAISS returns -1 for empty results
                continue
            if filtering and int(row_id) not in allowed_ids:
                continue
            hits.append((float(similarity), int(row_id)))
            if len(hits) >= top_k:
                break

        chunks_by_id = self.metadata_store.get_chunks_by_rowids([rid for _, rid in hits])
        doc_infos = self.metadata_store.get_documents_info(
            {chunk["document_id"] for chunk in chunks_by_id.values()}
        )

        results = []
        for similarity, row_id in hits:
            chunk = chunks_by_id.get(row_id)
            if not chunk:
                logger.warning(f"No metadata found for FAISS id {row_id}")
                continue
            results.append(self._build_search_result(chunk, similarity, doc_infos))

        return results

    def search_hybrid(
        self,
        query: str,
        query_embedding: np.ndarray,
        top_k: int = 10,
        semantic_weight: float = 0.7,
        allowed_chunk_ids: Optional[set] = None,
    ) -> List[SearchResult]:
        """
        Perform hybrid search combining semantic similarity and BM25 keyword matching.

        The final score is: semantic_weight * semantic_score + (1 - semantic_weight) * bm25_score

        Args:
            query: Original query text for BM25
            query_embedding: Query embedding vector for semantic search
            top_k: Number of results to return
            semantic_weight: Weight for semantic scores (0-1), default 0.7
                            Higher = more semantic, Lower = more keyword-focused

        Returns:
            List of SearchResult objects, sorted by combined score (highest first)
        """
        if self.index.ntotal == 0:
            logger.warning("Index is empty, returning no results")
            return []

        # An explicit empty filter set means "nothing matches" — don't search.
        if allowed_chunk_ids is not None and len(allowed_chunk_ids) == 0:
            return []

        filtering = allowed_chunk_ids is not None
        allowed_ids: Optional[set] = None
        if filtering:
            allowed_ids = set(
                self.metadata_store.get_ids_for_chunk_ids(allowed_chunk_ids).values()
            )
            if not allowed_ids:
                return []

        # Get more candidates than top_k to allow for re-ranking. When filtering,
        # scan the full index so the post-filter doesn't starve the result set,
        # but keep only the best candidate_limit survivors.
        candidate_limit = min(top_k * 3, self.index.ntotal)
        candidate_k = self.index.ntotal if filtering else candidate_limit

        # 1. Semantic search — collect surviving (row id, score) pairs first,
        # then batch-fetch metadata for just those.
        query_normalized = query_embedding / np.linalg.norm(query_embedding)
        query_normalized = query_normalized.reshape(1, -1).astype(np.float32)
        with self._index_lock:
            semantic_sims, semantic_labels = self.index.search(query_normalized, candidate_k)

        semantic_hits: List[tuple] = []
        for similarity, row_id in zip(semantic_sims[0], semantic_labels[0]):
            if row_id == -1:
                continue
            if filtering and int(row_id) not in allowed_ids:
                continue
            semantic_hits.append((int(row_id), float(similarity)))
            if len(semantic_hits) >= candidate_limit:
                break

        chunks_by_id = self.metadata_store.get_chunks_by_rowids([rid for rid, _ in semantic_hits])

        semantic_scores: Dict[str, float] = {}
        chunk_data: Dict[str, dict] = {}
        for row_id, similarity in semantic_hits:
            chunk = chunks_by_id.get(row_id)
            if chunk:
                chunk_id = chunk["chunk_id"]
                semantic_scores[chunk_id] = similarity
                chunk_data[chunk_id] = chunk

        # 2. BM25 keyword search
        bm25_k = self.index.ntotal if filtering else candidate_limit
        bm25_results = self.bm25_index.search(query, bm25_k)
        if filtering:
            bm25_results = [
                (cid, score) for cid, score in bm25_results if cid in allowed_chunk_ids
            ][:candidate_limit]

        # Normalize BM25 scores to 0-1 range
        bm25_scores: Dict[str, float] = {}
        if bm25_results:
            max_bm25 = max(score for _, score in bm25_results)
            if max_bm25 > 0:
                for chunk_id, score in bm25_results:
                    bm25_scores[chunk_id] = score / max_bm25
                # Batch-fetch chunk data for BM25-only matches
                missing = [cid for cid in bm25_scores if cid not in chunk_data]
                chunk_data.update(self.metadata_store.get_chunks_by_chunk_ids(missing))

        # 3. Combine scores
        all_chunk_ids = set(semantic_scores.keys()) | set(bm25_scores.keys())
        if filtering:
            all_chunk_ids &= allowed_chunk_ids
        combined_scores: Dict[str, float] = {}

        for chunk_id in all_chunk_ids:
            sem_score = semantic_scores.get(chunk_id, 0)
            bm25_score = bm25_scores.get(chunk_id, 0)
            combined_scores[chunk_id] = (
                semantic_weight * sem_score +
                (1 - semantic_weight) * bm25_score
            )

        # 4. Sort by combined score and build results
        sorted_chunks = sorted(combined_scores.items(), key=lambda x: x[1], reverse=True)

        top_chunks = [
            (chunk_data[cid], score) for cid, score in sorted_chunks[:top_k] if cid in chunk_data
        ]
        doc_infos = self.metadata_store.get_documents_info(
            {chunk["document_id"] for chunk, _ in top_chunks}
        )

        results = [
            self._build_search_result(chunk, score, doc_infos)
            for chunk, score in top_chunks
        ]

        logger.info(f"Hybrid search returned {len(results)} results "
                   f"(semantic_weight={semantic_weight})")
        return results

    @staticmethod
    def _build_search_result(
        chunk: dict, score: float, doc_infos: Dict[str, dict]
    ) -> SearchResult:
        """Build a SearchResult from chunk metadata and pre-fetched document info."""
        doc_info = doc_infos.get(chunk["document_id"])
        return SearchResult(
            filename=chunk["filename"],
            page_number=chunk["page_number"],
            text_snippet=chunk["text"],
            similarity_score=score,
            document_id=chunk["document_id"],
            chunk_id=chunk["chunk_id"],
            pdf_url="",  # Populated by the API endpoint
            page_url="",  # Populated by the API endpoint
            source_format=chunk.get("source_format"),
            extraction_method=chunk.get("extraction_method"),
            csv_row_number=chunk.get("csv_row_number"),
            csv_columns=chunk.get("csv_columns"),
            csv_values=chunk.get("csv_values"),
            # Code chunks: the symbol a passage is, so results can say
            # "handler() in router.py, lines 40-72" rather than "page 3".
            language=chunk.get("language"),
            symbol_name=chunk.get("symbol_name"),
            symbol_type=chunk.get("symbol_type"),
            line_start=chunk.get("line_start"),
            line_end=chunk.get("line_end"),
            source_type=doc_info.get("source_type") if doc_info else None,
            source_path=doc_info.get("source_path") if doc_info else None,
            # Document-level override only; the API layer resolves the
            # collection default (it knows which collection it is serving).
            sensitivity=doc_info.get("sensitivity") if doc_info else None,
        )

    def delete_document(self, document_id: str) -> int:
        """
        Delete all chunks belonging to a document.

        The id-mapped index supports in-place removal — no rebuild needed.

        Args:
            document_id: Document ID to delete

        Returns:
            Number of chunks deleted
        """
        # Capture row ids before the metadata rows disappear. Tabular documents
        # (CSV/XLSX) are indexed with zero chunks by design — they must still
        # fall through so their documents row and structured tables are removed.
        row_ids = self.metadata_store.get_document_chunk_rowids(document_id)
        num_deleted = len(row_ids)
        logger.info(f"Deleting {num_deleted} chunks for document {document_id}")

        if row_ids:
            # Delete from BM25 index (reads chunk ids from metadata.db, so
            # this must run before the metadata rows are deleted)
            self.bm25_index.remove_documents_by_document_id(document_id, self.metadata_db_path)

        # Delete from metadata (SQLite) — removes chunks and the documents row
        self.metadata_store.delete_document(document_id)

        # Drop any structured tables (CSV/XLSX) created for this document
        try:
            self.structured_store.delete_document(document_id)
        except Exception as e:
            logger.warning(f"Failed to drop structured tables for {document_id}: {e}")

        if row_ids:
            # Remove the vectors in place
            with self._index_lock:
                removed = self.index.remove_ids(np.asarray(row_ids, dtype=np.int64))
            logger.info(f"Removed {removed} vectors from FAISS index for document {document_id}")

        return num_deleted

    def list_documents(self) -> List[dict]:
        """
        List all indexed documents with their metadata.

        Returns:
            List of document metadata dictionaries
        """
        return self.metadata_store.list_documents()

    def save(self):
        """Persist the FAISS index to disk (metadata is already in SQLite)."""
        with self._index_lock:
            logger.info(f"Saving FAISS index with {self.index.ntotal} vectors")
            self._write_index_atomic(self.index)
            # Record what produced these vectors next to them, so an export
            # (or a person) can tell which model a query must use.
            if self.embedding_info:
                try:
                    self.metadata_store.set_index_info(
                        {**self.embedding_info, "dimension": int(self.index.d)}
                    )
                except Exception as e:
                    logger.warning(f"Could not record embedding info: {e}")
        logger.info("Index saved successfully")

    def _write_index_atomic(self, index: faiss.Index):
        """Write the FAISS index via temp file + rename.

        A crash mid-write must never leave a truncated faiss.index behind —
        faiss.read_index refuses truncated files, which would make the whole
        collection unopenable.

        The temp name is unique per write: concurrent savers (an upload batch
        and a document delete, say) previously shared one .tmp path, and the
        loser's os.replace raced a FileNotFoundError that took the server down.
        Last rename wins; both renames are complete, valid index files.
        """
        tmp_path = self.index_path.with_suffix(f".index.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            faiss.write_index(index, str(tmp_path))
            os.replace(tmp_path, self.index_path)
        finally:
            # A failed write must not litter the collection dir.
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def get_total_chunks(self) -> int:
        """Get the total number of chunks in the index."""
        return self.metadata_store.get_total_chunks()

    def clear_index(self):
        """Clear all data from the index and metadata store."""
        logger.info("Clearing vector store index and metadata")

        # Create new empty FAISS index
        with self._index_lock:
            self.index = self._new_index()

        # Clear all metadata from database
        self.metadata_store.clear_all()

        # Clear structured tables
        try:
            self.structured_store.clear_all()
        except Exception as e:
            logger.warning(f"Failed to clear structured tables: {e}")

        # Clear BM25 index
        self.bm25_index.clear()

        # Save the empty index
        self.save()

        logger.info("Vector store cleared successfully")

    def get_bm25_stats(self) -> Dict[str, any]:
        """Get BM25 index statistics."""
        return self.bm25_index.get_stats()

