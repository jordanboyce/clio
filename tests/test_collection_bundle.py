"""Collection export/import bundles, and the embedding record they rely on.

Embeddings are faked (deterministic vectors, same harness as
tests/test_governance.py) so no model loads; the fake carries a signature
the way services built by create_embedding_service do.
"""

import io
import json
import sqlite3
import time
import zipfile

import pytest

import config
from services import collection_bundle as cb
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from models.schemas import SearchMode
from tests.test_governance import FakeEmbedder

SIG = "local::fake-embed"


class SignedEmbedder(FakeEmbedder):
    signature = SIG


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # Other test modules reload config, so the bundle module may hold an
    # older Settings object than config.settings: patch every one in play.
    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())
    return tmp_path


def _source_collection(tmp_path, quarantine=False):
    """A collection with two indexed text files; optionally one quarantined."""
    col = collection_service.create_collection(name="Handbooks", embedding_model="fake-embed")
    cid = col["id"]
    indexer = indexer_manager.get_indexer(cid)
    docs_dir = collection_service.get_documents_path(cid)
    ids = []
    for name, text in [("holidays.txt", "Staff accrue 25 days of holiday a year. " * 20),
                       ("expenses.txt", "Expenses are reimbursed within thirty days. " * 20)]:
        path = docs_dir / name
        path.write_text(text, encoding="utf-8")
        ids.append(indexer.index_document(path, name, collection_id=cid).document_id)
    indexer.save_index()
    if quarantine:
        db = collection_service.get_indexes_path(cid) / "metadata.db"
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (ids[1],))
    return cid, ids


def _wait(job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = app_db.get_upload_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_save_records_what_produced_the_vectors(env):
    cid, _ = _source_collection(env)
    info = indexer_manager.get_indexer(cid).vector_store.metadata_store.get_index_info()
    assert info["signature"] == SIG
    assert info["dimension"] == FakeEmbedder.embedding_dim
    assert info["normalization"] == "l2"


def test_export_strips_quarantined_documents_and_records_the_model(env):
    cid, (kept, hidden) = _source_collection(env, quarantine=True)
    path, filename = cb.export_collection(cid, include_sources=True)
    assert filename == "Handbooks.clio.zip"
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = zf.namelist()
        zf.extract("indexes/metadata.db", env / "out")
    assert manifest["embedding"]["signature"] == SIG
    assert manifest["contents"]["documents"] == 1
    assert manifest["contents"]["quarantined_excluded"] == 1
    assert manifest["contents"]["vectors_included"] is True
    assert [d["document_id"] for d in manifest["documents"]] == [kept]
    assert f"sources/{kept}/holidays.txt" in names
    assert not any(hidden in n for n in names)
    with sqlite3.connect(env / "out" / "indexes" / "metadata.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (hidden,)).fetchone()[0] == 0


def test_import_with_matching_model_reuses_vectors(env):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid, include_sources=True)
    result = cb.import_collection(path, owner_id="default", name="Copy")
    assert result["vectors"] == "reused" and result["sources_imported"] == 2
    job = _wait(result["job_id"])
    assert job["status"] == "completed", job
    assert json.loads(job["result_summary"])["vectors"] == "reused"

    new = indexer_manager.get_indexer(result["id"])
    assert new.vector_store.index.ntotal == new.vector_store.metadata_store.get_total_chunks() > 0
    # Fake vectors are hashes of the text, so a chunk's own text must find
    # that exact chunk first - proof each reused vector kept its chunk.
    chunk = new.vector_store.metadata_store.get_all_chunks_ordered()[-1]
    hits = new.search(chunk["text"], top_k=1, mode=SearchMode.SEMANTIC)["results"]
    assert hits[0].text_snippet.startswith(chunk["text"][:40])
    assert (collection_service.get_documents_path(result["id"]) / "holidays.txt").exists()


def test_import_with_another_model_re_embeds_from_chunk_text(env, monkeypatch):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)  # no originals
    import services.embedder as embedder
    monkeypatch.setattr(embedder, "service_signature", lambda **kw: "openrouter::x::other")
    result = cb.import_collection(path, owner_id="default")
    assert result["vectors"] == "re-embed" and result["sources_imported"] == 0
    job = _wait(result["job_id"])
    assert job["status"] == "completed", job
    assert json.loads(job["result_summary"])["vectors"] == "re-embedded"
    new = indexer_manager.get_indexer(result["id"])
    assert new.vector_store.index.ntotal == new.vector_store.metadata_store.get_total_chunks()
    chunk = new.vector_store.metadata_store.get_all_chunks_ordered()[0]
    hits = new.search(chunk["text"], top_k=1, mode=SearchMode.SEMANTIC)["results"]
    assert hits[0].text_snippet.startswith(chunk["text"][:40])


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize("entries, message", [
    ({"manifest.json": "{}"}, "manifest.json or metadata.db is missing"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 99}),
      "indexes/metadata.db": b""}, "newer Clio"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 1}),
      "indexes/metadata.db": b"", "sources/../../evil.py": b""}, "unsafe path"),
    ({"manifest.json": json.dumps({"format": "clio-collection", "version": 1}),
      "indexes/metadata.db": b"", "main.py": b""}, "unexpected file"),
])
def test_bad_bundles_are_refused_before_anything_is_created(env, entries, message):
    path = env / "bad.zip"
    path.write_bytes(_zip(entries))
    before = len(app_db.get_all_collections())
    with pytest.raises(cb.BundleError, match=message):
        cb.import_collection(path, owner_id="default")
    assert len(app_db.get_all_collections()) == before


def test_not_a_zip_is_refused(env):
    path = env / "notes.txt"
    path.write_text("hello")
    with pytest.raises(cb.BundleError, match="not a Clio collection export"):
        cb.read_manifest(path)


def test_http_round_trip(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)

    r = client.get(f"/api/collections/{cid}/export", params={"include_sources": "true"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "Handbooks.clio.zip" in r.headers["content-disposition"]
    assert not list((env / "tmp").glob("*.zip"))  # removed after sending

    r = client.post("/api/collections/import", data={"name": "Shared handbooks"},
                    files={"file": ("Handbooks.clio.zip", r.content, "application/zip")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Shared handbooks" and body["vectors"] == "reused"
    assert _wait(body["job_id"])["status"] == "completed"

    r = client.post("/api/collections/import",
                    files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400 and "not a Clio collection export" in r.json()["detail"]


# ── Saying which model indexed it, and previewing before importing ───────────


def test_manifest_says_which_model_in_words(env):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    emb = manifest["embedding"]
    assert emb["signature"] == SIG and emb["recorded"] is True
    assert emb["label"] == f"fake-embed · on this machine · {FakeEmbedder.embedding_dim} dimensions"
    assert manifest["clio_version"]


def test_describe_model_falls_back_to_document_records_and_admits_it():
    info = cb.describe_model({}, [{"embedding_model": "old-model"}, {"embedding_model": "old-model"}])
    assert info["model"] == "old-model" and info["recorded"] is False
    assert cb.describe_model({}, [])["label"] == "unknown model"
    remote = cb.describe_model({"signature": "openrouter::https://x::text-embed", "dimension": 1536})
    assert remote["model"] == "text-embed" and remote["label"] == "text-embed · via openrouter · 1536 dimensions"


def test_export_preview_matches_what_export_builds(env):
    cid, _ = _source_collection(env, quarantine=True)
    preview = cb.export_preview(cid)
    assert preview["documents"] == 1 and preview["quarantined_excluded"] == 1
    assert preview["embedding"]["model"] == "fake-embed" and preview["vectors_included"] is True
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        contents = json.loads(zf.read("manifest.json"))["contents"]
    assert (preview["documents"], preview["chunks"]) == (contents["documents"], contents["chunks"])


def test_inspect_says_reuse_for_the_same_model_and_re_embed_otherwise(env, monkeypatch):
    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)
    before = len(app_db.get_all_collections())

    same = cb.inspect_bundle(path)
    assert same["action"] == "reuse" and "reused" in same["reason"]
    assert same["name"] == "Handbooks"
    assert same["embedding"]["model"] == "fake-embed" and same["documents"] == 2

    import services.embedder as embedder
    monkeypatch.setattr(embedder, "service_signature", lambda **kw: "openrouter::x::other")
    other = cb.inspect_bundle(path)
    assert other["action"] == "re-embed" and "different model" in other["reason"]
    assert len(app_db.get_all_collections()) == before  # inspecting creates nothing


def test_inspect_reports_documents_this_server_has_blocklisted(env):
    cid, ids = _source_collection(env)
    path, _ = cb.export_collection(cid)
    with zipfile.ZipFile(path) as zf:
        content_hash = json.loads(zf.read("manifest.json"))["documents"][0]["content_hash"]
    app_db.add_blocked_hash(content_hash, "admin", "test", "x.txt")
    assert cb.inspect_bundle(path)["blocked_here"] == 1


def test_http_preview_then_confirm_without_uploading_twice(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)

    pre = client.get(f"/api/collections/{cid}/export/preview").json()
    assert pre["embedding"]["label"].startswith("fake-embed") and pre["documents"] == 2

    bundle = client.get(f"/api/collections/{cid}/export").content
    r = client.post("/api/collections/import/preview", files={"file": ("h.clio.zip", bundle, "application/zip")})
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["action"] == "reuse" and len(summary["upload_id"]) == 32
    staged = env / "tmp" / "staged" / f"{summary['upload_id']}.zip"
    assert staged.exists()

    r = client.post("/api/collections/import", data={"upload_id": summary["upload_id"], "name": "From preview"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "From preview"
    assert _wait(r.json()["job_id"])["status"] == "completed"
    assert not staged.exists()  # consumed

    # An expired or invented id is refused plainly; so is "nothing at all".
    r = client.post("/api/collections/import", data={"upload_id": summary["upload_id"]})
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    r = client.post("/api/collections/import", data={"upload_id": "../../etc/passwd"})
    assert r.status_code == 400
    assert client.post("/api/collections/import").status_code == 400


def test_preview_refuses_a_bad_bundle_and_keeps_nothing(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    r = TestClient(main.app).post("/api/collections/import/preview",
                                  files={"file": ("x.zip", b"nope", "application/zip")})
    assert r.status_code == 400
    assert not list((env / "tmp").glob("**/*.zip"))


def test_discard_removes_the_staged_bundle_and_sweep_removes_stale_ones(env, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main
    import os

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(env)
    bundle = client.get(f"/api/collections/{cid}/export").content
    uid = client.post("/api/collections/import/preview",
                      files={"file": ("h.zip", bundle, "application/zip")}).json()["upload_id"]
    assert client.delete(f"/api/collections/import/preview/{uid}").status_code == 200
    assert not (env / "tmp" / "staged" / f"{uid}.zip").exists()

    stale = cb._staged_dir() / ("a" * 32 + ".zip")
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))
    cb.sweep_staged()
    assert not stale.exists()
