#!/usr/bin/env python
"""Command-line client for a Clio server: index a folder, then search it.

Standard library only, so it runs on any host or inside an agent sandbox.
It talks to the same HTTP API as the web app; it never touches the data
directory.

    export CLIO_URL=https://clio.example.internal      # default http://localhost:8473
    export CLIO_TOKEN=...                              # AUTH_PASSWORD or a personal MCP token

    clio_cli.py status
    clio_cli.py collections
    clio_cli.py create "Policies" --description "HR and security policies"
    clio_cli.py upload ./handbook --collection policies      # stages, indexes, waits
    clio_cli.py search "password rotation interval" --collection policies
    clio_cli.py stats --collection policies                  # exact counts, sizes, types
    clio_cli.py files --collection policies --type pdf       # list files (table)
    clio_cli.py files -c policies --csv all-files.csv        # every file, however many
    clio_cli.py coverage -c policies                         # how findable is this collection?
    clio_cli.py coverage -c policies --run                   # owner: refresh the report
    clio_cli.py jobs

The token is read from the environment (or --token-file), never from argv, so
it does not land in shell history or the process list. Every command accepts
--json for machine-readable output. Exit status: 0 ok, 1 request failed or
the indexing job failed, 2 usage / configuration error.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_URL = "http://localhost:8473"
BATCH_BYTES = 40 * 1024 * 1024      # stay under typical reverse-proxy body limits
BATCH_FILES = 50
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store"}


class ClioError(Exception):
    """A request the server refused or could not answer."""


class Client:
    def __init__(self, base_url: str, token: str = "", timeout: float = 120.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        if self.token and self.base.startswith("http://") and not _is_local(self.base):
            print(
                "warning: sending a credential over plain http to a non-local host; "
                "use https.", file=sys.stderr,
            )

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, *, body: Optional[bytes] = None,
                 headers: Optional[dict] = None, query: Optional[dict] = None) -> Any:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        req = urllib.request.Request(url, data=body, method=method)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            hint = {401: " (set CLIO_TOKEN)", 403: " (token lacks access to this collection)"}.get(e.code, "")
            raise ClioError(f"{method} {path} -> HTTP {e.code}: {detail}{hint}") from None
        except urllib.error.URLError as e:
            raise ClioError(f"cannot reach {self.base}: {e.reason}") from None
        return json.loads(raw) if raw else None

    def get(self, path: str, **query: Any) -> Any:
        return self._request("GET", path, query=query)

    def download(self, path: str, dest: Path, **query: Any) -> int:
        """Stream a response body to `dest` without holding it in memory."""
        url = self.base + path + "?" + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v not in (None, "")})
        req = urllib.request.Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        partial = dest.with_name(dest.name + ".partial")
        written = 0
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(partial, "wb") as out:
                for block in iter(lambda: resp.read(1 << 16), b""):
                    out.write(block)
                    written += len(block)
            partial.replace(dest)
        except urllib.error.HTTPError as e:
            partial.unlink(missing_ok=True)
            detail = e.read().decode("utf-8", "replace")[:300]
            raise ClioError(f"GET {path} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            partial.unlink(missing_ok=True)
            raise ClioError(f"cannot reach {self.base}: {e.reason}") from None
        return written

    def post_json(self, path: str, payload: dict, **query: Any) -> Any:
        return self._request("POST", path, body=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"}, query=query)

    def post_files(self, path: str, files: list[Path], **query: Any) -> Any:
        boundary = uuid.uuid4().hex
        chunks: list[bytes] = []
        for f in files:
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            chunks.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
                 f"filename=\"{_ascii_name(f)}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
            )
            chunks.append(f.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        return self._request(
            "POST", path, body=b"".join(chunks), query=query,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


def _is_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in {"localhost", "127.0.0.1", "::1"}


def _ascii_name(path: Path) -> str:
    # Header-safe; the server flattens folder paths itself.
    return path.name.replace('"', "").encode("ascii", "replace").decode()


# -- helpers ------------------------------------------------------------------

def collect_files(root: Path, extensions: Optional[set[str]]) -> list[Path]:
    """Regular files under root, skipping VCS/build directories and symlinks."""
    if root.is_file():
        return [root]
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() or name in SKIP_DIRS:
                continue
            if extensions and p.suffix.lower() not in extensions:
                continue
            out.append(p)
    return out


def batches(files: Iterable[Path]) -> Iterable[list[Path]]:
    batch: list[Path] = []
    size = 0
    for f in files:
        s = f.stat().st_size
        if batch and (size + s > BATCH_BYTES or len(batch) >= BATCH_FILES):
            yield batch
            batch, size = [], 0
        batch.append(f)
        size += s
    if batch:
        yield batch


def resolve_collection(client: Client, ref: str) -> str:
    """Accept a collection id or (case-insensitive) name."""
    data = client.get("/api/collections")
    cols = data.get("collections", data) if isinstance(data, dict) else data
    for c in cols:
        if ref == c.get("id"):
            return c["id"]
    matches = [c for c in cols if str(c.get("name", "")).lower() == ref.lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        raise ClioError(f"collection name {ref!r} is ambiguous; use its id")
    raise ClioError(f"no collection {ref!r}; run `collections` to list them")


def wait_for_job(client: Client, job_id: int, poll: float, quiet: bool) -> dict:
    last = ""
    while True:
        job = client.get(f"/documents/upload/{job_id}/status")
        line = f"{job['status']} {job.get('processed_files', 0)}/{job.get('total_files', 0)}"
        if line != last and not quiet:
            print(f"  job {job_id}: {line}", file=sys.stderr)
            last = line
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(poll)


# -- commands -----------------------------------------------------------------

def cmd_status(c: Client, a) -> Any:
    return c.get("/health")


def cmd_collections(c: Client, a) -> Any:
    data = c.get("/api/collections")
    return data.get("collections", data) if isinstance(data, dict) else data


def cmd_create(c: Client, a) -> Any:
    return c.post_json("/api/collections", {
        "name": a.name, "description": a.description or "", "visibility": "team",
    })


def cmd_upload(c: Client, a) -> Any:
    root = Path(a.path).expanduser()
    if not root.exists():
        raise ClioError(f"{root} does not exist")
    exts = {("." + e.lstrip(".")).lower() for e in a.ext} if a.ext else None
    files = collect_files(root, exts)
    if not files:
        raise ClioError("no files to upload")
    cid = resolve_collection(c, a.collection)
    paths: list[str] = []
    failed: list[dict] = []
    for batch in batches(files):
        res = c.post_files("/documents/upload-staged", batch, collection_id=cid)
        paths += [s["path"] for s in res.get("staged", [])]
        failed += res.get("failed", [])
        if not a.json:
            print(f"staged {len(paths)}/{len(files)}", file=sys.stderr)
    if not paths:
        return {"collection_id": cid, "staged": 0, "failed": failed}
    job = c.post_json("/documents/index-local-async",
                      {"file_paths": paths, "collection_id": cid})
    result = {"collection_id": cid, "staged": len(paths), "failed": failed,
              "job_id": job["job_id"]}
    if not a.no_wait:
        done = wait_for_job(c, job["job_id"], a.poll, a.json)
        result["status"] = done["status"]
        summary = done.get("result_summary") or {}
        result["summary"] = summary
        if done["status"] != "completed":
            result["error"] = done.get("error")
        # A job can finish "completed" with files missing. Surface which and why.
        rejected = summary.get("failed_files") or []
        if rejected:
            result["failed_files"] = rejected
        codes = {summary.get("error_code")} | {f.get("code") for f in rejected}
        if "embedding_mismatch" in codes:
            result["hint"] = (f"The embedding model no longer matches this collection's index. "
                              f"Rebuild it with: clio_cli.py reindex -c {a.collection}")
    return result


def cmd_reindex(c: Client, a) -> Any:
    """Rebuild a collection's index with the embedding model now in use."""
    cid = resolve_collection(c, a.collection)
    if a.ext:
        # Only the files of these types, e.g. after changing a custom type's profile.
        started = c.post_json(f"/api/collections/{cid}/reindex-extensions", {"extensions": a.ext})
    else:
        started = c.post_json(f"/api/collections/{cid}/reindex", {})
    result = {"collection_id": cid, "job_id": started.get("job_id")}
    if a.no_wait:
        return result
    while True:
        job = c.get("/api/reindex/status", job_id=started["job_id"])
        line = f"{job['status']} {job.get('processed_documents', 0)}/{job.get('total_documents', 0)}"
        if not a.json:
            print(f"  reindex {started['job_id']}: {line}", file=sys.stderr)
        if job["status"] in ("completed", "failed"):
            result.update(status=job["status"], error=job.get("error"),
                          documents=job.get("processed_documents"))
            return result
        time.sleep(a.poll)


def cmd_stats(c: Client, a) -> Any:
    return c.get("/documents/stats", collection_id=resolve_collection(c, a.collection))


def cmd_files(c: Client, a) -> Any:
    cid = resolve_collection(c, a.collection)
    query = {"collection_id": cid, "q": a.name, "file_type": a.type, "sort": a.sort}
    if a.csv or a.jsonl:
        dest = Path(a.csv or a.jsonl).expanduser()
        size = c.download("/documents/export", dest,
                          format="csv" if a.csv else "jsonl", **query)
        return {"collection_id": cid, "written_to": str(dest), "bytes": size}
    # Table to the terminal: stream JSON Lines and stop at --limit.
    tmp = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or ".") / f".clio-files-{os.getpid()}.jsonl"
    try:
        c.download("/documents/export", tmp, format="jsonl", **query)
        rows = []
        total = 0
        with open(tmp, encoding="utf-8") as f:
            for line in f:
                total += 1
                if len(rows) < a.limit:
                    rows.append(json.loads(line))
    finally:
        tmp.unlink(missing_ok=True)
    return {"collection_id": cid, "total": total, "shown": len(rows), "files": rows}


def cmd_jobs(c: Client, a) -> Any:
    return c.get("/documents/jobs")


def cmd_coverage(c: Client, a) -> Any:
    """Show a collection's retrieval coverage report, or run a fresh one (--run).

    Running is owner-only on the server and takes seconds to a couple of
    minutes; the command waits for it unless --no-wait.
    """
    cid = resolve_collection(c, a.collection)
    path = f"/api/collections/{cid}/coverage"
    if a.run:
        payload = {"probes": a.probes} if a.probes else {}
        try:
            c.post_json(path, payload)
        except ClioError as e:
            if "HTTP 409" not in str(e):
                raise
            if not a.json:
                print("a run is already in progress; waiting for it", file=sys.stderr)
        if a.no_wait:
            return {"collection_id": cid, "run": c.get(path).get("run")}
    while True:
        data = c.get(path)
        run = data.get("run") or {}
        if not a.run or run.get("state") != "running":
            break
        if not a.json:
            print(f"  coverage run: {run.get('done', 0)}/{run.get('total') or '?'} probes", file=sys.stderr)
        time.sleep(a.poll)
    data["collection_id"] = cid
    return data


def cmd_job(c: Client, a) -> Any:
    return c.get(f"/documents/upload/{a.job_id}/status")


def cmd_search(c: Client, a) -> Any:
    cid = resolve_collection(c, a.collection)
    body: dict = {"query": a.query, "top_k": a.top_k}
    if getattr(a, "include_superseded", False):
        body["filters"] = {"include_superseded": True}
    return c.post_json("/search", body, collection_id=cid)


def cmd_supersede(c: Client, a) -> Any:
    """Suggest, record or clear 'replaced by' relationships between documents."""
    cid = resolve_collection(c, a.collection)
    if a.action == "suggest":
        return c.get("/documents/supersede-suggestions", collection_id=cid)
    if a.action == "mark":
        if not a.replaced_by:
            raise ClioError("mark needs both the old document id and the id that replaces it")
        body = {"replaced_by": a.replaced_by}
        if a.note:
            body["note"] = a.note
        return c.post_json(f"/documents/{a.document_id}/supersede", body, collection_id=cid)
    return c._request("DELETE", f"/documents/{a.document_id}/supersede",
                      query={"collection_id": cid})


# -- custom file types commands -------------------------------------------------

def cmd_types(c: Client, a) -> Any:
    """List all registered custom file types."""
    data = c.get("/api/custom-file-types")
    return data.get("types", []) if isinstance(data, dict) else data


def cmd_type_suggest(c: Client, a) -> Any:
    """Propose a profile from a sample file."""
    sample_path = Path(a.sample).expanduser()
    if not sample_path.exists():
        raise ClioError(f"{sample_path} does not exist")
    if sample_path.stat().st_size > 2 * 1024 * 1024:
        raise ClioError("sample is larger than 2 MB; use a smaller excerpt")

    with open(sample_path, "rb") as f:
        raw = f.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ClioError("sample is larger than 2 MB; use a smaller excerpt")

    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="extension"\r\n\r\n{a.extension}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="use_ai"\r\n\r\n{str(a.use_ai).lower()}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{_ascii_name(sample_path)}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
        raw,
        b'\r\n',
        f'--{boundary}--\r\n'.encode(),
    ]
    return c._request(
        "POST", "/api/custom-file-types/suggest",
        body=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )


def cmd_type_save(c: Client, a) -> Any:
    """Save a profile for an extension."""
    profile = json.loads(a.profile)
    ext = a.extension
    if not ext.startswith("."):
        ext = "." + ext
    return c._request("PUT", f"/api/custom-file-types/{ext}",
                      body=json.dumps(profile).encode(),
                      headers={"Content-Type": "application/json"})


def _human(n: Optional[int]) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{round(100 * v)}%"


def render_coverage(result: dict) -> str:
    report, run = result.get("report"), result.get("run") or {}
    if run.get("state") == "running" and not report:
        return f"coverage run in progress ({run.get('done', 0)}/{run.get('total') or '?'} probes)"
    if run.get("state") == "failed":
        return f"coverage run failed: {run.get('error')}"
    if not report:
        return "no coverage report yet; an owner can create one with: clio_cli.py coverage -c NAME --run"
    o = report["overall"]
    stale = report.get("stale_reasons") or []
    lines = [
        f"retrieval coverage  document hit@5 {_pct(o['document_hit_at_5'])}  "
        f"chunk hit@10 {_pct(o['chunk_hit_at_10'])}  MRR {o['mrr_chunk'] if o['mrr_chunk'] is not None else '-'}  "
        f"({o['probes']} probes, {report.get('age') or '?'} old)",
    ]
    if stale:
        lines.append("STALE: " + ", ".join(s.replace("_", " ") for s in stale) + " since this run; re-run it")
    pm = report["probe_method"]
    used = ", ".join(f"{m} {v['probes']}" for m, v in pm["by_method"].items()) or "none"
    lines.append(f"probes: {used}; embedding {report.get('embedding_model')}; corpus {report['corpus_version']}")
    lines.append(report["measures"])
    for note in report.get("notes", [])[1:-1]:
        lines.append(f"note: {note}")
    weak = report.get("weakest_documents") or []
    if weak:
        lines.append("weakest documents")
        for d in weak[:5]:
            lines.append(f"  {d['filename']}  {_pct(d['document_hit_at_5'])} of {d['probes']} probes found")
            for f in d["failing_probes"][:2]:
                lines.append(f"      missed: {f['query']!r}")
    never = report["chunks_never_retrieved"]["count"]
    if never:
        lines.append(f"{never} probed chunks never came back in the top 10")
    if report["unprobeable_chunks"]["count"]:
        lines.append(f"{report['unprobeable_chunks']['count']} chunks have no searchable words (table noise?)")
    owner = report.get("owner_questions")
    if owner:
        lines.append(f"owner questions  hit@5 {_pct(owner['document_hit_at_5'])} "
                     f"({owner['measured']} of {owner['total']} measured)")
        for item in owner["items"]:
            if item["status"] != "hit":
                names = ", ".join(d["filename"] for d in item["expected_documents"])
                lines.append(f"  {item['status'].replace('_', ' ')}: {item['question']}  (expected {names})")
    return "\n".join(lines)


def render(command: str, result: Any) -> str:
    if command == "coverage":
        return render_coverage(result)
    if command == "search":
        rows = [f"{r['filename']} p.{r['page_number']}  score {r['similarity_score']:.2f}\n"
                f"    {r['text_snippet'].strip()[:300]}" for r in result.get("results", [])]
        return "\n".join(rows) or "no results"
    if command == "stats":
        lines = [f"documents  {result['total_documents']}",
                 f"pages      {result['total_pages']}",
                 f"chunks     {result['total_chunks']}",
                 f"size       {_human(result['total_bytes'])}"]
        if result.get("oldest_upload"):
            lines.append(f"uploaded   {result['oldest_upload'][:10]} to {result['newest_upload'][:10]}")
        if result["by_extension"]:
            lines.append("by type")
            lines += [f"  {r['extension']:<10}{r['documents']:>8}  {_human(r['bytes']):>10}"
                      for r in result["by_extension"]]
            more = result["distinct_extensions"] - len(result["by_extension"])
            if more > 0:
                lines.append(f"  (+{more} more types)")
        return "\n".join(lines)
    if command == "files":
        if "written_to" in result:
            return f"wrote {_human(result['bytes'])} to {result['written_to']}"
        rows = [f"{f['filename']}\t{_human(f['bytes']) if f['bytes'] is not None else '-'}\t"
                f"{(f['uploaded'] or '')[:10]}" for f in result["files"]]
        if result["total"] > result["shown"]:
            rows.append(f"... {result['total'] - result['shown']} more "
                        f"({result['total']} total); use --csv FILE for the full list")
        return "\n".join(rows) or "no files"
    if command == "collections":
        return "\n".join(f"{x.get('id')}\t{x.get('name')}\t{x.get('document_count', '')} docs"
                         for x in result) or "no collections"
    if command == "types":
        if not result:
            return "no custom file types registered"
        return "\n".join(f"{x.get('extension')}\t{x.get('label')}\t{x.get('kind')}\t{x.get('source')}"
                         for x in result)
    return json.dumps(result, indent=2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Command-line client for a Clio server.")
    p.add_argument("--url", default=os.environ.get("CLIO_URL", DEFAULT_URL),
                   help="server address (env CLIO_URL)")
    p.add_argument("--token-file", help="read the token from this file instead of CLIO_TOKEN")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="server health and embedding state").set_defaults(fn=cmd_status)
    sub.add_parser("collections", help="list collections").set_defaults(fn=cmd_collections)
    s = sub.add_parser("create", help="create a team collection")
    s.add_argument("name"); s.add_argument("--description")
    s.set_defaults(fn=cmd_create)
    s = sub.add_parser("upload", help="upload a file or folder and index it")
    s.add_argument("path"); s.add_argument("--collection", "-c", default="default")
    s.add_argument("--ext", action="append", help="only these extensions (repeatable)")
    s.add_argument("--no-wait", action="store_true", help="return once the job is queued")
    s.add_argument("--poll", type=float, default=2.0)
    s.set_defaults(fn=cmd_upload)
    s = sub.add_parser("stats", help="exact document counts, sizes and file types")
    s.add_argument("--collection", "-c", default="default")
    s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("files", help="list the files in a collection, or export them all")
    s.add_argument("--collection", "-c", default="default")
    s.add_argument("--type", help="extension (pdf) or family (docs, code, data, media, other)")
    s.add_argument("--name", help="filename contains")
    s.add_argument("--sort", default="name",
                   choices=["newest", "oldest", "name", "name_desc", "largest", "smallest"])
    s.add_argument("--limit", type=int, default=50, help="rows to print (default 50)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--csv", metavar="FILE", help="write every matching file to FILE as CSV")
    g.add_argument("--jsonl", metavar="FILE", help="write every matching file as JSON Lines")
    s.set_defaults(fn=cmd_files)
    s = sub.add_parser("reindex", help="rebuild a collection's index with the current embedding model")
    s.add_argument("--collection", "-c", default="default")
    s.add_argument("--ext", action="append", metavar="EXT",
                   help="re-index only files with this extension (repeatable), e.g. --ext .acme; "
                        "everything else is left untouched")
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("--poll", type=float, default=2.0)
    s.set_defaults(fn=cmd_reindex)
    s = sub.add_parser("coverage", help="how findable a collection's content is (owner: --run to refresh)")
    s.add_argument("--collection", "-c", default="default")
    s.add_argument("--run", action="store_true", help="run a fresh report (owner only) and wait for it")
    s.add_argument("--probes", type=int, help="chunks to sample, up to 300 (default 100)")
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("--poll", type=float, default=2.0)
    s.set_defaults(fn=cmd_coverage)
    sub.add_parser("jobs", help="recent indexing jobs").set_defaults(fn=cmd_jobs)
    s = sub.add_parser("job", help="one indexing job"); s.add_argument("job_id", type=int)
    s.set_defaults(fn=cmd_job)
    s = sub.add_parser("search", help="search a collection")
    s.add_argument("query"); s.add_argument("--collection", "-c", default="default")
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--include-superseded", action="store_true",
                   help="do not demote documents marked as replaced (for 'what did v2 say')")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("supersede", help="suggest, mark or clear 'replaced by' between documents")
    s.add_argument("action", choices=["suggest", "mark", "clear"])
    s.add_argument("document_id", nargs="?", help="the OLD document (mark/clear)")
    s.add_argument("replaced_by", nargs="?", help="document id of the current version (mark)")
    s.add_argument("--collection", "-c", default="default")
    s.add_argument("--note", help="why it was replaced (mark)")
    s.set_defaults(fn=cmd_supersede)
    s = sub.add_parser("types", help="list registered custom file types")
    s.set_defaults(fn=cmd_types)
    s = sub.add_parser("type-suggest", help="propose a profile from a sample file")
    s.add_argument("extension")
    s.add_argument("sample", help="path to sample file")
    s.add_argument("--use-ai", action="store_true", default=False, help="ask the deployment's chat model to refine the proposal")
    s.set_defaults(fn=cmd_type_suggest)
    s = sub.add_parser("type-save", help="save a profile for an extension")
    s.add_argument("extension")
    s.add_argument("profile", help="JSON profile, e.g. '{\"record_path\":\"pumps\",\"title_field\":\"name\"}'")
    s.set_defaults(fn=cmd_type_save)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("CLIO_TOKEN", "")
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"error: cannot read token file: {e}", file=sys.stderr)
            return 2
    client = Client(args.url, token)
    try:
        result = args.fn(client, args)
    except ClioError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else render(args.command, result))
    if isinstance(result, dict) and result.get("hint"):
        print(f"hint: {result['hint']}", file=sys.stderr)
    if args.command == "upload" and isinstance(result, dict) and (
            result.get("status") not in (None, "completed") or result.get("failed_files")):
        return 1
    if args.command == "reindex" and isinstance(result, dict) and result.get("status") == "failed":
        return 1
    if args.command == "coverage" and isinstance(result, dict) and (result.get("run") or {}).get("state") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
