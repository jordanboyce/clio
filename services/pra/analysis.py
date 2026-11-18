"""Screening calculations over a PRA model: point probabilities, cut set
quantification, importance measures and cut set generation.

Pure functions over ``services.pra.model.PraModel``: no I/O, no globals. The
numbers are a screening estimate from the cut sets; the PRA tool's own
quantification (SAPHIRE, CAFTA, RiskSpectrum, SCRAM, XFTA...) governs.

Conventions used throughout:

- A cut set is a list of event names; ``"/X"`` is the complement of ``X``
  (its success), with probability ``1 - p(X)``.
- A cut set's probability is the product of its events' probabilities.
- Totals are given two ways: the rare-event approximation ``sum(P_j)`` and
  the minimal cut set upper bound (MCUB) ``1 - prod(1 - P_j)``.
- Importance measures are all evaluated on the MCUB (see ``importance``),
  unless the cut sets carry a frequency (an initiating event, as sequence
  cut sets do) or a value over 1, where the MCUB is undefined and the
  rare-event sum is used instead; the result says which.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from services.pra.model import PraModel

DEFAULT_TRUNCATION = 1e-15
DEFAULT_MAX_CUT_SETS = 50_000
DEFAULT_TIME_BUDGET_SECONDS = 30.0
# A k-out-of-n gate expands into C(n, k) branches; past this it is refused
# rather than allowed to explode.
MAX_ATLEAST_COMBINATIONS = 10_000
# Deeper than any real fault tree; a deeper chain is a malformed model.
MAX_LOGIC_DEPTH = 5000

BASIS_MCUB = "mcub"
SUCCESS_TERMS_DROP = "drop"
SUCCESS_TERMS_KEEP = "keep"
SUCCESS_TERMS = (SUCCESS_TERMS_DROP, SUCCESS_TERMS_KEEP)
BASIS_RARE_EVENT = "rare_event"


class AnalysisError(ValueError):
    """The logic cannot be analysed: unknown top, a cycle, an oversized gate."""


def split_name(name: str) -> Tuple[str, bool]:
    """``"/X"`` -> ``("X", True)``; ``"X"`` -> ``("X", False)``."""
    if name.startswith("/"):
        return name[1:], True
    return name, False


# ── point probabilities ────────────────────────────────────────────────────

def event_probability(model: PraModel, name: str) -> Optional[float]:
    """The point probability of one event, or None when the model has none.

    A basic event's ``probability``; a house event is 1 when true and 0 when
    false; an initiating event's frequency (it can exceed 1); ``"/X"`` is
    ``1 - p(X)``. Anything else (a gate, an undefined name, a basic event
    without a value) is None, and callers report it as missing data.
    """
    bare, negated = split_name(name)
    p: Optional[float] = None
    if bare in model.basic_events:
        p = model.basic_events[bare].probability
    elif bare in model.house_events:
        p = 1.0 if model.house_events[bare].state else 0.0
    elif bare in model.initiating_events:
        p = model.initiating_events[bare].probability
    if p is None:
        return None
    p = float(p)
    if math.isnan(p):
        return None
    return 1.0 - p if negated else p


# ── quantifying a cut set list ─────────────────────────────────────────────

@dataclass
class QuantifiedCutSet:
    events: List[str]
    probability: Optional[float]     # None when an event lacks data
    missing: List[str] = field(default_factory=list)


@dataclass
class Quantification:
    cut_sets: List[QuantifiedCutSet]  # most probable first; those lacking data last
    rare_event: float                 # sum over the cut sets with data
    mcub: Optional[float]             # 1 - prod(1 - P); None for frequencies or values over 1
    missing_events: List[str]         # events with no probability, sorted
    incomplete_cut_sets: int          # cut sets left out of the totals for lack of data

    @property
    def basis(self) -> str:
        return BASIS_MCUB if self.mcub is not None else BASIS_RARE_EVENT

    @property
    def total(self) -> float:
        return self.mcub if self.mcub is not None else self.rare_event


def _unique(events: Iterable[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for e in events:
        seen.setdefault(e, None)
    return list(seen)


def is_frequency(model: PraModel, name: str) -> bool:
    """An initiating event: its value is a frequency (per year), not a probability."""
    bare = split_name(name)[0]
    return bare in model.initiating_events and bare not in model.basic_events


def _mcub(values: Iterable[float]) -> Optional[float]:
    """1 - prod(1 - v), computed in log space so tiny values keep precision."""
    s = 0.0
    for v in values:
        if v > 1.0:
            return None
        if v >= 1.0:
            return 1.0
        s += math.log1p(-v)
    return -math.expm1(s) + 0.0  # never -0.0


def quantify_cut_sets(model: PraModel, cut_sets: Sequence[Sequence[str]]) -> Quantification:
    """Probability of each cut set and of the list, most probable first.

    Each cut set's probability is the product of its (distinct) events'
    point probabilities (``event_probability``). A cut set with any event
    lacking data has probability None, is listed after the others and is
    left out of both totals, which are then lower bounds; the events are
    named in ``missing_events``.
    """
    cache: Dict[str, Optional[float]] = {}
    rows: List[QuantifiedCutSet] = []
    missing: Dict[str, None] = {}
    for cs in cut_sets:
        events = _unique(cs)
        prob: Optional[float] = 1.0
        lacking: List[str] = []
        for e in events:
            if e not in cache:
                cache[e] = event_probability(model, e)
            p = cache[e]
            if p is None:
                lacking.append(e)
                missing.setdefault(split_name(e)[0], None)
            elif prob is not None:
                prob *= p
        if lacking:
            prob = None
        rows.append(QuantifiedCutSet(events, prob, lacking))
    rows.sort(key=lambda r: (r.probability is None, -(r.probability or 0.0)))
    known = [r.probability for r in rows if r.probability is not None]
    return Quantification(
        cut_sets=rows,
        rare_event=math.fsum(known),
        mcub=None if any(is_frequency(model, e) for r in rows for e in r.events) else _mcub(known),
        missing_events=sorted(missing),
        incomplete_cut_sets=len(rows) - len(known),
    )


# ── importance ─────────────────────────────────────────────────────────────

@dataclass
class ImportanceRow:
    event: str
    probability: Optional[float]
    cut_sets: int                     # how many cut sets (with data) contain it, either polarity
    fussell_vesely: Optional[float]
    raw: Optional[float]
    rrw: Optional[float]              # None when unbounded (F with p=0 is 0) or undefined
    birnbaum: Optional[float]


@dataclass
class ImportanceResult:
    rows: List[ImportanceRow]
    basis: str                        # BASIS_MCUB or BASIS_RARE_EVENT
    total: float                      # F, on that basis
    incomplete_cut_sets: int
    missing_events: List[str]


class _TotalModel:
    """F(p) over a fixed cut set list, with fast re-evaluation for one event
    set to a new value. Cut sets with missing data are left out."""

    def __init__(self, factors: List[List[Tuple[str, bool, float]]], basis: str):
        self.factors = factors
        self.basis = basis
        self.values = [math.prod(1.0 - p if neg else p for _, neg, p in f) for f in factors]
        if basis == BASIS_MCUB:
            self.certain = sum(1 for v in self.values if v >= 1.0)
            self.logs = [math.log1p(-v) if v < 1.0 else 0.0 for v in self.values]
            self.s = math.fsum(self.logs)
            self.total = 1.0 if self.certain else -math.expm1(self.s)
        else:
            self.total = math.fsum(self.values)

    def _value_with(self, j: int, event: str, value: float) -> float:
        out = 1.0
        for name, neg, p in self.factors[j]:
            q = value if name == event else p
            out *= (1.0 - q) if neg else q
        return out

    def delta(self, event: str, affected: List[int], value: float) -> float:
        """F(p_event = value) - F, without the cancellation of subtracting two
        nearly equal totals."""
        if self.basis != BASIS_MCUB:
            return math.fsum(self._value_with(j, event, value) - self.values[j] for j in affected)
        certain = self.certain
        ds = 0.0
        for j in affected:
            new = self._value_with(j, event, value)
            if self.values[j] >= 1.0:
                certain -= 1
            else:
                ds -= self.logs[j]
            if new >= 1.0:
                certain += 1
            else:
                ds += math.log1p(-new)
        if self.certain == 0 and certain == 0:
            return -math.exp(self.s) * math.expm1(ds)
        new_total = 1.0 if certain else -math.expm1(self.s + ds)
        return new_total - self.total


def importance(model: PraModel, cut_sets: Sequence[Sequence[str]], basis: Optional[str] = None) -> ImportanceResult:
    """Importance of every event in a cut set list.

    ``F`` is the top (or sequence) value from the cut sets on one basis: the
    minimal cut set upper bound ``F = 1 - prod_j (1 - P_j)``, consistently,
    for every measure; only when the cut sets carry a frequency (an
    initiating event) or a value over 1 is the rare-event sum ``F = sum_j P_j`` used, and ``basis`` says so.
    ``F(p_i = x)`` is F re-evaluated with event i's probability set to x (a
    complemented occurrence ``/i`` then counts ``1 - x``). For event i:

    - Fussell-Vesely ``FV = (F - F(p_i=0)) / F``: the fraction of the risk
      that involves i.
    - Risk Achievement Worth ``RAW = F(p_i=1) / F``: how much F rises if i
      is certain to occur.
    - Risk Reduction Worth ``RRW = F / F(p_i=0)``: how much F falls if i
      never occurs; None when ``F(p_i=0) = 0`` (i is in every cut set, so
      the worth is unbounded).
    - Birnbaum ``B = F(p_i=1) - F(p_i=0)``: the sensitivity dF/dp_i.

    ``basis`` forces BASIS_RARE_EVENT (for comparison with a tool that uses
    it); BASIS_MCUB is refused when a cut set value exceeds 1.

    FV, RAW and RRW are None when F is 0. Events without a probability get
    None everywhere; cut sets containing them are left out of F
    (``incomplete_cut_sets``), as in ``quantify_cut_sets``.
    """
    cache: Dict[str, Optional[float]] = {}
    factors: List[List[Tuple[str, bool, float]]] = []
    missing: Dict[str, None] = {}
    incomplete = 0
    seen_events: Dict[str, None] = {}
    for cs in cut_sets:
        row: List[Tuple[str, bool, float]] = []
        complete = True
        for e in _unique(cs):
            bare, neg = split_name(e)
            seen_events.setdefault(bare, None)
            if bare not in cache:
                cache[bare] = event_probability(model, bare)
            p = cache[bare]
            if p is None:
                complete = False
                missing.setdefault(bare, None)
                continue
            row.append((bare, neg, p))
        if complete:
            factors.append(row)
        else:
            incomplete += 1

    over_one = any(math.prod(1.0 - p if n else p for _, n, p in f) > 1.0 for f in factors) or any(
        is_frequency(model, name) for name in seen_events)
    if basis is None:
        basis = BASIS_RARE_EVENT if over_one else BASIS_MCUB
    elif basis not in (BASIS_MCUB, BASIS_RARE_EVENT):
        raise AnalysisError(f"basis must be '{BASIS_MCUB}' or '{BASIS_RARE_EVENT}'")
    elif basis == BASIS_MCUB and over_one:
        raise AnalysisError("A cut set value exceeds 1 (a frequency): the MCUB is undefined, use the rare-event basis")
    tm = _TotalModel(factors, basis)
    F = tm.total

    affected: Dict[str, List[int]] = {}
    for j, f in enumerate(factors):
        for name in {name for name, _, _ in f}:
            affected.setdefault(name, []).append(j)

    rows: List[ImportanceRow] = []
    for name in seen_events:
        p = cache.get(name)
        idx = affected.get(name, [])
        if p is None:
            rows.append(ImportanceRow(name, None, len(idx), None, None, None, None))
            continue
        d0 = tm.delta(name, idx, 0.0)
        d1 = tm.delta(name, idx, 1.0)
        f0 = max(0.0, F + d0)
        f1 = max(0.0, F + d1)
        fv = raw = rrw = None
        if F > 0:
            fv = -d0 / F
            raw = f1 / F
            rrw = F / f0 if f0 > 0 else None
        rows.append(ImportanceRow(name, p, len(idx), fv, raw, rrw, d1 - d0))
    return ImportanceResult(rows, basis, F, incomplete, sorted(missing))


IMPORTANCE_SORT_KEYS = {
    "fv": "fussell_vesely",
    "raw": "raw",
    "rrw": "rrw",
    "birnbaum": "birnbaum",
}


def sort_importance(rows: List[ImportanceRow], sort: str = "fv") -> List[ImportanceRow]:
    """Largest first by one measure; None last, except an unbounded RRW,
    which is the largest RRW there is."""
    attr = IMPORTANCE_SORT_KEYS[sort]

    def key(r: ImportanceRow):
        v = getattr(r, attr)
        if v is None:
            unbounded = attr == "rrw" and r.probability is not None and r.fussell_vesely is not None
            return (0, 0.0) if unbounded else (2, 0.0)
        return (1, -v)

    return sorted(rows, key=key)


# ── cut set generation ─────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    target: str
    top_gate: Optional[str]
    cut_sets: List[List[str]]        # minimal, most probable first ("/X" = success term)
    truncation: float                # the cutoff in force at the end (may be raised, see below)
    requested_truncation: float
    max_order: Optional[int]
    complete: bool                   # False: stopped by the time budget; the list is partial
    stopped: Optional[str]           # "time_budget" | None
    truncation_raised: bool          # max_cut_sets reached: cutoff raised to keep the most probable
    non_coherent: bool
    warnings: List[str]
    stats: Dict[str, float]


_AND, _OR, _ATLEAST = 0, 1, 2
# A compiled item: (kind, value). kind 0: constant (value bool); 1: literal
# (value = 2 * event index + negated); 2: node (value = node index).
_CONST, _LIT, _NODE = 0, 1, 2
_TRUE = (_CONST, True)
_FALSE = (_CONST, False)


class _Compiler:
    """Fault tree logic -> an and/or/k-of-n graph over literals, with negation
    pushed down to the events (De Morgan), house events folded in as
    constants, transfers resolved, and shared gates compiled once."""

    def __init__(self, model: PraModel):
        self.model = model
        self.events: List[str] = []
        self.event_index: Dict[str, int] = {}
        self.types: List[int] = []
        self.ks: List[int] = []
        self.lits: List[Tuple[int, ...]] = []
        self.kids: List[Tuple[int, ...]] = []
        self.memo: Dict[Tuple[str, bool], Tuple[int, object]] = {}
        self.warnings: Dict[str, None] = {}
        self.negative_literals = False
        self.undeveloped: Dict[str, None] = {}

    def warn(self, text: str) -> None:
        self.warnings.setdefault(text, None)

    def literal(self, name: str, negated: bool) -> Tuple[int, int]:
        idx = self.event_index.get(name)
        if idx is None:
            idx = self.event_index[name] = len(self.events)
            self.events.append(name)
        if negated:
            self.negative_literals = True
        return (_LIT, 2 * idx + int(negated))

    def node(self, kind: int, items: List[Tuple[int, object]], k: int = 0) -> Tuple[int, object]:
        """Make a node, folding constants and trivial shapes."""
        if kind == _ATLEAST:
            trues = sum(1 for it in items if it == _TRUE)
            items = [it for it in items if it[0] != _CONST]
            k -= trues
            if k <= 0:
                return _TRUE
            if k > len(items):
                return _FALSE
            if k == 1:
                kind = _OR
            elif k == len(items):
                kind = _AND
        if kind == _AND:
            if any(it == _FALSE for it in items):
                return _FALSE
            items = [it for it in items if it[0] != _CONST]
            if not items:
                return _TRUE
        elif kind == _OR:
            if any(it == _TRUE for it in items):
                return _TRUE
            items = [it for it in items if it[0] != _CONST]
            if not items:
                return _FALSE
        lits = tuple(dict.fromkeys(v for t, v in items if t == _LIT))
        kids = tuple(dict.fromkeys(v for t, v in items if t == _NODE))
        if kind != _ATLEAST and len(lits) + len(kids) == 1:
            return items[0]
        if kind == _ATLEAST and len(lits) + len(kids) != len(items):
            # A repeated input counts once per occurrence in k-of-n; keep the
            # semantics simple and exact by refusing the shortcut.
            lits = tuple(v for t, v in items if t == _LIT)
            kids = tuple(v for t, v in items if t == _NODE)
        if kind != _ATLEAST and any(l ^ 1 in lits for l in lits):
            # X and /X in one AND is impossible; in one OR it is always true.
            return _FALSE if kind == _AND else _TRUE
        self.types.append(kind)
        self.ks.append(k)
        self.lits.append(lits)
        self.kids.append(kids)
        return (_NODE, len(self.types) - 1)

    def compile(self, name: str, negated: bool = False) -> Tuple[int, object]:
        """The compiled item for ``name`` (``"/X"`` = complemented).

        Depth first without recursion: a key is built once every key it
        depends on is built, so a deep chain of transfers cannot exhaust the
        interpreter's stack. A key met again while one of its ancestors is
        still being built is a cycle."""
        bare, flip = split_name(name)
        root = (bare, negated ^ flip)
        if root in self.memo:
            return self.memo[root]
        work: List[Tuple[Tuple[str, bool], bool]] = [(root, False)]
        on_path: Dict[str, int] = {}
        while work:
            key, ready = work.pop()
            if ready:
                on_path[key[0]] -= 1
                if not on_path[key[0]]:
                    del on_path[key[0]]
                self.memo[key] = self._compile_name(*key)
                continue
            if key in self.memo:
                continue
            if key[0] in on_path:
                raise AnalysisError(f"The logic has a cycle through '{key[0]}'")
            if len(on_path) >= MAX_LOGIC_DEPTH:
                raise AnalysisError(f"The logic under '{root[0]}' is nested deeper than {MAX_LOGIC_DEPTH} levels")
            on_path[key[0]] = on_path.get(key[0], 0) + 1
            work.append((key, True))
            work.extend((d, False) for d in reversed(self._deps(*key)) if d not in self.memo)
        return self.memo[root]

    def _transfer_top(self, gate) -> Tuple[str, Optional[str]]:
        """(target tree or gate, the gate it resolves to or None)."""
        m = self.model
        target = split_name(gate.inputs[0] if gate.inputs else (gate.tree or ""))[0]
        top = m.top_gate(target) if target in m.fault_trees else (target if target in m.gates else None)
        return target, (top if top and top != gate.name else None)

    def _deps(self, bare: str, negated: bool) -> List[Tuple[str, bool]]:
        """The keys ``_compile_name(bare, negated)`` compiles; it may then
        call ``compile`` only for these, and finds each built."""
        def key(name: str, neg: bool) -> Tuple[str, bool]:
            b, f = split_name(name)
            return (b, neg ^ f)

        m = self.model
        gate = m.gates.get(bare)
        if gate is not None:
            if gate.type == "transfer":
                top = self._transfer_top(gate)[1]
                return [(top, negated)] if top else []
            if gate.type in ("and", "or", "atleast"):
                return [key(i, negated) for i in gate.inputs]
            if gate.type in ("nand", "nor", "not"):
                return [key(i, not negated) for i in gate.inputs]
            if gate.type == "xor":
                return [key(i, pol) for i in gate.inputs for pol in (False, True)]
            return []
        if bare in m.gates or bare in m.house_events or bare in m.basic_events or bare in m.initiating_events:
            return []
        if bare in m.fault_trees:
            top = m.top_gate(bare)
            return [(top, negated)] if top and top != bare else []
        return []

    def _undeveloped(self, bare: str, negated: bool, why: str) -> Tuple[int, int]:
        self.undeveloped.setdefault(bare, None)
        self.warn(why)
        return self.literal(bare, negated)

    def _compile_name(self, bare: str, negated: bool) -> Tuple[int, object]:
        m = self.model
        gate = m.gates.get(bare)
        if gate is not None:
            if gate.type == "transfer":
                target, top = self._transfer_top(gate)
                if not top:
                    return self._undeveloped(bare, negated, f"Transfer '{bare}' to '{target}' could not be "
                                             "resolved; it is treated as an undeveloped event")
                return self.compile(top, negated)
            return self._compile_gate(gate, negated)
        if bare in m.house_events:
            return _TRUE if m.house_events[bare].state != negated else _FALSE
        if bare in m.basic_events or bare in m.initiating_events:
            return self.literal(bare, negated)
        if bare in m.fault_trees:
            top = m.top_gate(bare)
            if not top or top == bare:
                return self._undeveloped(bare, negated, f"Fault tree '{bare}' has no top gate; it is "
                                         "treated as an undeveloped event")
            return self.compile(top, negated)
        return self._undeveloped(bare, negated, f"'{bare}' is not defined in the model; it is treated "
                                 "as an undeveloped event without data")

    def _compile_gate(self, gate, negated: bool) -> Tuple[int, object]:
        t = gate.type
        inputs = list(gate.inputs)
        if not inputs:
            return self._undeveloped(gate.name, negated, f"Gate '{gate.name}' has no inputs; it is "
                                     "treated as an undeveloped event")

        def kids(neg: bool) -> List[Tuple[int, object]]:
            return [self.compile(i, neg) for i in inputs]

        if t == "and":
            return self.node(_OR, kids(True)) if negated else self.node(_AND, kids(False))
        if t == "or":
            return self.node(_AND, kids(True)) if negated else self.node(_OR, kids(False))
        if t == "nand":
            return self.node(_AND, kids(False)) if negated else self.node(_OR, kids(True))
        if t == "nor":
            return self.node(_OR, kids(False)) if negated else self.node(_AND, kids(True))
        if t == "not":
            if len(inputs) != 1:
                self.warn(f"NOT gate '{gate.name}' has {len(inputs)} inputs; it is read as NOR")
                return self.node(_OR, kids(False)) if negated else self.node(_AND, kids(True))
            return self.compile(inputs[0], not negated)
        if t == "atleast":
            n = len(inputs)
            k = gate.k if gate.k is not None else 1
            if negated:
                k = n - k + 1
            if k > 0 and math.comb(n, min(k, n)) > MAX_ATLEAST_COMBINATIONS:
                raise AnalysisError(f"Gate '{gate.name}' ({k} of {n}) has more than "
                                    f"{MAX_ATLEAST_COMBINATIONS} combinations")
            return self.node(_ATLEAST, kids(negated), k)
        if t == "xor":
            # Associative: fold pairwise, carrying both polarities.
            pos, neg = self.compile(inputs[0], False), self.compile(inputs[0], True)
            for other in inputs[1:]:
                op, on = self.compile(other, False), self.compile(other, True)
                pos, neg = (
                    self.node(_OR, [self.node(_AND, [pos, on]), self.node(_AND, [neg, op])]),
                    self.node(_OR, [self.node(_AND, [pos, op]), self.node(_AND, [neg, on])]),
                )
            return neg if negated else pos
        return self._undeveloped(gate.name, negated, f"Gate '{gate.name}' has type '{t}', which cannot "
                                 "be analysed; it is treated as an undeveloped event")


class _SubsetIndex:
    """Sets of literals, answering "does any stored set lie inside this one?".

    Each stored set is filed under one of its own literals; a stored M inside
    a query Q is filed under some literal of Q, so the query only looks in
    the buckets of its own literals. Filing under the literal whose bucket is
    currently smallest keeps the buckets even."""

    def __init__(self) -> None:
        self.buckets: Dict[int, List[frozenset]] = {}
        self.has_empty = False

    def clear(self) -> None:
        self.buckets.clear()
        self.has_empty = False

    def add(self, cs: frozenset) -> None:
        if not cs:
            self.has_empty = True
            return
        buckets = self.buckets
        key = min(cs, key=lambda l: len(buckets.get(l, ())))
        buckets.setdefault(key, []).append(cs)

    def covers(self, q: frozenset) -> bool:
        if self.has_empty:
            return True
        buckets = self.buckets
        for l in q:
            bucket = buckets.get(l)
            if bucket:
                for m in bucket:
                    if m <= q:
                        return True
        return False


def _resolve_target(model: PraModel, target: str) -> str:
    """A fault tree's top gate, or the gate itself."""
    top = model.top_gate(target)
    if top and top in model.gates:
        return top
    if target in model.gates:
        return target
    if target in model.fault_trees:
        raise AnalysisError(f"Fault tree '{target}' has no top gate")
    raise AnalysisError(f"'{target}' is neither a fault tree nor a gate in this model")




class _Graph:
    """The compiled logic plus what the search needs per node: the events it
    can touch (a bitset), the largest probability any of its cut sets can
    have, and the fewest events any of its cut sets can have."""

    def __init__(self, model: PraModel, comp: _Compiler, drop: bool):
        self.comp = comp
        self.names = comp.events
        self.types, self.ks, self.lits, self.kids = comp.types, comp.ks, comp.lits, comp.kids
        self.drop = drop and comp.negative_literals
        # Literal probabilities for the bounds: an event without data counts
        # as 1 (it can never justify dropping a row), and so does a success
        # term that will be deleted (a product is truncated as it is reported).
        lit_p = [1.0] * (2 * len(self.names))
        for i, name in enumerate(self.names):
            p = event_probability(model, name)
            if p is not None:
                p = min(1.0, max(0.0, p))
                lit_p[2 * i] = p
                if not drop:
                    lit_p[2 * i + 1] = 1.0 - p
        self.lit_p = lit_p

        n_nodes = len(self.types)
        self.sup = sup = [0] * n_nodes
        self.ub = ub = [1.0] * n_nodes
        self.minord = minord = [1] * n_nodes
        for n in range(n_nodes):  # children are always numbered before parents
            parts = [(lit_p[l], 1 << (l >> 1), 0 if drop and l & 1 else 1) for l in self.lits[n]]
            parts += [(ub[c], sup[c], minord[c]) for c in self.kids[n]]
            s = 0
            for _, ps, _ in parts:
                s |= ps
            sup[n] = s
            kind = self.types[n]
            if kind == _OR:
                ub[n] = max(u for u, _, _ in parts)
                minord[n] = min(o for _, _, o in parts)
            elif kind == _AND:
                # A product over inputs with disjoint events is a valid bound;
                # inputs sharing events may share the same cut set events.
                acc, bound, order = 0, 1.0, 0
                for u, ps, o in sorted(parts, key=lambda x: x[0]):
                    if ps & acc == 0:
                        bound *= u
                        order += o
                        acc |= ps
                ub[n] = bound
                minord[n] = max(order, max(o for _, _, o in parts))
            else:
                k = self.ks[n]
                if sum(ps.bit_count() for _, ps, _ in parts) == s.bit_count():  # inputs disjoint
                    ub[n] = math.prod(sorted((u for u, _, _ in parts), reverse=True)[:k])
                    minord[n] = sum(sorted(o for _, _, o in parts)[:k])
                else:
                    ub[n] = max(u for u, _, _ in parts)
                    minord[n] = min(o for _, _, o in parts)

    def literal_name(self, lit: int) -> str:
        return ("/" if lit & 1 else "") + self.names[lit >> 1]

    def width(self, n: int) -> int:
        size = len(self.lits[n]) + len(self.kids[n])
        return size if self.types[n] == _OR else math.comb(size, self.ks[n])


def _minimise(cands: Dict[frozenset, float]) -> Dict[frozenset, float]:
    """Drop every set that contains another (subset absorption)."""
    kept: Dict[frozenset, float] = {}
    idx = _SubsetIndex()
    for cs in sorted(cands, key=len):
        if not idx.covers(cs):
            kept[cs] = cands[cs]
            idx.add(cs)
    return kept


def _keep_most_probable(pool: Dict[frozenset, float], cutoff: float, limit: int) -> Tuple[Dict[frozenset, float], float]:
    probs = sorted(pool.values(), reverse=True)
    cutoff = max(cutoff, probs[limit - 1])
    return {cs: p for cs, p in pool.items() if p >= cutoff}, cutoff


@dataclass
class _Pass:
    pool: Dict[frozenset, float]
    cutoff: float
    raised: bool
    finished: bool
    stats: Dict[str, float]


def _search(g: _Graph, root: int, cutoff: float, max_order: Optional[int], max_cut_sets: int,
            deadline: float, clock: Callable[[], float]) -> _Pass:
    """One depth-first MOCUS pass at one cutoff; stops at ``deadline``."""
    types, ks, nlits, nkids = g.types, g.ks, g.lits, g.kids
    sup, ub, minord, lit_p = g.sup, g.ub, g.minord, g.lit_p
    count_positive = g.drop
    raised = False
    pool: Dict[frozenset, float] = {}
    index = _SubsetIndex()
    adds = 0  # sets ever added to the pool: a "not absorbed" check stays good while unchanged
    stats = {"rows": 0, "dropped_by_truncation": 0, "dropped_by_order": 0, "absorbed": 0}

    def add(row_lits: frozenset, supp: int, prob: float, new_lits: Iterable[int]):
        """The row with more literals (the same frozenset when none is new),
        or None when one contradicts a literal already there."""
        extra: List[int] = []
        for l in new_lits:
            if l in row_lits or l in extra:
                continue
            if l ^ 1 in row_lits or l ^ 1 in extra:
                return None
            extra.append(l)
            supp |= 1 << (l >> 1)
            prob *= lit_p[l]
        if not extra:
            return row_lits, supp, prob
        return row_lits.union(extra), supp, prob

    # A row: (literals, event bitset, probability, pending nodes, expanded
    # nodes, the value of `adds` when its literals were last found not absorbed).
    stack: List[Tuple[frozenset, int, float, Tuple[int, ...], frozenset, int]] = [
        (frozenset(), 0, 1.0, (root,), frozenset(), -1)]
    while stack:
        stats["rows"] += 1
        if clock() > deadline:  # every row: one row can be costly in deep k-of-n chains
            return _Pass(pool, cutoff, raised, False, stats)
        lits, supp, prob, pending, done, checked = stack.pop()
        popped_lits = lits

        # Expand every AND in the row and drop the gates it already satisfies.
        dead = False
        changed = True
        while changed and not dead:
            changed = False
            keep: List[int] = []
            for n in pending:
                if types[n] == _AND:
                    got = add(lits, supp, prob, nlits[n])
                    if got is None:
                        dead = True
                        break
                    lits, supp, prob = got
                    done = done | {n}
                    for c in nkids[n]:
                        if c not in done and c not in keep and c not in pending:
                            keep.append(c)
                    changed = True
                elif n not in keep:
                    keep.append(n)
            if dead:
                break
            pending = tuple(keep)
            if any(types[n] == _AND for n in pending):
                changed = True
                continue
            implied = done.union(pending)
            still: List[int] = []
            for n in pending:
                hits = sum(1 for l in nlits[n] if l in lits) + sum(1 for c in nkids[n] if c in implied and c != n)
                if hits >= (1 if types[n] == _OR else ks[n]):
                    done = done | {n}
                    changed = True
                else:
                    still.append(n)
            pending = tuple(still)
        if dead:
            continue

        order = sum(1 for l in lits if not l & 1) if count_positive else len(lits)
        bound, acc = prob, supp
        for n in pending:
            if sup[n] & acc == 0:
                bound *= ub[n]
                order += minord[n]
                acc |= sup[n]
        if bound < cutoff:
            stats["dropped_by_truncation"] += 1
            continue
        if max_order is not None and order > max_order:
            stats["dropped_by_order"] += 1
            continue
        if lits is not popped_lits or checked != adds:
            if pool and index.covers(lits):
                stats["absorbed"] += 1
                continue
            checked = adds

        if not pending:
            if lits not in pool:
                pool[lits] = prob
                index.add(lits)
                adds += 1
                if len(pool) > 2 * max_cut_sets:
                    pool = _minimise(pool)
                    if len(pool) > max_cut_sets:
                        pool, cutoff = _keep_most_probable(pool, cutoff, max_cut_sets)
                        raised = True
                    index.clear()
                    for cs in pool:
                        index.add(cs)
            continue

        # Branch on the pending gate with the fewest alternatives.
        n = min(pending, key=g.width)
        rest = tuple(x for x in pending if x != n)
        done_n = done | {n}
        if types[n] == _OR:
            options = [((l,), ()) for l in nlits[n]] + [((), (c,)) for c in nkids[n]]
        else:
            # Only the inputs the row does not satisfy yet need choosing.
            implied = done.union(pending)
            members = [((l,), ()) for l in nlits[n] if l not in lits]
            members += [((), (c,)) for c in nkids[n] if c not in implied]
            need = ks[n] - (len(nlits[n]) + len(nkids[n]) - len(members))
            options = [(tuple(l for m in combo for l in m[0]), tuple(c for m in combo for c in m[1]))
                       for combo in itertools.combinations(members, need)]
        branches = []
        for add_lits, add_kids in options:
            got = add(lits, supp, prob, add_lits)
            if got is None:
                continue
            new_lits, new_supp, new_prob = got
            if new_prob < cutoff:
                stats["dropped_by_truncation"] += 1
                continue
            new_pending = rest + tuple(c for c in add_kids if c not in done_n and c not in rest)
            branches.append((new_lits, new_supp, new_prob, new_pending, done_n,
                             checked if new_lits is lits else -1))
        # The most promising branch goes on top of the stack (popped first).
        branches.sort(key=lambda r: r[2] * math.prod(ub[c] for c in r[3] if c not in pending))
        stack.extend(branches)
    return _Pass(pool, cutoff, raised, True, stats)


def _deepening_cutoffs(truncation: float) -> List[float]:
    """Coarse to fine: 1e-3, 1e-5, ... 1e-15, then the requested cutoff."""
    steps = [10.0 ** -e for e in range(3, 16, 2) if 10.0 ** -e > truncation]
    return steps + [truncation]


def generate_cut_sets(
    model: PraModel,
    target: str,
    *,
    truncation: float = DEFAULT_TRUNCATION,
    max_order: Optional[int] = None,
    max_cut_sets: int = DEFAULT_MAX_CUT_SETS,
    time_budget: float = DEFAULT_TIME_BUDGET_SECONDS,
    success_terms: str = SUCCESS_TERMS_DROP,
    clock: Callable[[], float] = time.monotonic,
) -> GenerationResult:
    """Minimal cut sets of a fault tree (by name) or a gate, top-down.

    MOCUS-style: rows of (events so far, gates still to expand) are expanded
    depth first, an AND adding all its inputs to the row and an OR (or k of
    n) branching it; a gate the row already satisfies is dropped, and a row
    containing a cut set already found is absorbed. Shared gates are
    compiled once; negation is pushed down to the events; house events are
    constants (a true one drops out of an AND and satisfies an OR, a false
    one kills an AND and drops out of an OR); a transfer resolves to the
    referenced tree's top gate.

    Guards, all reported in the result:

    - ``truncation``: a row is dropped once an upper bound on any cut set it
      can still produce falls below the cutoff (absolute probability). The
      bound multiplies the row's event probabilities by the largest cut set
      probability of each pending gate whose events are disjoint from the
      row's and from each other, so it never drops a cut set at or above
      the cutoff.
    - ``max_order``: cut sets with more events are not produced.
    - ``max_cut_sets``: when more are found, the cutoff is raised to keep the
      most probable ``max_cut_sets`` (``truncation_raised``).
    - ``time_budget`` (seconds of wall clock): the search runs at cutoffs
      1e-3, 1e-5, ... down to ``truncation``, each pass exact for its cutoff.
      When the budget runs out, the last finished pass is returned
      (``stopped="time_budget"``, ``truncation`` says how far it got); if
      not even the first pass finished, what it found is returned with
      ``complete`` False, a partial list whose totals are lower bounds.

    In every case with ``complete`` True the list holds every minimal cut
    set with probability at or above the returned ``truncation`` (and order
    up to ``max_order``).

    Non-coherent logic (NOT, NAND, NOR, XOR or complemented inputs) is
    pushed down to the events and expanded exactly, so products carry
    success terms ``/X`` and a product with both X and /X is dropped as
    impossible. Then, with ``success_terms="drop"`` (the default, and what
    SCRAM does for minimal cut sets), the ``/X`` terms are deleted from each
    product and the list is minimised again: the coherent approximation, an
    upper bound. With ``"keep"`` the products keep their ``/X`` terms
    (probability ``1 - p``); they are minimised by subset absorption only,
    so they are not necessarily prime implicants and a tautology such as
    ``A + B + /A*/B`` is not recognised. Either way ``non_coherent`` and a
    warning say so.
    """
    if not (0.0 <= truncation <= 1.0):
        raise AnalysisError("truncation must be between 0 and 1")
    if max_order is not None and max_order < 1:
        raise AnalysisError("max_order must be at least 1")
    if max_cut_sets < 1:
        raise AnalysisError("max_cut_sets must be at least 1")
    if success_terms not in SUCCESS_TERMS:
        raise AnalysisError(f"success_terms must be one of {', '.join(SUCCESS_TERMS)}")
    started = clock()
    deadline = started + time_budget
    top_gate = _resolve_target(model, target)
    comp = _Compiler(model)
    root = comp.compile(top_gate)
    warnings = list(comp.warnings)
    non_coherent = comp.negative_literals
    drop = success_terms == SUCCESS_TERMS_DROP
    if non_coherent and drop:
        warnings.append("The logic is non-coherent (NOT/NAND/NOR/XOR or complemented inputs): success "
                        "terms '/X' were deleted from the cut sets (the coherent approximation), so "
                        "totals are an upper bound.")
    elif non_coherent:
        warnings.append("The logic is non-coherent (NOT/NAND/NOR/XOR or complemented inputs): cut sets "
                        "keep success terms '/X' and are minimised by subset absorption only, so totals "
                        "from them are approximate.")
    if comp.undeveloped:
        warnings.append("Events without data in the logic: " + ", ".join(sorted(comp.undeveloped)[:20]))

    def result(cut_sets, cutoff=truncation, complete=True, stopped=None, raised=False, stats=None):
        stats = dict(stats or {"rows": 0})
        stats["elapsed_seconds"] = round(clock() - started, 4)
        return GenerationResult(
            target=target, top_gate=top_gate, cut_sets=cut_sets, truncation=cutoff,
            requested_truncation=truncation, max_order=max_order, complete=complete, stopped=stopped,
            truncation_raised=raised, non_coherent=non_coherent, warnings=warnings, stats=stats,
        )

    if root == _TRUE:
        warnings.append("The top event is certain (its logic reduces to TRUE under the house events).")
        return result([[]])
    if root == _FALSE:
        warnings.append("The top event cannot occur (its logic reduces to FALSE under the house events).")
        return result([])
    graph = _Graph(model, comp, drop)
    if root[0] == _LIT:
        lit = root[1]
        if drop and lit & 1:
            warnings.append("The top event reduces to a success term, which counts as certain once deleted.")
            return result([[]])
        return result([[graph.literal_name(lit)]] if graph.lit_p[lit] >= truncation else [])

    best: Optional[_Pass] = None
    timed_out = False
    totals = {"rows": 0, "dropped_by_truncation": 0, "dropped_by_order": 0, "absorbed": 0, "passes": 0}
    for step in _deepening_cutoffs(truncation):
        run = _search(graph, root[1], step, max_order, max_cut_sets, deadline, clock)
        totals["passes"] += 1
        for k, v in run.stats.items():
            totals[k] += v
        if run.finished or best is None:
            best = run
        if not run.finished:
            timed_out = True
            break
        if run.raised:
            break  # a finer cutoff would only be raised again

    pool = best.pool
    if graph.drop:
        stripped: Dict[frozenset, float] = {}
        for cs, p in pool.items():
            key = frozenset(l for l in cs if not l & 1)
            stripped[key] = max(p, stripped.get(key, 0.0))
        pool = stripped
    pool = _minimise(pool)
    cutoff, raised = best.cutoff, best.raised
    if len(pool) > max_cut_sets:
        pool, cutoff = _keep_most_probable(pool, cutoff, max_cut_sets)
        raised = True
    names = graph.names
    ordered = sorted(pool.items(), key=lambda kv: (-kv[1], len(kv[0]), sorted(kv[0])))
    cut_sets = [[graph.literal_name(l) for l in sorted(cs, key=lambda l: (names[l >> 1], l & 1))]
                for cs, _ in ordered]

    complete = best.finished
    stopped = "time_budget" if timed_out else None
    if not complete:
        warnings.append(f"Stopped after the {time_budget:g} s time budget before the first pass (cutoff "
                        f"{cutoff:.3g}) finished: the list is partial and totals from it are lower bounds.")
    elif stopped:
        warnings.append(f"The {time_budget:g} s time budget ran out: the list is complete down to cutoff "
                        f"{cutoff:.3g}, not the {truncation:.3g} asked for, so totals are lower bounds.")
    if raised:
        warnings.append(f"More than {max_cut_sets} cut sets: the cutoff was raised to {cutoff:.3g} to keep "
                        "the most probable ones.")
    return result(cut_sets, cutoff, complete, stopped, raised, totals)
