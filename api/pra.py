"""PRA model endpoints: fault trees, event trees, cut sets and importance
over the probabilistic risk assessment models indexed in a collection.

Every route reads: collection access runs inside get_indexer (404 when the
collection is hidden), the document must exist and be servable (a held or
quarantined one is 404 to all but a reviewer), and it must be a PRA document
(``source_format`` starting "pra-"). The shapes are built in
services/pra/queries.py, which the MCP tools share. Routes are plain ``def``
so FastAPI runs them in the threadpool; cut set generation is bounded by the
time budget in queries.COMPUTE_TIME_BUDGET_SECONDS.

Every response carries ``notice``: the numbers are a screening estimate from
the cut sets, and the PRA tool's own quantification governs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, status

from api.deps import get_indexer
from services import governance
from services.pra import analysis, queries
from services.pra.model import PraModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pra", tags=["pra"])

MAX_LIMIT = 1000
MAX_DEPTH = 10

_Truncation = Query(analysis.DEFAULT_TRUNCATION, ge=0.0, le=1.0,
                    description="compute=true only: absolute probability cutoff for generated cut sets")
_MaxOrder = Query(None, ge=1, le=50, description="compute=true only: largest cut set order to generate")
_SuccessTerms = Query(analysis.SUCCESS_TERMS_DROP,
                      description="compute=true only: delete success terms '/X' (drop) or keep them")


def _http(e: queries.PraQueryError) -> HTTPException:
    headers = {"Retry-After": str(e.retry_after)} if isinstance(e, queries.PraBusy) else None
    return HTTPException(status_code=e.status_code, detail=str(e), headers=headers)


def _indexer(collection_id: str):
    try:
        return get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _document(collection_id: str, document_id: str) -> Tuple[Dict[str, Any], PraModel]:
    indexer = _indexer(collection_id)
    doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)
    if not doc_info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} not found")
    governance.assert_document_servable(doc_info)
    if not queries.is_pra_document(doc_info):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Document {document_id} is not a PRA model")
    try:
        return doc_info, queries.model_for(indexer, doc_info)
    except queries.PraQueryError as e:
        raise _http(e)


def _head(collection_id: str, doc_info: Dict[str, Any]) -> Dict[str, Any]:
    return {"collection_id": collection_id, "document_id": doc_info.get("document_id"),
            "filename": doc_info.get("filename")}


@router.get("/models", summary="PRA models in a collection")
def list_models(collection_id: str = "default"):
    """The collection's PRA documents (fault tree and event tree models), each
    with its counts and the reader's warnings."""
    indexer = _indexer(collection_id)
    docs = governance.visible_documents(indexer.vector_store.metadata_store.list_documents())
    return {"collection_id": collection_id, "models": queries.list_models(indexer, docs),
            "notice": queries.NOTICE}


@router.get("/models/{document_id}", summary="One PRA model's contents")
def get_model(document_id: str, collection_id: str = "default"):
    """Fault trees, event trees, end states and stored cut set lists."""
    doc_info, model = _document(collection_id, document_id)
    return {"collection_id": collection_id, **queries.model_summary(doc_info, model)}


@router.get("/models/{document_id}/fault-trees/{tree:path}", summary="A fault tree's structure")
def get_fault_tree(document_id: str, tree: str, collection_id: str = "default",
                   depth: int = Query(3, ge=1, le=MAX_DEPTH)):
    """The tree as nested gates and events, ``depth`` gate levels deep; deeper
    gates carry ``more``, another tree's top is a ``transfer``."""
    doc_info, model = _document(collection_id, document_id)
    try:
        view = queries.fault_tree_view(model, tree, depth)
    except queries.PraQueryError as e:
        raise _http(e)
    return {**_head(collection_id, doc_info), **view, "notice": queries.NOTICE}


@router.get("/models/{document_id}/event-trees/{name:path}", summary="An event tree's sequences")
def get_event_tree(document_id: str, name: str, collection_id: str = "default"):
    doc_info, model = _document(collection_id, document_id)
    try:
        view = queries.event_tree_view(model, name)
    except queries.PraQueryError as e:
        raise _http(e)
    return {**_head(collection_id, doc_info), **view, "notice": queries.NOTICE}


@router.get("/models/{document_id}/events/{name:path}", summary="One event and where it is used")
def get_event(document_id: str, name: str, collection_id: str = "default"):
    """A basic, house or initiating event (or a gate): its data, the gates and
    sequences that use it, and how many stored cut sets contain it."""
    doc_info, model = _document(collection_id, document_id)
    try:
        view = queries.event_view(model, name)
    except queries.PraQueryError as e:
        raise _http(e)
    return {**_head(collection_id, doc_info), **view, "notice": queries.NOTICE}


@router.get("/models/{document_id}/cut-sets/{context:path}", summary="Cut sets, quantified")
def get_cut_sets(document_id: str, context: str, collection_id: str = "default",
                 limit: int = Query(50, ge=1, le=MAX_LIMIT), compute: bool = False,
                 truncation: float = _Truncation, max_order: Optional[int] = _MaxOrder,
                 success_terms: Literal["drop", "keep"] = _SuccessTerms):
    """The stored cut sets of a fault tree, sequence or end state, most
    probable first, each with its probability and share of the total, plus
    the rare-event and MCUB totals. With ``compute=true`` and no stored list,
    Clio generates them for a fault tree or gate (``source: computed``)."""
    doc_info, model = _document(collection_id, document_id)
    try:
        view = queries.cut_sets_view(model, context, limit=limit, compute=compute, truncation=truncation,
                                     max_order=max_order, success_terms=success_terms)
    except queries.PraQueryError as e:
        raise _http(e)
    return {**_head(collection_id, doc_info), **view}


@router.get("/models/{document_id}/importance/{context:path}", summary="Importance measures")
def get_importance(document_id: str, context: str, collection_id: str = "default",
                   limit: int = Query(50, ge=1, le=MAX_LIMIT),
                   sort: Literal["fv", "raw", "rrw", "birnbaum"] = "fv", compute: bool = False,
                   truncation: float = _Truncation, max_order: Optional[int] = _MaxOrder,
                   success_terms: Literal["drop", "keep"] = _SuccessTerms):
    """Fussell-Vesely, RAW, RRW and Birnbaum for every event in the context's
    cut sets, evaluated on the minimal cut set upper bound."""
    doc_info, model = _document(collection_id, document_id)
    try:
        view = queries.importance_view(model, context, limit=limit, sort=sort, compute=compute,
                                       truncation=truncation, max_order=max_order, success_terms=success_terms)
    except queries.PraQueryError as e:
        raise _http(e)
    return {**_head(collection_id, doc_info), **view}
