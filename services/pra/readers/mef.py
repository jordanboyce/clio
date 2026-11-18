"""Open-PSA Model Exchange Format (MEF) reader: XML with an ``<opsa-mef>`` root.

Covers the layers PRA tools exchange in practice (XFTA, SCRAM, RiskSpectrum and
others that export MEF): fault trees with their components, gates with any
formula, basic and house events, parameters and the common stochastic
expressions, CCF groups (members only), event trees with forks, branches and
sequences, initiating events, and ``<sum-of-products>`` cut sets from a report.

Nothing here computes a model's results. What cannot be represented is kept
as a warning on the model, never dropped silently. Names follow the MEF scoping
rules (fault_tree_layer.rst, "Solving Name Conflicts"): a public element keeps
its own name, a private one is known by its full path ``FT.COMPONENT.NAME``.

XML safety: a file that declares a DTD or entities is refused (expat would
expand internal entities), the element count is checked before the tree is
built, and every recursion is bounded.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Dict, List, Optional, Sequence as Seq, Tuple

from services.pra.model import (BasicEvent, CutSetList, EventTree, FaultTree, Gate, HouseEvent,
                                PraModel, Sequence, SequencePathStep)
from services.pra.readers.base import PraFormatError, Source, WarningLog, check_size, limits

SOURCE_FORMAT = "open-psa-mef"
# MEF leaves the mission time to the analysis tool; SCRAM's default.
DEFAULT_MISSION_TIME_HOURS = 8760.0
MAX_DEPTH = 200
MAX_PATHS_PER_EVENT_TREE = 20000

_DTD = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_EVENT_TAGS = {"gate", "basic-event", "house-event", "event"}
_NARY = {"and": "and", "or": "or", "xor": "xor", "nand": "nand", "nor": "nor"}
_DEVIATES = {"lognormal-deviate", "normal-deviate", "uniform-deviate", "gamma-deviate", "beta-deviate"}
_IGNORED_TOP_LEVEL = {
    "define-substitution": "substitutions",
    "define-alignment": "alignments and mission phases",
    "define-phase": "alignments and mission phases",
    "define-consequence": "consequences",
    "define-consequence-group": "consequences",
    "define-initiating-event-group": "initiating event groups",
    "define-extern-library": "external functions",
    "define-extern-function": "external functions",
}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: ET.Element) -> List[ET.Element]:
    return [c for c in el if isinstance(c.tag, str)]


def _label(el: ET.Element) -> str:
    for c in _children(el):
        if _local(c.tag) == "label":
            return " ".join((c.text or "").split())
    return ""


def _attributes(el: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in _children(el):
        if _local(c.tag) == "attributes":
            for a in _children(c):
                if _local(a.tag) == "attribute" and a.get("name"):
                    out[a.get("name")] = a.get("value", "")
    return out


def _body(el: ET.Element) -> List[ET.Element]:
    """Children other than <label> and <attributes>."""
    return [c for c in _children(el) if _local(c.tag) not in ("label", "attributes")]


def _number(text: Optional[str]) -> Optional[float]:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _fmt(value: float) -> str:
    return f"{value:.4G}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_root(name: str, data: bytes) -> ET.Element:
    """The XML root of one MEF file, refusing DTDs, entities and huge trees."""
    if _DTD.search(data):
        raise PraFormatError(f"{name} declares a DTD or XML entities, which Clio does not read.")
    cap = limits()["elements"]
    if cap:
        approx = data.count(b"<") - data.count(b"</") - data.count(b"<!--") - data.count(b"<?")
        if approx > cap:
            raise PraFormatError(
                f"{name} has more than {cap:,} XML elements; Clio does not read models that "
                f"large (PRA_MAX_ELEMENTS)."
            )
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise PraFormatError(f"{name} is not well-formed XML: {e}") from None


@dataclass
class _Def:
    kind: str               # gate | basic-event | house-event | parameter
    name: str               # the model's name for it (bare, or full path if private)
    element: ET.Element
    path: Tuple[str, ...]   # containers, outermost first
    tree: Optional[str]     # the fault tree that defines it


class _Unsupported(Exception):
    """An expression Clio does not evaluate; the argument names it."""


@dataclass
class _Value:
    probability: Optional[float] = None
    failure_rate: Optional[float] = None
    mission_time: Optional[float] = None
    uncertainty: Optional[str] = None
    unsupported: Optional[str] = None
    used_mission_time: bool = False


@dataclass
class _Scope:
    """Name resolution for one namespace (events, or parameters)."""
    by_path: Dict[str, str] = field(default_factory=dict)
    public: Dict[str, str] = field(default_factory=dict)

    def register(self, name: str, path: Tuple[str, ...], private: bool) -> str:
        full = ".".join(path + (name,))
        model_name = full if private else name
        self.by_path[full] = model_name
        if not private:
            self.public.setdefault(name, model_name)
        return model_name

    def resolve(self, ref: str, path: Tuple[str, ...]) -> Optional[str]:
        for i in range(len(path), -1, -1):
            hit = self.by_path.get(".".join(path[:i] + (ref,)))
            if hit:
                return hit
        return self.public.get(ref)


class _Builder:
    def __init__(self, files: Seq[str]):
        self.model = PraModel(name="", source_format=SOURCE_FORMAT, source_files=list(files))
        self.log = WarningLog()
        self.events = _Scope()
        self.params = _Scope()
        self.defs: Dict[str, _Def] = {}           # event definitions by model name
        self.param_defs: Dict[str, _Def] = {}
        self.order: List[str] = []                 # event definitions in file order
        self.trees: Dict[str, ET.Element] = {}
        self.ccf_groups: List[Tuple[ET.Element, Tuple[str, ...]]] = []
        self.event_trees: List[ET.Element] = []
        self.initiating: List[ET.Element] = []
        self.rules: Dict[str, ET.Element] = {}
        self.reports: List[ET.Element] = []
        self.unresolved: List[str] = []
        self._param_cache: Dict[str, _Value] = {}
        self._param_busy: set = set()
        self._ie_for_tree: Dict[str, str] = {}     # event tree -> its initiating event
        self.sub_gates: set = set()                # gates made for nested formulas

    # ── collecting definitions ─────────────────────────────────────────

    def add_root(self, name: str, root: ET.Element) -> None:
        tag = _local(root.tag)
        if tag == "report":
            self.reports.append(root)
            return
        if tag != "opsa-mef":
            raise PraFormatError(f"{name} is XML but not an Open-PSA model (its root is <{tag}>).")
        if not self.model.name:
            self.model.name = root.get("name") or _label(root) or PurePath(name).stem
        if not self.model.description and _label(root):
            self.model.description = _label(root)
        for child in _children(root):
            ctag = _local(child.tag)
            if ctag == "define-fault-tree":
                tree = child.get("name", "")
                if tree in self.trees:
                    self.log.group("{count} fault trees are defined more than once; the first "
                                   "definition is used: {names}.", tree)
                    continue
                self.trees[tree] = child
                self._container(child, (tree,), private=False, tree=tree)
            elif ctag == "model-data":
                self._container(child, (), private=False, tree=None)
            elif ctag in ("define-gate", "define-basic-event", "define-house-event", "define-parameter"):
                self._define(child, (), private=False, tree=None)
            elif ctag == "define-CCF-group":
                self.ccf_groups.append((child, ()))
            elif ctag == "define-event-tree":
                self.event_trees.append(child)
            elif ctag == "define-initiating-event":
                self.initiating.append(child)
            elif ctag == "define-rule":
                self.rules[child.get("name", "")] = child
            elif ctag == "include":
                self.log.group("Included files are not followed; upload them in the same zip: "
                               "{names}.", child.get("file", "?"))
            elif ctag in _IGNORED_TOP_LEVEL:
                self.log.group("Not modelled by Clio ({names}): " + _IGNORED_TOP_LEVEL[ctag] + ".",
                               child.get("name", ctag))
            elif ctag not in ("label", "attributes"):
                self.log.group("Unknown top-level MEF elements were skipped: {names}.", f"<{ctag}>")

    def _container(self, el: ET.Element, path: Tuple[str, ...], private: bool, tree: Optional[str]) -> None:
        for child in _children(el):
            ctag = _local(child.tag)
            if ctag in ("define-gate", "define-basic-event", "define-house-event", "define-parameter"):
                self._define(child, path, private, tree)
            elif ctag == "define-component":
                role = child.get("role")
                inner_private = role == "private" if role else private
                self._container(child, path + (child.get("name", ""),), inner_private, tree)
            elif ctag == "define-CCF-group":
                self.ccf_groups.append((child, path))
            elif ctag == "define-substitution":
                self.log.group("Not modelled by Clio ({names}): substitutions.", child.get("name", ctag))
            elif ctag == "include":
                self.log.group("Included files are not followed; upload them in the same zip: "
                               "{names}.", child.get("file", "?"))

    def _define(self, el: ET.Element, path: Tuple[str, ...], private: bool, tree: Optional[str]) -> None:
        kind = _local(el.tag)[len("define-"):]
        role = el.get("role")
        is_private = role == "private" if role else private
        name = el.get("name", "")
        scope = self.params if kind == "parameter" else self.events
        model_name = scope.register(name, path, is_private)
        table = self.param_defs if kind == "parameter" else self.defs
        if model_name in table:
            self.log.group("{count} names are defined more than once; the first definition is used: "
                           "{names}.", model_name)
            return
        table[model_name] = _Def(kind, model_name, el, path, tree)
        if kind != "parameter":
            self.order.append(model_name)

    # ── names ──────────────────────────────────────────────────────────

    def event_ref(self, ref: str, path: Tuple[str, ...]) -> str:
        hit = self.events.resolve(ref, path)
        if hit is None:
            self.unresolved.append(ref)
            return ref
        return hit

    # ── expressions ────────────────────────────────────────────────────

    def evaluate(self, el: ET.Element, path: Tuple[str, ...], state: _Value, depth: int = 0) -> float:
        if depth > MAX_DEPTH:
            raise _Unsupported("expressions nested too deeply")
        tag = _local(el.tag)
        args = _children(el)

        def ev(i: int) -> float:
            if i >= len(args):
                raise _Unsupported(f"<{tag}> with missing arguments")
            return self.evaluate(args[i], path, state, depth + 1)

        if tag in ("float", "int"):
            value = _number(el.get("value"))
            if value is None:
                raise _Unsupported(f"<{tag}> without a number")
            return value
        if tag == "bool":
            return 1.0 if el.get("value") == "true" else 0.0
        if tag == "parameter":
            ref = el.get("name", "")
            target = self.params.resolve(ref, path)
            if target is None:
                raise _Unsupported(f"undefined parameter {ref}")
            inner = self.parameter(target)
            state.used_mission_time |= inner.used_mission_time
            if inner.unsupported:
                raise _Unsupported(inner.unsupported)
            if inner.probability is None:
                raise _Unsupported(f"parameter {ref} without a value")
            state.uncertainty = state.uncertainty or inner.uncertainty
            return inner.probability
        if tag == "system-mission-time":
            state.used_mission_time = True
            return DEFAULT_MISSION_TIME_HOURS
        if tag == "add":
            return sum(ev(i) for i in range(len(args)))
        if tag == "sub":
            return ev(0) - sum(ev(i) for i in range(1, len(args)))
        if tag == "mul":
            out = 1.0
            for i in range(len(args)):
                out *= ev(i)
            return out
        if tag == "div":
            out = ev(0)
            for i in range(1, len(args)):
                d = ev(i)
                if d == 0:
                    raise _Unsupported("division by zero")
                out /= d
            return out
        if tag == "neg":
            return -ev(0)
        if tag == "exponential":
            if len(args) != 2:
                raise _Unsupported("<exponential> with other than two arguments")
            rate, time = ev(0), ev(1)
            if depth == 0:
                state.failure_rate, state.mission_time = rate, time
            return 1.0 - math.exp(-rate * time)
        if tag in _DEVIATES:
            values = [ev(i) for i in range(len(args))]
            mean = self._deviate_mean(tag, values)
            if state.uncertainty is None:
                state.uncertainty = self._deviate_text(tag, values)
            return mean
        raise _Unsupported(f"<{tag}>")

    @staticmethod
    def _deviate_mean(tag: str, v: List[float]) -> float:
        need = {"lognormal-deviate": 2, "normal-deviate": 2, "uniform-deviate": 2,
                "gamma-deviate": 2, "beta-deviate": 2}[tag]
        if len(v) < need:
            raise _Unsupported(f"<{tag}> with missing arguments")
        if tag in ("lognormal-deviate", "normal-deviate"):
            return v[0]
        if tag == "uniform-deviate":
            return (v[0] + v[1]) / 2
        if tag == "gamma-deviate":
            return v[0] * v[1]
        if v[0] + v[1] == 0:
            raise _Unsupported("<beta-deviate> with zero parameters")
        return v[0] / (v[0] + v[1])

    @staticmethod
    def _deviate_text(tag: str, v: List[float]) -> str:
        if tag == "lognormal-deviate":
            text = f"lognormal mean={_fmt(v[0])} EF={_fmt(v[1])}"
            return text + (f" level={_fmt(v[2])}" if len(v) > 2 else "")
        if tag == "normal-deviate":
            return f"normal mean={_fmt(v[0])} sd={_fmt(v[1])}"
        if tag == "uniform-deviate":
            return f"uniform min={_fmt(v[0])} max={_fmt(v[1])}"
        if tag == "gamma-deviate":
            return f"gamma k={_fmt(v[0])} theta={_fmt(v[1])}"
        return f"beta alpha={_fmt(v[0])} beta={_fmt(v[1])}"

    def value_of(self, expression: Optional[ET.Element], path: Tuple[str, ...]) -> _Value:
        state = _Value()
        if expression is None:
            return state
        try:
            state.probability = self.evaluate(expression, path, state)
        except _Unsupported as e:
            state.unsupported = str(e)
            state.probability = None
        if _local(expression.tag) == "parameter" and state.failure_rate is None:
            # A basic event defined by a parameter that is itself an exponential.
            target = self.params.resolve(expression.get("name", ""), path)
            if target:
                inner = self.parameter(target)
                state.failure_rate, state.mission_time = inner.failure_rate, inner.mission_time
        return state

    def parameter(self, name: str) -> _Value:
        if name in self._param_cache:
            return self._param_cache[name]
        if name in self._param_busy:
            return _Value(unsupported=f"parameter {name} that refers to itself")
        self._param_busy.add(name)
        try:
            d = self.param_defs[name]
            body = _body(d.element)
            value = self.value_of(body[0] if body else None, d.path)
        finally:
            self._param_busy.discard(name)
        self._param_cache[name] = value
        return value

    # ── formulas ───────────────────────────────────────────────────────

    def gate(self, d: _Def) -> List[Gate]:
        """The gate a <define-gate> describes, and the sub-gates its nested formulas need."""
        made: List[Optional[Gate]] = []  # None holds a parent's place while its inputs are built
        counter = [0]

        def sub_name() -> str:
            counter[0] += 1
            name = f"{d.name}.{counter[0]}"
            self.sub_gates.add(name)
            return name

        def negate(name: str) -> str:
            return name[1:] if name.startswith("/") else "/" + name

        def operand(el: ET.Element, depth: int) -> str:
            tag = _local(el.tag)
            if tag in _EVENT_TAGS:
                return self.event_ref(el.get("name", ""), d.path)
            kids = _children(el)
            if tag == "not" and len(kids) == 1 and _local(kids[0].tag) in _EVENT_TAGS | {"not"}:
                return negate(operand(kids[0], depth + 1))
            if tag == "constant":
                name = sub_name()
                self.model.house_events[name] = HouseEvent(
                    name, el.get("value") == "true", f"constant in gate {d.name}")
                return name
            name = sub_name()
            build(name, el, depth + 1)
            return name

        def build(name: str, el: ET.Element, depth: int) -> None:
            if depth > MAX_DEPTH:
                raise PraFormatError(f"Gate {d.name} nests formulas more than {MAX_DEPTH} deep.")
            tag = _local(el.tag)
            kids = _children(el)
            label = _label(d.element) if name == d.name else ""
            slot = len(made)
            made.append(None)

            def put(gate_type: str, inputs: List[str], k: Optional[int] = None) -> None:
                made[slot] = Gate(name, gate_type, inputs, k=k, tree=d.tree, description=label)

            if tag in _NARY:
                put(_NARY[tag], [operand(c, depth) for c in kids])
            elif tag == "atleast":
                k = int(_number(el.get("min")) or 0)
                put("atleast", [operand(c, depth) for c in kids], k=k)
            elif tag == "not":
                put("not", [operand(kids[0], depth)] if kids else [])
            elif tag == "imply" and len(kids) == 2:
                put("or", [negate(operand(kids[0], depth)), operand(kids[1], depth)])
            elif tag == "iff" and len(kids) == 2:
                inner = sub_name()
                put("not", [inner])
                slot2 = len(made)
                made.append(None)
                made[slot2] = Gate(inner, "xor", [operand(c, depth) for c in kids], tree=d.tree)
            elif tag == "cardinality":
                inputs = [operand(c, depth) for c in kids]
                low, high = int(_number(el.get("min")) or 0), int(_number(el.get("max")) or 0)
                parts = []
                if low > 0:
                    parts.append(self._sub_gate(made, sub_name(), "atleast", inputs, low, d.tree))
                if high < len(inputs):
                    parts.append(negate(self._sub_gate(made, sub_name(), "atleast", inputs, high + 1, d.tree)))
                if parts:
                    put("and", parts)
                else:
                    put("other", inputs)
                    self.log.group("Gates whose cardinality formula is always true are kept as "
                                   "'other': {names}.", d.name)
            elif tag in _EVENT_TAGS or tag == "constant":
                put("or", [operand(el, depth)])
            else:
                put("other", [operand(c, depth) for c in kids])
                self.log.group("Gates with formulas Clio does not model are kept as 'other' "
                               "({names}).", f"{d.name} <{tag}>")

        body = _body(d.element)
        if not body:
            self.log.group("{count} gates have no formula: {names}.", d.name)
            return [Gate(d.name, "other", [], tree=d.tree, description=_label(d.element))]
        build(d.name, body[0], 0)
        return [g for g in made if g is not None]

    @staticmethod
    def _sub_gate(made: List[Optional[Gate]], name: str, gate_type: str, inputs: List[str],
                  k: Optional[int], tree: Optional[str]) -> str:
        made.append(Gate(name, gate_type, list(inputs), k=k, tree=tree))
        return name

    # ── building the model ─────────────────────────────────────────────

    def build(self) -> PraModel:
        m = self.model
        tree_gates: Dict[str, List[str]] = {t: [] for t in self.trees}
        for name in self.order:
            d = self.defs[name]
            if d.kind == "gate":
                for g in self.gate(d):
                    m.gates[g.name] = g
                    if d.tree:
                        tree_gates[d.tree].append(g.name)
            elif d.kind == "house-event":
                body = _body(d.element)
                state = bool(body) and _local(body[0].tag) == "constant" and body[0].get("value") == "true"
                m.house_events[name] = HouseEvent(name, state, _label(d.element))
            else:
                self._basic_event(d)
        for el, path in self.ccf_groups:
            self._ccf_group(el, path)
        for tree, el in self.trees.items():
            m.fault_trees[tree] = FaultTree(tree, top=self._top(tree, tree_gates[tree]),
                                            description=_label(el), gates=tree_gates[tree])
        for el in self.initiating:
            self._initiating_event(el)
        for el in self.event_trees:
            self._event_tree(el)
        for root in self.reports:
            self._report(root)
        for ref in sorted(set(self.unresolved)):
            self.log.group("{count} names are used but never defined in these files: {names}.", ref)
        m.warnings.extend(self.log.render())
        if not m.name:
            m.name = PurePath(m.source_files[0]).stem if m.source_files else "MEF model"
        return m

    def _basic_event(self, d: _Def) -> None:
        body = _body(d.element)
        value = self.value_of(body[0] if body else None, d.path)
        if not body:
            self.log.group("{count} basic events have no probability in the model: {names}.", d.name)
        elif value.unsupported:
            self.log.group("{count} basic events use expressions Clio does not evaluate, so their "
                           "probability is left empty: {names}.", f"{d.name} ({value.unsupported})")
        if value.used_mission_time:
            self.log.group("The mission time is set by the analysis tool, not the model; Clio used "
                           f"{DEFAULT_MISSION_TIME_HOURS:g} hours (SCRAM's default) for {{count}} "
                           "basic events: {names}.", d.name)
        p = value.probability
        if p is not None and not 0.0 <= p <= 1.0:
            self.log.group("{count} basic events have a value outside 0 to 1 (kept as given): {names}.",
                           d.name)
        self.model.basic_events[d.name] = BasicEvent(
            d.name, _label(d.element), probability=p, failure_rate=value.failure_rate,
            mission_time=value.mission_time, uncertainty=value.uncertainty,
            attributes=_attributes(d.element))

    def _ccf_group(self, el: ET.Element, path: Tuple[str, ...]) -> None:
        group, model = el.get("name", ""), el.get("model", "")
        members: List[str] = []
        distribution = None
        for c in _children(el):
            tag = _local(c.tag)
            if tag == "members":
                members = [b.get("name", "") for b in _children(c)]
            elif tag == "distribution":
                kids = _children(c)
                distribution = kids[0] if kids else None
        value = self.value_of(distribution, path)
        for member in members:
            name = self.events.register(member, path, private=False)
            if name in self.model.basic_events:
                continue
            self.model.basic_events[name] = BasicEvent(
                name, f"Member of common-cause group {group} ({model})",
                probability=value.probability, uncertainty=value.uncertainty,
                attributes={"ccf_group": group, "ccf_model": model})
        self.log.group("Common-cause groups are not expanded ({names}): their members are kept as "
                       "independent basic events with the group's total probability, so the "
                       "common-cause contribution is left out.", group)

    def _top(self, tree: str, gates: List[str]) -> Optional[str]:
        own = [g for g in gates if g not in self.sub_gates]
        used = {i.lstrip("/") for g in gates for i in self.model.gates[g].inputs}
        tops = [g for g in own if g not in used]
        if len(tops) > 1:
            self.log.group("Fault trees with several top gates use the first as their top: {names}.",
                           f"{tree} ({', '.join(tops[:4])})")
        return tops[0] if tops else (own[0] if own else None)

    def _initiating_event(self, el: ET.Element) -> None:
        name = el.get("name", "")
        frequency = None
        for c in _body(el):
            tag = _local(c.tag)
            if tag == "parameter":
                frequency = self.value_of(c, ()).probability
            elif tag == "basic-event":
                be = self.model.basic_events.get(self.event_ref(c.get("name", ""), ()))
                frequency = be.probability if be else None
        self.model.initiating_events[name] = BasicEvent(name, _label(el), probability=frequency)
        target = el.get("event-tree")
        if target:
            self._ie_for_tree.setdefault(target, name)

    def _event_tree(self, el: ET.Element) -> None:
        name = el.get("name", "")
        et = EventTree(name, description=_label(el))
        et.initiating_event = self._ie_for_tree.get(name)
        sequences: Dict[str, ET.Element] = {}
        branches: Dict[str, ET.Element] = {}
        initial = None
        for c in _children(el):
            tag = _local(c.tag)
            if tag == "define-functional-event":
                fe = c.get("name", "")
                et.functional_events.append(fe)
                if _label(c):
                    et.functional_event_descriptions[fe] = _label(c)
            elif tag == "define-sequence":
                sequences[c.get("name", "")] = c
                self.model.end_states.setdefault(c.get("name", ""), _label(c))
            elif tag == "define-branch":
                branches[c.get("name", "")] = c
            elif tag == "initial-state":
                initial = c
        if initial is None:
            self.log.group("Event trees without an initial state have no sequences: {names}.", name)
            self.model.event_trees[name] = et
            return

        found: List[Sequence] = []

        def apply(instr: ET.Element, logic: List[str], depth: int) -> Optional[str]:
            """Fold one instruction into the path's logic; returns a transfer target."""
            tag = _local(instr.tag)
            if depth > MAX_DEPTH:
                raise PraFormatError(f"Event tree {name} nests instructions more than {MAX_DEPTH} deep.")
            if tag == "collect-formula":
                kids = _children(instr)
                ref = self._simple_literal(kids[0]) if kids else None
                if ref is None:
                    self.log.group("Sequence logic that is not a single event is not kept "
                                   "(event trees {names}).", name)
                else:
                    logic.append(ref)
            elif tag == "event-tree":
                return instr.get("name")
            elif tag == "rule":
                rule = self.rules.get(instr.get("name", ""))
                target = None
                for r in _body(rule) if rule is not None else []:
                    target = apply(r, logic, depth + 1) or target
                return target
            elif tag == "block":
                target = None
                for r in _children(instr):
                    target = apply(r, logic, depth + 1) or target
                return target
            elif tag == "collect-expression":
                self.log.group("Path probabilities (collect-expression) are not kept (event trees "
                               "{names}).", name)
            elif tag == "if":
                self.log.group("Conditional instructions (<if>) are not modelled (event trees "
                               "{names}).", name)
            elif tag.startswith("set-"):
                self.log.group("Changes made on event tree paths (<set-house-event> and similar) "
                               "are not modelled (event trees {names}).", name)
            return None

        def walk(branch: ET.Element, steps: List[SequencePathStep], logic: List[str],
                 transfer: Optional[str], depth: int, via: Tuple[str, ...]) -> None:
            if len(found) >= MAX_PATHS_PER_EVENT_TREE:
                return
            if depth > MAX_DEPTH:
                raise PraFormatError(f"Event tree {name} nests branches more than {MAX_DEPTH} deep.")
            for c in _children(branch):
                tag = _local(c.tag)
                if tag == "fork":
                    fe = c.get("functional-event", "")
                    for p in _children(c):
                        if _local(p.tag) != "path":
                            continue
                        state = p.get("state", "")
                        if state.lower() in ("success", "failure"):
                            state = state.lower()
                        walk(p, steps + [SequencePathStep(fe, state)], list(logic), transfer,
                             depth + 1, via)
                    return
                if tag == "sequence":
                    seq_name = c.get("name", "")
                    seq_logic, seq_transfer = list(logic), transfer
                    definition = sequences.get(seq_name)
                    for instr in _body(definition) if definition is not None else []:
                        seq_transfer = apply(instr, seq_logic, depth + 1) or seq_transfer
                    found.append(Sequence(seq_name, end_state=seq_name, path=steps, logic=seq_logic,
                                          description=_label(definition) if definition is not None else "",
                                          transfer=seq_transfer))
                    return
                if tag == "branch":
                    ref = c.get("name", "")
                    target = branches.get(ref)
                    if target is None or ref in via:
                        self.log.group("Event tree branches that are missing or loop are cut short: "
                                       "{names}.", f"{name}/{ref}")
                        return
                    walk(target, steps, logic, transfer, depth + 1, via + (ref,))
                    return
                if tag not in ("label", "attributes"):
                    transfer = apply(c, logic, depth) or transfer

        walk(initial, [], [], None, 0, ())
        if len(found) >= MAX_PATHS_PER_EVENT_TREE:
            self.log.group(f"Event trees with more than {MAX_PATHS_PER_EVENT_TREE:,} paths are cut "
                           "short: {names}.", name)
        counts: Dict[str, int] = {}
        for s in found:
            counts[s.name] = counts.get(s.name, 0) + 1
        seen: Dict[str, int] = {}
        for s in found:
            if counts[s.name] > 1:
                seen[s.name] = seen.get(s.name, 0) + 1
                s.name = f"{s.name}-{seen[s.name]}"
        et.sequences = found
        if not et.functional_events:
            et.functional_events = list(dict.fromkeys(st.functional_event for s in found for st in s.path))
        self.model.event_trees[name] = et

    def _simple_literal(self, el: ET.Element) -> Optional[str]:
        tag = _local(el.tag)
        if tag in _EVENT_TAGS:
            return self.event_ref(el.get("name", ""), ())
        kids = _children(el)
        if tag == "not" and len(kids) == 1:
            inner = self._simple_literal(kids[0])
            if inner is not None:
                return inner[1:] if inner.startswith("/") else "/" + inner
        return None

    def _report(self, root: ET.Element) -> None:
        tops = {ft.top: ft.name for ft in self.model.fault_trees.values() if ft.top}
        sequence_trees = {s.name: et.name for et in self.model.event_trees.values() for s in et.sequences}
        for sop in root.iter():
            if _local(sop.tag) != "sum-of-products":
                continue
            label = sop.get("name", "")
            cut_sets: List[List[str]] = []
            for product in _children(sop):
                if _local(product.tag) != "product":
                    continue
                events = []
                for lit in _children(product):
                    ref = self._report_literal(lit)
                    if ref:
                        events.append(ref)
                cut_sets.append(events)
            if label in sequence_trees:
                entry = CutSetList(label, kind="sequence", event_tree=sequence_trees[label])
            else:
                entry = CutSetList(tops.get(label, label), kind="fault_tree")
            entry.cut_sets = cut_sets
            entry.source = "model"
            self.model.cut_sets.append(entry)

    @staticmethod
    def _report_literal(el: ET.Element) -> Optional[str]:
        tag = _local(el.tag)
        if tag == "basic-event":
            return el.get("name")
        if tag == "not":
            kids = _children(el)
            if kids and _local(kids[0].tag) == "basic-event":
                return "/" + kids[0].get("name", "")
        return None


def read_mef(sources: List[Source]) -> PraModel:
    """One model from one or more MEF files (a model may be split across files)."""
    if not sources:
        raise PraFormatError("No Open-PSA files were given.")
    check_size(sources)
    builder = _Builder([name for name, _ in sources])
    for name, data in sources:
        builder.add_root(name, parse_root(name, data))
    model = builder.build()
    if not any((model.gates, model.basic_events, model.house_events, model.event_trees, model.cut_sets)):
        raise PraFormatError("These Open-PSA files define no fault trees, events, event trees or cut sets.")
    return model
