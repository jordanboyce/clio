"""Indexing jobs under private collections: a job is as visible as its collection.

Job ids are sequential, so every job route answers a job in a collection the
caller cannot see exactly like a missing one (404), and the listings drop
such jobs. Cancelling additionally needs write access. A job tied to no
collection (a deployment-wide re-index) is an admin's.
"""

import json

import pytest

import config
from config import settings as _import_time_settings
from services.app_database import app_db
from tests.test_private_collections import (  # noqa: F401  (fixtures)
    ALICE, BOB, _as, fresh_db, private_client,
)

ADMIN = "admin@example.com"


@pytest.fixture()
def admin_gate(monkeypatch):
    """ADMIN_EMAILS set, on top of the identity provider private_client fakes."""
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    for s in objs.values():
        monkeypatch.setattr(s, "admin_emails", ADMIN)


def _alice_collection(client):
    r = client.post("/api/collections", json={"name": "Alice's drawings"}, headers=_as(ALICE))
    assert r.status_code == 201
    return r.json()["id"]


def _running_job(cid, current_file="wing_spar_rev_C.pdf"):
    job_id = app_db.create_upload_job(cid, total_files=3)
    app_db.update_upload_job(
        job_id, status="running", current_file=current_file,
        result_summary=json.dumps({"failed_files": [{"file": "spec.docx", "error": "x"}]}),
    )
    return job_id


def test_job_status_is_404_outside_the_collection(private_client):
    cid = _alice_collection(private_client)
    job_id = _running_job(cid)

    assert private_client.get(f"/documents/upload/{job_id}/status", headers=_as(ALICE)).status_code == 200
    r = private_client.get(f"/documents/upload/{job_id}/status", headers=_as(BOB))
    assert r.status_code == 404
    assert "wing_spar" not in r.text
    # Same answer as a job that does not exist.
    missing = private_client.get(f"/documents/upload/{job_id + 999}/status", headers=_as(BOB))
    assert missing.status_code == 404
    assert r.json()["detail"].replace(str(job_id), "") == missing.json()["detail"].replace(str(job_id + 999), "")


def test_job_listings_only_show_visible_collections(private_client):
    cid = _alice_collection(private_client)
    job_id = _running_job(cid)
    team_job = _running_job("default", current_file="team.txt")

    alice_active = {j["job_id"] for j in private_client.get("/documents/upload/active", headers=_as(ALICE)).json()}
    bob_active = {j["job_id"] for j in private_client.get("/documents/upload/active", headers=_as(BOB)).json()}
    assert {job_id, team_job} <= alice_active
    assert job_id not in bob_active and team_job in bob_active

    bob_recent = {j["job_id"] for j in private_client.get("/documents/jobs", headers=_as(BOB)).json()}
    assert job_id not in bob_recent and team_job in bob_recent
    assert private_client.get(f"/documents/jobs?collection_id={cid}", headers=_as(BOB)).status_code == 404
    mine = private_client.get(f"/documents/jobs?collection_id={cid}", headers=_as(ALICE)).json()
    assert [j["job_id"] for j in mine] == [job_id]


def test_job_listing_limit_counts_visible_jobs(private_client):
    cid = _alice_collection(private_client)
    team_job = _running_job("default")
    for _ in range(5):
        _running_job(cid)
    # Alice's five newer jobs must not crowd Bob's one out of his first page.
    bob = private_client.get("/documents/jobs?limit=1", headers=_as(BOB)).json()
    assert [j["job_id"] for j in bob] == [team_job]


def test_job_stream_is_404_outside_the_collection(private_client):
    cid = _alice_collection(private_client)
    job_id = _running_job(cid)
    # Finished, so a stream that is (wrongly) opened closes instead of hanging.
    app_db.update_upload_job(job_id, status="completed")
    assert private_client.get(f"/documents/upload/{job_id}/stream", headers=_as(BOB)).status_code == 404

    r = private_client.get(f"/documents/upload/{job_id}/stream", headers=_as(ALICE))
    assert r.status_code == 200
    assert "job_complete" in r.text


def test_cancel_needs_write_access(private_client):
    cid = _alice_collection(private_client)
    job_id = _running_job(cid)

    assert private_client.post(f"/documents/upload/{job_id}/cancel", headers=_as(BOB)).status_code == 404
    assert app_db.get_upload_job(job_id)["status"] == "running"

    # A read-only share may watch the job but not stop it.
    token = private_client.post(f"/api/collections/{cid}/share", json={"permission": "read"},
                                headers=_as(ALICE)).json()["share_id"]
    assert private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB)).status_code == 200
    assert private_client.get(f"/documents/upload/{job_id}/status", headers=_as(BOB)).status_code == 200
    assert private_client.post(f"/documents/upload/{job_id}/cancel", headers=_as(BOB)).status_code == 403
    assert app_db.get_upload_job(job_id)["status"] == "running"

    assert private_client.post(f"/documents/upload/{job_id}/cancel", headers=_as(ALICE)).status_code == 200
    assert app_db.get_upload_job(job_id)["status"] == "cancelled"


def test_reindex_status_follows_the_collection(private_client):
    cid = _alice_collection(private_client)
    jid = app_db.create_reindex_job({"collection_id": cid, "embedding_model": "x"})
    app_db.update_reindex_job(jid, status="running", total_documents=9, current_file="quote.pdf")

    assert private_client.get(f"/api/reindex/status?job_id={jid}", headers=_as(BOB)).status_code == 404
    latest = private_client.get("/api/reindex/status", headers=_as(BOB))
    assert latest.status_code == 200 and latest.json() is None

    mine = private_client.get("/api/reindex/status", headers=_as(ALICE)).json()
    assert mine["id"] == jid and mine["collection_id"] == cid
    assert private_client.get(f"/api/reindex/status?job_id={jid}", headers=_as(ALICE)).status_code == 200


def test_deployment_wide_jobs_are_for_admins(private_client, admin_gate):
    jid = app_db.create_reindex_job({"embedding_model": "x"})
    app_db.update_reindex_job(jid, status="running", total_documents=3)
    assert private_client.get(f"/api/reindex/status?job_id={jid}", headers=_as(BOB)).status_code == 404
    assert private_client.get("/api/reindex/status", headers=_as(BOB)).json() is None
    assert private_client.get("/api/reindex/status", headers=_as(ADMIN)).json()["id"] == jid

    orphan = app_db.create_upload_job("", total_files=1)
    assert private_client.get(f"/documents/upload/{orphan}/status", headers=_as(BOB)).status_code == 404
    assert private_client.get(f"/documents/upload/{orphan}/status", headers=_as(ADMIN)).status_code == 200
