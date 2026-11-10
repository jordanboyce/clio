"""Classification markings, and the rule that some of them keep data on this server.

A collection or a single document can carry a classification: export
controlled, CUI, proprietary, or whatever the site's own vocabulary is (Admin,
Policy). Each marking either is a label only, or is *local only*: nothing
outside this deployment may be handed the data. That is separate from the
sensitivity label (services/governance.py), which says who among the people
here should see something. This says which machines may.

What counts as outside:

- A hosted AI model, or one at an address that is not on this network.
- A hosted embedding service.
- Agents over MCP and registered apps on /api/v1. They are other programs, and
  where they send what they read is not this server's to know.
- Scholarly catalogue lookups, share and report emails, and the change webhook.

Two rules do the work, and both fail closed.

Both work on whole collections: a collection that holds anything local only,
because it is marked or because one document in it is (``holds_local_only``),
is closed to everything outside. A marked document is not picked out of its
collection and the rest served, because a collection reaches a model in many
ways besides its passages (file lists, tables, figures, overviews, statistics)
and one missed path would be a release. Someone who wants outside services
for the rest moves the marked documents to a collection of their own.

Serving. A request that will hand what it reads to something outside runs
inside ``outside_consumer``. While that is set, such a collection cannot be
opened: ``check_collection`` is called where every surface resolves a
collection. As a second line, a local-only document is also hidden the way a
quarantined one is (``governance.is_hidden``).

Processing. Work the server does on its own when a source is added (embedding,
vision OCR, figure descriptions, the model's content review) never uses an
outside service for such a collection (``local_processing``,
``LocalOnlyEmbedding``).

The marking is stored on the collection row and the document row. A document
is local only when its own marking is, or its collection's is: a document can
tighten its collection's rule and never loosen it. A marking that is no longer
in the catalogue is treated as local only, so deleting a marking cannot
quietly release what carried it.

"On this network" is decided without asking DNS, which could be made to
answer differently later: loopback, a private address written as an address, a
single-label name such as a compose service, or a host the operator listed in
CONTROLLED_DATA_HOSTS.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from fastapi import HTTPException

from config import settings

logger = logging.getLogger(__name__)

# Used until an administrator saves their own list.
DEFAULT_CLASSIFICATIONS: List[Dict[str, Any]] = [
    {"id": "export_controlled", "label": "Export controlled",
     "description": "Technical data under export control, such as ITAR or EAR.", "local_only": True},
    {"id": "cui", "label": "CUI",
     "description": "Controlled Unclassified Information.", "local_only": True},
    {"id": "proprietary", "label": "Proprietary",
     "description": "Trade secrets and other company-confidential material.", "local_only": True},
]

MAX_CLASSIFICATIONS = 24
_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")

# Providers that are always someone else's servers. Ollama and an
# OpenAI-compatible endpoint are judged by their address instead.
_ADDRESSED_PROVIDERS = ("ollama", "openai_compatible")

BLOCKED_CODE = "external_service_blocked"


# ── The catalogue ─────────────────────────────────────────────────────────

def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]


def clean_catalog(value: Any) -> List[Dict[str, Any]]:
    """Validate a catalogue as an administrator typed it. Raises ValueError."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except json.JSONDecodeError as e:
            raise ValueError("data_classifications must be a list of markings") from e
    if not isinstance(value, list):
        raise ValueError("data_classifications must be a list of markings")
    if len(value) > MAX_CLASSIFICATIONS:
        raise ValueError(f"At most {MAX_CLASSIFICATIONS} markings")
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Each marking needs a name")
        label = str(entry.get("label") or "").strip()
        if not label or len(label) > 60:
            raise ValueError("Each marking needs a name of at most 60 characters")
        marking_id = str(entry.get("id") or "").strip().lower() or _slug(label)
        if not _ID.match(marking_id):
            raise ValueError(f"'{label}' needs a name with letters or digits in it")
        if marking_id in seen:
            raise ValueError(f"Two markings are both called '{label}'")
        seen.add(marking_id)
        local_only = entry.get("local_only", True)
        if not isinstance(local_only, bool):
            raise ValueError(f"'{label}': local_only must be true or false")
        out.append({"id": marking_id, "label": label,
                    "description": str(entry.get("description") or "").strip()[:300],
                    "local_only": local_only})
    return out


def catalog() -> List[Dict[str, Any]]:
    """The markings this deployment offers."""
    raw = settings.data_classifications
    if isinstance(raw, str):
        raw = raw.strip()
    if not raw:
        return [dict(m) for m in DEFAULT_CLASSIFICATIONS]
    try:
        return clean_catalog(raw)
    except ValueError as e:
        # Unreadable is not the same as empty: with no catalogue every stored
        # marking is unknown, and unknown markings are local only.
        logger.error(f"DATA_CLASSIFICATIONS cannot be read, so every marking is treated as local only: {e}")
        return []


def _by_id() -> Dict[str, Dict[str, Any]]:
    return {m["id"]: m for m in catalog()}


def normalize(value: Any) -> Optional[str]:
    """A marking id for storage. Empty means none. Raises ValueError for one
    this deployment does not have."""
    if value is None or str(value).strip() == "":
        return None
    marking_id = str(value).strip().lower()
    if marking_id not in _by_id():
        offered = ", ".join(m["id"] for m in catalog()) or "none are defined"
        raise ValueError(f"Unknown classification '{marking_id}'. This server has: {offered}")
    return marking_id


def marking(marking_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """What a stored id means now. One no longer in the catalogue keeps its
    name and is local only."""
    if not marking_id:
        return None
    known = _by_id().get(marking_id)
    if known:
        return known
    return {"id": marking_id, "label": marking_id.replace("_", " "), "description": "", "local_only": True,
            "retired": True}


def is_local_only(marking_id: Optional[str]) -> bool:
    found = marking(marking_id)
    return bool(found and found["local_only"])


def label_of(marking_id: Optional[str]) -> str:
    found = marking(marking_id)
    return found["label"] if found else ""


# ── What is on this network ───────────────────────────────────────────────

def _allowed_hosts() -> List[str]:
    return [h.strip().lower() for h in (settings.controlled_data_hosts or "").split(",") if h.strip()]


def host_is_internal(host: str) -> bool:
    """Whether a host name or address is inside this deployment's boundary."""
    host = (host or "").strip().lower().strip("[]").rstrip(".")
    if not host:
        return False
    if any(host == allowed or (allowed.startswith("*.") and host.endswith(allowed[1:]))
           for allowed in _allowed_hosts()):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A name. One label ("ollama", "localhost") cannot be a public name;
        # anything with a dot is public unless the operator listed it.
        return host == "localhost" or host.endswith(".localhost") or "." not in host
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local


def url_is_internal(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False
    if "://" not in url:
        url = "http://" + url
    try:
        return host_is_internal(urlsplit(url).hostname or "")
    except ValueError:
        return False


def model_is_external(provider_name: str, base_url: str = "") -> bool:
    """Whether calling this model sends what it is given off this network."""
    name = (provider_name or "").strip().lower()
    if name not in _ADDRESSED_PROVIDERS:
        return True
    if name == "ollama":
        base_url = base_url or settings.ollama_base_url
    return not url_is_internal(base_url)


def provider_is_external(provider: Any) -> bool:
    """For a provider built by ``ai_service.create_provider``, which records
    where it points. One that does not say is assumed to be outside."""
    if provider is None:
        return False
    return bool(getattr(provider, "external", True))


def embedding_is_external(provider_id: Optional[str] = None, base_url: str = "") -> bool:
    """Whether the embedding provider sends text off this network."""
    provider_id = (provider_id or settings.embedding_provider or "local").strip().lower()
    if provider_id == "local":
        return False
    if provider_id == "ollama":
        return not url_is_internal(base_url or settings.ollama_base_url)
    if provider_id == "openai_compatible":
        return not url_is_internal(base_url or settings.embedding_base_url)
    return True


# ── What is marked ────────────────────────────────────────────────────────

def _collection(collection: Any) -> Optional[Dict[str, Any]]:
    """A collection row from a row or an id."""
    if collection is None or isinstance(collection, dict):
        return collection
    from services.app_database import app_db

    return app_db.get_collection(str(collection))


def collection_classification(collection: Any) -> Optional[str]:
    return (_collection(collection) or {}).get("classification") or None


def collection_local_only(collection: Any) -> bool:
    return is_local_only(collection_classification(collection))


def effective_classification(collection: Any, doc_classification: Optional[str]) -> Optional[str]:
    """What applies to a document: its own marking, else its collection's. When
    both are set, a local-only one wins over a label-only one."""
    own = doc_classification or None
    inherited = collection_classification(collection)
    if own and inherited and is_local_only(inherited) and not is_local_only(own):
        return inherited
    return own or inherited


def holds_local_only(collection_id: Optional[str]) -> bool:
    """Whether the collection is local only or has a local-only document in it.

    Not being able to tell counts as yes.
    """
    if not collection_id:
        return False
    try:
        if collection_local_only(collection_id):
            return True
        return any(is_local_only(m) for m in _document_markings(collection_id).values())
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not read the classification of {collection_id}; treating it as local only: {e}")
        return True


def _document_markings(collection_id: str) -> Dict[str, str]:
    """document_id -> classification for the marked documents of a collection.

    From the open index when there is one, else straight from the collection's
    database: listing collections must not load every one of their indexes.
    """
    from services.indexer_manager import indexer_manager

    indexer = indexer_manager._indexers.get(collection_id)
    if indexer is not None:
        return indexer.vector_store.metadata_store.classified_document_ids()

    import sqlite3

    from services.collection_service import collection_service

    db_path = collection_service.get_indexes_path(collection_id) / "metadata.db"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        if "classification" not in columns:
            return {}  # from before markings, so nothing in it is marked
        return dict(conn.execute(
            "SELECT document_id, classification FROM documents "
            "WHERE classification IS NOT NULL AND classification != ''"))
    finally:
        conn.close()


def local_only_summary(collection_id: str) -> Dict[str, Any]:
    """Why a collection is closed to outside services, for messages and the UI."""
    row = _collection(collection_id)
    own = collection_classification(row)
    if is_local_only(own):
        return {"local_only": True, "classification": own, "documents": 0}
    try:
        marked = [m for m in _document_markings(collection_id).values() if is_local_only(m)]
    except Exception:  # noqa: BLE001
        return {"local_only": True, "classification": None, "documents": 0}
    return {"local_only": bool(marked), "classification": marked[0] if marked else None, "documents": len(marked)}


# ── Serving: who is asking ────────────────────────────────────────────────

_outside: ContextVar[str] = ContextVar("clio_outside_consumer", default="")
_local_processing: ContextVar[bool] = ContextVar("clio_local_processing", default=False)


def current_outside_consumer() -> str:
    """What outside this deployment the current request hands its reads to,
    or an empty string."""
    return _outside.get()


def mark_outside_consumer(name: str):
    """Set it for the rest of this context. Returns the token to reset with.
    Never replaces one that is already set: a model on this network asked by
    an MCP client is still answering an MCP client."""
    return _outside.set(_outside.get() or name)


def reset_outside_consumer(token) -> None:
    _outside.reset(token)


@contextmanager
def outside_consumer(name: str):
    """Everything read in this block is on its way to ``name``. An empty name
    changes nothing, so callers can pass ``model_destination(...)`` straight in."""
    if not name:
        yield
        return
    token = mark_outside_consumer(name)
    try:
        yield
    finally:
        _outside.reset(token)


def model_destination(provider_name: str, base_url: str = "") -> str:
    """The name to give ``outside_consumer`` for a model, empty when the model
    is on this network. Applies the deployment's own endpoint the way the
    provider factory does, so the answer matches where the call really goes."""
    name = (provider_name or "").strip().lower()
    if not name:
        return ""
    if not base_url and name == (settings.ai_provider or "").strip().lower():
        base_url = settings.ai_base_url
    return f"the AI model ({name})" if model_is_external(name, base_url) else ""


def provider_destination(provider: Any) -> str:
    """The same, for a provider that is already built."""
    if provider is None or not provider_is_external(provider):
        return ""
    return f"the AI model ({getattr(provider, 'egress_name', '') or 'outside this network'})"


class ExternalServiceBlocked(HTTPException):
    """A local-only marking stopped data going to something outside. A 403 whose
    detail says what was stopped and what would work instead."""

    def __init__(self, message: str, *, classification: Optional[str] = None, service: str = ""):
        super().__init__(status_code=403, detail={
            "code": BLOCKED_CODE,
            # Under both names: the app's error handling reads "detail".
            "detail": message,
            "message": message,
            "classification": classification,
            "service": service,
        })
        self.message = message

    def __str__(self) -> str:
        return self.message


REMEDY = ("Use a model on your own network (local Ollama, or an OpenAI-compatible server at an internal "
          "address), or work with it in this app without one.")


def blocked_message(collection: Any, service: str) -> str:
    row = _collection(collection) or {}
    why = local_only_summary(row.get("id")) if row.get("id") else {"documents": 0, "classification": None}
    name = label_of(why.get("classification")) or "local only"
    if why.get("documents"):
        n = why["documents"]
        return (f"This collection holds {n} source{'' if n == 1 else 's'} marked {name}, so nothing in it can "
                f"go to {service}. {REMEDY} To use an outside service with the rest, move the marked "
                f"source{'' if n == 1 else 's'} to a collection of {'its' if n == 1 else 'their'} own.")
    return f"This collection is marked {name}, so nothing in it can go to {service}. {REMEDY}"


def check_collection(collection: Any) -> None:
    """Refuse to open a local-only collection for an outside consumer.

    Called where each surface resolves a collection (``api.deps.get_indexer``
    and the MCP resolver), so a new endpoint is covered by using either.
    """
    service = _outside.get()
    if not service:
        return
    row = _collection(collection)
    if not row or not holds_local_only(row.get("id")):
        return
    _record_block(row, service)
    raise ExternalServiceBlocked(blocked_message(row, service),
                                 classification=collection_classification(row), service=service)


def collection_withheld(collection: Any) -> bool:
    """The same test without raising or auditing, for listings."""
    return bool(_outside.get()) and holds_local_only((_collection(collection) or {}).get("id"))


def document_withheld(doc_info: Optional[Dict[str, Any]]) -> bool:
    """Whether this document row must stay out of what the current request reads."""
    return bool(doc_info) and bool(_outside.get()) and is_local_only(doc_info.get("classification"))


def external_allowed(collection: Any, doc_info: Optional[Dict[str, Any]] = None) -> bool:
    """Whether a service outside may be told anything about this collection:
    its name in an email, a filename in a webhook, a DOI in a lookup."""
    return not (is_local_only((doc_info or {}).get("classification"))
                or holds_local_only((_collection(collection) or {}).get("id")))


def _record_block(row: Optional[Dict[str, Any]], service: str, **detail: Any) -> None:
    """A refusal is evidence the control held, so it goes in the audit trail."""
    try:
        from services import audit

        audit.record("classification.blocked", collection_id=(row or {}).get("id"), target=service,
                     detail={"classification": collection_classification(row), **detail})
    except Exception as e:  # noqa: BLE001 - never turn a refusal into an error
        logger.debug(f"Could not audit a blocked call: {e}")


# ── Processing: what the server does on its own ───────────────────────────

@contextmanager
def local_processing(collection_id: Optional[str]):
    """Index a source without outside help when its collection holds anything
    local only. Read by ``allow_outside_processing`` further down the same call."""
    if not holds_local_only(collection_id):
        yield False
        return
    token = _local_processing.set(True)
    try:
        yield True
    finally:
        _local_processing.reset(token)


def allow_outside_processing(provider: Any = None, *, external: Optional[bool] = None) -> bool:
    """Whether the step running now may call this service."""
    if not _local_processing.get():
        return True
    return not (provider_is_external(provider) if external is None else external)


def embedding_withheld(collection_id: Optional[str]) -> bool:
    """Whether this collection's text is kept from the embedding provider."""
    return embedding_is_external() and holds_local_only(collection_id)


class LocalOnlyEmbedding:
    """An embedding service that will not send a local-only collection's text to
    an outside provider.

    Refusing looks like the model being unreachable (``EmbeddingUnavailable``),
    which every caller already handles: sources index for keyword search and
    searches answer from keywords. Decided on each call, so marking a
    collection takes effect on the next one. Everything else is the wrapped
    service's.
    """

    def __init__(self, service: Any, collection_id: str):
        object.__setattr__(self, "_service", service)
        object.__setattr__(self, "_collection_id", collection_id)

    @property
    def __class__(self):  # isinstance() sees the wrapped service, as its callers expect
        return type(self._service)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._service, name, value)

    def _check(self) -> None:
        if embedding_withheld(self._collection_id):
            from services.embedder import EmbeddingUnavailable

            raise EmbeddingUnavailable(
                "This collection holds data marked local only, and this server's embedding provider is "
                "outside the network, so its text is not sent there. It is searched by keyword. Choose "
                "the built-in model or one on your own network in Settings to search it by meaning."
            )

    def embed_texts(self, *args: Any, **kwargs: Any) -> Any:
        self._check()
        return self._service.embed_texts(*args, **kwargs)

    def embed_query(self, *args: Any, **kwargs: Any) -> Any:
        self._check()
        return self._service.embed_query(*args, **kwargs)


def guard_embedding(service: Any, collection_id: Optional[str]) -> Any:
    if not collection_id or type(service) is LocalOnlyEmbedding:
        return service
    return LocalOnlyEmbedding(service, collection_id)


def public_payload() -> Dict[str, Any]:
    """What the UI needs to offer and explain the markings."""
    return {"classifications": catalog(), "embedding_external": embedding_is_external()}


def guard_model_call(collection_id: str, provider: Any) -> None:
    """Refuse a model call about a collection that holds local-only data when
    the model is outside. For calls that already have their indexer in hand,
    and for each step of a background job, since a source may have been marked
    after the job began."""
    service = provider_destination(provider)
    if service:
        with outside_consumer(service):
            check_collection(collection_id)
