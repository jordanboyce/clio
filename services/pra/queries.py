"""Read-only PRA queries shared by the REST API (api/pra.py) and the MCP
tools (services/mcp_server.py), so the two cannot drift.

Callers resolve access first (collection visibility, a held or quarantined
document); everything here takes the document's metadata row and its parsed
``PraModel`` and returns plain JSON-ready dicts. Errors are ``PraQueryError``
subclasses carrying the HTTP status each surface maps them to.

Numbers are a screening estimate from the cut sets (``NOTICE``); the PRA
tool's own quantification governs.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.pra import analysis
from services.pra.model import PraModel

logger = logging.getLogger(__name__)

PRA_FORMAT_PREFIX = "pra-"

NOTICE = (
    "Screening estimate computed by Clio from the model's cut sets with point values (rare-event "
    "sum and minimal cut set upper bound). The PRA tool's own quantification governs; check any "
    "number that matters there."
)

# Clio's cut set generation, run on request when a model carries no list.
COMPUTE_TIME_BUDGET_SECONDS = 20.0
COMPUTE_MAX_CUT_SETS = analysis.DEFAULT_MAX_CUT_SETS
# At most this many generations at once; the rest are told to retry.
COMPUTE_SLOTS = 2
COMPUTE_SLOT_WAIT_SECONDS = 2.0
COMPUTE_RETRY_AFTER_SECONDS = 10

FAULT_TREE_MAX_NODES = 2000
EVENT_USAGE_LIMIT = 200
WARNINGS_LIMIT = 50
MISSING_EVENTS_LIMIT = 50

DEFINITIONS = {
    "probability": "Product of the cut set's event probabilities; '/X' counts 1 - p(X).",
    "share": "The cut set's probability over the rare-event total.",
    "rare_event": "Sum of the cut set probabilities.",
    "mcub": "Minimal cut set upper bound, 1 - prod(1 - P).",
    "fussell_vesely": "(F - F[p=0]) / F: the fraction of the total involving the event.",
    "raw": "F[p=1] / F: risk achievement worth.",
    "rrw": "F / F[p=0]: risk reduction worth; null when unbounded (the event is in every cut set).",
    "birnbaum": "F[p=1] - F[p=0]: the sensitivity of F to the event's probability.",
}


class PraQueryError(Exception):
    status_code = 400


class PraNotFound(PraQueryError):
    status_code = 404


class PraInvalid(PraQueryError):
    status_code = 422


class PraGuardStopped(PraQueryError):
    """A computation hit a guard before producing anything usable."""
    status_code = 409


class PraBusy(PraQueryError):
    status_code = 429
    retry_after = COMPUTE_RETRY_AFTER_SECONDS


# ── documents and models ───────────────────────────────────────────────────

def is_pra_document(doc_info: Optional[Dict[str, Any]]) -> bool:
    return bool(doc_info) and str(doc_info.get("source_format") or "").lower().startswith(PRA_FORMAT_PREFIX)


def _index_dir(indexer: Any) -> Path:
    return Path(indexer.vector_store.index_dir)


def load_model(indexer: Any, document_id: str) -> Optional[PraModel]:
    """The parsed model: through the ingest module when it is installed
    (it re-parses the source when the stored file is missing), else the
    stored JSON beside the index."""
    try:
        from services.pra.ingest import model_for_document
    except ModuleNotFoundError as e:
        if e.name not in ("services.pra.ingest",):
            raise
        model_for_document = None
    if model_for_document is not None:
        return model_for_document(indexer, document_id)
    from services.pra import store

    return store.load_model(_index_dir(indexer), document_id)


_MODEL_CACHE_SIZE = 8
_model_cache: "OrderedDict[Tuple[Any, ...], PraModel]" = OrderedDict()
_model_lock = threading.Lock()


def _model_key(indexer: Any, doc_info: Dict[str, Any]) -> Tuple[Any, ...]:
    document_id = doc_info.get("document_id")
    stored = _index_dir(indexer) / "pra" / f"{document_id}.json"
    try:
        mtime = stored.stat().st_mtime_ns
    except (OSError, ValueError):
        mtime = None
    return (str(_index_dir(indexer)), document_id, doc_info.get("content_hash"),
            doc_info.get("upload_timestamp"), mtime)


def model_for(indexer: Any, doc_info: Dict[str, Any]) -> PraModel:
    """The document's model, cached while its row and stored file are unchanged."""
    key = _model_key(indexer, doc_info)
    with _model_lock:
        model = _model_cache.get(key)
        if model is not None:
            _model_cache.move_to_end(key)
            return model
    model = load_model(indexer, doc_info["document_id"])
    if model is None:
        raise PraNotFound(f"Document '{doc_info.get('filename')}' has no parsed PRA model; re-index it")
    with _model_lock:
        _model_cache[key] = model
        while len(_model_cache) > _MODEL_CACHE_SIZE:
            _model_cache.popitem(last=False)
    return model


def _warnings(items: Iterable[str]) -> Dict[str, Any]:
    items = list(items)
    out: Dict[str, Any] = {"warnings": items[:WARNINGS_LIMIT]}
    if len(items) > WARNINGS_LIMIT:
        out["warnings_total"] = len(items)
    return out


def list_models(indexer: Any, docs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The PRA documents among ``docs`` (already filtered for visibility)."""
    out = []
    for doc in docs:
        if not is_pra_document(doc):
            continue
        entry: Dict[str, Any] = {
            "document_id": doc.get("document_id"),
            "filename": doc.get("filename"),
            "source_format": doc.get("source_format"),
        }
        try:
            model = model_for(indexer, doc)
        except PraNotFound:
            entry.update(name=None, counts=None,
                         warnings=["The parsed model is not available; re-index the document."])
        except Exception as e:  # noqa: BLE001 - one bad model must not hide the others
            logger.warning("PRA model for %s could not be loaded: %s", doc.get("document_id"), e)
            entry.update(name=None, counts=None, warnings=["The parsed model could not be read."])
        else:
            entry.update(name=model.name, counts=model.counts(), **_warnings(model.warnings))
        out.append(entry)
    out.sort(key=lambda e: (str(e.get("filename") or "").lower(), str(e.get("document_id"))))
    return out


def _top_to_tree(model: PraModel) -> Dict[str, str]:
    return {ft.top: name for name, ft in model.fault_trees.items() if ft.top}


def _sequences(model: PraModel):
    for et in model.event_trees.values():
        for seq in et.sequences:
            yield et, seq


def model_summary(doc_info: Dict[str, Any], model: PraModel) -> Dict[str, Any]:
    lists = {c.context: c for c in model.cut_sets}
    return {
        "document_id": doc_info.get("document_id"),
        "filename": doc_info.get("filename"),
        "source_format": doc_info.get("source_format"),
        "name": model.name,
        "model_format": model.source_format,
        "description": model.description,
        "source_files": model.source_files,
        "counts": model.counts(),
        "fault_trees": [
            {"name": name, "top": ft.top, "description": ft.description, "gate_count": len(ft.gates),
             "has_cut_sets": name in lists}
            for name, ft in sorted(model.fault_trees.items())
        ],
        "event_trees": [
            {"name": name, "initiating_event": et.initiating_event, "description": et.description,
             "functional_events": et.functional_events, "sequences": len(et.sequences)}
            for name, et in sorted(model.event_trees.items())
        ],
        "end_states": [{"name": n, "description": d} for n, d in sorted(model.end_states.items())],
        "cut_set_lists": [
            {"context": c.context, "kind": c.kind, "event_tree": c.event_tree, "cut_sets": len(c.cut_sets),
             "source": c.source, "truncation": c.truncation}
            for c in model.cut_sets
        ],
        **_warnings(model.warnings),
        "notice": NOTICE,
    }


# ── structure ──────────────────────────────────────────────────────────────

def fault_tree_view(model: PraModel, tree: str, depth: int = 3,
                    max_nodes: int = FAULT_TREE_MAX_NODES) -> Dict[str, Any]:
    """The tree as nested nodes, ``depth`` gate levels deep.

    A gate past the depth (or past ``max_nodes`` nodes in all) carries
    ``"more": true`` and its input count instead of children; a gate that is
    another fault tree's top is shown as a transfer to that tree; a gate met
    again below itself is marked ``"cycle": true`` and not expanded.
    """
    if depth < 1:
        raise PraInvalid("depth must be at least 1")
    ft = model.fault_trees.get(tree)
    if ft is not None:
        root = ft.top
    elif tree in model.gates:
        root = tree
    else:
        raise PraNotFound(f"No fault tree or gate named '{tree}' in this model")
    tops = _top_to_tree(model)
    emitted = [0]
    truncated = [False]

    def node(name: str, level: int, ancestors: frozenset) -> Dict[str, Any]:
        emitted[0] += 1
        bare, neg = analysis.split_name(name)
        out: Dict[str, Any] = {"name": bare}
        if neg:
            out["complemented"] = True
        gate = model.gates.get(bare)
        if gate is not None:
            other = tops.get(bare)
            if gate.type == "transfer":
                target = analysis.split_name(gate.inputs[0])[0] if gate.inputs else gate.tree
                return {"kind": "transfer", **out, "tree": target}
            if level > 0 and other is not None and other != tree and gate.tree != tree and bare != root:
                return {"kind": "transfer", **out, "tree": other}
            out = {"kind": "gate", **out, "type": gate.type, "description": gate.description}
            if gate.k is not None:
                out["k"] = gate.k
            if bare in ancestors:
                out["cycle"] = True
                return out
            if level >= depth or emitted[0] >= max_nodes:
                if emitted[0] >= max_nodes:
                    truncated[0] = True
                out["more"] = True
                out["input_count"] = len(gate.inputs)
                return out
            out["children"] = [node(i, level + 1, ancestors | {bare}) for i in gate.inputs]
            return out
        be = model.basic_events.get(bare)
        if be is not None:
            return {"kind": "basic_event", **out, "description": be.description, "probability": be.probability}
        he = model.house_events.get(bare)
        if he is not None:
            return {"kind": "house_event", **out, "description": he.description, "state": he.state}
        if bare in model.fault_trees:
            return {"kind": "transfer", **out, "tree": bare}
        ie = model.initiating_events.get(bare)
        if ie is not None:
            return {"kind": "initiating_event", **out, "description": ie.description, "frequency": ie.probability}
        return {"kind": "undefined", **out}

    structure = node(root, 0, frozenset()) if root else None
    return {
        "name": tree,
        "top": root,
        "description": ft.description if ft is not None else (model.gates[tree].description or ""),
        "gate_count": len(ft.gates) if ft is not None else None,
        "has_cut_sets": model.cut_sets_for(tree) is not None,
        "depth": depth,
        "truncated": truncated[0],
        "structure": structure,
    }


def event_tree_view(model: PraModel, name: str) -> Dict[str, Any]:
    et = model.event_trees.get(name)
    if et is None:
        raise PraNotFound(f"No event tree named '{name}' in this model")
    ie = model.initiating_events.get(et.initiating_event or "")
    return {
        "name": et.name,
        "description": et.description,
        "initiating_event": et.initiating_event,
        "initiating_event_frequency": ie.probability if ie else None,
        "functional_events": [
            {"name": fe, "fault_tree": fe if fe in model.fault_trees else None,
             "description": model.fault_trees[fe].description if fe in model.fault_trees else ""}
            for fe in et.functional_events
        ],
        "sequences": [
            {"name": s.name, "end_state": s.end_state, "description": s.description,
             "path": [{"functional_event": p.functional_event, "state": p.state} for p in s.path],
             "logic": s.logic, "frequency": s.frequency, "has_cut_sets": model.cut_sets_for(s.name) is not None}
            for s in et.sequences
        ],
    }


def event_view(model: PraModel, name: str) -> Dict[str, Any]:
    """One event's data, where the logic uses it, and the stored cut sets containing it."""
    bare, _ = analysis.split_name(name)
    out: Dict[str, Any] = {"name": bare}
    if bare in model.basic_events:
        be = model.basic_events[bare]
        out.update(kind="basic_event", description=be.description, probability=be.probability,
                   failure_rate=be.failure_rate, mission_time=be.mission_time, calc_type=be.calc_type,
                   uncertainty=be.uncertainty, attributes=be.attributes)
    elif bare in model.house_events:
        he = model.house_events[bare]
        out.update(kind="house_event", description=he.description, state=he.state)
    elif bare in model.initiating_events:
        ie = model.initiating_events[bare]
        out.update(kind="initiating_event", description=ie.description, frequency=ie.probability)
    elif bare in model.gates:
        g = model.gates[bare]
        out.update(kind="gate", description=g.description, type=g.type, k=g.k, tree=g.tree, inputs=g.inputs)
    elif bare in model.fault_trees:
        ft = model.fault_trees[bare]
        out.update(kind="fault_tree", description=ft.description, top=ft.top)
    else:
        raise PraNotFound(f"No event named '{bare}' in this model")

    used = []
    for g in model.gates.values():
        for i in g.inputs:
            ib, ineg = analysis.split_name(i)
            if ib == bare:
                used.append({"gate": g.name, "tree": g.tree, "type": g.type, "complemented": ineg})
    used.sort(key=lambda u: (str(u["tree"] or ""), u["gate"]))
    trees = sorted({u["tree"] for u in used if u["tree"]})

    sequences = []
    event_trees = set()
    for et, seq in _sequences(model):
        if et.initiating_event == bare:
            event_trees.add(et.name)
        for term in seq.logic:
            tb, tneg = analysis.split_name(term)
            if tb == bare:
                sequences.append({"event_tree": et.name, "sequence": seq.name, "end_state": seq.end_state,
                                  "branch": "success" if tneg else "failure"})
    for et in model.event_trees.values():
        if bare in et.functional_events:
            event_trees.add(et.name)

    contexts = []
    total = 0
    for c in model.cut_sets:
        n = sum(1 for cs in c.cut_sets if any(analysis.split_name(e)[0] == bare for e in cs))
        if n:
            total += n
            contexts.append({"context": c.context, "kind": c.kind, "cut_sets": n})
    contexts.sort(key=lambda c: -c["cut_sets"])
    out.update(
        used_in_gates=used[:EVENT_USAGE_LIMIT],
        used_in_gates_total=len(used),
        fault_trees=trees,
        event_trees=sorted(event_trees),
        sequences=sequences[:EVENT_USAGE_LIMIT],
        sequences_total=len(sequences),
        stored_cut_sets={"total": total, "by_context": contexts[:EVENT_USAGE_LIMIT]},
    )
    return out


# ── cut sets and importance ────────────────────────────────────────────────

_slots = threading.BoundedSemaphore(COMPUTE_SLOTS)
_COMPUTED_CACHE_SIZE = 16
_computed: "OrderedDict[Tuple[Any, ...], Tuple[PraModel, analysis.GenerationResult]]" = OrderedDict()
_computed_lock = threading.Lock()


def _known_context(model: PraModel, context: str) -> Optional[str]:
    if context in model.fault_trees:
        return "fault_tree"
    if context in model.gates:
        return "gate"
    if any(seq.name == context for _, seq in _sequences(model)):
        return "sequence"
    if context in model.end_states:
        return "end_state"
    return None


def _generate(model: PraModel, context: str, truncation: float, max_order: Optional[int],
              success_terms: str) -> analysis.GenerationResult:
    key = (id(model), context, truncation, max_order, success_terms)
    with _computed_lock:
        hit = _computed.get(key)
        if hit is not None and hit[0] is model:
            _computed.move_to_end(key)
            return hit[1]
    if not _slots.acquire(timeout=COMPUTE_SLOT_WAIT_SECONDS):
        raise PraBusy("Other cut set computations are running; retry shortly")
    try:
        result = analysis.generate_cut_sets(
            model, context, truncation=truncation, max_order=max_order, max_cut_sets=COMPUTE_MAX_CUT_SETS,
            time_budget=COMPUTE_TIME_BUDGET_SECONDS, success_terms=success_terms,
        )
    except analysis.AnalysisError as e:
        raise PraInvalid(str(e)) from e
    finally:
        _slots.release()
    if result.complete:  # a partial list depends on the clock; do not keep it
        with _computed_lock:
            _computed[key] = (model, result)
            while len(_computed) > _COMPUTED_CACHE_SIZE:
                _computed.popitem(last=False)
    return result


def _cut_set_source(model: PraModel, context: str, compute: bool, truncation: float,
                    max_order: Optional[int], success_terms: str) -> Tuple[List[List[str]], Dict[str, Any], List[str]]:
    stored = model.cut_sets_for(context)
    if stored is not None:
        meta = {"source": stored.source, "kind": stored.kind, "event_tree": stored.event_tree,
                "truncation": stored.truncation}
        return stored.cut_sets, meta, []
    kind = _known_context(model, context)
    if kind is None:
        raise PraNotFound(f"No fault tree, gate, sequence or end state named '{context}' in this model")
    if kind not in ("fault_tree", "gate"):
        raise PraNotFound(f"The model stores no cut sets for {kind.replace('_', ' ')} '{context}', and Clio "
                          "generates cut sets for fault trees and gates only")
    if not compute:
        raise PraNotFound(f"The model stores no cut sets for '{context}'; pass compute=true to generate them")
    result = _generate(model, context, truncation, max_order, success_terms)
    if not result.cut_sets and result.stopped:
        raise PraGuardStopped(
            f"Cut set generation for '{context}' stopped at the {COMPUTE_TIME_BUDGET_SECONDS:g} s time budget "
            f"before finding any cut set (none at or above {result.truncation:.3g}). Set max_order to narrow "
            "the search, or use the cut sets from the PRA tool itself.")
    meta = {
        "source": "computed",
        "kind": kind,
        "event_tree": None,
        "truncation": result.truncation,
        "computation": {
            "top_gate": result.top_gate,
            "complete": result.complete,
            "stopped": result.stopped,
            "requested_truncation": result.requested_truncation,
            "truncation_raised": result.truncation_raised,
            "max_order": result.max_order,
            "max_cut_sets": COMPUTE_MAX_CUT_SETS,
            "time_budget_seconds": COMPUTE_TIME_BUDGET_SECONDS,
            "success_terms": success_terms,
            "non_coherent": result.non_coherent,
            "elapsed_seconds": result.stats.get("elapsed_seconds"),
            "rows_explored": result.stats.get("rows"),
        },
    }
    return result.cut_sets, meta, list(result.warnings)


def _event_details(model: PraModel, names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        bare = analysis.split_name(name)[0]
        if bare in out:
            continue
        be = model.basic_events.get(bare) or model.initiating_events.get(bare)
        if be is not None:
            out[bare] = {"description": be.description, "probability": be.probability}
        elif bare in model.house_events:
            he = model.house_events[bare]
            out[bare] = {"description": he.description, "house_event": he.state}
        else:
            out[bare] = {"description": "", "probability": None}
    return out


def cut_sets_view(model: PraModel, context: str, *, limit: int = 50, compute: bool = False,
                  truncation: float = analysis.DEFAULT_TRUNCATION, max_order: Optional[int] = None,
                  success_terms: str = analysis.SUCCESS_TERMS_DROP) -> Dict[str, Any]:
    if limit < 1:
        raise PraInvalid("limit must be at least 1")
    cut_sets, meta, warnings = _cut_set_source(model, context, compute, truncation, max_order, success_terms)
    q = analysis.quantify_cut_sets(model, cut_sets)
    rows = []
    for rank, cs in enumerate(q.cut_sets[:limit], start=1):
        share = cs.probability / q.rare_event if cs.probability is not None and q.rare_event > 0 else None
        rows.append({"rank": rank, "events": cs.events, "order": len(cs.events), "probability": cs.probability,
                     "share": share, **({"missing": cs.missing} if cs.missing else {})})
    if q.missing_events:
        warnings.append(f"{len(q.missing_events)} event(s) have no probability; {q.incomplete_cut_sets} cut "
                        "set(s) containing them are left out of the totals, which are lower bounds.")
    if q.mcub is None and q.cut_sets:
        warnings.append("A cut set value exceeds 1 (a frequency), so the MCUB is not defined; use the "
                        "rare-event total.")
    return {
        "context": context,
        **meta,
        "totals": {"rare_event": q.rare_event, "mcub": q.mcub},
        "cut_set_count": len(q.cut_sets),
        "returned": len(rows),
        "cut_sets": rows,
        "events": _event_details(model, (e for r in rows for e in r["events"])),
        "missing_data": {"events": q.missing_events[:MISSING_EVENTS_LIMIT],
                         "events_total": len(q.missing_events),
                         "cut_sets_without_data": q.incomplete_cut_sets},
        "warnings": warnings,
        "definitions": {k: DEFINITIONS[k] for k in ("probability", "share", "rare_event", "mcub")},
        "notice": NOTICE,
    }


IMPORTANCE_SORTS = tuple(analysis.IMPORTANCE_SORT_KEYS)


def importance_view(model: PraModel, context: str, *, limit: int = 50, sort: str = "fv",
                    compute: bool = False, truncation: float = analysis.DEFAULT_TRUNCATION,
                    max_order: Optional[int] = None,
                    success_terms: str = analysis.SUCCESS_TERMS_DROP) -> Dict[str, Any]:
    if sort not in analysis.IMPORTANCE_SORT_KEYS:
        raise PraInvalid(f"sort must be one of {', '.join(IMPORTANCE_SORTS)}")
    if limit < 1:
        raise PraInvalid("limit must be at least 1")
    cut_sets, meta, warnings = _cut_set_source(model, context, compute, truncation, max_order, success_terms)
    result = analysis.importance(model, cut_sets)
    rows = analysis.sort_importance(result.rows, sort)
    details = _event_details(model, (r.event for r in rows[:limit]))
    events = [
        {"rank": rank, "event": r.event, "description": details[r.event]["description"],
         "probability": r.probability, "cut_sets": r.cut_sets, "fussell_vesely": r.fussell_vesely,
         "raw": r.raw, "rrw": r.rrw, "birnbaum": r.birnbaum}
        for rank, r in enumerate(rows[:limit], start=1)
    ]
    if result.missing_events:
        warnings.append(f"{len(result.missing_events)} event(s) have no probability; their measures are null "
                        f"and the {result.incomplete_cut_sets} cut set(s) containing them are left out of F.")
    if result.basis == analysis.BASIS_RARE_EVENT:
        warnings.append("A cut set value exceeds 1 (a frequency), so the measures use the rare-event sum "
                        "instead of the MCUB.")
    return {
        "context": context,
        **meta,
        "basis": result.basis,
        "total": result.total,
        "sort": sort,
        "events_total": len(rows),
        "returned": len(events),
        "events": events,
        "missing_data": {"events": result.missing_events[:MISSING_EVENTS_LIMIT],
                         "events_total": len(result.missing_events),
                         "cut_sets_without_data": result.incomplete_cut_sets},
        "warnings": warnings,
        "definitions": {k: DEFINITIONS[k] for k in ("mcub", "fussell_vesely", "raw", "rrw", "birnbaum")},
        "notice": NOTICE,
    }
