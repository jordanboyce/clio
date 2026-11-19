"""A failed upload says why, and what to do next (work order D1)."""

import pytest

from services.upload_failures import explain


@pytest.mark.parametrize("error, reason, fix", [
    (RuntimeError("Failed to extract text from PDF scan.pdf: PDF appears to be image-only and OCR is disabled"),
     "scan with no text", "Turn on OCR"),
    (RuntimeError("Unsupported file type: .xyz"), "cannot read this kind of file", "Custom file types"),
    (RuntimeError("PDF is encrypted with a password"), "password", "without the password"),
    (RuntimeError("Collection storage limit reached"), "storage limit", "Remove sources"),
    (RuntimeError("weird internal failure at /srv/clio/data/x"), "could not be indexed", "Retry"),
])
def test_each_failure_names_a_reason_and_a_fix(error, reason, fix):
    out = explain("f.pdf", error)
    assert out["filename"] == "f.pdf"
    assert reason in out["reason"] and fix in out["fix"]
    # Never a path or a setting name in what a person reads.
    assert "/srv/" not in out["reason"] + out["fix"] and "ENABLE_OCR" not in out["fix"]


def test_the_upload_response_lists_failed_files_with_their_reason(monkeypatch, tmp_path):
    """POST /documents/upload: the scan of the live walk now says why."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import documents as docs_api

    class Indexer:
        vector_store = type("VS", (), {"vectors_pending": False})()

        def index_document(self, path, name, **kw):
            if name == "scan.pdf":
                raise RuntimeError("Failed to extract text from PDF scan.pdf: PDF appears to be image-only and OCR is disabled")
            return type("Meta", (), {"document_id": "d1", "total_pages": 1, "total_chunks": 1})()

        def save_index(self):
            pass

    monkeypatch.setattr(docs_api, "get_indexer", lambda cid: Indexer())
    monkeypatch.setattr(docs_api.collection_service, "get_documents_path", lambda cid: tmp_path)
    monkeypatch.setattr(docs_api.collection_service, "add_document", lambda cid, did: None)
    monkeypatch.setattr(docs_api.governance, "retire_older_versions", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(docs_api.router)
    client = TestClient(app)
    r = client.post("/documents/upload?collection_id=default",
                    files=[("files", ("ok.txt", b"hello world", "text/plain")),
                           ("files", ("scan.pdf", b"%PDF-1.4", "application/pdf"))])
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["failed"] == [{"filename": "scan.pdf",
                               "reason": "It is a scan with no text to read, and reading scans (OCR) is off on this server.",
                               "fix": "Turn on OCR in Settings, Features (or choose a vision model there), then retry."}]
    assert "OCR" in body["message"]


# ── U2: Retry a failed browser upload without asking for the file again ──

@pytest.fixture()
def retry_client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import documents as docs_api

    staging = tmp_path / ".staged"
    staging.mkdir()
    monkeypatch.setattr(docs_api.collection_service, "get_staging_path", lambda cid: staging)
    monkeypatch.setattr(docs_api, "_require_ingest", lambda cid: None)
    seen = {}

    def fake_index(request):
        seen["paths"] = request.file_paths
        return {"job_id": 7, "collection_id": request.collection_id, "status": "pending", "total_files": 1,
                "processed_files": 0}
    monkeypatch.setattr(docs_api, "index_local_files_async", fake_index)
    app = FastAPI()
    app.include_router(docs_api.router)
    return TestClient(app), staging, seen


def test_a_failed_upload_is_retried_from_its_staged_copy(retry_client):
    client, staging, seen = retry_client
    (staging / "tmpabc").mkdir()
    (staging / "tmpabc" / "scan.pdf").write_bytes(b"%PDF-1.4")
    r = client.post("/documents/retry-failed", json={"collection_id": "default", "items": ["tmpabc/scan.pdf"]})
    assert r.status_code == 202, r.text
    assert seen["paths"] == [str((staging / "tmpabc" / "scan.pdf").resolve())]


def test_a_retry_handle_cannot_reach_outside_the_staging_folder(retry_client):
    client, staging, seen = retry_client
    for bad in ["../secret.txt", "a/b/c.pdf", "tmpabc/../../x"]:
        r = client.post("/documents/retry-failed", json={"collection_id": "default", "items": [bad]})
        assert r.status_code in (410, 422), bad
    assert "paths" not in seen


def test_a_file_that_is_gone_says_to_add_it_again(retry_client):
    client, _, _ = retry_client
    r = client.post("/documents/retry-failed", json={"collection_id": "default", "items": ["tmpgone/scan.pdf"]})
    assert r.status_code == 410 and "Add it again" in r.json()["detail"]
