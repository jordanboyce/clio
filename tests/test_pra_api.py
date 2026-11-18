"""PRA REST routes (/api/pra) and the MCP tools that share their shapes.

Models are built with the dataclasses and stored with store.save_model, as
the ingest step will; documents are metadata rows marked source_format
"pra-...".
"""

import asyncio
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from services.pra import analysis, queries, store
from services.pra.model import (BasicEvent, CutSetList, EventTree, FaultTree, Gate, HouseEvent, PraModel,
                                Sequence, SequencePathStep)
from tests.test_governance import _make_indexer

DOC = "pradoc1"


def _pumps_model():
    m = PraModel("PUMPS", "open-psa-mef", source_files=["pumps.xml"], description="Two pump trains")
    m.fault_trees["PUMPS-FT"] = FaultTree("PUMPS-FT", top="TOP", description="Both trains fail",
                                          gates=["TOP", "TRAIN-A", "TRAIN-B"])
    m.fault_trees["SUPPORT/AC"] = FaultTree("SUPPORT/AC", top="AC", gates=["AC"])
    m.fault_trees["NO-LIST-FT"] = FaultTree("NO-LIST-FT", top="SPARE", gates=["SPARE"])
    m.gates["TOP"] = Gate("TOP", "and", ["TRAIN-A", "TRAIN-B"], tree="PUMPS-FT", description="No flow")
    m.gates["TRAIN-A"] = Gate("TRAIN-A", "or", ["PUMP-A", "VALVE", "AC"], tree="PUMPS-FT")
    m.gates["TRAIN-B"] = Gate("TRAIN-B", "or", ["PUMP-B", "VALVE", "/HOUSE-OFF"], tree="PUMPS-FT")
    m.gates["AC"] = Gate("AC", "or", ["GRID", "UNKNOWN-EV"], tree="SUPPORT/AC")
    m.gates["SPARE"] = Gate("SPARE", "or", ["PUMP-A", "VALVE", "AC"], tree="NO-LIST-FT")
    m.basic_events["PUMP-A"] = BasicEvent("PUMP-A", "Pump A fails to run", probability=0.1)
    m.basic_events["PUMP-B"] = BasicEvent("PUMP-B", "Pump B fails to run", probability=0.2)
    m.basic_events["VALVE"] = BasicEvent("VALVE", "Common valve closed", probability=0.01)
    m.basic_events["GRID"] = BasicEvent("GRID", "Offsite power lost", probability=0.05)
    m.house_events["HOUSE-OFF"] = HouseEvent("HOUSE-OFF", state=True)
    m.initiating_events["IE-LOOP"] = BasicEvent("IE-LOOP", "Loss of offsite power", probability=0.5)
    m.event_trees["LOOP"] = EventTree(
        "LOOP", initiating_event="IE-LOOP", functional_events=["PUMPS-FT"],
        sequences=[Sequence("LOOP-1", end_state="OK", path=[SequencePathStep("PUMPS-FT", "success")],
                            logic=["/PUMPS-FT"]),
                   Sequence("LOOP-2", end_state="CD", path=[SequencePathStep("PUMPS-FT", "failure")],
                            logic=["PUMPS-FT"])])
    m.end_states["CD"] = "Core damage"
    m.cut_sets.append(CutSetList("PUMPS-FT", cut_sets=[["VALVE"], ["PUMP-A", "PUMP-B"], ["GRID", "PUMP-B"]],
                                 truncation=1e-12))
    m.cut_sets.append(CutSetList("LOOP-2", kind="sequence", event_tree="LOOP",
                                 cut_sets=[["IE-LOOP", "VALVE"], ["IE-LOOP", "PUMP-A", "PUMP-B"]]))
    m.warnings.append("example reader warning")
    return m


def _add_doc(indexer, document_id, filename, source_format, model=None, policy_status="clear"):
    indexer.vector_store.metadata_store.add_document(
        document_id, filename, num_pages=1, num_chunks=0, upload_timestamp="2026-11-18T00:00:00",
        source_format=source_format, content_hash=f"hash-{document_id}", policy_status=policy_status)
    if model is not None:
        store.save_model(indexer.vector_store.index_dir, document_id, model)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    import main

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    indexer = _make_indexer(tmp_path / "col")
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    monkeypatch.setattr(deps, "_initialized", True)
    _add_doc(indexer, DOC, "pumps.xml", "pra-mef", _pumps_model())
    _add_doc(indexer, "plaindoc", "notes.md", "md")
    _add_doc(indexer, "noparse", "broken.mard", "pra-mard")
    c = TestClient(main.app)
    c.indexer = indexer
    return c


def _get(client, path, **params):
    return client.get(f"/api/pra{path}", params=params)


# ── listing and structure ──────────────────────────────────────────────────

def test_models_lists_only_pra_documents(client):
    r = _get(client, "/models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "screening estimate" in body["notice"].lower()
    by_id = {m["document_id"]: m for m in body["models"]}
    assert set(by_id) == {DOC, "noparse"}
    assert by_id[DOC]["name"] == "PUMPS" and by_id[DOC]["counts"]["fault_trees"] == 3
    assert by_id[DOC]["warnings"] == ["example reader warning"] and by_id[DOC]["source_format"] == "pra-mef"
    assert by_id["noparse"]["counts"] is None and "re-index" in by_id["noparse"]["warnings"][0]
    assert _get(client, "/models", collection_id="nope").status_code == 404


def test_model_summary(client):
    r = _get(client, f"/models/{DOC}")
    assert r.status_code == 200, r.text
    body = r.json()
    trees = {t["name"]: t for t in body["fault_trees"]}
    assert trees["PUMPS-FT"] == {"name": "PUMPS-FT", "top": "TOP", "description": "Both trains fail",
                                 "gate_count": 3, "has_cut_sets": True}
    assert body["event_trees"][0]["name"] == "LOOP" and body["event_trees"][0]["sequences"] == 2
    assert body["end_states"] == [{"name": "CD", "description": "Core damage"}]
    assert {c["context"] for c in body["cut_set_lists"]} == {"PUMPS-FT", "LOOP-2"}
    assert body["notice"] and body["warnings"] == ["example reader warning"]
    assert _get(client, "/models/plaindoc").status_code == 404
    assert _get(client, "/models/missing").status_code == 404
    assert _get(client, "/models/noparse").status_code == 404


def test_fault_tree_structure_is_depth_limited_and_marks_transfers(client):
    r = _get(client, f"/models/{DOC}/fault-trees/PUMPS-FT", depth=2)
    assert r.status_code == 200, r.text
    s = r.json()["structure"]
    assert s["kind"] == "gate" and s["name"] == "TOP" and s["type"] == "and" and s["description"] == "No flow"
    train_a, train_b = s["children"]
    kinds = {c["name"]: c for c in train_a["children"]}
    assert kinds["PUMP-A"] == {"kind": "basic_event", "name": "PUMP-A", "description": "Pump A fails to run",
                               "probability": 0.1}
    assert kinds["AC"] == {"kind": "transfer", "name": "AC", "tree": "SUPPORT/AC"}
    house = train_b["children"][2]
    assert house == {"kind": "house_event", "name": "HOUSE-OFF", "complemented": True, "description": "",
                     "state": True}
    shallow = _get(client, f"/models/{DOC}/fault-trees/PUMPS-FT", depth=1).json()["structure"]
    assert shallow["children"][0]["more"] is True and shallow["children"][0]["input_count"] == 3
    # A tree name with a slash travels in the path.
    ac = _get(client, f"/models/{DOC}/fault-trees/SUPPORT/AC").json()["structure"]
    assert [c["kind"] for c in ac["children"]] == ["basic_event", "undefined"]
    assert _get(client, f"/models/{DOC}/fault-trees/NOPE").status_code == 404
    assert _get(client, f"/models/{DOC}/fault-trees/PUMPS-FT", depth=0).status_code == 422
    assert _get(client, f"/models/{DOC}/fault-trees/PUMPS-FT", depth=11).status_code == 422


def test_fault_tree_view_is_cycle_safe():
    m = _pumps_model()
    m.gates["TRAIN-A"].inputs.append("TOP")
    view = queries.fault_tree_view(m, "PUMPS-FT", depth=5)
    loop = view["structure"]["children"][0]["children"][-1]
    assert loop["name"] == "TOP" and loop["cycle"] is True and "children" not in loop
    view = queries.fault_tree_view(m, "PUMPS-FT", depth=5, max_nodes=3)
    assert view["truncated"] is True


def test_event_tree_and_event(client):
    r = _get(client, f"/models/{DOC}/event-trees/LOOP")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["initiating_event"] == "IE-LOOP" and body["initiating_event_frequency"] == 0.5
    assert [s["name"] for s in body["sequences"]] == ["LOOP-1", "LOOP-2"]
    assert body["sequences"][1]["has_cut_sets"] is True and body["functional_events"][0]["fault_tree"] == "PUMPS-FT"
    assert _get(client, f"/models/{DOC}/event-trees/NOPE").status_code == 404

    ev = _get(client, f"/models/{DOC}/events/VALVE").json()
    assert ev["kind"] == "basic_event" and ev["probability"] == 0.01
    assert {u["gate"] for u in ev["used_in_gates"]} == {"SPARE", "TRAIN-A", "TRAIN-B"}
    assert ev["fault_trees"] == ["NO-LIST-FT", "PUMPS-FT"]
    assert ev["stored_cut_sets"]["total"] == 2
    assert {c["context"] for c in ev["stored_cut_sets"]["by_context"]} == {"PUMPS-FT", "LOOP-2"}
    house = _get(client, f"/models/{DOC}/events/HOUSE-OFF").json()
    assert house["kind"] == "house_event" and house["used_in_gates"][0]["complemented"] is True
    tree = _get(client, f"/models/{DOC}/events/PUMPS-FT").json()
    assert tree["kind"] == "fault_tree" and {s["branch"] for s in tree["sequences"]} == {"success", "failure"}
    assert tree["event_trees"] == ["LOOP"]
    assert _get(client, f"/models/{DOC}/events/IE-LOOP").json()["event_trees"] == ["LOOP"]
    assert _get(client, f"/models/{DOC}/events/NOPE").status_code == 404


# ── cut sets and importance ────────────────────────────────────────────────

def test_stored_cut_sets_are_quantified(client):
    r = _get(client, f"/models/{DOC}/cut-sets/PUMPS-FT", limit=2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "model" and body["kind"] == "fault_tree" and body["truncation"] == 1e-12
    assert body["cut_set_count"] == 3 and body["returned"] == 2
    first, second = body["cut_sets"]
    assert first["events"] == ["PUMP-A", "PUMP-B"] and first["probability"] == pytest.approx(0.02)
    assert second["events"] == ["GRID", "PUMP-B"] and second["probability"] == pytest.approx(0.01)
    total = 0.02 + 0.01 + 0.01
    assert body["totals"]["rare_event"] == pytest.approx(total)
    assert body["totals"]["mcub"] == pytest.approx(1 - 0.98 * 0.99 * 0.99)
    assert first["share"] == pytest.approx(0.02 / total)
    assert body["events"]["PUMP-A"]["description"] == "Pump A fails to run"
    assert "screening estimate" in body["notice"].lower() and body["missing_data"]["events"] == []


def test_sequence_cut_sets_carry_the_frequency(client):
    body = _get(client, f"/models/{DOC}/cut-sets/LOOP-2").json()
    assert body["kind"] == "sequence" and body["event_tree"] == "LOOP"
    assert body["totals"]["rare_event"] == pytest.approx(0.5 * 0.01 + 0.5 * 0.02)
    assert body["totals"]["mcub"] is None and any("frequency" in w for w in body["warnings"])
    imp = _get(client, f"/models/{DOC}/importance/LOOP-2").json()
    assert imp["basis"] == "rare_event"


def test_computed_cut_sets_and_their_errors(client):
    r = _get(client, f"/models/{DOC}/cut-sets/NO-LIST-FT")
    assert r.status_code == 404 and "compute=true" in r.json()["detail"]
    r = _get(client, f"/models/{DOC}/cut-sets/NO-LIST-FT", compute="true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "computed" and body["computation"]["complete"] is True
    assert body["computation"]["top_gate"] == "SPARE"
    assert [c["events"] for c in body["cut_sets"]] == [["PUMP-A"], ["GRID"], ["VALVE"], ["UNKNOWN-EV"]]
    assert body["missing_data"] == {"events": ["UNKNOWN-EV"], "events_total": 1, "cut_sets_without_data": 1}
    assert body["totals"]["rare_event"] == pytest.approx(0.16)
    # A gate works too (/HOUSE-OFF is false: it drops out); a sequence without a stored list is not generated.
    gate = _get(client, f"/models/{DOC}/cut-sets/TRAIN-B", compute="true").json()
    assert [c["events"] for c in gate["cut_sets"]] == [["PUMP-B"], ["VALVE"]] and gate["kind"] == "gate"
    r = _get(client, f"/models/{DOC}/cut-sets/LOOP-1", compute="true")
    assert r.status_code == 404 and "fault trees and gates only" in r.json()["detail"]
    assert _get(client, f"/models/{DOC}/cut-sets/NOPE", compute="true").status_code == 404
    assert _get(client, f"/models/{DOC}/cut-sets/PUMPS-FT", limit=0).status_code == 422
    assert _get(client, f"/models/{DOC}/cut-sets/PUMPS-FT", truncation=2).status_code == 422
    assert _get(client, f"/models/{DOC}/cut-sets/NO-LIST-FT", compute="true",
                success_terms="maybe").status_code == 422


def test_a_guard_with_no_result_is_409_and_a_busy_server_is_429(client, monkeypatch):
    def stopped(model, context, **kw):
        return analysis.GenerationResult(
            target=context, top_gate=context, cut_sets=[], truncation=1e-3, requested_truncation=1e-15,
            max_order=None, complete=False, stopped="time_budget", truncation_raised=False, non_coherent=False,
            warnings=[], stats={})

    monkeypatch.setattr(analysis, "generate_cut_sets", stopped)
    r = _get(client, f"/models/{DOC}/cut-sets/NO-LIST-FT", compute="true", truncation=1e-14)
    assert r.status_code == 409 and "time budget" in r.json()["detail"]

    monkeypatch.setattr(queries, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(queries, "COMPUTE_SLOT_WAIT_SECONDS", 0.01)
    queries._slots.acquire()
    r = _get(client, f"/models/{DOC}/cut-sets/NO-LIST-FT", compute="true", truncation=1e-13)
    assert r.status_code == 429 and r.headers["retry-after"] == str(queries.COMPUTE_RETRY_AFTER_SECONDS)


def test_importance_sorts_by_each_measure(client):
    r = _get(client, f"/models/{DOC}/importance/PUMPS-FT")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "mcub" and body["sort"] == "fv" and body["events_total"] == 4
    rows = {e["event"]: e for e in body["events"]}
    total = 1 - 0.98 * 0.99 * 0.99
    assert body["total"] == pytest.approx(total)
    assert rows["PUMP-B"]["fussell_vesely"] == pytest.approx((total - 0.01) / total)
    assert rows["PUMP-B"]["cut_sets"] == 2 and rows["PUMP-B"]["description"] == "Pump B fails to run"
    assert body["events"][0]["event"] == "PUMP-B"
    for sort in ("raw", "rrw", "birnbaum"):
        ranked = _get(client, f"/models/{DOC}/importance/PUMPS-FT", sort=sort).json()["events"]
        key = {"raw": "raw", "rrw": "rrw", "birnbaum": "birnbaum"}[sort]
        values = [e[key] for e in ranked]
        assert values == sorted(values, reverse=True)
    assert _get(client, f"/models/{DOC}/importance/PUMPS-FT", sort="nope").status_code == 422
    assert _get(client, f"/models/{DOC}/importance/PUMPS-FT", limit=5000).status_code == 422
    computed = _get(client, f"/models/{DOC}/importance/NO-LIST-FT", compute="true").json()
    assert computed["source"] == "computed" and computed["events_total"] == 4


# ── access ─────────────────────────────────────────────────────────────────

def test_a_quarantined_model_is_hidden_from_non_reviewers(client, monkeypatch):
    from fastapi import HTTPException

    from api import deps

    with sqlite3.connect(client.indexer.vector_store.metadata_store.db_path) as conn:
        conn.execute("UPDATE documents SET policy_status = 'quarantined' WHERE document_id = ?", (DOC,))

    def deny(action):
        raise HTTPException(status_code=403, detail=action)

    monkeypatch.setattr(deps, "require_admin", deny)
    assert DOC not in {m["document_id"] for m in _get(client, "/models").json()["models"]}
    for path in ("", "/fault-trees/PUMPS-FT", "/cut-sets/PUMPS-FT", "/importance/PUMPS-FT", "/events/VALVE",
                 "/event-trees/LOOP"):
        assert _get(client, f"/models/{DOC}{path}").status_code == 404, path


def test_another_users_private_collection_is_404(private_client, monkeypatch, tmp_path):
    from api import deps
    from services.indexer_manager import indexer_manager
    from tests.test_private_collections import ALICE, BOB, _as

    r = private_client.post("/api/collections", json={"name": "Alice's PRA"}, headers=_as(ALICE))
    assert r.status_code == 201
    cid = r.json()["id"]
    indexer = _make_indexer(tmp_path / "alice")
    monkeypatch.setitem(indexer_manager._indexers, cid, indexer)
    monkeypatch.setattr(deps, "_initialized", True)
    _add_doc(indexer, DOC, "pumps.xml", "pra-mef", _pumps_model())

    paths = ["/models", f"/models/{DOC}", f"/models/{DOC}/fault-trees/PUMPS-FT", f"/models/{DOC}/cut-sets/PUMPS-FT",
             f"/models/{DOC}/importance/PUMPS-FT", f"/models/{DOC}/events/VALVE", f"/models/{DOC}/event-trees/LOOP"]
    for path in paths:
        url = f"/api/pra{path}?collection_id={cid}"
        assert private_client.get(url, headers=_as(ALICE)).status_code == 200, path
        bob = private_client.get(url, headers=_as(BOB))
        assert bob.status_code == 404, path
        assert "PUMPS" not in bob.text


# ── MCP tools ──────────────────────────────────────────────────────────────

@pytest.fixture()
def mcp_indexer(client, monkeypatch):
    import services.mcp_server as mcp

    monkeypatch.setattr(mcp.settings, "mcp_default_collection", "default")
    return client.indexer


def _call(name, args):
    import services.mcp_server as mcp

    _, structured = asyncio.run(mcp._clio_mcp.call_tool(name, args))
    return structured


def test_mcp_tools_mirror_the_rest_shapes(client, mcp_indexer):
    listing = _call("pra_models", {})
    assert {m["document_id"] for m in listing["models"]} == {DOC, "noparse"} and listing["collection_id"] == "default"
    summary = _call("pra_models", {"document_id": DOC})
    rest = client.get(f"/api/pra/models/{DOC}").json()
    assert summary["fault_trees"] == rest["fault_trees"] and summary["notice"] == queries.NOTICE

    tree = _call("pra_fault_tree", {"document_id": DOC, "tree": "PUMPS-FT", "depth": 2})
    assert tree["structure"] == client.get(f"/api/pra/models/{DOC}/fault-trees/PUMPS-FT?depth=2").json()["structure"]
    cuts = _call("pra_cut_sets", {"document_id": DOC, "context": "PUMPS-FT", "limit": 2})
    rest_cuts = client.get(f"/api/pra/models/{DOC}/cut-sets/PUMPS-FT?limit=2").json()
    assert cuts["cut_sets"] == rest_cuts["cut_sets"] and cuts["totals"] == rest_cuts["totals"]
    imp = _call("pra_importance", {"document_id": DOC, "context": "PUMPS-FT", "sort": "raw"})
    assert imp["events"] == client.get(f"/api/pra/models/{DOC}/importance/PUMPS-FT?sort=raw&limit=20").json()["events"]
    ev = _call("pra_event", {"document_id": DOC, "name": "VALVE"})
    assert ev["stored_cut_sets"]["total"] == 2 and ev["filename"] == "pumps.xml"
    computed = _call("pra_cut_sets", {"document_id": DOC, "context": "NO-LIST-FT", "compute": True})
    assert computed["source"] == "computed"


def test_mcp_tools_refuse_what_they_cannot_show(client, mcp_indexer):
    with pytest.raises(ToolError, match="not a PRA model"):
        _call("pra_fault_tree", {"document_id": "plaindoc", "tree": "X"})
    with pytest.raises(ToolError, match="not found"):
        _call("pra_event", {"document_id": "missing", "name": "X"})
    with pytest.raises(ToolError, match="compute=true"):
        _call("pra_cut_sets", {"document_id": DOC, "context": "NO-LIST-FT"})
    with pytest.raises(ToolError, match="less than or equal to 100"):
        _call("pra_cut_sets", {"document_id": DOC, "context": "PUMPS-FT", "limit": 500})
    with pytest.raises(ToolError):
        _call("pra_importance", {"document_id": DOC, "context": "PUMPS-FT", "sort": "nope"})
    with sqlite3.connect(client.indexer.vector_store.metadata_store.db_path) as conn:
        conn.execute("UPDATE documents SET policy_status = 'quarantined' WHERE document_id = ?", (DOC,))
    with pytest.raises(ToolError, match="not found"):
        _call("pra_models", {"document_id": DOC})
    assert DOC not in {m["document_id"] for m in _call("pra_models", {})["models"]}


def test_mcp_pra_tools_are_read_only_and_short():
    import services.mcp_server as mcp

    tools = {t.name: t for t in asyncio.run(mcp._clio_mcp.list_tools())}
    for name in ("pra_models", "pra_fault_tree", "pra_cut_sets", "pra_importance", "pra_event"):
        assert tools[name].annotations.readOnlyHint is True
        assert len(tools[name].description) < 400
    assert "pra_models" in mcp._SERVER_INSTRUCTIONS


# Fixtures shared with the private-collections tests.
from tests.test_private_collections import fresh_db, private_client  # noqa: E402,F401
