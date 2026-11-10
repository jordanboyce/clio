"""The static-site export: what it writes, and what it refuses to publish.

Same harness as tests/test_collection_bundle.py: faked embeddings, a
collection of two text files. The fake model is registered as having a
browser build so the model gate can be passed.
"""

import io
import json
import sqlite3
import zipfile

import numpy as np
import pytest

from services import static_export as se
from services.app_database import app_db
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from tests.test_collection_bundle import SignedEmbedder, _source_collection, env  # noqa: F401
from tests.test_governance import FakeEmbedder

DIM = FakeEmbedder.embedding_dim


@pytest.fixture()
def site(env, monkeypatch):  # noqa: F811
    monkeypatch.setitem(se.BROWSER_MODELS, "fake-embed", {"model": "Xenova/fake-embed", "pooling": "mean"})
    return env


def _public(tmp_path, **kw):
    cid, ids = _source_collection(tmp_path, **kw)
    app_db.update_collection(cid, sensitivity="public")
    return cid, ids


def _audit(action):
    rows = app_db.list_audit_events(action=action)
    for row in rows:
        if isinstance(row.get("detail"), str):
            row["detail"] = json.loads(row["detail"])
    return rows


def _read(out):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    passages = []
    for f in manifest["passages"]["files"]:
        passages += json.loads((out / f["file"]).read_text(encoding="utf-8"))
    v = manifest["vectors"]
    raw = (out / v["file"]).read_bytes()
    dtype = np.int8 if v["quantization"] == "int8" else "<f4"
    vectors = np.frombuffer(raw, dtype=dtype).reshape(v["count"], v["dimension"]).astype(np.float32) / v["scale"]
    return manifest, passages, vectors


def _clio_scores(cid, query):
    """chunk_id -> similarity, from Clio's own vector search."""
    indexer = indexer_manager.get_indexer(cid)
    hits = indexer.vector_store.search(indexer.embedding_service.embed_query(query), top_k=1000)
    return {h.chunk_id: h.similarity_score for h in hits}


def test_a_collection_is_not_public_until_someone_says_so(site, tmp_path):
    cid, _ = _source_collection(site)
    pre = se.preview(cid)
    assert pre["ready"] is False and [b["code"] for b in pre["blockers"]] == ["not_public"]
    assert "labelled Internal" in pre["blockers"][0]["message"]
    with pytest.raises(se.StaticExportError) as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True)
    assert e.value.code == "not_public"
    assert not (tmp_path / "out").exists()


def test_a_public_collection_still_needs_the_confirmation(site, tmp_path):
    cid, _ = _public(site)
    assert se.preview(cid)["ready"] is True
    with pytest.raises(se.StaticExportError) as e:
        se.write_export(cid, tmp_path / "out")
    assert e.value.code == "confirm" and "cleared for public release" in str(e.value)
    assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True


def test_export_writes_three_files_that_agree_with_each_other(site, tmp_path):
    cid, ids = _public(site)
    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True)
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "passages.json", "vectors.bin"]

    assert manifest["format"] == "clio-static-search" and manifest["version"] == 1
    emb = manifest["embedding"]
    assert emb["model"] == "fake-embed" and emb["signature"] == "local::fake-embed"
    assert emb["dimension"] == DIM and emb["pooling"] == "mean" and emb["normalization"] == "l2"
    assert emb["browser"]["model"] == "Xenova/fake-embed"
    assert manifest["vectors"]["quantization"] == "int8"
    assert manifest["vectors"]["bytes"] == manifest["passages"]["count"] * DIM
    assert (out / "vectors.bin").stat().st_size == manifest["vectors"]["bytes"]
    assert {d["id"] for d in manifest["documents"]} == set(ids)
    assert sum(d["passages"] for d in manifest["documents"]) == manifest["passages"]["count"]
    assert detail["passages"] == manifest["passages"]["count"] and detail["confirmed_public"] is True

    _, passages, _ = _read(out)
    assert len(passages) == manifest["passages"]["count"] > 2
    assert set(passages[0]) == {"id", "document_id", "page", "text"}
    # Ordered by source name, then page and position: expenses.txt before holidays.txt.
    names = {d["id"]: d["name"] for d in manifest["documents"]}
    assert [names[p["document_id"]] for p in passages] == sorted(names[p["document_id"]] for p in passages)
    assert "Expenses are reimbursed" in passages[0]["text"]
    # One passage to a line, so a yearly diff is readable.
    lines = (out / "passages.json").read_bytes().split(b"\n")
    assert lines[0] == b"[" and lines[-2] == b"]" and len(lines) == len(passages) + 3


@pytest.mark.parametrize("quantization, tolerance", [("int8", 0.01), ("float32", 1e-6)])
def test_scores_from_the_files_match_clios_own_search(site, tmp_path, quantization, tolerance):
    cid, _ = _public(site)
    out = tmp_path / "out"
    se.write_export(cid, out, confirm_public=True, quantization=quantization)
    manifest, passages, vectors = _read(out)

    for query in ["holiday allowance", "when are expenses paid"]:
        q = np.asarray(FakeEmbedder().embed_query(query), dtype=np.float32)
        q /= np.linalg.norm(q)
        ours = vectors @ q
        theirs = _clio_scores(cid, query)
        assert set(theirs) == {p["id"] for p in passages}
        for passage, score in zip(passages, ours):
            assert abs(float(score) - theirs[passage["id"]]) < tolerance
        best = passages[int(np.argmax(ours))]["id"]
        assert theirs[best] == pytest.approx(max(theirs.values()), abs=tolerance)


def test_the_probe_is_the_models_own_query_vector(site, tmp_path):
    cid, _ = _public(site)
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True)
    probe = manifest["embedding"]["probe"]
    expected = np.asarray(FakeEmbedder().embed_query(probe["text"]), dtype=np.float32)
    expected /= np.linalg.norm(expected)
    assert np.allclose(probe["vector"], expected, atol=1e-5)


def test_export_goes_ahead_without_a_probe_when_the_model_is_not_loaded(site, tmp_path, monkeypatch):
    cid, _ = _public(site)
    indexer_manager.get_indexer(cid)  # opened while the model was there

    def gone(model):
        raise RuntimeError("model not on disk")

    monkeypatch.setattr(indexer_manager, "_get_embedding_service", gone)
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True)
    assert manifest["embedding"]["probe"] is None and manifest["passages"]["count"] > 0


def test_held_and_stricter_sources_are_left_out_and_not_counted_in_public(site, tmp_path):
    cid, (kept, quarantined) = _public(site, quarantine=True)
    docs_dir = collection_service.get_documents_path(cid)
    (docs_dir / "salaries.txt").write_text("Salary bands are confidential. " * 20, encoding="utf-8")
    indexer = indexer_manager.get_indexer(cid)
    internal = indexer.index_document(docs_dir / "salaries.txt", "salaries.txt", collection_id=cid).document_id
    indexer.save_index()
    indexer.vector_store.metadata_store.set_document_governance(internal, sensitivity="confidential")

    pre = se.preview(cid)
    assert pre["ready"] and pre["documents"] == 1
    assert pre["held_excluded"] == 1 and pre["not_public_excluded"] == 1

    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True)
    assert [d["id"] for d in manifest["documents"]] == [kept]
    assert detail["held_excluded"] == 1 and detail["not_public_excluded"] == 1
    published = b"".join(p.read_bytes() for p in out.iterdir())
    for secret in (b"Salary bands", b"reimbursed", quarantined.encode(), internal.encode(), b"held", b"left_out"):
        assert secret not in published
    _, passages, vectors = _read(out)
    assert {p["document_id"] for p in passages} == {kept} and len(vectors) == len(passages)


def test_a_local_only_marking_refuses_the_whole_collection(site, tmp_path):
    cid, ids = _public(site)
    app_db.update_collection(cid, classification="cui")
    pre = se.preview(cid)
    assert pre["blockers"][0]["code"] == "local_only" and "marked CUI" in pre["blockers"][0]["message"]
    with pytest.raises(se.StaticExportError) as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True)
    assert e.value.code == "local_only"

    # One marked source closes the collection too: it is not picked out.
    app_db.update_collection(cid, classification="")
    assert se.preview(cid)["ready"]
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(
        ids[0], classification="proprietary")
    pre = se.preview(cid)
    assert pre["blockers"][0]["code"] == "local_only" and "1 source marked Proprietary" in pre["blockers"][0]["message"]
    with pytest.raises(se.StaticExportError):
        se.write_export(cid, tmp_path / "out", confirm_public=True)
    assert not (tmp_path / "out" / "passages.json").exists()


def test_a_model_with_no_browser_build_is_refused(env, tmp_path):  # noqa: F811
    cid, _ = _public(env)  # "fake-embed" is not registered here
    pre = se.preview(cid)
    assert [b["code"] for b in pre["blockers"]] == ["model"] and pre["browser_model"] is None
    message = pre["blockers"][0]["message"]
    assert "fake-embed" in message and "BAAI/bge-small-en-v1.5" in message and "all-MiniLM-L6-v2" in message


def test_browser_models_are_matched_by_name():
    assert se.browser_model("BAAI/bge-small-en-v1.5")["model"] == "Xenova/bge-small-en-v1.5"
    assert se.browser_model("sentence-transformers/all-MiniLM-L6-v2")["pooling"] == "mean"
    assert se.browser_model("text-embedding-3-small") is None and se.browser_model(None) is None


def test_two_exports_are_byte_identical_and_large_ones_split(site, tmp_path):
    cid, _ = _public(site)
    a, _ = se.write_export(cid, tmp_path / "a", confirm_public=True)
    b, _ = se.write_export(cid, tmp_path / "b", confirm_public=True)
    assert (tmp_path / "a" / "passages.json").read_bytes() == (tmp_path / "b" / "passages.json").read_bytes()
    assert a["vectors"]["sha256"] == b["vectors"]["sha256"]

    split, _ = se.write_export(cid, tmp_path / "c", confirm_public=True, passages_per_file=2)
    files = split["passages"]["files"]
    assert len(files) > 1 and files[0]["file"] == "passages-0001.json"
    assert all(f["count"] <= 2 for f in files) and sum(f["count"] for f in files) == a["passages"]["count"]
    _, whole, v1 = _read(tmp_path / "a")
    _, parts, v2 = _read(tmp_path / "c")
    assert whole == parts and np.array_equal(v1, v2)


def test_vectors_that_are_out_of_step_are_refused(site, tmp_path):
    cid, ids = _public(site)
    indexer = indexer_manager.get_indexer(cid)
    row_ids = indexer.vector_store.metadata_store.get_document_chunk_rowids(ids[0])
    with indexer.vector_store._index_lock:
        indexer.vector_store.index.remove_ids(np.asarray(row_ids[:1], dtype=np.int64))
        indexer.vector_store._dirty = True
    with pytest.raises(se.StaticExportError) as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True)
    assert e.value.code == "vectors" and "1 passage has no vector" in str(e.value)
    assert not list((tmp_path / "out").iterdir())


def test_http_preview_download_and_audit(site, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _source_collection(site)

    pre = client.get(f"/api/collections/{cid}/export/static/preview").json()
    assert pre["ready"] is False and pre["blockers"][0]["code"] == "not_public"
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true")
    assert r.status_code == 400 and "labelled Internal" in r.json()["detail"]

    app_db.update_collection(cid, sensitivity="public")
    pre = client.get(f"/api/collections/{cid}/export/static/preview").json()
    assert pre["ready"] and pre["browser_model"] == "Xenova/fake-embed"
    assert pre["vectors_bytes"] == pre["passages"] * DIM and pre["passages_bytes"] > 0

    assert client.get(f"/api/collections/{cid}/export/static").status_code == 400  # not confirmed
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true")
    assert r.status_code == 200, r.text
    assert "Handbooks.static-search.zip" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["manifest.json", "passages.json", "vectors.bin"]
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["passages"]["count"] == pre["passages"]

    entry = _audit(action="collection.static_export")[0]
    assert entry["collection_id"] == cid and entry["detail"]["confirmed_public"] is True
    assert entry["detail"]["passages"] == pre["passages"]

    assert client.get("/api/collections/nope/export/static/preview").status_code == 404
    assert client.get(f"/api/collections/{cid}/export/static?confirm_public=true&quantization=int4").status_code == 400

    # A local-only marking answers like every other outside service: 403, audited.
    app_db.update_collection(cid, classification="cui")
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "external_service_blocked"
    assert _audit(action="classification.blocked")[0]["collection_id"] == cid


def test_cli_checks_then_replaces_last_years_files(site, tmp_path, capsys):
    from scripts import export_static

    cid, _ = _source_collection(site)
    out = tmp_path / "site" / "search"
    out.mkdir(parents=True)
    (out / "passages-0009.json").write_text("[]", encoding="utf-8")   # left by a larger export
    (out / "index.html").write_text("<html>", encoding="utf-8")       # the site's own file

    # Refused: last year's files stay as they were.
    assert export_static.main(["Handbooks", "--out", str(out), "--confirm-public"]) == 1
    assert "labelled Internal" in capsys.readouterr().err
    assert (out / "passages-0009.json").exists()

    app_db.update_collection(cid, sensitivity="public")
    assert export_static.main(["Handbooks", "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    assert export_static.main(["Handbooks", "--out", str(out)]) == 1  # no confirmation
    assert export_static.main([cid, "--out", str(out), "--confirm-public"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "manifest.json", "passages.json", "vectors.bin"]
    assert _audit(action="collection.static_export")[0]["actor"] == "cli"
