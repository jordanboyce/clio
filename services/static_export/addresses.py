"""Where each source lives on the web: the addresses a static export links to."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from services.static_export.common import StaticExportError

MAX_ADDRESS_CHARS = 2048
_ADDRESS_SETTINGS = "static_export_addresses:{}"
# Sources whose file a site can publish; a pasted note or an agent's note has
# no file at an address of its own.
_FILE_SOURCE_TYPES = (None, "", "upload", "local_reference", "folder_sync")
_PAGE_EXTENSIONS = (".pdf",)
_TEXT_FRAGMENT_EXTENSIONS = (".html", ".htm", ".xhtml", ".md", ".txt")


def clean_public_url(value: Optional[str]) -> Optional[str]:
    """An address a visitor's browser can open, or None for "none".

    Accepted: an absolute http(s) URL with no user name or password in it, or
    a path on the site itself starting with "/". Anything else (javascript:,
    a file path, an address with spaces) is refused with StaticExportError.
    """
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_ADDRESS_CHARS or re.search(r"[\s\x00-\x1f\x7f<>\"\\]", text):
        raise StaticExportError(f"{text[:80]} is not a web address a page can link to.", "address")
    if text.startswith("/") and not text.startswith("//"):
        return text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise StaticExportError(
            f"{text[:80]} is not a web address: use one that starts with https:// (or a path on the site, "
            f"starting with /).", "address")
    if parts.username or parts.password:
        raise StaticExportError("A web address for a public site must not carry a user name or password.",
                                "address")
    return text


def clean_site_url(value: Optional[str]) -> Optional[str]:
    """Where the export will be served, or None: an absolute http(s) address
    (a sitemap, canonical links and a feed need one), ending with "/"."""
    text = clean_public_url(value)
    if text is None:
        return None
    if text.startswith("/"):
        raise StaticExportError(
            "Where the site will be served must be a whole address, starting with https://, since a sitemap and "
            "a feed need one.", "address")
    return text if text.endswith("/") else text + "/"


ADDRESS_FIELDS = ("files_base_url", "site_url")


def address_settings(collection_id: str) -> Dict[str, Any]:
    """The collection's saved export addresses: ``{"files_base_url", "site_url"}``."""
    from services.app_database import app_db

    saved = app_db.get_config(_ADDRESS_SETTINGS.format(collection_id), {}) or {}
    saved = saved if isinstance(saved, dict) else {}
    return {name: saved.get(name) for name in ADDRESS_FIELDS}


def restore_address_settings(collection_id: str, saved: Any) -> Dict[str, Any]:
    """Settings carried in a bundle, kept only where each is still an address
    a browser may open (an imported bundle is data, not trusted)."""
    from services.app_database import app_db

    saved = saved if isinstance(saved, dict) else {}
    kept: Dict[str, Any] = {}
    for name, clean in (("files_base_url", clean_public_url), ("site_url", clean_site_url)):
        value = saved.get(name)
        try:
            kept[name] = clean(value if isinstance(value, str) else None)
        except StaticExportError:
            kept[name] = None
    if any(kept.values()):
        app_db.set_config(_ADDRESS_SETTINGS.format(collection_id), kept)
    return kept


def _web_page(path: str) -> str:
    return quote(path, safe="/-._~")


# A repository host whose web pages show a file at a commit: (does the host
# match, the file's page, the anchor for lines). Other hosts get no link of
# their own; give their sources an address instead.
_FORGES = (
    (lambda h: h == "github.com", "{base}/blob/{commit}/{path}", "#L{start}-L{end}"),
    (lambda h: h == "gitlab.com" or h.startswith("gitlab."), "{base}/-/blob/{commit}/{path}", "#L{start}-{end}"),
    (lambda h: h == "bitbucket.org", "{base}/src/{commit}/{path}", "#lines-{start}:{end}"),
    (lambda h: h == "codeberg.org" or h.startswith(("gitea.", "forgejo.")),
     "{base}/src/commit/{commit}/{path}", "#L{start}-L{end}"),
)


def repository_file_link(remote: Optional[str], commit: Optional[str], path: str) -> Optional[Dict[str, str]]:
    """The web page of one file at one commit, for the hosts in ``_FORGES``.

    Takes the clone's remote as git has it (https://, ssh:// or git@host:...);
    credentials in it are dropped, never published.
    """
    remote = (remote or "").strip()
    if not remote or not commit or not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
        return None
    scp = re.fullmatch(r"[\w.-]+@([\w.-]+):(.+)", remote)
    if scp:
        host, repo = scp.group(1), scp.group(2)
    else:
        parts = urlsplit(remote)
        if parts.scheme not in ("http", "https", "ssh", "git") or not parts.hostname:
            return None
        host, repo = parts.hostname, parts.path
    host = host.lower()
    repo = re.sub(r"\.git$", "", repo.strip("/"))
    if repo.count("/") < 1 or ".." in repo.split("/"):
        return None
    for matches, page, lines in _FORGES:
        if matches(host):
            url = page.format(base=f"https://{host}/{repo}", commit=commit, path=_web_page(path))
            return {"url": url, "lines": lines}
    return None


def _repositories(collection_id: str) -> List[Dict[str, Any]]:
    """Synced folders that are git repositories: [{"root", "remote", "commit"}]."""
    from services.upload_service import upload_service

    found = []
    for entry in upload_service.list_sync_folders(collection_id):
        if not entry.get("git") or not entry.get("git_head"):
            continue
        remote = entry.get("git_url")
        root = Path(entry["path"])
        if not remote:
            # A checkout indexed in place: its own origin, read from disk.
            try:
                from services import git_source

                remote = git_source._run(["config", "--get", "remote.origin.url"], cwd=root,
                                         safe_dir=root).strip()
            except Exception:  # noqa: BLE001 - no origin, no link
                remote = None
        if remote:
            found.append({"root": root, "remote": remote, "commit": entry["git_head"]})
    return found


def _repository_link(doc: Dict[str, Any], repositories: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if doc.get("source_type") != "folder_sync" or not doc.get("source_path"):
        return None
    try:
        path = Path(doc["source_path"]).resolve()
    except (OSError, ValueError):
        return None
    for repo in repositories:
        try:
            relative = path.relative_to(repo["root"].resolve())
        except (OSError, ValueError):
            continue
        return repository_file_link(repo["remote"], repo["commit"], relative.as_posix())
    return None


def document_link(doc: Dict[str, Any], files_base_url: Optional[str],
                  repositories: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Where a visitor goes for this source, and how to point inside it.

    In order: the address someone gave the source; the page a link source was
    fetched from; the file at its commit, for a file synced from a public
    repository; the collection's base address for files plus the file name.
    ``page`` and ``lines`` are anchors with {page}, {start} and {end} to fill
    in; ``text`` says the page can take a text fragment (#:~:text=).
    """
    name = doc.get("filename") or ""
    extension = Path(name).suffix.lower()
    url, came_from, lines = None, None, None
    own = doc.get("public_url")
    try:
        own = clean_public_url(own)
    except StaticExportError:
        own = None  # an address that was never checked (an old bundle) is not published
    if own:
        url, came_from = own, "own"
    elif doc.get("source_type") == "url" and str(doc.get("source_path") or "").lower().startswith(("http://", "https://")):
        url, came_from = doc["source_path"], "link"
    else:
        repo = _repository_link(doc, repositories)
        if repo:
            url, came_from, lines = repo["url"], "repository", repo["lines"]
        elif files_base_url and doc.get("source_type") in _FILE_SOURCE_TYPES:
            url, came_from = files_base_url.rstrip("/") + "/" + _web_page(name), "files"
    if not url:
        return None
    link: Dict[str, Any] = {"url": url, "from": came_from}
    if lines:
        link["lines"] = lines
    elif extension in _PAGE_EXTENSIONS:
        link["page"] = "#page={page}"
    elif extension in _TEXT_FRAGMENT_EXTENSIONS or came_from == "link":
        link["text"] = True
    return link


def addresses(collection_id: str) -> Dict[str, Any]:
    """Every source that would be exported, with the address it would carry."""
    survey = _pkg()._survey(collection_id)
    survey["conn"].close()
    settings_ = address_settings(collection_id)
    repositories = _pkg()._repositories(collection_id)
    rows = []
    for d in survey["documents"]:
        link = document_link(d, settings_["files_base_url"], repositories)
        rows.append({"id": d["document_id"], "name": d["filename"], "source_type": d.get("source_type") or "upload",
                     "own_url": d.get("public_url"), "url": (link or {}).get("url"),
                     "from": (link or {}).get("from")})
    return {"files_base_url": settings_["files_base_url"], "site_url": settings_["site_url"], "documents": rows,
            "with_address": sum(1 for r in rows if r["url"]), "total": len(rows)}


def save_addresses(collection_id: str, *, files_base_url: Any = ..., site_url: Any = ...,
                   documents: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Save the base address for files, where the site will be served, and
    any source's own address.

    ``files_base_url`` or ``site_url`` left out keeps the saved one; None or
    "" clears it.
    ``documents`` maps a source's id to its address (None or "" clears it).
    Every address is checked first, so a refusal saves nothing.
    """
    from services.app_database import app_db
    from services.indexer_manager import indexer_manager

    base = clean_public_url(files_base_url) if files_base_url is not ... else ...
    site = clean_site_url(site_url) if site_url is not ... else ...
    cleaned = {doc_id: clean_public_url(url) for doc_id, url in (documents or {}).items()}
    if cleaned:
        store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
        unknown = [doc_id for doc_id in cleaned if not store.get_document_info(doc_id)]
        if unknown:
            raise StaticExportError(f"This collection has no source {unknown[0]}.", "address")
        for doc_id, url in cleaned.items():
            store.set_document_public_url(doc_id, url)
    saved = address_settings(collection_id)
    if base is not ... or site is not ...:
        saved = {**saved, **({"files_base_url": base} if base is not ... else {}),
                 **({"site_url": site} if site is not ... else {})}
        app_db.set_config(_ADDRESS_SETTINGS.format(collection_id), saved)
    return {**saved, "documents": len(cleaned)}


def _pkg():
    """The package, for functions a caller (a test) may have replaced there."""
    from services import static_export

    return static_export
