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
from services.app_database import app_db
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


def _counts(**outcomes):
    """The watcher's pass summary: every outcome zero but the ones given."""
    return {**{k: 0 for k in source_watcher.OUTCOMES}, **outcomes}


def _entry(service):
    return service.list_sync_folders(COLLECTION)[0]


def _fresh(service):
    return source_watcher.freshness(_entry(service))


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
    assert source_watcher.run_due() == _counts()
    assert source_changes.list_events(COLLECTION)["events"] == []

    later = datetime.utcnow() + timedelta(minutes=11)
    assert source_watcher.run_due(now=later) == _counts(synced=1)
    event = source_changes.list_events(COLLECTION)["events"][0]
    assert event["trigger"] == "watch" and event["changed"] == ["readme.txt"]
    assert [c["subject"] for c in event["commits"]] == ["Tweak readme"]
    assert service.list_sync_folders(COLLECTION)[0]["last_error"] is None
    assert entry["path"] == service.list_sync_folders(COLLECTION)[0]["path"]


def test_watcher_ignores_unwatched_sources(env, service, folder, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    service.sync_folder(str(folder), COLLECTION)
    assert source_watcher.run_due(now=datetime.utcnow() + timedelta(days=2)) == _counts()


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

    assert source_watcher.run_due(now=later) == _counts(unchanged=1)
    assert source_changes.list_events(COLLECTION)["events"] == []

    (remote["work"] / "b.txt").write_text(_text("b"), encoding="utf-8")
    (remote["work"] / "a.txt").unlink()
    _commit(remote["work"], "swap files")
    assert source_watcher.run_due(now=later + timedelta(hours=1)) == _counts(synced=1)
    event = source_changes.list_events(COLLECTION)["events"][0]
    assert event["added"] == ["b.txt"] and event["removed"] == ["a.txt"]
    assert event["label"] == "upstream"
    assert [c["subject"] for c in event["commits"]] == ["swap files"]


# ── Knowing a source is out of date ──────────────────────────────────────

def test_a_fresh_checkout_reads_as_current(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    state = _fresh(service)
    assert state["out_of_date"] is False and state["stale_reason"] is None
    assert state["current_head"] == _git(repo, "rev-parse", "HEAD")
    assert _entry(service)["git_fingerprint"]


def test_new_commits_make_a_checkout_out_of_date_until_synced(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    (repo / "readme.txt").write_text(_text("readme", salt=2), encoding="utf-8")
    _commit(repo, "one")
    (repo / "notes.md").write_text(_text("notes", salt=2), encoding="utf-8")
    _commit(repo, "two")

    state = _fresh(service)
    assert state["out_of_date"] is True
    assert state["new_commits"] == 2
    assert state["stale_reason"] == "2 new commits since the last sync"
    assert state["current_head"] == _git(repo, "rev-parse", "HEAD")

    service.sync_folder(str(repo), COLLECTION)
    assert _fresh(service)["out_of_date"] is False


def test_uncommitted_edits_make_a_checkout_out_of_date_without_hashing(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    assert _fresh(service)["out_of_date"] is False

    (repo / "readme.txt").write_text(_text("readme", salt=3), encoding="utf-8")
    # Nothing is read: the verdict comes from `git status` and stat alone.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.governance.content_hash_of",
                   lambda p: pytest.fail("freshness must not hash files"))
        state = _fresh(service)
    assert state["out_of_date"] is True
    assert state["stale_reason"] == "Files changed since the last sync"

    (repo / "scratch.txt").write_text(_text("scratch"), encoding="utf-8")  # untracked counts too
    service.sync_folder(str(repo), COLLECTION)
    assert _fresh(service)["out_of_date"] is False
    assert "scratch.txt" in _names(env)


def test_fingerprint_ignores_what_git_ignores(repo):
    before = git_source.worktree_fingerprint(repo)
    (repo / "ignored.txt").write_text("changed, but .gitignore says no", encoding="utf-8")
    assert git_source.worktree_fingerprint(repo) == before
    (repo / "readme.txt").write_text("edited", encoding="utf-8")
    assert git_source.worktree_fingerprint(repo) != before


def test_an_empty_repository_is_not_a_checkout(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q", "-b", "main")
    assert git_source.head_info(empty) is None
    assert git_source.worktree_fingerprint(empty) is None


def test_an_unfinished_indexing_run_is_out_of_date(env, service, repo):
    service.sync_folder(str(repo), COLLECTION)
    job_id = _entry(service)["job_id"]
    assert job_id
    app_db.update_upload_job(job_id, status="failed", error="the server restarted")
    state = _fresh(service)
    assert state["out_of_date"] is True
    assert state["stale_reason"] == "The last indexing run did not finish"

    app_db.update_upload_job(job_id, status="running")
    state = _fresh(service)
    assert state["indexing"] is True and state["out_of_date"] is False


def test_a_plain_folder_has_no_cheap_verdict(env, service, folder):
    service.sync_folder(str(folder), COLLECTION)
    (folder / "readme.txt").write_text(_text("readme", salt=9), encoding="utf-8")
    assert _fresh(service)["out_of_date"] is False  # Sync tells; nothing is hashed here


def test_watcher_leaves_a_source_alone_while_its_job_is_running(env, service, repo, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    service.sync_folder(str(repo), COLLECTION, remember={"watch": True})
    app_db.update_upload_job(_entry(service)["job_id"], status="running")
    (repo / "readme.txt").write_text(_text("readme", salt=5), encoding="utf-8")
    _commit(repo, "while indexing")
    assert source_watcher.run_due(now=datetime.utcnow() + timedelta(hours=1)) == _counts(busy=1)
    assert source_changes.list_events(COLLECTION)["events"] == []
    assert _entry(service)["last_error"] is None


def test_watcher_repeats_a_sync_whose_job_failed_even_when_the_tip_stood_still(env, service, remote, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    dest = git_source.ensure_clone(remote["url"], "main")
    service.sync_folder(str(dest), COLLECTION, prune_missing=True,
                        remember={"git_url": remote["url"], "git_ref": "main", "watch": True})
    later = datetime.utcnow() + timedelta(hours=1)
    assert source_watcher.run_due(now=later) == _counts(unchanged=1)

    app_db.update_upload_job(_entry(service)["job_id"], status="failed", error="embedder down")
    assert _fresh(service)["out_of_date"] is True
    assert source_watcher.run_due(now=later + timedelta(hours=1)) == _counts(synced=1)
    assert _fresh(service)["out_of_date"] is False


def test_watcher_makes_a_lost_clone_again(env, service, remote, monkeypatch):
    import shutil

    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    dest = git_source.ensure_clone(remote["url"], "main")
    service.sync_folder(str(dest), COLLECTION, prune_missing=True,
                        remember={"git_url": remote["url"], "git_ref": "main", "watch": True})
    shutil.rmtree(dest)
    assert _fresh(service)["stale_reason"] == "The clone is gone; Sync makes it again"
    assert source_watcher.run_due(now=datetime.utcnow() + timedelta(hours=1)) == _counts(synced=1)
    assert (dest / "a.txt").exists()
    assert _fresh(service)["out_of_date"] is False


def test_an_unwatched_remote_is_probed_so_the_panel_can_say_it_is_behind(env, service, remote, monkeypatch):
    import services.upload_service as us

    monkeypatch.setattr(us, "upload_service", service)
    dest = git_source.ensure_clone(remote["url"], "main")
    service.sync_folder(str(dest), COLLECTION, prune_missing=True,
                        remember={"git_url": remote["url"], "git_ref": "main", "watch": False})
    first = git_source.head_info(dest)["head"]
    later = datetime.utcnow() + timedelta(hours=1)
    assert source_watcher.run_due(now=later) == _counts(probed=1)
    assert _entry(service)["upstream_head"] == first
    assert _fresh(service)["out_of_date"] is False

    (remote["work"] / "b.txt").write_text(_text("b"), encoding="utf-8")
    _commit(remote["work"], "second")
    tip = _git(remote["work"], "rev-parse", "HEAD")
    assert source_watcher.run_due(now=later + timedelta(hours=1)) == _counts(probed=1)
    entry = _entry(service)
    assert entry["upstream_head"] == tip and entry["git_head"] == first  # noted, not pulled
    assert "b.txt" not in _names(env)
    assert source_changes.list_events(COLLECTION)["events"] == []
    assert _fresh(service)["stale_reason"] == "New commits upstream"

    # Not due again before the interval (the probe stamped the real clock),
    # and never synced on its own.
    assert source_watcher.run_due(now=datetime.utcnow() + timedelta(minutes=5)) == _counts()


def test_sync_folder_endpoint_fetches_a_managed_clone_first(client, env, remote):
    r = client.post("/documents/sync-git", json={"url": remote["url"], "branch": "main", "collection_id": COLLECTION})
    assert r.status_code in (200, 202), r.text
    assert _wait_for_job(client, r.json()["job_id"])["status"] == "completed"
    clone = r.json()["path"]

    (remote["work"] / "b.txt").write_text(_text("b"), encoding="utf-8")
    (remote["work"] / "a.txt").unlink()
    _commit(remote["work"], "swap files")
    tip = _git(remote["work"], "rev-parse", "HEAD")

    # The Sync button posts the clone's path, exactly like a folder.
    r = client.post("/documents/sync-folder", json={"path": clone, "collection_id": COLLECTION})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] == 1 and body["pruned"] == ["a.txt"]  # deleted upstream: always pruned from a clone
    assert _wait_for_job(client, body["job_id"])["status"] == "completed"
    names = {Path(d["source_path"]).name for d in env["indexer"].list_documents()}
    assert "b.txt" in names and "a.txt" not in names

    folder = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"][0]
    assert folder["git_head"] == tip and folder["upstream_head"] == tip
    assert folder["out_of_date"] is False
    event = client.get("/documents/changes", params={"collection_id": COLLECTION}).json()["events"][-1]
    assert [c["subject"] for c in event["commits"]] == ["swap files"]


def test_sync_folder_endpoint_makes_a_lost_clone_again(client, env, remote):
    import shutil

    r = client.post("/documents/sync-git", json={"url": remote["url"], "branch": "main", "collection_id": COLLECTION})
    assert r.status_code in (200, 202), r.text
    assert _wait_for_job(client, r.json()["job_id"])["status"] == "completed"
    clone = Path(r.json()["path"])
    shutil.rmtree(clone)

    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"][0]
    assert listed["exists"] is False and listed["out_of_date"] is True

    r = client.post("/documents/sync-folder", json={"path": str(clone), "collection_id": COLLECTION})
    assert r.status_code == 200, r.text  # same bytes, already indexed
    assert clone.is_dir()
    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"][0]
    assert listed["exists"] is True and listed["out_of_date"] is False


def test_sync_folders_listing_reports_freshness(client, env, repo):
    body = {"path": str(repo), "collection_id": COLLECTION}
    first = client.post("/documents/sync-folder", json=body)
    assert first.status_code in (200, 202)
    if first.json()["job_id"]:
        assert _wait_for_job(client, first.json()["job_id"])["status"] == "completed"

    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"][0]
    assert listed["git"] is True and listed["out_of_date"] is False and listed["current_head"] == listed["git_head"]

    (repo / "readme.txt").write_text(_text("readme", salt=7), encoding="utf-8")
    _commit(repo, "edit")
    listed = client.get("/documents/sync-folders", params={"collection_id": COLLECTION}).json()["folders"][0]
    assert listed["out_of_date"] is True
    assert listed["stale_reason"] == "1 new commit since the last sync"
    assert listed["new_commits"] == 1
    assert listed["current_head"] == _git(repo, "rev-parse", "HEAD") != listed["git_head"]


def test_clone_lock_is_shared_per_clone(tmp_path):
    a = git_source.clone_lock(tmp_path / "x")
    assert a is git_source.clone_lock(tmp_path / "x")
    assert a is not git_source.clone_lock(tmp_path / "y")
