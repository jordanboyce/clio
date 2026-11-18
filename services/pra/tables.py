"""The structured tables of a PRA model, in the sheet shape the structured store takes.

Four tables per model document, each registered under the document with its
sheet name, so ``list_tables`` shows them as "<file> / basic_events" and so
on and ``query_table`` can join them:

  basic_events  name, kind (basic, house, initiating), description, probability,
                failure_rate, mission_time, calc_type, uncertainty
  gates         name, type, k, tree, inputs (space separated, "/X" = NOT X), description
  sequences     event_tree, name, end_state, transfer, path, logic, frequency, description
  cut_sets      context, kind, event_tree, rank, events (" * " joined), order,
                probability (product of the events' probabilities), source

Column types are fixed rather than inferred: a SAPHIRE sequence name such as
"02-2-2" would otherwise read as a date.
"""

from __future__ import annotations

from typing import Any, Dict, List

from services.pra.model import PraModel
from services.pra.text import product_probability

TEXT, REAL, INTEGER = "text", "real", "integer"

_COLUMNS = {
    "basic_events": [("name", TEXT), ("kind", TEXT), ("description", TEXT), ("probability", REAL),
                     ("failure_rate", REAL), ("mission_time", REAL), ("calc_type", TEXT),
                     ("uncertainty", TEXT)],
    "gates": [("name", TEXT), ("type", TEXT), ("k", INTEGER), ("tree", TEXT), ("inputs", TEXT),
              ("description", TEXT)],
    "sequences": [("event_tree", TEXT), ("name", TEXT), ("end_state", TEXT), ("transfer", TEXT),
                  ("path", TEXT), ("logic", TEXT), ("frequency", REAL), ("description", TEXT)],
    "cut_sets": [("context", TEXT), ("kind", TEXT), ("event_tree", TEXT), ("rank", INTEGER),
                 ("events", TEXT), ("order", INTEGER), ("probability", REAL), ("source", TEXT)],
}


def _sheet(name: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    columns = [c for c, _ in _COLUMNS[name]]
    return {
        "sheet_name": name,
        "columns": columns,
        "rows": rows,
        "type_overrides": dict(_COLUMNS[name]),
        "role_overrides": {},
    }


def model_sheets(model: PraModel) -> List[Dict[str, Any]]:
    """The non-empty tables of ``model``; each is one ``create_table`` call."""
    events: List[Dict[str, Any]] = []
    for kind, table in (("basic", model.basic_events), ("initiating", model.initiating_events)):
        for be in table.values():
            events.append({"name": be.name, "kind": kind, "description": be.description,
                           "probability": be.probability, "failure_rate": be.failure_rate,
                           "mission_time": be.mission_time, "calc_type": be.calc_type,
                           "uncertainty": be.uncertainty})
    for h in model.house_events.values():
        events.append({"name": h.name, "kind": "house", "description": h.description,
                       "probability": 1.0 if h.state else 0.0, "failure_rate": None,
                       "mission_time": None, "calc_type": "TRUE" if h.state else "FALSE",
                       "uncertainty": None})
    gates = [{"name": g.name, "type": g.type, "k": g.k, "tree": g.tree, "inputs": " ".join(g.inputs),
              "description": g.description} for g in model.gates.values()]
    sequences = [{"event_tree": et.name, "name": s.name, "end_state": s.end_state, "transfer": s.transfer,
                  "path": ", ".join(f"{p.functional_event} {p.state}" for p in s.path),
                  "logic": " ".join(s.logic), "frequency": s.frequency, "description": s.description}
                 for et in model.event_trees.values() for s in et.sequences]
    cut_sets = [{"context": c.context, "kind": c.kind, "event_tree": c.event_tree, "rank": rank,
                 "events": " * ".join(cs), "order": len(cs),
                 "probability": product_probability(model, cs), "source": c.source}
                for c in model.cut_sets for rank, cs in enumerate(c.cut_sets, start=1)]
    tables = {"basic_events": events, "gates": gates, "sequences": sequences, "cut_sets": cut_sets}
    return [_sheet(name, rows) for name, rows in tables.items() if rows]
