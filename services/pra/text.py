"""The searchable text of a PRA model: pages that answer questions in words.

Page 1 is an overview; then each fault tree (long ones over several pages),
each event tree, the basic events, house and initiating events, and the cut
sets carried in the model. Every page opens with a ``#`` heading line and
every gate, sequence or event is one paragraph, so the chunker keeps a gate
with its inputs and repeats the heading in front of each chunk: a passage is
citable on its own ("what feeds gate X", "the probability of the AFW pump
failing to start", "which sequences end in core damage").
"""

from __future__ import annotations

from typing import Dict, List, Optional

from services.pra.model import BasicEvent, EventTree, FaultTree, Gate, PraModel, Sequence

GATES_PER_PAGE = 40
EVENTS_PER_PAGE = 80
SEQUENCES_PER_PAGE = 60
CUT_SETS_SHOWN = 10
CUT_SET_LISTS_PER_PAGE = 20

_FORMAT_NAMES = {"open-psa-mef": "Open-PSA Model Exchange Format (MEF)",
                 "saphire-mard": "SAPHIRE MAR-D export"}
_PHRASES = {
    "or": "fails if any of",
    "and": "fails if all of",
    "nand": "fails unless all of",
    "nor": "fails if none of",
    "not": "occurs when this does not occur",
    "xor": "fails if exactly one of",
}


def number(value: Optional[float]) -> str:
    """3 significant figures, in the E notation PRA tools print (7.94E-04) for small values."""
    if value is None:
        return ""
    if value != 0 and not 0.01 <= abs(value) < 1e4:
        return f"{value:.2E}"
    return f"{value:.3G}"


class _Renderer:
    def __init__(self, model: PraModel):
        self.m = model
        self.parents: Dict[str, List[str]] = {}
        for gate in model.gates.values():
            for i in gate.inputs:
                self.parents.setdefault(i.lstrip("/"), []).append(gate.name)

    # ── how one name reads ─────────────────────────────────────────────

    def describe(self, ref: str) -> str:
        negated = ref.startswith("/")
        name = ref[1:] if negated else ref
        m = self.m
        if name in m.basic_events:
            be = m.basic_events[name]
            bits = [b for b in (be.description, f"p={number(be.probability)}" if be.probability is not None else "")
                    if b]
            text = f"{name} — {', '.join(bits)}" if bits else f"{name} (basic event)"
        elif name in m.gates:
            g = m.gates[name]
            text = f"{name} — {g.description} (gate)" if g.description else f"{name} (gate, {g.type.upper()})"
        elif name in m.house_events:
            h = m.house_events[name]
            state = "true" if h.state else "false"
            text = f"{name} — {h.description} (house event, {state})" if h.description \
                else f"{name} (house event, {state})"
        elif name in m.initiating_events:
            ie = m.initiating_events[name]
            text = f"{name} — {ie.description} (initiating event)" if ie.description else f"{name} (initiating event)"
        elif name in m.fault_trees:
            text = f"{name} (fault tree)"
        else:
            text = name
        return f"NOT {text}" if negated else text

    def gate_line(self, g: Gate) -> str:
        if g.type == "transfer":
            target = g.inputs[0] if g.inputs else g.name
            ft = self.m.fault_trees.get(target)
            where = f"fault tree {target}" + (f" ({ft.description})" if ft and ft.description else "")
            head = f"{g.name} (TRANSFER) — continues in {where}"
            if ft and ft.top and ft.top != g.name:
                head += f", top gate {ft.top}"
            return head + "."
        if g.type == "atleast":
            label, phrase = f"{g.k}-OF-{len(g.inputs)}", f"fails if at least {g.k} of"
        elif g.type == "other":
            label, phrase = "OTHER", "combines"
        else:
            label, phrase = g.type.upper(), _PHRASES.get(g.type, "combines")
        tree = f" in fault tree {g.tree}" if g.tree else ""
        desc = f" — {g.description}" if g.description else ""
        inputs = "; ".join(self.describe(i) for i in g.inputs) or "(no inputs)"
        line = f"Gate {g.name} ({label}){tree}{desc} — {phrase}: {inputs}."
        parents = self.parents.get(g.name)
        if parents:
            line += f" Feeds gate {', '.join(dict.fromkeys(parents))}."
        return line

    def event_line(self, be: BasicEvent, kind: str = "Basic event") -> str:
        parts = [f"{kind} {be.name}" + (f" — {be.description}" if be.description else "")]
        facts = []
        if be.probability is not None:
            facts.append(("frequency " if kind == "Initiating event" else "probability ") + number(be.probability))
        if be.failure_rate is not None:
            facts.append(f"failure rate {number(be.failure_rate)}/h")
        if be.mission_time is not None:
            facts.append(f"mission time {number(be.mission_time)} h")
        if be.calc_type:
            facts.append(f"calculation type {be.calc_type}")
        if be.uncertainty:
            facts.append(f"uncertainty {be.uncertainty}")
        for key, value in be.attributes.items():
            facts.append(f"{key.replace('_', ' ')} {value}")
        text = parts[0] + (": " + "; ".join(facts) if facts else "") + "."
        parents = self.parents.get(be.name)
        if parents:
            text += f" Input to gate {', '.join(dict.fromkeys(parents))}."
        return text

    # ── pages ──────────────────────────────────────────────────────────

    def overview(self) -> str:
        m = self.m
        c = m.counts()
        lines = [f"# PRA model {m.name}", ""]
        intro = f"PRA model {m.name}, read from a {_FORMAT_NAMES.get(m.source_format, m.source_format)}"
        if m.source_files:
            intro += f" ({', '.join(m.source_files[:12])}{' and more' if len(m.source_files) > 12 else ''})"
        lines.append(intro + "." + (f" {m.description}" if m.description else ""))
        lines.append("")
        lines.append(
            f"It holds {c['fault_trees']} fault trees, {c['gates']} gates, {c['basic_events']} basic events, "
            f"{c['house_events']} house events, {len(m.initiating_events)} initiating events, "
            f"{c['event_trees']} event trees with {c['sequences']} sequences, {len(m.end_states)} end states "
            f"and {c['cut_set_lists']} cut set lists from the model.")
        if m.fault_trees:
            lines += ["", "Fault trees: " + "; ".join(
                f"{ft.name}" + (f" — {ft.description}" if ft.description else "")
                + (f" (top gate {ft.top})" if ft.top and ft.top != ft.name else "")
                for ft in list(m.fault_trees.values())[:200]) + "."]
        if m.event_trees:
            lines += ["", "Event trees: " + "; ".join(
                f"{et.name}" + (f" — {et.description}" if et.description else "")
                + (f" (initiating event {et.initiating_event})" if et.initiating_event else "")
                for et in m.event_trees.values()) + "."]
        if m.end_states:
            lines += ["", "End states: " + "; ".join(
                f"{n}" + (f" — {d}" if d else "") for n, d in m.end_states.items()) + "."]
        if m.warnings:
            lines += ["", "Warnings from reading the model (what Clio could not represent):"]
            lines += [f"Warning: {w}" for w in m.warnings]
        return "\n".join(lines)

    def fault_tree_pages(self, ft: FaultTree) -> List[str]:
        gates = [self.m.gates[g] for g in ft.gates if g in self.m.gates]
        if ft.top in self.m.gates and self.m.gates[ft.top] not in gates:
            gates.insert(0, self.m.gates[ft.top])
        intro = f"Fault tree {ft.name}" + (f" — {ft.description}" if ft.description else "")
        intro += f". Top gate: {ft.top}." if ft.top else ". Its logic is not in these files."
        cut = self.m.cut_sets_for(ft.name)
        pages = []
        chunks = [gates[i:i + GATES_PER_PAGE] for i in range(0, len(gates), GATES_PER_PAGE)] or [[]]
        for n, part in enumerate(chunks, start=1):
            heading = f"# Fault tree {ft.name}" + (f" (part {n} of {len(chunks)})" if len(chunks) > 1 else "")
            body = [heading, "", intro, ""]
            body += [self.gate_line(g) + "\n" for g in part]
            if n == 1 and cut is not None and cut.kind == "fault_tree":
                body.append(self.cut_set_paragraph(cut.context, cut.cut_sets, "fault tree") + "\n")
            pages.append("\n".join(body))
        return pages

    def sequence_line(self, et: EventTree, s: Sequence) -> str:
        head = f"Sequence {s.name} of event tree {et.name}"
        if s.transfer:
            head += f" transfers to event tree {s.transfer}"
        elif s.end_state:
            desc = self.m.end_states.get(s.end_state)
            head += f" ends in {s.end_state}" + (f" ({desc})" if desc else "")
        parts = [head]
        if s.path:
            parts.append("Path: " + ", ".join(f"{p.functional_event} {p.state}" for p in s.path))
        if s.logic:
            parts.append("Logic: " + " ".join(s.logic))
        if s.frequency is not None:
            parts.append(f"Frequency {number(s.frequency)}")
        if s.description:
            parts.append(f"Label: {s.description}")
        return ". ".join(parts) + "."

    def event_tree_pages(self, et: EventTree) -> List[str]:
        intro = f"Event tree {et.name}" + (f" — {et.description}" if et.description else "") + "."
        if et.initiating_event:
            intro += f" Initiating event: {self.describe(et.initiating_event)}."
        if et.functional_events:
            intro += " Functional events in order: " + "; ".join(
                f + (f" — {et.functional_event_descriptions[f]}" if f in et.functional_event_descriptions else "")
                for f in et.functional_events) + "."
        by_end: Dict[str, List[str]] = {}
        for s in et.sequences:
            key = f"transfer to {s.transfer}" if s.transfer else (s.end_state or "")
            if key:
                by_end.setdefault(key, []).append(s.name)
        summary = " ".join(
            f"Sequences of {et.name} ending in {end}"
            + (f" ({self.m.end_states[end]})" if end in self.m.end_states and self.m.end_states[end] else "")
            + f": {', '.join(names)}." for end, names in by_end.items())
        seqs = et.sequences
        chunks = [seqs[i:i + SEQUENCES_PER_PAGE] for i in range(0, len(seqs), SEQUENCES_PER_PAGE)] or [[]]
        pages = []
        for n, part in enumerate(chunks, start=1):
            heading = f"# Event tree {et.name}" + (f" (part {n} of {len(chunks)})" if len(chunks) > 1 else "")
            body = [heading, "", intro, ""]
            if n == 1 and summary:
                body += [summary, ""]
            body += [self.sequence_line(et, s) + "\n" for s in part]
            pages.append("\n".join(body))
        return pages

    def event_pages(self, events: List[BasicEvent], title: str, kind: str) -> List[str]:
        pages = []
        total = len(events)
        for start in range(0, total, EVENTS_PER_PAGE):
            part = events[start:start + EVENTS_PER_PAGE]
            span = f" ({start + 1}-{start + len(part)} of {total})" if total > EVENTS_PER_PAGE else ""
            body = [f"# {title} of {self.m.name}{span}", ""]
            body += [self.event_line(be, kind) + "\n" for be in part]
            pages.append("\n".join(body))
        return pages

    def house_event_page(self) -> Optional[str]:
        if not self.m.house_events:
            return None
        body = [f"# House events of {self.m.name}", ""]
        for h in self.m.house_events.values():
            line = f"House event {h.name}" + (f" — {h.description}" if h.description else "")
            line += f": {'true' if h.state else 'false'}."
            parents = self.parents.get(h.name)
            if parents:
                line += f" Input to gate {', '.join(dict.fromkeys(parents))}."
            body.append(line + "\n")
        return "\n".join(body)

    def cut_set_paragraph(self, context: str, cut_sets: List[List[str]], what: str) -> str:
        shown = []
        for cs in cut_sets[:CUT_SETS_SHOWN]:
            p = product_probability(self.m, cs)
            shown.append((" * ".join(cs) or "TRUE") + (f" (p={number(p)})" if p is not None else ""))
        more = f" and {len(cut_sets) - CUT_SETS_SHOWN} more" if len(cut_sets) > CUT_SETS_SHOWN else ""
        return (f"Cut sets of {what} {context} from the model ({len(cut_sets)}): "
                + "; ".join(shown) + more + ".")

    def cut_set_pages(self) -> List[str]:
        lists = [c for c in self.m.cut_sets if c.kind != "sequence"
                 and not (c.kind == "fault_tree" and c.context in self.m.fault_trees)]
        pages = []
        for start in range(0, len(lists), CUT_SET_LISTS_PER_PAGE):
            body = [f"# Cut sets of {self.m.name}", ""]
            for c in lists[start:start + CUT_SET_LISTS_PER_PAGE]:
                what = {"end_state": "end state", "fault_tree": "fault tree"}.get(c.kind, c.kind)
                body.append(self.cut_set_paragraph(c.context, c.cut_sets, what) + "\n")
            pages.append("\n".join(body))
        sequence_lists = sum(1 for c in self.m.cut_sets if c.kind == "sequence")
        if sequence_lists:
            pages.append(f"# Sequence cut sets of {self.m.name}\n\n{sequence_lists} sequences carry cut sets "
                         f"from the model; they are in the cut_sets table (kind 'sequence'), one row per cut set "
                         f"with its events and probability.")
        return pages


def product_probability(model: PraModel, cut_set: List[str]) -> Optional[float]:
    """The product of the events' probabilities ("/X" counts 1 - p), or None when one is unknown."""
    p = 1.0
    for ref in cut_set:
        negated = ref.startswith("/")
        name = ref[1:] if negated else ref
        if name in model.basic_events:
            q = model.basic_events[name].probability
        elif name in model.house_events:
            q = 1.0 if model.house_events[name].state else 0.0
        elif name in model.initiating_events:
            q = model.initiating_events[name].probability
        else:
            q = None
        if q is None:
            return None
        p *= (1.0 - q) if negated else q
    return p


def render_pages(model: PraModel) -> Dict[int, str]:
    """{page number: text}, page 1 the overview."""
    r = _Renderer(model)
    pages: List[str] = [r.overview()]
    for ft in model.fault_trees.values():
        pages += r.fault_tree_pages(ft)
    orphans = [g for g in model.gates.values() if not g.tree or g.tree not in model.fault_trees]
    for start in range(0, len(orphans), GATES_PER_PAGE):
        pages.append("\n".join([f"# Gates of {model.name} outside any fault tree", ""]
                               + [r.gate_line(g) + "\n" for g in orphans[start:start + GATES_PER_PAGE]]))
    for et in model.event_trees.values():
        pages += r.event_tree_pages(et)
    pages += r.event_pages(list(model.basic_events.values()), "Basic events", "Basic event")
    house = r.house_event_page()
    if house:
        pages.append(house)
    pages += r.event_pages(list(model.initiating_events.values()), "Initiating events", "Initiating event")
    pages += r.cut_set_pages()
    return {n: text for n, text in enumerate(pages, start=1) if text.strip()}
