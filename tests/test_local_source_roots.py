"""LOCAL_SOURCE_ROOTS: which folders on the server may be read as sources.

Set, it is how a repository mounted into a container becomes a source, and a
limit on every route that takes a server path. Unset, nothing changes: an install
on someone's own machine still indexes whatever the native picker returns.
"""

import os
from pathlib import Path

import pytest

import config
from config import settings as _import_time_settings
from services import local_roots, source_watcher
from tests.test_folder_sync import (  # noqa: F401 - fixtures
    COLLECTION,
    _wait_for_job,
    client,
    env,
    folder,
    service,
)
from tests.test_git_sources import _commit, _git, repo  # noqa: F401 - fixtures


@pytest.fixture()
def roots(monkeypatch):
    def apply(*paths):
        value = ",".join(str(p) for p in paths)
        for s in {id(s): s for s in (_import_time_settings, config.settings, local_roots.settings)}.values():
            monkeypatch.setattr(s, "local_source_roots", value)

    apply()
    return apply


# ── the rule ──────────────────────────────────────────────────────────────


def test_no_roots_means_no_limit(roots, tmp_path):
    assert not local_roots.restricted()
    assert local_roots.allowed(tmp_path / "anywhere")


def test_inside_a_root_is_allowed_and_outside_is_not(roots, tmp_path):
    roots(tmp_path / "sources")

    assert local_roots.allowed(tmp_path / "sources")
    assert local_roots.allowed(tmp_path / "sources" / "repo" / "file.txt")
    assert not local_roots.allowed(tmp_path / "elsewhere")


def test_a_sibling_sharing_the_prefix_is_outside(roots, tmp_path):
    roots(tmp_path / "sources")

    assert not local_roots.allowed(tmp_path / "sources-private")


def test_dot_dot_cannot_step_out(roots, tmp_path):
    roots(tmp_path / "sources")

    assert not local_roots.allowed(f"{tmp_path / 'sources'}/../elsewhere")


def test_a_symlink_cannot_step_out(roots, tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "secret").mkdir()
    link = tmp_path / "sources" / "link"
    try:
        os.symlink(tmp_path / "secret", link, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make symlinks")
    roots(tmp_path / "sources")

    assert not local_roots.allowed(link)


def test_several_roots_with_spaces(roots, tmp_path, monkeypatch):
    roots(f"{tmp_path / 'a'} , {tmp_path / 'b'}")

    assert local_roots.configured() == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert local_roots.allowed(tmp_path / "b" / "repo")


def test_a_relative_root_allows_nothing_rather_than_everything(roots, tmp_path):
    roots("sources")

    assert local_roots.restricted()
    assert not local_roots.allowed(tmp_path)
    assert not local_roots.allowed(Path("sources").resolve())


@pytest.mark.parametrize("bad", ["bad\x00path"])
def test_an_unresolvable_path_is_refused(roots, tmp_path, bad):
    roots(tmp_path)

    assert not local_roots.allowed(bad)


# ── adding a source by path ───────────────────────────────────────────────


def test_a_checkout_inside_a_root_syncs_as_git(client, roots, repo):  # noqa: F811
    roots(repo.parent)
    (repo / "extra.txt").write_text("Notes about the pressure regulators. " * 20, encoding="utf-8")

    added = client.post("/documents/sync-folder",
                        json={"path": str(repo), "collection_id": COLLECTION, "watch": True})

    assert added.status_code == 202, added.text
    assert _wait_for_job(client, added.json()["job_id"])["status"] == "completed"
    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()
    assert listed["folders"][0]["git"] is True and listed["folders"][0]["watch"] is True


@pytest.mark.parametrize("route", ["/documents/sync-folder", "/documents/upload-repo"])
def test_a_path_outside_the_roots_is_refused(client, roots, tmp_path, folder, route):  # noqa: F811
    roots(tmp_path / "sources")

    refused = client.post(route, json={"path": str(folder), "collection_id": COLLECTION})

    assert refused.status_code == 403
    assert "LOCAL_SOURCE_ROOTS" in refused.json()["detail"]


@pytest.mark.parametrize("route", ["/documents/sync-folder", "/documents/upload-repo"])
def test_a_refusal_does_not_say_whether_the_path_exists(client, roots, tmp_path, folder, route):  # noqa: F811
    roots(tmp_path / "sources")

    real = client.post(route, json={"path": str(folder), "collection_id": COLLECTION})
    missing = client.post(route, json={"path": str(tmp_path / "no-such"), "collection_id": COLLECTION})

    assert real.status_code == missing.status_code == 403
    assert real.json()["detail"] == missing.json()["detail"]


def test_scan_folder_keeps_to_the_roots(client, roots, tmp_path, folder):  # noqa: F811
    roots(tmp_path / "sources")

    assert client.post("/api/scan-folder", json={"path": str(folder)}).status_code == 403

    roots(folder.parent)
    assert client.post("/api/scan-folder", json={"path": str(folder)}).status_code == 200


def test_with_no_roots_any_path_still_works(client, roots, folder):  # noqa: F811
    added = client.post("/documents/sync-folder", json={"path": str(folder), "collection_id": COLLECTION})

    assert added.status_code == 202, added.text
    assert _wait_for_job(client, added.json()["job_id"])["status"] == "completed"


# ── local index: files one by one ─────────────────────────────────────────


def test_index_local_refuses_files_outside_the_roots(client, roots, tmp_path, folder):  # noqa: F811
    roots(tmp_path / "sources")

    refused = client.post("/documents/index-local-async",
                          json={"file_paths": [str(folder / "readme.txt")], "collection_id": COLLECTION})

    assert refused.status_code == 400
    assert "LOCAL_SOURCE_ROOTS" in refused.text


def test_a_browser_upload_still_works_with_roots_set(client, env, roots, tmp_path):  # noqa: F811
    """The browser's route: /documents/upload-staged, then index-local-async over
    the staged paths, which are outside any root and must not be stopped."""
    roots(tmp_path / "sources")

    staged = client.post("/documents/upload-staged", params={"collection_id": COLLECTION},
                         files=[("files", ("uploaded.txt", b"Notes about the coolant pumps. " * 20, "text/plain"))])
    assert staged.status_code == 202, staged.text
    paths = [s["path"] for s in staged.json()["staged"]]
    assert paths

    job = client.post("/documents/index-local-async", json={"file_paths": paths, "collection_id": COLLECTION})

    assert job.status_code == 202, job.text
    assert _wait_for_job(client, job.json()["job_id"])["status"] == "completed"


def test_another_folder_in_the_data_directory_is_not_staging(client, env, roots, tmp_path):  # noqa: F811
    roots(tmp_path / "sources")
    private = tmp_path / "app-secrets.txt"
    private.write_text("Not a source. " * 20, encoding="utf-8")

    refused = client.post("/documents/index-local-async",
                          json={"file_paths": [str(private)], "collection_id": COLLECTION})

    assert refused.status_code == 400


# ── what the panel is told ────────────────────────────────────────────────


def test_the_listing_names_the_roots(client, roots, tmp_path):  # noqa: F811
    assert client.get("/documents/sync-folders",
                      params={"collection_id": COLLECTION}).json()["local_source_roots"] == []

    roots(tmp_path / "sources")
    assert client.get("/documents/sync-folders",
                      params={"collection_id": COLLECTION}).json()["local_source_roots"] == [str(tmp_path / "sources")]


# ── sources remembered from before ────────────────────────────────────────


def _entry(path, **extra):
    return {"collection_id": COLLECTION, "path": str(path), "watch": True, **extra}


def test_the_watcher_reports_a_folder_now_outside_the_roots(env, roots, tmp_path, folder, monkeypatch):  # noqa: F811
    from services.upload_service import upload_service

    roots(tmp_path / "sources")
    recorded = {}
    monkeypatch.setattr(upload_service, "update_sync_folder", lambda cid, path, **f: recorded.update(f))
    monkeypatch.setattr(upload_service, "sync_folder", lambda **k: pytest.fail("read a folder outside the roots"))
    monkeypatch.setattr(source_watcher, "_job_status", lambda entry: None)

    assert source_watcher.check_source(_entry(folder)) == "error"
    assert "LOCAL_SOURCE_ROOTS" in recorded["last_error"]


def test_the_watcher_still_syncs_a_folder_inside_the_roots(env, roots, folder, monkeypatch):  # noqa: F811
    from services.upload_service import upload_service

    roots(folder.parent)
    calls = []
    monkeypatch.setattr(upload_service, "sync_folder", lambda **k: calls.append(k))
    monkeypatch.setattr(source_watcher, "_job_status", lambda entry: None)

    assert source_watcher.check_source(_entry(folder)) == "synced"
    assert calls[0]["path"] == str(folder)


def test_a_managed_clone_is_not_held_to_the_roots(client, env, roots, tmp_path, monkeypatch):  # noqa: F811
    """It lives under DATA_DIR/repos because this server put it there."""
    from api import documents

    clone = tmp_path / "data" / "repos" / "abc"
    clone.mkdir(parents=True)
    (clone / "readme.txt").write_text("Notes about the heat exchangers. " * 20, encoding="utf-8")
    roots(tmp_path / "sources")
    entry = {"path": str(clone.resolve()), "git_url": "https://git.example/team/repo.git"}
    monkeypatch.setattr(documents, "_remembered_source", lambda cid, path: entry)
    monkeypatch.setattr(documents, "_refresh_managed_clone", lambda e: clone)

    synced = client.post("/documents/sync-folder", json={"path": str(clone), "collection_id": COLLECTION})

    assert synced.status_code in (200, 202), synced.text


def test_the_watcher_does_not_hold_a_managed_clone_to_the_roots(env, roots, tmp_path, monkeypatch):  # noqa: F811
    from services.upload_service import upload_service

    roots(tmp_path / "sources")
    calls = []
    monkeypatch.setattr(upload_service, "sync_folder", lambda **k: calls.append(k))
    monkeypatch.setattr(source_watcher, "_job_status", lambda entry: None)
    monkeypatch.setattr(source_watcher.git_source, "remote_head", lambda url, branch: "new-tip")
    monkeypatch.setattr(source_watcher.git_source, "ensure_clone", lambda url, branch: None)

    clone = tmp_path / "data" / "repos" / "abc"
    assert source_watcher.check_source(_entry(clone, git_url="https://git.example/team/repo.git")) == "synced"
    assert calls


# ── a shared deployment with LOCAL_SOURCE_ROOTS unset ─────────────────────
#
# "No limit" suits one person's own machine. Once there is an identity provider
# and an operator list, an ordinary writer naming any server path could index
# the server's own files and then search them, so that is the operator's call
# until LOCAL_SOURCE_ROOTS says which folders are fair game. The browser's own
# uploads, staged inside the collection, are never stopped.

ADMIN = "ops@example.org"
WRITER = "writer@example.org"


@pytest.fixture()
def shared_client(env, roots, monkeypatch):  # noqa: F811
    import importlib

    from fastapi.testclient import TestClient

    import main
    from tests import identity_helpers

    for s in {id(s): s for s in (_import_time_settings, config.settings)}.values():
        monkeypatch.setattr(s, "admin_emails", ADMIN)
    identity_helpers.install(monkeypatch)
    yield TestClient(importlib.reload(main).app)
    importlib.reload(main)


@pytest.mark.parametrize("route", ["/documents/sync-folder", "/documents/upload-repo"])
def test_only_an_admin_may_name_any_server_folder(shared_client, folder, route):  # noqa: F811
    from tests.identity_helpers import as_user

    refused = shared_client.post(route, json={"path": str(folder), "collection_id": COLLECTION},
                                 headers=as_user(WRITER))
    assert refused.status_code == 403
    assert "LOCAL_SOURCE_ROOTS" in refused.json()["detail"]

    allowed = shared_client.post(route, json={"path": str(folder), "collection_id": COLLECTION},
                                 headers=as_user(ADMIN))
    assert allowed.status_code == 202, allowed.text


def test_the_writer_refusal_does_not_say_whether_the_path_exists(shared_client, tmp_path, folder):  # noqa: F811
    from tests.identity_helpers import as_user

    real = shared_client.post("/documents/sync-folder", headers=as_user(WRITER),
                              json={"path": str(folder), "collection_id": COLLECTION})
    missing = shared_client.post("/documents/sync-folder", headers=as_user(WRITER),
                                 json={"path": str(tmp_path / "no-such"), "collection_id": COLLECTION})
    assert real.status_code == missing.status_code == 403
    assert real.json()["detail"] == missing.json()["detail"]


def test_a_writer_cannot_index_server_files_one_by_one(shared_client, tmp_path):  # noqa: F811
    from tests.identity_helpers import as_user

    secret = tmp_path / "app-secrets.txt"
    secret.write_text("OPENAI_API_KEY=sk-not-for-you " * 20, encoding="utf-8")

    refused = shared_client.post("/documents/index-local-async", headers=as_user(WRITER),
                                 json={"file_paths": [str(secret)], "collection_id": COLLECTION})
    assert refused.status_code == 400
    assert "admin" in refused.text.lower()

    allowed = shared_client.post("/documents/index-local-async", headers=as_user(ADMIN),
                                 json={"file_paths": [str(secret)], "collection_id": COLLECTION})
    assert allowed.status_code == 202, allowed.text


def test_a_writers_browser_upload_still_works(shared_client):  # noqa: F811
    from tests.identity_helpers import as_user

    staged = shared_client.post("/documents/upload-staged", params={"collection_id": COLLECTION},
                                headers=as_user(WRITER),
                                files=[("files", ("uploaded.txt", b"Notes about the coolant pumps. " * 20, "text/plain"))])
    assert staged.status_code == 202, staged.text
    paths = [s["path"] for s in staged.json()["staged"]]

    job = shared_client.post("/documents/index-local-async", headers=as_user(WRITER),
                             json={"file_paths": paths, "collection_id": COLLECTION})
    assert job.status_code == 202, job.text
    assert _wait_for_job(shared_client, job.json()["job_id"])["status"] == "completed"


def test_with_roots_set_a_writer_may_use_them(shared_client, roots, folder):  # noqa: F811
    from tests.identity_helpers import as_user

    roots(folder.parent)
    added = shared_client.post("/documents/sync-folder", headers=as_user(WRITER),
                               json={"path": str(folder), "collection_id": COLLECTION})
    assert added.status_code == 202, added.text
