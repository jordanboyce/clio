"""Supplier-quality pilot: demo files, an answer-level run, and the scorecard.

Three steps, each its own subcommand. Standard library only, so it runs on a
customer's machine with nothing installed beyond Python 3.9+.

1. ``export`` writes the fictional quality records (the same corpus the
   retrieval benchmark measures, tests/fixtures/quality_bench) as ordinary
   files to upload into a collection, plus ``REPLACED_BY.txt`` naming the
   "Replaced by" marks to record in the Sources panel:

       python scripts/pilot_eval.py export --out demo-quality

2. ``run`` asks every question in the evaluation set through the app API
   (``/api/v1``, docs/APP_API.md) with a registered app key, applies the
   automatic screening checks, and writes ``results.json``, ``review.csv``
   (one row per question for a quality engineer to mark) and ``summary.md``:

       export CLIO_KEY=clio_app_...
       python scripts/pilot_eval.py run --base-url https://clio.example/api/v1 \\
           --collection <id> --out pilot-run-1

   On a customer's own corpus, write a question file in the same shape as
   docs/demo/quality-evaluation.json and pass ``--questions``.

3. ``score`` reads the reviewed sheet back and prints the pilot scorecard:
   supported answers, correct citations, correct "not in the records"
   answers, and time to a checked answer against today's process:

       python scripts/pilot_eval.py score pilot-run-1/review.csv

The automatic checks screen; they do not judge. Substring matches cannot
tell a correct answer from one that quotes the right number in the wrong
sentence, so the scorecard counts only what the reviewer marked.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "quality_bench"
QUESTIONS = REPO_ROOT / "docs" / "demo" / "quality-evaluation.json"

# Phrases an answer uses when the records do not settle a question. Broad on
# purpose: it only decides whether the reviewer is told "looks like it
# abstained", and the reviewer has the last word.
_ABSTAIN = re.compile(
    r"\b(?:not (?:stated|specified|mentioned|given|included|recorded|listed|provided|named|"
    r"identified|establish\w*|say|state|specify|mention|contain|include|name|appear)"
    r"|no (?:information|record|mention|indication|cost|figure|data|name)"
    r"|do(?:es)? not (?:say|state|specify|mention|contain|include|name|establish|record|identify)"
    r"|(?:isn't|aren't|wasn't) (?:stated|specified|mentioned|recorded|named)"
    r"|cannot (?:find|determine|tell|confirm)|can't (?:find|determine|tell|confirm)"
    r"|unable to (?:find|determine)|not in the (?:sources|documents|records))",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\[Source\s+(\d+)\]", re.IGNORECASE)

REVIEW_COLUMNS = [
    "id", "kind", "question", "expect", "answer", "cited_sources",
    "auto_mentions", "auto_sources", "auto_abstain", "auto_flags", "latency_s",
    # Filled in by the reviewer: Y / N (or blank when not applicable).
    "reviewer_supported", "reviewer_citations_correct", "reviewer_abstained_correctly",
    "reviewer_minutes", "baseline_minutes", "reviewer_notes",
]


# ── export ───────────────────────────────────────────────────────────────────

def _csv_cells(passage: str, columns: Sequence[str]) -> List[str]:
    """One benchmark passage ("... row: a | b | c") back into CSV cells."""
    body = passage.split(": ", 1)[1] if " row: " in passage else passage
    cells = [c.strip() for c in body.split(" | ")]
    out = []
    for i, cell in enumerate(cells):
        name = columns[i] if i < len(columns) else ""
        for prefix in (f"{name}: ", f"{name} "):
            if name and cell.lower().startswith(prefix.lower()):
                cell = cell[len(prefix):]
                break
        out.append(cell)
    return out + [""] * (len(columns) - len(out))


def export_demo(out_dir: Path, fixture_dir: Path = FIXTURE_DIR) -> List[Path]:
    corpus = json.loads((fixture_dir / "corpus.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    by_id = {d["id"]: d for d in corpus["documents"]}
    for doc in corpus["documents"]:
        path = out_dir / doc["filename"]
        if doc.get("format") == "csv":
            columns = doc["columns"]
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(columns)
                for passage in doc["passages"]:
                    writer.writerow(_csv_cells(passage, columns))
        else:
            title = Path(doc["filename"]).stem
            body = "\n\n".join(doc["passages"])
            path.write_text(f"# {title}\n\n_Fictional record for a Clio demonstration._\n\n{body}\n",
                            encoding="utf-8")
        written.append(path)
    marks = [(by_id[d["id"]]["filename"], by_id[d["superseded_by"]]["filename"])
             for d in corpus["documents"] if d.get("superseded_by")]
    lines = [
        "Record these in the collection's Sources panel (the 'Replaced by...' button on the",
        "older document's row), or accept them from the suggestions Clio offers:",
        "",
        *[f"  {old}\n      is replaced by  {new}" for old, new in marks],
        "",
        "Everything in this folder is fictional: invented companies, part numbers and values.",
        "Do not load real customer-controlled or export-controlled data into a demonstration.",
    ]
    note = out_dir / "REPLACED_BY.txt"
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written + [note]


# ── run ──────────────────────────────────────────────────────────────────────

class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class Client:
    def __init__(self, base_url: str, key: str, timeout: float = 300.0):
        self.base = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 params: Optional[dict] = None) -> Any:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read().decode("utf-8"))
                except Exception:  # noqa: BLE001 - non-JSON error body
                    err = {}
                if e.code == 429 and attempt < 3:
                    time.sleep(min(float(e.headers.get("Retry-After") or 5), 60))
                    continue
                raise ApiError(e.code, err.get("code", "http_error"), str(err.get("detail", e.reason))) from None
        raise ApiError(429, "rate_limited", "still rate limited after retries")

    def documents(self, collection_id: str) -> List[dict]:
        docs, offset = [], 0
        while True:
            page = self._request("GET", f"/collections/{collection_id}/documents",
                                 params={"limit": 200, "offset": offset, "sort": "name"})
            docs += page.get("documents") or []
            offset = page.get("next_offset")
            if offset is None:
                return docs

    def ask(self, collection_id: str, question: str, document_ids: Optional[List[str]] = None,
            depth: str = "quick") -> dict:
        body: Dict[str, Any] = {"question": question, "depth": depth, "use_cache": False}
        if document_ids:
            body["document_ids"] = document_ids
        return self._request("POST", f"/collections/{collection_id}/ask", body)


def cited_filenames(answer: str, sources: Iterable[dict]) -> List[str]:
    """Filenames behind the [Source N] markers the answer actually uses."""
    used = {int(n) for n in _CITATION.findall(answer or "")}
    names: List[str] = []
    for s in sources:
        if s.get("n") in used and s.get("filename") and s["filename"] not in names:
            names.append(s["filename"])
    return names


def screen(case: dict, answer: str, cited: Sequence[str]) -> Dict[str, Any]:
    """The automatic checks: hints for the reviewer, never the verdict."""
    text = (answer or "").lower()
    groups = case.get("must_mention") or []
    missing = [g for g in groups if not any(alt.lower() in text for alt in g)]
    expected = case.get("expected_sources") or []
    cited_set = set(cited)
    sources_ok: Optional[bool] = (bool(cited_set & set(expected)) if expected else None)
    selected = case.get("selected_sources") or []
    outside = sorted(cited_set - set(selected)) if selected else []
    abstained = bool(_ABSTAIN.search(answer or ""))
    flags = [v for v in case.get("flag_values") or [] if v.lower() in text]
    if outside:
        flags.append("cites outside selection: " + "; ".join(outside))
    if not case.get("abstain") and not cited:
        flags.append("no [Source N] citation")
    return {
        "mentions_ok": not missing,
        "missing": [" / ".join(g) for g in missing],
        "sources_ok": sources_ok,
        "abstained": abstained,
        "abstain_ok": (abstained if case.get("abstain") else None),
        "flags": flags,
    }


def run_eval(client: Client, collection_id: str, cases: Sequence[dict], depth: str = "quick",
             log=lambda s: None) -> List[dict]:
    by_name = {d.get("filename"): d.get("document_id") for d in client.documents(collection_id)}
    results = []
    for case in cases:
        doc_ids = None
        if case.get("selected_sources"):
            missing = [n for n in case["selected_sources"] if n not in by_name]
            if missing:
                results.append({"case": case, "error": f"selected sources not in collection: {missing}"})
                log(f"{case['id']}: skipped, {missing} not in the collection")
                continue
            doc_ids = [by_name[n] for n in case["selected_sources"]]
        started = time.perf_counter()
        try:
            resp = client.ask(collection_id, case["question"], doc_ids, depth=depth)
        except ApiError as e:
            results.append({"case": case, "error": str(e)})
            log(f"{case['id']}: {e}")
            continue
        latency = time.perf_counter() - started
        cited = cited_filenames(resp.get("answer", ""), resp.get("sources") or [])
        results.append({
            "case": case, "answer": resp.get("answer", ""), "sources": resp.get("sources") or [],
            "cited": cited, "model": resp.get("model"), "corpus_version": resp.get("corpus_version"),
            "usage": resp.get("usage"), "latency_s": round(latency, 2),
            "checks": screen(case, resp.get("answer", ""), cited),
        })
        log(f"{case['id']}: {latency:.1f} s")
    return results


def _yn(v: Optional[bool]) -> str:
    return "" if v is None else ("yes" if v else "NO")


def write_outputs(results: Sequence[dict], out_dir: Path, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2),
                                          encoding="utf-8")
    with (out_dir / "review.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for r in results:
            case, checks = r["case"], r.get("checks") or {}
            writer.writerow({
                "id": case["id"], "kind": case.get("kind", ""), "question": case["question"],
                "expect": case.get("expect", ""),
                "answer": r.get("answer") or f"ERROR: {r.get('error')}",
                "cited_sources": "; ".join(r.get("cited") or []),
                "auto_mentions": "yes" if checks.get("mentions_ok") else
                                 ("missing " + "; ".join(checks.get("missing") or []) if checks else ""),
                "auto_sources": _yn(checks.get("sources_ok")),
                "auto_abstain": _yn(checks.get("abstain_ok")),
                "auto_flags": "; ".join(checks.get("flags") or []),
                "latency_s": r.get("latency_s", ""),
            })
    (out_dir / "summary.md").write_text(render_summary(results, meta), encoding="utf-8")


def render_summary(results: Sequence[dict], meta: dict) -> str:
    done = [r for r in results if "checks" in r]
    errors = [r for r in results if "error" in r]
    lat = [r["latency_s"] for r in done]

    def share(pred, pool) -> str:
        pool = list(pool)
        return f"{sum(1 for r in pool if pred(r))}/{len(pool)}" if pool else "-"

    answerable = [r for r in done if not r["case"].get("abstain")]
    unanswerable = [r for r in done if r["case"].get("abstain")]
    lines = [
        f"# Pilot evaluation run: {meta.get('questions_name')}",
        "",
        f"Deployment {meta.get('base_url')}, collection {meta.get('collection')}, depth {meta.get('depth')}, "
        f"model {', '.join(sorted({str(r.get('model')) for r in done})) or '-'}, "
        f"corpus_version {', '.join(sorted({str(r.get('corpus_version')) for r in done})) or '-'}, "
        f"run {meta.get('started')}.",
        "",
        "Automatic screening only. These counts say where to look first; the pilot is scored "
        "from the reviewer's marks in review.csv (`pilot_eval.py score`).",
        "",
        "| check | result |",
        "|---|---|",
        f"| questions answered | {len(done)}/{len(results)} |",
        f"| expected facts mentioned (answerable) | {share(lambda r: r['checks']['mentions_ok'], answerable)} |",
        f"| an expected source cited | {share(lambda r: r['checks']['sources_ok'], [r for r in done if r['checks']['sources_ok'] is not None])} |",
        f"| said the records do not settle it (unanswerable) | {share(lambda r: r['checks']['abstain_ok'], unanswerable)} |",
        f"| answers with a flag for the reviewer | {share(lambda r: r['checks']['flags'], done)} |",
        f"| latency p50 / p95 | {_pctl(lat, 0.5)} s / {_pctl(lat, 0.95)} s |",
    ]
    flagged = [r for r in done if r["checks"]["flags"] or not r["checks"]["mentions_ok"]
               or r["checks"]["sources_ok"] is False or r["checks"]["abstain_ok"] is False]
    if flagged:
        lines += ["", "## Look at these first", ""]
        for r in flagged:
            c = r["checks"]
            why = c["flags"] + [f"missing {m}" for m in c["missing"]]
            if c["sources_ok"] is False:
                why.append("no expected source cited")
            if c["abstain_ok"] is False:
                why.append("did not say the records are silent")
            lines.append(f"- **{r['case']['id']}** ({r['case'].get('kind')}): {'; '.join(why)}")
    if errors:
        lines += ["", "## Errors", ""] + [f"- {r['case']['id']}: {r['error']}" for r in errors]
    return "\n".join(lines) + "\n"


def _pctl(values: Sequence[float], pct: float) -> str:
    if not values:
        return "-"
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return f"{ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo):.1f}"


# ── score ────────────────────────────────────────────────────────────────────

def _mark(value: str) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in ("y", "yes", "true", "1", "pass"):
        return True
    if v in ("n", "no", "false", "0", "fail"):
        return False
    return None


def _minutes(value: str) -> Optional[float]:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def scorecard(rows: Sequence[dict]) -> Dict[str, Any]:
    def rate(col: str) -> Dict[str, Any]:
        marks = [m for m in (_mark(r.get(col, "")) for r in rows) if m is not None]
        return {"yes": sum(marks), "marked": len(marks),
                "rate": (sum(marks) / len(marks)) if marks else None}

    tool = [m for m in (_minutes(r.get("reviewer_minutes", "")) for r in rows) if m is not None]
    base = [m for m in (_minutes(r.get("baseline_minutes", "")) for r in rows) if m is not None]
    unmarked = [r["id"] for r in rows if _mark(r.get("reviewer_supported", "")) is None]
    return {
        "questions": len(rows),
        "supported": rate("reviewer_supported"),
        "citations_correct": rate("reviewer_citations_correct"),
        "abstained_correctly": rate("reviewer_abstained_correctly"),
        "median_minutes_with_clio": statistics.median(tool) if tool else None,
        "median_minutes_today": statistics.median(base) if base else None,
        "unmarked": unmarked,
    }


def render_scorecard(card: Dict[str, Any]) -> str:
    def fmt(r):
        return "-" if r["rate"] is None else f"{r['yes']}/{r['marked']} ({100 * r['rate']:.0f}%)"

    t, b = card["median_minutes_with_clio"], card["median_minutes_today"]
    speed = "-" if not (t and b) else f"{b / t:.1f}x faster" if t < b else f"{t / b:.1f}x slower"
    lines = [
        "| measure | result |", "|---|---|",
        f"| answers whose every material claim is supported | {fmt(card['supported'])} |",
        f"| citations that open the right passage | {fmt(card['citations_correct'])} |",
        f"| 'not in the records' handled correctly | {fmt(card['abstained_correctly'])} |",
        f"| median minutes to a checked answer, with Clio | {'-' if t is None else f'{t:g}'} |",
        f"| median minutes today | {'-' if b is None else f'{b:g}'} |",
        f"| time to a checked answer | {speed} |",
    ]
    if card["unmarked"]:
        lines += ["", f"Not yet reviewed: {', '.join(card['unmarked'])}"]
    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="write the fictional demo records as files to upload")
    p_export.add_argument("--out", type=Path, required=True)
    p_export.add_argument("--fixture", type=Path, default=FIXTURE_DIR)

    p_run = sub.add_parser("run", help="ask every question through /api/v1 and write a review sheet")
    p_run.add_argument("--base-url", required=True, help="the deployment's /api/v1 URL")
    p_run.add_argument("--collection", required=True, help="collection id the app key was granted")
    p_run.add_argument("--questions", type=Path, default=QUESTIONS)
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--depth", choices=("quick", "deep"), default="quick")
    p_run.add_argument("--only", help="comma-separated case ids to run")

    p_score = sub.add_parser("score", help="score a reviewed review.csv")
    p_score.add_argument("review", type=Path)
    p_score.add_argument("--json", action="store_true", help="print the numbers as JSON")

    args = parser.parse_args(argv)

    if args.cmd == "export":
        for path in export_demo(args.out, args.fixture):
            print(path)
        return 0

    if args.cmd == "run":
        key = os.environ.get("CLIO_KEY", "").strip()
        if not key:
            print("Set CLIO_KEY to a registered app key (Admin -> Registered apps).", file=sys.stderr)
            return 2
        spec = json.loads(args.questions.read_text(encoding="utf-8"))
        cases = spec["cases"]
        if args.only:
            wanted = {s.strip() for s in args.only.split(",")}
            cases = [c for c in cases if c["id"] in wanted]
        meta = {"base_url": args.base_url, "collection": args.collection, "depth": args.depth,
                "questions_name": spec.get("corpus") or args.questions.name,
                "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        client = Client(args.base_url, key)
        results = run_eval(client, args.collection, cases, depth=args.depth,
                           log=lambda s: print(s, file=sys.stderr, flush=True))
        write_outputs(results, args.out, meta)
        print((args.out / "summary.md").read_text(encoding="utf-8"))
        return 0 if all("checks" in r for r in results) else 1

    if args.cmd == "score":
        with args.review.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        card = scorecard(rows)
        print(json.dumps(card, indent=2) if args.json else render_scorecard(card))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
