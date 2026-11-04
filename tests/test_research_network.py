"""Network analysis: the structural measures, the author and concept graphs, the
brokers, the reliability note, and the /api/research/network routes.

Papers are invented (PAPER_A and PAPER_B from tests/test_research.py). The
measures that NetworkX also has are checked against NetworkX, because
services/research/network.py reimplements them to avoid SciPy.
"""

import json
import os
import string
import subprocess
import sys
from math import isnan
from pathlib import Path

import networkx as nx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import config
from services.research import jobs, library, lookup, network
from services.sqlite_utils import sqlite_connect
from tests.coverage_support import build_collection
from tests.test_research import PAPER_A, PAPER_B

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_cached_results_between_tests():
    network.clear_cache()
    yield
    network.clear_cache()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No registry lookups and no reranker."""
    monkeypatch.setattr(config.settings, "research_lookup_enabled", False, raising=False)
    monkeypatch.setattr(config.settings, "enable_reranker", False, raising=False)
    lookup.clear_cache()


@pytest.fixture()
def docs_dir(tmp_path, monkeypatch):
    from services.collection_service import collection_service
    directory = tmp_path / "documents"
    directory.mkdir()
    monkeypatch.setattr(collection_service, "get_documents_path", lambda cid: directory)
    return directory


@pytest.fixture()
def indexer(tmp_path, docs_dir):
    ix = build_collection(tmp_path, {"paper-a.pdf": PAPER_A, "2018-download.pdf": PAPER_B,
                                     "notes.txt": ["Lab meeting notes: order more pots."]})
    ix.document_extractor.extract_text.side_effect = OSError("no file in tests")
    for name in ("paper-a.pdf", "2018-download.pdf", "notes.txt"):
        (docs_dir / name).write_bytes(b"%PDF-1.4 test")
    return ix


@pytest.fixture()
def client(indexer, monkeypatch):
    from api import research as api

    state = {"write": True}

    def access(collection_id, user_id, write=False, owner=False):
        if collection_id != "c1":
            raise HTTPException(404, "not found")
        if (write or owner) and not state["write"]:
            raise HTTPException(403, "read only")
        return "owner" if state["write"] else "read"

    monkeypatch.setattr(api, "get_indexer", lambda cid: (access(cid, None), indexer)[1])
    monkeypatch.setattr(api, "require_collection_access", access)
    monkeypatch.setattr(api.governance, "require_aup", lambda user: None)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _analyze_collection(client):
    job = client.post("/api/research/analyze?collection_id=c1", json={}).json()["job"]
    jobs.wait_for(job["job_id"])


def _quarantine(indexer, document_id):
    conn = sqlite_connect(indexer.vector_store.metadata_db_path)
    try:
        with conn:
            conn.execute("UPDATE documents SET policy_status = 'quarantined' WHERE document_id = ?",
                         (document_id,))
    finally:
        conn.close()


def _rows(result):
    return {row["id"]: row for row in result["nodes"]}


# ── structures ──────────────────────────────────────────────────────────

def test_barbell_path_node_is_the_broker_and_the_least_constrained():
    rows = _rows(network.analyze(nx.barbell_graph(5, 1)))
    others = [n for n in rows if n != 5]
    assert all(rows[5]["betweenness"] > rows[n]["betweenness"] for n in others)
    assert all(rows[5]["constraint"] < rows[n]["constraint"] for n in others)


def test_karate_club_communities_are_stable_and_clearly_modular():
    U = network.undirected_view(nx.karate_club_graph())
    groups, score = network.communities(U, seed=network.SEED)
    assert 2 <= len(groups) <= 4
    assert score > 0.35
    assert network.communities(U, seed=network.SEED) == (groups, score)


def test_louvain_gives_the_same_answer_in_two_processes_with_different_hash_seeds():
    script = (
        "import json, networkx as nx\n"
        "from services.research import network\n"
        "G = nx.relabel_nodes(nx.karate_club_graph(), {i: f'n{i}' for i in range(34)})\n"
        "groups, score = network.communities(network.undirected_view(G), seed=network.SEED)\n"
        "print(json.dumps([groups, round(score, 12)]))\n"
    )
    outputs = []
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        done = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env,
                              capture_output=True, text=True, check=True)
        outputs.append(done.stdout.strip())
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])[0]          # something was found, not an empty list


# ── Revere: projection and brokers ──────────────────────────────────────

REVERE = {
    "g1": ["Bridge", "A1", "A2", "A3", "A4"],
    "g2": ["Bridge", "A1", "B1", "B2", "B3"],
    "g3": ["Bridge", "C1", "C2", "C3"],
    "g4": ["Bridge", "D1", "D2", "D3", "D4"],
    "g5": ["Bridge", "E1", "E2", "E3", "E4", "E5"],
    "g6": ["F1", "F2", "F3", "F4"],
}


def test_projection_weights_sources_and_bridge_ranking():
    bipartite, G = network.project_memberships(REVERE)
    assert G["A1"]["Bridge"]["weight"] == 2
    assert sorted(G["A1"]["Bridge"]["sources"]) == ["g1", "g2"]
    assert bipartite.has_edge(("group", "g1"), ("member", "Bridge"))
    assert bipartite.has_edge(("group", "g6"), ("member", "F1"))

    rows = _rows(network.analyze(G))
    others = [n for n in rows if n != "Bridge"]
    assert all(rows["Bridge"]["betweenness"] > rows[n]["betweenness"] for n in others)
    assert all(rows["Bridge"]["eigenvector"] > rows[n]["eigenvector"] for n in others)
    assert all(rows["Bridge"]["constraint"] < rows[n]["constraint"] for n in others)


# ── degenerate graphs ───────────────────────────────────────────────────

DEGENERATE = {
    "empty_graph": (nx.Graph, "empty"),
    "empty_digraph": (nx.DiGraph, "empty"),
    "one_node": (lambda: nx.empty_graph(1), "empty"),
    "two_isolates": (lambda: nx.empty_graph(2), "empty"),
    "two_paths": (lambda: nx.disjoint_union(nx.path_graph(3), nx.path_graph(3)), None),
}


@pytest.mark.parametrize("name", sorted(DEGENERATE))
def test_degenerate_graphs_analyse_without_error_or_nan(name):
    make, level = DEGENERATE[name]
    result = network.analyze(make())
    json.dumps(result, allow_nan=False)
    assert result["reliability"]["reliable"] is False
    assert isinstance(result["reliability"]["note"], str) and result["reliability"]["note"]
    if level:
        assert result["reliability"]["level"] == level
    for row in result["nodes"]:
        if row["degree"] == 0:
            assert row["constraint"] is None and row["effective_size"] is None


def test_eigenvector_is_per_component_and_isolates_score_zero():
    G = nx.Graph()
    G.add_edge("i1", "i2")                      # a two-node island
    G.add_node("lone")                          # an isolate
    G.add_edges_from((f"c{a}", f"c{b}") for a in range(20) for b in range(a + 1, 20))   # a 20-node clique
    rows = _rows(network.analyze(G))
    island = [rows["i1"]["eigenvector"], rows["i2"]["eigenvector"]]
    clique = [rows[f"c{i}"]["eigenvector"] for i in range(20)]
    assert max(island) < min(clique)
    assert rows["lone"]["eigenvector"] == 0.0


# ── equivalence with NetworkX ───────────────────────────────────────────

def _weighted(G):
    """Deterministic integer weights, so the weighted formulas are exercised."""
    for a, b in G.edges:
        G[a][b]["weight"] = 1 + (3 * min(a, b) + 7 * max(a, b)) % 4
    return G


@pytest.mark.parametrize("make", [
    lambda: _weighted(nx.karate_club_graph()),
    lambda: _weighted(nx.gnp_random_graph(30, 0.2, seed=7)),
], ids=["karate", "gnp30"])
def test_structural_holes_match_networkx(make):
    G = make()
    constraint, effective = network.structural_holes(network.undirected_view(G))
    nx_constraint = nx.constraint(G, weight="weight")
    nx_effective = nx.effective_size(G, weight="weight")
    for node in G:
        if constraint[node] is None:
            assert isnan(nx_constraint[node]) and isnan(nx_effective[node])
            continue
        assert abs(constraint[node] - nx_constraint[node]) < 1e-9
        assert abs(effective[node] - nx_effective[node]) < 1e-9


def test_pagerank_matches_networkx_with_dangling_nodes():
    from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python
    D = nx.gnp_random_graph(40, 0.08, seed=11, directed=True)
    for a, b in D.edges:
        D[a][b]["weight"] = 1
    assert [n for n in D if D.out_degree(n) == 0], "the test graph needs nodes with no out-edges"
    ours = network.pagerank(D)
    theirs = _pagerank_python(D, tol=1e-10, max_iter=500)
    assert all(abs(ours[n] - theirs[n]) < 1e-6 for n in D)
    assert abs(sum(ours.values()) - 1) < 1e-9


def test_pagerank_follows_citations_from_citer_to_cited():
    D = nx.DiGraph()
    D.add_edges_from([("a", "c"), ("b", "c")])
    for a, b in D.edges:
        D[a][b]["weight"] = 1
    scores = network.pagerank(D)
    assert scores["c"] > max(scores["a"], scores["b"])


def test_analysis_runs_without_scipy(monkeypatch):
    monkeypatch.setitem(sys.modules, "scipy", None)
    with pytest.raises(ImportError):
        import scipy  # noqa: F401
    assert network.analyze(nx.barbell_graph(5, 1))["stats"]["nodes"] == 11
    D = nx.DiGraph([("a", "b"), ("b", "c")])
    assert network.analyze(D, "citation")["stats"]["directed"] is True


def test_undirected_view_handles_an_edge_to_an_earlier_string_id():
    D = nx.DiGraph()
    D.add_edge("z", "a")
    U = network.undirected_view(D)
    assert U.has_edge("a", "z") and U["a"]["z"]["weight"] == 1


def test_mutual_citation_is_one_tie():
    D = nx.DiGraph()
    D.add_edges_from([("a", "b"), ("b", "a")])
    U = network.undirected_view(D)
    assert U.number_of_edges() == 1


# ── betweenness approximation ───────────────────────────────────────────

def test_large_graph_uses_sampled_betweenness_deterministically(monkeypatch):
    monkeypatch.setattr(network, "BETWEENNESS_EXACT_MAX_NODES", 10)
    monkeypatch.setattr(network, "BETWEENNESS_SAMPLES", 5)
    G = nx.gnp_random_graph(30, 0.2, seed=3)
    first = network.analyze(G)
    assert first["stats"]["betweenness_approximate"] is True
    assert first["stats"]["betweenness_samples"] == 5
    assert network.analyze(G) == first


def test_small_graph_reports_exact_betweenness():
    stats = network.analyze(nx.path_graph(10))["stats"]
    assert stats["betweenness_approximate"] is False and stats["betweenness_samples"] is None


# ── reliability ─────────────────────────────────────────────────────────

def test_reliability_too_small_names_the_counts():
    note = network.reliability_note(16, 17, 13, 10)
    assert note["level"] == "too_small" and note["reliable"] is False
    assert "16" in note["note"] and "17" in note["note"]


def test_reliability_ok_at_the_minimums():
    note = network.reliability_note(network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_EDGES,
                                    network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_SOURCES, 0.5)
    assert note["level"] == "ok" and note["reliable"] is True and note["weak_groups"] is False


def test_reliability_sources_below_minimum_is_too_small():
    note = network.reliability_note(network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_EDGES,
                                    network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_SOURCES - 1, 0.5)
    assert note["level"] == "too_small" and note["reliable"] is False


def test_reliability_fragmented_below_largest_component_share():
    nodes = network.MIN_RELIABLE_NODES
    largest = int(nodes * network.MIN_LARGEST_COMPONENT_SHARE) - 1
    note = network.reliability_note(nodes, network.MIN_RELIABLE_EDGES, largest, network.MIN_RELIABLE_SOURCES, 0.5)
    assert note["level"] == "fragmented" and note["reliable"] is False


def test_reliability_weak_modularity_is_reported():
    note = network.reliability_note(network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_EDGES,
                                    network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_SOURCES,
                                    network.MIN_MEANINGFUL_MODULARITY - 0.1)
    assert note["level"] == "ok" and note["reliable"] is False
    assert note["weak_groups"] is True and "modularity" in note["note"]


def test_reliability_without_sources_skips_the_source_check():
    note = network.reliability_note(network.MIN_RELIABLE_NODES, network.MIN_RELIABLE_EDGES,
                                    network.MIN_RELIABLE_NODES, None, 0.5)
    assert note["level"] == "ok" and note["reliable"] is True


# ── author names ────────────────────────────────────────────────────────

@pytest.mark.parametrize("names, display", [
    (["J. Smith", "John Smith"], "John Smith"),
    (["Smith, John", "john  smith", "JOHN SMITH"], "John Smith"),
    (["Chang, Ming-Wei", "M. Chang"], "Ming-Wei Chang"),
    (["José García", "Jose Garcia"], "José García"),
    (["Ana de la Cruz", "A. de la Cruz"], "Ana de la Cruz"),
    (["Smith J", "John Smith"], "John Smith"),
])
def test_author_forms_merge_to_one_readable_name(names, display):
    mapping, merges, ambiguous = network.resolve_authors(names)
    assert {mapping[n] for n in names} == {display}
    assert len(merges) == 1 and merges[0]["name"] == display
    assert sorted(merges[0]["variants"]) == sorted(names)
    assert ambiguous == []


def test_ambiguous_author_forms_stay_apart_and_are_reported():
    cases = [
        ["J. Smith", "John Smith", "Jane Smith"],
        ["J. Smith", "J. R. Smith"],
        ["Y. Wang", "Yu Wang"],
        ["John A. Smith", "John B. Smith", "John Smith"],
    ]
    for names in cases:
        mapping, merges, ambiguous = network.resolve_authors(names)
        assert len({mapping[n] for n in names}) == len(names), names
        assert merges == [] and ambiguous, names


def test_distinct_full_names_are_not_ambiguous():
    mapping, merges, ambiguous = network.resolve_authors(["John Smith", "Jane Smith"])
    assert mapping == {"John Smith": "John Smith", "Jane Smith": "Jane Smith"}
    assert merges == [] and ambiguous == []


def test_parse_author_edge_cases():
    assert network.parse_author("et al") is None
    assert network.parse_author("") is None
    assert network.parse_author("Okafor1") == ((), "okafor")
    assert network.parse_author("Di Wang") == (("di",), "wang")
    assert network.parse_author("Ana de la Cruz") == (("ana",), "de la cruz")
    assert network.parse_author("John Smith Jr.")[1] == "smith"


def test_glued_author_strings_are_recognised_and_left_out():
    assert network.looks_glued("LuyuGao XueguangMa JimmyLin") is True
    assert network.looks_glued("Yann LeCun") is False
    assert network.looks_glued("LeCun") is False

    papers = [{"id": "p1", "title": "Glued", "authors": ["LuyuGao XueguangMa JimmyLin", "Ann Lee"]},
              {"id": "p2", "title": "Clean", "authors": ["Ann Lee", "Bo Ito"]}]
    _, G = network.author_graph(papers)
    assert G.graph["unparsed"] == ["LuyuGao XueguangMa JimmyLin"]
    assert "LuyuGao XueguangMa JimmyLin" not in G
    assert "Ann Lee" in G and G.has_edge("Ann Lee", "Bo Ito")


# ── author graph ────────────────────────────────────────────────────────

def test_author_graph_does_not_merge_an_ambiguous_initial():
    papers = [
        {"id": "p1", "title": "One", "authors": ["J. Smith", "Ann Lee"]},
        {"id": "p2", "title": "Two", "authors": ["John Smith", "Bo Ito"]},
        {"id": "p3", "title": "Three", "authors": ["Jane Smith", "Cy Ng"]},
    ]
    B, G = network.author_graph(papers)
    assert "J. Smith" in G and G.has_edge("J. Smith", "Ann Lee")
    assert not nx.has_path(G, "Ann Lee", "Bo Ito")


def test_unambiguous_initial_merges_and_is_reported():
    papers = [
        {"id": "p1", "title": "One", "authors": ["J. Smith", "Ann Lee"]},
        {"id": "p2", "title": "Two", "authors": ["John Smith", "Bo Ito"]},
    ]
    _, G = network.author_graph(papers)
    assert G.has_edge("John Smith", "Ann Lee") and G.has_edge("John Smith", "Bo Ito")
    assert nx.has_path(G, "Ann Lee", "Bo Ito")
    assert "J. Smith" not in G
    assert [m["name"] for m in G.graph["merges"]] == ["John Smith"]
    assert sorted(G.graph["merges"][0]["variants"]) == ["J. Smith", "John Smith"]


def test_paper_with_too_many_authors_makes_no_ties():
    letters = string.ascii_lowercase
    big = [f"Pat{letters[i // 26]}{letters[i % 26]} Quinn" for i in range(network.MAX_AUTHORS_PER_PAPER + 1)]
    papers = [{"id": "big", "title": "Consortium", "authors": big},
              {"id": "small", "title": "Pair", "authors": ["Ann Lee", "Bo Ito"]}]
    _, G = network.author_graph(papers)
    result = network.analyze(G, "authors")
    assert result["stats"]["skipped_large_sources"] == 1
    assert G.has_edge("Ann Lee", "Bo Ito")
    assert G.number_of_edges() == 1


# ── entity graph ────────────────────────────────────────────────────────

DOC_ENTITIES = {f"d{i}": set() for i in range(10)}
for entity, docs in {
    "xylem": ["d0", "d1"],
    "stomata": ["d0", "d1", "d2"],
    "embolism": ["d0", "d4"],
    "sapwood": ["d0", "d1", "d2", "d3"],       # in 4 of 10: over the ceiling of 3
    "lonely": ["d5"],                          # in one document: bridges nothing
    "figure": ["d0", "d1"],
    "figure2": ["d0", "d1"],
    "proceeding": ["d0", "d1"],
    "lewisetal": ["d0", "d1"],
    "finally": ["d0", "d1"],
}.items():
    for doc in docs:
        DOC_ENTITIES[doc].add(entity)


def test_entity_graph_drops_singletons_common_terms_and_headings():
    G = network.entity_graph(DOC_ENTITIES)
    assert set(G.nodes) == {"xylem", "stomata", "embolism"}
    assert G["xylem"]["stomata"]["sources"] == ["d0", "d1"]
    assert G["xylem"]["stomata"]["weight"] == 2
    assert G["embolism"]["stomata"]["sources"] == ["d0"]
    assert G.graph["entity_filter"] == {"total": 5, "in_one_document": 1, "too_common": 1,
                                        "max_doc_frequency": network.CORPUS_MAX_DOC_FREQUENCY}
    assert G.graph["cap"] is None


def test_entity_graph_exclude_removes_named_entities():
    G = network.entity_graph(DOC_ENTITIES, exclude=("xylem",))
    assert "xylem" not in G and "stomata" in G


def test_entity_graph_cap_keeps_the_most_frequent():
    G = network.entity_graph(DOC_ENTITIES, max_nodes=2)
    assert set(G.nodes) == {"stomata", "embolism"}
    assert G.graph["cap"] == {"limit": 2, "kept": 2, "dropped": 1}
    assert G["stomata"]["embolism"]["sources"] == ["d0"]


# ── brokers (citation network) ──────────────────────────────────────────

def _citation_json():
    papers = {
        "r1": "Dense Retrieval for Scholarly Archives",
        "r2": "Retrieval Augmented Indexing at Scale",
        "r3": "Sparse Retrieval Ranking Methods",
        "r4": "Retrieval Pipelines for Archive Search",
        "q1": "Question Answering over Long Tables",
        "q2": "Question Answering with Long Documents",
        "q3": "Answer Extraction for Question Answering",
        "q4": "Question Answering Evaluation Sets",
        "b1": "Grounding Claims in Cited Evidence",
    }
    cites = [
        ("r1", "r2"), ("r2", "r3"), ("r3", "r4"), ("r4", "r1"), ("r1", "r3"), ("r2", "r4"),
        ("q1", "q2"), ("q2", "q3"), ("q3", "q4"), ("q4", "q1"), ("q1", "q3"), ("q2", "q4"),
        ("r1", "b1"), ("r2", "b1"), ("r3", "b1"), ("q1", "b1"), ("q2", "b1"), ("q3", "b1"),
    ]
    return {
        "nodes": [{"id": pid, "type": "paper", "label": title, "title": title} for pid, title in papers.items()],
        "edges": [{"source": s, "target": t, "reason": f"same DOI {s}{t}", "reference_id": 100 + i}
                  for i, (s, t) in enumerate(cites)],
        "stats": {"papers": len(papers)},
    }


def test_bridge_paper_is_the_top_broker_with_checkable_evidence():
    graph = _citation_json()
    result = network.citation_network(graph)
    top = result["brokers"][0]
    assert top["id"] == "b1"
    assert top["sentence"].startswith("Connects ")
    assert "cited by 3 papers in each" in top["sentence"]
    assert len(top["connects"]) == 2
    assert {row["id"] for row in result["communities"]} >= set(top["connects"])


def test_broker_labels_are_distinct_nonempty_and_deterministic():
    graph = _citation_json()
    first = network.citation_network(graph)
    second = network.citation_network(graph)
    labels = [c["label"] for c in first["communities"]]
    assert all(labels) and len(set(labels)) == len(labels)
    assert labels == [c["label"] for c in second["communities"]]


def test_a_node_with_zero_betweenness_is_never_a_broker():
    result = network.citation_network(_citation_json())
    zero = {row["id"] for row in result["nodes"] if row["betweenness"] == 0}
    assert zero                                   # the clusters' own members
    assert all(b["betweenness"] > 0 for b in result["brokers"])
    assert not zero & {b["id"] for b in result["brokers"]}


def test_every_broker_edge_exists_in_the_input():
    graph = _citation_json()
    input_edges = {(e["source"], e["target"]): e for e in graph["edges"]}
    result = network.citation_network(graph)
    assert result["brokers"]
    for broker in result["brokers"]:
        for group in broker["bridges"]:
            for edge in group["edges"]:
                neighbour = edge["node"]
                if "cited_by" in edge:
                    source, target = neighbour, broker["id"]
                else:
                    source, target = broker["id"], neighbour
                assert (source, target) in input_edges
                match = input_edges[(source, target)]
                key = "cited_by" if "cited_by" in edge else "cites"
                assert edge[key]["reason"] == match["reason"]
                assert edge[key]["reference_id"] == match["reference_id"]


# ── HTTP API ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("graph", ["citation", "authors", "entities"])
def test_network_route_returns_each_graph(client, graph):
    _analyze_collection(client)
    response = client.get(f"/api/research/network?collection_id=c1&graph={graph}")
    assert response.status_code == 200
    body = response.json()
    assert body["graph"] == graph
    assert "reliability" in body and "stats" in body
    assert "pagerank" in body["definitions"]


def test_network_route_rejects_an_unknown_graph_and_collection(client):
    assert client.get("/api/research/network?collection_id=c1&graph=nonsense").status_code == 422
    assert client.get("/api/research/network?collection_id=nope").status_code == 404


def test_selection_is_honoured(client):
    _analyze_collection(client)
    cited = client.post("/api/research/network?collection_id=c1",
                        json={"graph": "citation", "document_ids": ["doc-1"]})
    assert cited.status_code == 200
    rows = cited.json()["nodes"]
    assert {r["id"] for r in rows if r["type"] == "paper"} == {"doc-1"}
    assert "doc-2" not in {r["id"] for r in rows}

    authors = client.post("/api/research/network?collection_id=c1",
                          json={"graph": "authors", "document_ids": ["doc-2"]}).json()
    names = [r["id"] for r in authors["nodes"]]
    assert any("Hale" in name for name in names)
    assert not any("Okafor" in name for name in names)


def test_selection_of_nothing_that_exists_is_not_found(client):
    response = client.post("/api/research/network?collection_id=c1", json={"document_ids": ["nope"]})
    assert response.status_code == 404


def test_quarantined_document_leaves_no_trace_in_any_graph(client, indexer):
    _analyze_collection(client)
    store = library.research_store(indexer)
    stale = [r for r in store.list_references("doc-1", kind="reference") if r["matched_document_id"] == "doc-2"]
    assert len(stale) == 1                       # the stale link is really there before quarantine
    _quarantine(indexer, "doc-2")
    assert [r["matched_document_id"] for r in store.list_references("doc-1", kind="reference")].count("doc-2") == 1

    # min_citations=1 is what exposes the stale link: only one paper cites doc-2,
    # so at the default of 2 it would never become a node, leak or no leak.
    urls = [f"/api/research/network?collection_id=c1&graph={graph}&min_citations=1"
            for graph in ("citation", "authors", "entities")]
    urls.append("/api/research/graph?collection_id=c1&min_citations=1")
    for url in urls:
        text = json.dumps(client.get(url).json())
        assert "doc-2" not in text and "Xylem Embolism Thresholds" not in text and "Hale" not in text, url


def test_a_document_merely_outside_the_selection_still_appears(client):
    # The other side of the quarantine rule: not hidden, just not selected.
    _analyze_collection(client)
    body = client.post("/api/research/graph?collection_id=c1",
                       json={"document_ids": ["doc-1"], "min_citations": 1}).json()
    assert "ext:lib:doc-2" in {n["id"] for n in body["nodes"]}


def test_seed_is_its_own_cache_entry_and_reported(client):
    _analyze_collection(client)
    url = "/api/research/network?collection_id=c1&graph=citation"
    assert client.get(url).json()["stats"]["seed"] == network.SEED
    other = client.get(f"{url}&seed=7").json()
    assert other["stats"]["seed"] == 7 and other["cached"] is False
    assert client.get(f"{url}&seed=-1").status_code == 422


def test_cache_serves_repeats_and_recomputes_on_change(client, indexer):
    _analyze_collection(client)
    url = "/api/research/network?collection_id=c1&graph=citation"
    assert client.get(url).json()["cached"] is False
    assert client.get(url).json()["cached"] is True

    selection = {"document_ids": ["doc-1"]}
    assert client.post("/api/research/network?collection_id=c1", json=selection).json()["cached"] is False
    assert client.post("/api/research/network?collection_id=c1", json=selection).json()["cached"] is True

    # Un-matching a link changes the research token (a count, not a timestamp,
    # so the test does not depend on the clock).
    store = library.research_store(indexer)
    link = next(r for r in store.list_references("doc-1", kind="reference") if r["matched_document_id"] == "doc-2")
    store.update_reference(link["id"], matched_document_id=None)
    assert client.get(url).json()["cached"] is False

    assert client.get(url).json()["cached"] is True
    _quarantine(indexer, "doc-2")
    assert client.get(url).json()["cached"] is False


def test_reference_list_entities_are_left_out_of_an_article(indexer):
    store = indexer.vector_store.metadata_store
    article = network.load_document_entities(store, [{"id": "doc-2"}])["doc-2"]
    book = network.load_document_entities(store, [{"id": "doc-2", "kind": "book"}])["doc-2"]
    assert article < book
    # PAPER_B's reference list names a venue and a journal, and nothing else in
    # the paper does; the book keeps them. (The person names in that list are
    # not extracted as entities at all.)
    left_out = book - article
    assert {"imaginary botany", "plant water letter"} <= left_out
    assert not (left_out & article)
