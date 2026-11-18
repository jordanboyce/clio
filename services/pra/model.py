"""One in-memory shape for a probabilistic risk assessment (PRA) model.

Every reader (Open-PSA MEF, SAPHIRE MAR-D, and later others) produces a
``PraModel``; every consumer (the text and tables that reach the index, the
cut set and importance calculations, the REST and MCP tools, the viewer)
reads one. Nothing here parses or computes: it is the contract between them.

Names are the model's own identifiers, kept exactly as the tool wrote them
(SAPHIRE upper-cases, MEF is case-sensitive). Gates live in one model-wide
namespace, as they do in both formats: a fault tree in SAPHIRE can use a gate
another tree defines, and MEF gate names are global unless declared private.

The whole model must survive ``to_dict`` -> JSON -> ``from_dict`` unchanged:
it is stored beside the index (services/pra/store.py) so the tools never
re-parse the source on a read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# Gate types, normalised across formats. "atleast" carries k (k-out-of-n, the
# MEF <atleast min=k> and SAPHIRE's "3/4" style). "transfer" points at another
# fault tree: its single input is that tree's name.
GATE_TYPES = ("and", "or", "atleast", "not", "xor", "nand", "nor", "transfer", "other")

# Where a cut set list came from: carried in the source files, or computed by
# Clio (services/pra/analysis.py), which is a screening estimate.
CUT_SET_SOURCES = ("model", "computed")
CUT_SET_KINDS = ("fault_tree", "sequence", "end_state")


@dataclass
class Gate:
    name: str
    type: str                       # one of GATE_TYPES
    inputs: List[str]               # gate, basic event, house event or tree names; "/NAME" = complemented
    k: Optional[int] = None         # atleast only
    tree: Optional[str] = None      # the fault tree that defines it, when known
    description: str = ""


@dataclass
class BasicEvent:
    name: str
    description: str = ""
    probability: Optional[float] = None       # the point value used for quantification
    failure_rate: Optional[float] = None      # per hour, when the model gives one
    mission_time: Optional[float] = None      # hours
    calc_type: Optional[str] = None           # the tool's own calculation type code, verbatim
    uncertainty: Optional[str] = None         # e.g. "lognormal EF=3", verbatim summary
    attributes: Dict[str, Any] = field(default_factory=dict)  # anything else worth showing


@dataclass
class HouseEvent:
    name: str
    state: bool = True
    description: str = ""


@dataclass
class FaultTree:
    name: str
    top: Optional[str] = None       # the top gate's name
    description: str = ""
    gates: List[str] = field(default_factory=list)  # names of the gates this tree defines


@dataclass
class SequencePathStep:
    functional_event: str
    state: str                      # "success", "failure", or the branch label the tool used


@dataclass
class Sequence:
    name: str
    end_state: Optional[str] = None
    path: List[SequencePathStep] = field(default_factory=list)
    logic: List[str] = field(default_factory=list)  # tops in the sequence, "/X" = success of X
    frequency: Optional[float] = None
    description: str = ""
    # The event tree this sequence continues in, when it ends in a transfer
    # rather than an end state (SAPHIRE's transfer flag, MEF's <event-tree> link).
    transfer: Optional[str] = None


@dataclass
class EventTree:
    name: str
    initiating_event: Optional[str] = None
    description: str = ""
    functional_events: List[str] = field(default_factory=list)   # in column order
    sequences: List[Sequence] = field(default_factory=list)
    # What each functional event (top event) means, as the event tree labels it.
    functional_event_descriptions: Dict[str, str] = field(default_factory=dict)


@dataclass
class CutSetList:
    context: str                    # the fault tree, sequence or end state the list belongs to
    kind: str = "fault_tree"        # one of CUT_SET_KINDS
    cut_sets: List[List[str]] = field(default_factory=list)  # each a list of event names, "/X" = complemented
    source: str = "model"           # one of CUT_SET_SOURCES
    event_tree: Optional[str] = None  # for sequences
    truncation: Optional[float] = None  # the cutoff used, when known


@dataclass
class PraModel:
    name: str
    source_format: str              # "open-psa-mef", "saphire-mard", ...
    source_files: List[str] = field(default_factory=list)
    description: str = ""
    fault_trees: Dict[str, FaultTree] = field(default_factory=dict)
    gates: Dict[str, Gate] = field(default_factory=dict)
    basic_events: Dict[str, BasicEvent] = field(default_factory=dict)
    house_events: Dict[str, HouseEvent] = field(default_factory=dict)
    event_trees: Dict[str, EventTree] = field(default_factory=dict)
    initiating_events: Dict[str, BasicEvent] = field(default_factory=dict)  # frequency in `probability`
    end_states: Dict[str, str] = field(default_factory=dict)  # name -> description
    cut_sets: List[CutSetList] = field(default_factory=list)
    # What the reader could not represent or had to guess, in plain words.
    # Shown to people next to the model; never silently dropped.
    warnings: List[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    # ── lookups ─────────────────────────────────────────────────────────

    def kind_of(self, name: str) -> Optional[str]:
        """'gate', 'basic_event', 'house_event', 'fault_tree' or None."""
        bare = name[1:] if name.startswith("/") else name
        if bare in self.gates:
            return "gate"
        if bare in self.basic_events:
            return "basic_event"
        if bare in self.house_events:
            return "house_event"
        if bare in self.fault_trees:
            return "fault_tree"
        return None

    def top_gate(self, tree: str) -> Optional[str]:
        ft = self.fault_trees.get(tree)
        return ft.top if ft else None

    def cut_sets_for(self, context: str) -> Optional[CutSetList]:
        for c in self.cut_sets:
            if c.context == context:
                return c
        return None

    def counts(self) -> Dict[str, int]:
        return {
            "fault_trees": len(self.fault_trees),
            "gates": len(self.gates),
            "basic_events": len(self.basic_events),
            "house_events": len(self.house_events),
            "event_trees": len(self.event_trees),
            "sequences": sum(len(et.sequences) for et in self.event_trees.values()),
            "cut_set_lists": len(self.cut_sets),
        }

    # ── serialisation ───────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PraModel":
        version = int(data.get("schema_version", 0))
        if version > SCHEMA_VERSION:
            raise ValueError(f"PRA model schema {version} is newer than this build understands ({SCHEMA_VERSION})")

        def seq(d: Dict[str, Any]) -> Sequence:
            d = dict(d)
            d["path"] = [SequencePathStep(**p) for p in d.get("path") or []]
            return Sequence(**d)

        def et(d: Dict[str, Any]) -> EventTree:
            d = dict(d)
            d["sequences"] = [seq(s) for s in d.get("sequences") or []]
            return EventTree(**d)

        return cls(
            name=data["name"],
            source_format=data["source_format"],
            source_files=list(data.get("source_files") or []),
            description=data.get("description", ""),
            fault_trees={k: FaultTree(**v) for k, v in (data.get("fault_trees") or {}).items()},
            gates={k: Gate(**v) for k, v in (data.get("gates") or {}).items()},
            basic_events={k: BasicEvent(**v) for k, v in (data.get("basic_events") or {}).items()},
            house_events={k: HouseEvent(**v) for k, v in (data.get("house_events") or {}).items()},
            event_trees={k: et(v) for k, v in (data.get("event_trees") or {}).items()},
            initiating_events={k: BasicEvent(**v) for k, v in (data.get("initiating_events") or {}).items()},
            end_states=dict(data.get("end_states") or {}),
            cut_sets=[CutSetList(**c) for c in data.get("cut_sets") or []],
            warnings=list(data.get("warnings") or []),
            schema_version=SCHEMA_VERSION,
        )
