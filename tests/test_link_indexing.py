"""Link indexing: fetching the content behind a URL into a collection.

Three layers are covered. link_fetcher validates URLs (the server makes
the request, so private hosts are refused) and downloads with a size cap
and re-validated redirects. The upload service turns links into bulk-job
work items that save the body into the documents directory and record the
URL as the document's source. The API endpoint sorts a pasted list into a
job plus per-link skips.
"""

import json
import time

import httpx
import pytest

import config
from services import link_fetcher
from services.link_fetcher import (
    FetchedLink,
    LinkError,
    LinkRefused,
    fetch_link,
    suggest_filename,
    validate_link,
)


PUBLIC = {"example.com": ["93.184.216.34"], "docs.example.com": ["2606:2800:220:1:248:1893:25c8:1946"]}
PRIVATE = {"intranet": ["10.0.0.5"], "loop.example.com": ["127.0.0.1"],
           "link.example.com": ["169.254.169.254"], "mapped.example.com": ["::ffff:192.168.1.1"]}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """No real DNS in tests: a fixed table stands in for the resolver."""
    table = {**PUBLIC, **PRIVATE}

    def resolve(host):
        try:
            return table[host]
        except KeyError:
            raise LinkError(f"could not resolve host {host!r}")

    monkeypatch.setattr(link_fetcher, "_resolve_host", resolve)
    monkeypatch.setattr(config.settings, "offline_mode", False)
    monkeypatch.setattr(config.settings, "link_indexing_enabled", True)
    monkeypatch.setattr(config.settings, "link_allow_private_networks", False)


# ── validate_link ──────────────────────────────────────────────────────


def test_scheme_is_added_and_fragment_dropped():
    assert validate_link("example.com/docs#intro") == "https://example.com/docs"
    assert validate_link("  HTTP://Example.com  ") == "http://example.com/"


@pytest.mark.parametrize("bad", ["ftp://example.com/x", "javascript:alert(1)", "file:///etc/passwd"])
def test_non_http_schemes_are_refused(bad):
    with pytest.raises(LinkError):
        validate_link(bad)


def test_credentials_in_url_are_refused():
    with pytest.raises(LinkRefused):
        validate_link("https://user:secret@example.com/")


@pytest.mark.parametrize("host", ["intranet", "loop.example.com", "link.example.com",
                                  "mapped.example.com", "localhost", "printer.local"])
def test_private_and_local_hosts_are_refused(host):
    with pytest.raises(LinkRefused):
        validate_link(f"https://{host}/page")


def test_private_hosts_allowed_when_configured(monkeypatch):
    monkeypatch.setattr(config.settings, "link_allow_private_networks", True)
    assert validate_link("http://intranet/wiki") == "http://intranet/wiki"


def test_unresolvable_host_is_an_error():
    with pytest.raises(LinkError, match="resolve"):
        validate_link("https://nowhere.invalid/")


def test_offline_mode_refuses_links(monkeypatch):
    monkeypatch.setattr(config.settings, "offline_mode", True)
    with pytest.raises(LinkRefused, match="offline"):
        validate_link("https://example.com/")
    assert not link_fetcher.link_indexing_available()


def test_empty_and_whitespace_links_are_errors():
    with pytest.raises(LinkError):
        validate_link("")
    with pytest.raises(LinkError):
        validate_link("https://example.com/a b")


# ── suggest_filename ──────────────────────────────────────────────────


def test_filename_prefers_server_proposal_then_title_then_path():
    assert suggest_filename("https://example.com/x", ".pdf", disposition="Q3 report.pdf") == "Q3 report.pdf"
    assert suggest_filename("https://www.example.com/x", ".html", title="Getting  Started: v2") == \
        "Getting Started v2 (example.com).html"
    assert suggest_filename("https://example.com/guides/setup-guide.PDF", ".pdf") == "setup-guide (example.com).pdf"
    assert suggest_filename("https://example.com/", ".html") == "example.com.html"


def test_filename_extension_follows_content_not_claim():
    # The server said .pdf in the name but sent HTML: the saved copy is HTML.
    assert suggest_filename("https://example.com/x", ".html", disposition="thing.pdf") == "thing.pdf.html"


# ── fetch_link ────────────────────────────────────────────────────────


@pytest.fixture
def transport(monkeypatch):
    """Route link_fetcher's httpx client through an in-memory handler."""
    routes = {}
    real_client = httpx.Client

    def handler(request):
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404, text="missing")
        entry = routes[key]
        return entry(request) if callable(entry) else entry

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(link_fetcher.httpx, "Client", client)
    return routes


PAGE = b"<html><head><title>Hello  World</title></head><body><main><h1>Hi</h1><p>text</p></main></body></html>"


def test_html_page_is_fetched_with_title_filename(transport):
    transport["https://example.com/page"] = httpx.Response(200, content=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    got = fetch_link("example.com/page")
    assert got.extension == ".html"
    assert got.title == "Hello World"
    assert got.filename == "Hello World (example.com).html"
    assert got.body == PAGE
    assert got.final_url == "https://example.com/page"


def test_pdf_link_keeps_pdf_and_disposition_name(transport):
    transport["https://example.com/dl?id=7"] = httpx.Response(
        200, content=b"%PDF-1.4 fake",
        headers={"content-type": "application/pdf", "content-disposition": 'attachment; filename="brief.pdf"'},
    )
    got = fetch_link("https://example.com/dl?id=7")
    assert got.extension == ".pdf"
    assert got.filename == "brief.pdf"


def test_octet_stream_trusts_the_path_extension(transport):
    transport["https://example.com/files/notes.md"] = httpx.Response(
        200, content=b"# notes", headers={"content-type": "application/octet-stream"})
    assert fetch_link("https://example.com/files/notes.md").extension == ".md"


def test_unsupported_content_type_is_refused(transport):
    transport["https://example.com/clip"] = httpx.Response(
        200, content=b"\x00" * 10, headers={"content-type": "video/mp4"})
    with pytest.raises(LinkError, match="unsupported content type"):
        fetch_link("https://example.com/clip")


def test_http_errors_are_reported(transport):
    transport["https://example.com/gone"] = httpx.Response(404, text="no")
    with pytest.raises(LinkError, match="HTTP 404"):
        fetch_link("https://example.com/gone")


def test_redirects_are_followed_and_each_hop_validated(transport):
    transport["https://example.com/old"] = httpx.Response(301, headers={"location": "/new"})
    transport["https://example.com/new"] = httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})
    got = fetch_link("https://example.com/old")
    assert got.final_url == "https://example.com/new"
    assert got.url == "https://example.com/old"

    transport["https://example.com/bounce"] = httpx.Response(302, headers={"location": "http://loop.example.com/admin"})
    with pytest.raises(LinkRefused):
        fetch_link("https://example.com/bounce")


def test_redirect_loops_give_up(transport):
    transport["https://example.com/a"] = httpx.Response(302, headers={"location": "/b"})
    transport["https://example.com/b"] = httpx.Response(302, headers={"location": "/a"})
    with pytest.raises(LinkError, match="too many redirects"):
        fetch_link("https://example.com/a")


def test_size_cap_from_header_and_from_stream(transport, monkeypatch):
    monkeypatch.setattr(config.settings, "link_max_bytes", 100)
    transport["https://example.com/big-declared"] = httpx.Response(
        200, content=b"x" * 10, headers={"content-type": "text/plain", "content-length": "5000000"})
    with pytest.raises(LinkError, match="larger than"):
        fetch_link("https://example.com/big-declared")

    # A chunked body declares no length: only the running count can stop it.
    transport["https://example.com/big-stream"] = httpx.Response(
        200, content=iter([b"x" * 60, b"x" * 60]), headers={"content-type": "text/plain"})
    with pytest.raises(LinkError, match="exceeds"):
        fetch_link("https://example.com/big-stream")


def test_empty_body_is_an_error(transport):
    transport["https://example.com/empty"] = httpx.Response(200, content=b"", headers={"content-type": "text/html"})
    with pytest.raises(LinkError, match="no content"):
        fetch_link("https://example.com/empty")


# ── HTML boilerplate stripping ─────────────────────────────────────────


WEB_PAGE = """<!DOCTYPE html><html><head><title>Install Guide</title></head><body>
<header><a href="/">Acme</a> <nav><a href="/pricing">Pricing</a><a href="/login">Log in</a></nav></header>
<div role="dialog">We use cookies. <button>Accept</button></div>
<main>
  <article>
    <header><h1>Install Guide</h1><p>By Sam, 2026</p></header>
    <p>Run the installer and pick a data directory. The default keeps everything under one folder.</p>
    <h2>Upgrading</h2>
    <p>Stop the service before replacing the binary so the index is not written mid-copy.</p>
  </article>
</main>
<aside>Related: <a href="/x">Other guide</a></aside>
<footer>© Acme · <a href="/privacy">Privacy</a></footer>
</body></html>"""


def test_web_page_navigation_and_footer_are_not_indexed(tmp_path):
    from services.document_extractor import DocumentExtractor
    p = tmp_path / "guide.html"
    p.write_text(WEB_PAGE, encoding="utf-8")
    pages = DocumentExtractor(enable_ocr=False).extract_text(p).page_texts
    joined = "\n".join(pages.values())
    assert "Run the installer" in joined
    assert "Stop the service" in joined
    assert "By Sam" in joined            # the article's own header survives
    assert "Pricing" not in joined
    assert "We use cookies" not in joined
    assert "Other guide" not in joined
    assert "Privacy" not in joined
    assert list(pages) == [1, 2]         # still sectioned on h1/h2


def test_plain_report_without_landmarks_is_untouched(tmp_path):
    from services.document_extractor import DocumentExtractor
    p = tmp_path / "report.html"
    p.write_text("<html><body><h1>Summary</h1><p>All good.</p><p>Really.</p></body></html>", encoding="utf-8")
    pages = DocumentExtractor(enable_ocr=False).extract_text(p).page_texts
    assert "All good." in pages[1] and "Really." in pages[1]


# ── the job ───────────────────────────────────────────────────────────


def test_long_titles_trim_at_a_word():
    name = suggest_filename("https://example.com/x", ".html",
                            title="ipaddress — IPv4/IPv6 manipulation library — Python 3.14 documentation and more words")
    assert name.endswith(" (example.com).html")
    assert "documentati " not in name
    assert len(name) <= 100


SPHINX_PAGE = """<html><head><title>Ref</title></head><body>
<div class="related" role="navigation"><a href="/">index</a></div>
<div class="document"><div class="body" role="main">
<h1>Module ref</h1><p>The module does the thing.</p></div>
<div class="sphinxsidebar" role="navigation">Table of contents</div></div>
<div class="footer">Created using Sphinx. Please donate.</div>
</body></html>"""


def test_role_main_and_class_named_chrome(tmp_path):
    from services.document_extractor import DocumentExtractor
    p = tmp_path / "ref.html"
    p.write_text(SPHINX_PAGE, encoding="utf-8")
    joined = "\n".join(DocumentExtractor(enable_ocr=False).extract_text(p).page_texts.values())
    assert "does the thing" in joined
    assert "Please donate" not in joined
    assert "Table of contents" not in joined
    assert "index" not in joined.split("Module ref")[0]


def test_a_form_holding_the_whole_page_is_kept(tmp_path):
    """ASP.NET WebForms wraps the entire page in one <form>; it is not chrome."""
    from services.document_extractor import DocumentExtractor
    p = tmp_path / "webforms.html"
    p.write_text("""<html><body><form id="form1"><div class="menu"><a href="/">Home</a></div>
    <h1>Policy 12</h1><p>Employees must file the request two weeks ahead of travel.</p>
    <p>Late requests need a director's approval before booking.</p></form></body></html>""", encoding="utf-8")
    joined = "\n".join(DocumentExtractor(enable_ocr=False).extract_text(p).page_texts.values())
    assert "two weeks ahead" in joined
    assert "director's approval" in joined
    assert "Home" not in joined


def _fetched(url, body=PAGE, name="Hello World (example.com).html", ext=".html"):
    return FetchedLink(url=url, final_url=url, content_type="text/html", extension=ext,
                       body=body, filename=name, title="Hello World")


def test_link_job_saves_fetched_copy_and_records_the_url(tmp_path, monkeypatch):
    import services.upload_service as us
    from tests.test_bulk_indexing import FakeEmbedder
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "docs"

    def fake_fetch(url):
        if url.endswith("/broken"):
            raise LinkError("HTTP 500 from " + url)
        return _fetched(url, body=PAGE.replace(b"text", url.encode()))

    monkeypatch.setattr(us, "fetch_link", fake_fetch)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: docs_dir)
    job_updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: job_updates.append(kw))
    added = []
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: added.append(did))

    service = us.UploadService()
    service._cancel_flags[5] = False
    urls = ["https://example.com/one", "https://example.com/broken", "https://example.com/two"]
    service._run_link_index(5, urls, "c1", uploaded_by="sam@example.com")

    final = [u for u in job_updates if u.get("status") == "completed"]
    assert final, job_updates
    summary = json.loads(final[-1]["result_summary"])
    assert summary["documents_processed"] == 2
    assert summary["failed_files"] == [{"filename": "https://example.com/broken", "error": "HTTP 500 from https://example.com/broken"}]

    # Two pages with the same title get distinct saved copies.
    saved = sorted(p.name for p in docs_dir.iterdir())
    assert saved == ["Hello World (example.com).html", "Hello World (example.com)_1.html"]

    by_source = {d["source_path"]: d for d in indexer.list_documents()}
    assert set(by_source) == {"https://example.com/one", "https://example.com/two"}
    for doc in by_source.values():
        assert doc["source_type"] == "url"
        assert doc["uploaded_by"] == "sam@example.com"
    assert len(added) == 2


def test_failed_link_leaves_no_file_behind(tmp_path, monkeypatch):
    """A page that fetches but will not index (unsupported bytes) is cleaned up."""
    import services.upload_service as us
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr(us, "fetch_link",
                        lambda url: _fetched(url, body=b"not a pdf", name="broken.pdf", ext=".pdf"))
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: docs_dir)
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: None)
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: None)

    service = us.UploadService()
    service._cancel_flags[6] = False
    service._run_link_index(6, ["https://example.com/bad.pdf"], "c1")

    assert list(docs_dir.iterdir()) == []
    assert indexer.list_documents() == []


def test_link_job_counts_the_body_against_the_storage_cap(tmp_path, monkeypatch):
    import services.upload_service as us
    from services import storage_quota
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 50)
    monkeypatch.setattr(storage_quota, "usage_bytes", lambda cid: 0)
    monkeypatch.setattr(us, "fetch_link", lambda url: _fetched(url, body=b"<p>" + b"x" * 200 + b"</p>"))
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: docs_dir)
    job_updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: job_updates.append(kw))
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: None)

    service = us.UploadService()
    service._cancel_flags[7] = False
    service._run_link_index(7, ["https://example.com/large"], "c1")

    summary = json.loads([u for u in job_updates if u.get("status") == "completed"][-1]["result_summary"])
    assert summary["documents_processed"] == 0
    assert "storage limit" in summary["failed_files"][0]["error"]
    assert list(docs_dir.iterdir()) == []
    assert storage_quota.reserved_bytes("c1") == 0


# ── the endpoint ──────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_governance import _make_indexer
    import main

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setitem(indexer_manager._indexers, "default", _make_indexer(tmp_path))
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: tmp_path / "docs")
    monkeypatch.setattr(deps, "_initialized", True)
    return TestClient(main.app)


def test_endpoint_refuses_when_offline(client, monkeypatch):
    monkeypatch.setattr(config.settings, "offline_mode", True)
    r = client.post("/documents/index-links", json={"urls": ["https://example.com/"]})
    assert r.status_code == 403
    assert "offline" in r.json()["detail"]
    assert client.get("/api/capabilities").json()["link_indexing"] is False


def test_endpoint_reports_why_every_link_was_skipped(client):
    r = client.post("/documents/index-links", json={"urls": ["http://intranet/", "ftp://example.com/x", ""]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "intranet" in detail and "ftp" in detail


def test_endpoint_starts_a_job_and_lists_the_document(client, monkeypatch):
    import services.upload_service as us
    # A title with an em dash: the served copy's header has to survive it.
    monkeypatch.setattr(us, "fetch_link", lambda url: _fetched(url, name="Hello — World (example.com).html"))

    r = client.post("/documents/index-links", json={
        "urls": ["https://example.com/page", "https://example.com/page#dup", "http://loop.example.com/x"],
    })
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_type"] == "index"
    assert body["total_files"] == 1                    # the fragment twin was folded in
    assert body["skipped_files"] == [{"filename": "http://loop.example.com/x",
                                      "error": "'loop.example.com' resolves to a private or local address; the server only fetches public links"}]

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/documents/upload/{body['job_id']}/status").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job

    docs = client.get("/documents").json()["documents"]
    assert [d["source_type"] for d in docs] == ["url"]
    assert docs[0]["source_path"] == "https://example.com/page"
    assert docs[0]["filename"] == "Hello — World (example.com).html"

    # The saved copy is served as text, never rendered on this origin.
    served = client.get(f"/documents/{docs[0]['document_id']}/pdf")
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("text/plain")
    disposition = served.headers["content-disposition"]
    assert disposition.startswith('inline; filename="Hello  World (example.com).html"')
    assert "filename*=UTF-8''Hello%20%E2%80%94%20World" in disposition


def test_forbidden_explains_automated_access_and_names_the_host(transport):
    transport["https://example.com/blocked"] = httpx.Response(403, text="no")
    with pytest.raises(LinkError, match=r"HTTP 403 from example\.com: .*refused automated access"):
        fetch_link("https://example.com/blocked")


def test_user_agent_carries_a_contact_url_and_can_be_overridden(monkeypatch):
    # Wikimedia answers 403 to a user agent with no URL; the default must have one.
    assert "https://" in link_fetcher.USER_AGENT
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})

    real = httpx.Client
    monkeypatch.setattr(link_fetcher.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    fetch_link("https://example.com/a")
    monkeypatch.setattr(config.settings, "link_user_agent", "AcmeBot/2 (+https://acme.test)")
    fetch_link("https://example.com/a")
    assert seen == [link_fetcher.USER_AGENT, "AcmeBot/2 (+https://acme.test)"]
