"""Who may do what with a collection, and what the audit trail records.

- A *restricted* label is a boundary: nobody but the owner reaches the
  collection, so existing shares and an earlier publication stop granting
  anything, and relabelling withdraws both (audited).
- A *team* collection (no single owner) hands its owner-tier actions to the
  admins once an admin list is in force; everyone else contributes sources.
  Without one, every caller keeps them.
- Collection deletion, deployment settings, embedding changes and the team
  AI key are audited, with ids and field names only.
"""

import pytest
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services.app_database import app_db
from services.collection_service import collection_service
from services.config_manager import config_manager
from services.sharing_service import sharing_service
from tests.test_private_collections import (  # noqa: F401  (fixtures)
    ALICE, BOB, _as, fresh_db, private_client, private_mode,
)

ADMIN = "admin@example.com"


def _settings_objects():
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


@pytest.fixture()
def admin_gate(monkeypatch):
    """ADMIN_EMAILS set, on top of the identity provider private_client fakes."""
    for s in _settings_objects():
        monkeypatch.setattr(s, "admin_emails", ADMIN)


def _alice_collection(client):
    r = client.post("/api/collections", json={"name": "Alice's drawings"}, headers=_as(ALICE))
    assert r.status_code == 201
    return r.json()["id"]


def _actions(action):
    from services import audit

    return audit.list_events(action=action)


# ── Restricted ──────────────────────────────────────────────────────────────


def test_relabel_to_restricted_revokes_existing_shares(private_client):
    cid = _alice_collection(private_client)
    token = private_client.post(f"/api/collections/{cid}/share", json={"permission": "readwrite"},
                                headers=_as(ALICE)).json()["share_id"]
    assert private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB)).status_code == 200
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 200

    r = private_client.put(f"/api/collections/{cid}", json={"sensitivity": "restricted"}, headers=_as(ALICE))
    assert r.status_code == 200

    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    listed = {c["id"] for c in private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]}
    assert cid not in listed
    assert not app_db.get_share(token)["is_active"]
    revoked = _actions("share.revoke")
    assert revoked and revoked[0]["collection_id"] == cid
    assert revoked[0]["detail"] == {"share_id": token, "reason": "restricted"}

    # Taking the label off again does not bring the share back.
    private_client.put(f"/api/collections/{cid}", json={"sensitivity": "internal"}, headers=_as(ALICE))
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    # The owner is unaffected throughout.
    assert private_client.get(f"/api/collections/{cid}", headers=_as(ALICE)).status_code == 200


def test_restricted_ignores_a_share_left_from_before(private_mode):
    """Data written before relabel revoked shares: the access check alone holds."""
    cid = app_db.create_collection(name="vault", owner_id=ALICE)
    share = app_db.create_share(collection_id=cid, owner_id=ALICE, permission="read")
    app_db.accept_share(share, BOB)
    assert sharing_service.check_collection_access(cid, BOB) == "read"

    app_db.update_collection(cid, sensitivity="restricted")
    assert sharing_service.check_collection_access(cid, BOB) is None
    assert sharing_service.check_collection_access(cid, ALICE) == "owner"
    assert cid not in {c["id"] for c in collection_service.get_all_collections(BOB)}


def test_relabel_to_restricted_unpublishes(private_client):
    cid = _alice_collection(private_client)
    assert private_client.put(f"/api/collections/{cid}", json={"published": True},
                              headers=_as(ALICE)).status_code == 200
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 200

    r = private_client.put(f"/api/collections/{cid}", json={"sensitivity": "restricted"}, headers=_as(ALICE))
    assert r.status_code == 200
    assert r.json()["published"] is False
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    assert any(e["detail"] == {"published": False, "reason": "restricted"} for e in _actions("collection.published"))


def test_cannot_publish_and_restrict_in_one_request(private_client):
    cid = _alice_collection(private_client)
    r = private_client.put(f"/api/collections/{cid}", json={"sensitivity": "restricted", "published": True},
                           headers=_as(ALICE))
    assert r.status_code == 403
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404


def test_mcp_token_cannot_be_scoped_to_someone_elses_restricted_collection(private_client):
    cid = _alice_collection(private_client)
    token = private_client.post(f"/api/collections/{cid}/share", json={"permission": "read"},
                                headers=_as(ALICE)).json()["share_id"]
    private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB))
    private_client.put(f"/api/collections/{cid}", json={"sensitivity": "restricted"}, headers=_as(ALICE))
    r = private_client.post("/api/mcp/tokens", json={"name": "laptop", "collection_scope": [cid]},
                            headers=_as(BOB))
    assert r.status_code == 404


# ── Team collections ────────────────────────────────────────────────────────


def _team_collection(client, who=ADMIN):
    r = client.post("/api/collections", json={"name": "Programme X", "visibility": "team"}, headers=_as(who))
    assert r.status_code == 201
    return r.json()["id"]


def test_team_owner_actions_are_for_admins(private_client, admin_gate):
    cid = _team_collection(private_client)
    assert sharing_service.check_collection_access(cid, BOB) == "readwrite"
    assert sharing_service.check_collection_access(cid, ADMIN) == "owner"
    perms = {c["id"]: c["permission"] for c in
             private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]}
    assert perms[cid] == "readwrite"

    r = private_client.put(f"/api/collections/{cid}", json={"sensitivity": "public"}, headers=_as(BOB))
    assert r.status_code == 403
    assert "team collection" in r.json()["detail"]
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403
    assert private_client.get(f"/api/collections/{cid}/export?include_sources=true",
                              headers=_as(BOB)).status_code == 403
    assert private_client.post(f"/api/collections/{cid}/reindex", headers=_as(BOB)).status_code == 403

    assert private_client.put(f"/api/collections/{cid}", json={"sensitivity": "public"},
                              headers=_as(ADMIN)).status_code == 200
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(ADMIN)).status_code == 200


def test_team_tier_unchanged_without_an_admin_list(private_client):
    cid = _team_collection(private_client, who=BOB)
    assert sharing_service.check_collection_access(cid, BOB) == "owner"
    assert private_client.put(f"/api/collections/{cid}", json={"sensitivity": "public"},
                              headers=_as(BOB)).status_code == 200


def test_open_install_everyone_still_owner(fresh_db, monkeypatch):
    for s in _settings_objects():
        monkeypatch.setattr(s, "admin_emails", ADMIN)
    assert sharing_service.check_collection_access("default", None) == "owner"
    assert sharing_service.check_collection_access("default", BOB) == "owner"


# ── Audit ───────────────────────────────────────────────────────────────────


def test_collection_delete_is_audited(private_client):
    cid = _alice_collection(private_client)
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(ALICE)).status_code == 200
    events = _actions("collection.delete")
    assert events and events[0]["collection_id"] == cid and events[0]["actor"] == ALICE
    assert events[0]["detail"]["team"] is False


@pytest.fixture()
def open_client(fresh_db, monkeypatch):
    import main
    from api import system

    monkeypatch.setattr(system.settings, "embedding_provider", "local")
    monkeypatch.setattr(system.settings, "embedding_model", system.settings.embedding_model)
    monkeypatch.setattr(system.settings, "remote_embedding_model", system.settings.remote_embedding_model)
    monkeypatch.setattr(system.settings, "embedding_api_key", "")
    monkeypatch.setattr(system.settings, "chunk_size", system.settings.chunk_size)
    monkeypatch.setattr(system.settings, "offline_mode", False)
    monkeypatch.setattr(config_manager, "env_file", fresh_db.db_path.parent / ".env")
    return TestClient(main.app)


def test_config_changes_are_audited_without_values(open_client):
    current = open_client.get("/api/config").json()
    # An untouched save changes nothing and records nothing.
    assert open_client.post("/api/config", json={"chunk_size": current["chunk_size"]}).status_code == 200
    assert not _actions("config.update")

    r = open_client.post("/api/config", json={"chunk_size": current["chunk_size"] + 1,
                                              "embedding_api_key": "sk-secret-value"})
    assert r.status_code == 200
    event = _actions("config.update")[0]
    assert event["detail"]["fields"] == ["chunk_size", "embedding_api_key"]
    assert "sk-secret-value" not in str(app_db.list_audit_events())
    assert not _actions("embedding.change")


def test_embedding_change_is_audited(open_client):
    r = open_client.post("/api/config", json={"embedding_provider": "google",
                                              "remote_embedding_model": "gemini-embedding-001"})
    assert r.status_code == 200
    event = _actions("embedding.change")[0]
    assert event["detail"] == {"fields": ["embedding_provider", "remote_embedding_model"],
                               "provider": "google", "model": "gemini-embedding-001"}


def test_team_key_store_and_remove_are_audited(open_client, monkeypatch):
    from api import chat

    class _Valid:
        def validate(self):
            return True

    monkeypatch.setattr(chat, "create_provider", lambda provider, key: _Valid())
    r = open_client.post("/api/agent/config", json={"provider": "anthropic", "api_key": "sk-ant-secret"})
    assert r.status_code == 200
    assert _actions("ai_key.store")[0]["detail"] == {"provider": "anthropic", "replaced": False}

    assert open_client.delete("/api/agent/config/anthropic").status_code == 200
    assert _actions("ai_key.remove")[0]["detail"] == {"provider": "anthropic"}
    assert "sk-ant-secret" not in str(app_db.list_audit_events())
