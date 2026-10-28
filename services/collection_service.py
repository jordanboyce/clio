"""Collection service for managing document collections.

Each collection has:
- Its own directory for documents
- Its own FAISS index
- Its own settings (chunk size, overlap, etc.)
- An owner (user_id) for multi-user isolation
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from services import authored_text
from services.app_database import app_db
from config import settings

logger = logging.getLogger(__name__)


class CollectionService:
    """Manages document collections and their associated data."""

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or Path(settings.data_dir) / "collections"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_collection_dirs("default")

    def _get_collection_dir(self, collection_id: str) -> Path:
        return self.base_dir / collection_id

    def _get_documents_dir(self, collection_id: str) -> Path:
        return self._get_collection_dir(collection_id) / "documents"

    def _get_indexes_dir(self, collection_id: str) -> Path:
        return self._get_collection_dir(collection_id) / "indexes"

    def _ensure_collection_dirs(self, collection_id: str):
        self._get_documents_dir(collection_id).mkdir(parents=True, exist_ok=True)
        self._get_indexes_dir(collection_id).mkdir(parents=True, exist_ok=True)

    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
    ) -> Dict[str, Any]:
        """Create a new collection.

        Args:
            name: Collection name
            description: Collection description
            color: Hex color for UI
            chunk_size: Chunk size for text splitting
            chunk_overlap: Overlap between chunks
            embedding_model: Embedding model to use
            owner_id: User who owns this collection

        Returns:
            Collection details
        """
        collection_id = app_db.create_collection(
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
            owner_id=owner_id,
            mcp_display_name=None,
            mcp_display_description=None,
        )
        self._ensure_collection_dirs(collection_id)
        logger.info(f"Created collection '{name}' with ID {collection_id} owner={owner_id}")
        return app_db.get_collection(collection_id)

    @staticmethod
    def _decode_guide_flag(collection: Dict[str, Any]) -> Dict[str, Any]:
        """Turn the stored scan verdict into the flag that applies to the guide.

        A guide saved before scanning existed has no verdict (NULL), so it is
        scanned here: old text is not a blind spot. ``guide_flag`` is a dict
        (``level``, ``categories``, ``reason``) or None.
        """
        stored = collection.get("guide_flag")
        collection["guide_flag"] = authored_text.effective_flag(
            collection.get("guide"), authored_text.load_flag(stored), scanned=stored is not None,
        )
        return collection

    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        row = app_db.get_collection(collection_id)
        return self._decode_guide_flag(row) if row else None

    def source_file_paths(self, collection_id: str) -> List[Path]:
        """Every source file behind a collection, as paths on disk.

        Uploaded and agent-written sources live in the collection's documents
        directory; local references live wherever they were indexed from. Same
        two cases reindex_service walks, resolved here because cloning needs
        the file list and not the index.
        """
        from services.indexer_manager import indexer_manager

        documents_dir = indexer_manager.get_documents_path(collection_id)
        paths: List[Path] = []
        seen: set = set()
        indexer = indexer_manager.get_indexer(collection_id)
        for doc in indexer.vector_store.metadata_store.list_documents():
            source_path = doc.get("source_path")
            if (doc.get("source_type") or "upload") == "local_reference" and source_path:
                path = Path(source_path)
            else:
                path = documents_dir / (doc.get("filename") or "")
            key = str(path)
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            paths.append(path)
        return paths

    def clone_collection(
        self,
        source_id: str,
        owner_id: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Copy a collection's sources into a new collection owned by ``owner_id``.

        A clone is how someone works with a published collection on their own
        terms: they get their own copy, with the build settings inherited, and
        can reindex or rechunk it without touching the original. The copy is
        never published — releasing it is the new owner's decision.

        Returns the new collection with a ``job_id`` for the indexing job.
        Raises StorageLimitExceeded if the sources do not fit the caller's cap,
        and ValueError if the source has nothing to copy.
        """
        from services import storage_quota
        from services.upload_service import upload_service

        source = app_db.get_collection(source_id)
        if not source:
            raise ValueError(f"Collection '{source_id}' not found")

        paths = self.source_file_paths(source_id)
        if not paths:
            raise ValueError("This collection has no sources to copy.")

        clone = self.create_collection(
            name=name or f"Copy of {source.get('name') or source_id}",
            description=source.get("description") or "",
            color=source.get("color") or "#3b82f6",
            chunk_size=source.get("chunk_size") or 500,
            chunk_overlap=source.get("chunk_overlap") or 50,
            embedding_model=source.get("embedding_model"),
            owner_id=owner_id,
        )

        # The bulk job reserves per file, which would leave a half-copied
        # collection behind when the cap bites mid-run. Check the whole
        # transfer up front so an oversized clone is refused before any
        # bytes move, and drop the empty collection if it is.
        total = sum(p.stat().st_size for p in paths)
        try:
            storage_quota.check(clone["id"], total)
        except Exception:
            self.delete_collection(clone["id"])
            raise

        # The guide is part of how the collection answers, so it travels with
        # the copy; the governance label does too, because a clone holds the
        # same material as its source.
        if source.get("guide") or source.get("sensitivity"):
            self.update_collection(
                collection_id=clone["id"],
                guide=source.get("guide"),
                sensitivity=source.get("sensitivity"),
            )

        job_id = upload_service.start_local_index(
            file_paths=[str(p) for p in paths],
            collection_id=clone["id"],
            copy_to_library=True,
            uploaded_by=owner_id,
        )

        clone = app_db.get_collection(clone["id"])
        clone["job_id"] = job_id
        clone["cloned_from"] = source_id
        clone["permission"] = "owner"
        logger.info(
            f"Cloned collection {source_id} -> {clone['id']} "
            f"({len(paths)} sources, job {job_id}) owner={owner_id}"
        )
        return clone

    def get_all_collections(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all collections visible to a user.

        With private collections off: returns all (shared appliance).

        With private collections on, returns team collections (owner empty or
        "default"), the user's own, and collections shared with them. When
        user_id is omitted, the identity bound to the current request is used,
        so callers with no user in reach — chat's scope=all fan-out, the /mcp
        tools — are scoped automatically. Anonymous callers see team
        collections only.
        """
        return [self._decode_guide_flag(c) for c in self._visible_collections(user_id)]

    def _visible_collections(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not settings.private_collections:
            return app_db.get_all_collections()

        if user_id is None:
            from middleware.user_context import get_request_user
            user_id = get_request_user()

        visible: List[Dict[str, Any]] = []
        seen: set = set()
        for c in app_db.get_all_collections():
            owner = (c.get('owner_id') or '').strip()
            if owner in ('', settings.default_user_id):
                c['permission'] = 'owner'
                c['team'] = True
            elif user_id and owner == user_id:
                c['permission'] = 'owner'
            elif c.get('published'):
                # Reviewed and released: everyone may read it and reach it
                # over MCP, nobody but its owner may change how it is built.
                c['permission'] = 'read'
            else:
                continue
            visible.append(c)
            seen.add(c['id'])

        if user_id:
            for c in app_db.get_shared_collections(user_id):
                if c['id'] not in seen:
                    c['permission'] = c.get('permission', 'read')
                    visible.append(c)
                    seen.add(c['id'])
                else:
                    # Already visible because it is published — but an explicit
                    # share may carry write, so take the stronger permission.
                    for existing in visible:
                        if existing['id'] == c['id'] and existing.get('permission') == 'read':
                            existing['permission'] = c.get('permission', 'read')
                            existing['shared'] = True
                            break

        return visible

    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None,
        guide: Optional[str] = None,
        sensitivity: Optional[str] = None,
        published: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        # Every route that writes a guide (the PUT endpoint, bundle import,
        # clone) lands here, so the scan lives here too: the verdict is stored
        # with the text and cannot be skipped by a new caller. '' means
        # "scanned, clean"; NULL would mean "never scanned".
        guide_flag = None
        if guide is not None:
            guide_flag = authored_text.dump_flag(authored_text.scan(guide, f"guide of {collection_id}"))
        app_db.update_collection(
            collection_id=collection_id,
            name=name,
            description=description,
            color=color,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
            guide=guide,
            guide_flag=guide_flag,
            sensitivity=sensitivity,
            published=published,
        )
        row = app_db.get_collection(collection_id)
        return self._decode_guide_flag(row) if row else None

    def delete_collection(self, collection_id: str) -> bool:
        if collection_id == "default":
            logger.warning("Cannot delete default collection")
            return False

        if not app_db.delete_collection(collection_id):
            return False

        collection_dir = self._get_collection_dir(collection_id)
        if collection_dir.exists():
            try:
                shutil.rmtree(collection_dir)
                logger.info(f"Deleted collection directory: {collection_dir}")
            except PermissionError as e:
                logger.warning(f"Could not delete collection directory (files may be locked): {e}")
            except Exception as e:
                logger.error(f"Error deleting collection directory: {e}")

        return True

    def get_documents_path(self, collection_id: str) -> Path:
        self._ensure_collection_dirs(collection_id)
        return self._get_documents_dir(collection_id)

    def get_indexes_path(self, collection_id: str) -> Path:
        self._ensure_collection_dirs(collection_id)
        return self._get_indexes_dir(collection_id)

    def add_document(self, collection_id: str, document_id: str):
        app_db.add_document_to_collection(collection_id, document_id)

    def remove_document(self, collection_id: str, document_id: str):
        app_db.remove_document_from_collection(collection_id, document_id)

    def get_collection_document_ids(self, collection_id: str) -> List[str]:
        return app_db.get_collection_documents(collection_id)

    def clear_collection_index(self, collection_id: str):
        indexes_dir = self._get_indexes_dir(collection_id)
        if indexes_dir.exists():
            shutil.rmtree(indexes_dir)
            indexes_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Cleared index for collection: {collection_id}")


# Global instance
collection_service = CollectionService()
