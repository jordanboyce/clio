"""Research metadata benchmark: how often is a paper's record right?

Every labelled document goes through the same path as the Research tab's
"Analyze" button: the file is indexed into a real (throwaway) collection with
``DocumentIndexer.index_document``, exactly as an upload is, and then
``services.research.library.analyze_document`` reads its record, the call
``POST /api/research/papers/{id}/analyze`` makes. Each record is then scored
per field against the labels: title, authors, year, arXiv id, DOI and ISBN.

The analysis runs twice over the same indexed files:

  offline   catalogue lookups are on, as shipped, but every outside request
            is refused at once: the case of a site where arXiv, Crossref,
            OpenAlex and Open Library are blocked. Sockets to anything but
            loopback are refused for the whole pass, so this pass cannot use
            the network even by accident.
  online    lookups reach the catalogues (``--lookups live``, the default),
            with a delay between requests to each host (3 s for arXiv, 1 s
            elsewhere) and a generic User-Agent. Every response is saved, so
            ``--lookups replay`` repeats the same online pass later with the
            network blocked. ``--lookups off`` skips the online pass.

Indexing always happens with the network blocked; it needs no model (chunks
are stored for keyword search, which is what a site without a model hub
gets). OCR follows the usual settings (``ENABLE_OCR``, off by default).

Two document sets:

  manifest  tests/fixtures/research_bench/manifest.json: 59 real papers,
            reports and books, downloaded on request (``--download``) into a
            cache directory and checked against their sha256. Not committed.
  smoke     tests/fixtures/research_bench/smoke/: eight small invented PDFs,
            committed, that reproduce the failure shapes seen live. Runs with
            no download at all.

Usage (the venv lives in the main checkout on the dev machine):

    venv/Scripts/python scripts/research_bench.py --download    # first time: fetch ~80 MB
    venv/Scripts/python scripts/research_bench.py               # offline + online, cached files
    venv/Scripts/python scripts/research_bench.py --lookups replay   # online pass from the recording
    venv/Scripts/python scripts/research_bench.py --set smoke --lookups off
    venv/Scripts/python scripts/research_bench.py --json out.json --out out.md

or through pytest, where it is skipped unless asked for:

    CLIO_RESEARCH_BENCH=1 venv/Scripts/python -m pytest tests/benchmark/test_research_bench.py -s
    CLIO_RESEARCH_BENCH=full venv/Scripts/python -m pytest tests/benchmark/test_research_bench.py -s

The matching rules are in tests/fixtures/research_bench/README.md and in the
``score_*`` functions below. Numbers compare only within one manifest
version: change a label or a file, bump the version, and say so where the
new numbers are quoted.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "research_bench"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
SMOKE_PATH = FIXTURE_DIR / "smoke.json"
DEFAULT_CACHE = REPO_ROOT / "data" / "research_bench"

# Says what is asking and why, and nothing about who runs it.
USER_AGENT = "clio-research-bench/1.0 (document metadata accuracy benchmark)"
HOST_DELAY_SECONDS = {"arxiv.org": 3.0}
DEFAULT_HOST_DELAY = 1.0

FIELDS = ("title", "authors", "year", "arxiv_id", "doi", "isbn")
IDENTIFIERS = ("arxiv_id", "doi", "isbn")
KINDS = ("preprint", "conference", "journal", "report", "book", "scanned")
REQUIRED_KINDS = ("preprint", "conference", "report", "book", "scanned")
TITLE_NEAR = 0.9

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── Environment ──────────────────────────────────────────────────────────────

def prepare_environment() -> Path:
    """A throwaway data dir and the settings the benchmark depends on.

    Must run before ``config`` is imported. Values that would make the run
    unrepeatable or send something personal are forced; the rest are only
    defaulted. Returns the data dir."""
    os.environ.setdefault("CLIO_ENV_FILE", str(FIXTURE_DIR / ".env.does-not-exist"))
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="crb-"))
    for key, value in {
        "PRIVATE_COLLECTIONS": "false", "AUTH_PASSWORD": "", "IDENTITY_PROVIDER": "",
        "AI_PROVIDER": "", "AI_BASE_URL": "", "AI_MODEL": "", "AI_API_KEY": "",
        # No embedding model is needed to read a record; never fetch one.
        "HF_HUB_OFFLINE": "1", "ENABLE_RERANKER": "false",
        # Catalogue requests carry a generic agent and no contact address.
        "RESEARCH_CONTACT_EMAIL": "", "LINK_USER_AGENT": USER_AGENT,
        "RESEARCH_LOOKUP_ENABLED": "true",
    }.items():
        os.environ[key] = value
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(Path(os.environ["DATA_DIR"]) / "fastembed"))
    return Path(os.environ["DATA_DIR"])


# ── Documents and labels ─────────────────────────────────────────────────────

@dataclass
class Entry:
    id: str
    filename: str
    kind: str
    labels: Dict[str, Any]
    accept: Dict[str, list] = field(default_factory=dict)
    columns: int = 1
    url: Optional[str] = None
    sha256: Optional[str] = None
    licence: str = ""
    note: Optional[str] = None
    derive: Optional[dict] = None
    path: Optional[Path] = None      # the local file, once cached or committed


@dataclass
class DocSet:
    name: str
    version: int
    entries: List[Entry]


def _entry(raw: dict) -> Entry:
    return Entry(id=raw["id"], filename=raw["filename"], kind=raw["kind"], labels=dict(raw["labels"]),
                 accept=dict(raw.get("accept") or {}), columns=int(raw.get("columns") or 1),
                 url=raw.get("url"), sha256=raw.get("sha256"), licence=raw.get("licence", ""),
                 note=raw.get("note"), derive=raw.get("derive"))


def load_manifest(path: Path = MANIFEST_PATH) -> DocSet:
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocSet("manifest", int(data["version"]), [_entry(e) for e in data["entries"]])


def load_smoke(path: Path = SMOKE_PATH) -> DocSet:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for raw in data["entries"]:
        e = _entry(raw)
        e.path = path.parent / raw["file"]
        entries.append(e)
    return DocSet("smoke", int(data["version"]), entries)


def validate(docset: DocSet) -> List[str]:
    """Every problem with a document set, one line each; empty when sound."""
    problems: List[str] = []
    ids = [e.id for e in docset.entries]
    names = [e.filename for e in docset.entries]
    if len(set(ids)) != len(ids):
        problems.append("duplicate entry ids")
    if len(set(names)) != len(names):
        problems.append("duplicate filenames")
    for e in docset.entries:
        where = e.id
        if e.kind not in KINDS:
            problems.append(f"{where}: unknown kind {e.kind!r}")
        if set(e.labels) != set(FIELDS):
            problems.append(f"{where}: labels must have exactly {FIELDS}")
            continue
        if not isinstance(e.labels["title"], str) or not e.labels["title"].strip():
            problems.append(f"{where}: no title label")
        if not isinstance(e.labels["authors"], list) or not all(isinstance(a, str) and a.strip()
                                                                for a in e.labels["authors"]):
            problems.append(f"{where}: authors must be a list of names")
        if not isinstance(e.labels["year"], int):
            problems.append(f"{where}: year must be an integer")
        for ident in IDENTIFIERS:
            if e.labels[ident] is not None and not isinstance(e.labels[ident], str):
                problems.append(f"{where}: {ident} must be a string or null")
        if e.labels["isbn"] and len(norm_isbn(e.labels["isbn"])) != 13:
            problems.append(f"{where}: ISBN label is not a valid ISBN")
        for key, values in e.accept.items():
            if key not in FIELDS or not isinstance(values, list):
                problems.append(f"{where}: accept.{key} is not a list for a known field")
            elif key == "authors" and not all(isinstance(v, list) for v in values):
                problems.append(f"{where}: accept.authors must be a list of author lists")
        if not e.licence:
            problems.append(f"{where}: no licence noted")
        if docset.name == "manifest":
            if e.derive:
                if e.derive.get("from") not in ids:
                    problems.append(f"{where}: derived from unknown entry {e.derive.get('from')!r}")
            else:
                if not (e.url or "").startswith("https://"):
                    problems.append(f"{where}: url must be https")
                if not re.fullmatch(r"[0-9a-f]{64}", e.sha256 or ""):
                    problems.append(f"{where}: sha256 missing or malformed")
        elif e.path is None or not e.path.exists():
            problems.append(f"{where}: committed file {e.path} is missing")
    return problems


# ── Matching rules ───────────────────────────────────────────────────────────

def fold(text: Optional[str]) -> str:
    """Compatibility-decomposed (ligatures split), accents dropped, casefolded."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def norm_title(text: Optional[str]) -> str:
    """Words only: every run of non-alphanumerics becomes one space. Word
    boundaries are kept on purpose, so run-together text does not match."""
    return re.sub(r"[^0-9a-z]+", " ", fold(text)).strip()


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def family_name(name: str) -> str:
    """The family name of a display name: the part before a comma in
    "Family, Given", else the last word (suffixes such as "Jr." skipped)."""
    folded = fold(name)
    if "," in folded:
        head = folded.split(",", 1)[0]
        words = re.findall(r"[a-z][a-z'\-]*", head)
        return re.sub(r"[^a-z]", "", words[-1]) if words else ""
    words = [w for w in re.findall(r"[a-z][a-z'\-]*", folded) if w.strip("'-") not in _SUFFIXES]
    return re.sub(r"[^a-z]", "", words[-1]) if words else ""


def authors_f1(predicted: Sequence[str], gold: Sequence[str]) -> float:
    """F1 over the multisets of family names. No authors expected and none
    given is a match; no authors expected and some given scores 0."""
    pred = Counter(f for f in (family_name(a) for a in predicted or []) if f)
    want = Counter(f for f in (family_name(a) for a in gold or []) if f)
    if not want:
        return 1.0 if not pred else 0.0
    if not pred:
        return 0.0
    tp = sum((pred & want).values())
    if not tp:
        return 0.0
    precision, recall = tp / sum(pred.values()), tp / sum(want.values())
    return 2 * precision * recall / (precision + recall)


def norm_doi(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".,;")


def norm_arxiv(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)", "", value)
    return re.sub(r"v\d+$", "", value)


def norm_isbn(value: Optional[str]) -> str:
    """ISBN-13 digits (an ISBN-10 is converted), or "" when not a valid ISBN."""
    digits = re.sub(r"[^0-9Xx]", "", value or "").upper()
    if len(digits) == 10:
        if not (digits[:9].isdigit() and (digits[9].isdigit() or digits[9] == "X")):
            return ""
        if (sum((10 - i) * int(d) for i, d in enumerate(digits[:9]))
                + (10 if digits[9] == "X" else int(digits[9]))) % 11:
            return ""
        core = "978" + digits[:9]
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core)) % 10) % 10
        return core + str(check)
    if len(digits) == 13 and digits.isdigit():
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12])) % 10) % 10
        return digits if check == int(digits[12]) else ""
    return ""


NORMALISERS: Dict[str, Callable[[Optional[str]], str]] = {
    "arxiv_id": norm_arxiv, "doi": norm_doi, "isbn": norm_isbn,
}


@dataclass
class FieldScore:
    status: str          # ok | wrong | missing | partial | spurious | none_ok
    expected: Any
    got: Any
    f1: Optional[float] = None
    near: Optional[bool] = None

    @property
    def correct(self) -> bool:
        return self.status in ("ok", "none_ok")


def score_title(got: Optional[str], expected: str, also: Sequence[str] = ()) -> FieldScore:
    if not (got or "").strip():
        return FieldScore("missing", expected, got, near=False)
    targets = {norm_title(t) for t in [expected, *also]}
    pred = norm_title(got)
    if pred in targets:
        return FieldScore("ok", expected, got, near=True)
    near = max(difflib.SequenceMatcher(None, pred, t).ratio() for t in targets) >= TITLE_NEAR
    return FieldScore("wrong", expected, got, near=near)


def score_authors(got: Optional[Sequence[str]], expected: Sequence[str],
                  also: Sequence[Sequence[str]] = ()) -> FieldScore:
    got = list(got or [])
    f1 = max(authors_f1(got, alt) for alt in [expected, *also])
    if f1 == 1.0:
        status = "ok"
    elif not got:
        status = "missing"
    else:
        status = "partial" if f1 > 0 else "wrong"
    return FieldScore(status, expected, got, f1=round(f1, 4))


def score_year(got: Any, expected: int, also: Sequence[int] = ()) -> FieldScore:
    try:
        year = int(got) if got not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    if year is None:
        return FieldScore("missing", expected, got)
    return FieldScore("ok" if year in {expected, *also} else "wrong", expected, year)


def score_identifier(kind: str, got: Optional[str], expected: Optional[str],
                     also: Sequence[str] = ()) -> FieldScore:
    norm = NORMALISERS[kind]
    pred = norm(got) if got else ""
    ok = {norm(v) for v in [expected, *also] if v}
    if expected:
        if not pred:
            return FieldScore("missing", expected, got)
        return FieldScore("ok" if pred in ok else "wrong", expected, got)
    if not pred or pred in ok:
        return FieldScore("none_ok", expected, got)
    return FieldScore("spurious", expected, got)


def score_record(entry: Entry, record: Optional[dict]) -> Dict[str, FieldScore]:
    record = record or {}
    labels, accept = entry.labels, entry.accept
    out = {
        "title": score_title(record.get("title"), labels["title"], accept.get("title", [])),
        "authors": score_authors(record.get("authors"), labels["authors"], accept.get("authors", [])),
        "year": score_year(record.get("year"), labels["year"], accept.get("year", [])),
    }
    for ident in IDENTIFIERS:
        out[ident] = score_identifier(ident, record.get(ident), labels[ident], accept.get(ident, []))
    return out


# ── Failure patterns ─────────────────────────────────────────────────────────

_BOILERPLATE = re.compile(
    r"proceedings|journal|conference|workshop|published|preprint|copyright|licen[cs]e|vol\.|volume|"
    r"university|institute|department|laboratory|association|©|\(c\)|arxiv|https?://|www\.|@|handbook rev|"
    r"page \d|^\d+$|editor|series", re.IGNORECASE)


def title_pattern(score: FieldScore, abstract: Optional[str] = None) -> str:
    if score.status == "missing":
        return "title: none read"
    got, want = score.got or "", score.expected or ""
    squashed = re.sub(r"\s+", "", norm_title(got))
    if squashed and squashed == re.sub(r"\s+", "", norm_title(want)):
        return "title: right letters, spaces lost or added"
    if re.search(r"\S{25,}", got):
        return "title: run-together text (spaces lost)"
    pred, gold = norm_title(got), norm_title(want)
    if pred and pred in gold:
        return "title: only part of the title"
    if gold and gold in pred:
        return "title: the title plus other text"
    if _BOILERPLATE.search(got):
        return "title: a header, venue or affiliation line"
    if len(got.split()) >= 12 or re.search(r"[a-z]\. [A-Z]", got) or (
            abstract and norm_title(got)[:60] in norm_title(abstract)):
        return "title: a sentence of body or abstract text"
    return "title: some other line of the page"


def authors_pattern(score: FieldScore) -> str:
    got, want = list(score.got or []), list(score.expected or [])
    if score.status == "missing":
        return "authors: none read"
    if any(re.search(r"[a-z][A-Z]", n) and len(n.split()) >= 2 and sum(map(str.isupper, n)) >= 3 for n in got):
        return "authors: several names run together as one"
    if not want:
        return "authors: names given where the document credits no person"
    if score.f1 == 0:
        return "authors: none of the right people"
    pred = Counter(family_name(a) for a in got)
    gold = Counter(family_name(a) for a in want)
    if not (pred - gold):
        return "authors: some authors missing"
    if not (gold - pred):
        return "authors: right people plus non-authors"
    return "authors: some right, some wrong"


def year_pattern(score: FieldScore) -> str:
    if score.status == "missing":
        return "year: none read"
    if abs(int(score.got) - int(score.expected)) == 1:
        return "year: off by one"
    return "year: a year from somewhere else"


def failure_patterns(scores: Dict[str, FieldScore], record: Optional[dict], error: Optional[str]) -> List[str]:
    if error:
        reason = re.sub(r"\S+\.(?:pdf|PDF)\b", "the file", error.split(": ", 1)[-1]).split(". ")[0]
        return [f"document: not read ({reason[:90]})"]
    out = []
    if not scores["title"].correct:
        out.append(title_pattern(scores["title"], (record or {}).get("abstract")))
    if not scores["authors"].correct:
        out.append(authors_pattern(scores["authors"]))
    if not scores["year"].correct:
        out.append(year_pattern(scores["year"]))
    for ident in IDENTIFIERS:
        s = scores[ident]
        if s.status == "missing":
            out.append(f"{ident}: printed or known but not recorded")
        elif s.status in ("wrong", "spurious"):
            out.append(f"{ident}: {'a wrong value' if s.status == 'wrong' else 'one recorded that this file does not have'}")
    return out


# ── Network control ──────────────────────────────────────────────────────────

class NetworkBlocked(OSError):
    pass


_LOOPBACK = ("127.", "::1", "localhost")


@dataclass
class HostStats:
    requests: int = 0
    ok: int = 0
    statuses: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    waited: float = 0.0


class Network:
    """Patches httpx and sockets for one pass.

    ``blocked``: every httpx request fails at once with ConnectError and any
    socket to a non-loopback address raises. ``live``: requests go out,
    spaced per host, and responses are recorded. ``replay``: requests are
    answered from a recording, sockets blocked; a request missing from the
    recording fails like a blocked one."""

    def __init__(self, mode: str, recording: Optional[Dict[str, dict]] = None) -> None:
        assert mode in ("blocked", "live", "replay")
        self.mode = mode
        self.recording: Dict[str, dict] = recording if recording is not None else {}
        self.hosts: Dict[str, HostStats] = defaultdict(HostStats)
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.misses = 0

    def _space(self, host: str) -> float:
        delay = next((d for base, d in HOST_DELAY_SECONDS.items()
                      if host == base or host.endswith("." + base)), DEFAULT_HOST_DELAY)
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._last.get(host, 0.0) + delay - now)
            self._last[host] = now + wait
        if wait:
            time.sleep(wait)
        return wait

    @contextmanager
    def active(self) -> Iterator["Network"]:
        import httpx

        original_send = httpx.Client.send
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        net = self

        def guard(address) -> None:
            host = str(address[0]) if isinstance(address, tuple) else str(address)
            if not host.startswith(_LOOPBACK):
                raise NetworkBlocked(f"network blocked by research_bench ({net.mode} pass): {host}")

        def connect(sock, address):
            guard(address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            guard(address)
            return original_connect_ex(sock, address)

        def send(client, request, **kwargs):
            url = str(request.url)
            key = f"{request.method} {url}"
            host = urlparse(url).hostname or ""
            stats = net.hosts[host]
            stats.requests += 1
            if net.mode == "blocked":
                stats.errors["blocked"] += 1
                raise httpx.ConnectError("blocked by research_bench (offline pass)", request=request)
            if net.mode == "replay":
                saved = net.recording.get(key)
                if saved is None:
                    net.misses += 1
                    stats.errors["not in recording"] += 1
                    raise httpx.ConnectError("not in the research_bench recording", request=request)
                if saved.get("error"):
                    stats.errors[saved["error"]] += 1
                    raise httpx.ConnectError(f"recorded failure: {saved['error']}", request=request)
                stats.statuses[saved["status"]] += 1
                stats.ok += saved["status"] == 200
                return httpx.Response(saved["status"], headers={"content-type": saved.get("content_type", "")},
                                      content=base64.b64decode(saved["body"]), request=request)
            stats.waited += net._space(host)
            started = time.perf_counter()
            try:
                response = original_send(client, request, **kwargs)
                body = response.content
            except httpx.HTTPError as exc:
                stats.seconds += time.perf_counter() - started
                stats.errors[type(exc).__name__] += 1
                net.recording[key] = {"error": type(exc).__name__}
                raise
            stats.seconds += time.perf_counter() - started
            stats.statuses[response.status_code] += 1
            stats.ok += response.status_code == 200
            net.recording[key] = {"status": response.status_code,
                                  "content_type": response.headers.get("content-type", ""),
                                  "body": base64.b64encode(body).decode("ascii")}
            return response

        httpx.Client.send = send
        if self.mode != "live":
            socket.socket.connect = connect
            socket.socket.connect_ex = connect_ex
        try:
            yield self
        finally:
            httpx.Client.send = original_send
            socket.socket.connect = original_connect
            socket.socket.connect_ex = original_connect_ex

    def summary(self) -> Dict[str, dict]:
        return {host: {"requests": s.requests, "ok": s.ok, "statuses": dict(s.statuses),
                       "errors": dict(s.errors), "seconds": round(s.seconds, 1), "waited": round(s.waited, 1)}
                for host, s in sorted(self.hosts.items())}


# ── Downloads ────────────────────────────────────────────────────────────────

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_files(cache: Path) -> Path:
    return cache / "files"


def locate(docset: DocSet, cache: Path) -> List[str]:
    """Point each manifest entry at its cached file; return the ids missing."""
    if docset.name != "manifest":
        return [e.id for e in docset.entries if not (e.path and e.path.exists())]
    missing = []
    for e in docset.entries:
        path = cache_files(cache) / f"{e.id}.pdf"
        e.path = path if path.exists() else None
        if e.path is None:
            missing.append(e.id)
    return missing


def download(docset: DocSet, cache: Path, log: Callable[[str], None] = print) -> List[str]:
    """Fetch every missing or mismatched file; build derived ones. Returns
    one line per problem. Files land in their own directory and are only
    ever parsed, never run."""
    import httpx

    target = cache_files(cache)
    target.mkdir(parents=True, exist_ok=True)
    problems: List[str] = []
    net = Network("live")
    with httpx.Client(timeout=120.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for e in docset.entries:
            if e.derive:
                continue
            path = target / f"{e.id}.pdf"
            if path.exists() and sha256_of(path) == e.sha256:
                continue
            host = urlparse(e.url).hostname or ""
            net._space(host)
            log(f"  downloading {e.id} from {host}")
            try:
                response = client.get(e.url)
            except httpx.HTTPError as exc:
                problems.append(f"{e.id}: {type(exc).__name__} {exc}")
                continue
            if response.status_code != 200:
                problems.append(f"{e.id}: HTTP {response.status_code} from {host}")
                continue
            partial = path.with_suffix(".part")
            partial.write_bytes(response.content)
            digest = hashlib.sha256(response.content).hexdigest()
            if digest != e.sha256:
                partial.replace(path.with_suffix(".mismatch"))
                problems.append(f"{e.id}: sha256 {digest[:12]}… does not match the manifest; "
                                f"kept as {path.with_suffix('.mismatch').name}")
                continue
            partial.replace(path)
    for e in docset.entries:
        if e.derive:
            source = target / f"{e.derive['from']}.pdf"
            path = target / f"{e.id}.pdf"
            if not source.exists():
                problems.append(f"{e.id}: source {e.derive['from']} is not cached")
            elif not path.exists():
                rasterize(source, path, int(e.derive.get("rasterize_pages", 3)), int(e.derive.get("dpi", 110)))
                log(f"  derived {e.id} (image-only pages, no text layer)")
    return problems


def rasterize(source: Path, out: Path, pages: int, dpi: int) -> None:
    """The first pages of a PDF as an image-only PDF: a scan with no OCR."""
    import pypdfium2 as pdfium

    from services.pdfium_lock import PDFIUM_LOCK

    images = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(source))
        try:
            for index in range(min(pages, len(pdf))):
                images.append(pdf[index].render(scale=dpi / 72).to_pil().convert("L"))
        finally:
            pdf.close()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=float(dpi))


# ── The run ──────────────────────────────────────────────────────────────────

@dataclass
class DocResult:
    entry: Entry
    document_id: Optional[str] = None
    index_seconds: float = 0.0
    error: Optional[str] = None
    passes: Dict[str, dict] = field(default_factory=dict)   # pass -> {record, scores, seconds, patterns}


@dataclass
class Report:
    docset: str
    version: int
    lookups: str
    results: List[DocResult]
    missing: List[str]
    network: Dict[str, Dict[str, dict]]
    timings: Dict[str, float]
    environment: Dict[str, Any]
    notes: List[str] = field(default_factory=list)

    @property
    def passes(self) -> List[str]:
        return ["offline"] + (["online"] if self.lookups != "off" else [])


RECORD_FIELDS = ("title", "authors", "year", "arxiv_id", "doi", "isbn", "kind", "venue", "abstract")


def _record(paper: dict) -> dict:
    out = {k: paper.get(k) for k in RECORD_FIELDS}
    out["abstract"] = (out.get("abstract") or "")[:300] or None
    out["sources"] = {k: (v or {}).get("source") for k, v in (paper.get("provenance") or {}).items()
                      if k in FIELDS and isinstance(v, dict)}
    out["notes"] = list(paper.get("analysis_notes") or [])
    return out


def recording_path(cache: Path, docset: DocSet) -> Path:
    return cache / f"lookups-{docset.name}-v{docset.version}.json"


def run_benchmark(docset: Optional[DocSet] = None, cache: Path = DEFAULT_CACHE, lookups: str = "live",
                  only: Optional[Sequence[str]] = None, log: Callable[[str], None] = print) -> Report:
    """Index every available document once, analyze it offline and online,
    and score both records."""
    started = time.perf_counter()
    prepare_environment()
    docset = docset or load_manifest()
    missing = locate(docset, cache)
    entries = [e for e in docset.entries if e.path is not None and (not only or e.id in only)]
    notes: List[str] = []

    import config
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from services.research import library, lookup

    settings = config.settings
    if lookups != "off" and not lookup.lookup_available():
        notes.append("Catalogue lookups are unavailable in this environment (OFFLINE_MODE or "
                     "RESEARCH_LOOKUP_ENABLED=false), so the online pass equals the offline one.")

    recording: Dict[str, dict] = {}
    rec_path = recording_path(cache, docset)
    if lookups == "replay":
        if not rec_path.exists():
            raise SystemExit(f"No recording at {rec_path}; run once with --lookups live first.")
        recording = json.loads(rec_path.read_text(encoding="utf-8"))

    timings: Dict[str, float] = {}
    network: Dict[str, Dict[str, dict]] = {}
    results = [DocResult(e) for e in entries]

    # Index everything with the network blocked, as an upload would be.
    t0 = time.perf_counter()
    indexing = Network("blocked")
    with indexing.active():
        collection = collection_service.create_collection("Research benchmark",
                                                          description="scripts/research_bench.py")
        cid = collection["id"]
        indexer = indexer_manager.get_indexer(cid)
        docs_dir = collection_service.get_documents_path(cid)
        for r in results:
            target = docs_dir / r.entry.filename
            shutil.copyfile(r.entry.path, target)
            t = time.perf_counter()
            try:
                meta = indexer.index_document(target, r.entry.filename, collection_id=cid)
                collection_service.add_document(cid, meta.document_id)
                r.document_id = meta.document_id
            except Exception as exc:  # noqa: BLE001 - an unreadable file is a result, not a crash
                r.error = f"{type(exc).__name__}: {exc}"
                target.unlink(missing_ok=True)
            r.index_seconds = time.perf_counter() - t
            log(f"  indexed {r.entry.id:<34} {r.index_seconds:6.1f}s" + (f"  FAILED {r.error[:80]}" if r.error else ""))
        indexer.save_index()
    timings["index"] = time.perf_counter() - t0
    store = library.research_store(indexer)

    for pass_name in (["offline"] + (["online"] if lookups != "off" else [])):
        mode = "blocked" if pass_name == "offline" else lookups
        net = Network(mode, recording)
        lookup.clear_cache()
        t0 = time.perf_counter()
        with net.active():
            for r in results:
                record, seconds, error = None, 0.0, r.error
                if r.document_id:
                    t = time.perf_counter()
                    try:
                        paper = library.analyze_document(cid, indexer, r.document_id, use_registry=True, store=store)
                        record = _record(paper)
                    except Exception as exc:  # noqa: BLE001 - a crash is a result too
                        error = f"{type(exc).__name__}: {exc}"
                    seconds = time.perf_counter() - t
                scores = score_record(r.entry, record)
                r.passes[pass_name] = {"record": record, "scores": scores, "seconds": seconds,
                                       "error": error, "patterns": failure_patterns(scores, record, error)}
                misses = [f for f in FIELDS if not scores[f].correct]
                log(f"  {pass_name:<7} {r.entry.id:<34} {seconds:5.1f}s  "
                    + ("all right" if not misses else "wrong: " + ", ".join(misses)))
        timings[pass_name] = time.perf_counter() - t0
        network[pass_name] = net.summary()
        if pass_name == "online" and mode == "replay" and net.misses:
            notes.append(f"{net.misses} catalogue requests were not in the recording and failed as blocked.")
        if pass_name == "online" and mode == "live":
            rec_path.parent.mkdir(parents=True, exist_ok=True)
            rec_path.write_text(json.dumps(net.recording, indent=0, sort_keys=True), encoding="utf-8")
    timings["total"] = time.perf_counter() - started

    environment = {
        "python": platform.python_version(), "platform": platform.platform(),
        "machine": platform.machine(), "cpus": os.cpu_count(),
        "ocr": bool(settings.enable_ocr), "offline_mode": bool(settings.offline_mode),
        "lookup_timeout_seconds": getattr(settings, "research_lookup_timeout_seconds", None),
    }
    if not settings.enable_ocr:
        notes.append("OCR is off (the default outside the full image), so an image-only scan cannot be read.")
    return Report(docset.name, docset.version, lookups, results, missing, network, timings, environment, notes)


# ── Summaries ────────────────────────────────────────────────────────────────

def field_summary(results: Sequence[DocResult], pass_name: str) -> Dict[str, dict]:
    n = len(results)
    out: Dict[str, dict] = {}
    scores = [r.passes[pass_name]["scores"] for r in results]
    for name in ("title", "year"):
        ok = sum(s[name].correct for s in scores)
        out[name] = {"correct": ok, "of": n}
    out["title_near"] = {"correct": sum(bool(s["title"].near) for s in scores), "of": n}
    f1s = [s["authors"].f1 or 0.0 for s in scores]
    out["authors"] = {"mean_f1": round(sum(f1s) / n, 4) if n else 0.0,
                      "exact": sum(s["authors"].correct for s in scores), "of": n}
    for ident in IDENTIFIERS:
        has = [s[ident] for r, s in zip(results, scores) if r.entry.labels[ident]]
        lacks = [s[ident] for r, s in zip(results, scores) if not r.entry.labels[ident]]
        out[ident] = {"found": sum(x.correct for x in has), "has": len(has),
                      "wrong": sum(x.status == "wrong" for x in has),
                      "spurious": sum(x.status == "spurious" for x in lacks), "lacks": len(lacks)}
    return out


def kind_summary(results: Sequence[DocResult], pass_name: str) -> Dict[str, dict]:
    groups: Dict[str, List[DocResult]] = defaultdict(list)
    for r in results:
        groups[r.entry.kind].append(r)
    return {kind: field_summary(group, pass_name) for kind, group in sorted(groups.items())}


def pattern_summary(results: Sequence[DocResult], pass_name: str) -> List[Tuple[str, int, str]]:
    """(pattern, count, one example) most common first."""
    counts: Counter = Counter()
    example: Dict[str, str] = {}
    for r in results:
        p = r.passes[pass_name]
        for pattern in p["patterns"]:
            counts[pattern] += 1
            if pattern not in example:
                example[pattern] = f"{r.entry.id}: {_miss_text(pattern, p)}"
    return [(pat, n, example[pat]) for pat, n in counts.most_common()]


def _short(value: Any, limit: int = 70) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        text = "; ".join(map(str, value[:4])) + (f"; … ({len(value)} in all)" if len(value) > 4 else "")
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _miss_text(pattern: str, p: dict) -> str:
    if pattern.startswith("document:"):
        return _short(p["error"], 90)
    name = pattern.split(":", 1)[0]
    s: FieldScore = p["scores"][name]
    return f"got “{_short(s.got)}”, want “{_short(s.expected)}”"


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "—"


def render_markdown(report: Report) -> str:
    results = report.results
    lines = [f"# Research metadata benchmark — {report.docset} v{report.version}", ""]
    lines.append(f"{len(results)} documents analyzed"
                 + (f"; {len(report.missing)} in the manifest not cached ({', '.join(report.missing[:6])}"
                    f"{'…' if len(report.missing) > 6 else ''}) — numbers are not comparable until "
                    "every file is present" if report.missing else "")
                 + f". Online pass: lookups {report.lookups}.")
    env = report.environment
    lines.append(f"Python {env['python']} on {env['platform']}, {env['cpus']} CPUs; OCR {'on' if env['ocr'] else 'off'}.")
    lines += [""] + [f"- {n}" for n in report.notes] + ([""] if report.notes else [])

    passes = report.passes
    sums = {p: field_summary(results, p) for p in passes}
    lines += ["## Per-field accuracy", "", "| field | " + " | ".join(passes) + " |",
              "|---|" + "---|" * len(passes)]

    def row(label: str, fn: Callable[[dict], str]) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(sums[p]) for p in passes) + " |")

    row("title (exact after normalising)", lambda s: _pct(s["title"]["correct"], s["title"]["of"]))
    row("title within 0.9 similarity", lambda s: _pct(s["title_near"]["correct"], s["title_near"]["of"]))
    row("authors, mean F1 over family names", lambda s: f"{s['authors']['mean_f1']:.2f}")
    row("authors, whole list right", lambda s: _pct(s["authors"]["exact"], s["authors"]["of"]))
    row("year (exact)", lambda s: _pct(s["year"]["correct"], s["year"]["of"]))
    for ident, label in (("arxiv_id", "arXiv id"), ("doi", "DOI"), ("isbn", "ISBN")):
        row(f"{label} found, of documents that have one",
            lambda s, i=ident: _pct(s[i]["found"], s[i]["has"]) + (f", {s[i]['wrong']} wrong" if s[i]["wrong"] else ""))
        row(f"{label} invented, of documents without one", lambda s, i=ident: _pct(s[i]["spurious"], s[i]["lacks"]))

    lines += ["", "## By kind of document", "",
              "| kind | n | " + " | ".join(f"title {p} | authors F1 {p} | year {p}" for p in passes) + " |",
              "|---|---|" + "---|---|---|" * len(passes)]
    kinds = {p: kind_summary(results, p) for p in passes}
    for kind in sorted(kinds[passes[0]]):
        k0 = kinds[passes[0]][kind]
        cells = []
        for p in passes:
            k = kinds[p][kind]
            cells += [_pct(k["title"]["correct"], k["title"]["of"]), f"{k['authors']['mean_f1']:.2f}",
                      _pct(k["year"]["correct"], k["year"]["of"])]
        lines.append(f"| {kind} | {k0['title']['of']} | " + " | ".join(cells) + " |")

    for p in passes:
        lines += ["", f"## Failure patterns, {p}", "", "| pattern | count | example |", "|---|---|---|"]
        for pattern, n, example in pattern_summary(results, p):
            lines.append(f"| {pattern} | {n} | {example} |")

    for p in passes:
        lines += ["", f"## Misses per document, {p}", "", "| document | kind | misses |", "|---|---|---|"]
        for r in results:
            data = r.passes[p]
            if data["error"]:
                lines.append(f"| {r.entry.id} | {r.entry.kind} | not read: {_short(data['error'], 90)} |")
                continue
            misses = []
            for f in FIELDS:
                s = data["scores"][f]
                if s.correct:
                    continue
                if f == "authors":
                    misses.append(f"**authors** F1 {s.f1:.2f}: got “{_short(s.got, 60)}”")
                elif s.status == "spurious":
                    misses.append(f"**{f}** invented: “{_short(s.got, 40)}”")
                else:
                    misses.append(f"**{f}** got “{_short(s.got, 60)}”, want “{_short(s.expected, 60)}”")
            if misses:
                lines.append(f"| {r.entry.id} | {r.entry.kind} | " + "<br>".join(misses).replace("|", "\\|") + " |")

    if "online" in report.network:
        lines += ["", "## Catalogue requests, online pass", "",
                  "| host | requests | 200 | other answers | failures | seconds | waited (politeness) |",
                  "|---|---|---|---|---|---|---|"]
        for host, s in report.network["online"].items():
            other = ", ".join(f"{k}×{v}" for k, v in s["statuses"].items() if int(k) != 200) or "—"
            errors = ", ".join(f"{k}×{v}" for k, v in s["errors"].items()) or "—"
            lines.append(f"| {host} | {s['requests']} | {s['ok']} | {other} | {errors} | {s['seconds']} | {s['waited']} |")
        offline_requests = sum(s["requests"] for s in report.network.get("offline", {}).values())
        lines.append(f"\nThe offline pass attempted {offline_requests} catalogue requests; all were refused.")

    t = report.timings
    n = max(1, len(results))
    lines += ["", "## Time", "",
              f"Indexing {t.get('index', 0):.0f} s; offline analysis {t.get('offline', 0):.0f} s "
              f"({t.get('offline', 0) / n:.1f} s a document)"
              + (f"; online analysis {t['online']:.0f} s ({t['online'] / n:.1f} s a document)" if "online" in t else "")
              + f"; whole run {t.get('total', 0):.0f} s."]
    return "\n".join(lines) + "\n"


def to_json(report: Report) -> dict:
    def score(s: FieldScore) -> dict:
        return {"status": s.status, "expected": s.expected, "got": s.got, "f1": s.f1, "near": s.near}

    return {
        "set": report.docset, "version": report.version, "lookups": report.lookups,
        "environment": report.environment, "timings": {k: round(v, 2) for k, v in report.timings.items()},
        "notes": report.notes, "missing": report.missing, "network": report.network,
        "fields": {p: field_summary(report.results, p) for p in report.passes},
        "kinds": {p: kind_summary(report.results, p) for p in report.passes},
        "patterns": {p: pattern_summary(report.results, p) for p in report.passes},
        "documents": [{
            "id": r.entry.id, "filename": r.entry.filename, "kind": r.entry.kind,
            "index_seconds": round(r.index_seconds, 2), "error": r.error,
            "passes": {p: {"seconds": round(d["seconds"], 2), "error": d["error"], "record": d["record"],
                           "patterns": d["patterns"], "scores": {f: score(s) for f, s in d["scores"].items()}}
                       for p, d in r.passes.items()},
        } for r in report.results],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", choices=("manifest", "smoke"), default="manifest",
                        help="the labelled real documents (cached) or the committed smoke PDFs")
    parser.add_argument("--download", action="store_true", help="fetch missing manifest files into the cache first")
    parser.add_argument("--cache", type=Path, default=Path(os.environ.get("CLIO_RESEARCH_BENCH_CACHE") or DEFAULT_CACHE),
                        help=f"where downloads and recordings live (default {DEFAULT_CACHE})")
    parser.add_argument("--lookups", choices=("live", "replay", "off"), default="live",
                        help="the online pass: real catalogues (recorded), the recording, or no online pass")
    parser.add_argument("--only", help="comma-separated entry ids")
    parser.add_argument("--json", type=Path, help="write the full results as JSON")
    parser.add_argument("--out", type=Path, help="write the Markdown report to a file as well")
    parser.add_argument("--quiet", action="store_true", help="no per-document progress lines")
    parser.add_argument("--verbose", action="store_true", help="show the app's own log output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log = (lambda _m: None) if args.quiet else (lambda m: print(m, flush=True))

    docset = load_smoke() if args.set == "smoke" else load_manifest()
    problems = validate(docset)
    if problems:
        print("The document set has problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if args.set == "manifest" and args.download:
        log(f"Downloading into {cache_files(args.cache)}")
        for line in download(docset, args.cache, log):
            print("  " + line, file=sys.stderr)
    if args.set == "manifest" and len(locate(docset, args.cache)) == len(docset.entries):
        print(f"No manifest files are cached in {cache_files(args.cache)}; run with --download, or use "
              "--set smoke. Running the smoke set now.", file=sys.stderr)
        docset = load_smoke()

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    report = run_benchmark(docset, cache=args.cache, lookups=args.lookups, only=only, log=log)
    text = render_markdown(report)
    print()
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(to_json(report), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
