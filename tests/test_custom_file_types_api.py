"""Custom file types over HTTP: admin-only changes, open read, end-to-end indexing."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import custom_file_types as cft

PUMPS = json.dumps({"pumps": [
    {"id": "9f1c2d3e-4b5a-6789-abcd-ef0123456789", "name": "Pump A", "psi": 62},
    {"id": "0a1b2c3d-4b5a-6789-abcd-ef0123456789", "name": "Pump B", "psi": 48},
    {"id": "11111111-4b5a-6789-abcd-ef0123456789", "name": "Pump C", "psi": 51}]})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.custom_file_types as api
    import main
    monkeypatch.setattr(cft.registry, "_path_fn", lambda: tmp_path / "custom_file_types.json")
    monkeypatch.setattr(cft.registry, "_ttl", 0)
    monkeypatch.setattr(cft.registry, "_stamp", None)
    audited = []
    monkeypatch.setattr(api.audit, "record", lambda *a, **k: audited.append((a, k)))
    c = TestClient(main.app)
    c.audited = audited
    c.api = api
    return c


def test_save_list_and_delete_round_trip(client):
    r = client.put("/api/custom-file-types/.acme", json={"label": "Acme", "title_field": "name"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["extension"] == ".acme"
    assert "affected_documents" in body
    types = client.get("/api/custom-file-types").json()["types"]
    assert [t["extension"] for t in types] == [".acme"]
    del_resp = client.delete("/api/custom-file-types/.acme").json()
    assert del_resp["deleted"] is True
    assert "affected_documents" in del_resp
    assert client.delete("/api/custom-file-types/.acme").status_code == 404
    assert [a[0][0] for a in client.audited] == ["custom_file_type.save", "custom_file_type.delete"]


@pytest.mark.parametrize("ext,body", [
    (".pdf", {}), (".exe", {}), ("bad ext", {}), (".acme", {"format": "xml"}), (".acme", {"records_per_section": 0}),
])
def test_invalid_requests_are_400_with_a_reason(client, ext, body):
    r = client.put(f"/api/custom-file-types/{ext}", json=body)
    assert r.status_code == 400 and r.json()["detail"]


def test_changes_need_admin_but_listing_does_not(client, monkeypatch):
    from fastapi import HTTPException

    def deny(action="x"):
        raise HTTPException(status_code=403, detail="Only an administrator can " + action)

    monkeypatch.setattr(client.api, "require_admin", deny)
    assert client.put("/api/custom-file-types/.acme", json={}).status_code == 403
    assert client.delete("/api/custom-file-types/.acme").status_code == 403
    assert client.post("/api/custom-file-types/preview", json={
        "extension": ".acme", "sample": "{}", "profile": {}}).status_code == 403
    assert client.post("/api/custom-file-types/suggest", data={"extension": ".acme"},
                       files={"file": ("a.acme", b"{}")}).status_code == 403
    assert client.get("/api/custom-file-types").status_code == 200
    assert cft.registry.all() == []


def test_preview_shows_the_text_without_saving(client):
    r = client.post("/api/custom-file-types/preview", json={
        "extension": ".acme", "sample": PUMPS,
        "profile": {"record_path": "pumps", "title_field": "name", "exclude": ["id"]}})
    body = r.json()
    assert r.status_code == 200 and body["preview"]["records"] == 3
    assert "Pump A" in body["preview"]["text"] and "9f1c2d3e" not in body["preview"]["text"]
    assert cft.registry.all() == []


def test_preview_reports_a_bad_sample_clearly(client):
    r = client.post("/api/custom-file-types/preview", json={
        "extension": ".acme", "sample": "{ nope", "profile": {}})
    assert r.status_code == 400 and "line 1" in r.json()["detail"]
    r = client.post("/api/custom-file-types/preview", json={
        "extension": ".acme", "sample": PUMPS, "profile": {"record_path": "nope"}})
    assert r.status_code == 400 and "record_path" in r.json()["detail"]


def test_oversized_samples_are_refused(client):
    r = client.post("/api/custom-file-types/suggest", data={"extension": ".acme", "use_ai": "false"},
                    files={"file": ("a.acme", b"x" * (2 * 1024 * 1024 + 10))})
    assert r.status_code == 413


def test_suggest_returns_a_profile_and_preview_without_the_model(client):
    r = client.post("/api/custom-file-types/suggest", data={"extension": ".acme", "use_ai": "false"},
                    files={"file": ("sample.acme", PUMPS.encode())})
    body = r.json()
    assert r.status_code == 200 and body["source"] == "heuristic"
    assert body["profile"]["record_path"] == "pumps" and body["profile"]["title_field"] == "name"
    assert "id" in body["profile"]["exclude"] and body["preview"]["records"] == 3
    assert cft.registry.all() == []                      # a suggestion is never saved by itself


def test_suggest_rejects_unreadable_samples(client):
    r = client.post("/api/custom-file-types/suggest", data={"extension": ".acme", "use_ai": "false"},
                    files={"file": ("sample.acme", b"not json at all")})
    assert r.status_code == 400


def test_a_saved_type_is_then_accepted_and_indexed(client, tmp_path):
    """Save a profile over HTTP, then run a real file through the extractor."""
    from services.document_extractor import DocumentExtractor, is_supported_filename
    f = tmp_path / "plant.acme"
    f.write_text(PUMPS, encoding="utf-8")
    assert not is_supported_filename(f.name)
    client.put("/api/custom-file-types/.acme", json={
        "label": "Acme", "record_path": "pumps", "title_field": "name", "exclude": ["id"],
        "labels": {"psi": "Pressure (psi)"}})
    assert is_supported_filename(f.name)
    text = "\n".join(DocumentExtractor().extract_text(f).page_texts.values())
    assert "Pump B" in text and "Pressure (psi): 48" in text and "0a1b2c3d" not in text
