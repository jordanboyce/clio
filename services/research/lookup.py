"""Scholarly registry lookups: OpenAlex, Crossref and the arXiv API.

A registry record is only ever consulted for an identifier the paper itself
prints (a DOI, arXiv id or a book's ISBN — Open Library), or — for a reference with no identifier — for a
title search whose answer must then be found printed in that reference
(``grounding.similarity`` >= ``TITLE_MATCH_THRESHOLD``). A registry answer
that fails its check is discarded, with a note saying why.

Nothing here sends document text: the requests carry identifiers and, for a
title search, the cited title. ``OFFLINE_MODE`` or
``RESEARCH_LOOKUP_ENABLED=false`` turn every call into a no-op that returns
None, and the research layer then works from the paper's text alone.

None means the catalogue answered and has no such record. A catalogue that
could not be asked (a timeout, a refused connection, 429, a 5xx, a reply
that is not what it should be) raises :class:`Unreachable` instead: that is
never cached and never saved as "no record", so asking again later can
still find one.
"""

from __future__ import annotations

import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

import config
from services.research.grounding import similarity

logger = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
OPENLIBRARY = "https://openlibrary.org"
CROSSREF = "https://api.crossref.org"
ARXIV_API = "https://export.arxiv.org/api/query"
TITLE_MATCH_THRESHOLD = 0.9

_CACHE_MAX = 4000
# Tests swap in an ``httpx.MockTransport``; None is the real network.
_transport: Optional[httpx.BaseTransport] = None
_cache: "OrderedDict[str, Optional[dict]]" = OrderedDict()
_cache_lock = threading.Lock()


class Unreachable(RuntimeError):
    """A catalogue could not be asked, so its silence means nothing. The
    message says which one and why, in words a researcher can act on."""


def lookup_available() -> bool:
    settings = config.settings
    return bool(getattr(settings, "research_lookup_enabled", True)) and not settings.offline_mode


def _user_agent() -> str:
    from services.link_fetcher import DEFAULT_USER_AGENT
    agent = (getattr(config.settings, "link_user_agent", "") or DEFAULT_USER_AGENT)
    email = (getattr(config.settings, "research_contact_email", "") or "").strip()
    return f"{agent} mailto:{email}" if email else agent


def _cached(key: str, fetch) -> Optional[dict]:
    """``fetch()``'s answer, remembered. A fetch that raises (the catalogue
    could not be asked) leaves nothing behind."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    value = fetch()
    with _cache_lock:
        _cache[key] = value
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    reset_hosts()


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0]


# ── reachability (roadmap R6) ───────────────────────────────────────────
#
# A catalogue that does not answer is given a rest: once one request to a
# host times out or cannot connect, the host is not asked again for
# HOST_REST_SECONDS, so a site that blocks the catalogues pays the timeout
# once per host rather than once per source. A 429 rests the host for as
# long as its Retry-After asks (capped). The Library reads
# ``unreachable_hosts()`` to say so once, and "Try again" clears the rest.

HOST_REST_SECONDS = 300.0
MAX_RETRY_AFTER_SECONDS = 3600.0
# A host that drops packets fails at the connect step; give it less time
# than a slow answer gets.
CONNECT_TIMEOUT_SECONDS = 5.0
_rest: Dict[str, dict] = {}
_rest_lock = threading.Lock()


def _monotonic() -> float:
    import time
    return time.monotonic()


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Retry-After as seconds: a number, or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _rest_host(host: str, reason: str, seconds: float = HOST_REST_SECONDS) -> None:
    with _rest_lock:
        _rest[host] = {"until": _monotonic() + min(seconds, MAX_RETRY_AFTER_SECONDS), "reason": reason}


def _check_host(host: str) -> None:
    with _rest_lock:
        entry = _rest.get(host)
        if entry and entry["until"] <= _monotonic():
            _rest.pop(host, None)
            entry = None
    if entry:
        raise Unreachable(f"{host} was not asked: {entry['reason']} a moment ago")


def unreachable_hosts() -> List[str]:
    """Catalogue hosts resting after a failure, for the Library's notice."""
    now = _monotonic()
    with _rest_lock:
        return sorted(h for h, e in _rest.items() if e["until"] > now)


def reset_hosts() -> None:
    """"Try again": ask every catalogue on the next lookup."""
    with _rest_lock:
        _rest.clear()


def _get(url: str, params: Optional[dict] = None) -> Optional[httpx.Response]:
    """One GET: the response for a 200, None for a 404 or 410 (the catalogue
    has no such record). Anything else raises :class:`Unreachable`."""
    params = dict(params or {})
    email = (getattr(config.settings, "research_contact_email", "") or "").strip()
    if email and url.startswith(OPENALEX):
        params.setdefault("mailto", email)
    timeout = float(getattr(config.settings, "research_lookup_timeout_seconds", 15.0) or 15.0)
    host = _host(url)
    _check_host(host)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, CONNECT_TIMEOUT_SECONDS)),
                          follow_redirects=True, transport=_transport,
                          headers={"User-Agent": _user_agent()}) as client:
            response = client.get(url, params=params or None)
    except httpx.TimeoutException as exc:
        logger.info("Registry lookup timed out for %s: %s", url, exc)
        _rest_host(host, "it did not answer")
        raise Unreachable(f"{host} did not answer within {timeout:g} seconds") from exc
    except httpx.HTTPError as exc:
        logger.info("Registry lookup failed for %s: %s", url, exc)
        _rest_host(host, "it could not be reached")
        raise Unreachable(f"{host} could not be reached ({exc.__class__.__name__})") from exc
    if response.status_code in (404, 410):
        return None
    if response.status_code != 200:
        logger.info("Registry lookup %s answered %s", url, response.status_code)
        if response.status_code == 429:
            wait = _retry_after_seconds(response.headers.get("Retry-After"))
            _rest_host(host, "it was turning requests away (429)", wait if wait is not None else HOST_REST_SECONDS)
            raise Unreachable(f"{host} is turning requests away for now (429, too many requests)")
        if response.status_code >= 500:
            _rest_host(host, f"it answered with an error ({response.status_code})")
        raise Unreachable(f"{host} answered with an error ({response.status_code})")
    return response


def _json(response: httpx.Response):
    """A 200's JSON body. One that is not JSON is a catalogue fault, not a miss."""
    try:
        return response.json()
    except ValueError as exc:
        raise Unreachable(f"{_host(str(response.request.url))} sent a reply that could not be read") from exc


# ── record shapes ───────────────────────────────────────────────────────
# Every lookup returns the same dict:
#   {"title", "authors", "year", "venue", "doi", "arxiv_id", "abstract",
#    "pdf_url", "landing_url", "is_oa", "source"}


def _openalex_abstract(inverted: Optional[Dict[str, List[int]]]) -> Optional[str]:
    if not inverted:
        return None
    positions = []
    for word, places in inverted.items():
        for place in places:
            positions.append((place, word))
    positions.sort()
    text = " ".join(word for _, word in positions)
    return text or None


def _from_openalex(work: dict) -> Optional[dict]:
    if not isinstance(work, dict) or not (work.get("display_name") or work.get("title")):
        return None
    doi = (work.get("doi") or "").lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi) or None
    best = work.get("best_oa_location") or {}
    open_access = work.get("open_access") or {}
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    arxiv_id = None
    if doi and doi.startswith("10.48550/arxiv."):
        arxiv_id = doi.split("arxiv.", 1)[1]
    return {
        "title": work.get("display_name") or work.get("title"),
        "authors": [a.get("author", {}).get("display_name") for a in work.get("authorships") or []
                    if a.get("author", {}).get("display_name")][:50],
        "year": work.get("publication_year"),
        "venue": source.get("display_name"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
        "pdf_url": best.get("pdf_url") or None,
        "landing_url": best.get("landing_page_url") or open_access.get("oa_url") or None,
        "is_oa": bool(open_access.get("is_oa")),
        "source": "openalex",
    }


def _strip_tags(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip() or None


def _from_crossref(message: dict) -> Optional[dict]:
    titles = message.get("title") or []
    if not titles:
        return None
    authors = []
    for author in message.get("author") or []:
        name = " ".join(p for p in (author.get("given"), author.get("family")) if p)
        if name:
            authors.append(name)
    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    pdf_url = None
    for link in message.get("link") or []:
        if link.get("content-type") == "application/pdf" and link.get("intended-application") != "text-mining":
            pdf_url = link.get("URL")
            break
    venue = (message.get("container-title") or [None])[0]
    return {
        "title": titles[0],
        "authors": authors[:50],
        "year": year,
        "venue": venue,
        "doi": (message.get("DOI") or "").lower() or None,
        "arxiv_id": None,
        "abstract": _strip_tags(message.get("abstract")),
        # Crossref PDF links are usually publisher links behind a paywall;
        # they are offered only as a landing page, never as an open copy.
        "pdf_url": None,
        "landing_url": pdf_url,
        "is_oa": False,
        "source": "crossref",
    }


def _from_arxiv(xml_text: str, arxiv_id: str) -> Optional[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        # A 200 that is not Atom (a proxy's block page, say) is no answer.
        raise Unreachable("export.arxiv.org sent a reply that could not be read") from exc
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
    if not title or title.lower() == "error":
        return None
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    doi = entry.findtext("{http://arxiv.org/schemas/atom}doi", default="") or None
    return {
        "title": title,
        "authors": [" ".join((a.findtext("a:name", default="", namespaces=ns) or "").split())
                    for a in entry.findall("a:author", ns)][:50],
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "venue": "arXiv",
        "doi": doi.lower() if doi else None,
        "arxiv_id": arxiv_id,
        "abstract": " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split()) or None,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "landing_url": f"https://arxiv.org/abs/{arxiv_id}",
        "is_oa": True,
        "source": "arxiv",
    }


def _from_openlibrary(data: dict, isbn: str) -> Optional[dict]:
    if not isinstance(data, dict) or not data.get("title"):
        return None
    year = None
    match = re.search(r"(1[5-9]\d\d|20\d\d)", str(data.get("publish_date") or ""))
    if match:
        year = int(match.group(1))
    publishers = [p.get("name") for p in data.get("publishers") or [] if p.get("name")]
    excerpt = ((data.get("excerpts") or [{}])[0] or {}).get("text")
    return {
        "title": data["title"],
        "authors": [a.get("name") for a in data.get("authors") or [] if a.get("name")][:50],
        "year": year,
        "venue": publishers[0] if publishers else None,
        "doi": None,
        "arxiv_id": None,
        "isbn": isbn,
        "abstract": excerpt or None,
        "pdf_url": None,
        "landing_url": data.get("url"),
        "is_oa": False,
        "source": "openlibrary",
    }


# ── public lookups ──────────────────────────────────────────────────────

def by_isbn(isbn: str) -> Optional[dict]:
    """Open Library's record for a book's ISBN (title, authors, year,
    publisher). Books have no open PDF to offer, so nothing to download."""
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "").upper()
    if not isbn or not lookup_available():
        return None

    def fetch():
        key = f"ISBN:{isbn}"
        response = _get(f"{OPENLIBRARY}/api/books", {"bibkeys": key, "format": "json", "jscmd": "data"})
        if response is None:
            return None
        return _from_openlibrary((_json(response) or {}).get(key) or {}, isbn)

    return _cached(f"isbn:{isbn}", fetch)


def by_doi(doi: str) -> Optional[dict]:
    """OpenAlex first (it knows open-access copies), Crossref as fallback.
    Like every lookup here, raises :class:`Unreachable` when the catalogues
    could not be asked."""
    doi = (doi or "").strip().lower()
    if not doi or not lookup_available():
        return None

    def fetch():
        # Either catalogue may answer for the other, but "no record" stands
        # only when both were asked and both said so.
        failure: Optional[Unreachable] = None
        try:
            response = _get(f"{OPENALEX}/works/doi:{quote(doi, safe='/:;()._-')}")
            record = _from_openalex(_json(response)) if response is not None else None
            if record:
                return record
        except Unreachable as exc:
            failure = exc
        response = _get(f"{CROSSREF}/works/{quote(doi, safe='/:;()._-')}")
        record = _from_crossref((_json(response) or {}).get("message") or {}) if response is not None else None
        if record is None and failure is not None:
            raise failure
        return record

    return _cached(f"doi:{doi}", fetch)


def by_arxiv(arxiv_id: str) -> Optional[dict]:
    arxiv_id = (arxiv_id or "").strip()
    if not arxiv_id or not lookup_available():
        return None

    def fetch():
        failure: Optional[Unreachable] = None
        try:
            response = _get(ARXIV_API, {"id_list": arxiv_id, "max_results": 1})
            record = _from_arxiv(response.text, arxiv_id) if response is not None else None
            if record:
                return record
        except Unreachable as exc:
            failure = exc
        # arXiv's DataCite DOIs are indexed by OpenAlex.
        response = _get(f"{OPENALEX}/works/doi:10.48550/arxiv.{arxiv_id}")
        record = _from_openalex(_json(response)) if response is not None else None
        if record is None and failure is not None:
            raise failure
        if record:
            record["arxiv_id"] = arxiv_id
            record["pdf_url"] = record.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"
            record["is_oa"] = True
        return record

    return _cached(f"arxiv:{arxiv_id}", fetch)


def by_title(title: str, printed_in: str, year: Optional[int] = None) -> Optional[dict]:
    """Title search, accepted only when the found title is printed in
    ``printed_in`` (the reference entry, or page one of the paper).

    The returned record carries ``match`` (the similarity) so callers can
    say how the match was made."""
    title = " ".join((title or "").split())
    if len(title) < 12 or not lookup_available():
        return None

    def fetch():
        response = _get(f"{OPENALEX}/works", {"search": title[:300], "per-page": 5})
        if response is None:
            return None
        return {"results": (_json(response) or {}).get("results") or []}

    hits = _cached(f"title:{title.lower()}", fetch) or {}
    best: Optional[dict] = None
    for work in hits.get("results") or []:
        record = _from_openalex(work)
        # A short generic title ("Introduction", "Results") would match
        # almost any reference text.
        if not record or len(re.sub(r"[^a-z0-9]", "", record["title"].lower())) < 15:
            continue
        score = similarity(record["title"], printed_in)
        if score < TITLE_MATCH_THRESHOLD:
            continue
        if year and record.get("year") and abs(int(record["year"]) - int(year)) > 1:
            continue
        if best is None or score > best["match"]:
            best = {**record, "match": round(score, 3)}
    return best
