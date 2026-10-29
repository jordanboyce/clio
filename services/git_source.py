"""Git repositories as sources: file listing, commit tracking, safe remote clones.

Clio only ever *reads* a repository. Everything shells out to the ``git``
binary with a locked-down environment rather than reimplementing git: it
already understands .gitignore, ssh keys, credential helpers and every
protocol quirk, and those are exactly the things that go wrong on a locked-down
network.

Hardening applied to every invocation:

* no prompts (``GIT_TERMINAL_PROMPT=0``), no LFS downloads, no hooks, no
  submodule recursion, only ``https`` and ``ssh`` transports;
* ``safe.directory`` is pinned to the one path being read, so a repository
  owned by another account (a bind mount into a container) is readable
  without lowering the check globally;
* credentials never appear in a URL (refused) or on a command line; an HTTPS
  token from ``GIT_HTTP_TOKEN`` is handed over through the environment and
  scoped to the repository's own host.

Remote clones are shallow (one branch, depth 1) and live under
``DATA_DIR/repos``; a watcher refresh is a ``fetch`` plus a hard reset, so the
clone is a disposable mirror, never a working copy anyone edits.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config import settings

logger = logging.getLogger(__name__)

# Transports git may use. The local ``file`` transport is off: a clone must
# never be steered at a path on this server. Tests widen these to clone from a
# repository on disk.
ALLOW_PROTOCOL = "https:ssh"
FILE_PROTOCOL = "never"

# How much history a managed clone keeps. One commit would do for indexing,
# but the Updates feed lists the commits between two syncs, and a fetch that
# re-shallows to the new tip alone would make the previous head unreachable.
# This many commits stay, so up to that many syncs' worth of history can be
# named (MAX_COMMITS in source_changes.py is smaller); further back, the feed
# shows the tip only.
CLONE_DEPTH = 25


class GitError(RuntimeError):
    """git failed, or is not available."""


class GitRefused(GitError):
    """The repository address is not allowed by this deployment's policy."""


_SCP_LIKE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s:][^\s]*)$")
_REF_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,199}$")


def git_available() -> bool:
    return shutil.which("git") is not None


def is_worktree(path: Path) -> bool:
    """Cheap check (no subprocess): the folder has a ``.git`` entry."""
    return (Path(path) / ".git").exists()


def repos_dir() -> Path:
    return Path(settings.data_dir) / "repos"


# ── Running git ──────────────────────────────────────────────────────────

def _base_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ALLOW_PROTOCOL": ALLOW_PROTOCOL,
        "GIT_OPTIONAL_LOCKS": "0",
        # A hung ssh must fail rather than wait for a passphrase or host-key prompt.
        "GIT_SSH_COMMAND": env.get("GIT_SSH_COMMAND")
        or "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20",
    })
    env.update(extra or {})
    return env


def _run(args: List[str], *, cwd: Optional[Path] = None, safe_dir: Optional[Path] = None,
         env_extra: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> str:
    if not git_available():
        raise GitError("git is not installed on this server")
    cmd = ["git", "-c", "core.hooksPath=", "-c", f"protocol.file.allow={FILE_PROTOCOL}",
           "-c", "core.fsmonitor=false", "-c", "core.quotepath=off"]
    if safe_dir is not None:
        cmd += ["-c", f"safe.directory={Path(safe_dir).resolve().as_posix()}"]
    cmd += args
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=_base_env(env_extra),
            capture_output=True, timeout=timeout or float(settings.git_timeout_seconds),
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {int(e.timeout)}s") from e
    if proc.returncode != 0:
        raise GitError(_scrub(proc.stderr.decode("utf-8", "replace").strip()) or f"git {args[0]} failed")
    return proc.stdout.decode("utf-8", "replace")


def _scrub(text: str) -> str:
    """Last line of git's complaint, with anything credential-shaped removed."""
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1", text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " ".join(lines[-2:])[:400]


# ── Local work trees ─────────────────────────────────────────────────────

def head_info(path: Path) -> Optional[Dict[str, str]]:
    """``{head, branch}`` for a work tree, or None when it is not a usable repository.

    One git call: the Sources panel asks this for every git source it lists.
    """
    path = Path(path)
    if not is_worktree(path) or not git_available():
        return None
    try:
        out = _run(["rev-parse", "HEAD", "--abbrev-ref", "HEAD"], cwd=path, safe_dir=path, timeout=30)
    except GitError as e:
        logger.info(f"{path} has a .git entry but is not readable as a repository: {e}")
        return None
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return None  # an empty repository: no commit to read
    return {"head": lines[0], "branch": lines[1] if len(lines) > 1 else "HEAD"}


def worktree_fingerprint(path: Path, head: Optional[str] = None) -> Optional[str]:
    """A cheap digest of what a sync of this checkout would see.

    HEAD plus, for every path ``git status`` lists (modified, staged, deleted
    or untracked-but-not-ignored: exactly the files ``list_files`` indexes
    beyond the commit), its size and mtime. Nothing is hashed, so this costs
    one ``git status`` however large the tree. Equal fingerprints mean no
    indexed file can have changed; a differing one says *something* did (a
    ``touch`` counts, which only costs one unnecessary sync).

    None when the folder is not a readable repository. ``head`` saves the
    call when the caller has just read it.
    """
    path = Path(path)
    if head is None:
        info = head_info(path)
        if info is None:
            return None
        head = info["head"]
    try:
        out = _run(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
                   cwd=path, safe_dir=path, timeout=120)
    except GitError as e:
        logger.info(f"git status failed in {path}: {e}")
        return None
    digest = hashlib.sha256(head.encode())
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        rel = entry[3:]
        digest.update(rel.encode("utf-8", "surrogateescape"))
        try:
            st = (path / rel).stat()
            digest.update(f"\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        except OSError:
            digest.update(b"\0gone\n")
    return digest.hexdigest()


def count_commits(path: Path, old: Optional[str], new: Optional[str]) -> Optional[int]:
    """How many commits ``new`` is ahead of ``old``; None when git cannot say
    (``old`` rewritten away, or beyond a shallow clone's history)."""
    if not old or not new:
        return None
    if old == new:
        return 0
    try:
        out = _run(["rev-list", "--count", f"{old}..{new}"], cwd=path, safe_dir=path, timeout=60)
        return int(out.strip())
    except (GitError, ValueError):
        return None


def list_files(path: Path, recursive: bool = True) -> Optional[List[str]]:
    """Paths (relative to ``path``, forward slashes) of tracked files plus
    untracked files git does not ignore.

    ``None`` means "not a readable repository" and the caller falls back to a
    plain directory walk. Only the repository root counts: a sub-folder of a
    repository is walked like any other folder.
    """
    path = Path(path)
    if not is_worktree(path) or not git_available():
        return None
    try:
        out = _run(["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                   cwd=path, safe_dir=path)
    except GitError as e:
        logger.info(f"git ls-files failed in {path}, walking the directory instead: {e}")
        return None
    return [rel for rel in out.split("\0") if rel and (recursive or "/" not in rel)]


def commits_between(path: Path, old: Optional[str], new: Optional[str], limit: int = 20) -> List[Dict[str, str]]:
    """Newest-first commits in ``old..new`` (the last ``limit`` when ``old`` is unknown or gone)."""
    if not new or old == new:
        return []
    fmt = "%H%x1f%an%x1f%aI%x1f%s"
    rev = f"{old}..{new}" if old else new
    try:
        out = _run(["log", f"--format={fmt}", f"-n{int(limit)}", rev], cwd=path, safe_dir=path, timeout=60)
    except GitError:
        out = None
    if out is None:  # old commit no longer reachable (force push): show the tip instead
        try:
            out = _run(["log", f"--format={fmt}", "-n5", new], cwd=path, safe_dir=path, timeout=60)
        except GitError:
            return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0][:10], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return commits


# ── Remote repositories ──────────────────────────────────────────────────

def _allowed_hosts() -> List[str]:
    return [h.strip().lower() for h in (settings.git_allowed_hosts or "").split(",") if h.strip()]


def _host_allowed(host: str, allowed: List[str]) -> bool:
    return any(host == a or (a.startswith("*.") and host.endswith(a[1:])) for a in allowed)


def validate_remote(url: str, branch: Optional[str] = None) -> Dict[str, str]:
    """Normalize a repository address and enforce the deployment's policy.

    Returns ``{url, host, branch}``. Raises :class:`GitRefused` for anything
    the server must not clone.
    """
    from services import link_fetcher

    raw = (url or "").strip()
    if not raw or raw.startswith("-") or re.search(r"[\s\x00-\x1f]", raw):
        raise GitRefused("That is not a repository address")
    ref = (branch or "").strip()
    if ref and (not _REF_OK.match(ref) or ".." in ref):
        raise GitRefused("That is not a valid branch name")

    scp = _SCP_LIKE.match(raw)
    if scp:
        host = scp.group("host").lower()
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in ("https", "ssh"):
            raise GitRefused("Only https:// and ssh:// repository addresses (or user@host:path) can be cloned")
        if parsed.password or (parsed.scheme == "https" and parsed.username):
            raise GitRefused(
                "Do not put credentials in the address. Use GIT_HTTP_TOKEN, an ssh key or a git credential helper."
            )
        if not parsed.hostname:
            raise GitRefused("That repository address has no host")
        host = parsed.hostname.lower()

    allowed = _allowed_hosts()
    if allowed:
        if not _host_allowed(host, allowed):
            raise GitRefused(f"{host} is not in this deployment's list of allowed git hosts (GIT_ALLOWED_HOSTS)")
    elif settings.offline_mode:
        raise GitRefused(
            "Offline mode: remote repositories are disabled. Index a local checkout, or set "
            "GIT_ALLOWED_HOSTS to your internal git server."
        )
    elif not settings.link_allow_private_networks:
        try:
            addresses = link_fetcher._resolve_host(host)
        except link_fetcher.LinkError as e:
            raise GitRefused(str(e)) from e
        if not all(link_fetcher._is_public_address(a) for a in addresses):
            raise GitRefused(
                f"{host} is on a private network. Add it to GIT_ALLOWED_HOSTS to allow it."
            )
    return {"url": raw, "host": host, "branch": ref}


def clone_path(url: str, branch: str = "") -> Path:
    key = hashlib.sha256(f"{url}\n{branch}".encode()).hexdigest()[:16]
    return repos_dir() / key


def display_name(url: str) -> str:
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _auth_env(url: str, host: str) -> Dict[str, str]:
    """HTTPS token via the environment, scoped to this repository's host only.

    The token is the deployment's credential and anyone who can add a source
    can type any address, so it is only ever offered to a host the operator
    put in GIT_ALLOWED_HOSTS. With no allowlist, no token is sent anywhere.
    """
    token = settings.git_http_token
    if not token or not url.startswith("https://"):
        return {}
    if not _host_allowed(host.lower(), _allowed_hosts()):
        logger.info(f"GIT_HTTP_TOKEN is not sent to {host}: it is not in GIT_ALLOWED_HOSTS")
        return {}
    basic = base64.b64encode(f"{settings.git_http_user or 'x-access-token'}:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


# One refresh of a clone at a time. The same repository can be a source of
# several collections, and the watcher and a Sync button can both want it:
# a reset while another sync is hashing the tree would index a mix of two
# commits.
_clone_locks: Dict[str, threading.Lock] = {}
_clone_locks_guard = threading.Lock()


def clone_lock(dest: Path) -> threading.Lock:
    key = str(Path(dest).resolve())
    with _clone_locks_guard:
        lock = _clone_locks.get(key)
        if lock is None:
            lock = _clone_locks[key] = threading.Lock()
        return lock


def ensure_clone(url: str, branch: str = "") -> Path:
    """Clone (first time) or refresh (afterwards) a remote repository; return its path."""
    info = validate_remote(url, branch)
    dest = clone_path(info["url"], info["branch"])
    env = _auth_env(info["url"], info["host"])
    with clone_lock(dest):
        if (dest / ".git").exists():
            ref = info["branch"] or "HEAD"
            _run(["fetch", f"--depth={CLONE_DEPTH}", "--no-tags", "--no-recurse-submodules", "origin", ref],
                 cwd=dest, safe_dir=dest, env_extra=env)
            _run(["reset", "--hard", "FETCH_HEAD"], cwd=dest, safe_dir=dest)
            _run(["clean", "-fdxq"], cwd=dest, safe_dir=dest)
            return dest
        repos_dir().mkdir(parents=True, exist_ok=True)
        args = ["clone", f"--depth={CLONE_DEPTH}", "--no-tags", "--single-branch", "--no-recurse-submodules"]
        if info["branch"]:
            args += ["--branch", info["branch"]]
        args += ["--", info["url"], str(dest)]
        try:
            _run(args, env_extra=env)
        except GitError:
            shutil.rmtree(dest, ignore_errors=True)  # a half-made clone must not be mistaken for one
            raise
        return dest


def remote_head(url: str, branch: str = "") -> Optional[str]:
    """Tip commit of the remote branch, without downloading it (a cheap change check)."""
    info = validate_remote(url, branch)
    ref = info["branch"] or "HEAD"
    out = _run(["ls-remote", "--", info["url"], ref], env_extra=_auth_env(info["url"], info["host"]), timeout=60)
    first = out.split("\n", 1)[0].split("\t", 1)[0].strip()
    return first or None
