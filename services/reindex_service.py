"""Re-indexing service for batch document processing.

Handles:
- Background re-indexing of all documents (global or per-collection)
- Progress tracking
- Configuration snapshot for consistent indexing
- Support for both uploaded files and local file references
"""

import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any
from services.app_database import app_db
from services.document_extractor import DocumentExtractor
from services.chunker import TextChunker
from services.embedder import create_embedding_service, describe_embedding
from services.vector_store import VectorStore
from services.metadata_store import MetadataStore
from services.indexing.indexer import DocumentIndexer
from models.schemas import ChunkMetadata
from config import settings

logger = logging.getLogger(__name__)


class ReindexService:
    """Manages document re-indexing operations."""

    def __init__(self):
        self.current_job_id: Optional[int] = None
        self.current_collection_id: Optional[str] = None
        self.is_running = False
        self.reload_callback: Optional[Callable[[str], None]] = None  # Takes collection_id
        # asyncio keeps only a weak reference to tasks; hold them until done.
        self._tasks: set = set()

    def _spawn(self, fn: Callable, **kwargs) -> None:
        """Run a blocking job body on a worker thread.

        The job bodies are entirely synchronous - extraction, per-page vision
        OCR over the network, embedding, saving - so running them as a plain
        coroutine froze the event loop for the whole job: every request,
        health checks included, hung until a 400-page PDF finished OCR.
        """
        task = asyncio.create_task(asyncio.to_thread(fn, **kwargs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start_collection_reindex(
        self,
        collection_id: str,
        documents_dir: Path,
        indexes_dir: Path,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        embedding_dim: int = 384,
    ) -> int:
        """Start a re-indexing job for a specific collection.

        Args:
            collection_id: Collection to reindex
            documents_dir: Directory containing documents to index
            indexes_dir: Directory for storing indexes
            embedding_model: Embedding model to use
            chunk_size: Chunk size for text splitting
            chunk_overlap: Overlap between chunks
            embedding_dim: Embedding dimensions

        Returns:
            Job ID for tracking progress

        Raises:
            RuntimeError: If a re-indexing job is already running
        """
        # Check if already running
        active_job = app_db.get_active_reindex_job()
        if active_job:
            raise RuntimeError(
                f"Re-indexing job {active_job['id']} is already running"
            )

        # Create job record with collection info
        config_snapshot = {
            "collection_id": collection_id,
            "embedding_model": embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }

        job_id = app_db.create_reindex_job(config_snapshot)

        self._spawn(
            self._run_collection_reindex,
            job_id=job_id,
            collection_id=collection_id,
            documents_dir=documents_dir,
            indexes_dir=indexes_dir,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_dim=embedding_dim,
        )

        return job_id

    def _run_collection_reindex(
        self,
        job_id: int,
        collection_id: str,
        documents_dir: Path,
        indexes_dir: Path,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        embedding_dim: int,
    ):
        """Run the re-indexing process for a collection in background.

        Args:
            job_id: Job ID for tracking
            collection_id: Collection being reindexed
            documents_dir: Directory with documents
            indexes_dir: Directory for indexes
            embedding_model: Model name
            chunk_size: Characters per chunk
            chunk_overlap: Overlap size
            embedding_dim: Embedding dimensions
        """
        try:
            self.is_running = True
            self.current_job_id = job_id
            self.current_collection_id = collection_id

            # Update job status
            app_db.update_reindex_job(job_id, status="running")

            # Initialize services with new config
            # Route through the shared factory so a reindex re-embeds with the
            # SAME provider queries use (a hardcoded local EmbeddingService here
            # silently produced mismatched vectors under EMBEDDING_PROVIDER=ollama).
            embedding_service = create_embedding_service(collection_model=embedding_model)
            text_chunker = TextChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            document_extractor = DocumentExtractor(
                enable_ocr=settings.enable_ocr,
                ocr_max_pages=settings.ocr_max_pages,
                ocr_max_file_mb=settings.ocr_max_file_mb,
                vision_ocr_provider=settings.vision_ocr_provider,
                vision_ocr_model=settings.vision_ocr_model,
                vision_ocr_api_key=settings.vision_ocr_api_key,
                ollama_base_url=settings.ollama_base_url,
            )

            # Create new vector store in collection's indexes directory
            vector_store = VectorStore(
                index_dir=indexes_dir,
                embedding_dim=embedding_service.embedding_dim,
                # A model change is exactly when the dimension moves; the old
                # index is cleared below anyway.
                discard_mismatched_index=True,
            )
            vector_store.embedding_info = describe_embedding(embedding_service)

            # Delegate all per-document work to DocumentIndexer so CSV/XLSX
            # files get routed through the tabular path (structured SQL
            # tables + row-level chunks) and code files get symbol-aware
            # chunking. Previously this loop reinvented the chunk-embed-add
            # pipeline and silently skipped the tabular branch, leaving
            # csv_schemas empty after a re-index.
            indexer = DocumentIndexer(
                vector_store=vector_store,
                embedding_service=embedding_service,
                document_extractor=document_extractor,
                text_chunker=text_chunker,
            )

            # Get list of documents from metadata store (includes local references)
            # This ensures we reindex ALL documents, not just those in the documents_dir
            document_list = self._get_documents_to_reindex(
                vector_store.metadata_store,
                documents_dir
            )

            total_docs = len(document_list)
            app_db.update_reindex_job(job_id, total_documents=total_docs)

            if total_docs == 0:
                app_db.update_reindex_job(
                    job_id,
                    status="completed",
                    processed_documents=0
                )
                return

            # Clear existing index AFTER getting the document list
            logger.info(f"Clearing existing index for collection {collection_id} (job {job_id})")
            vector_store.clear_index()

            # Process each document
            for idx, doc_info in enumerate(document_list, 1):
                doc_path = doc_info["path"]
                source_type = doc_info.get("source_type", "upload")
                source_path = doc_info.get("source_path")

                try:
                    app_db.update_reindex_job(
                        job_id,
                        current_file=doc_path.name,
                        processed_documents=idx - 1
                    )

                    logger.info(f"Re-indexing ({idx}/{total_docs}): {doc_path.name} (source_type={source_type})")

                    if not doc_path.exists():
                        logger.warning(f"Document not found, skipping: {doc_path}")
                        continue

                    metadata = indexer.index_document(doc_path, doc_path.name)

                    if source_type in ("local_reference", "url") and source_path:
                        vector_store.metadata_store.update_document_source(
                            document_id=metadata.document_id,
                            source_path=source_path,
                            source_type=source_type,
                        )

                    logger.info(
                        f"Indexed {doc_path.name}: "
                        f"{metadata.total_pages} pages, {metadata.total_chunks} chunks"
                    )

                except Exception as e:
                    logger.error(f"Failed to process {doc_path.name}: {e}")
                    continue

            # Save the index to disk
            logger.info("Saving re-indexed data to disk...")
            vector_store.save()

            # Mark as completed
            app_db.update_reindex_job(
                job_id,
                status="completed",
                processed_documents=total_docs,
                current_file=None
            )

            logger.info(f"Re-indexing job {job_id} for collection {collection_id} completed successfully")

            # Trigger reload callback to refresh the indexer for this collection
            if self.reload_callback:
                try:
                    self.reload_callback(collection_id)
                    logger.info(f"Collection {collection_id} indexer reloaded successfully")
                except Exception as e:
                    logger.error(f"Failed to reload indexer for collection {collection_id}: {e}")

        except Exception as e:
            logger.error(f"Re-indexing job {job_id} failed: {e}")
            app_db.update_reindex_job(
                job_id,
                status="failed",
                error=str(e)
            )

        finally:
            self.is_running = False
            self.current_job_id = None
            self.current_collection_id = None

    def _get_documents_to_reindex(
        self,
        metadata_store: MetadataStore,
        documents_dir: Path
    ) -> List[Dict[str, Any]]:
        """Get list of documents to reindex from metadata store.

        This includes both:
        - Uploaded files (in documents_dir)
        - Local references (source_type='local_reference' with source_path)

        Args:
            metadata_store: MetadataStore instance to query
            documents_dir: Directory containing uploaded documents

        Returns:
            List of dicts with 'path', 'document_id', 'source_type', 'source_path'
        """
        documents = []
        seen_paths = set()
        # Files the metadata lists but that are not on disk. The caller clears
        # the index before re-adding, so skipping these would delete them.
        missing: List[str] = []

        # Get all documents from metadata store
        # list_documents() returns chunks grouped by document_id, with source info from LEFT JOIN
        try:
            doc_list = metadata_store.list_documents()

            for doc in doc_list:
                doc_id = doc.get("document_id")
                filename = doc.get("filename")

                # list_documents() already joins with documents table, so we have source info
                # If source_type is None, it means there's no document record (orphan chunks)
                source_type = doc.get("source_type") or "upload"
                source_path = doc.get("source_path")

                if source_type == "local_reference" and source_path:
                    # Local reference - use source_path
                    path = Path(source_path)
                    if not path.exists():
                        missing.append(f"{filename} (expected at {path})")
                    elif str(path) not in seen_paths:
                        documents.append({
                            "path": path,
                            "document_id": doc_id,
                            "source_type": source_type,
                            "source_path": source_path
                        })
                        seen_paths.add(str(path))
                else:
                    # Uploaded file or orphan chunks - use documents_dir
                    path = documents_dir / filename
                    if not path.exists():
                        missing.append(f"{filename} (expected at {path})")
                    elif str(path) not in seen_paths:
                        documents.append({
                            "path": path,
                            "document_id": doc_id,
                            "source_type": "upload",
                            "source_path": None
                        })
                        seen_paths.add(str(path))

        except Exception as e:
            logger.warning(f"Failed to get documents from metadata store: {e}")
            # Fallback to scanning documents_dir
            supported_extensions = {
                # Documents
                ".pdf", ".txt", ".docx", ".pptx", ".csv", ".md", ".json", ".jsonl",
                # Pascal/Delphi
                ".pas", ".dpr", ".dpk", ".pp", ".inc", ".dfm",
                # Modula-2
                ".mod", ".def", ".mi",
                # Assembly
                ".asm", ".s"
            }
            for f in documents_dir.glob("*"):
                if f.is_file() and f.suffix.lower() in supported_extensions:
                    if str(f) not in seen_paths:
                        documents.append({
                            "path": f,
                            "document_id": None,
                            "source_type": "upload",
                            "source_path": None
                        })
                        seen_paths.add(str(f))

        if missing:
            shown = "; ".join(missing[:5]) + (f"; and {len(missing) - 5} more" if len(missing) > 5 else "")
            raise RuntimeError(
                f"Re-index stopped before changing anything: {len(missing)} source file(s) "
                f"could not be found, and re-indexing would remove them from the collection. "
                f"Missing: {shown}"
            )

        logger.info(f"Found {len(documents)} documents to reindex")
        return documents

    async def start_reindex(
        self,
        documents_dir: Path,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        embedding_dim: int = 384,
    ) -> int:
        """Start a re-indexing job.

        Args:
            documents_dir: Directory containing documents to index
            embedding_model: Embedding model to use
            chunk_size: Chunk size for text splitting
            chunk_overlap: Overlap between chunks
            embedding_dim: Embedding dimensions

        Returns:
            Job ID for tracking progress

        Raises:
            RuntimeError: If a re-indexing job is already running
        """
        # Check if already running
        active_job = app_db.get_active_reindex_job()
        if active_job:
            raise RuntimeError(
                f"Re-indexing job {active_job['id']} is already running"
            )

        # Create job record
        config_snapshot = {
            "embedding_model": embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }

        job_id = app_db.create_reindex_job(config_snapshot)

        self._spawn(
            self._run_reindex,
            job_id=job_id,
            documents_dir=documents_dir,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_dim=embedding_dim,
        )

        return job_id

    def _run_reindex(
        self,
        job_id: int,
        documents_dir: Path,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        embedding_dim: int,
    ):
        """Run the re-indexing process in background.

        Args:
            job_id: Job ID for tracking
            documents_dir: Directory with documents
            embedding_model: Model name
            chunk_size: Characters per chunk
            chunk_overlap: Overlap size
            embedding_dim: Embedding dimensions
        """
        try:
            self.is_running = True
            self.current_job_id = job_id

            # Update job status
            app_db.update_reindex_job(job_id, status="running")

            # Initialize services with new config
            # Route through the shared factory so a reindex re-embeds with the
            # SAME provider queries use (a hardcoded local EmbeddingService here
            # silently produced mismatched vectors under EMBEDDING_PROVIDER=ollama).
            embedding_service = create_embedding_service(collection_model=embedding_model)
            text_chunker = TextChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            document_extractor = DocumentExtractor(
                enable_ocr=settings.enable_ocr,
                ocr_max_pages=settings.ocr_max_pages,
                ocr_max_file_mb=settings.ocr_max_file_mb,
                vision_ocr_provider=settings.vision_ocr_provider,
                vision_ocr_model=settings.vision_ocr_model,
                vision_ocr_api_key=settings.vision_ocr_api_key,
                ollama_base_url=settings.ollama_base_url,
            )

            # Create new vector store
            indexes_dir = documents_dir.parent / "indexes"
            vector_store = VectorStore(
                index_dir=indexes_dir,
                embedding_dim=embedding_service.embedding_dim,
                # A model change is exactly when the dimension moves; the old
                # index is cleared below anyway.
                discard_mismatched_index=True,
            )
            vector_store.embedding_info = describe_embedding(embedding_service)

            indexer = DocumentIndexer(
                vector_store=vector_store,
                embedding_service=embedding_service,
                document_extractor=document_extractor,
                text_chunker=text_chunker,
            )

            # Get list of documents from metadata store (includes local references)
            document_list = self._get_documents_to_reindex(
                vector_store.metadata_store,
                documents_dir
            )

            total_docs = len(document_list)
            app_db.update_reindex_job(job_id, total_documents=total_docs)

            if total_docs == 0:
                app_db.update_reindex_job(
                    job_id,
                    status="completed",
                    processed_documents=0
                )
                return

            # Clear existing index AFTER getting the document list
            logger.info(f"Clearing existing index for re-indexing job {job_id}")
            vector_store.clear_index()

            # Process each document
            for idx, doc_info in enumerate(document_list, 1):
                doc_path = doc_info["path"]
                source_type = doc_info.get("source_type", "upload")
                source_path = doc_info.get("source_path")

                try:
                    app_db.update_reindex_job(
                        job_id,
                        current_file=doc_path.name,
                        processed_documents=idx - 1
                    )

                    logger.info(f"Re-indexing ({idx}/{total_docs}): {doc_path.name}")

                    if not doc_path.exists():
                        logger.warning(f"Document not found, skipping: {doc_path}")
                        continue

                    metadata = indexer.index_document(doc_path, doc_path.name)

                    if source_type in ("local_reference", "url") and source_path:
                        vector_store.metadata_store.update_document_source(
                            document_id=metadata.document_id,
                            source_path=source_path,
                            source_type=source_type,
                        )

                    logger.info(
                        f"Indexed {doc_path.name}: "
                        f"{metadata.total_pages} pages, {metadata.total_chunks} chunks"
                    )

                except Exception as e:
                    logger.error(f"Failed to process {doc_path.name}: {e}")
                    continue

            # Save the index to disk
            logger.info("Saving re-indexed data to disk...")
            vector_store.save()

            # Mark as completed
            app_db.update_reindex_job(
                job_id,
                status="completed",
                processed_documents=total_docs,
                current_file=None
            )

            logger.info(f"Re-indexing job {job_id} completed successfully")

            # Trigger reload callback to refresh the main indexer
            if self.reload_callback:
                try:
                    self.reload_callback()
                    logger.info("Main indexer reloaded successfully")
                except Exception as e:
                    logger.error(f"Failed to reload main indexer: {e}")

        except Exception as e:
            logger.error(f"Re-indexing job {job_id} failed: {e}")
            app_db.update_reindex_job(
                job_id,
                status="failed",
                error=str(e)
            )

        finally:
            self.is_running = False
            self.current_job_id = None

    def get_job_status(self, job_id: int) -> Optional[dict]:
        """Get status of a re-indexing job.

        Args:
            job_id: Job ID

        Returns:
            Job details with progress information
        """
        return app_db.get_reindex_job(job_id)

    def get_current_job(self) -> Optional[dict]:
        """Get currently active re-indexing job.

        Returns:
            Active job details or None
        """
        return app_db.get_active_reindex_job()


# Global instance
reindex_service = ReindexService()
