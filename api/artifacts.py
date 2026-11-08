"""Source-grounded artifact endpoints: generate, list, open, delete, download.

Generation is a thin adapter over the chat tool loop: each artifact type is an
instruction message run through /api/chat, so grounding, citations,
structured-table tools, and the sources list are all reused rather than
reimplemented. The result is saved with the collection (services/artifact_store)
and produced on a background job (services/artifact_jobs), so it outlasts the
page that asked for it.

Reading needs read access. Generating and deleting change what everyone with
access to the collection sees, so they need contributor (write) access. An
artifact built from a source that is now quarantined or blocklisted, or that
was deleted while quarantined, looks absent to everyone but a reviewer.
"""

import logging
from types import SimpleNamespace
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

from api.chat import chat_with_documents
from api.deps import get_indexer, require_admin, require_collection_access
from api.research import _budget_or_429, _provider
from middleware.user_context import get_request_user
from models.schemas import (
    ArtifactDetail,
    ArtifactListResponse,
    ArtifactRequest,
    ArtifactSummary,
    ChatMessage,
    ChatRequest,
    ChatResponse,
)
from services import artifact_jobs, artifact_store, audit, governance
from services import artifacts as artifacts_service
from services.research import jobs as research_jobs
from services.research import library as research_library
from services.artifact_store import ArtifactStore
from services.collection_service import collection_service

logger = logging.getLogger(__name__)

router = APIRouter()

# The chat handler only reads the request for the host to build source URLs
# with, and saved citations do not keep URLs.
_NO_HOST_REQUEST = SimpleNamespace(base_url="")


def _indexer(collection_id: str):
    try:
        return get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _store(indexer, collection_id: str) -> ArtifactStore:
    return ArtifactStore(indexer.vector_store.metadata_db_path,
                         collection_service.get_artifacts_path(collection_id))


def _can_review() -> bool:
    try:
        require_admin("review held sources")
        return True
    except HTTPException:
        return False


class _Reader:
    """What one request needs to judge artifacts against the live documents."""

    def __init__(self, indexer):
        self.documents = {d["document_id"]: d for d in indexer.list_documents()}
        self.hidden_ids = {i for i, d in self.documents.items() if governance.is_hidden(d)}
        self.blocked_hashes = artifact_store.load_blocked_hashes()
        self._can_review = None

    def held(self, artifact: dict) -> bool:
        return artifact_store.is_withheld(artifact, self.hidden_ids, self.blocked_hashes, self.documents)

    def may_see(self, artifact: dict) -> bool:
        if not self.held(artifact):
            return True
        if self._can_review is None:
            self._can_review = _can_review()
        return self._can_review

    def summary_fields(self, artifact: dict) -> dict:
        fields = {name: artifact.get(name) for name in ArtifactSummary.model_fields if name in artifact}
        fields.update(artifact_jobs.effective_state(artifact))
        fields["has_file"] = bool(artifact.get("file_name"))
        fields["held"] = self.held(artifact)
        fields["source_changes"] = artifact_store.source_changes(artifact, self.documents)
        return fields

    def source_views(self, artifact: dict, collection_id: str, base_url: str) -> List[dict]:
        """Citations with URLs for this host; none for a source that cannot be opened."""
        views = []
        for source in artifact.get("sources") or []:
            document_id = source.get("document_id")
            url = pdf = ""
            if document_id in self.documents and document_id not in self.hidden_ids:
                pdf = f"{base_url}/documents/{document_id}/pdf?collection_id={collection_id}"
                url = f"{pdf}#page={source.get('page_number')}"
            views.append({**source, "pdf_url": pdf, "page_url": url})
        return views


def _visible_or_404(store: ArtifactStore, reader: _Reader, artifact_id: str) -> dict:
    artifact = store.get(artifact_id)
    if artifact is None or not reader.may_see(artifact):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return artifact


def _source_documents(selected: Optional[List[str]], sources: List[dict], documents: dict) -> List[dict]:
    """What the artifact was built from, as those documents stand now."""
    ids = dict.fromkeys([*(selected or []), *(s["document_id"] for s in sources)])
    recorded = []
    for document_id in ids:
        doc = documents.get(document_id) or {}
        cited = next((s for s in sources if s["document_id"] == document_id), {})
        recorded.append({"document_id": document_id,
                         "filename": doc.get("filename") or cited.get("filename") or "",
                         "content_hash": doc.get("content_hash")})
    return recorded


@router.get("/api/artifacts/types", tags=["artifacts"])
async def list_artifact_types_endpoint() -> dict:
    """List the available artifact types for the generate UI."""
    return {"types": artifacts_service.list_artifact_types()}


@router.post("/api/artifacts", response_model=ArtifactSummary, tags=["artifacts"],
             status_code=status.HTTP_202_ACCEPTED)
def generate_artifact(
    artifact_request: ArtifactRequest,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> dict:
    """Start generating a source-grounded artifact and save it to the collection.

    Returns at once with the queued artifact; read it back from
    `GET /api/artifacts/{id}` when its status is `done`.
    """
    indexer = _indexer(collection_id)
    require_collection_access(collection_id, get_request_user(), write=True)
    user = get_request_user()
    _budget_or_429(user)
    # Refuse a missing key here, rather than as a failed row a moment later.
    _, model = _provider(artifact_request.provider, x_ai_key, x_ollama_model, x_anthropic_model,
                         x_openai_model, x_ai_model, x_ai_base_url)

    reader = _Reader(indexer)
    selected = None
    if artifact_request.document_ids:
        selected = [i for i in dict.fromkeys(artifact_request.document_ids)
                    if i in reader.documents and i not in reader.hidden_ids]
        if not selected:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="None of those sources were found")

    chat_request = ChatRequest(
        messages=[ChatMessage(role="user", content=artifacts_service.build_artifact_message(
            artifact_request.artifact_type,
            focus=artifact_request.focus,
            custom_instructions=artifact_request.custom_instructions,
        ))],
        provider=artifact_request.provider,
        mode=artifact_request.mode,
        scope="current",
        rerank=False,
        # Asking again must generate again, not repeat a saved answer.
        use_cache=False,
        max_output_tokens=artifacts_service.output_token_limit(artifact_request.artifact_type),
        top_k=artifact_request.top_k,
        document_ids=selected,
    )

    def work() -> dict:
        try:
            response = chat_with_documents(
                chat_request, _NO_HOST_REQUEST, collection_id=collection_id,
                x_ai_key=x_ai_key, x_ollama_model=x_ollama_model, x_anthropic_model=x_anthropic_model,
                x_openai_model=x_openai_model, x_ai_model=x_ai_model, x_ai_base_url=x_ai_base_url,
            )
        except HTTPException as e:
            raise RuntimeError(str(e.detail)) from e
        if not isinstance(response, ChatResponse):
            raise RuntimeError("Your daily token budget ran out before this could start.")
        sources = [s.model_dump(exclude={"pdf_url", "page_url"}) for s in response.sources]
        documents = {d["document_id"]: d for d in indexer.list_documents()}
        content = artifacts_service.finish_content(artifact_request.artifact_type, response.message.content)
        return {"content": content, "sources": sources,
                "source_documents": _source_documents(selected, sources, documents)}

    try:
        artifact = artifact_jobs.start(
            _store(indexer, collection_id), work,
            artifact_type=artifact_request.artifact_type,
            title=artifacts_service.artifact_title(artifact_request.artifact_type, artifact_request.focus),
            focus=artifact_request.focus,
            custom_instructions=artifact_request.custom_instructions,
            document_ids=selected,
            provider=artifact_request.provider,
            model=model,
            created_by=user,
        )
    except artifact_jobs.JobQueueFull as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return reader.summary_fields(artifact)


@router.get("/api/artifacts", response_model=ArtifactListResponse, tags=["artifacts"],
            summary="The collection's saved artifacts, newest first")
def list_artifacts(collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    store = _store(indexer, collection_id)
    reader = _Reader(indexer)
    rows = store.list()
    return {
        "artifacts": [reader.summary_fields(row) for row in rows if reader.may_see(row)],
        "truncated": store.count() > len(rows),
    }


@router.get("/api/artifacts/{artifact_id}", response_model=ArtifactDetail, tags=["artifacts"],
            summary="One saved artifact, with its body and citations")
def get_artifact(artifact_id: str, request: Request, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    reader = _Reader(indexer)
    artifact = _visible_or_404(_store(indexer, collection_id), reader, artifact_id)
    return {
        **reader.summary_fields(artifact),
        "content": artifact.get("content") or "",
        "custom_instructions": artifact.get("custom_instructions"),
        "document_ids": artifact.get("document_ids"),
        "sources": reader.source_views(artifact, collection_id, str(request.base_url).rstrip("/")),
    }


@router.get("/api/artifacts/{artifact_id}/file", tags=["artifacts"],
            summary="Download an artifact's file (audio, for example)")
def download_artifact_file(artifact_id: str, collection_id: str = "default") -> FileResponse:
    indexer = _indexer(collection_id)
    store = _store(indexer, collection_id)
    artifact = _visible_or_404(store, _Reader(indexer), artifact_id)
    path = store.file_path(artifact)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This artifact has no file")
    return FileResponse(path, media_type=artifact.get("media_type") or "application/octet-stream",
                        filename=path.name)


@router.delete("/api/artifacts/{artifact_id}", tags=["artifacts"], summary="Delete a saved artifact")
def delete_artifact(artifact_id: str, collection_id: str = "default") -> dict:
    indexer = _indexer(collection_id)
    require_collection_access(collection_id, get_request_user(), write=True)
    store = _store(indexer, collection_id)
    _visible_or_404(store, _Reader(indexer), artifact_id)
    store.delete(artifact_id)
    return {"deleted": True}


def _reports_by_visibility(store: ArtifactStore, reader: "_Reader") -> tuple:
    """``(reports this caller may see, how many are held from them)``."""
    rows = store.list(limit=max(store.count(), 1))
    visible = [row for row in rows if reader.may_see(row)]
    return visible, len(rows) - len(visible)


@router.get("/api/collections/{collection_id}/outputs", tags=["artifacts"],
            summary="How much has been generated from this collection")
def get_collection_outputs(collection_id: str) -> dict:
    """Counts of generated reviews, screening verdicts and saved reports.

    Includes reviews of sources since deleted, which no list shows.
    """
    indexer = _indexer(collection_id)
    visible, held = _reports_by_visibility(_store(indexer, collection_id), _Reader(indexer))
    return {**research_library.research_store(indexer).output_counts(),
            "reports": len(visible), "reports_held": held}


@router.delete("/api/collections/{collection_id}/outputs", tags=["artifacts"],
               summary="Delete what was generated from this collection")
def delete_collection_outputs(collection_id: str, reviews: bool = False, reports: bool = False) -> dict:
    """Delete generated reviews and verdicts, saved reports, or both. Sources stay.

    A report held from this caller stays, under the same rule as deleting one:
    what they may not see, they may not delete. 409 while a research job is
    running, which would only write its results straight back.
    """
    if not (reviews or reports):
        raise HTTPException(status_code=422,
                            detail="Name what to delete: reviews, reports, or both")
    indexer = _indexer(collection_id)
    user = get_request_user()
    require_collection_access(collection_id, user, write=True)

    research = research_library.research_store(indexer)
    if reviews and any(job["status"] in ("queued", "running") for job in research_jobs.list_jobs(research)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A research job is running. Stop it or wait for it, then delete the reviews.")

    deleted = {"reviews": 0, "screenings": 0, "reports": 0, "reports_held": 0}
    if reviews:
        deleted.update(research.delete_outputs())
    if reports:
        store = _store(indexer, collection_id)
        visible, deleted["reports_held"] = _reports_by_visibility(store, _Reader(indexer))
        for artifact in visible:
            store.delete(artifact["id"])
        deleted["reports"] = len(visible)

    audit.record("collection.outputs_deleted", actor=user, collection_id=collection_id, detail=deleted)
    return deleted
