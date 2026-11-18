"""A bundle preview refuses an oversized or explosive bundle before reading it.

The Import dialog's preview (``POST /api/collections/import/preview``) and the
import itself both start at ``collection_bundle.read_manifest``. Until these
limits it read manifest.json whole, however large it unpacked, so a small
upload could exhaust the server's memory before anything said no.
"""

import json
import os
import struct
import tracemalloc
import zipfile

import pytest

import config
from services import collection_bundle as cb
from tests.test_collection_bundle import env  # noqa: F401 - fixture

MB = 1024 * 1024
MANIFEST = {"format": "clio-collection", "version": 1, "collection": {"name": "Handbooks"}, "documents": []}


@pytest.fixture()
def limits(monkeypatch):
    def apply(**values):
        for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
            for name, value in values.items():
                monkeypatch.setattr(s, name, value)
    return apply


def _bundle(path, *, manifest=None, extra=()):
    """A bundle with the two entries every bundle has, then ``extra``: (name, writer) pairs."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if callable(manifest):
            with zf.open("manifest.json", "w", force_zip64=True) as out:
                manifest(out)
        else:
            zf.writestr("manifest.json", json.dumps(manifest or MANIFEST))
        zf.writestr("indexes/metadata.db", b"")
        for name, write in extra:
            with zf.open(name, "w", force_zip64=True) as out:
                write(out)
    return path


def _spaces(total):
    """A JSON document of ``total`` bytes that is almost all whitespace."""
    def write(out):
        out.write(b'{"format": "clio-collection", "version": 1, "pad": "')
        block = b" " * MB
        for _ in range(total // MB):
            out.write(block)
        out.write(b'"}')
    return write


def _peak_while(fn):
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _refused(path, match):
    def go():
        with pytest.raises(cb.BundleError, match=match):
            cb.inspect_bundle(path)
    return go


# ── each limit ─────────────────────────────────────────────────────────────


def test_a_manifest_zip_bomb_is_refused_without_unpacking_it(env, tmp_path):  # noqa: F811
    """256 MB of whitespace deflates to about 250 KB."""
    path = _bundle(tmp_path / "bomb.clio.zip", manifest=_spaces(256 * MB))
    assert path.stat().st_size < 2 * MB

    peak = _peak_while(_refused(path, r"manifest\.json in the bundle is compressed \d+ to 1"))

    assert peak < 8 * MB


def test_an_oversized_manifest_that_compresses_honestly_is_refused(env, tmp_path, limits):  # noqa: F811
    limits(bundle_max_manifest_mb=1)
    noise = os.urandom(MB).hex()  # 2 MB of hex: about 2 to 1, under any ratio limit
    path = _bundle(tmp_path / "big.clio.zip", manifest={**MANIFEST, "pad": noise})

    peak = _peak_while(_refused(path, r"manifest\.json is larger than 1 MB.*BUNDLE_MAX_MANIFEST_MB"))

    assert peak < 2 * MB


def test_too_many_entries_are_refused_from_the_end_record(env, tmp_path, limits):  # noqa: F811
    limits(bundle_max_members=20)
    entries = [(f"sources/doc{i:02d}/f.txt", lambda out: out.write(b"x")) for i in range(30)]
    path = _bundle(tmp_path / "many.clio.zip", extra=entries)

    built = []
    original = zipfile.ZipFile.__init__

    def spy(self, *a, **k):
        built.append(a)
        original(self, *a, **k)

    import unittest.mock as mock
    with mock.patch.object(zipfile.ZipFile, "__init__", spy):
        with pytest.raises(cb.BundleError, match="more than 20 entries.*BUNDLE_MAX_MEMBERS"):
            cb.inspect_bundle(path)
    assert built == []  # refused before zipfile parsed the directory


def test_an_end_record_that_claims_a_huge_directory_is_refused(env, tmp_path):  # noqa: F811
    """A forged end record: two entries, but a 2 GB directory to read for them."""
    path = _bundle(tmp_path / "forged.clio.zip")
    data = bytearray(path.read_bytes())
    at = data.rfind(b"PK\x05\x06")
    struct.pack_into("<I", data, at + 12, 0x7FFFFFFF)  # size of the central directory
    path.write_bytes(bytes(data))

    with pytest.raises(cb.BundleError, match="BUNDLE_MAX_MEMBERS"):
        cb.inspect_bundle(path)


def test_a_bundle_that_unpacks_past_the_total_is_refused(env, tmp_path, limits):  # noqa: F811
    limits(bundle_max_uncompressed_mb=1)
    noise = os.urandom(2 * MB)
    path = _bundle(tmp_path / "large.clio.zip", extra=[("sources/doc/big.bin", lambda out: out.write(noise))])

    with pytest.raises(cb.BundleError, match="unpacks to more than 1 MB.*BUNDLE_MAX_UNCOMPRESSED_MB"):
        cb.inspect_bundle(path)


def test_an_explosive_entry_elsewhere_in_the_bundle_is_refused(env, tmp_path):  # noqa: F811
    def zeros(out):
        block = bytes(MB)
        for _ in range(64):
            out.write(block)
    path = _bundle(tmp_path / "vectors.clio.zip", extra=[("indexes/faiss.index", zeros)])

    with pytest.raises(cb.BundleError, match=r"indexes/faiss\.index in the bundle is compressed"):
        cb.inspect_bundle(path)


def test_a_manifest_that_is_not_an_object_is_refused(env, tmp_path):  # noqa: F811
    path = _bundle(tmp_path / "list.clio.zip", manifest=["clio-collection"])

    with pytest.raises(cb.BundleError, match="not a Clio collection export"):
        cb.inspect_bundle(path)


def test_limits_at_zero_are_off(env, tmp_path, limits):  # noqa: F811
    limits(bundle_max_members=0, bundle_max_uncompressed_mb=0, bundle_max_manifest_mb=0)
    path = _bundle(tmp_path / "ok.clio.zip", manifest={**MANIFEST, "pad": os.urandom(MB).hex()},
                   extra=[("sources/doc/big.bin", lambda out: out.write(os.urandom(2 * MB)))])

    assert cb.read_manifest(path)["format"] == "clio-collection"


# ── where they apply ───────────────────────────────────────────────────────


def test_a_real_export_still_previews(env):  # noqa: F811
    from tests.test_collection_bundle import _source_collection

    cid, _ = _source_collection(env)
    path, _ = cb.export_collection(cid)

    assert cb.inspect_bundle(path)["documents"] == 2


def test_the_preview_route_answers_4xx_and_keeps_nothing(env, tmp_path, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    import main
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    path = _bundle(tmp_path / "bomb.clio.zip", manifest=_spaces(256 * MB))

    r = TestClient(main.app).post("/api/collections/import/preview",
                                  files={"file": ("bomb.clio.zip", path.read_bytes(), "application/zip")})

    assert r.status_code == 400
    assert "ARCHIVE_MAX_COMPRESSION_RATIO" in r.json()["detail"]
    assert not list((env / "tmp").glob("**/*.zip"))


def test_the_import_refuses_the_same_bundle_and_creates_nothing(env, tmp_path):  # noqa: F811
    from services.app_database import app_db

    path = _bundle(tmp_path / "bomb.clio.zip", manifest=_spaces(256 * MB))
    before = len(app_db.get_all_collections())

    with pytest.raises(cb.BundleError, match="compressed"):
        cb.import_collection(path, owner_id="default")
    assert len(app_db.get_all_collections()) == before
