"""The PRA model behind an indexed document, for the tools that work on it.

Indexing (DocumentIndexer._index_pra_document) saves the parsed model beside
the index; ``model_for_document`` returns it, and when the file is missing or
unreadable (a re-index, a restored backup, an imported bundle) parses the
document's source file again and saves the result.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from services.pra import store
from services.pra.model import PraModel

logger = logging.getLogger(__name__)

PRA_SOURCE_FORMATS = ("pra-mef", "pra-mard")


def is_pra_document_info(doc_info: Optional[dict]) -> bool:
    """Whether a documents row describes a PRA model."""
    return bool(doc_info) and (doc_info.get("source_format") or "").startswith("pra")


def _documents_dir(indexer: Any) -> Path:
    collection_id = getattr(indexer, "collection_id", None)
    if collection_id:
        from services.collection_service import collection_service

        return collection_service.get_documents_path(collection_id)
    # A collection keeps documents/ beside indexes/.
    return Path(indexer.vector_store.index_dir).parent / "documents"


def model_for_document(indexer: Any, document_id: str) -> Optional[PraModel]:
    """The document's PRA model, or None when it is not a PRA document (or is gone).

    Raises PraFormatError (a ValueError) when the source must be parsed again
    and no longer reads as a model.
    """
    index_dir = Path(indexer.vector_store.index_dir)
    try:
        model = store.load_model(index_dir, document_id)
    except ValueError:
        return None  # not a document id
    if model is not None:
        return model
    doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)
    if not is_pra_document_info(doc_info):
        return None
    from services import governance

    path = governance.document_file(_documents_dir(indexer), doc_info)
    if not path.is_file():
        logger.warning("PRA document %s has no stored model and its file %s is gone", document_id, path.name)
        return None
    model = indexer.document_extractor.read_pra_model(path)
    store.save_model(index_dir, document_id, model)
    return model
