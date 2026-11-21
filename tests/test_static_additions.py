"""What a static export carries besides the search (``include``), and that
every addition goes through the same gate as the export itself."""

import json
import zipfile
from pathlib import Path

import pytest

from services import static_export as se
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.static_export import additions
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _audit, _public, kit, site  # noqa: F401


def _echo(ctx):
    """A stand-in addition: one file per source, holding its passages' text."""
    for doc in ctx.documents:
        texts = [p["text"] for p in ctx.passages if p["document_id"] == doc["id"]]
        ctx.put(f"echo/{doc['id'][:8]}.txt", "\n".join(texts))
    return {"sources": len(ctx.documents)}


@pytest.fixture()
def echo(monkeypatch):
    addition = additions.Addition(id="echo", label="Echo", write=_echo,
                                  offer=lambda o: {"available": True, "reason": None, "bytes": 10})
    monkeypatch.setitem(additions.ADDITIONS, "echo", addition)
    return addition


def _files(folder: Path):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_nothing_chosen_is_the_export_as_it_was(site, tmp_path):
    cid, _ = _public(site)
    plain, _ = se.write_export(cid, tmp_path / "a", confirm_public=True)
    none, detail = se.write_export(cid, tmp_path / "b", confirm_public=True, include="")
    assert "additions" not in plain and "additions" not in none and detail["include"] == []
    assert _files(tmp_path / "a") == _files(tmp_path / "b") == ["manifest.json", "passages.json", "vectors.bin"]
    for name in ("passages.json", "vectors.bin"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    strip = lambda m: {k: v for k, v in m.items() if k != "exported_at"}  # noqa: E731
    assert strip(plain) == strip(none)


def test_an_addition_is_listed_with_checksums_audited_and_zipped(site, echo, tmp_path):
    cid, ids = _public(site)
    pre = se.preview(cid)
    assert {"id": "echo", "label": "Echo", "site_only": False, "available": True, "reason": None,
            "bytes": 10} in pre["additions"]

    manifest, detail = se.write_export(cid, tmp_path / "out", confirm_public=True, include="echo")
    entry = manifest["additions"]["echo"]
    assert entry["sources"] == 2 and len(entry["files"]) == 2 and detail["include"] == ["echo"]
    for f in entry["files"]:
        raw = (tmp_path / "out" / f["file"]).read_bytes()
        assert f["bytes"] == len(raw) and len(f["sha256"]) == 64
    assert "Expenses are reimbursed" in "".join(
        (tmp_path / "out" / f["file"]).read_text(encoding="utf-8") for f in entry["files"])

    path, _, _ = se.export_zip(cid, confirm_public=True, include=["echo"])
    try:
        with zipfile.ZipFile(path) as zf:
            assert sorted(zf.namelist()) == sorted(["manifest.json", "passages.json", "vectors.bin",
                                                    *[f["file"] for f in entry["files"]]])
    finally:
        path.unlink()


def test_an_unknown_or_unavailable_addition_is_refused_and_nothing_is_written(site, echo, monkeypatch, tmp_path):
    cid, _ = _public(site)
    with pytest.raises(se.StaticExportError, match="no addition called nope") as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True, include="echo,nope")
    assert e.value.code == "option"
    monkeypatch.setattr(echo, "offer", lambda o: {"available": False, "reason": "Not today.", "bytes": 0})
    with pytest.raises(se.StaticExportError, match="Not today") as e:
        se.write_export(cid, tmp_path / "out", confirm_public=True, include="echo")
    assert not (tmp_path / "out" / "manifest.json").exists()
    monkeypatch.setattr(echo, "offer", lambda o: {"available": True, "bytes": 0})
    monkeypatch.setattr(echo, "site_only", True)
    with pytest.raises(se.StaticExportError, match="needs a whole site"):
        se.write_export(cid, tmp_path / "out", confirm_public=True, include="echo")


def test_a_failing_addition_takes_the_whole_export_back(site, echo, monkeypatch, tmp_path):
    cid, _ = _public(site)

    def half(ctx):
        ctx.put("echo/one.txt", "x")
        raise RuntimeError("disk full")

    monkeypatch.setattr(echo, "write", half)
    with pytest.raises(RuntimeError):
        se.write_export(cid, tmp_path / "out", confirm_public=True, include="echo")
    assert _files(tmp_path / "out") == []


def test_replacing_an_export_removes_last_times_additions_only(site, echo, tmp_path):
    cid, _ = _public(site)
    out = tmp_path / "out"
    manifest, _ = se.write_export(cid, out, confirm_public=True, include="echo")
    (out / "echo" / "mine.txt").write_text("the site's own file", encoding="utf-8")
    (out / "index.html").write_text("the site's own page", encoding="utf-8")
    se.clear_previous(out)
    assert _files(out) == ["echo/mine.txt", "index.html"]


def test_the_cli_and_http_take_include(site, echo, monkeypatch, tmp_path, capsys):
    from scripts import export_static

    cid, _ = _public(site)
    out = tmp_path / "site"
    assert export_static.main([cid, "--out", str(out), "--confirm-public", "--include", "echo"]) == 0
    assert "Included echo: 2 files" in capsys.readouterr().out
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["additions"]["echo"]
    assert _audit("collection.static_export")[0]["detail"]["include"] == ["echo"]

    from fastapi.testclient import TestClient

    from api import deps
    from main import app

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(app)
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true&include=echo")
    assert r.status_code == 200
    r = client.get(f"/api/collections/{cid}/export/static?confirm_public=true&include=nope")
    assert r.status_code == 400 and "no addition called nope" in r.json()["detail"]


def test_everything_chosen_mentions_no_source_the_gate_left_out(kit, site, tmp_path):
    """Every addition there is, in a whole site, from a collection holding a
    held source and a source with a stricter label: neither one's id, name or
    text appears in any file written."""
    cid, (kept, quarantined) = _public(site, quarantine=True)
    docs_dir = collection_service.get_documents_path(cid)
    (docs_dir / "salaries.txt").write_text("Salary bands are confidential. " * 20, encoding="utf-8")
    indexer = indexer_manager.get_indexer(cid)
    internal = indexer.index_document(docs_dir / "salaries.txt", "salaries.txt", collection_id=cid).document_id
    indexer.save_index()
    indexer.vector_store.metadata_store.set_document_governance(internal, sensitivity="confidential")

    out = tmp_path / "out"
    manifest, detail = se.write_export(cid, out, confirm_public=True, site=True, include="all")
    assert detail["include"] == [a["id"] for a in se.preview(cid)["additions"] if a["available"]]
    published = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file() and "models" not in p.parts)
    for secret in (b"Salary bands", b"salaries", b"reimbursed", quarantined.encode(), internal.encode(),
                   b"expenses", b"Expenses"):
        assert secret not in published, secret
