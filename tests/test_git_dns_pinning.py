"""Git connects to the address Clio checked, not to a fresh DNS answer.

validate_remote resolves a remote's host and refuses it unless every address
is public. git then used to resolve the name again for itself, so a name that
answered with a public address for the check and an internal one a moment
later (DNS rebinding) could still clone from inside the network. Now the
checked address is handed to git: http.curloptResolve for https, HostName for
ssh (HostKeyAlias keeps the host key check on the name).
"""

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from config import settings
from services import git_source, link_fetcher

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
INTERNAL = "10.0.0.5"


@pytest.fixture()
def resolver(monkeypatch):
    """A fake DNS: each call to the resolver pops the next answer for that host."""
    monkeypatch.setattr(settings, "git_allowed_hosts", "")
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(settings, "link_allow_private_networks", False)
    answers, calls = {}, []

    def resolve(host):
        calls.append(host)
        queue = answers.get(host) or [[PUBLIC]]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(link_fetcher, "_resolve_host", resolve)
    return {"answers": answers, "calls": calls}


@pytest.fixture()
def git_calls(monkeypatch):
    """Record every git command line and environment instead of running git."""
    seen = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, b"git version 2.47.1\n", b"")
        seen.append({"cmd": cmd, "env": kwargs.get("env") or {}})
        out = b"0123456789abcdef0123456789abcdef01234567\tHEAD\n"
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(git_source, "git_available", lambda: True)
    monkeypatch.setattr(git_source.subprocess, "run", fake_run)
    git_source.git_version.cache_clear()
    yield seen
    getattr(git_source.git_version, "cache_clear", lambda: None)()  # a test may have replaced it


def _resolve_options(call):
    cmd = call["cmd"]
    return [cmd[i + 1] for i, part in enumerate(cmd[:-1]) if part == "-c" and cmd[i + 1].startswith("http.curloptResolve=")]


# ── what validate_remote records ──────────────────────────────────────────


@pytest.mark.parametrize("url, scheme, port", [
    ("https://git.example.org/team/r.git", "https", 443),
    ("https://git.example.org:8443/team/r.git", "https", 8443),
    ("ssh://git@git.example.org/team/r.git", "ssh", 22),
    ("ssh://git@git.example.org:2222/team/r.git", "ssh", 2222),
    ("git@git.example.org:team/r.git", "ssh", 22),
])
def test_the_checked_address_is_recorded(resolver, url, scheme, port):
    info = git_source.validate_remote(url)

    assert info["pin"] == {"scheme": scheme, "address": PUBLIC, "port": port}
    assert resolver["calls"] == ["git.example.org"]


def test_a_host_with_any_internal_answer_is_still_refused(resolver):
    resolver["answers"]["git.example.org"] = [[PUBLIC, INTERNAL]]

    with pytest.raises(git_source.GitRefused, match="private network"):
        git_source.validate_remote("https://git.example.org/r.git")


@pytest.mark.parametrize("setting, value", [
    ("git_allowed_hosts", "git.example.org"),
    ("link_allow_private_networks", True),
])
def test_nothing_is_pinned_when_the_operator_decided_instead(resolver, monkeypatch, setting, value):
    """An allowlisted host, or private networks allowed: no check ran, so nothing to hold git to."""
    monkeypatch.setattr(settings, setting, value)

    assert git_source.validate_remote("https://git.example.org/r.git")["pin"] is None


def test_an_address_literal_is_not_pinned(resolver):
    resolver["answers"][PUBLIC] = [[PUBLIC]]

    assert git_source.validate_remote(f"https://{PUBLIC}/r.git")["pin"] is None


def test_a_bad_port_is_refused(resolver):
    with pytest.raises(git_source.GitRefused, match="invalid port"):
        git_source.validate_remote("https://git.example.org:99999/r.git")


# ── what git is told ──────────────────────────────────────────────────────


def test_a_rebinding_name_cannot_move_ls_remote(resolver, git_calls):
    """The check sees the public answer; git is given that answer, never a later one."""
    resolver["answers"]["rebind.example.org"] = [[PUBLIC], [INTERNAL]]

    assert git_source.remote_head("https://rebind.example.org/team/r.git")

    assert _resolve_options(git_calls[-1]) == [f"http.curloptResolve=rebind.example.org:443:{PUBLIC}"]
    assert resolver["calls"] == ["rebind.example.org"]  # git never asked Clio's resolver again


def test_clone_and_refresh_are_both_pinned(resolver, git_calls, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    url = "https://git.example.org:8443/team/r.git"

    dest = git_source.ensure_clone(url)
    clone = next(c for c in git_calls if "clone" in c["cmd"])
    assert _resolve_options(clone) == [f"http.curloptResolve=git.example.org:8443:{PUBLIC}"]

    (dest / ".git").mkdir(parents=True)
    git_calls.clear()
    git_source.ensure_clone(url)
    fetch = next(c for c in git_calls if "fetch" in c["cmd"])
    assert _resolve_options(fetch) == [f"http.curloptResolve=git.example.org:8443:{PUBLIC}"]
    # reset and clean touch only the local clone: nothing to pin there.
    assert all(not _resolve_options(c) for c in git_calls if "fetch" not in c["cmd"])


def test_an_ipv6_answer_is_bracketed(resolver, git_calls):
    resolver["answers"]["git.example.org"] = [[PUBLIC_V6]]

    git_source.remote_head("https://git.example.org/r.git")

    assert _resolve_options(git_calls[-1]) == [f"http.curloptResolve=git.example.org:443:[{PUBLIC_V6}]"]


@pytest.mark.parametrize("url", ["ssh://git@git.example.org/team/r.git", "git@git.example.org:team/r.git"])
def test_ssh_connects_to_the_checked_address_and_checks_the_key_by_name(resolver, git_calls, monkeypatch, url):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)

    git_source.remote_head(url)

    ssh = git_calls[-1]["env"]["GIT_SSH_COMMAND"]
    assert ssh.startswith(git_source._DEFAULT_SSH_COMMAND)
    assert ssh.endswith(f"-o HostName={PUBLIC} -o HostKeyAlias=git.example.org")
    assert not _resolve_options(git_calls[-1])


def test_an_operators_own_ssh_command_is_kept(resolver, git_calls, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/deploy -o BatchMode=yes")

    git_source.remote_head("git@git.example.org:team/r.git")

    assert git_calls[-1]["env"]["GIT_SSH_COMMAND"] == (
        f"ssh -i /keys/deploy -o BatchMode=yes -o HostName={PUBLIC} -o HostKeyAlias=git.example.org")


def test_an_allowlisted_host_is_left_to_git(resolver, git_calls, monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "git.example.org")
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)

    git_source.remote_head("https://git.example.org/r.git")
    git_source.remote_head("git@git.example.org:team/r.git")

    assert not any(_resolve_options(c) for c in git_calls)
    assert all(c["env"]["GIT_SSH_COMMAND"] == git_source._DEFAULT_SSH_COMMAND for c in git_calls)
    assert resolver["calls"] == []


def test_a_git_too_old_to_pin_refuses_rather_than_cloning_unpinned(resolver, git_calls, monkeypatch):
    monkeypatch.setattr(git_source, "git_version", lambda: (2, 36))

    with pytest.raises(git_source.GitRefused, match="git 2.37 or later.*GIT_ALLOWED_HOSTS"):
        git_source.remote_head("https://git.example.org/r.git")
    assert git_calls == []


# ── the real git binary honours the pin ───────────────────────────────────


@pytest.mark.skipif(not git_source.git_available(), reason="git is not installed")
def test_real_git_connects_to_the_pinned_address_not_to_dns(monkeypatch):
    """A name that does not resolve at all still reaches the pinned address,
    so git's own lookup played no part."""
    git_source.git_version.cache_clear()
    if git_source.git_version() < git_source.MIN_PIN_GIT_VERSION:
        pytest.skip("this git cannot pin")
    hosts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(git_source, "ALLOW_PROTOCOL", "http")
    port = server.server_port
    url = f"http://pinned.invalid:{port}/r.git"
    remote = {"host": "pinned.invalid", "pin": {"scheme": "https", "address": "127.0.0.1", "port": port}}
    try:
        with pytest.raises(git_source.GitError):
            git_source._run(["ls-remote", "--", url, "HEAD"], timeout=30)  # unpinned: the name does not resolve
        assert hosts == []
        with pytest.raises(git_source.GitError):
            git_source._run(["ls-remote", "--", url, "HEAD"], timeout=30, remote=remote)  # 404, but it arrived
    finally:
        server.shutdown()
    assert hosts and hosts[0] == f"pinned.invalid:{port}"
