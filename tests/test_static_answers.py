"""C2: answers written in advance, published with the export."""

import json

import pytest

from services import static_export as se
from services.static_export import answers
from tests.browser_harness import static_server
from tests.static_real import passages_of, real_env, real_model_cache, sample_collection  # noqa: F401
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import handbook  # noqa: F401


def _cite(cid, doc_id, contains):
    return next(p["id"] for p in passages_of(cid, doc_id) if contains in p["text"])


@pytest.fixture()
def answered(handbook):
    cid, ids = handbook
    holiday = _cite(cid, ids[0], "25 days")
    good = answers.add_answer(cid, "How much holiday do staff get?", "Staff accrue 25 days a year [Source 1].",
                              [holiday], actor="owner")
    wrong = answers.add_answer(cid, "How much holiday?", "Staff accrue 30 days a year [Source 1].", [holiday],
                               actor="owner")
    waiting = answers.add_answer(cid, "Is holiday paid?", "Holiday is accrued [Source 1].", [holiday], actor="owner")
    for row in (good, wrong):
        answers.approve_answer(cid, row["id"], actor="owner")
    return cid, ids, {"good": good["id"], "wrong": wrong["id"], "waiting": waiting["id"]}


def test_only_approved_answers_that_pass_the_check_are_ready(answered):
    cid, _, a = answered
    review = {r["id"]: r for r in answers.review(cid)["answers"]}
    assert review[a["good"]]["status"] == "ready"
    assert review[a["waiting"]]["status"] == "waiting"
    assert review[a["wrong"]]["status"] == "check_failed" and review[a["wrong"]]["failures"]
    assert "Says something its passages do not" in review[a["wrong"]]["status_text"]


def test_the_export_carries_only_ready_answers_with_their_citations(answered, tmp_path):
    cid, ids, a = answered
    offer = next(x for x in se.preview(cid)["additions"] if x["id"] == "answers")
    assert offer["available"] and offer["note"].startswith("1 answer; 2 not ready, left out")
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True, include="answers")
    assert manifest["additions"]["answers"]["count"] == 1
    data = json.loads((tmp_path / "out" / "answers.json").read_text(encoding="utf-8"))
    [published] = data["answers"]
    assert published["id"] == a["good"] and published["question"] == "How much holiday do staff get?"
    assert published["citations"][0]["document_id"] == ids[0] and published["citations"][0]["n"] == 1
    assert published["sources"] == ["holidays"] and len(published["written"]) == 10
    assert data["label"] == answers.LABEL
    # The fake model has no measured threshold: listed, never matched.
    assert data["threshold"] is None and published["vector"] is None
    raw = (tmp_path / "out" / "answers.json").read_text(encoding="utf-8")
    assert "30 days" not in raw and "Is holiday paid" not in raw and "owner" not in raw


def test_an_answer_citing_a_left_out_source_is_not_published(answered, tmp_path):
    from services.indexer_manager import indexer_manager

    cid, ids, a = answered
    travel = _cite(cid, ids[2], "Clause 1 ")
    row = answers.add_answer(cid, "Which journeys are covered?", "Journeys of 7 miles [Source 1].", [travel],
                             actor="owner")
    answers.approve_answer(cid, row["id"], actor="owner")
    assert {r["id"]: r for r in answers.review(cid)["answers"]}[row["id"]]["status"] == "ready"
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(ids[2], sensitivity="internal")
    assert {r["id"]: r for r in answers.review(cid)["answers"]}[row["id"]]["status"] == "left_out_source"
    se.write_export(cid, tmp_path / "out", confirm_public=True, include="answers")
    raw = (tmp_path / "out" / "answers.json").read_text(encoding="utf-8")
    assert row["id"] not in raw and travel not in raw and "Which journeys" not in raw


def test_an_answer_needs_a_question_an_answer_and_citations(site):
    cid, _ = _public(site)
    for q, ans, cites in [("", "x [Source 1]", ["c"]), ("q", "", ["c"]), ("q", "x", []), ("q" * 301, "x", ["c"]),
                          ("q", "x", ["c", "c"])]:
        with pytest.raises(se.StaticExportError):
            answers.add_answer(cid, q, ans, cites, actor="o")
    with pytest.raises(se.StaticExportError) as e:
        answers.approve_answer(cid, "nope", actor="o")
    assert e.value.code == "not_found"


def test_http_add_approve_list_remove_with_audit(answered, monkeypatch):
    from fastapi.testclient import TestClient

    from api import deps
    from main import app
    from tests.test_static_export import _audit

    cid, ids, a = answered
    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(app)
    base = f"/api/collections/{cid}/export/static/answers"
    r = client.post(base, json={"question": "Q?", "answer": "A [Source 1].", "citations": [_cite(cid, ids[0], "25")]})
    assert r.status_code == 201 and r.json()["approved_at"] is None
    new = r.json()["id"]
    assert client.post(f"{base}/{new}/approval", json={"approved": True}).json()["approved_at"]
    listed = {x["id"]: x for x in client.get(base).json()["answers"]}
    assert listed[new]["status"] == "ready"
    assert client.post(f"{base}/nope/approval", json={}).status_code == 404
    assert client.post(base, json={"question": "Q", "answer": "A", "citations": []}).status_code == 422
    assert client.delete(f"{base}/{new}").status_code == 200 and client.delete(f"{base}/{new}").status_code == 404
    actions = [e["detail"]["action"] for e in _audit("collection.static_answers")]
    assert sorted(actions) == ["added", "approved", "removed"]


def test_answers_travel_in_a_bundle_and_wait_for_approval_there(answered):
    from services import collection_bundle as cb

    cid, _, _ = answered
    path, _ = cb.export_collection(cid)
    try:
        copy = cb.import_collection(path, owner_id="default", name="Copy")
    finally:
        path.unlink(missing_ok=True)
    rows = answers.review(copy["id"])["answers"]
    assert len(rows) == 3 and all(r["status"] in ("waiting", "missing_passage") for r in rows)
    assert all(r["approved_at"] is None and r["created_by"] == "import" for r in rows)


# ── With the real model, in a browser ───────────────────────────────────────


def test_a_paraphrase_shows_the_published_answer_and_an_unrelated_query_does_not(real_env, browser, tmp_path):
    cid, ids = sample_collection()
    policy = ids["01-equipment-policy.md"]
    cite = _cite(cid, policy, "five working days")
    row = answers.add_answer(cid, "When must shared equipment be returned?",
                             "Within five working days of checkout [Source 1].", [cite], actor="owner")
    answers.approve_answer(cid, row["id"], actor="owner")
    out = tmp_path / "site"
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, include="reader,answers")
    data = json.loads((out / "search/answers.json").read_text(encoding="utf-8"))
    assert data["threshold"] == answers.THRESHOLDS["all-MiniLM-L6-v2"] and len(data["answers"][0]["vector"]) == 384

    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("index.html"))
        box = "document.querySelector('clio-search')"
        page.eval(f"{box}.setAttribute('load', 'now')")
        page.type_into(f"{box}.shadowRoot.querySelector('input')", "x")
        assert page.wait_for(f"{box}.search && {box}.search.semantic", timeout=120)

        def ask(query):
            page.type_into(f"{box}.shadowRoot.querySelector('input')", query)
            page.wait_for(f"{box}.shadowRoot.querySelectorAll('li').length > 0 && "
                          f"{box}.search.lastVector && true", timeout=60)
            page.eval("new Promise(r => setTimeout(r, 400))")
            return page.eval(f"(() => {{ const a = {box}.shadowRoot.querySelector('.answer');"
                             " return a.hidden ? null : a.textContent })()")

        shown = ask("How long do I have to bring back borrowed equipment?")
        assert shown and "five working days" in shown and "Written in advance" in shown
        assert "01 equipment policy" in shown or "EQ-17" in shown or "equipment policy" in shown.lower()
        link = page.eval(f"{box}.shadowRoot.querySelector('.answer a').href")
        assert link.startswith(server.url())
        assert ask("What is the capital of Australia?") is None
        assert not [u for u in page.requested() if not u.startswith((server.url(), "data:", "blob:"))]
        page.close()
