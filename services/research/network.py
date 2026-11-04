"""Network analysis of a collection: which papers, authors and concepts hold it
together, and why.

The method is Kieran Healy's "Using Metadata to Find Paul Revere" (2013): take a
membership table, project it to a person-to-person graph, and let a few
structural measures find the brokers. It claims exactly that much. A high score
says how a node sits in these links, not that a paper is good or a person
important, and every broker carries the edges that make it one, so the claim can
be checked against the papers.

Three graphs, all built from data the citation map already loads:

* citations: directed, from ``build_graph``. An edge A → B means A cites B.
* authors: authors × papers, projected to co-authorship.
* entities: entities × documents, projected to co-mention.

SciPy is deliberately not required: ``requirements.txt`` has no SciPy, so the
slim image has none, and adding it would cost about 100 MB for three formulas.
That matters because NetworkX is pure Python only in part. ``nx.pagerank``
imports SciPy unconditionally, and without it ``nx.constraint`` and
``nx.effective_size`` fall back to code that recomputes each normalisation on
every lookup, roughly O(d³) per node, which took minutes on a dense 200-node
projection. ``pagerank`` and ``structural_holes`` below compute the same
formulas with the normalisations computed once; tests check them against
NetworkX's own results.
"""

from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from itertools import combinations
from typing import Any, Callable, Dict, Hashable, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import networkx as nx

from services.entity_graph import _STOPWORDS, CORPUS_MAX_DOC_FREQUENCY, corpus_filter, normalize_entity
from services.research.extract import _REF_HEAD_RE

# ── named limits ────────────────────────────────────────────────────────

# Below these a graph is reported as too small for centrality to mean much:
# one added or removed link can reorder the top. There is no canonical cut-off;
# these are deliberately cautious, and they are what the reliability note cites.
MIN_RELIABLE_NODES = 50
MIN_RELIABLE_EDGES = 50
# Projections inherit their evidence from the sources behind them: ten papers
# can yield 200 co-mentioned entities, and those are still ten papers.
MIN_RELIABLE_SOURCES = 30
# Below this share in the largest connected part, scores compare separate islands.
MIN_LARGEST_COMPONENT_SHARE = 0.5
# Below this the groups barely stand out from chance, so "connects A and B" is tentative.
MIN_MEANINGFUL_MODULARITY = 0.3

SEED = 42
BETWEENNESS_EXACT_MAX_NODES = 500
BETWEENNESS_SAMPLES = 200
MAX_ENTITY_NODES = 200
MAX_AUTHOR_NODES = 1000
# A consortium paper's clique says nothing about who brokers between whom, and
# 1,000 authors would make half a million edges.
MAX_AUTHORS_PER_PAPER = 50
TOP_BROKERS = 10
TOP_KEY_NODES = 10
MAX_EVIDENCE_EDGES = 20
MAX_EVIDENCE_SOURCES = 8
CACHE_ENTRIES = 64

GRAPH_KINDS = ("citation", "authors", "entities")

# Bibliographic furniture. The entity extractor keeps any capitalised phrase, so
# section headings and venues pass for concepts; entries go through
# normalize_entity because that is the form the store holds ("proceeding").
_BIBLIOGRAPHIC_ENTITIES = frozenset(normalize_entity(term) for term in (
    "arXiv", "arXiv preprint", "CoRR", "DOI", "URL", "HTTP", "HTTPS", "PDF", "ISBN", "ISSN",
    "ACL", "AAAI", "ACM", "IEEE", "EMNLP", "EMNLP-IJCNLP", "NAACL", "NeurIPS", "NIPS", "ICLR", "ICML",
    "CVPR", "SIGIR", "ECCV", "ICCV", "KDD", "TACL",
    "Proceedings", "Conference", "International Conference", "Annual Meeting", "Association", "Journal",
    "Transactions", "Workshop", "Symposium", "Press", "University", "Advances", "Technologies", "Volume",
    "North American Chapter", "Computational Linguistics", "Linguistics", "Computational",
    "Association for Computational Linguistics", "Neural Information Processing Systems",
    "Advances in Neural Information Processing Systems", "Machine Learning Research", "Empirical Methods",
    "Natural Language Processing",
    "Figure", "Fig", "Table", "Section", "Appendix", "Abstract", "Introduction", "Conclusion", "References",
    "Reference", "Related Work", "Results", "Discussion", "Acknowledgments", "Acknowledgements", "Method",
    "Methods", "Experiment", "Experiments", "Comparison", "Analysis", "Et Al",
    "Equal Contribution", "EqualContribution",
    # Sentence openers the extractor can keep.
    "Finally", "Moreover", "However", "Furthermore", "Additionally", "Therefore", "Thus", "Overall",
    "Specifically", "Similarly", "Recently", "Next", "Instead", "Second", "Third",
))
# "Figure 2" glued by PDF extraction, and "Lewis et al" glued the same way.
_NUMBERED_OR_CITATION = re.compile(r"^(?:figure|fig|table|section|appendix|equation|eq)\d*$|.{3,}etal$")

_GENERIC_TITLE_TERMS = frozenset("""
paper papers study studies analysis approach approaches method methods using based toward towards via
survey review case evidence framework system systems results effect effects role use novel improved
improving efficient effective learning model models new data
""".split())


# ── author names ────────────────────────────────────────────────────────

_PARTICLES = frozenset("de la le du del della der den van von di da dos das ten ter bin ibn al el st".split())
_SUFFIXES = frozenset("jr sr ii iii iv phd md".split())
_NOT_A_PERSON = frozenset({"et al", "et", "al", "others", "and others", "anonymous", "unknown"})
# An initial tells these surnames apart from almost nobody: "Y. Wang" in a
# machine-learning library is many people, so an initial never merges them.
COMMON_SURNAMES = frozenset("""
wang li zhang liu chen yang huang zhao wu zhou xu sun ma zhu hu guo he gao lin luo zheng liang xie tang
kim lee park choi jung kang cho yoon nguyen tran pham le singh kumar gupta sharma patel khan ali
""".split())


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def parse_author(raw: str) -> Optional[Tuple[Tuple[str, ...], str]]:
    """``(given_tokens, surname)`` in folded lowercase, or None if it is no name.

    Handles case, spacing, accents, footnote marks, "Last, First", "Smith JR"
    and surname particles. An initial is simply a one-letter given token.
    """
    text = re.sub(r"[\d*†‡§¶]+", " ", _fold(raw or "")).replace("’", "'")
    text = " ".join(text.split()).strip(" ,;")
    if not text or text.casefold() in _NOT_A_PERSON:
        return None
    if "," in text:
        last, _, rest = text.partition(",")
        rest = rest.strip()
        text = last if rest.replace(".", "").casefold() in _SUFFIXES or not rest else f"{rest} {last}"
    tokens = [t for t in re.split(r"[\s.]+", text) if t]
    while len(tokens) > 1 and tokens[-1].casefold() in _SUFFIXES:
        tokens.pop()
    # "Smith JR" (surname first, initials in capitals), but not "SMITH JR".
    if len(tokens) == 2 and tokens[1].isupper() and len(tokens[1]) <= 3 and not tokens[0].isupper():
        tokens = list(tokens[1]) + [tokens[0]]
    if not tokens:
        return None
    # Particles join the surname, but the first token is never one: "Di Wang"
    # and "Da Li" are given names.
    start = len(tokens) - 1
    while start > 1 and tokens[start - 1].casefold() in _PARTICLES:
        start -= 1
    surname = " ".join(tokens[start:]).casefold()
    given: List[str] = []
    for token in tokens[:start]:
        given.extend(part.casefold() for part in token.split("-") if part)
    return tuple(given), surname


def looks_glued(raw: str) -> bool:
    """Several names run together by PDF extraction ("LuyuGao XueguangMa"). One
    camel-cased word is an ordinary name (LeCun); every word being one is not."""
    tokens = (raw or "").split()
    return len(tokens) >= 2 and all(re.search(r"[a-z][A-Z]", t) for t in tokens)


def _readable(raw: str) -> str:
    """"Chang, Ming-Wei" reads as "Ming-Wei Chang"; a suffix stays where it is."""
    last, sep, rest = raw.partition(",")
    if not sep or rest.replace(".", "").strip().casefold() in _SUFFIXES:
        return raw
    return " ".join(f"{rest} {last}".split())


def _given_compatible(a: Sequence[str], b: Sequence[str]) -> bool:
    if not a or not b:
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        if (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y)):
            continue
        return False
    return True


def _spelled_out(given: Sequence[str]) -> bool:
    return bool(given) and len(given[0]) > 1


def resolve_authors(names: Iterable[str]) -> Tuple[Dict[str, str], List[dict], List[dict]]:
    """Map raw author strings to one display name each, conservatively.

    Identical names after normalising case, spacing and accents always merge.
    Different forms merge only when all of these hold; a false merge invents a
    broker, so anything else stays apart and is reported:

    * same surname, and given names that agree token by token, an initial
      agreeing with a name it starts ("J. Smith" and "John Smith");
    * every form in the group agrees with every other ("J. Smith" with both
      "John Smith" and "Jane Smith" is ambiguous);
    * at least one form spells out the first name (two sets of initials are no
      evidence of one person);
    * the surname is not in COMMON_SURNAMES.

    Glued names (``looks_glued``) are left out, and the caller reports them.
    Returns ``(raw -> display name, merges, ambiguous)``. Homonyms, two people
    with one printed name, cannot be told apart here and do merge.
    """
    raw_counts = Counter(n.strip() for n in names if n and n.strip() and not looks_glued(n))
    forms: Dict[Tuple[Tuple[str, ...], str], Counter] = defaultdict(Counter)
    for raw, count in raw_counts.items():
        parsed = parse_author(raw)
        if parsed:
            forms[parsed][raw] += count

    by_surname: Dict[str, List[Tuple[Tuple[str, ...], str]]] = defaultdict(list)
    for key in sorted(forms):
        by_surname[key[1]].append(key)

    def display(keys: Sequence[Tuple[Tuple[str, ...], str]]) -> str:
        # The fullest spelling, in mixed case and with its accents if any form
        # has them, then the most used, then alphabetical.
        options = [(sum(len(g) > 1 for g in key[0]), len(key[0]), raw not in (raw.upper(), raw.lower()),
                    raw != _fold(raw), count, raw)
                   for key in keys for raw, count in forms[key].items()]
        best = max(option[:5] for option in options)
        return _readable(min(option[5] for option in options if option[:5] == best))

    groups: List[List[Tuple[Tuple[str, ...], str]]] = []
    ambiguous: List[dict] = []
    for surname, keys in by_surname.items():
        linked = nx.Graph()
        linked.add_nodes_from(keys)
        linked.add_edges_from((a, b) for a, b in combinations(keys, 2) if _given_compatible(a[0], b[0]))
        for component in nx.connected_components(linked):
            members = sorted(component)
            if len(members) == 1:
                groups.append(members)
                continue
            agree = all(_given_compatible(a[0], b[0]) for a, b in combinations(members, 2))
            spelled = any(_spelled_out(k[0]) for k in members)
            if agree and spelled and surname not in COMMON_SURNAMES:
                groups.append(members)
                continue
            reason = ("the surname is too common to merge on an initial" if surname in COMMON_SURNAMES
                      else "no form spells out the first name" if agree
                      else "more than one person could be meant")
            ambiguous.append({"names": sorted(display([k]) for k in members), "reason": reason})
            groups.extend([k] for k in members)

    mapping: Dict[str, str] = {}
    merges: List[dict] = []
    for members in groups:
        name = display(members)
        variants = sorted(raw for key in members for raw in forms[key])
        for raw in variants:
            mapping[raw] = name
        if len(variants) > 1:
            merges.append({
                "name": name, "variants": variants,
                "reason": ("an initial matching the only spelled-out name with this surname" if len(members) > 1
                           else "the same name after normalising case, spacing and accents"),
            })
    merges.sort(key=lambda m: m["name"])
    ambiguous.sort(key=lambda a: a["names"])
    return mapping, merges, ambiguous


# ── builders ────────────────────────────────────────────────────────────

def project_memberships(memberships: Mapping[Hashable, Iterable[Hashable]],
                        max_group_size: Optional[int] = None) -> Tuple[nx.Graph, nx.Graph]:
    """The Revere step: ``group -> members`` to a bipartite graph and its
    one-mode projection.

    The projection's edge ``weight`` counts shared groups and ``sources`` lists
    them, which is the evidence for the tie. A group over ``max_group_size``
    stays in the bipartite graph but makes no ties.
    """
    bipartite = nx.Graph()
    projected = nx.Graph()
    skipped = []
    for group in sorted(memberships, key=str):
        members = sorted(set(memberships[group]), key=str)
        bipartite.add_node(("group", group), bipartite=1)
        for member in members:
            bipartite.add_node(("member", member), bipartite=0)
            bipartite.add_edge(("group", group), ("member", member))
            projected.add_node(member)
        if max_group_size is not None and len(members) > max_group_size:
            skipped.append(group)
            continue
        for a, b in combinations(members, 2):
            if projected.has_edge(a, b):
                projected[a][b]["weight"] += 1
                projected[a][b]["sources"].append(group)
            else:
                projected.add_edge(a, b, weight=1, sources=[group])
    projected.graph["skipped_groups"] = skipped
    return bipartite, projected


def citation_graph(graph_json: dict) -> nx.DiGraph:
    """``build_graph``'s output as a DiGraph: an edge points from citer to cited."""
    G = nx.DiGraph()
    for node in sorted(graph_json.get("nodes") or [], key=lambda n: n["id"]):
        G.add_node(node["id"], label=node.get("label") or node["id"], title=node.get("title"),
                   type=node.get("type"), document_id=node.get("document_id") or (
                       node["id"] if node.get("type") == "paper" else None))
    for edge in sorted(graph_json.get("edges") or [], key=lambda e: (e["source"], e["target"])):
        if edge["source"] in G and edge["target"] in G and edge["source"] != edge["target"]:
            G.add_edge(edge["source"], edge["target"], weight=1, reason=edge.get("reason") or "",
                       reference_id=edge.get("reference_id"))
    return G


def author_graph(papers: Iterable[dict], max_nodes: int = MAX_AUTHOR_NODES) -> Tuple[nx.Graph, nx.Graph]:
    """Authors × papers from ``{"id", "title", "authors"}`` dicts (the paper
    nodes of ``build_graph``), and the co-authorship projection.

    ``graph`` on the projection reports what normalisation merged and declined
    to merge, and which large papers made no ties.
    """
    papers = [p for p in papers if p.get("authors")]
    unparsed = sorted({a for p in papers for a in p["authors"] if looks_glued(a)})
    mapping, merges, ambiguous = resolve_authors(a for p in papers for a in p["authors"])
    memberships: Dict[Hashable, Set[str]] = {
        p["id"]: {mapping[a.strip()] for a in p["authors"] if a and a.strip() in mapping} for p in papers}
    counts = Counter(a for members in memberships.values() for a in members)
    keep = {a for a, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_nodes]}
    B, G = project_memberships({p: m & keep for p, m in memberships.items()}, MAX_AUTHORS_PER_PAPER)
    titles = {p["id"]: p.get("title") or p.get("filename") or p["id"] for p in papers}
    for node in G:
        G.nodes[node].update(label=node, type="author",
                             sources=sorted(p for p, m in memberships.items() if node in m))
    G.graph.update(merges=merges, ambiguous=ambiguous, unparsed=unparsed, source_labels=titles,
                   source_count=len(papers),
                   cap={"limit": max_nodes, "kept": len(keep), "dropped": len(counts) - len(keep)}
                   if len(counts) > len(keep) else None)
    return B, G


def entity_graph(doc_entities: Mapping[str, Iterable[str]], titles: Optional[Mapping[str, str]] = None,
                 max_nodes: int = MAX_ENTITY_NODES,
                 max_doc_frequency: float = CORPUS_MAX_DOC_FREQUENCY,
                 exclude: Iterable[str] = ()) -> nx.Graph:
    """Entities co-mentioned across documents, projected from ``document ->
    entities``.

    ``corpus_filter`` drops entities in one document (they can bridge nothing)
    and those in more than ``max_doc_frequency`` of them (they link everything
    to everything), counted here per document, not per chunk. The rest are
    capped to the ``max_nodes`` most frequent; ``graph`` says what was dropped.
    Headings, venues and anything in ``exclude`` are not concepts and go first.
    """
    excluded = set(exclude)

    def is_concept(entity: str) -> bool:
        return not (entity in _BIBLIOGRAPHIC_ENTITIES or entity in excluded or _NUMBERED_OR_CITATION.match(entity))

    sets = {doc: {e for e in entities if is_concept(e)} for doc, entities in doc_entities.items()}
    df: Counter = Counter(e for entities in sets.values() for e in entities)
    kept = corpus_filter(df, len(sets), max_doc_frequency)
    ranked = sorted(kept, key=lambda e: (-df[e], e))[:max_nodes]
    chosen = set(ranked)
    _, G = project_memberships({doc: entities & chosen for doc, entities in sets.items()})
    for node in G:
        G.nodes[node].update(label=node, type="entity",
                             sources=sorted(d for d, entities in sets.items() if node in entities))
    G.graph.update(
        source_labels=dict(titles or {}), source_count=len(sets),
        cap={"limit": max_nodes, "kept": len(chosen), "dropped": len(kept) - len(chosen)}
        if len(kept) > len(chosen) else None,
        entity_filter={"total": len(df), "in_one_document": sum(1 for c in df.values() if c < 2),
                       "too_common": sum(1 for e, c in df.items() if c >= 2 and e not in kept),
                       "max_doc_frequency": max_doc_frequency},
    )
    return G


# ── metrics ─────────────────────────────────────────────────────────────

def undirected_view(G: nx.Graph) -> nx.Graph:
    """A simple weighted undirected copy with nodes and edges in sorted order, so
    seeded algorithms do not depend on insertion order. A citation in either
    direction is one tie."""
    U = nx.Graph()
    U.add_nodes_from(sorted(G.nodes, key=str))
    ties: Dict[Tuple[Hashable, Hashable], float] = {}
    for a, b, data in G.edges(data=True):
        if a != b:
            ties.setdefault((a, b) if str(a) <= str(b) else (b, a), data.get("weight", 1))
    for (a, b), weight in sorted(ties.items(), key=lambda tie: (str(tie[0][0]), str(tie[0][1]))):
        U.add_edge(a, b, weight=weight)
    return U


def pagerank(G: nx.Graph, alpha: float = 0.85, max_iter: int = 200, tol: float = 1e-9) -> Dict[Hashable, float]:
    """PageRank by power iteration, the formula ``nx.pagerank`` uses without its
    SciPy import. On a DiGraph score flows along edges, so from citer to cited.
    Rank at a node with no out-edges spreads evenly, as in NetworkX."""
    n = G.number_of_nodes()
    if not n:
        return {}
    out = {u: sum(d.get("weight", 1) for d in G.adj[u].values()) for u in G}
    rank = dict.fromkeys(G, 1.0 / n)
    for _ in range(max_iter):
        dangling = sum(rank[u] for u in G if not out[u])
        base = (1.0 - alpha + alpha * dangling) / n
        next_rank = dict.fromkeys(G, base)
        for u in G:
            if out[u]:
                share = alpha * rank[u] / out[u]
                for v, d in G.adj[u].items():
                    next_rank[v] += share * d.get("weight", 1)
        delta = sum(abs(next_rank[u] - rank[u]) for u in G)
        rank = next_rank
        if delta < n * tol:
            break
    return rank


def component_eigenvector(U: nx.Graph) -> Tuple[Dict[Hashable, Optional[float]], List[str]]:
    """Eigenvector centrality per connected component.

    Plain eigenvector centrality is ill-defined on a disconnected graph. Each
    component gets its own, scaled by that component's share of the nodes so a
    two-node island does not outrank the main body. Isolates score 0. A
    component that does not converge is left as None and reported.
    """
    scores: Dict[Hashable, Optional[float]] = {}
    warnings: List[str] = []
    n = U.number_of_nodes()
    for component in nx.connected_components(U):
        if len(component) == 1:
            scores.update(dict.fromkeys(component, 0.0))
            continue
        H = U.subgraph(sorted(component, key=str))
        try:
            values = nx.eigenvector_centrality(H, max_iter=2000, tol=1e-8, weight="weight")
        except nx.PowerIterationFailedConvergence:
            scores.update(dict.fromkeys(component))
            warnings.append(f"Eigenvector centrality did not converge on a component of {len(component)} nodes.")
            continue
        share = len(component) / n
        scores.update({node: value * share for node, value in values.items()})
    return scores, warnings


def betweenness(U: nx.Graph, seed: int = SEED) -> Tuple[Dict[Hashable, float], Optional[int]]:
    """Normalised betweenness over hops, and the sample size if approximate.

    Tie weights are deliberately ignored here: NetworkX reads a weight as a
    distance, which would make the strongest ties look longest, and a count of
    hops is what the bridge evidence can show. It also puts the same four people
    first, in the same order, as the betweenness table in Healy's post.
    """
    n = U.number_of_nodes()
    if n > BETWEENNESS_EXACT_MAX_NODES:
        return nx.betweenness_centrality(U, k=BETWEENNESS_SAMPLES, normalized=True, seed=seed), BETWEENNESS_SAMPLES
    return nx.betweenness_centrality(U, normalized=True), None


def structural_holes(U: nx.Graph) -> Tuple[Dict[Hashable, Optional[float]], Dict[Hashable, Optional[float]]]:
    """Burt's constraint and effective size, equal to ``nx.constraint`` and
    ``nx.effective_size`` with ``weight="weight"`` except that isolates are None
    instead of NaN, since JSON has no NaN.

    Constraint is high when a node's ties all lead to people who are tied to one
    another, low when its ties reach otherwise separate groups. Effective size is
    the number of ties that are not redundant with its other ties.
    """
    share: Dict[Hashable, Dict[Hashable, float]] = {}
    of_max: Dict[Hashable, Dict[Hashable, float]] = {}
    for u in U:
        weights = {v: d.get("weight", 1) for v, d in U.adj[u].items() if v != u}
        total, largest = sum(weights.values()), max(weights.values(), default=0)
        share[u] = {v: w / total for v, w in weights.items()} if total else {}
        of_max[u] = {v: w / largest for v, w in weights.items()} if largest else {}

    constraint: Dict[Hashable, Optional[float]] = {}
    effective: Dict[Hashable, Optional[float]] = {}
    for u in U:
        p = share[u]
        if not p:
            constraint[u] = effective[u] = None
            continue
        c = e = 0.0
        for v in p:
            shared_with_v = share[v]
            of_max_v = of_max[v]
            # Each ratio is looked up, not recomputed, which makes this O(Σd²).
            indirect = redundancy = 0.0
            for w, p_uw in p.items():
                if w in shared_with_v:
                    indirect += p_uw * share[w][v]
                    redundancy += p_uw * of_max_v[w]
            c += (p[v] + indirect) ** 2
            e += 1.0 - redundancy
        constraint[u], effective[u] = c, e
    return constraint, effective


def communities(U: nx.Graph, seed: int = SEED) -> Tuple[List[List[Hashable]], float]:
    """Louvain communities of at least two nodes, largest first, and the
    modularity of the whole partition. Runs on integer labels because a set of
    strings iterates differently in each process, and Louvain would inherit that."""
    if not U.number_of_edges():
        return [], 0.0
    order = sorted(U.nodes, key=str)
    index = {node: i for i, node in enumerate(order)}
    H = nx.Graph()
    H.add_nodes_from(range(len(order)))
    H.add_edges_from((index[a], index[b], {"weight": d.get("weight", 1)}) for a, b, d in U.edges(data=True))
    parts = nx.community.louvain_communities(H, weight="weight", seed=seed)
    score = nx.community.modularity(H, parts, weight="weight")
    groups = [sorted((order[i] for i in part), key=str) for part in parts if len(part) > 1]
    groups.sort(key=lambda g: (-len(g), str(g[0])))
    return groups, score


# ── community labels ────────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]+")


def _title_terms(title: str) -> Dict[str, str]:
    """Words and adjacent word pairs of a title: lowercase -> as printed. A
    stopword breaks a pair, so "retrieval for question" never forms one."""
    terms: Dict[str, str] = {}
    run: List[str] = []

    def flush():
        for word in run:
            if word.lower() not in _GENERIC_TITLE_TERMS:
                terms.setdefault(word.lower(), word)
        for a, b in zip(run, run[1:]):
            if a.lower() not in _GENERIC_TITLE_TERMS and b.lower() not in _GENERIC_TITLE_TERMS:
                terms.setdefault(f"{a} {b}".lower(), f"{a} {b}")
        run.clear()

    for word in _WORD.findall(title or ""):
        if word.lower() in _STOPWORDS or len(word) < 3:
            flush()
        else:
            run.append(word)
    flush()
    return terms


def label_communities(groups: Sequence[Sequence[Hashable]],
                      node_texts: Mapping[Hashable, Iterable[str]]) -> List[dict]:
    """A deterministic label per community from its most distinctive title
    terms: the share of its nodes using a term minus the share of other nodes
    using it. No model writes these. Labels stay distinct across communities;
    one with no usable terms is "Group N"."""
    node_terms: Dict[Hashable, Dict[str, str]] = {}
    for node, texts in node_texts.items():
        merged: Dict[str, str] = {}
        for text in texts:
            for key, shown in _title_terms(text).items():
                merged.setdefault(key, shown)
        node_terms[node] = merged

    total_nodes = sum(len(g) for g in groups)
    everywhere: Counter = Counter(k for g in groups for n in g for k in node_terms.get(n, {}))
    used: Set[str] = set()
    labelled = []
    for number, group in enumerate(groups, 1):
        inside: Counter = Counter(k for n in group for k in node_terms.get(n, {}))
        shown: Dict[str, str] = {}
        for n in group:
            for k, s in node_terms.get(n, {}).items():
                shown.setdefault(k, s)
        rest = max(total_nodes - len(group), 1)
        floor = 2 if len(group) > 2 else 1
        scored = sorted(
            ((count / len(group) - (everywhere[k] - count) / rest, k) for k, count in inside.items() if count >= floor),
            key=lambda item: (-item[0], -(" " in item[1]), item[1]),
        )
        terms: List[str] = []
        for _, key in scored:
            if any(key in chosen or chosen in key for chosen in terms):
                continue
            terms.append(key)
            if len(terms) == 5:
                break
        label_key = next((t for t in terms if t not in used), None)
        if label_key:
            used.add(label_key)
            text = shown[label_key]
            label = text[0].upper() + text[1:]
        else:
            label = f"Group {number}"
        labelled.append({"label": label, "terms": [shown[t] for t in terms]})
    return labelled


def _label_from_members(groups: Sequence[Sequence[Hashable]], U: nx.Graph, labels: Mapping[Hashable, str]) -> List[dict]:
    """For entity graphs the members are the terms: each community is named by
    its best-connected entity not already used as a label."""
    used: Set[str] = set()
    out = []
    for group in groups:
        ranked = sorted(group, key=lambda n: (-U.degree(n, weight="weight"), str(n)))
        names = [labels.get(n, str(n)) for n in ranked]
        label = next((name for name in names if name not in used and len(name) > 2),
                     next((name for name in names if name not in used), names[0]))
        used.add(label)
        out.append({"label": label, "terms": names[:5]})
    return out


# ── reliability ─────────────────────────────────────────────────────────

def reliability_note(nodes: int, edges: int, largest_component: int, sources: Optional[int] = None,
                     modularity: Optional[float] = None) -> dict:
    """Whether the graph is big enough to read, in words. ``sources`` is the
    number of papers behind it, when there are any."""
    thresholds = {"min_nodes": MIN_RELIABLE_NODES, "min_edges": MIN_RELIABLE_EDGES,
                  "min_sources": MIN_RELIABLE_SOURCES, "min_largest_component_share": MIN_LARGEST_COMPONENT_SHARE,
                  "min_meaningful_modularity": MIN_MEANINGFUL_MODULARITY}
    size = f"{nodes} node{'' if nodes == 1 else 's'} and {edges} link{'' if edges == 1 else 's'}"
    if sources is not None:
        size += f" from {sources} source{'' if sources == 1 else 's'}"
    if not nodes or not edges:
        level, note = "empty", (f"{size[0].upper()}{size[1:]}: nothing connects here, so there is nothing "
                                "to measure yet.")
    elif (nodes < MIN_RELIABLE_NODES or edges < MIN_RELIABLE_EDGES
          or (sources is not None and sources < MIN_RELIABLE_SOURCES)):
        level, note = "too_small", (
            f"{size[0].upper()}{size[1:]} is too small for centrality to mean much: one added or removed link "
            "can reorder the top. Read these as a description of these links, not a ranking of what matters.")
    elif largest_component / nodes < MIN_LARGEST_COMPONENT_SHARE:
        level, note = "fragmented", (
            f"Only {largest_component} of {nodes} nodes are in the largest connected part, so scores mostly "
            "compare separate islands. Compare nodes within one group rather than across groups.")
    else:
        level, note = "ok", (
            f"{size[0].upper()}{size[1:]} is enough for a rough structural reading. It still describes links "
            "in the metadata, not the quality of the work.")
    weak_groups = bool(edges) and modularity is not None and modularity < MIN_MEANINGFUL_MODULARITY
    if weak_groups:
        note += (f" The groups are also weak (modularity {modularity:.2f}, below {MIN_MEANINGFUL_MODULARITY}), "
                 "so treat \"connects\" as tentative.")
    return {"level": level, "reliable": level == "ok" and not weak_groups, "weak_groups": weak_groups,
            "note": note, "thresholds": thresholds}


# ── the analysis ────────────────────────────────────────────────────────

DEFINITIONS = {
    "citation": {
        "pagerank": "Importance passed along citations: a work scores high when it is cited by works that are "
                    "themselves cited. Influence flows from the citing paper to the cited one.",
        "betweenness": "How often this work lies on the shortest chain of citations between two others, in either "
                       "direction, as a share of all such chains.",
    },
    "authors": {
        "pagerank": "Importance passed along co-authorship: high when your co-authors are themselves well connected, "
                    "weighted by papers shared.",
        "betweenness": "How often this author lies on the shortest chain of co-authors between two others, as a "
                       "share of all such chains.",
    },
    "entities": {
        "pagerank": "Importance passed along co-mentions: high when a concept appears with concepts that are "
                    "themselves widely co-mentioned, weighted by documents shared.",
        "betweenness": "How often this concept lies on the shortest chain of co-mentions between two others, as a "
                       "share of all such chains.",
    },
    "membership": {
        "pagerank": "Importance passed along shared groups, weighted by how many groups two members share.",
        "betweenness": "How often this member lies on the shortest chain of shared groups between two others, as a "
                       "share of all such chains.",
    },
}
_SHARED_DEFINITIONS = {
    "eigenvector": "High when this node's neighbours are themselves well connected, computed separately for each "
                   "connected part and scaled by the part's share of the graph. Ignores citation direction.",
    "constraint": "Burt's constraint: high when a node's ties all lead to neighbours tied to each other, low when "
                  "they reach separate groups. Lower means more of a broker. Blank for a node with no ties.",
    "effective_size": "Burt's effective size: how many of a node's ties are not redundant with its other ties. "
                      "Blank for a node with no ties.",
    "community": "A group found by Louvain, tied more within itself than to the rest. Named after its most "
                 "distinctive terms by counting, not by a model.",
    "modularity": "How much more the groups are tied within themselves than chance would give. Roughly 0.3 and "
                  "up suggests real group structure, and small graphs can score high by chance.",
}


_IRREGULAR_PLURALS = {"person": "people"}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun if count == 1 else _IRREGULAR_PLURALS.get(noun, noun + 's')}"


def _join_names(names: Sequence[str]) -> str:
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    return f"{names[0]}, {names[1]} and {len(names) - 2} more groups"


def _counts_phrase(verb: str, noun: str, counts: Sequence[Tuple[str, int]]) -> str:
    """"cited by 3 papers in each", or "cited by 3 papers in A and 1 in B"."""
    if len(counts) == 1:
        return f"{verb} {_plural(counts[0][1], noun)} in {counts[0][0]}"
    if len({c for _, c in counts}) == 1:
        return f"{verb} {_plural(counts[0][1], noun)} in each"
    head, *rest = counts[:3]
    tail = " and ".join(f"{count} in {name}" for name, count in rest)
    return f"{verb} {_plural(head[1], noun)} in {head[0]} and {tail}"


def _broker_sentence(kind: str, groups: Sequence[dict]) -> str:
    """One plain sentence, built only from the counts in ``groups``."""
    connects = f"Connects {_join_names([g['label'] for g in groups])}"
    if kind == "citation":
        cited_by = [(g["label"], g["cited_by"]) for g in groups if g["cited_by"]]
        cites = [(g["label"], g["cites"]) for g in groups if g["cites"]]
        parts = []
        if cited_by:
            parts.append(_counts_phrase("cited by", "paper", cited_by))
        if cites:
            parts.append(_counts_phrase("cites", "work", cites))
        return f"{connects}: {'; '.join(parts)}"
    verb, noun = {"authors": ("co-authored with", "person"), "entities": ("mentioned alongside", "concept")}.get(
        kind, ("linked to", "member"))
    return f"{connects}: {_counts_phrase(verb, noun, [(g['label'], g['count']) for g in groups])}"


def _source_refs(ids: Iterable[Hashable], labels: Mapping[Hashable, str]) -> List[dict]:
    return [{"document_id": s, "title": labels.get(s) or str(s)} for s in ids]


def _edge_evidence(G: nx.Graph, node: Hashable, neighbour: Hashable, labels: Mapping[Hashable, str],
                   source_labels: Mapping[Hashable, str]) -> dict:
    evidence: Dict[str, Any] = {"node": neighbour, "label": labels.get(neighbour, str(neighbour))}
    if G.is_directed():
        if G.has_edge(neighbour, node):
            evidence["cited_by"] = {"reason": G[neighbour][node].get("reason") or "",
                                    "reference_id": G[neighbour][node].get("reference_id")}
        if G.has_edge(node, neighbour):
            evidence["cites"] = {"reason": G[node][neighbour].get("reason") or "",
                                 "reference_id": G[node][neighbour].get("reference_id")}
    else:
        sources = G[node][neighbour].get("sources") or []
        evidence["weight"] = G[node][neighbour].get("weight", 1)
        evidence["sources"] = _source_refs(sources[:MAX_EVIDENCE_SOURCES], source_labels)
    return evidence


def _neighbour_groups(G: nx.Graph, U: nx.Graph, node: Hashable, member_of: Mapping[Hashable, int],
                      community_rows: Sequence[dict], labels: Mapping[Hashable, str],
                      source_labels: Mapping[Hashable, str]) -> List[dict]:
    by_group: Dict[int, List[Hashable]] = defaultdict(list)
    for neighbour in U.adj[node]:
        if neighbour in member_of:
            by_group[member_of[neighbour]].append(neighbour)
    groups = []
    for cid, neighbours in by_group.items():
        neighbours.sort(key=lambda n: (-U[node][n].get("weight", 1), str(labels.get(n, n))))
        row = {"community": cid, "label": community_rows[cid]["label"], "count": len(neighbours),
               "own": member_of.get(node) == cid,
               "edges": [_edge_evidence(G, node, n, labels, source_labels) for n in neighbours[:MAX_EVIDENCE_EDGES]]}
        if G.is_directed():
            row["cited_by"] = sum(1 for n in neighbours if G.has_edge(n, node))
            row["cites"] = sum(1 for n in neighbours if G.has_edge(node, n))
        groups.append(row)
    groups.sort(key=lambda g: (-g["count"], g["community"]))
    return groups


def _ties_sentence(kind: str, node: Hashable, G: nx.Graph, U: nx.Graph) -> str:
    """For a key node that bridges nothing: what it does have."""
    degree = U.degree(node)
    if kind == "citation":
        return f"Cited by {G.in_degree(node)} here; cites {G.out_degree(node)} here"
    sources = G.nodes[node].get("sources") or []
    if kind == "authors":
        return f"Co-authored with {_plural(degree, 'person')} across {_plural(len(sources), 'paper')}"
    if kind == "entities":
        return f"In {_plural(len(sources), 'document')}, alongside {_plural(degree, 'concept')}"
    return f"Linked to {_plural(degree, 'member')}"


def analyze(G: nx.Graph, kind: str = "membership", seed: int = SEED,
            node_texts: Optional[Mapping[Hashable, Iterable[str]]] = None,
            sources: Optional[int] = None) -> dict:
    """Every metric, the communities, the brokers and the reliability note for
    one graph. Never raises on an empty, single-node or disconnected graph."""
    U = undirected_view(G)
    n, m = U.number_of_nodes(), U.number_of_edges()
    labels = {node: G.nodes[node].get("label", str(node)) for node in G}
    source_labels = G.graph.get("source_labels") or {}
    if sources is None:
        sources = G.graph.get("source_count")

    components = sorted((len(c) for c in nx.connected_components(U)), reverse=True)
    largest = components[0] if components else 0
    page = pagerank(G)
    eigen, warnings = component_eigenvector(U)
    between, samples = betweenness(U, seed)
    constraint, effective = structural_holes(U)
    groups, modularity = communities(U, seed)

    if kind == "entities":
        community_rows = _label_from_members(groups, U, labels)
    else:
        community_rows = label_communities(groups, node_texts or {})
    member_of: Dict[Hashable, int] = {}
    for cid, group in enumerate(groups):
        community_rows[cid].update(id=cid, size=len(group))
        for node in group:
            member_of[node] = cid
    for cid, group in enumerate(groups):
        top = sorted(group, key=lambda x: (-between[x], -U.degree(x), str(labels.get(x, x))))[:3]
        community_rows[cid]["top_members"] = [{"id": x, "label": labels.get(x, str(x))} for x in top]

    def rounded(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    rows = []
    for node in sorted(U.nodes, key=str):
        row = {
            "id": node, "label": labels.get(node, str(node)), "type": G.nodes[node].get("type"),
            "degree": U.degree(node), "weighted_degree": U.degree(node, weight="weight"),
            "pagerank": rounded(page[node]), "eigenvector": rounded(eigen[node]),
            "betweenness": rounded(between[node]), "constraint": rounded(constraint[node]),
            "effective_size": rounded(effective[node]), "community": member_of.get(node),
        }
        if G.is_directed():
            row.update(in_degree=G.in_degree(node), out_degree=G.out_degree(node),
                       document_id=G.nodes[node].get("document_id"))
        elif G.nodes[node].get("sources"):
            row["sources"] = _source_refs(G.nodes[node]["sources"][:MAX_EVIDENCE_SOURCES], source_labels)
            row["source_count"] = len(G.nodes[node]["sources"])
        rows.append(row)

    ranked = sorted(U.nodes, key=lambda x: (-between[x], -U.degree(x, weight="weight"), str(labels.get(x, x))))

    brokers = []
    for node in ranked:
        if len(brokers) >= TOP_BROKERS or between[node] <= 0:
            break
        neighbour_groups = _neighbour_groups(G, U, node, member_of, community_rows, labels, source_labels)
        if len(neighbour_groups) < 2:
            continue
        brokers.append({
            "id": node, "label": labels.get(node, str(node)), "betweenness": rounded(between[node]),
            "constraint": rounded(constraint[node]), "community": member_of.get(node),
            "document_id": G.nodes[node].get("document_id"),
            "connects": [g["community"] for g in neighbour_groups],
            "sentence": _broker_sentence(kind, neighbour_groups),
            "bridges": neighbour_groups,
        })
    broker_by_id = {b["id"]: b for b in brokers}

    key_nodes = []
    for node in ranked:
        if len(key_nodes) >= TOP_KEY_NODES or not U.degree(node):
            break
        broker = broker_by_id.get(node)
        key_nodes.append({
            "id": node, "label": labels.get(node, str(node)), "betweenness": rounded(between[node]),
            "is_broker": broker is not None, "community": member_of.get(node),
            "sentence": broker["sentence"] if broker else _ties_sentence(kind, node, G, U),
            "sources": _source_refs((G.nodes[node].get("sources") or [])[:MAX_EVIDENCE_SOURCES], source_labels),
            "source_count": len(G.nodes[node].get("sources") or []),
            "bridges": broker["bridges"] if broker else _neighbour_groups(
                G, U, node, member_of, community_rows, labels, source_labels),
        })

    stats: Dict[str, Any] = {
        "nodes": n, "edges": m, "components": len(components), "largest_component": largest,
        "isolates": sum(1 for c in components if c == 1), "density": round(nx.density(U), 6) if n > 1 else 0.0,
        "directed": G.is_directed(), "sources": sources, "seed": seed,
        "communities": len(groups), "modularity": round(modularity, 6),
        "betweenness_approximate": samples is not None, "betweenness_samples": samples,
        "capped": G.graph.get("cap"),
    }
    if G.graph.get("skipped_groups"):
        stats["skipped_large_sources"] = len(G.graph["skipped_groups"])
    if G.graph.get("entity_filter"):
        stats["entity_filter"] = G.graph["entity_filter"]

    return {
        "graph": kind, "stats": stats,
        "reliability": reliability_note(n, m, largest, sources, modularity),
        "definitions": {**DEFINITIONS.get(kind, DEFINITIONS["membership"]), **_SHARED_DEFINITIONS},
        "nodes": rows, "communities": community_rows, "brokers": brokers, "key_nodes": key_nodes,
        "merges": G.graph.get("merges") or [], "ambiguous": G.graph.get("ambiguous") or [],
        "unparsed_authors": G.graph.get("unparsed") or [],
        "warnings": warnings,
    }


# ── a collection's network, cached ──────────────────────────────────────

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def documents_digest(*id_groups: Iterable[str]) -> str:
    """A short fingerprint of exactly which documents are in view and hidden."""
    digest = hashlib.sha1()
    for ids in id_groups:
        digest.update("\n".join(sorted(ids)).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def cached(key: tuple, compute: Callable[[], dict]) -> dict:
    """``compute()`` once per key. A result is never mutated once cached, so
    concurrent readers can share it; a race only computes it twice."""
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return {**hit, "cached": True}
    result = compute()
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)
    return {**result, "cached": False}


def citation_network(graph_json: dict, seed: int = SEED) -> dict:
    G = citation_graph(graph_json)
    texts = {n: [G.nodes[n]["title"]] for n in G if G.nodes[n].get("title")}
    stats = graph_json.get("stats") or {}
    return analyze(G, "citation", seed=seed, node_texts=texts, sources=stats.get("papers"))


def author_network(graph_json: dict, seed: int = SEED) -> dict:
    papers = [node for node in graph_json.get("nodes") or [] if node.get("type") == "paper"]
    _, G = author_graph(papers)
    labels = G.graph["source_labels"]
    texts = {n: [labels[s] for s in G.nodes[n]["sources"]] for n in G}
    return analyze(G, "authors", seed=seed, node_texts=texts, sources=len(papers))


def author_name_forms(authors: Iterable[str]) -> Set[str]:
    """The stored entity forms of these authors: "Kelvin Guu" as "kelvin guu" and
    "guu". These are the people the collection knows are people; a cited name
    the library has no record of can still reach the concept list."""
    forms: Set[str] = set()
    for author in authors:
        if looks_glued(author):
            continue
        forms.add(normalize_entity(_readable(author)))
        parsed = parse_author(author)
        if parsed:
            forms.add(normalize_entity(parsed[1]))
    return forms


def load_document_entities(metadata_store, papers: Sequence[dict]) -> Dict[str, Set[str]]:
    """Each paper's entities, leaving out its reference list: a bibliography
    mentions people and venues, not what the paper is about. A book prints a
    reference list per chapter, so the first one says nothing about where the
    book ends, and a book's are kept."""
    articles = [p["id"] for p in papers if p.get("kind") != "book"]
    boundaries = metadata_store.first_chunk_matching(articles, ("references", "bibliography", "literature cited",
                                                                "works cited", "cited literature"), _REF_HEAD_RE)
    return metadata_store.entities_by_document([p["id"] for p in papers], stop_at=boundaries)


def entity_network(doc_entities: Mapping[str, Iterable[str]], titles: Mapping[str, str], seed: int = SEED,
                   exclude: Iterable[str] = ()) -> dict:
    G = entity_graph(doc_entities, titles, exclude=exclude)
    return analyze(G, "entities", seed=seed, sources=len(doc_entities))
