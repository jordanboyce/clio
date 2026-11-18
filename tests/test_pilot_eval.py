"""scripts/pilot_eval.py: the supplier-quality pilot's demo export, screening
checks, and scorecard. The API is faked, but every request body is checked
against the real /api/v1 request model, so the script cannot drift from it."""

import csv
import importlib.util
import json
import pathlib
import sys

import pytest

import api.v1

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pilot_eval.py"
_spec = importlib.util.spec_from_file_location("pilot_eval", _SCRIPT)
pilot = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pilot
_spec.loader.exec_module(pilot)

CASES = json.loads(pilot.QUESTIONS.read_text(encoding="utf-8"))["cases"]
CORPUS = json.loads((pilot.FIXTURE_DIR / "corpus.json").read_text(encoding="utf-8"))


def test_question_set_points_at_documents_that_exist():
    filenames = {d["filename"] for d in CORPUS["documents"]}
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        for name in case.get("expected_sources", []) + case.get("selected_sources", []):
            assert name in filenames, (case["id"], name)
        assert case.get("abstain") or case["must_mention"], case["id"]
    kinds = {c["kind"] for c in CASES}
    assert {"revision", "effectivity", "conflict", "scope", "unanswerable", "table"} <= kinds


def test_export_writes_every_record_and_the_replaced_by_marks(tmp_path):
    written = pilot.export_demo(tmp_path)
    assert len(written) == len(CORPUS["documents"]) + 1
    with (tmp_path / "NCR log 2026.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[2] == {
        "NCR": "NCR-2026-0141", "P/N": "7731-204", "lot": "2611",
        "defect": "torque stripe missing on 3 of 40 assemblies", "opened": "2026-09-08",
        "disposition": "pending MRB", "status": "open",
    }
    with (tmp_path / "Document control register.csv").open(newline="", encoding="utf-8") as fh:
        register = list(csv.DictReader(fh))
    assert register[1]["rev"] == "D" and register[1]["state"] == "Released"
    rev_g = (tmp_path / "WI-2140 Bracket assembly 7731-204 rev G.md").read_text(encoding="utf-8")
    assert rev_g.startswith("# WI-2140 Bracket assembly 7731-204 rev G") and "32 in-lb" in rev_g
    note = (tmp_path / "REPLACED_BY.txt").read_text(encoding="utf-8")
    assert "WI-2140 Bracket assembly 7731-204 rev F.md\n      is replaced by  WI-2140 Bracket assembly 7731-204 rev G.md" in note


def test_citations_count_only_markers_the_answer_uses():
    sources = [{"n": 1, "filename": "a.md"}, {"n": 2, "filename": "b.md"}, {"n": 3, "filename": "a.md"}]
    assert pilot.cited_filenames("Yes [Source 3] and [source 1].", sources) == ["a.md"]
    assert pilot.cited_filenames("No markers.", sources) == []


@pytest.mark.parametrize("answer, abstains", [
    ("The deviation does not name the approver.", True),
    ("No cost is recorded in the sources.", True),
    ("The records don't say; the approver is not stated [Source 1].", True),
    ("R. Okafor approved it [Source 2].", False),
])
def test_abstention_screen(answer, abstains):
    assert bool(pilot._ABSTAIN.search(answer)) is abstains


def test_screen_flags_stale_values_and_out_of_scope_citations():
    case = next(c for c in CASES if c["id"] == "q01")
    good = pilot.screen(case, "32 in-lb ±2 per rev G [Source 1].", ["WI-2140 Bracket assembly 7731-204 rev G.md"])
    assert good["mentions_ok"] and good["sources_ok"] and good["flags"] == []
    stale = pilot.screen(case, "Torque to 28 in-lb [Source 1].", ["WI-2140 Bracket assembly 7731-204 rev F.md"])
    assert not stale["mentions_ok"] and stale["sources_ok"] is False and "28 in-lb" in stale["flags"]
    scope = next(c for c in CASES if c["id"] == "q12")
    leaked = pilot.screen(scope, "28 in-lb, replaced by rev G [Source 1][Source 2].",
                          ["WI-2140 Bracket assembly 7731-204 rev F.md", "ECN-0419 Torque change 7731-204.md"])
    assert leaked["mentions_ok"]
    assert any(f.startswith("cites outside selection") for f in leaked["flags"])
    silent = next(c for c in CASES if c["id"] == "q13")
    guessed = pilot.screen(silent, "It was approved by R. Okafor.", [])
    assert guessed["abstain_ok"] is False and "Okafor" in guessed["flags"]


class _FakeApi:
    """Stands in for a deployment; validates bodies with the real request model."""

    def __init__(self):
        self.asked = []
        self.docs = [{"document_id": f"id-{i}", "filename": d["filename"]}
                     for i, d in enumerate(CORPUS["documents"])]

    def __call__(self, method, path, body=None, params=None):
        if method == "GET" and path.endswith("/documents"):
            off = params["offset"]
            page = self.docs[off:off + 10]
            return {"documents": page, "next_offset": off + 10 if off + 10 < len(self.docs) else None}
        assert method == "POST" and path == "/collections/c1/ask"
        api.v1.AskRequest(**body)  # raises on a field the API would reject
        self.asked.append(body)
        if body["question"].startswith("Who at Aldermark"):
            raise pilot.ApiError(502, "answer_failed", "model timed out")
        return {"answer": "32 in-lb [Source 1].", "model": "m", "corpus_version": "v9",
                "sources": [{"n": 1, "filename": "WI-2140 Bracket assembly 7731-204 rev G.md"}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_run_writes_a_review_sheet(tmp_path, monkeypatch):
    client = pilot.Client("https://clio.example/api/v1", "key")
    fake = _FakeApi()
    monkeypatch.setattr(client, "_request", fake)
    results = pilot.run_eval(client, "c1", CASES)
    scoped = next(b for b in fake.asked if b.get("document_ids"))
    rev_f = next(d["document_id"] for d in fake.docs if d["filename"].endswith("rev F.md")
                 and d["filename"].startswith("WI-2140"))
    assert scoped["document_ids"] == [rev_f] and all(b["use_cache"] is False for b in fake.asked)
    pilot.write_outputs(results, tmp_path, {"base_url": "x", "collection": "c1", "depth": "quick"})
    with (tmp_path / "review.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["id"] for r in rows] == [c["id"] for c in CASES]
    assert rows[0]["auto_mentions"] == "yes" and rows[0]["auto_sources"] == "yes"
    failed = next(r for r in rows if r["id"] == "q13")
    assert failed["answer"].startswith("ERROR: 502 answer_failed")
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "questions answered | 14/15" in summary and "q13: 502 answer_failed" in summary


def test_scorecard_counts_only_reviewer_marks():
    rows = [
        {"id": "q01", "reviewer_supported": "Y", "reviewer_citations_correct": "y",
         "reviewer_abstained_correctly": "", "reviewer_minutes": "2", "baseline_minutes": "14"},
        {"id": "q02", "reviewer_supported": "N", "reviewer_citations_correct": "yes",
         "reviewer_abstained_correctly": "", "reviewer_minutes": "4", "baseline_minutes": "20"},
        {"id": "q13", "reviewer_supported": "", "reviewer_citations_correct": "",
         "reviewer_abstained_correctly": "Y", "reviewer_minutes": "", "baseline_minutes": "9"},
    ]
    card = pilot.scorecard(rows)
    assert card["supported"] == {"yes": 1, "marked": 2, "rate": 0.5}
    assert card["abstained_correctly"]["marked"] == 1
    assert card["median_minutes_with_clio"] == 3 and card["median_minutes_today"] == 14
    assert card["unmarked"] == ["q13"]
    text = pilot.render_scorecard(card)
    assert "1/2 (50%)" in text and "4.7x faster" in text
