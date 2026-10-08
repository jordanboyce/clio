"""Document indexing orchestration service."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Callable
import hashlib
import logging
from datetime import datetime

from config import settings
from models.schemas import (
    ChunkMetadata,
    DocumentMetadata,
    AIOptions,
    AIUsage,
    AIUsageDetail,
    SearchMode,
)
from services.document_extractor import DocumentExtractor, ExtractionResult
from services.chunker import TextChunker
from services.embedder import EmbeddingService
from services.vector_store import VectorStore
from services import audit, content_policy
from services.content_policy import BlockedContentError, ContentRejectedError
from services.governance import content_hash_of

logger = logging.getLogger(__name__)

# Type alias for progress callback
# Signature: (phase: str, progress: int, detail: str, chunks_done: int, chunks_total: int)
ProgressCallback = Callable[[str, int, Optional[str], int, int], None]


@dataclass
class PreparedDocument:
    """Extraction/chunking output held in memory until a bulk embed+persist flush.

    Produced by DocumentIndexer.prepare_document, consumed (possibly many files
    at a time) by DocumentIndexer.index_prepared_documents.
    """

    document_id: str
    filename: str
    chunks: List[ChunkMetadata]
    total_pages: int
    source_format: str
    extraction_method: str
    injection_warnings: Optional[dict] = None
    # v3.3 governance: decided at prepare time so the batch persist is a
    # pure write and a rejected file never reaches the embedder.
    uploaded_by: Optional[str] = None
    content_hash: Optional[str] = None
    sensitivity: Optional[str] = None
    policy_status: str = "clear"
    policy_flags: Optional[dict] = None
    # v3.4: bytes of the source file, for the per-collection storage cap.
    file_size: Optional[int] = None


class ChunkBatcher:
    """Accumulates items until their combined chunk count reaches a threshold.

    Flush boundaries are document-aligned: a document's chunks are never split
    across flushes, so a batch may exceed the threshold by up to one document.
    """

    def __init__(self, flush_chunks: int):
        self.flush_chunks = max(1, int(flush_chunks))
        self._pending: List[Any] = []
        self._chunk_count = 0

    def __len__(self) -> int:
        return len(self._pending)

    @property
    def chunk_count(self) -> int:
        return self._chunk_count

    def add(self, item: Any, num_chunks: int) -> Optional[List[Any]]:
        """Add an item; returns the accumulated batch once it is due to flush."""
        self._pending.append(item)
        self._chunk_count += num_chunks
        if self._chunk_count >= self.flush_chunks:
            return self.drain()
        return None

    def drain(self) -> List[Any]:
        """Return and clear whatever is pending (possibly empty)."""
        batch = self._pending
        self._pending = []
        self._chunk_count = 0
        return batch


class DocumentIndexer:
    """Orchestrates the document indexing pipeline."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        document_extractor: DocumentExtractor,
        text_chunker: TextChunker,
    ):
        """
        Initialize the document indexer.

        Args:
            vector_store: Vector store instance
            embedding_service: Embedding service instance
            document_extractor: Document extractor instance (supports PDF, TXT, DOCX, CSV)
            text_chunker: Text chunker instance
        """
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.document_extractor = document_extractor
        self.text_chunker = text_chunker

    def index_document(
        self,
        document_path: Path,
        filename: str,
        collection_id: str | None = None,
        uploaded_by: str | None = None,
    ) -> DocumentMetadata:
        """
        Index a single document (PDF, TXT, DOCX, CSV, MD, or JSON).

        Args:
            document_path: Path to the document file
            filename: Original filename
            collection_id: Collection being indexed into
            uploaded_by: Verified identity adding the document (attribution)

        Returns:
            DocumentMetadata object
        """
        return self.index_document_with_progress(
            document_path, filename,
            progress_callback=None,
            collection_id=collection_id,
            uploaded_by=uploaded_by,
        )

    # ── Governance helpers ────────────────────────────────────────────

    @staticmethod
    def _file_size(document_path: Path) -> Optional[int]:
        """Bytes on disk, or None when the path cannot be stat'ed."""
        try:
            return document_path.stat().st_size
        except OSError:
            return None

    def _admit(self, document_path: Path, filename: str, collection_id: str | None,
               uploaded_by: str | None) -> tuple[str, str]:
        """Hash the file and refuse it if the hash is blocklisted.

        Returns (content_hash, document_id). The document id has always been
        the first 16 hex chars of the sha256; the full hash is what the
        blocklist keys on.
        """
        content_hash = content_hash_of(document_path)
        document_id = content_hash[:16]
        if content_policy.is_hash_blocked(content_hash):
            audit.record("document.blocked", actor=uploaded_by, collection_id=collection_id,
                         document_id=document_id, target=content_hash,
                         detail={"filename": filename})
            raise BlockedContentError(
                f"{filename} matches a file an administrator removed and blocked; it cannot be re-added."
            )
        return content_hash, document_id

    def _apply_policy(
        self,
        page_results: dict,
        page_texts: dict,
        *,
        filename: str,
        document_id: str,
        collection_id: str | None,
        uploaded_by: str | None,
    ) -> tuple[str, Optional[dict], Optional[str]]:
        """Fold the per-page scan into the document's policy decision.

        Records the outcome in the audit trail and raises
        ContentRejectedError under CONTENT_POLICY_ACTION=reject so the file
        never reaches the embedder or the metadata store.
        """
        if not content_policy.scanning_enabled():
            return "clear", None, None
        llm = content_policy.llm_review(page_texts, filename) if settings.content_policy_llm_review else None
        status, flags, suggested = content_policy.decide(page_results or {}, llm=llm)
        if status != "clear":
            audit.record(
                f"document.{status}", actor=uploaded_by, collection_id=collection_id,
                document_id=document_id,
                detail={
                    "filename": filename,
                    "categories": (flags or {}).get("categories"),
                    "max_score": (flags or {}).get("max_score"),
                    "critical": (flags or {}).get("critical"),
                    "llm": ((flags or {}).get("llm") or {}).get("categories"),
                },
            )
        if status == "rejected":
            cats = ", ".join(sorted((flags or {}).get("categories") or {})) or "policy"
            raise ContentRejectedError(
                f"{filename} was refused by the content policy ({cats}). "
                f"Contact an administrator if you believe this is a mistake."
            )
        return status, flags, suggested

    def index_document_with_progress(
        self,
        document_path: Path,
        filename: str,
        progress_callback: Optional[ProgressCallback] = None,
        collection_id: str | None = None,
        uploaded_by: str | None = None,
    ) -> DocumentMetadata:
        """
        Index a single document with granular progress reporting (v4.0).

        Args:
            document_path: Path to the document file
            filename: Original filename
            progress_callback: Optional callback for progress updates
                Signature: (phase, progress, detail, chunks_done, chunks_total)
            uploaded_by: Verified identity adding the document (attribution)

        Returns:
            DocumentMetadata object
        """
        logger.info(f"Indexing document: {filename}")

        def report(phase: str, progress: int, detail: str = None,
                   chunks_done: int = 0, chunks_total: int = 0):
            """Helper to safely call progress callback."""
            if progress_callback:
                try:
                    progress_callback(phase, progress, detail, chunks_done, chunks_total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        # Hash the content (document id + blocklist key); refuse blocked files.
        content_hash, document_id = self._admit(document_path, filename, collection_id, uploaded_by)

        # Determine source format from file extension
        source_format = document_path.suffix.lower().lstrip(".")

        # Handle CSV/XLSX with row-level + structured indexing.
        # Both formats are normalized to the same "sheet of rows" shape and get
        # both a semantic row index (for free-text search) and a typed SQL
        # table (for aggregation / numeric queries).
        if source_format in ("csv", "xlsx", "xls") and settings.csv_row_level_indexing:
            return self._index_tabular_document(
                document_path, filename, document_id, source_format=source_format,
                progress_callback=progress_callback,
                collection_id=collection_id,
                uploaded_by=uploaded_by, content_hash=content_hash,
            )

        # Handle code files with symbol-aware chunking
        if self.document_extractor.is_code_file(document_path):
            return self._index_code_file(
                document_path, filename, document_id, source_format,
                progress_callback=progress_callback,
                collection_id=collection_id,
                uploaded_by=uploaded_by, content_hash=content_hash,
            )

        # Phase 1: Extract text from document
        report("extracting", 0, f"Extracting text from {filename}")
        logger.debug(f"Extracting text from {filename}")
        extraction_result = self.document_extractor.extract_text(document_path)

        page_texts = extraction_result.page_texts
        extraction_method = extraction_result.method
        injection_warnings = {
            str(page): scan.to_dict()  # JSON keys; DocumentMetadata requires str
            for page, scan in extraction_result.injection_warnings.items()
            if scan.is_flagged
        } or None

        num_pages = len(page_texts)
        report("extracting", 100, f"Extracted {num_pages} pages")

        if num_pages == 0:
            logger.warning(f"No pages extracted from {filename}")
            raise ValueError(f"Could not extract any pages from {filename}")

        # Content policy: decide before spending embedding time.
        policy_status, policy_flags, suggested_sensitivity = self._apply_policy(
            extraction_result.policy_warnings, page_texts,
            filename=filename, document_id=document_id,
            collection_id=collection_id, uploaded_by=uploaded_by,
        )

        # Phase 2: Chunk the text with format metadata
        report("chunking", 0, f"Chunking {num_pages} pages")
        logger.debug(f"Chunking text from {filename}")
        chunks = self.text_chunker.chunk_document(
            page_texts=page_texts,
            document_id=document_id,
            filename=filename,
            source_format=source_format,
            extraction_method=extraction_method,
        )

        num_chunks = len(chunks)
        report("chunking", 100, f"Created {num_chunks} chunks", 0, num_chunks)

        if num_chunks == 0:
            logger.warning(f"No chunks created from {filename}")
            raise ValueError(f"Could not create any chunks from {filename}")

        # Phase 3: Generate embeddings (with progress for large files)
        report("embedding", 0, f"Generating embeddings for {num_chunks} chunks", 0, num_chunks)
        logger.debug(f"Generating embeddings for {num_chunks} chunks")

        chunk_texts = [chunk.text for chunk in chunks]

        # The embedding service batches internally; we just relay progress.
        def _embed_progress(done: int, total: int):
            report("embedding", min(100, int(done / total * 100)),
                   f"Embedded {done}/{total} chunks", done, total)

        embeddings = self.embedding_service.embed_texts(
            chunk_texts,
            progress_callback=_embed_progress if progress_callback else None,
        )
        report("embedding", 100, f"Embedded {num_chunks} chunks", num_chunks, num_chunks)

        # Phase 4: Add to vector store
        report("saving", 0, f"Saving {num_chunks} chunks to index")
        logger.debug(f"Adding {num_chunks} chunks to vector store")
        self.vector_store.add_chunks(chunks, embeddings)

        # Add document record to metadata store
        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=num_pages,
            num_chunks=num_chunks,
            upload_timestamp=indexed_at,
            source_format=source_format,
            extraction_method=extraction_method,
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            injection_warnings=injection_warnings,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )
        report("saving", 100, f"Saved {num_chunks} chunks")

        # Create metadata with v3.0 fields
        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=num_pages,
            total_chunks=num_chunks,
            indexed_at=indexed_at,
            source_format=source_format,
            extraction_method=extraction_method,
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            injection_warnings=injection_warnings,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )

        logger.info(
            f"Successfully indexed {filename}: {num_pages} pages, {num_chunks} chunks "
            f"(format={source_format}, method={extraction_method}, policy={policy_status})"
        )
        return metadata

    def prepare_document(
        self,
        document_path: Path,
        filename: str,
        collection_id: str | None = None,
        uploaded_by: str | None = None,
    ) -> Optional[PreparedDocument]:
        """
        Extract and chunk a document without embedding or persisting anything.

        This is the front half of the bulk ingest pipeline: callers accumulate
        the returned PreparedDocuments and hand them to
        index_prepared_documents in batches, so embedding batches actually
        fill and SQLite/BM25 commits amortize across many files.

        Returns None when the file must go through the single-file path
        instead (tabular CSV/XLSX documents, which skip the chunk/embed
        pipeline entirely). Raises on extraction/chunking failure, matching
        index_document_with_progress.
        """
        source_format = document_path.suffix.lower().lstrip(".")

        # Tabular documents are indexed structured-only; there is nothing to
        # batch, so the caller should fall back to the single-file path.
        if source_format in ("csv", "xlsx", "xls") and settings.csv_row_level_indexing:
            return None

        content_hash, document_id = self._admit(document_path, filename, collection_id, uploaded_by)

        if self.document_extractor.is_code_file(document_path):
            code_chunks = self.document_extractor.extract_code_chunks(document_path, document_id)
            if not code_chunks:
                logger.warning(f"No chunks extracted from code file {filename}")
                raise ValueError(f"Could not extract any chunks from {filename}")
            chunks = self._code_chunks_to_metadata(code_chunks, source_format)
            policy_status, policy_flags, suggested = self._scan_code_policy(
                chunks, filename=filename, document_id=document_id,
                collection_id=collection_id, uploaded_by=uploaded_by,
            )
            return PreparedDocument(
                document_id=document_id,
                filename=filename,
                chunks=chunks,
                total_pages=len(chunks),  # for code, "pages" = symbol chunks
                source_format=source_format,
                extraction_method="text",
                uploaded_by=uploaded_by,
                content_hash=content_hash,
                sensitivity=suggested,
                policy_status=policy_status,
                policy_flags=policy_flags,
                file_size=self._file_size(document_path),
            )

        extraction_result = self.document_extractor.extract_text(document_path)
        page_texts = extraction_result.page_texts
        injection_warnings = {
            str(page): scan.to_dict()  # JSON keys; DocumentMetadata requires str
            for page, scan in extraction_result.injection_warnings.items()
            if scan.is_flagged
        } or None

        if len(page_texts) == 0:
            logger.warning(f"No pages extracted from {filename}")
            raise ValueError(f"Could not extract any pages from {filename}")

        chunks = self.text_chunker.chunk_document(
            page_texts=page_texts,
            document_id=document_id,
            filename=filename,
            source_format=source_format,
            extraction_method=extraction_result.method,
        )
        if len(chunks) == 0:
            logger.warning(f"No chunks created from {filename}")
            raise ValueError(f"Could not create any chunks from {filename}")

        policy_status, policy_flags, suggested = self._apply_policy(
            extraction_result.policy_warnings, page_texts,
            filename=filename, document_id=document_id,
            collection_id=collection_id, uploaded_by=uploaded_by,
        )

        return PreparedDocument(
            document_id=document_id,
            filename=filename,
            chunks=chunks,
            total_pages=len(page_texts),
            source_format=source_format,
            extraction_method=extraction_result.method,
            injection_warnings=injection_warnings,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )

    def index_prepared_documents(
        self,
        prepared: List[PreparedDocument],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[DocumentMetadata]:
        """
        Embed and persist a batch of prepared documents in one pass.

        All chunks across the batch are embedded together (the embedding
        service still batches internally at EMBED_BATCH_SIZE and holds its
        encode lock only per internal batch), then chunk rows, BM25 entries,
        FAISS vectors, and document rows each land as a single batched write
        instead of one round-trip per file.

        Args:
            prepared: PreparedDocuments from prepare_document
            progress_callback: Optional (chunks_done, chunks_total) callback
                covering the whole batch

        Returns:
            One DocumentMetadata per prepared document, in input order
        """
        if not prepared:
            return []

        all_chunks = [chunk for p in prepared for chunk in p.chunks]
        logger.info(
            f"Bulk-indexing {len(prepared)} prepared documents "
            f"({len(all_chunks)} chunks)"
        )

        embeddings = self.embedding_service.embed_texts(
            [chunk.text for chunk in all_chunks],
            progress_callback=progress_callback,
        )
        self.vector_store.add_chunks(all_chunks, embeddings)

        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_documents([
            {
                "document_id": p.document_id,
                "filename": p.filename,
                "num_pages": p.total_pages,
                "num_chunks": len(p.chunks),
                "upload_timestamp": indexed_at,
                "source_format": p.source_format,
                "extraction_method": p.extraction_method,
                "embedding_model": self.embedding_service.model_name,
                "chunk_size": self.text_chunker.chunk_size,
                "chunk_overlap": self.text_chunker.chunk_overlap,
                "injection_warnings": p.injection_warnings,
                "uploaded_by": p.uploaded_by,
                "content_hash": p.content_hash,
                "sensitivity": p.sensitivity,
                "policy_status": p.policy_status,
                "policy_flags": p.policy_flags,
                "file_size": p.file_size,
            }
            for p in prepared
        ])

        return [
            DocumentMetadata(
                document_id=p.document_id,
                filename=p.filename,
                total_pages=p.total_pages,
                total_chunks=len(p.chunks),
                indexed_at=indexed_at,
                source_format=p.source_format,
                extraction_method=p.extraction_method,
                embedding_model=self.embedding_service.model_name,
                chunk_size=self.text_chunker.chunk_size,
                chunk_overlap=self.text_chunker.chunk_overlap,
                injection_warnings=p.injection_warnings,
                uploaded_by=p.uploaded_by,
                content_hash=p.content_hash,
                sensitivity=p.sensitivity,
                policy_status=p.policy_status,
                policy_flags=p.policy_flags,
                file_size=p.file_size,
            )
            for p in prepared
        ]

    def _index_tabular_document(
        self,
        document_path: Path,
        filename: str,
        document_id: str,
        source_format: str = "csv",
        progress_callback: Optional[ProgressCallback] = None,
        collection_id: str | None = None,
        uploaded_by: str | None = None,
        content_hash: str | None = None,
    ) -> DocumentMetadata:
        """Index a CSV or Excel workbook into the structured SQL store only.

        Tabular data doesn't go through the chunk/embed pipeline — every real
        question against it ("top positions", "sector concentration",
        "unrealized G/L by account") is SQL, which the chat path already runs
        against the typed tables built here. Skipping embedding saves a
        network round-trip per upload and avoids false-positive "missing from
        semantic search" confusion for numeric data.

        Every sheet in a multi-sheet workbook gets its own structured table.
        """

        def report(phase: str, progress: int, detail: str = None,
                   chunks_done: int = 0, chunks_total: int = 0):
            if progress_callback:
                try:
                    progress_callback(phase, progress, detail, chunks_done, chunks_total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        logger.info(f"Indexing tabular document ({source_format}): {filename}")
        report("extracting", 0, f"Reading {filename}")

        sheets = self.document_extractor.extract_tabular_sheets(document_path)
        if not sheets:
            raise ValueError(f"Could not extract any rows from {filename}")

        total_rows = sum(len(s['rows']) for s in sheets)
        report("extracting", 100,
               f"Extracted {total_rows} rows across {len(sheets)} sheet(s)")

        # Content policy over a rendering of the first rows of each sheet —
        # a credential dump is most often a spreadsheet.
        policy_status, policy_flags, suggested_sensitivity = "clear", None, None
        if content_policy.scanning_enabled():
            page_texts = {
                n: "\n".join(
                    ", ".join(str(v) for v in (row.values() if isinstance(row, dict) else row))
                    for row in sheet['rows'][:500]
                )
                for n, sheet in enumerate(sheets, start=1)
            }
            policy_status, policy_flags, suggested_sensitivity = self._apply_policy(
                content_policy.scan_pages(page_texts, filename=filename), page_texts,
                filename=filename, document_id=document_id,
                collection_id=collection_id, uploaded_by=uploaded_by,
            )

        # Populate the structured store for every sheet. No chunks, no
        # embeddings — the chat path queries this via SQL.
        for sheet_num, sheet in enumerate(sheets, start=1):
            sheet_name = sheet['sheet_name']
            columns = sheet['columns']
            rows = sheet['rows']

            # A tabular document with no structured table AND no chunks is
            # completely unqueryable — surfacing that as success would hide
            # the failure from the user, so let the exception propagate and
            # fail the file (batch endpoints already report per-file errors).
            self.vector_store.structured_store.create_table(
                document_id=document_id,
                filename=filename,
                columns=columns,
                rows=rows,
                sheet_name=sheet_name,
                role_overrides=sheet.get('role_overrides') or {},
                type_overrides=sheet.get('type_overrides') or {},
            )

        report("saving", 0, f"Recording {total_rows} rows")
        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=total_rows,
            num_chunks=0,
            upload_timestamp=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )
        report("saving", 100, f"Recorded {total_rows} rows")

        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=total_rows,
            total_chunks=0,
            indexed_at=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )

        logger.info(
            f"Successfully indexed {source_format.upper()} {filename}: "
            f"{total_rows} rows across {len(sheets)} sheet(s) (structured-only)"
        )
        return metadata

    def _index_code_file(
        self,
        document_path: Path,
        filename: str,
        document_id: str,
        source_format: str,
        progress_callback: Optional[ProgressCallback] = None,
        collection_id: str | None = None,
        uploaded_by: str | None = None,
        content_hash: str | None = None,
    ) -> DocumentMetadata:
        """
        Index a source code file using symbol-aware chunking.

        Extracts functions, classes, procedures etc. as individual chunks
        so semantic search preserves symbol boundaries.
        """
        def report(phase: str, progress: int, detail: str = None,
                   chunks_done: int = 0, chunks_total: int = 0):
            if progress_callback:
                try:
                    progress_callback(phase, progress, detail, chunks_done, chunks_total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        logger.info(f"Indexing code file with symbol-aware chunking: {filename}")

        report("extracting", 0, f"Extracting symbols from {filename}")
        code_chunks = self.document_extractor.extract_code_chunks(document_path, document_id)

        if not code_chunks:
            logger.warning(f"No chunks extracted from code file {filename}")
            raise ValueError(f"Could not extract any chunks from {filename}")

        chunks = self._code_chunks_to_metadata(code_chunks, source_format)
        num_chunks = len(chunks)
        report("extracting", 100, f"Extracted {num_chunks} symbol chunks")

        policy_status, policy_flags, suggested_sensitivity = self._scan_code_policy(
            chunks, filename=filename, document_id=document_id,
            collection_id=collection_id, uploaded_by=uploaded_by,
        )

        report("embedding", 0, f"Generating embeddings for {num_chunks} chunks", 0, num_chunks)
        chunk_texts = [c.text for c in chunks]

        # The embedding service batches internally; we just relay progress.
        def _embed_progress(done: int, total: int):
            report("embedding", min(100, int(done / total * 100)),
                   f"Embedded {done}/{total} chunks", done, total)

        embeddings = self.embedding_service.embed_texts(
            chunk_texts,
            progress_callback=_embed_progress if progress_callback else None,
        )
        report("embedding", 100, f"Embedded {num_chunks} chunks", num_chunks, num_chunks)

        report("saving", 0, f"Saving {num_chunks} chunks to index")
        self.vector_store.add_chunks(chunks, embeddings)

        indexed_at = datetime.utcnow().isoformat()
        self.vector_store.metadata_store.add_document(
            document_id=document_id,
            filename=filename,
            num_pages=num_chunks,   # for code, "pages" = symbol chunks
            num_chunks=num_chunks,
            upload_timestamp=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )
        report("saving", 100, f"Saved {num_chunks} chunks")

        metadata = DocumentMetadata(
            document_id=document_id,
            filename=filename,
            total_pages=num_chunks,
            total_chunks=num_chunks,
            indexed_at=indexed_at,
            source_format=source_format,
            extraction_method="text",
            embedding_model=self.embedding_service.model_name,
            chunk_size=self.text_chunker.chunk_size,
            chunk_overlap=self.text_chunker.chunk_overlap,
            uploaded_by=uploaded_by,
            content_hash=content_hash,
            sensitivity=suggested_sensitivity,
            policy_status=policy_status,
            policy_flags=policy_flags,
            file_size=self._file_size(document_path),
        )

        logger.info(f"Successfully indexed code file {filename}: {num_chunks} symbol chunks")
        return metadata

    def _scan_code_policy(self, chunks, *, filename: str, document_id: str,
                          collection_id: str | None, uploaded_by: str | None):
        """Code gets only the sensitivity packs (secrets, credential dumps):
        the acceptability rules are noise over identifiers and fixtures."""
        if not content_policy.scanning_enabled():
            return "clear", None, None
        page_texts = {i: c.text for i, c in enumerate(chunks, start=1)}
        results = content_policy.scan_pages(page_texts, filename=filename, code=True)
        return self._apply_policy(
            results, page_texts, filename=filename, document_id=document_id,
            collection_id=collection_id, uploaded_by=uploaded_by,
        )

    @staticmethod
    def _code_chunks_to_metadata(code_chunks, source_format: str) -> List[ChunkMetadata]:
        """Convert CodeChunk objects to ChunkMetadata for the vector store."""
        return [
            ChunkMetadata(
                chunk_id=cc.chunk_id,
                document_id=cc.document_id,
                filename=cc.filename,
                page_number=cc.line_start,   # line number as logical "page"
                chunk_index=cc.chunk_index,
                text=cc.text,
                source_format=source_format,
                extraction_method="text",
                language=cc.language,
                unit_name=cc.unit_name,
                symbol_name=cc.symbol_name,
                symbol_type=cc.symbol_type,
                line_start=cc.line_start,
                line_end=cc.line_end,
                parent_symbol=cc.parent_symbol,
            )
            for cc in code_chunks
        ]

    def rebuild_vector_index(self, batch_size: int = 128) -> int:
        """Rebuild the FAISS index by re-embedding every chunk's stored text.

        Repairs collections whose index drifted from metadata (the legacy
        positional index could accumulate stale vectors across deletes and
        re-indexes). Needs no source documents — chunk text lives in SQLite.

        Returns the number of chunks re-embedded.
        """
        import numpy as np
        import faiss

        metadata_store = self.vector_store.metadata_store
        chunks = metadata_store.get_all_chunks_ordered()
        id_by_chunk = metadata_store.get_ids_for_chunk_ids(
            [c["chunk_id"] for c in chunks]
        )

        logger.info(f"Rebuilding vector index from metadata: {len(chunks)} chunks")
        new_index = self.vector_store._new_index()

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            embeddings = self.embedding_service.embed_texts([c["text"] for c in batch])
            embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
            ids = np.asarray([id_by_chunk[c["chunk_id"]] for c in batch], dtype=np.int64)
            new_index.add_with_ids(embeddings.astype(np.float32), ids)
            if (start // batch_size) % 10 == 0:
                logger.info(f"Rebuild progress: {min(start + batch_size, len(chunks))}/{len(chunks)}")

        self.vector_store.index = new_index
        self.vector_store.save()
        logger.info(f"Vector index rebuilt with {new_index.ntotal} vectors")
        return new_index.ntotal

    def search(
        self,
        query: str,
        top_k: int = 10,
        ai_service=None,
        ai_options: Optional[AIOptions] = None,
        mode: SearchMode = SearchMode.SEMANTIC,
        semantic_weight: float = 0.7,
        collection_overview: Optional[str] = None,
        structured_context: Optional[str] = None,
        skip_filenames: Optional[set] = None,
        filters: Optional[dict] = None,
    ) -> dict:
        """
        Search for documents matching the query, with optional AI enhancements.

        Args:
            query: Search query text
            top_k: Number of results to return
            ai_service: Optional AIService instance (created from user's API key)
            ai_options: Optional AI feature flags
            mode: Search mode (semantic, keyword, or hybrid)
            semantic_weight: Weight for semantic search in hybrid mode (0-1)
            filters: Optional metadata pre-filter dict. Recognized keys:
                document_ids, source_formats, filenames, date_from, date_to.
                Restricts retrieval to chunks whose document matches before
                ranking — sharply improves precision on large collections.

        Returns:
            Dict with 'results', and optionally 'enhanced_query', 'synthesis', 'ai_usage'
        """
        logger.info(f"Searching for: {query[:100]} (mode={mode.value})")

        # Resolve the metadata pre-filter to a concrete set of allowed chunk_ids.
        # None => no restriction. Empty set => filter matched nothing; bail early.
        allowed_chunk_ids: Optional[set] = None
        if filters:
            allowed_chunk_ids = self.vector_store.metadata_store.get_filtered_chunk_ids(
                document_ids=filters.get("document_ids"),
                source_formats=filters.get("source_formats"),
                filenames=filters.get("filenames"),
                date_from=filters.get("date_from"),
                date_to=filters.get("date_to"),
            )
            if allowed_chunk_ids is not None and len(allowed_chunk_ids) == 0:
                logger.info("Search filters matched no documents; returning no results")
                return {"results": [], "synthesis": None, "ai_usage": None}

        ai_active = ai_service and ai_options
        ai_usage = AIUsage() if ai_active else None
        synthesis = None

        # A local cross-encoder reranker (if enabled) runs for EVERY caller,
        # including MCP search which passes no ai_service. Resolve it up front so
        # we know whether to widen the candidate pool below.
        from services.reranker import get_reranker
        local_reranker = get_reranker()

        # Fetch extra results when any reranker will run (local cross-encoder or
        # the AI-judgment reranker) so the reranker has a bigger pool to reorder.
        from config import settings as _settings
        want_wider_pool = local_reranker is not None or (ai_active and ai_options.rerank)
        mult = max(1, getattr(_settings, "reranker_candidate_multiplier", 5))
        fetch_k = min(top_k * mult, 50) if want_wider_pool else top_k

        # Perform search based on mode
        if mode == SearchMode.KEYWORD:
            # Pure BM25 keyword search. When filtering, pull the full ranked list
            # so post-filtering down to fetch_k stays exact.
            bm25_k = self.vector_store.index.ntotal if allowed_chunk_ids is not None else fetch_k
            bm25_results = self.vector_store.bm25_index.search(query, max(bm25_k, 1))
            if allowed_chunk_ids is not None:
                bm25_results = [
                    (cid, score) for cid, score in bm25_results if cid in allowed_chunk_ids
                ][:fetch_k]
            results = self._bm25_to_search_results(bm25_results)
        elif mode == SearchMode.HYBRID:
            # Combined semantic + keyword search
            query_embedding = self.embedding_service.embed_query(query)
            results = self.vector_store.search_hybrid(
                query=query,
                query_embedding=query_embedding,
                top_k=fetch_k,
                semantic_weight=semantic_weight,
                allowed_chunk_ids=allowed_chunk_ids,
            )
        else:
            # Default: pure semantic search
            query_embedding = self.embedding_service.embed_query(query)
            results = self.vector_store.search(
                query_embedding, top_k=fetch_k, allowed_chunk_ids=allowed_chunk_ids
            )

        # Entity-graph boost: fuse chunks that share entities with the query
        # into the result set. Cross-document matches enter as candidates
        # (base vector score); chunks sharing >= 2 distinct query entities
        # also get a saturating bonus, rarer entities weighing more. Off by
        # config, or a no-op when the query names no known entities.
        from config import settings as _eg_settings
        if _eg_settings.enable_entity_boost:
            results = self._apply_entity_graph(
                query=query,
                results=results,
                fetch_k=fetch_k,
                mode=mode,
                query_embedding=locals().get("query_embedding"),
                allowed_chunk_ids=allowed_chunk_ids,
            )

        # Quarantined documents stay indexed (so an admin can approve them
        # without a re-index) but must never surface: this is the retrieval
        # choke point every surface — search, chat, MCP — comes through.
        hidden = self.vector_store.metadata_store.get_hidden_document_ids()
        if hidden:
            results = [r for r in results if r.document_id not in hidden]

        # Drop results from files that are already fully inlined as structured
        # JSONL in the synthesis prompt — keeping their chunks would waste
        # tokens and risk the model trusting truncated snippets over the full
        # authoritative data.
        if skip_filenames:
            results = [r for r in results if r.filename not in skip_filenames]

        # Step 3a: Local cross-encoder rerank (preferred — local, free, nothing
        # leaves the machine, runs for every caller). When it reorders the pool we skip the
        # AI-judgment reranker below to avoid a redundant second pass.
        reranked_locally = False
        if local_reranker is not None and len(results) > 1:
            try:
                results = local_reranker.rerank(query, results, top_k)
                reranked_locally = True
            except Exception as e:
                logger.warning(f"Local reranking failed, using retrieval order: {e}")

        # Step 3b: Optionally rerank results with the AI-judgment reranker
        if ai_active and ai_options.rerank and not reranked_locally and len(results) > 0:
            try:
                rerank_input = [
                    {
                        "index": i,
                        "filename": r.filename,
                        "text_snippet": r.text_snippet,
                        "similarity_score": r.similarity_score,
                    }
                    for i, r in enumerate(results)
                ]
                rerank_result = ai_service.rerank_results(query, rerank_input, top_k)
                usage = rerank_result["usage"]

                if usage:
                    ai_usage.features_used.append("reranking")
                    ai_usage.reranking = AIUsageDetail(**usage)
                    ai_usage.total_input_tokens += usage["input_tokens"]
                    ai_usage.total_output_tokens += usage["output_tokens"]

                # Reorder results based on AI ranking
                valid_indices = [
                    i for i in rerank_result["reranked_indices"]
                    if 0 <= i < len(results)
                ]
                results = [results[i] for i in valid_indices] if valid_indices else results[:top_k]
            except Exception as e:
                logger.warning(f"Reranking failed, using original order: {e}")
                results = results[:top_k]
        else:
            results = results[:top_k]

        # Step 4: Optionally synthesize an answer from results
        if ai_active and ai_options.synthesize and (
            len(results) > 0 or collection_overview or structured_context
        ):
            try:
                synth_input = [
                    {
                        "filename": r.filename,
                        "page_number": r.page_number,
                        "text_snippet": r.text_snippet,
                    }
                    for r in results
                ]
                synth_result = ai_service.synthesize_results(
                    query,
                    synth_input,
                    collection_overview=collection_overview,
                    structured_context=structured_context,
                )
                synthesis = synth_result["synthesis"]
                usage = synth_result["usage"]

                if usage:
                    ai_usage.features_used.append("synthesis")
                    ai_usage.synthesis = AIUsageDetail(**usage)
                    ai_usage.total_input_tokens += usage["input_tokens"]
                    ai_usage.total_output_tokens += usage["output_tokens"]
            except Exception as e:
                logger.warning(f"Synthesis failed: {e}")

        logger.info(f"Found {len(results)} results")

        return {
            "results": results,
            "synthesis": synthesis,
            "ai_usage": ai_usage,
        }

    def list_documents_page(self, limit: int, offset: int = 0, q: str = "",
                            keys: Optional[List[str]] = None) -> List[dict]:
        """One page of documents (newest first, optional filename filter)."""
        return self.vector_store.metadata_store.list_documents_page(limit, offset=offset, q=q, keys=keys)

    def count_documents(self, q: str = "", keys: Optional[List[str]] = None) -> int:
        """Total documents, honoring the same filename filter as the paged list."""
        return self.vector_store.metadata_store.count_documents(q=q, keys=keys)

    def get_document_stats(self) -> dict:
        """SQL-aggregated totals: {total_documents, total_pages}."""
        return self.vector_store.metadata_store.get_document_stats()

    def list_documents(self) -> List[dict]:
        """
        List all indexed documents.

        Returns:
            List of document metadata dictionaries
        """
        return self.vector_store.list_documents()

    def delete_document(self, document_id: str) -> int:
        """
        Delete a document from the index.

        Args:
            document_id: Document ID to delete

        Returns:
            Number of chunks deleted
        """
        logger.info(f"Deleting document: {document_id}")
        num_deleted = self.vector_store.delete_document(document_id)
        logger.info(f"Deleted {num_deleted} chunks")
        return num_deleted

    def save_index(self):
        """Persist the vector store to disk."""
        self.vector_store.save()

    def _apply_entity_graph(
        self,
        query: str,
        results: list,
        fetch_k: int,
        mode: "SearchMode",
        query_embedding,
        allowed_chunk_ids,
    ) -> list:
        """Entity-graph retrieval augmentation (services/entity_graph.py).

        Two effects on the candidate pool:

        1. Surface: chunks sharing at least one query entity but absent from
           the retrieval results join as candidates — scored by their vector
           similarity when an embedding is available, otherwise at 0 so the
           downstream reranker still sees them. This is what finds the
           cross-document chunk that names "Alice" and "Project Titan"
           without resembling the question.
        2. Boost: chunks sharing >= 2 distinct query entities get a
           saturating score bonus weighted by entity rarity, applied to
           chunks already in the results too.

        No-op when the query names no entity the corpus knows, when the
        entity table hasn't been backfilled yet, or when any step throws —
        the failure mode is always "behave like plain search".
        """
        try:
            from collections import Counter
            from config import settings as _settings
            from services.entity_graph import (
                corpus_filter, extract_entities, graph_boost, related_entities,
            )

            metadata_store = self.vector_store.metadata_store
            query_entities = extract_entities(query, max_per_chunk=10)
            if not query_entities:
                return results

            # Restrict to entities the corpus actually links (>1 chunk, not
            # ubiquitous). Substring-aware: "alice" in the query stands for
            # "alice johnson" in the corpus when the shorter mention is the
            # natural way of asking about the longer one.
            df = metadata_store.entity_document_frequency()
            if not df:
                return results
            total_chunks = max(metadata_store.get_total_chunks(), 1)
            eligible = corpus_filter(Counter(df), total_chunks)
            resolved = related_entities(
                {e: df[e] for e in eligible},
                max_doc_frequency=0.3,
                query_entities=query_entities,
            )
            usable = set(resolved.values())
            if not usable:
                return results

            # Rare entities carry more signal: rarity = 1 - df/max_df.
            max_df = max(df[e] for e in df)
            rarity = {e: 1.0 - (df[e] / max_df) for e in usable}

            graph_hits = metadata_store.find_chunks_by_entities(
                list(usable),
                allowed_chunk_ids=allowed_chunk_ids,
                min_shared=1,
                limit=_settings.entity_boost_max_candidates * 4,
            )
            if not graph_hits:
                return results

            by_chunk = {r.chunk_id: r for r in results}

            # Effect 2 first: boost chunks already ranked.
            for chunk_id, shared in graph_hits.items():
                if len(shared) >= 2 and chunk_id in by_chunk:
                    by_chunk[chunk_id].similarity_score += graph_boost(
                        shared, rarity, weight=_settings.entity_boost_weight)

            # Effect 1: bring in graph-only chunks as candidates.
            missing = [
                cid for cid in graph_hits
                if cid not in by_chunk
            ][:_settings.entity_boost_max_candidates]
            if missing:
                chunk_lookup = metadata_store.get_chunks_by_chunk_ids(missing)
                doc_infos = metadata_store.get_documents_info(
                    {c["document_id"] for c in chunk_lookup.values()})
                from services.vector_store import VectorStore
                for cid in missing:
                    chunk = chunk_lookup.get(cid)
                    if not chunk:
                        continue
                    base = 0.0
                    if query_embedding is not None:
                        # Score by vector similarity using the stored vector.
                        base = self._chunk_vector_score(
                            query_embedding, cid)
                    result = VectorStore._build_search_result(
                        chunk, base, doc_infos)
                    shared = graph_hits[cid]
                    if len(shared) >= 2:
                        result.similarity_score += graph_boost(
                            shared, rarity,
                            weight=_settings.entity_boost_weight)
                    by_chunk[cid] = result

            merged = sorted(
                by_chunk.values(),
                key=lambda r: r.similarity_score,
                reverse=True,
            )
            return merged[:fetch_k]
        except Exception as exc:
            logger.warning(f"Entity-graph boost skipped: {exc}")
            return results

    def _chunk_vector_score(self, query_embedding, chunk_id: str) -> float:
        """Cosine similarity between the query vector and one indexed chunk,
        read back out of the FAISS id-mapped index. 0.0 when the chunk has
        no vector (e.g. tabular rows indexed without embeddings)."""
        try:
            import numpy as np
            id_by_chunk = self.vector_store.metadata_store.get_ids_for_chunk_ids(
                [chunk_id])
            row_id = id_by_chunk.get(chunk_id)
            if row_id is None:
                return 0.0
            with self.vector_store._index_lock:
                vec = self.vector_store.index.reconstruct(int(row_id))
            q = query_embedding.reshape(-1).astype(np.float32)
            q = q / np.linalg.norm(q)
            v = vec.reshape(-1).astype(np.float32)
            n = np.linalg.norm(v)
            if n == 0:
                return 0.0
            return float(np.dot(q, v / n))
        except Exception:
            return 0.0

    def _generate_document_id(self, document_path: Path) -> str:
        """
        Generate a unique document ID based on file content.

        Args:
            document_path: Path to the document file

        Returns:
            Document ID (first 16 hex chars of the file's SHA256)
        """
        return content_hash_of(document_path)[:16]

    def _bm25_to_search_results(self, bm25_results: list) -> list:
        """
        Convert BM25 results (chunk_id, score) to SearchResult objects.

        Args:
            bm25_results: List of (chunk_id, score) tuples from BM25 search

        Returns:
            List of SearchResult objects
        """
        from models.schemas import SearchResult

        if not bm25_results:
            return []

        # Normalize scores to 0-1 range
        max_score = max(score for _, score in bm25_results) if bm25_results else 1
        if max_score == 0:
            max_score = 1

        # Batch-fetch only the matched chunks (indexed lookup) and their docs
        metadata_store = self.vector_store.metadata_store
        chunk_lookup = metadata_store.get_chunks_by_chunk_ids(
            [chunk_id for chunk_id, _ in bm25_results]
        )
        doc_infos = metadata_store.get_documents_info(
            {chunk["document_id"] for chunk in chunk_lookup.values()}
        )

        results = []
        for chunk_id, score in bm25_results:
            chunk = chunk_lookup.get(chunk_id)
            if not chunk:
                continue
            results.append(
                self.vector_store._build_search_result(
                    chunk, score / max_score, doc_infos  # Normalize to 0-1
                )
            )

        return results
