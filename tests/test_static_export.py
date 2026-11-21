"""The static-site export: what it writes, and what it refuses to publish.

Same harness as tests/test_collection_bundle.py: faked embeddings, a
collection of two text files. The fake model is registered as having a
browser build so the model gate can be passed.
"""

import hashlib
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


def test_a_model_with_no_browser_build_is_exported_for_keyword_search(env, tmp_path):  # noqa: F811
    """Any collection that may be published can be: one whose model has no
    browser build is searched by keyword on the site instead of refused."""
    cid, ids = _public(env)  # "fake-embed" is not registered here
    pre = se.preview(cid)
    assert pre["ready"] is True and pre["blockers"] == [] and pre["browser_model"] is None
    assert pre["search"] == "keywords" and pre["vectors_bytes"] == 0
    reason = pre["keywords_reason"]
    assert "fake-embed" in reason and "BAAI/bge-small-en-v1.5" in reason and "all-MiniLM-L6-v2" in reason
    assert pre["site"]["available"] is True and pre["site"]["model"] is None

    out = tmp_path / "out"
    out.mkdir()
    (out / "vectors.bin").write_bytes(b"last year")   # would not match the new passages
    manifest, detail = se.write_export(cid, out, confirm_public=True)
    assert manifest["version"] == se.KEYWORD_VERSION and manifest["vectors"] is None
    assert manifest["embedding"]["search"] == "keywords" and manifest["embedding"]["reason"] == reason
    assert manifest["embedding"]["browser"] is None and manifest["embedding"]["probe"] is None
    passages = json.loads((out / "passages.json").read_text(encoding="utf-8"))
    assert manifest["passages"]["count"] == len(passages) > 0
    assert not (out / "vectors.bin").exists()
    assert detail["search"] == "keywords" and detail["vectors_sha256"] is None

    zip_path, _, _ = se.export_zip(cid, confirm_public=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert sorted(zf.namelist()) == ["manifest.json", "passages.json"]
    finally:
        zip_path.unlink()
    # A whole keyword site: the page, the element and the server settings, no model.
    site_out = tmp_path / "site"
    se.write_export(cid, site_out, confirm_public=True, site=True)
    files = sorted(p.relative_to(site_out).as_posix() for p in site_out.rglob("*") if p.is_file())
    assert files == sorted([*se.SITE_PAGE_FILES, *se.SITE_HOSTING_FILES,
                            "search/manifest.json", "search/passages.json"])


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


# ── A whole site, with the model and its runtime ─────────────────────────────


def _fake_runtime(root):
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    for name in se.SITE_RUNTIME_FILES:
        (vendor / name).write_bytes(b"runtime:" + name.encode())
    (vendor / "LICENSE.onnxruntime-web.txt").write_text("MIT", encoding="utf-8")
    (vendor / "runtime.json").write_text(json.dumps({"packages": {
        "@huggingface/transformers": {"version": "4.3.2", "license": "Apache-2.0"}}}), encoding="utf-8")
    return vendor


def _fake_model(root):
    folder = root / "snapshot"
    folder.mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    (folder / "model_optimized.onnx").write_bytes(b"onnx" * 10)
    files = {n: folder / n for n in ("config.json", "tokenizer.json", "tokenizer_config.json")}
    files["onnx/model.onnx"] = folder / "model_optimized.onnx"
    return {"files": files, "source": "Qdrant/fake-embed-onnx", "license": "mit",
            "bytes": sum(p.stat().st_size for p in files.values())}


@pytest.fixture()
def kit(site, tmp_path, monkeypatch):
    vendor = _fake_runtime(tmp_path / "runtime")
    model = _fake_model(tmp_path / "model")
    monkeypatch.setattr(se, "site_runtime_dirs", lambda: [tmp_path / "nowhere", vendor])
    monkeypatch.setattr(se, "site_model", lambda name: model if name == "fake-embed" else None)
    return model


def test_the_preview_says_whether_a_whole_site_can_be_made(site, tmp_path, monkeypatch):
    cid, _ = _public(site)
    monkeypatch.setattr(se, "site_runtime_dirs", lambda: [tmp_path / "nowhere"])
    offer = se.preview(cid)["site"]
    assert offer["available"] is False and "no copy of the browser runtime" in offer["reason"]
    with pytest.raises(se.StaticExportError) as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True, site=True)
    assert e.value.code == "site"
    assert not (tmp_path / "out").exists()

    monkeypatch.setattr(se, "site_runtime_dirs", lambda: [_fake_runtime(tmp_path / "rt")])
    monkeypatch.setattr(se, "site_model", lambda name: None)
    offer = se.preview(cid)["site"]
    assert offer["available"] is False and "not on this server's disk" in offer["reason"]


def test_a_whole_site_carries_the_page_the_runtime_and_clios_own_model(kit, site, tmp_path):
    cid, _ = _public(site)
    offer = se.preview(cid)["site"]
    assert offer["available"] is True and offer["model"] == "fake-embed"
    assert offer["bytes"] == offer["model_bytes"] + offer["runtime_bytes"] and offer["model_bytes"] == kit["bytes"]

    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True, site=True)
    assert detail["site"] is True
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == sorted([
        "index.html", "clio-page.js", "clio-common.js", "clio-static-search.js", "clio-search.js", "clio-related.js", "clio-answers.js", "clio-cite.js", "clio-table.js", "clio-offline.js", "clio-transcript.js",
        "_headers", ".htaccess", "web.config", "README.txt",
        "search/manifest.json", "search/passages.json", "search/vectors.bin",
        "vendor/LICENSE.onnxruntime-web.txt", "vendor/runtime.json", *[f"vendor/{n}" for n in se.SITE_RUNTIME_FILES],
        "models/fake-embed/config.json", "models/fake-embed/tokenizer.json",
        "models/fake-embed/tokenizer_config.json", "models/fake-embed/onnx/model.onnx",
        "models/fake-embed/SOURCE.txt",
    ])
    # The ONNX file is the one Clio embeds with, whatever fastembed named it.
    assert (out / "models/fake-embed/onnx/model.onnx").read_bytes() == b"onnx" * 10
    assert "Qdrant/fake-embed-onnx" in (out / "models/fake-embed/SOURCE.txt").read_text(encoding="utf-8")

    bundled = manifest["embedding"]["browser"]["bundled"]
    assert bundled["model"] == "fake-embed" and bundled["source"] == "Qdrant/fake-embed-onnx"
    assert bundled["sha256"] == hashlib.sha256(b"onnx" * 10).hexdigest()
    assert bundled["runtime"]["@huggingface/transformers"]["version"] == "4.3.2"
    assert json.loads((out / "search/manifest.json").read_text(encoding="utf-8")) == manifest
    # The data files are the same as a plain export's.
    plain, _ = se.write_export(cid, tmp_path / "plain", confirm_public=True)
    assert "bundled" not in plain["embedding"]["browser"]
    assert (tmp_path / "plain/vectors.bin").read_bytes() == (out / "search/vectors.bin").read_bytes()
    # The page it ships knows how to load what it ships.
    assert "clio-search.js" in (out / "index.html").read_text(encoding="utf-8")
    assert "localTransformers" in (out / "clio-search.js").read_text(encoding="utf-8")
    # IIS serves no file it has no type for: the settings name all three.
    config = (out / "web.config").read_text(encoding="utf-8")
    assert all(f'fileExtension="{ext}"' in config for ext in (".bin", ".onnx", ".wasm"))


def test_the_site_downloads_as_one_zip_over_http(kit, site, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    cid, _ = _public(site)
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true&site=true")
    assert r.status_code == 200, r.text
    assert "Handbooks.static-site.zip" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("search/manifest.json"))
    assert "index.html" in names and "models/fake-embed/onnx/model.onnx" in names
    assert "vendor/transformers.min.js" in names and manifest["embedding"]["browser"]["bundled"]
    assert _audit(action="collection.static_export")[0]["detail"]["site"] is True


def test_cli_writes_a_site_and_leaves_the_folders_other_files(kit, site, tmp_path, capsys):
    from scripts import export_static

    cid, _ = _public(site)
    out = tmp_path / "public"
    (out / "search").mkdir(parents=True)
    (out / "search" / "passages-0009.json").write_text("[]", encoding="utf-8")
    (out / "about.html").write_text("<html>", encoding="utf-8")
    assert export_static.main([cid, "--out", str(out), "--site", "--confirm-public"]) == 0
    assert "serve" in capsys.readouterr().out
    assert (out / "about.html").exists() and not (out / "search" / "passages-0009.json").exists()
    assert (out / "index.html").exists() and (out / "models/fake-embed/onnx/model.onnx").exists()
    assert not (out / ".clio-static-export").exists()


def test_site_model_finds_fastembeds_copy_and_its_onnx_file(env, tmp_path, monkeypatch):  # noqa: F811
    pytest.importorskip("fastembed")
    from services import embedder

    cache = tmp_path / "fastembed"
    snapshot = cache / "models--Qdrant--bge-small-en-v1.5-onnx-Q" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"):
        (snapshot / name).write_text("{}", encoding="utf-8")
    (snapshot / "model_optimized.onnx").write_bytes(b"q" * 100)
    monkeypatch.setattr(embedder, "fastembed_cached_in", lambda name: cache)

    found = se.site_model("BAAI/bge-small-en-v1.5")
    assert found["source"] == "Qdrant/bge-small-en-v1.5-onnx-Q" and found["license"] == "mit"
    assert found["files"]["onnx/model.onnx"] == snapshot / "model_optimized.onnx"
    assert set(found["files"]) == {"config.json", "tokenizer.json", "tokenizer_config.json",
                                   "special_tokens_map.json", "onnx/model.onnx"}

    monkeypatch.setattr(embedder, "fastembed_cached_in", lambda name: None)
    assert se.site_model("BAAI/bge-small-en-v1.5") is None


# ── An asset for a website: where each source links ────────────────────────


@pytest.mark.parametrize("address", [
    "https://example.org/handbook/leave.pdf", "http://intranet.example/x?y=1#z", "/files/handbook/",
])
def test_an_address_a_browser_can_open_is_accepted(address):
    assert se.clean_public_url(f"  {address} ") == address


@pytest.mark.parametrize("address", [
    "javascript:alert(1)", "file:///C:/handbook.pdf", "C:\\docs\\a.pdf", "//evil.example/x",
    "https://user:secret@example.org/a.pdf", "https://example.org/a b.pdf", "ftp://example.org/a.pdf",
    "https://example.org/" + "a" * 2100,
])
def test_anything_else_is_refused(address):
    with pytest.raises(se.StaticExportError) as e:
        se.clean_public_url(address)
    assert e.value.code == "address"


def test_empty_means_no_address():
    assert se.clean_public_url("") is None and se.clean_public_url(None) is None


@pytest.mark.parametrize("remote, expected", [
    ("git@github.com:acme/handbook.git", "https://github.com/acme/handbook/blob/abc1234/docs/a%20b.md#L3-L9"),
    ("https://token:x@gitlab.com/group/sub/handbook.git",
     "https://gitlab.com/group/sub/handbook/-/blob/abc1234/docs/a%20b.md#L3-9"),
    ("ssh://git@bitbucket.org/acme/handbook", "https://bitbucket.org/acme/handbook/src/abc1234/docs/a%20b.md#lines-3:9"),
    ("https://codeberg.org/acme/handbook.git",
     "https://codeberg.org/acme/handbook/src/commit/abc1234/docs/a%20b.md#L3-L9"),
])
def test_a_file_from_a_public_repository_links_to_its_lines(remote, expected):
    link = se.repository_file_link(remote, "abc1234", "docs/a b.md")
    assert link["url"] + link["lines"].format(start=3, end=9) == expected
    assert "token" not in link["url"]


@pytest.mark.parametrize("remote, commit", [
    ("https://git.example.internal/acme/handbook.git", "abc1234"),   # a host Clio cannot speak for
    ("git@github.com:acme/handbook.git", None),
    ("git@github.com:acme/handbook.git", "not-a-commit"),
    ("https://github.com/handbook", "abc1234"),                       # no owner
])
def test_no_repository_link_without_a_known_host_and_commit(remote, commit):
    assert se.repository_file_link(remote, commit, "a.md") is None


def _rows(cid):
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return {r["filename"]: dict(r) for r in conn.execute("SELECT * FROM documents")}


def test_each_source_carries_where_it_lives_on_the_web(site, tmp_path):
    cid, ids = _public(site)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    indexer_manager.get_indexer(cid)  # brings the database up to date (public_url)
    with sqlite3.connect(db) as conn:
        # One source came from a web page.
        conn.execute("UPDATE documents SET source_type='url', source_path='https://example.org/expenses' "
                     "WHERE document_id=?", (ids[1],))
    pre = se.preview(cid)
    assert pre["addresses"] == {"with_address": 1, "total": 2, "files_base_url": None}

    saved = se.save_addresses(cid, files_base_url="https://example.org/files/")
    assert saved["files_base_url"] == "https://example.org/files/"
    manifest, detail = se.write_export(cid, tmp_path / "out", confirm_public=True)
    by_name = {d["name"]: d for d in manifest["documents"]}
    assert by_name["holidays.txt"]["link"] == {"url": "https://example.org/files/holidays.txt", "from": "files",
                                              "text": True}
    assert by_name["holidays.txt"]["title"] == "holidays"
    assert by_name["expenses.txt"]["link"] == {"url": "https://example.org/expenses", "from": "link", "text": True}
    assert detail["linked_documents"] == 2

    # A source's own address wins, and an export can override the base for itself.
    se.save_addresses(cid, documents={ids[0]: "https://example.org/policies/holidays.html"})
    assert _rows(cid)["holidays.txt"]["public_url"] == "https://example.org/policies/holidays.html"
    manifest, _ = se.write_export(cid, tmp_path / "out2", confirm_public=True, files_base_url="")
    by_name = {d["name"]: d for d in manifest["documents"]}
    assert by_name["holidays.txt"]["link"]["from"] == "own"

    # Clearing it falls back to nothing, since the override cleared the base too.
    se.save_addresses(cid, documents={ids[0]: None}, files_base_url=None)
    manifest, _ = se.write_export(cid, tmp_path / "out3", confirm_public=True)
    assert "link" not in {d["name"]: d for d in manifest["documents"]}["holidays.txt"]


def test_a_bad_address_saves_nothing(site):
    cid, ids = _public(site)
    with pytest.raises(se.StaticExportError):
        se.save_addresses(cid, files_base_url="https://example.org/files/",
                          documents={ids[0]: "https://example.org/ok", ids[1]: "javascript:alert(1)"})
    assert se.address_settings(cid)["files_base_url"] is None
    assert _rows(cid)["holidays.txt"]["public_url"] is None
    with pytest.raises(se.StaticExportError, match="no source"):
        se.save_addresses(cid, documents={"nope": "https://example.org/x"})


def test_a_pdf_opens_at_the_page_and_a_repository_file_at_its_lines(site, tmp_path, monkeypatch):
    cid, ids = _public(site)
    indexer_manager.get_indexer(cid)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    root = tmp_path / "checkout"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET filename='guide.pdf', public_url='https://example.org/guide.pdf' "
                     "WHERE document_id=?", (ids[0],))
        conn.execute("UPDATE documents SET source_type='folder_sync', source_path=? WHERE document_id=?",
                     (str(root / "docs" / "expenses.txt"), ids[1]))
        conn.execute("UPDATE chunks SET line_start=10, line_end=24 WHERE document_id=?", (ids[1],))
    monkeypatch.setattr(se, "_repositories", lambda _cid: [
        {"root": root, "remote": "git@github.com:acme/handbook.git", "commit": "abc1234"}])

    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True)
    by_name = {d["name"]: d for d in manifest["documents"]}
    assert by_name["guide.pdf"]["link"] == {"url": "https://example.org/guide.pdf", "from": "own",
                                           "page": "#page={page}"}
    assert by_name["expenses.txt"]["link"] == {
        "url": "https://github.com/acme/handbook/blob/abc1234/docs/expenses.txt", "from": "repository",
        "lines": "#L{start}-L{end}"}
    assert "lines" in manifest["passages"]["fields"]
    passages = json.loads((tmp_path / "out" / "passages.json").read_text(encoding="utf-8"))
    assert {tuple(p["lines"]) for p in passages if p["document_id"] == ids[1]} == {(10, 24)}
    assert all("lines" not in p for p in passages if p["document_id"] == ids[0])


def test_where_a_source_is_published_survives_a_reindex(site):
    from services.reindex_service import ReindexService

    cid, ids = _public(site)
    store = indexer_manager.get_indexer(cid).vector_store.metadata_store
    assert store.set_document_public_url(ids[0], "https://example.org/holidays") is True
    prior = store.get_document_info(ids[0])
    assert prior["public_url"] == "https://example.org/holidays"
    store.set_document_public_url(ids[0], None)
    prepared = type("Prepared", (), {"document_id": ids[0], "policy_status": "clear"})()
    ReindexService._carry_over_review(store, prepared, prior)
    assert store.get_document_info(ids[0])["public_url"] == "https://example.org/holidays"


def test_an_imported_bundle_keeps_only_addresses_a_browser_may_open(tmp_path):
    from services.collection_bundle import _settle_source_types

    conn = sqlite3.connect(tmp_path / "m.db")
    conn.execute("CREATE TABLE documents (document_id TEXT, source_type TEXT, source_path TEXT, public_url TEXT)")
    conn.executemany("INSERT INTO documents VALUES (?, 'upload', NULL, ?)", [
        ("a", "https://example.org/a.pdf"), ("b", "javascript:alert(1)"), ("c", "/files/c.pdf"),
        ("d", "//evil.example/d"), ("e", None)])
    _settle_source_types(conn)
    kept = dict(conn.execute("SELECT document_id, public_url FROM documents"))
    assert kept == {"a": "https://example.org/a.pdf", "b": None, "c": "/files/c.pdf", "d": None, "e": None}


def test_http_addresses_owner_reads_and_saves_them_with_an_audit_row(site, monkeypatch):
    from fastapi.testclient import TestClient

    from api import deps
    import main

    monkeypatch.setattr(deps, "_initialized", True)
    cid, ids = _public(site)
    client = TestClient(main.app)
    got = client.get(f"/api/collections/{cid}/export/static/addresses")
    assert got.status_code == 200 and got.json()["total"] == 2 and got.json()["with_address"] == 0

    bad = client.put(f"/api/collections/{cid}/export/static/addresses", json={"files_base_url": "ftp://x/y"})
    assert bad.status_code == 400 and "https://" in bad.json()["detail"]

    put = client.put(f"/api/collections/{cid}/export/static/addresses",
                     json={"files_base_url": "/files/handbook/", "documents": {ids[1]: "https://example.org/e"}})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["files_base_url"] == "/files/handbook/" and body["with_address"] == 2
    rows = {r["name"]: r for r in body["documents"]}
    assert rows["holidays.txt"]["url"] == "/files/handbook/holidays.txt" and rows["holidays.txt"]["from"] == "files"
    assert rows["expenses.txt"]["from"] == "own"
    # Leaving files_base_url out keeps it.
    again = client.put(f"/api/collections/{cid}/export/static/addresses", json={"documents": {ids[1]: None}})
    assert again.json()["files_base_url"] == "/files/handbook/"
    audit_rows = _audit(action="collection.static_addresses")
    assert len(audit_rows) == 2 and audit_rows[0]["collection_id"] == cid


def test_cli_links_results_to_the_sites_copies_of_the_files(site, tmp_path):
    from scripts import export_static

    cid, _ = _public(site)
    out = tmp_path / "public" / "search"
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--files-url", "/files/handbook/"]) == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert {d["link"]["url"] for d in manifest["documents"]} == {
        "/files/handbook/holidays.txt", "/files/handbook/expenses.txt"}
    # For that export only: nothing was saved.
    assert se.address_settings(cid)["files_base_url"] is None
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--files-url", "javascript:x"]) == 1


def test_a_source_is_named_by_its_library_title_only_where_the_library_is_sure(site, tmp_path):
    from services.research.store import ResearchStore

    cid, ids = _public(site)
    store = ResearchStore(collection_service.get_indexes_path(cid) / "metadata.db")
    store.upsert_paper(ids[0], title="Holiday Entitlement for Staff", analyzed_at="2026-11-18T00:00:00",
                       provenance={"title": {"source": "user"}})
    store.upsert_paper(ids[1], title="reimbursed within thirty days. Expenses are", analyzed_at="2026-11-18T00:00:00",
                       provenance={"title": {"source": "paper", "confidence": "low", "check": "starts mid-sentence"}})
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True)
    titles = {d["name"]: d["title"] for d in manifest["documents"]}
    assert titles == {"holidays.txt": "Holiday Entitlement for Staff", "expenses.txt": "expenses"}
    assert se._title("leave-policy_2024.pdf") == "leave policy 2024"
    assert se._title("Long-term plan v2.docx") == "Long-term plan v2"
