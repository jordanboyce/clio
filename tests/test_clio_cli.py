"""skills/clio/scripts/clio_cli.py against a stub server."""

import http.server
import importlib.util
import json
import pathlib
import threading

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "skills" / "clio" / "scripts" / "clio_cli.py")
_spec = importlib.util.spec_from_file_location("clio_cli", _SCRIPT)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


CSV_BODY = (chr(13) + chr(10)).join(["document_id,filename", "d1,a.pdf", "d2,b.pdf", ""]).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []
    export_query = ""
    job_summary = {"ok": 1}
    reindex_polls = 0

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_raw(self, code, raw, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        _Handler.seen.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer good":
            return self._send(401, {"detail": "Not authenticated"})
        if self.path == "/api/collections":
            return self._send(200, {"collections": [
                {"id": "abc", "name": "Policies", "document_count": 2}]})
        if self.path.startswith("/documents/stats"):
            return self._send_raw(200, json.dumps({
                "total_documents": 3, "total_pages": 5, "total_chunks": 7, "total_bytes": 2048,
                "oldest_upload": "2026-01-02T00:00:00", "newest_upload": "2026-03-04T00:00:00",
                "distinct_extensions": 3,
                "by_extension": [{"extension": ".pdf", "documents": 2, "bytes": 2000, "chunks": 5}],
            }).encode(), "application/json")
        if self.path.startswith("/documents/export"):
            _Handler.export_query = self.path
            if "format=csv" in self.path:
                return self._send_raw(200, CSV_BODY, "text/csv")
            lines = "".join(
                json.dumps({"filename": f"f{i}.md", "bytes": i * 10, "uploaded": "2026-02-03T00:00:00"}) + "\n"
                for i in range(5))
            return self._send_raw(200, lines.encode(), "application/x-ndjson")
        if self.path.startswith("/documents/upload/7/status"):
            return self._send(200, {"status": "completed", "processed_files": 1,
                                    "total_files": 1, "result_summary": _Handler.job_summary})
        if self.path.startswith("/api/reindex/status"):
            _Handler.reindex_polls += 1
            done = _Handler.reindex_polls >= 2
            return self._send(200, {"status": "completed" if done else "running",
                                    "processed_documents": 3 if done else 1, "total_documents": 3})
        self._send(404, {"detail": "nope"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        _Handler.seen.append((self.path, self.headers.get("Content-Type")))
        if self.path.startswith("/documents/upload-staged"):
            assert b'filename="a.md"' in body
            return self._send(202, {"staged": [{"filename": "a.md", "path": "/app/data/a.md"}],
                                    "failed": []})
        if self.path == "/documents/index-local-async":
            assert json.loads(body)["file_paths"] == ["/app/data/a.md"]
            return self._send(202, {"job_id": 7})
        if self.path.endswith("/reindex"):
            return self._send(200, {"job_id": 11, "status": "started"})
        if self.path.startswith("/search"):
            return self._send(200, {"results": [{
                "filename": "a.md", "page_number": 1, "similarity_score": 0.9,
                "text_snippet": "rotate every 90 days"}]})
        self._send(404, {"detail": "nope"})


@pytest.fixture
def server():
    _Handler.seen = []
    _Handler.job_summary = {"ok": 1}
    _Handler.reindex_polls = 0
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_upload_then_search(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    assert cli.main(["--url", server, "--json", "upload", str(tmp_path), "-c", "policies"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed" and out["staged"] == 1
    assert cli.main(["--url", server, "search", "rotation", "-c", "Policies"]) == 0
    assert "rotate every 90 days" in capsys.readouterr().out


def test_bad_token_reports_401_with_hint(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "bad")
    assert cli.main(["--url", server, "collections"]) == 1
    assert "set CLIO_TOKEN" in capsys.readouterr().err


def test_unknown_collection_is_a_clear_error(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    assert cli.main(["--url", server, "search", "q", "-c", "missing"]) == 1
    assert "no collection" in capsys.readouterr().err


def test_unreachable_server_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("CLIO_TOKEN", raising=False)
    assert cli.main(["--url", "http://127.0.0.1:1", "status"]) == 1
    assert "cannot reach" in capsys.readouterr().err


def test_token_never_in_argv_and_http_warning(capsys):
    c = cli.Client("http://clio.example.com", "secret")
    assert "plain http" in capsys.readouterr().err
    cli.Client("http://localhost:8473", "secret")
    assert capsys.readouterr().err == ""


def test_collect_files_skips_vcs_and_symlinks(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "keep.md").write_text("x", encoding="utf-8")
    (tmp_path / "skip.bin").write_text("x", encoding="utf-8")
    got = cli.collect_files(tmp_path, {".md"})
    assert [p.name for p in got] == ["keep.md"]


def test_batches_respect_file_count(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "BATCH_FILES", 2)
    files = []
    for i in range(5):
        p = tmp_path / f"{i}.txt"
        p.write_text("x", encoding="utf-8")
        files.append(p)
    assert [len(b) for b in cli.batches(files)] == [2, 2, 1]


def test_stats_renders_counts_and_sizes(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    assert cli.main(["--url", server, "stats", "-c", "Policies"]) == 0
    out = capsys.readouterr().out
    assert "documents  3" in out and "2.0 KB" in out and ".pdf" in out
    assert "2026-01-02 to 2026-03-04" in out and "+2 more types" in out


def test_files_prints_a_bounded_table_and_says_how_to_get_the_rest(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    assert cli.main(["--url", server, "files", "-c", "Policies", "--limit", "2",
                     "--type", "md", "--sort", "largest"]) == 0
    out = capsys.readouterr().out
    assert "f0.md" in out and "f1.md" in out and "f4.md" not in out
    assert "3 more (5 total)" in out and "--csv" in out
    q = _Handler.export_query
    assert "file_type=md" in q and "sort=largest" in q and "collection_id=abc" in q


def test_files_csv_is_streamed_to_disk_atomically(server, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    dest = tmp_path / "all.csv"
    assert cli.main(["--url", server, "files", "-c", "Policies", "--csv", str(dest)]) == 0
    assert dest.read_text(encoding="utf-8").splitlines()[1] == "d1,a.pdf"
    assert not (tmp_path / "all.csv.partial").exists()
    assert "wrote" in capsys.readouterr().out


def test_files_csv_failure_leaves_no_partial_file(server, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "bad")
    dest = tmp_path / "all.csv"
    assert cli.main(["--url", server, "files", "-c", "Policies", "--csv", str(dest)]) == 1
    assert not dest.exists() and not (tmp_path / "all.csv.partial").exists()


def test_upload_with_rejected_files_exits_nonzero_and_says_why(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _Handler.job_summary = {"documents_processed": 0, "failed_files": [
        {"filename": "https://example.com/a", "error": "2048-dimensional ...", "code": "embedding_mismatch"}]}
    rc = cli.main(["--url", server, "--json", "upload", str(tmp_path), "-c", "policies"])
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert rc == 1                                  # "completed" with nothing indexed is not success
    assert out["failed_files"][0]["code"] == "embedding_mismatch"
    assert "reindex -c policies" in out["hint"] and "hint:" in cap.err


def test_job_level_mismatch_code_also_gives_the_hint(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _Handler.job_summary = {"error_code": "embedding_mismatch", "failed_files": []}
    cli.main(["--url", server, "--json", "upload", str(tmp_path), "-c", "policies"])
    assert "reindex" in json.loads(capsys.readouterr().out)["hint"]


def test_clean_upload_has_no_hint_and_exits_zero(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    assert cli.main(["--url", server, "--json", "upload", str(tmp_path), "-c", "policies"]) == 0
    assert "hint" not in json.loads(capsys.readouterr().out)


def test_reindex_waits_for_the_job(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    assert cli.main(["--url", server, "--json", "reindex", "-c", "Policies", "--poll", "0.01"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"collection_id": "abc", "job_id": 11, "status": "completed",
                   "error": None, "documents": 3}


def test_reindex_no_wait_returns_the_job_id(server, monkeypatch, capsys):
    monkeypatch.setenv("CLIO_TOKEN", "good")
    assert cli.main(["--url", server, "--json", "reindex", "-c", "Policies", "--no-wait"]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == 11
    assert _Handler.reindex_polls == 0
