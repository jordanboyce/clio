"""Git repositories as sources, the Updates feed and the watcher.

Reuses the folder-sync fixtures: a throwaway app database, a fake-embedding
indexer and an UploadService whose jobs run on the calling thread.
"""

import hashlib
import hmac
import json
import subprocess
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from config import settings
from services import git_source, source_changes, source_watcher
from tests.test_folder_sync import (  # noqa: F401 - fixtures
    COLLECTION, _docs, _text, _wait_for_job, client, env, folder, service,
)

pytestmark = pytest.mark.skipif(not git_source.git_available(), reason="git is not installed")

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
}


def _git(repo: Path, *args: str) -> str:
    import os

    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         env={**os.environ, **_IDENTITY, "GIT_CONFIG_GLOBAL": os.devnull,
                              "GIT_CONFIG_SYSTEM": os.devnull})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "readme.txt").write_text(_text("readme"), encoding="utf-8")
    (root / "notes.md").write_text(_text("notes"), encoding="utf-8")
    (root / "ignored.txt").write_text(_text("ignored"), encoding="utf-8")
    _commit(root, "Initial import")
    return root


def _names(env):
    return {Path(d["source_path"]).name for d in _docs(env)}


# ── Listing ──────────────────────────────────────────────────────────────

def test_a_checkout_is_listed_by_git_so_gitignore_is_honoured(env, service, repo):
    (repo / "scratch.txt").write_text(_text("scratch"), encoding="utf-8")  # untracked, not ignored
    service.sync_folder(str(repo), COLLECTION)
    names = _names(env)
    assert {"readme.txt", "notes.md", "scratch.txt"} <= names
    assert "ignored.txt" not in names


def test_a_plain_folder_is_still_walked(env, service, folder):
    service.sync_folder(str(folder), COLLECTION)
    assert {"readme.txt", "notes.md", "guide.txt"} <= _names(env)


def test_a_broken_dot_git_falls_back_to_walking(env, service, tmp_path):
    root = tmp_path / "odd"
    root.mkdir()
    (root / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    (root / "a.txt").write_text(_text("a"), encoding="utf-8")
    service.sync_folder(str(root), COLLECTION)
    assert "a.txt" in _names(env)


# ── The Updates feed ─────────────────────────────────────────────────────

def test_first_sync_is_an_import_not_news(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    assert source_changes.list_events(COLLECTION)["events"] == []


def test_unchanged_resync_records_nothing(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    service.sync_folder(str(repo), COLLECTION)
    assert source_changes.list_events(COLLECTION)["events"] == []


def test_a_commit_becomes_one_event_with_the_commits(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    first_head = _git(repo, "rev-parse", "HEAD")

    (repo / "readme.txt").write_text(_text("readme", salt=2), encoding="utf-8")  # changed
    (repo / "added.txt").write_text(_text("added"), encoding="utf-8")            # added
    (repo / "notes.md").unlink()                                                  # removed
    _commit(repo, "Rework the docs")
    service.sync_folder(str(repo), COLLECTION, prune_missing=True)

    feed = source_changes.list_events(COLLECTION)
    assert len(feed["events"]) == 1 and feed["latest_id"] == 1
    event = feed["events"][0]
    assert event["summary"] == "1 added, 1 changed, 1 removed"
    assert event["added"] == ["added.txt"]
    assert event["changed"] == ["readme.txt"]
    assert event["removed"] == ["notes.md"]
    assert event["previous_head"] == first_head
    assert event["head"] == _git(repo, "rev-parse", "HEAD")
    assert event["branch"] == "main"
    assert [c["subject"] for c in event["commits"]] == ["Rework the docs"]
    assert event["commits"][0]["author"] == "Ada"
    assert event["trigger"] == "manual"


def test_feed_ids_are_monotonic_and_since_filters(env, service, folder):
    service.sync_folder(str(folder), COLLECTION)
    for n in range(3):
        (folder / "readme.txt").write_text(_text("readme", salt=n + 1), encoding="utf-8")
        service.sync_folder(str(folder), COLLECTION)
    feed = source_changes.list_events(COLLECTION)
    assert [e["id"] for e in feed["events"]] == [1, 2, 3]
    assert [e["id"] for e in source_changes.list_events(COLLECTION, since_id=2)["events"]] == [3]
    assert source_changes.list_events(COLLECTION, since_id=3)["events"] == []
    assert source_changes.list_events(COLLECTION, since_id=3)["latest_id"] == 3


def test_feed_is_capped(env, monkeypatch):
    monkeypatch.setattr(source_changes, "MAX_EVENTS", 5)
    for n in range(8):
        source_changes.record(COLLECTION, source="/x", label="x", added=[f"f{n}"], changed=[], removed=[])
    feed = source_changes.list_events(COLLECTION, limit=100)
    assert len(feed["events"]) == 5 and feed["latest_id"] == 8
    assert feed["events"][0]["id"] == 4


def test_remembered_settings_survive_a_resync(env, service, folder):
    service.sync_folder(str(folder), COLLECTION, remember={"watch": True, "interval_minutes": 5})
    service.sync_folder(str(folder), COLLECTION)
    entry = service.list_sync_folders(COLLECTION)[0]
    assert entry["watch"] is True and entry["interval_minutes"] == 5


# ── Watcher ──────────────────────────────────────────────────────────────

def test_watcher_syncs_due_sources_and_records_the_change(env, service, repo, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    service.sync_folder(str(repo), COLLECTION, remember={"watch": True, "interval_minutes": 10})
    entry = service.list_sync_folders(COLLECTION)[0]

    # Not due yet: nothing happens even though a file changed.
    (repo / "readme.txt").write_text(_text("readme", salt=4), encoding="utf-8")
    _commit(repo, "Tweak readme")
    assert source_watcher.run_due() == {"synced": 0, "unchanged": 0, "error": 0}
    assert source_changes.list_events(COLLECTION)["events"] == []

    later = datetime.utcnow() + timedelta(minutes=11)
    assert source_watcher.run_due(now=later) == {"synced": 1, "unchanged": 0, "error": 0}
    event = source_changes.list_events(COLLECTION)["events"][0]
    assert event["trigger"] == "watch" and event["changed"] == ["readme.txt"]
    assert [c["subject"] for c in event["commits"]] == ["Tweak readme"]
    assert service.list_sync_folders(COLLECTION)[0]["last_error"] is None
    assert entry["path"] == service.list_sync_folders(COLLECTION)[0]["path"]


def test_watcher_ignores_unwatched_sources(env, service, folder, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    service.sync_folder(str(folder), COLLECTION)
    assert source_watcher.run_due(now=datetime.utcnow() + timedelta(days=2)) == \
        {"synced": 0, "unchanged": 0, "error": 0}


def test_a_failing_source_is_reported_not_fatal(env, service, folder, monkeypatch):
    import shutil

    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    service.sync_folder(str(folder), COLLECTION, remember={"watch": True})
    shutil.rmtree(folder)
    counts = source_watcher.run_due(now=datetime.utcnow() + timedelta(days=1))
    assert counts["error"] == 1
    assert "Invalid folder path" in service.list_sync_folders(COLLECTION)[0]["last_error"]


# ── Webhook ──────────────────────────────────────────────────────────────

def test_webhook_posts_a_signed_json_body(env, monkeypatch):
    received = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["body"] = self.rfile.read(length)
            received["signature"] = self.headers.get("X-Clio-Signature")
            self.send_response(200)
            self.end_headers()
            done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(settings, "alert_webhook_url", f"http://127.0.0.1:{server.server_port}/hook")
        monkeypatch.setattr(settings, "alert_webhook_secret", "s3cret")
        source_changes.record(COLLECTION, source="/src", label="docs", added=["a.md"], changed=[], removed=[])
        assert done.wait(10)
    finally:
        server.shutdown()

    body = json.loads(received["body"])
    assert body["event"] == "source.changed" and body["collection_id"] == COLLECTION
    assert "docs: 1 added" in body["text"]
    assert body["change"]["added"] == ["a.md"]
    expected = hmac.new(b"s3cret", received["body"], hashlib.sha256).hexdigest()
    assert received["signature"] == f"sha256={expected}"


def test_webhook_failure_never_breaks_recording(env, monkeypatch):
    monkeypatch.setattr(settings, "alert_webhook_url", "http://127.0.0.1:9/nope")
    assert source_changes.record(COLLECTION, source="/s", label="s", added=["x"], changed=[], removed=[])


def test_webhook_refuses_non_http_schemes(env, monkeypatch):
    monkeypatch.setattr(settings, "alert_webhook_url", "file:///etc/passwd")
    # Must return quietly without opening the file URL.
    source_changes._post_webhook(COLLECTION, {"label": "x", "summary": "y"})


# ── Remote address policy ────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "", "   ", "-oProxyCommand=evil", "ext::sh -c evil", "file:///etc/passwd", "git://example.com/r.git",
    "http://example.com/r.git", "https://user:pw@example.com/r.git", "https://token@example.com/r.git",
    "https://example.com/a b.git", "/etc", "ssh://",
])
def test_unsafe_addresses_are_refused(url, monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "example.com")
    with pytest.raises(git_source.GitRefused):
        git_source.validate_remote(url)


def test_bad_branch_names_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "example.com")
    for branch in ["--upload-pack=x", "a..b", "a b", "-x"]:
        with pytest.raises(git_source.GitRefused):
            git_source.validate_remote("https://example.com/r.git", branch)


def test_allowlist_decides_the_host(monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "git.corp.example, *.mirror.example")
    assert git_source.validate_remote("https://git.corp.example/team/r.git")["host"] == "git.corp.example"
    assert git_source.validate_remote("git@a.mirror.example:team/r.git")["host"] == "a.mirror.example"
    assert git_source.validate_remote("ssh://git@git.corp.example/r.git", "release/1.x")["branch"] == "release/1.x"
    with pytest.raises(git_source.GitRefused, match="allowed git hosts"):
        git_source.validate_remote("https://github.com/o/r.git")


def test_offline_mode_refuses_remotes_unless_allowlisted(monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "")
    monkeypatch.setattr(settings, "offline_mode", True)
    with pytest.raises(git_source.GitRefused, match="Offline mode"):
        git_source.validate_remote("https://github.com/o/r.git")
    monkeypatch.setattr(settings, "git_allowed_hosts", "git.corp.example")
    assert git_source.validate_remote("https://git.corp.example/r.git")


def test_private_hosts_need_the_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "")
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(settings, "link_allow_private_networks", False)
    with pytest.raises(git_source.GitRefused, match="private network"):
        git_source.validate_remote("https://127.0.0.1/r.git")


# ── Clones ───────────────────────────────────────────────────────────────

@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A bare 'remote' on disk, reachable as file:// with the policy relaxed."""
    work = tmp_path / "upstream"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "a.txt").write_text(_text("a"), encoding="utf-8")
    _commit(work, "first")
    monkeypatch.setattr(git_source, "ALLOW_PROTOCOL", "https:ssh:file")
    monkeypatch.setattr(git_source, "FILE_PROTOCOL", "always")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    url = work.resolve().as_uri()
    monkeypatch.setattr(git_source, "validate_remote",
                        lambda u, b=None: {"url": u, "host": "local", "branch": (b or "").strip()})
    return {"work": work, "url": url}


def test_clone_then_refresh_follows_the_remote(remote):
    dest = git_source.ensure_clone(remote["url"], "main")
    assert (dest / "a.txt").exists() and dest.parent == git_source.repos_dir()
    first = git_source.head_info(dest)["head"]
    assert git_source.remote_head(remote["url"], "main") == first

    (remote["work"] / "b.txt").write_text(_text("b"), encoding="utf-8")
    _commit(remote["work"], "second")
    assert git_source.remote_head(remote["url"], "main") != first

    assert git_source.ensure_clone(remote["url"], "main") == dest
    assert (dest / "b.txt").exists()
    head = git_source.head_info(dest)["head"]
    assert [c["subject"] for c in git_source.commits_between(dest, first, head)] == ["second"]


def test_a_failed_clone_leaves_nothing_behind(remote):
    with pytest.raises(git_source.GitError):
        git_source.ensure_clone(remote["url"] + "-missing", "main")
    assert not any(git_source.repos_dir().glob("*")) if git_source.repos_dir().exists() else True


def test_http_token_is_scoped_to_the_repository_host(monkeypatch):
    monkeypatch.setattr(settings, "git_http_token", "tok")
    monkeypatch.setattr(settings, "git_allowed_hosts", "*.corp.example")
    env = git_source._auth_env("https://git.corp.example/r.git", "git.corp.example")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://git.corp.example/.extraHeader"
    assert "tok" not in env["GIT_CONFIG_KEY_0"]
    assert git_source._auth_env("ssh://git@git.corp.example/r.git", "git.corp.example") == {}
    monkeypatch.setattr(settings, "git_http_token", "")
    assert git_source._auth_env("https://git.corp.example/r.git", "git.corp.example") == {}


def test_http_token_is_never_offered_to_a_host_nobody_allowlisted(monkeypatch):
    """Anyone who can add a source can type an address; the deployment's token
    must not follow them to a server they control."""
    monkeypatch.setattr(settings, "git_http_token", "tok")
    monkeypatch.setattr(settings, "git_allowed_hosts", "")
    assert git_source._auth_env("https://github.com/org/repo.git", "github.com") == {}
    monkeypatch.setattr(settings, "git_allowed_hosts", "git.corp.example")
    assert git_source._auth_env("https://evil.example/repo.git", "evil.example") == {}
    assert git_source._auth_env("https://git.corp.example/repo.git", "git.corp.example") != {}


# ── API ──────────────────────────────────────────────────────────────────

def test_changes_endpoint_and_watch_toggle(client, env, folder):
    body = {"path": str(folder), "collection_id": COLLECTION}
    first = client.post("/documents/sync-folder", json=body)
    assert first.status_code in (200, 202)
    # The sync indexes on a background thread. Rewriting readme.txt before
    # that job has read it would let it index the new text, and the second
    # sync would then (rightly) see no change.
    if first.json()["job_id"]:
        assert _wait_for_job(client, first.json()["job_id"])["status"] == "completed"

    watched = client.post("/documents/sync-folders/watch",
                          json={**body, "watch": True, "interval_minutes": 30})
    assert watched.status_code == 200, watched.text
    assert watched.json()["watch"] is True and watched.json()["interval_minutes"] == 30
    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()
    assert listed["folders"][0]["watch"] is True

    (folder / "readme.txt").write_text(_text("readme", salt=6), encoding="utf-8")
    second = client.post("/documents/sync-folder", json=body)
    assert second.status_code == 202 and second.json()["job_id"]
    assert _wait_for_job(client, second.json()["job_id"])["status"] == "completed"
    feed = client.get("/documents/changes", params={"collection_id": COLLECTION}).json()
    assert feed["latest_id"] == 1 and feed["events"][0]["changed"] == ["readme.txt"]
    assert client.get("/documents/changes", params={"collection_id": COLLECTION, "since": 1}).json()["events"] == []

    assert client.post("/documents/sync-folders/watch",
                       json={**body, "path": str(folder / "nope"), "watch": True}).status_code == 404


def test_sync_git_endpoint_enforces_the_address_policy(client, env, monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "")
    monkeypatch.setattr(settings, "offline_mode", True)
    r = client.post("/documents/sync-git",
                    json={"url": "https://github.com/o/r.git", "collection_id": COLLECTION})
    assert r.status_code == 403 and "Offline mode" in r.json()["detail"]

    monkeypatch.setattr(settings, "offline_mode", False)
    r = client.post("/documents/sync-git",
                    json={"url": "https://u:p@example.com/r.git", "collection_id": COLLECTION})
    assert r.status_code == 403 and "credentials" in r.json()["detail"]


def test_sync_git_endpoint_clones_syncs_and_remembers_the_remote(client, env, remote):
    r = client.post("/documents/sync-git",
                    json={"url": remote["url"], "branch": "main", "collection_id": COLLECTION, "watch": True})
    assert r.status_code in (200, 202), r.text
    assert _wait_for_job(client, r.json()["job_id"])["status"] == "completed"
    assert "a.txt" in {Path(d["source_path"]).name for d in env["indexer"].list_documents()}

    folders = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"]
    assert len(folders) == 1
    folder = folders[0]
    assert folder["git"] is True and folder["git_url"] == remote["url"]
    assert folder["watch"] is True and folder["label"] == "upstream"
    assert folder["git_head"] == git_source.head_info(Path(folder["path"]))["head"]


def test_watcher_pulls_a_remote_only_when_it_moved(env, service, remote, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    dest = git_source.ensure_clone(remote["url"], "main")
    service.sync_folder(str(dest), COLLECTION, prune_missing=True,
                        remember={"git_url": remote["url"], "git_ref": "main", "watch": True})
    later = datetime.utcnow() + timedelta(hours=1)

    assert source_watcher.run_due(now=later) == {"synced": 0, "unchanged": 1, "error": 0}
    assert source_changes.list_events(COLLECTION)["events"] == []

    (remote["work"] / "b.txt").write_text(_text("b"), encoding="utf-8")
    (remote["work"] / "a.txt").unlink()
    _commit(remote["work"], "swap files")
    assert source_watcher.run_due(now=later + timedelta(hours=1)) == {"synced": 1, "unchanged": 0, "error": 0}
    event = source_changes.list_events(COLLECTION)["events"][0]
    assert event["added"] == ["b.txt"] and event["removed"] == ["a.txt"]
    assert event["label"] == "upstream"
    assert [c["subject"] for c in event["commits"]] == ["swap files"]
