"""Manages DocumentIndexer instances for each collection.

Each collection has its own:
- Vector store (FAISS index + metadata)
- Document directory
- Settings (chunk size, overlap, embedding model)
"""

import asyncio
import logging
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Dict

from services.collection_service import collection_service
from services.document_extractor import DocumentExtractor
from services.chunker import TextChunker
from services.embedder import (
    DeferredEmbeddingService, create_embedding_service, describe_embedding, index_dimension,
    embedding_signature, known_embedding_dim, local_model_cached,
)
from services.vector_store import VectorStore
from services.indexing import DocumentIndexer
from config import settings

logger = logging.getLogger(__name__)

_UNLOAD_SWEEP_INTERVAL_S = 60

# How long opening a collection or a first embed waits for a model that has to
# come over a network. A healthy endpoint answers within this; a blocked one
# carries on in the background while the collection works by keyword.
EMBEDDING_BUILD_WAIT_SECONDS = 5.0


class IndexerManager:
    """Manages DocumentIndexer instances for multiple collections."""

    def __init__(self):
        """Initialize the indexer manager."""
        self._indexers: Dict[str, DocumentIndexer] = {}
        self._embedding_services: Dict[str, object] = {}
        self.index_unload_count = 0
        # get_indexer is called from request threads and background indexing
        # threads. Without this lock, two concurrent first-touches of a
        # collection each construct a VectorStore over the same directory and
        # the loser's in-memory FAISS index silently wins the next save().
        self._creation_lock = threading.Lock()
        self._builds: Dict[str, Future] = {}
        self._builds_lock = threading.Lock()

        # Apply any DB config overrides to settings before creating the extractor
        self._apply_db_config()

        # v3.0: Initialize document extractor with OCR settings
        self._document_extractor = self._create_document_extractor()

        if settings.enable_ocr:
            ocr_available = self._document_extractor.is_ocr_available()
            engine_name = self._document_extractor.get_ocr_engine_name()
            logger.info(f"OCR enabled: {ocr_available} (engine: {engine_name or 'none'})")

    def _apply_db_config(self):
        """Apply DB config overrides to in-memory settings on startup.

        The Settings tab persists changes to the config DB and mirrors them
        into .env, but the .env write is best-effort only — in a container it
        lands on the ephemeral filesystem and is lost on recreate. The DB is
        the persistence that survives, so every persistable field must be
        re-applied from it here, not just a subset.
        """
        try:
            from services.app_database import app_db
            from services.config_manager import VALID_CONFIG_FIELDS, normalize_config_keys
            db_config = normalize_config_keys(app_db.get_all_config())
            for key in VALID_CONFIG_FIELDS:
                if key in db_config:
                    try:
                        setattr(settings, key, db_config[key])
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Could not apply DB config on startup: {e}")

    def _create_document_extractor(self) -> DocumentExtractor:
        """Create a DocumentExtractor with current settings."""
        return DocumentExtractor(
            enable_ocr=settings.enable_ocr,
            ocr_max_pages=settings.ocr_max_pages,
            ocr_max_file_mb=settings.ocr_max_file_mb,
            vision_ocr_provider=settings.vision_ocr_provider,
            vision_ocr_model=settings.vision_ocr_model,
            vision_ocr_api_key=settings.vision_ocr_api_key,
            ollama_base_url=settings.ollama_base_url,
        )

    def reload_document_extractor(self):
        """Recreate the document extractor with current settings and update all cached indexers."""
        self._document_extractor = self._create_document_extractor()
        for indexer in self._indexers.values():
            indexer.document_extractor = self._document_extractor
        if settings.enable_ocr:
            ocr_available = self._document_extractor.is_ocr_available()
            engine_name = self._document_extractor.get_ocr_engine_name()
            logger.info(f"OCR settings reloaded: available={ocr_available}, engine={engine_name or 'none'}")
        else:
            logger.info("OCR settings reloaded: OCR disabled")

    def get_indexer(self, collection_id: str = "default") -> DocumentIndexer:
        """Get or create an indexer for a collection.

        Args:
            collection_id: Collection ID (default: "default")

        Returns:
            DocumentIndexer instance for the collection
        """
        if collection_id in self._indexers:
            return self._indexers[collection_id]

        with self._creation_lock:
            # Double-checked: another thread may have created it while we waited
            if collection_id in self._indexers:
                return self._indexers[collection_id]

            # Get collection settings
            collection = collection_service.get_collection(collection_id)
            if not collection:
                raise ValueError(f"Collection '{collection_id}' not found")

            # Create indexer for this collection
            indexer = self._create_indexer(collection)
            self._indexers[collection_id] = indexer

            logger.info(f"Created indexer for collection '{collection_id}'")
            return indexer

    def _get_embedding_service(self, collection_embedding_model: str):
        """Return the appropriate embedding service based on config.

        Provider selection lives in embedder.create_embedding_service — shared
        with the reindex service so both always agree. Remote providers use
        the single globally-configured model regardless of the per-collection
        embedding_model override; the local path honors the override.
        """
        if settings.embedding_provider != "local":
            key = embedding_signature()
            if key not in self._embedding_services:
                self._embedding_services[key] = create_embedding_service()
            return self._embedding_services[key]

        # Local sentence-transformers path
        model = collection_embedding_model
        key = f"local::{model}"
        if key not in self._embedding_services:
            self._embedding_services[key] = create_embedding_service(collection_model=model)
        return self._embedding_services[key]

    def _reach_embedding_service(self, collection_embedding_model: str):
        """_get_embedding_service, for callers that must not wait on a network.

        A firewall that drops packets can hold a hub client or a remote probe
        for minutes, and opening a collection blocks every other request that
        needs one. A model already on disk loads here as before. Anything else
        is built in the background, one attempt at a time, and raises after a
        short wait; the model is in the cache when that attempt succeeds.
        """
        model = collection_embedding_model
        local = settings.embedding_provider == "local"
        if local and local_model_cached(model):
            return self._get_embedding_service(model)

        key = f"local::{model}" if local else embedding_signature()
        with self._builds_lock:
            if key in self._embedding_services:
                return self._embedding_services[key]
            build = self._builds.get(key)
            started_here = build is None
            if started_here:
                build = Future()
                self._builds[key] = build
                threading.Thread(
                    target=self._run_build, args=(key, model, build), name="embedding-build", daemon=True,
                ).start()
        try:
            # Only the first caller waits, so opening many collections does not
            # add up to minutes while one attempt is under way.
            return build.result(timeout=EMBEDDING_BUILD_WAIT_SECONDS if started_here else 0)
        except FutureTimeout:
            name = model if local else (settings.remote_embedding_model or model)
            raise RuntimeError(
                f"Still trying to reach embedding model '{name}'. "
                f"Behind a firewall this can take minutes to fail."
            ) from None

    def _run_build(self, key: str, model: str, build: Future) -> None:
        try:
            build.set_result(self._get_embedding_service(model))
        except Exception as e:  # noqa: BLE001 - the waiter or the next attempt reports it
            build.set_exception(e)
        finally:
            with self._builds_lock:
                self._builds.pop(key, None)

    def reset_embedding_services(self):
        """Forget cached embedding services after the embedding settings change.

        Indexers already open keep the service they were built with — their
        on-disk index only matches those vectors — so search keeps working.
        The new settings take effect for collections opened from now on and
        for every re-index, after which rebuild_indexer() swaps the new
        index in.
        """
        with self._creation_lock:
            self._embedding_services = {}
        logger.info("Embedding services reset; new settings apply to re-indexes and newly opened collections")

    def rebuild_indexer(self, collection_id: str = "default") -> DocumentIndexer:
        """Close a cached indexer and construct it again from current settings.

        Used after a re-index completes: unlike reload_indexer(), this copes
        with the index having been rebuilt under a different embedding
        provider (different vector dimension).
        """
        with self._creation_lock:
            old = self._indexers.pop(collection_id, None)
            if old is not None and hasattr(old.vector_store, "close"):
                try:
                    old.vector_store.close()
                except Exception as e:
                    logger.debug(f"Closing old vector store for '{collection_id}': {e}")
        return self.get_indexer(collection_id)

    def _create_indexer(self, collection: dict) -> DocumentIndexer:
        """Create a DocumentIndexer for a collection.

        Args:
            collection: Collection settings dict

        Returns:
            DocumentIndexer instance
        """
        collection_id = collection["id"]
        embedding_model = collection.get("embedding_model", settings.embedding_model)
        chunk_size = collection.get("chunk_size", settings.chunk_size)
        chunk_overlap = collection.get("chunk_overlap", settings.chunk_overlap)

        # Get paths for this collection
        indexes_dir = collection_service.get_indexes_path(collection_id)

        # Get or create embedding service. When the model cannot be built
        # right now (hub blocked behind a firewall, backend not installed,
        # key missing) the collection still opens: a deferred service
        # carries the dimension from the catalog or the existing index, if
        # either knows it, and builds the real one on the first embed, raising
        # EmbeddingUnavailable until then. Browsing, settings, the onboarding
        # and indexing for keyword search never depend on a download finishing.
        try:
            embedding_service = self._reach_embedding_service(embedding_model)
        except Exception as e:
            dim = known_embedding_dim(embedding_model, indexes_dir)
            logger.warning(
                f"Embedding model for collection '{collection_id}' is not available yet "
                f"({e}); search and new sources use keywords only until it is."
            )
            embedding_service = DeferredEmbeddingService(
                model_name=embedding_model if settings.embedding_provider == "local"
                else (settings.remote_embedding_model or embedding_model),
                embedding_dim=dim,
                factory=lambda m=embedding_model: self._reach_embedding_service(m),
                reason=str(e),
            )

        # A model that builds fine but writes vectors of another size than the
        # index on disk used to fail the whole open with a bare RuntimeError.
        # Open read-only behind the deferred service instead: browsing and
        # search keep working, and anything that needs new vectors raises the
        # typed EmbeddingDimensionMismatch the UI turns into a re-index offer.
        on_disk = index_dimension(indexes_dir)
        if on_disk is not None and on_disk != embedding_service.embedding_dim:
            logger.warning(
                f"Collection '{collection_id}' has a {on_disk}-dimensional index but model "
                f"'{embedding_model}' produces {embedding_service.embedding_dim}; opening "
                f"read-only until the collection is re-indexed."
            )
            if isinstance(embedding_service, DeferredEmbeddingService):
                # The catalog's guess lost to the vectors actually on disk.
                embedding_service.embedding_dim = on_disk
            else:
                built = embedding_service
                embedding_service = DeferredEmbeddingService(
                    model_name=getattr(built, "model_name", embedding_model),
                    embedding_dim=on_disk,
                    factory=lambda: built,
                )

        # Create vector store
        vector_store = VectorStore(
            index_dir=indexes_dir,
            embedding_dim=embedding_service.embedding_dim,
        )
        vector_store.embedding_info = describe_embedding(embedding_service)
        if isinstance(embedding_service, DeferredEmbeddingService):
            def model_arrived(real):
                from services.embedding_status import embedding_status

                vector_store.settle_embedding(describe_embedding(real))
                embedding_status.model_arrived(real)

            embedding_service.on_ready = model_arrived

        # Create text chunker with collection settings
        text_chunker = TextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        # Create indexer
        return DocumentIndexer(
            vector_store=vector_store,
            embedding_service=embedding_service,
            document_extractor=self._document_extractor,
            text_chunker=text_chunker,
        )

    def reload_indexer(self, collection_id: str = "default"):
        """Reload an indexer's vector store from disk.

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            self._indexers[collection_id].vector_store.load()
            logger.info(f"Reloaded indexer for collection '{collection_id}'")

    def remove_indexer(self, collection_id: str):
        """Remove a cached indexer without saving (e.g., after collection deletion).

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            del self._indexers[collection_id]
            logger.info(f"Removed indexer for deleted collection '{collection_id}'")

    def close_indexer(self, collection_id: str):
        """Close an indexer and release all resources (e.g., before restore).

        This properly closes database connections and clears references
        to allow file deletion on Windows.

        Args:
            collection_id: Collection ID
        """
        if collection_id in self._indexers:
            indexer = self._indexers[collection_id]
            # Close the vector store to release file handles
            if hasattr(indexer.vector_store, 'close'):
                indexer.vector_store.close()
            del self._indexers[collection_id]
            logger.info(f"Closed indexer for collection '{collection_id}'")

    def unload_idle_indexes(self) -> int:
        """Release vectors no search or write has touched for a while.

        Returns how many collections were released. They stay open and read
        their vectors back on the next use; see VectorStore.unload_if_idle.
        """
        idle_seconds = settings.index_idle_unload_seconds
        if idle_seconds <= 0:
            return 0
        released = 0
        for collection_id, indexer in list(self._indexers.items()):
            if indexer.vector_store.unload_if_idle(idle_seconds):
                released += 1
                logger.info(f"Released idle index of collection '{collection_id}' from memory")
        self.index_unload_count += released
        return released

    def index_memory_stats(self) -> dict:
        """What the open collections hold in memory, for /metrics."""
        stats = [indexer.vector_store for indexer in list(self._indexers.values())]
        memory = [store.memory_stats() for store in stats]
        return {
            "open": len(stats),
            "loaded": sum(m["loaded"] for m in memory),
            "vectors": sum(m["vectors"] for m in memory),
            "bytes": sum(m["bytes"] for m in memory),
            "unloads": self.index_unload_count,
            "reloads": sum(store.reload_count for store in stats),
        }

    def save_all(self):
        """Save all indexers to disk."""
        for collection_id, indexer in self._indexers.items():
            try:
                indexer.save_index()
                logger.info(f"Saved index for collection '{collection_id}'")
            except Exception as e:
                logger.error(f"Failed to save index for '{collection_id}': {e}")

    def get_documents_path(self, collection_id: str = "default") -> Path:
        """Get the documents directory for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to documents directory
        """
        return collection_service.get_documents_path(collection_id)

    def get_indexes_path(self, collection_id: str = "default") -> Path:
        """Get the indexes directory for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Path to indexes directory
        """
        return collection_service.get_indexes_path(collection_id)

    def get_collection_stats(self, collection_id: str = "default") -> dict:
        """Get statistics for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            Dict with document count, chunk count, etc.
        """
        try:
            indexer = self.get_indexer(collection_id)
            # SQL aggregates, not a full list_documents() walk — this backs
            # /health and the stats endpoint, so it must stay flat as the
            # collection grows (63k documents was a ~25MB list previously).
            doc_stats = indexer.get_document_stats()

            from services import storage_quota

            limit = storage_quota.limit_bytes()
            used = int(doc_stats.get("storage_bytes") or 0)
            return {
                "collection_id": collection_id,
                "total_documents": doc_stats["total_documents"],
                "total_chunks": indexer.vector_store.get_total_chunks(),
                "total_pages": doc_stats["total_pages"],
                "storage_bytes": used,
                "storage_limit_bytes": limit,
                "storage_remaining_bytes": max(0, limit - used) if limit else None,
                "storage_percent": round(min(100.0, used / limit * 100), 1) if limit else 0.0,
            }
        except Exception as e:
            logger.error(f"Failed to get stats for '{collection_id}': {e}")
            return {
                "collection_id": collection_id,
                "total_documents": 0,
                "total_chunks": 0,
                "total_pages": 0,
                "storage_bytes": 0,
                "storage_limit_bytes": 0,
                "storage_remaining_bytes": None,
                "storage_percent": 0.0,
            }


# Global instance
indexer_manager = IndexerManager()


async def index_unload_loop() -> None:
    """Started from main.py's lifespan; a no-op when INDEX_IDLE_UNLOAD_SECONDS is 0."""
    while True:
        await asyncio.sleep(_UNLOAD_SWEEP_INTERVAL_S)
        try:
            await asyncio.to_thread(indexer_manager.unload_idle_indexes)
        except Exception as e:  # noqa: BLE001 - the next sweep tries again
            logger.warning(f"Idle index sweep failed: {e}")

