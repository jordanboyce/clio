"""Fetch the content behind a link so it can be indexed like an upload.

"Add link" in the Sources panel hands a URL to the indexing job; the job
calls fetch_link() from an extraction worker, saves the body into the
collection's documents directory, and the ordinary pipeline (extract,
policy scan, chunk, embed) takes it from there. Only the fetch is new.

The request is made by the server, which is what makes a link different
from an upload: a shared appliance that fetches whatever it is told to
becomes a proxy into its own network. validate_link() therefore refuses
URLs whose host resolves to a private, loopback, or link-local address
unless LINK_ALLOW_PRIVATE_NETWORKS is set, and every redirect hop is
checked the same way. OFFLINE_MODE refuses the feature entirely.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote

import httpx

import config

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
USER_AGENT = "Clio/1.0 (+document indexer; fetches pages a user asked to index)"

# Media type -> file extension the extractor already handles. A type not in
# this table (video, archives, binaries) is refused rather than saved as
# something the extractor would then reject anyway.
CONTENT_TYPE_EXTENSIONS = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}

# Extensions a URL path may carry that are trusted when the server sends
# no usable media type (octet-stream, or nothing at all).
_PATH_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".json", ".jsonl", ".docx",
                    ".pptx", ".xlsx", ".xls", ".html", ".htm", ".png", ".jpg", ".jpeg",
                    ".webp", ".tiff", ".tif"}

_HTML_TYPES = {"text/html", "application/xhtml+xml"}


class LinkError(ValueError):
    """The link cannot be indexed and retrying will not help (bad URL,
    refused host, unsupported content, too large, HTTP error)."""


class LinkRefused(LinkError):
    """Refused by policy: private network target, credentials in the URL,
    or link indexing switched off."""


@dataclass
class FetchedLink:
    url: str                # the URL as submitted (normalized)
    final_url: str          # after redirects
    content_type: str       # media type without parameters
    extension: str          # extension the saved copy gets
    body: bytes
    filename: str           # collision-free-ish name for the saved copy
    title: str = ""


# ── URL validation ────────────────────────────────────────────────────


def link_indexing_available() -> bool:
    return bool(config.settings.link_indexing_enabled) and not config.settings.offline_mode


def _resolve_host(host: str) -> List[str]:
    """Every address the host resolves to (a literal IP resolves to itself)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise LinkError(f"could not resolve host {host!r}") from e
    addresses = []
    for info in infos:
        addr = info[4][0]
        # getaddrinfo can hand back scoped IPv6 ("fe80::1%eth0")
        addr = addr.split("%", 1)[0]
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise LinkError(f"could not resolve host {host!r}")
    return addresses


def _is_public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def validate_link(url: str) -> str:
    """Normalize a submitted URL and refuse the ones the server must not fetch.

    Returns the URL to fetch (scheme added when missing, fragment dropped).
    Raises LinkError for a malformed or unresolvable URL and LinkRefused for
    one that policy forbids.
    """
    if not link_indexing_available():
        if config.settings.offline_mode:
            raise LinkRefused("link indexing is unavailable in offline mode")
        raise LinkRefused("link indexing is disabled on this server")

    raw = (url or "").strip()
    if not raw:
        raise LinkError("empty link")
    if any(ch.isspace() for ch in raw):
        raise LinkError("a link cannot contain whitespace")
    if "://" not in raw:
        if raw.startswith("//"):
            raw = "https:" + raw
        else:
            raw = "https://" + raw

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise LinkError(f"only http and https links can be indexed (got {scheme!r})")
    if parts.username is not None or parts.password is not None:
        raise LinkRefused("links with embedded credentials are not accepted")
    host = parts.hostname
    if not host:
        raise LinkError("link has no host")
    host = host.strip(".").lower()
    if not host:
        raise LinkError("link has no host")

    if not config.settings.link_allow_private_networks:
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            raise LinkRefused(f"{host!r} is not a public host")
        for addr in _resolve_host(host):
            if not _is_public_address(addr):
                raise LinkRefused(
                    f"{host!r} resolves to a private or local address; "
                    "the server only fetches public links"
                )
    else:
        _resolve_host(host)  # still has to exist

    try:
        port = parts.port  # raises for a non-numeric port
    except ValueError:
        raise LinkError("link has an invalid port")
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


# ── Fetching ──────────────────────────────────────────────────────────


def _media_type(content_type_header: str) -> str:
    return (content_type_header or "").split(";", 1)[0].strip().lower()


def _disposition_filename(header: str) -> str:
    """The filename a Content-Disposition header proposes, or ''."""
    if not header:
        return ""
    m = re.search(r"filename\*\s*=\s*(?:[\w-]+)?''([^;]+)", header, flags=re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', header, flags=re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"filename\s*=\s*([^;]+)", header, flags=re.I)
    if m:
        return m.group(1).strip().strip('"')
    return ""


def _html_title(body: bytes) -> str:
    head = body[:262144]
    m = re.search(rb"<title[^>]*>(.*?)</title>", head, flags=re.I | re.S)
    if not m:
        return ""
    from html import unescape
    raw = m.group(1)
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    return re.sub(r"\s+", " ", unescape(text)).strip()


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.0f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} bytes"


def _safe_stem(text: str, limit: int = 80) -> str:
    stem = _UNSAFE.sub(" ", text)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if len(stem) > limit:
        cut = stem[:limit]
        if " " in cut[limit // 2:]:
            cut = cut[: cut.rfind(" ")]
        stem = cut.rstrip(" .,;:-")
    return stem


def suggest_filename(final_url: str, extension: str, *, disposition: str = "",
                     title: str = "") -> str:
    """A filename for the saved copy that still says where it came from.

    Preference: the name the server proposed, else the page title with the
    host in parentheses, else the last path segment, else the host itself.
    The extension always matches the content actually received, whatever
    the URL or the header claimed.
    """
    parts = urlsplit(final_url)
    host = (parts.hostname or "link").lower()
    if host.startswith("www."):
        host = host[4:]

    proposed = _safe_stem(disposition)
    if proposed:
        stem = proposed
        if stem.lower().endswith(extension):
            stem = stem[: -len(extension)]
        return f"{stem}{extension}"

    if title:
        stem = _safe_stem(title, limit=70)
        if stem:
            return f"{stem} ({host}){extension}"

    last = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) if parts.path else ""
    last = _safe_stem(last)
    if last:
        for ext in _PATH_EXTENSIONS:
            if last.lower().endswith(ext):
                last = last[: -len(ext)]
                break
        return f"{last} ({host}){extension}" if last else f"{host}{extension}"
    return f"{host}{extension}"


def _extension_for(media_type: str, final_url: str) -> str:
    ext = CONTENT_TYPE_EXTENSIONS.get(media_type)
    if ext:
        return ext
    # An unhelpful or absent media type: trust a document extension in the
    # path, since that is what the person clicked on.
    path = urlsplit(final_url).path.lower()
    if not media_type or media_type in ("application/octet-stream", "binary/octet-stream"):
        for candidate in _PATH_EXTENSIONS:
            if path.endswith(candidate):
                return ".html" if candidate == ".htm" else candidate
        if not media_type:
            return ".html"  # a server that says nothing is almost always a web page
    if media_type.startswith("text/"):
        return ".txt"
    raise LinkError(f"unsupported content type {media_type!r}")


def fetch_link(url: str, *, max_bytes: Optional[int] = None,
               timeout: Optional[float] = None) -> FetchedLink:
    """Download the content behind a validated link.

    Redirects are followed by hand (at most MAX_REDIRECTS) so every hop
    passes validate_link(): a public page must not be able to bounce the
    server onto 127.0.0.1. The body is streamed and abandoned the moment it
    passes max_bytes.
    """
    max_bytes = max_bytes or config.settings.link_max_bytes
    timeout = timeout or config.settings.link_fetch_timeout_seconds
    url = validate_link(url)
    current = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,"
                  "text/markdown,application/json,*/*;q=0.5",
    }

    with httpx.Client(follow_redirects=False, timeout=timeout, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise LinkError(f"redirect from {current} carries no Location")
                        current = validate_link(urljoin(current, location))
                        continue
                    if response.status_code >= 400:
                        raise LinkError(f"HTTP {response.status_code} from {current}")
                    if not response.is_success:
                        raise LinkError(f"unexpected HTTP {response.status_code} from {current}")

                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise LinkError(
                            f"content is {_human_size(int(declared))}, larger than the "
                            f"{_human_size(max_bytes)} limit for links"
                        )
                    media_type = _media_type(response.headers.get("content-type", ""))
                    extension = _extension_for(media_type, current)

                    chunks = []
                    received = 0
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise LinkError(
                                f"content exceeds the {_human_size(max_bytes)} limit for links"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
            except httpx.TimeoutException as e:
                raise LinkError(f"timed out fetching {current}") from e
            except httpx.HTTPError as e:
                raise LinkError(f"could not fetch {current}: {e}") from e

            if not body:
                raise LinkError(f"{current} returned no content")
            title = _html_title(body) if extension == ".html" else ""
            filename = suggest_filename(
                current, extension,
                disposition=_disposition_filename(response.headers.get("content-disposition", "")),
                title=title,
            )
            logger.info(f"Fetched link {url} -> {filename} ({len(body)} bytes, {media_type or 'no type'})")
            return FetchedLink(
                url=url, final_url=current, content_type=media_type,
                extension=extension, body=body, filename=filename, title=title,
            )

    raise LinkError(f"too many redirects fetching {url}")
