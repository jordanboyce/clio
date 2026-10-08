"""Pydantic schemas for API request/response models."""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    """Metadata for a single text chunk."""

    chunk_id: str = Field(..., description="Unique identifier for the chunk")
    document_id: str = Field(..., description="Parent document identifier")
    filename: str = Field(..., description="Original filename")
    page_number: int = Field(..., description="Page number or section number (1-indexed)")
    chunk_index: int = Field(..., description="Index of chunk within the page")
    text: str = Field(..., description="Chunk text content")

    # v3.0: Format-aware metadata
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="Extraction method: text, ocr, hybrid")

    # v3.0: CSV-specific metadata (for row-level indexing)
    csv_row_number: Optional[int] = Field(None, description="Original row number in CSV (for CSV chunks)")
    csv_columns: Optional[List[str]] = Field(None, description="Column names for CSV row")
    csv_values: Optional[Dict[str, Any]] = Field(None, description="Column-value pairs for CSV row")

    # v3.0: Code-specific metadata (for source code indexing)
    language: Optional[str] = Field(None, description="Programming language: pascal, delphi, modula2, assembly")
    unit_name: Optional[str] = Field(None, description="Unit/module name for code files")
    symbol_name: Optional[str] = Field(None, description="Function/procedure/class name")
    symbol_type: Optional[str] = Field(None, description="Symbol type: procedure, function, class, record, macro, label")
    line_start: Optional[int] = Field(None, description="Starting line number in source file")
    line_end: Optional[int] = Field(None, description="Ending line number in source file")
    parent_symbol: Optional[str] = Field(None, description="Parent class/module for nested symbols")


class DocumentMetadata(BaseModel):
    """Metadata for an indexed document."""

    document_id: str = Field(..., description="Unique identifier for the document")
    filename: str = Field(..., description="Original filename")
    total_pages: int = Field(..., description="Total number of pages")
    total_chunks: int = Field(..., description="Total number of chunks created")
    indexed_at: str = Field(default="", description="ISO timestamp of indexing")

    # v3.0: Document-level versioning and format info
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="Primary extraction method used")
    embedding_model: Optional[str] = Field(None, description="Embedding model used for this document")
    chunk_size: Optional[int] = Field(None, description="Chunk size used during indexing")
    chunk_overlap: Optional[int] = Field(None, description="Chunk overlap used during indexing")
    schema_version: str = Field(default="3.1", description="Schema version for migration compatibility")

    # v3.1: Local file reference support
    source_path: Optional[str] = Field(None, description="Original filesystem path for local references")
    source_type: str = Field(default="upload", description="Source type: 'upload', 'local_reference', or 'url' (source_path holds the link)")

    # v3.2: Prompt injection warnings (page_num -> scan result dict)
    injection_warnings: Optional[Dict[str, Any]] = Field(None, description="Flagged pages with injection scan details")

    # v3.3: Governance — attribution, labels, and the content-policy decision
    uploaded_by: Optional[str] = Field(None, description="Verified identity that added the document (None = unattributed)")
    content_hash: Optional[str] = Field(None, description="Full sha256 of the file (blocklist key)")
    sensitivity: Optional[str] = Field(None, description="Per-document sensitivity override; None inherits the collection label")
    sensitivity_effective: Optional[str] = Field(None, description="Label in force: the override, else the collection's")
    policy_status: str = Field(default="clear", description="clear | flagged | quarantined | approved")
    policy_flags: Optional[Dict[str, Any]] = Field(None, description="Content-policy scan summary when anything was found")

    # v3.4: storage accounting
    file_size: Optional[int] = Field(None, description="Bytes of the source file on disk (None when unknown)")


class SearchResult(BaseModel):
    """A single search result."""

    filename: str = Field(..., description="Document filename")
    page_number: int = Field(..., description="Page or section number (1-indexed)")
    text_snippet: str = Field(..., description="Matching text chunk")
    similarity_score: float = Field(..., description="Cosine similarity score (0-1)")
    document_id: str = Field(..., description="Document identifier")
    chunk_id: str = Field(..., description="Chunk identifier")
    pdf_url: str = Field(..., description="URL to download the document")
    page_url: str = Field(..., description="URL to view the specific page")

    # v3.0: Format-aware result metadata
    source_format: Optional[str] = Field(None, description="Source format: pdf, txt, docx, csv, md, json")
    extraction_method: Optional[str] = Field(None, description="How text was extracted: text, ocr, hybrid")

    # v3.3: Sensitivity label in force for the source document
    sensitivity: Optional[str] = Field(None, description="public | internal | confidential | restricted")

    # v3.0: CSV-specific result data (for table rendering)
    csv_row_number: Optional[int] = Field(None, description="Row number for CSV results")
    csv_columns: Optional[List[str]] = Field(None, description="Column names for CSV row")
    csv_values: Optional[Dict[str, Any]] = Field(None, description="Column-value pairs for CSV row")

    # v3.0: Code-specific result data (for code navigation)
    language: Optional[str] = Field(None, description="Programming language")
    unit_name: Optional[str] = Field(None, description="Unit/module name")
    symbol_name: Optional[str] = Field(None, description="Function/procedure/class name")
    symbol_type: Optional[str] = Field(None, description="Symbol type")
    line_start: Optional[int] = Field(None, description="Starting line number")
    line_end: Optional[int] = Field(None, description="Ending line number")

    # v3.1: Local file reference support
    source_type: Optional[str] = Field(None, description="Source type: 'upload' or 'local_reference'")
    source_path: Optional[str] = Field(None, description="Original filesystem path for local references")


class UploadResponse(BaseModel):
    """Response from document upload endpoint."""

    message: str = Field(..., description="Status message")
    documents_processed: int = Field(..., description="Number of documents processed")
    total_pages: int = Field(..., description="Total pages across all documents")
    total_chunks: int = Field(..., description="Total chunks created")
    document_ids: List[str] = Field(..., description="List of created document IDs")


class UploadPhase(str, Enum):
    """Upload processing phases for granular progress tracking."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadJobResponse(BaseModel):
    """Response from async upload job endpoint."""

    job_id: int = Field(..., description="Unique job identifier")
    collection_id: str = Field(..., description="Target collection ID")
    status: str = Field(..., description="Job status: pending, running, completed, failed")
    total_files: int = Field(..., description="Total files to process")
    processed_files: int = Field(..., description="Files processed so far")
    current_file: Optional[str] = Field(None, description="File currently being processed")
    progress_percent: float = Field(0.0, description="Progress percentage (0-100)")
    error: Optional[str] = Field(None, description="Error message if failed")
    result_summary: Optional[Dict[str, Any]] = Field(None, description="Results when completed")
    started_at: Optional[str] = Field(None, description="ISO timestamp when job started")
    completed_at: Optional[str] = Field(None, description="ISO timestamp when job completed")

    # v4.0: Granular progress tracking
    phase: Optional[str] = Field(None, description="Current processing phase")
    phase_progress: Optional[int] = Field(None, description="Progress within current phase (0-100)")
    phase_detail: Optional[str] = Field(None, description="Detailed phase status message")
    chunks_processed: Optional[int] = Field(None, description="Chunks processed in current file")
    chunks_total: Optional[int] = Field(None, description="Total chunks in current file")

    job_type: str = Field("upload", description="'upload' for browser uploads, 'index' for local/folder indexing")
    queue_position: Optional[int] = Field(
        None,
        description="1-based place in line while waiting for a slot; null once the job is running",
    )
    cancel_requested: bool = Field(
        False, description="True while a running job is stopping at the next file boundary"
    )
    skipped_files: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Files left out of the job at submission, with the reason for each",
    )


class AIOptions(BaseModel):
    """Optional AI enhancement settings for search."""

    provider: str = Field("anthropic", description="AI provider: 'anthropic', 'openai', or 'ollama'")
    rerank: bool = Field(False, description="Rerank results using AI for better relevance")
    synthesize: bool = Field(False, description="Generate an AI summary with citations")


class SearchMode(str, Enum):
    """Search mode options."""

    SEMANTIC = "semantic"  # Pure semantic/embedding search
    KEYWORD = "keyword"    # Pure BM25 keyword search
    HYBRID = "hybrid"      # Combined semantic + keyword


class SearchFilters(BaseModel):
    """Metadata pre-filter applied before ranking. All fields AND-combine."""

    document_ids: Optional[List[str]] = Field(None, description="Restrict to these document ids")
    filenames: Optional[List[str]] = Field(None, description="Restrict to these exact filenames")
    source_formats: Optional[List[str]] = Field(
        None, description="Restrict to these file types, e.g. ['pdf', 'csv']"
    )
    date_from: Optional[str] = Field(None, description="ISO timestamp lower bound on upload time")
    date_to: Optional[str] = Field(None, description="ISO timestamp upper bound on upload time")

    def to_dict(self) -> Optional[dict]:
        """Return a plain dict of set filters, or None if nothing is set."""
        data = {k: v for k, v in self.model_dump().items() if v}
        return data or None


class SearchRequest(BaseModel):
    """Request body for search endpoint."""

    query: str = Field(..., description="Search query text", min_length=1)
    top_k: int = Field(10, description="Number of results to return", ge=1, le=50)
    mode: SearchMode = Field(SearchMode.SEMANTIC, description="Search mode: semantic, keyword, or hybrid")
    semantic_weight: float = Field(
        0.7,
        description="Weight for semantic search in hybrid mode (0-1). Higher = more semantic.",
        ge=0.0,
        le=1.0
    )
    filters: Optional[SearchFilters] = Field(None, description="Optional metadata pre-filter")
    ai: Optional[AIOptions] = Field(None, description="Optional AI enhancement settings")


class AIUsageDetail(BaseModel):
    """Token usage for a single AI operation."""

    input_tokens: int = Field(..., description="Input tokens consumed")
    output_tokens: int = Field(..., description="Output tokens consumed")
    model: str = Field(..., description="Model used")


class AIUsage(BaseModel):
    """AI usage metadata for cost transparency."""

    features_used: List[str] = Field(default_factory=list, description="AI features that were applied")
    reranking: Optional[AIUsageDetail] = None
    synthesis: Optional[AIUsageDetail] = None
    total_input_tokens: int = Field(0, description="Total input tokens across all AI calls")
    total_output_tokens: int = Field(0, description="Total output tokens across all AI calls")


class SearchResponse(BaseModel):
    """Response from search endpoint."""

    query: str = Field(..., description="Original search query")
    results: List[SearchResult] = Field(..., description="Ranked search results")
    total_results: int = Field(..., description="Total number of results returned")
    synthesis: Optional[str] = Field(None, description="AI-generated answer with citations")
    ai_usage: Optional[AIUsage] = Field(None, description="AI token usage for cost transparency")


class DocumentListResponse(BaseModel):
    """Response from document list endpoint."""

    documents: List[DocumentMetadata] = Field(..., description="List of indexed documents")
    total_documents: int = Field(..., description="Total number of documents")
    kind_counts: Optional[Dict[str, int]] = Field(
        None, description="Documents per kind (code, docs, data, media, other) for the unfiltered-by-kind set"
    )


class DocumentChunkView(BaseModel):
    """Chunk payload for document chunk inspection."""

    chunk_id: str = Field(..., description="Unique identifier for the chunk")
    page_number: int = Field(..., description="Page or section number")
    chunk_index: int = Field(..., description="Chunk index within page")
    text: str = Field(..., description="Chunk text")
    source_format: Optional[str] = Field(None, description="Source format")
    extraction_method: Optional[str] = Field(None, description="Extraction method")
    # Code chunks: which symbol the passage is, and where it sits in the file
    language: Optional[str] = Field(None, description="Programming language for code chunks")
    symbol_name: Optional[str] = Field(None, description="Function/class/procedure name for code chunks")
    symbol_type: Optional[str] = Field(None, description="Symbol kind: function, class, method, code_block, ...")
    line_start: Optional[int] = Field(None, description="First source line of the chunk (1-based)")
    line_end: Optional[int] = Field(None, description="Last source line of the chunk (1-based)")
    extracted_fields: Optional[Dict[str, str]] = Field(
        None,
        description="Heuristically extracted field/value pairs from chunk text"
    )
    form_score: Optional[float] = Field(
        None,
        description="Estimated form-likeness score (0-1) used to decide field extraction"
    )
    form_like: Optional[bool] = Field(
        None,
        description="Whether chunk text was classified as form-like"
    )
    field_extraction_applied: Optional[bool] = Field(
        None,
        description="Whether field extraction was applied to this chunk"
    )


class DocumentChunksResponse(BaseModel):
    """Response for listing chunks in a single document."""

    document_id: str = Field(..., description="Document identifier")
    filename: str = Field(..., description="Original filename")
    extraction_method: Optional[str] = Field(None, description="Document extraction method")
    total_chunks: int = Field(..., description="Total chunks stored for this document")
    returned_chunks: int = Field(..., description="Chunks returned in this response")
    chunks: List[DocumentChunkView] = Field(..., description="Chunk records")


# Chat schemas
class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatSource(BaseModel):
    """A document chunk retrieved as context for a chat response."""

    filename: str = Field(..., description="Source document filename")
    page_number: int = Field(..., description="Page or section number")
    text_snippet: str = Field(..., description="Relevant text excerpt")
    similarity_score: float = Field(..., description="Similarity score (0-1)")
    document_id: str = Field(..., description="Document identifier")
    pdf_url: str = Field(..., description="URL to download the document")
    page_url: str = Field(..., description="URL to view the specific page")
    sensitivity: Optional[str] = Field(None, description="Sensitivity label in force for the source")


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    messages: List[ChatMessage] = Field(..., description="Conversation history including the latest user message")
    provider: str = Field("anthropic", description="AI provider: 'anthropic', 'openai', or 'ollama'")
    mode: SearchMode = Field(SearchMode.SEMANTIC, description="Search mode for context retrieval")
    scope: str = Field("current", description="Collection scope: 'current' (single collection) or 'all' (search across all collections)")
    rerank: bool = Field(False, description="Rerank retrieved context chunks using AI before generating a response")
    top_k: int = Field(5, description="Number of source chunks to consider", ge=1, le=20)
    use_cache: bool = Field(
        True,
        description="Serve a semantically matching cached answer when one exists; "
                    "false forces a fresh response (and replaces the cached one)",
    )
    cache_threshold: Optional[float] = Field(
        None, ge=0.5, le=1.0,
        description="How similar (cosine, local embeddings) this question must be to an "
                    "already-answered one for its cached answer to be reused. 1.0 reuses "
                    "only the identical question. Omitted = the deployment default "
                    "(ANSWER_CACHE_THRESHOLD). Questions that differ in numbers or "
                    "negation are never treated as the same.",
    )
    document_ids: Optional[List[str]] = Field(
        None,
        description="Limit this conversation to these sources (document ids in the current "
                    "collection). Retrieval, tool calls, table queries, the overview and "
                    "citations all stay inside the selection. Omitted = every source in scope.",
    )
    depth: Literal["quick", "research"] = Field(
        "research",
        description="'quick' answers in one pass from the retrieved context (large tables "
                    "stay queryable); 'research' lets the assistant run document searches "
                    "and verification tools before answering.",
    )
    related: bool = Field(
        True,
        description="Suggest follow-up questions after the answer (one extra fast-model call).",
    )


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    message: ChatMessage = Field(..., description="Assistant's response message")
    sources: List[ChatSource] = Field(default_factory=list, description="Document chunks used as context")
    ai_usage: Optional[AIUsage] = Field(None, description="AI token usage")
    structured_results: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Structured query / metric tool-call results executed during this turn"
    )
    cached: bool = Field(False, description="True when this answer was served from the semantic answer cache")
    cached_question: Optional[str] = Field(
        None, description="The originally asked question the cached answer was generated for"
    )
    cached_similarity: Optional[float] = Field(
        None, description="Similarity between this question and the cached one (1.0 = identical)"
    )
    related_questions: List[str] = Field(
        default_factory=list, description="Suggested follow-up questions for this answer"
    )
    depth: str = Field("research", description="The answer depth this turn ran at")


class StarterQuestionsRequest(BaseModel):
    """Request body for suggested opening questions about a collection."""

    provider: str = Field("anthropic", description="AI provider used to write the questions")
    document_ids: Optional[List[str]] = Field(
        None, description="Limit the suggestions to these sources (document ids in the collection)"
    )


class StarterQuestionsResponse(BaseModel):
    """Suggested opening questions for a collection."""

    questions: List[str] = Field(default_factory=list)
    cached: bool = Field(False, description="True when served from the per-corpus-version cache")


class ArtifactRequest(BaseModel):
    """Request body for generating a collection artifact."""

    artifact_type: str = Field(..., description="Artifact type, e.g. 'summary', 'faq', 'timeline', 'briefing', 'study_guide'")
    provider: str = Field("anthropic", description="AI provider")
    scope: str = Field("current", description="'current' (this collection) or 'all'")
    mode: SearchMode = Field(SearchMode.HYBRID, description="Search mode for context retrieval")
    top_k: int = Field(8, description="Chunks per retrieval call during generation", ge=1, le=20)
    focus: Optional[str] = Field(None, description="Optional subject to narrow the artifact to")
    custom_instructions: Optional[str] = Field(None, description="Optional extra freeform guidance")
    document_ids: Optional[List[str]] = Field(
        None, description="Limit the artifact to these sources (document ids in the current collection)"
    )


class ArtifactResponse(BaseModel):
    """A generated, source-grounded artifact."""

    artifact_type: str = Field(..., description="The artifact type that was generated")
    title: str = Field(..., description="Human-readable title")
    content: str = Field(..., description="The artifact body as Markdown, with inline citations")
    sources: List[ChatSource] = Field(default_factory=list, description="Sources cited/used")
    ai_usage: Optional[AIUsage] = Field(None, description="AI token usage")


# Repository/Folder upload schemas
class RepoUploadRequest(BaseModel):
    """Request for uploading a code repository or folder."""

    path: str = Field(..., description="Local filesystem path to the repository or folder")
    collection_id: str = Field("default", description="Collection to add files to")
    recursive: bool = Field(True, description="Recursively scan subdirectories")
    include_patterns: Optional[List[str]] = Field(
        None,
        description="Glob patterns to include (e.g., ['*.pas', '*.dpr']). If not set, uses all supported extensions."
    )
    exclude_patterns: Optional[List[str]] = Field(
        default_factory=lambda: ["**/node_modules/**", "**/.git/**", "**/build/**", "**/dist/**", "**/__pycache__/**"],
        description="Glob patterns to exclude"
    )


class RepoUploadResponse(BaseModel):
    """Response from repository upload endpoint."""

    message: str = Field(..., description="Status message")
    status: str = Field(
        "queued",
        description="'queued' when a job was started; 'up_to_date' when every file was already indexed",
    )
    job_id: Optional[int] = Field(None, description="Background job ID for async processing")
    files_found: int = Field(0, description="Total files found matching patterns")
    skipped_unchanged: int = Field(
        0, description="Files left out because their exact bytes are already in the collection"
    )
    skipped_unsupported: int = Field(0, description="Files skipped because their type cannot be indexed")
    files_indexed: int = Field(0, description="Files successfully indexed")
    files_failed: int = Field(0, description="Files that failed to index")
    total_chunks: int = Field(0, description="Total chunks created")
    document_ids: List[str] = Field(default_factory=list, description="List of created document IDs")
    failed_files: List[Dict[str, str]] = Field(default_factory=list, description="List of files that failed with error messages")


# ── Expertise Library schemas ─────────────────────────────────────────────────

class ExpertisePack(BaseModel):
    """A named, reusable block of advisor guidance injected into collection chat prompts."""

    id: str = Field(..., description="UUID primary key")
    name: str = Field(..., description="Short human-readable name for the pack")
    description: Optional[str] = Field(None, description="One-line summary shown in the library list")
    body: str = Field(..., description="Markdown body — the full guidance text injected into the LLM prompt")
    created_at: datetime = Field(..., description="UTC creation timestamp")
    updated_at: datetime = Field(..., description="UTC last-update timestamp")


class ExpertisePackCreate(BaseModel):
    """Request body for creating a new expertise pack."""

    name: str = Field(..., min_length=1, description="Pack name")
    description: Optional[str] = Field(None, description="Optional one-line summary")
    body: str = Field(..., min_length=1, description="Markdown guidance body")


class ExpertisePackUpdate(BaseModel):
    """Partial-update request body for an expertise pack (all fields optional)."""

    name: Optional[str] = Field(None, description="New name")
    description: Optional[str] = Field(None, description="New description")
    body: Optional[str] = Field(None, description="New markdown body")


class CollectionExpertiseResponse(BaseModel):
    """Response listing packs attached to a collection."""

    collection_id: str
    packs: List[ExpertisePack]


class SetCollectionExpertiseRequest(BaseModel):
    """Request body for replacing a collection's attached pack list."""

    pack_ids: List[str] = Field(..., description="Full set of pack IDs to attach (replaces existing)")


class SyncFolderRequest(BaseModel):
    """Request for bringing a collection up to date with a folder on disk."""

    path: str = Field(..., description="Local filesystem path to the folder")
    collection_id: str = Field("default", description="Collection to sync into")
    recursive: bool = Field(True, description="Recursively scan subdirectories")
    file_extensions: Optional[List[str]] = Field(
        None, description="Extensions to include (e.g. ['.py', '.md']). All files when unset."
    )
    exclude_patterns: Optional[List[str]] = Field(
        None, description="Glob patterns to exclude, on top of the built-in defaults"
    )
    prune_missing: bool = Field(
        False,
        description="Remove documents this folder was synced from whose file no longer exists",
    )


class SyncFolderResponse(BaseModel):
    """Outcome of a folder sync. Only new and changed files are queued."""

    status: str = Field(..., description="'queued' when a job was started, 'up_to_date' otherwise")
    job_id: Optional[int] = Field(None, description="Background job ID, when something was queued")
    path: str = Field(..., description="The folder that was synced (resolved)")
    files_found: int = Field(0, description="Files matched in the folder")
    queued: int = Field(0, description="New or changed files handed to the indexing job")
    skipped_unchanged: int = Field(0, description="Files whose bytes were already indexed")
    skipped_unsupported: int = Field(0, description="Files the scan left out because Clio cannot index their type")
    replaced: List[str] = Field(default_factory=list, description="Documents removed because their file changed")
    replaced_count: int = Field(0)
    pruned: List[str] = Field(default_factory=list, description="Documents removed because their file is gone")
    pruned_count: int = Field(0)
    last_synced_at: Optional[str] = Field(None, description="ISO timestamp of this sync")
    message: str = Field("", description="Status message")


class SyncFolderRecord(BaseModel):
    """A folder remembered for a collection, with the counts of its last sync."""

    path: str
    last_synced_at: Optional[str] = None
    job_id: Optional[int] = None
    files_found: int = 0
    queued: int = 0
    skipped_unchanged: int = 0
    replaced_count: int = 0
    pruned_count: int = 0
    recursive: bool = True
    file_extensions: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    prune_missing: bool = False
    exists: bool = Field(True, description="Whether the folder is still present on disk")


class SyncFoldersResponse(BaseModel):
    collection_id: str
    folders: List[SyncFolderRecord] = Field(default_factory=list)
