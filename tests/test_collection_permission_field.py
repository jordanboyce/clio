"""The collection list says what each caller may do with each collection.

The app hides or explains the owner-tier actions (export, edit, re-index,
delete, instructions) from ``permission`` and ``team`` in GET
/api/collections, so the field must agree with what the server enforces
(api/deps.require_collection_access) for an admin, an owner and a member.
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_access_tiers import ADMIN, _settings_objects, _team_collection
from tests.test_private_collections import ALICE, BOB, _as, fresh_db, private_client  # noqa: F401


@pytest.fixture()
def admin_gate(monkeypatch):
    for s in _settings_objects():
        monkeypatch.setattr(s, "admin_emails", ADMIN)


def _listed(client, who):
    body = client.get("/api/collections", headers=_as(who)).json()
    return {c["id"]: c for c in body["collections"]}


def _owner_action_status(client, cid, who):
    return client.post(f"/api/collections/{cid}/reindex", headers=_as(who)).status_code


def test_permission_matches_what_the_server_allows(private_client, admin_gate):
    team = _team_collection(private_client)
    alices = private_client.post("/api/collections", json={"name": "Alice's notes"}, headers=_as(ALICE)).json()["id"]
    shared = private_client.post(f"/api/collections/{alices}/share", json={"permission": "readwrite"},
                                 headers=_as(ALICE)).json()["share_id"]
    assert private_client.post(f"/api/shares/{shared}/accept", headers=_as(BOB)).status_code == 200

    admin, owner, member = _listed(private_client, ADMIN), _listed(private_client, ALICE), _listed(private_client, BOB)

    # An admin: the team collection's owner-tier actions are theirs.
    assert admin[team]["permission"] == "owner" and admin[team]["team"] is True
    assert alices not in admin  # a private collection is its owner's, admin or not
    # The owner of a private collection.
    assert owner[alices]["permission"] == "owner" and not owner[alices].get("team")
    assert owner[team]["permission"] == "readwrite"
    # A member: contributes to the team collection and to what was shared read-write.
    assert member[team]["permission"] == "readwrite" and member[team]["team"] is True
    assert member[alices]["permission"] == "readwrite"

    # Each listed permission is what the server then enforces.
    assert _owner_action_status(private_client, alices, BOB) == 403
    assert _owner_action_status(private_client, team, BOB) == 403
    assert _owner_action_status(private_client, team, ALICE) == 403
    assert _owner_action_status(private_client, alices, ALICE) != 403
    assert _owner_action_status(private_client, team, ADMIN) != 403


def test_without_an_admin_list_everyone_owns_a_team_collection(private_client):
    team = _team_collection(private_client, who=BOB)

    assert _listed(private_client, BOB)[team]["permission"] == "owner"
    assert _listed(private_client, ALICE)[team]["permission"] == "owner"


def test_an_open_install_lists_every_collection_as_owned(fresh_db, monkeypatch):  # noqa: F811
    import main
    from api import deps

    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    client.post("/api/collections", json={"name": "Handbooks"})

    listed = client.get("/api/collections").json()["collections"]

    assert listed and all(c["permission"] == "owner" for c in listed)
