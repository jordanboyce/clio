"""SAPHIRE MAR-D reader: the text exports of NUREG/CR-7039 Volume 7.

A SAPHIRE project exports as one file per kind of data, all named after the
project (G-PWR.FTL, G-PWR.BEI, ...). Each kind is readable alone, and a set
combines into one model:

  .FTL  fault tree logic          .BEI  basic event data      .BED  basic event descriptions
  .FTD  fault tree descriptions   .GTD  gate descriptions     .ETL  event tree logic
  .ETD  event tree descriptions   .ESD  end state descriptions
  .SQL  sequence logic            .SQC  sequence cut sets     .FTC  fault tree cut sets
  .ESC  end state cut sets

Other MAR-D kinds (.BEA, .FTA, .GTA, .SQA, ...) hold attributes, recovery
rules and the like; they are accepted in an export and listed as not read.

The layouts follow what SAPHIRE 8 writes, checked against a real export and
against the MIT-licensed KUREAS MAR-D readers (KUREAS-FTL and KUREAS-ETL by
the KUREAS project), whose handling of root gates, ``^EOS`` sections, the
``^LOGIC`` indentation and the SEQUENCES columns this reader follows.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import PurePath
from typing import Dict, Iterator, List, Optional, Set, Tuple

from services.pra.model import (BasicEvent, CutSetList, EventTree, FaultTree, Gate, HouseEvent,
                                PraModel, Sequence, SequencePathStep)
from services.pra.readers.base import (PraFormatError, Source, WarningLog, check_size, decode,
                                       limits, name_list)

SOURCE_FORMAT = "saphire-mard"

# The kinds this reader understands, by extension (upper case, no dot).
READ_KINDS = ("FTL", "BEI", "BED", "FTD", "GTD", "ETL", "ETD", "ESD", "SQL", "SQC", "FTC", "ESC")
# MAR-D kinds a SAPHIRE export also carries, accepted and listed as not read.
OTHER_KINDS = frozenset({
    "BEA", "BEC", "BECAT", "BECATW", "BECW", "BEDA", "BEG", "BEH", "BEMD", "BEMDA", "BEMT", "BEN",
    "BER", "BERN", "BERW", "BET", "EGD", "EGI", "ESI", "EST", "ETA", "ETP", "ETR", "ETT", "ETY",
    "FAA", "FAC", "FAD", "FAL", "FAO", "FAS", "FAT", "FAU", "FAY", "FTA", "FTT", "FTY", "GTA",
    "HID", "MTD", "PHD", "SQA", "SQD", "SQP", "SQY", "MARD",
})

# A project header: "G-PWR =", "G-PWR, ACC-FT =", "G-PWR, TRANS, 02-2-2=", "G-PWR, ATWS, RPS-FT, T =".
_HEADER = re.compile(r"^\s*([^\s,=]+)\s*((?:,[^,=]*)*)=\s*$")
_VOTE = re.compile(r"^(\d+)/(\d+)$")
_LOGIC_TOKEN = re.compile(r"([+-]?)(\d+)\.(\d+)")
_GATE_TYPES = {"AND": "and", "OR": "or", "NAND": "nand", "NOR": "nor", "XOR": "xor", "NOT": "not",
               "TRAN": "transfer", "TRANSFER": "transfer"}
# BEI columns in SAPHIRE 8's order; a header comment row, when present, wins.
_BEI_COLUMNS = ("name", "fdt", "udc", "udt", "udvalue", "prob", "lambda", "tau", "mission", "init",
                "pf", "udvalue2", "calcprob", "freq", "analysistype", "phasetype", "project")
# SAPHIRE uncertainty distribution codes (UdT) and what UdValue holds for each.
_UDT = {"L": ("lognormal", "EF"), "N": ("normal", "SD"), "B": ("beta", "b"), "G": ("gamma", "alpha"),
        "C": ("chi-squared", "dof"), "E": ("exponential", None), "U": ("uniform", "upper"),
        "H": ("histogram", None), "M": ("maximum entropy", None), "S": ("seismic lognormal", "beta"),
        "O": ("constrained non-informative", None), "T": ("triangular", "mode"),
        "D": ("Dirichlet", None)}
# Calculation types (FdT) whose probability SAPHIRE derives from data a MAR-D
# export does not fully carry; Clio uses SAPHIRE's calculated value as given.
_CALC_TYPE_NAMES = {"R": "common-cause", "C": "compound", "X": "SPAR-H human error", "S": "fault tree result",
                    "E": "end state result", "G": "seismic", "H": "seismic hazard curve", "V": "value",
                    "J": "J", "O": "O", "Q": "initiating-event CCF", "A": "train CCF"}


def kind_of_name(name: str) -> str:
    return PurePath(name).suffix.lstrip(".").upper()


def _lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _is_comment(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("*") or s.startswith("|")


def _number(text: str) -> Optional[float]:
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


def _clean(text: str) -> str:
    return " ".join(text.replace('"', " ").split())


def _sections(text: str) -> Iterator[List[str]]:
    """The ``^EOS``-separated sections of a file, comment lines dropped."""
    current: List[str] = []
    for line in _lines(text):
        if line.strip().upper().startswith("^EOS"):
            if current:
                yield current
            current = []
            continue
        if line.strip().startswith("*"):
            continue
        current.append(line)
    if any(l.strip() for l in current):
        yield current


def _split_fields(line: str) -> List[str]:
    """Comma-separated fields; quotes protect commas only when they are balanced."""
    if line.count('"') % 2:
        return [f.strip() for f in line.replace('"', "").split(",")]
    out, cur, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def looks_like_mard(text: str, kind: str = "") -> bool:
    """Whether the start of a file reads as MAR-D: a project header first.

    ``kind`` "SQL" asks for more, because .sql is also a SQL script: a
    "PROJECT, EVENT TREE, SEQUENCE=" header and a ``^EOS`` line.
    """
    meaningful = [l for l in _lines(text)[:50] if not _is_comment(l)]
    if not meaningful:
        return False
    first = meaningful[0]
    head = _HEADER.match(first)
    if kind == "SQL":
        return bool(head) and first.count(",") == 2 and "^EOS" in text.upper()
    if head:
        return True
    # Cut set files: "PROJECT, TREE,RANDOM/CD" (or "PROJECT, TREE, SEQUENCE," and
    # "RANDOM/CD") and then "=" on a line of its own.
    return (kind in ("SQC", "FTC", "ESC") and "," in first
            and any(line.strip().endswith("=") for line in meaningful[1:3]))


class _Builder:
    def __init__(self) -> None:
        self.model = PraModel(name="", source_format=SOURCE_FORMAT)
        self.log = WarningLog()
        self.project = ""
        self.trans: Dict[str, str] = OrderedDict()      # transfer gate -> tree it appears in
        self.descriptions: Dict[str, Dict[str, str]] = {k: {} for k in ("BED", "GTD", "FTD", "ETD")}
        self.read_bei = False
        self.calc_types: Dict[str, int] = {}
        self.kinds: Set[str] = set()                   # the MAR-D kinds being read

    def header(self, line: str) -> Optional[List[str]]:
        m = _HEADER.match(line)
        if not m:
            return None
        fields = [m.group(1)] + [f.strip() for f in m.group(2).split(",")[1:]]
        self.project = self.project or m.group(1)
        return fields

    # ── fault tree logic ───────────────────────────────────────────────

    def ftl(self, text: str) -> None:
        for section in _sections(text):
            rows = [l for l in section if l.strip()]
            if not rows:
                continue
            fields = self.header(rows[0])
            if not fields or len(fields) < 2:
                raise PraFormatError("This .FTL file does not start with a 'PROJECT, FAULT TREE =' header.")
            tree = fields[1]
            defined: "OrderedDict[str, Gate]" = OrderedDict()
            last: Optional[Gate] = None
            for line in rows[1:]:
                tokens = line.split()
                if len(tokens) >= 2 and (tokens[1].upper() in _GATE_TYPES or _VOTE.match(tokens[1])):
                    last = self._gate_line(tree, tokens, defined)
                elif last is not None and line[:1].isspace():
                    last.inputs.extend(tokens)       # a continued input list
                elif len(tokens) >= 2:
                    last = Gate(tokens[0], "other", tokens[2:], tree=tree,
                                description=f"SAPHIRE gate type {tokens[1]}")
                    defined.setdefault(tokens[0], last)
                    self.log.group("Gates of types Clio does not know are kept as 'other': {names}.",
                                   f"{tokens[0]} ({tokens[1]})")
            self._add_tree(tree, defined)

    def _gate_line(self, tree: str, tokens: List[str], defined: "OrderedDict[str, Gate]") -> Optional[Gate]:
        name, raw = tokens[0], tokens[1].upper()
        inputs = tokens[2:]
        if _GATE_TYPES.get(raw) == "transfer":
            self.trans.setdefault(name, tree)
            return None
        vote = _VOTE.match(raw)
        if vote:
            k, n = int(vote.group(1)), int(vote.group(2))
            if n != len(inputs):
                self.log.group("k/n gates whose n differs from their number of inputs: {names}.",
                               f"{name} ({raw}, {len(inputs)} inputs)")
            gate = Gate(name, "atleast", inputs, k=k, tree=tree)
        else:
            gate = Gate(name, _GATE_TYPES[raw], inputs, tree=tree)
        defined.setdefault(name, gate)
        return gate

    def _add_tree(self, tree: str, defined: "OrderedDict[str, Gate]") -> None:
        m = self.model
        own: List[str] = []
        for name, gate in defined.items():
            if name in m.gates:
                old = m.gates[name]
                if (old.type, old.inputs, old.k) != (gate.type, gate.inputs, gate.k):
                    self.log.group("Gates defined differently in two fault trees keep the first "
                                   "definition: {names}.", name)
                continue
            m.gates[name] = gate
            own.append(name)
        used = {i.lstrip("/") for g in defined.values() for i in g.inputs}
        if tree in defined and tree not in used:
            top = tree
        else:
            top = next((g for g in defined if g not in used), next(iter(defined), None))
        ft = m.fault_trees.get(tree)
        if ft is None:
            m.fault_trees[tree] = FaultTree(tree, top=top, gates=own)
        else:
            ft.top = ft.top or top
            ft.gates.extend(g for g in own if g not in ft.gates)

    # ── basic event data ───────────────────────────────────────────────

    def bei(self, text: str) -> None:
        self.read_bei = True
        columns = list(_BEI_COLUMNS)
        for line in _lines(text):
            stripped = line.strip()
            if stripped.startswith("*"):
                names = [re.sub(r"[^a-z0-9]", "", f.lower()) for f in stripped.lstrip("*").split(",")]
                if "name" in names and "fdt" in names:
                    columns = names
                continue
            if not stripped or self.header(line):
                continue
            fields = [f.strip() for f in line.split(",")]
            if not re.match(r"^(<[^>]*>|[A-Za-z0-9][^\s,]*)$", fields[0] or " "):
                continue  # a wrapped header row or stray text
            self._bei_row(dict(zip(columns, fields)))

    def _bei_row(self, row: Dict[str, str]) -> None:
        name = row.get("name", "")
        fdt = (row.get("fdt") or "").strip()
        calc = _number(row.get("calcprob", ""))
        point = _number(row.get("prob", ""))
        probability = calc if calc is not None else point
        if fdt.upper() in ("T", "F"):
            self.model.house_events[name] = HouseEvent(name, fdt.upper() == "T")
            return
        rate = _number(row.get("lambda", ""))
        mission = _number(row.get("mission", ""))
        tau = _number(row.get("tau", ""))
        attributes: Dict[str, object] = {}
        udc = (row.get("udc") or "").strip()
        if udc and _number(udc) is None:
            attributes["correlation_class"] = udc
        if tau:
            attributes["repair_time"] = tau
        if point and calc is not None and point != calc:
            attributes["point_value"] = point
        event = BasicEvent(name, probability=probability, failure_rate=rate or None,
                           mission_time=mission or None, calc_type=fdt or None,
                           uncertainty=self._uncertainty(row), attributes=attributes)
        if (row.get("init") or "").strip().upper() == "I":
            self.model.initiating_events[name] = event
            return
        self.model.basic_events[name] = event
        if fdt.upper() in _CALC_TYPE_NAMES:
            self.calc_types[fdt.upper()] = self.calc_types.get(fdt.upper(), 0) + 1

    @staticmethod
    def _uncertainty(row: Dict[str, str]) -> Optional[str]:
        code = (row.get("udt") or "").strip().upper()
        if not code:
            return None
        name, param = _UDT.get(code, (f"UdT {code}", None))
        value = _number(row.get("udvalue", ""))
        if code == "L":  # older exports put the mean first and the EF second
            value = next((v for v in (value, _number(row.get("udvalue2", ""))) if v and v > 1), value)
        if param and value:
            return f"{name} {param}={value:.4G}"
        return name

    # ── descriptions ───────────────────────────────────────────────────

    def descriptions_file(self, kind: str, text: str) -> None:
        trailing = {"BED": 1, "GTD": 2, "FTD": 3, "ETD": 2, "ESD": 2}[kind]
        target = self.model.end_states if kind == "ESD" else self.descriptions[kind]
        for line in _lines(text):
            if _is_comment(line) or line.strip().upper().startswith("^EOS") or self.header(line):
                continue
            fields = _split_fields(line)
            if len(fields) < 2 or not fields[0]:
                continue
            if '"' not in line and len(fields) > 2 + trailing:
                description = ", ".join(fields[1:len(fields) - trailing])
            else:
                description = fields[1]
            target[fields[0]] = _clean(description)

    # ── event trees ────────────────────────────────────────────────────

    def etl(self, text: str) -> None:
        for section in _sections(text):
            self._event_tree(section)

    def _event_tree(self, section: List[str]) -> None:
        rows = [l for l in section if l.strip()]
        if not rows:
            return
        fields = self.header(rows[0])
        if not fields or len(fields) < 3:
            raise PraFormatError("This .ETL file does not start with a "
                                 "'PROJECT, EVENT TREE, INITIATING EVENT =' header.")
        name, initiator = fields[1], fields[2]
        is_transfer_tree = len(fields) > 3 and fields[3].upper() == "T"
        blocks: Dict[str, List[str]] = {}
        current = None
        for line in rows[1:]:
            if line.lstrip().startswith("^"):
                current = line.strip()[1:].split()[0].upper() if line.strip()[1:] else ""
                blocks.setdefault(current, [])
                continue
            if current is not None:
                blocks[current].append(line)
        tops = [t.strip() for t in ",".join(blocks.get("TOPS", [])).split(",") if t.strip()]
        et = self.model.event_trees.get(name) or EventTree(name)
        et.initiating_event = initiator
        et.functional_events = tops
        if not is_transfer_tree and initiator not in self.model.initiating_events:
            self.model.initiating_events[initiator] = BasicEvent(initiator)
        self._top_descriptions(et, blocks.get("TOPDESC", []), initiator, is_transfer_tree)
        paths = self._logic(name, tops, blocks.get("LOGIC", []))
        rows_meta = [r for r in blocks.get("SEQUENCES", []) if r.strip()]
        if rows_meta and ("SEQUENCE NAME" in rows_meta[0].upper() or "END STATE" in rows_meta[0].upper()):
            rows_meta = rows_meta[1:]
        if len(rows_meta) != len(paths):
            self.log.group("Event trees whose ^LOGIC and ^SEQUENCES rows differ in number: {names}.",
                           f"{name} ({len(paths)} paths, {len(rows_meta)} sequences)")
        width = max(2, len(str(max(len(paths), len(rows_meta)))))
        existing = {s.name: s for s in et.sequences}
        sequences = []
        for i in range(max(len(paths), len(rows_meta))):
            seq = existing.pop(f"{i + 1:0{width}d}", None) or Sequence(f"{i + 1:0{width}d}")
            seq.path = paths[i] if i < len(paths) else []
            if i < len(rows_meta):
                self._sequence_meta(seq, _split_fields(rows_meta[i]))
            sequences.append(seq)
        et.sequences = sequences + list(existing.values())
        if blocks.get("NODESUBS") and any(l.strip().upper().startswith("NODEPOS") for l in blocks["NODESUBS"]):
            self.log.group("Event trees that substitute fault trees on some branches (^NODESUBS) show "
                           "the top event names on their paths: {names}.", name)
        self.model.event_trees[name] = et

    def _top_descriptions(self, et: EventTree, lines: List[str], initiator: str, transfer: bool) -> None:
        items: List[str] = []
        current: List[str] = []
        for line in lines:
            if line.strip() == "!":
                items.append("".join(current))
                current = []
            else:
                current.append(line.strip().strip('"'))
        if current:
            items.append("".join(current))
        items = [" ".join(i.split()) for i in items]
        if len(items) == len(et.functional_events) + 1:
            ie_text, items = items[0], items[1:]
            ie = self.model.initiating_events.get(initiator)
            if ie_text and ie is not None and not ie.description:
                ie.description = ie_text
            if ie_text and not et.description:
                et.description = ie_text
        for top, text in zip(et.functional_events, items):
            if text:
                et.functional_event_descriptions[top] = text

    def _logic(self, tree: str, tops: List[str], lines: List[str]) -> List[List[SequencePathStep]]:
        """^LOGIC rows as paths. A row starts at the column where it branches off
        the row above (SAPHIRE indents it there); "+" is the success (up) branch,
        "-" the failure branch, a bare number passes the top by."""
        order: List[int] = []
        previous: Dict[int, str] = {}
        paths: List[List[SequencePathStep]] = []
        for line in lines:
            tokens = list(_LOGIC_TOKEN.finditer(line.split("//")[0]))
            if not tokens:
                continue
            if not order:
                order = [int(t.group(2)) for t in tokens]
            first = int(tokens[0].group(2))
            states = {n: previous[n] for n in order[:order.index(first)]} if first in order else {}
            for t in tokens:
                sign, number = t.group(1), int(t.group(2))
                states[number] = {"+": "success", "-": "failure"}.get(sign, "")
                if t.group(3) != "0":
                    self.log.group("Event trees with multi-branch tops are read as success/failure: "
                                   "{names}.", tree)
            previous = states
            path = []
            for number in order:
                state = states.get(number, "")
                if not state:
                    continue
                if not 1 <= number <= len(tops):
                    self.log.group("Event tree logic that names a top beyond its ^TOPS list: {names}.", tree)
                    continue
                path.append(SequencePathStep(tops[number - 1], state))
            paths.append(path)
        return paths

    @staticmethod
    def _sequence_meta(seq: Sequence, fields: List[str]) -> None:
        def at(i: int) -> str:
            return fields[i].strip() if i < len(fields) else ""

        label, end, frequency, comment, flag = at(1), at(3), at(5), at(7), at(8).upper()
        if flag in ("T", "Y"):
            seq.transfer, seq.end_state = end or None, None
        else:
            seq.end_state = end or None
        seq.frequency = _number(frequency) if frequency else seq.frequency
        seq.description = " - ".join(p for p in (label, comment) if p) or seq.description

    # ── sequence logic and cut sets ────────────────────────────────────

    def _event_tree_for(self, name: str) -> EventTree:
        et = self.model.event_trees.get(name)
        if et is None:
            et = self.model.event_trees[name] = EventTree(name)
        return et

    def sql(self, text: str) -> None:
        for header, body in self._header_sections(text, "SQL"):
            if len(header) < 3:
                continue
            et = self._event_tree_for(header[1])
            seq = next((s for s in et.sequences if s.name == header[2]), None)
            if seq is None:
                seq = Sequence(header[2])
                et.sequences.append(seq)
            tokens = " ".join(body).split()
            if tokens and tokens[-1] == ".":
                tokens.pop()
            elif tokens and tokens[-1].endswith("."):
                tokens[-1] = tokens[-1][:-1]
            seq.logic = tokens

    def cut_sets(self, kind: str, text: str) -> None:
        for header, body in self._header_sections(text, kind):
            if kind == "SQC":
                if len(header) < 3:
                    continue
                entry = CutSetList(header[2], kind="sequence", event_tree=header[1])
            else:
                if len(header) < 2:
                    continue
                entry = CutSetList(header[1], kind="fault_tree" if kind == "FTC" else "end_state")
            entry.cut_sets = self._products(body)
            self.model.cut_sets.append(entry)

    def _header_sections(self, text: str, kind: str) -> Iterator[Tuple[List[str], List[str]]]:
        """(header fields, body lines) per section. A header runs to the line ending in '='."""
        for section in _sections(text):
            header: List[str] = []
            body: List[str] = []
            in_body = False
            for line in section:
                if not line.strip():
                    continue
                if in_body:
                    body.append(line)
                    continue
                header.append(line.strip())
                if line.strip().endswith("="):
                    in_body = True
            if not in_body:
                continue
            fields = [f.strip() for f in ",".join(header).rstrip("=").split(",") if f.strip()]
            if fields:
                self.project = self.project or fields[0]
                yield fields, body

    @staticmethod
    def _products(lines: List[str]) -> List[List[str]]:
        products: List[List[str]] = []
        current: List[str] = []
        for token in re.findall(r"[^\s*+]+|[*+]", " ".join(lines)):
            if token == "*":
                continue
            end = token == "." or token.endswith(".")
            if token == "+" or end:
                name = token[:-1] if end and token != "." else ""
                if name:
                    current.append(name)
                if current or token == "+":
                    products.append([] if current == ["<TRUE>"] else current)
                current = []
                if end:
                    break
                continue
            current.append(token)
        if current:
            products.append(current)
        return products

    # ── putting it together ────────────────────────────────────────────

    def finish(self, files: List[str], ignored: List[str]) -> PraModel:
        m = self.model
        m.name = self.project or (PurePath(files[0]).stem if files else "SAPHIRE project")
        m.source_files = list(files)
        for name, tree in self.trans.items():
            if name not in m.gates:
                m.gates[name] = Gate(name, "transfer", [name], tree=tree)
        missing_trees = [n for n in self.trans if n not in m.fault_trees and m.gates[n].type == "transfer"]
        if missing_trees and "FTL" in self.kinds:
            self.log.add(f"{len(missing_trees)} transfers point at fault trees that are not in these files: "
                         f"{name_list(missing_trees)}.")
        self._apply_descriptions()
        known = set(m.gates) | set(m.basic_events) | set(m.house_events) | set(m.initiating_events)
        stubs = sorted({i.lstrip("/") for g in m.gates.values() if g.type != "transfer"
                        for i in g.inputs} - known)
        for name in stubs:
            m.basic_events[name] = BasicEvent(name)
        if stubs:
            where = ("have no data in the .BEI file" if self.read_bei
                     else "have no data here (no .BEI file was read)")
            self.log.add(f"{len(stubs)} basic events used in the logic {where}: {name_list(stubs)}.")
        if self.calc_types:
            kinds = ", ".join(f"{code} {_CALC_TYPE_NAMES[code]} ({n})" if _CALC_TYPE_NAMES[code] != code
                              else f"{code} ({n})" for code, n in sorted(self.calc_types.items()))
            self.log.add("Probabilities are SAPHIRE's calculated values ('Calc. Prob'); Clio does not "
                         f"recompute calculation types {kinds}, and does not expand common-cause groups.")
        if ignored:
            self.log.add(f"{len(ignored)} MAR-D files of kinds Clio does not use were not read: "
                         f"{name_list(ignored)}.")
        m.warnings.extend(self.log.render())
        return m

    def _apply_descriptions(self) -> None:
        m = self.model
        for name, text in self.descriptions["BED"].items():
            event = m.basic_events.get(name) or m.initiating_events.get(name) or m.house_events.get(name)
            if event is None:
                event = m.basic_events[name] = BasicEvent(name)
            event.description = text
        lost = []
        for name, text in self.descriptions["GTD"].items():
            if name in m.gates:
                m.gates[name].description = text
            elif name in m.fault_trees and not m.fault_trees[name].description:
                m.fault_trees[name].description = text
            else:
                lost.append(name)
        if lost and m.gates:
            self.log.add(f"{len(lost)} gate descriptions name gates the fault tree logic does not "
                         f"define: {name_list(lost)}.")
        for name, text in self.descriptions["FTD"].items():
            ft = m.fault_trees.get(name)
            if ft is None:
                ft = m.fault_trees[name] = FaultTree(name)
            ft.description = text
            top = m.gates.get(ft.top or "")
            if top is not None and not top.description:
                top.description = text
        for name, text in self.descriptions["ETD"].items():
            et = self._event_tree_for(name)
            et.description = text


def read_mard(sources: List[Source]) -> PraModel:
    """One model from one or more MAR-D files of the same SAPHIRE project."""
    if not sources:
        raise PraFormatError("No SAPHIRE MAR-D files were given.")
    check_size(sources)
    cap = limits()["elements"]
    texts: List[Tuple[str, str, str]] = []
    lines = 0
    ignored: List[str] = []
    for name, data in sources:
        kind = kind_of_name(name)
        if kind in OTHER_KINDS:
            ignored.append(PurePath(name).name)
            continue
        if kind not in READ_KINDS:
            raise PraFormatError(f"{PurePath(name).name} is not a SAPHIRE MAR-D file Clio reads.")
        text = decode(data)
        lines += text.count("\n") + 1
        if cap and lines > cap:
            raise PraFormatError(f"These MAR-D files have more than {cap:,} lines; Clio does not read "
                                 f"models that large (PRA_MAX_ELEMENTS).")
        if not looks_like_mard(text, kind):
            raise PraFormatError(f"{PurePath(name).name} does not read as a SAPHIRE MAR-D .{kind} file "
                                 f"(it should start with a 'PROJECT =' header).")
        texts.append((PurePath(name).name, kind, text))
    if not texts:
        raise PraFormatError("None of these files is a MAR-D kind Clio reads (.FTL, .BEI, .ETL, ...).")
    builder = _Builder()
    builder.kinds = {kind for _, kind, _ in texts}
    order = {k: i for i, k in enumerate(READ_KINDS)}
    for _, kind, text in sorted(texts, key=lambda t: order[t[1]]):
        if kind == "FTL":
            builder.ftl(text)
        elif kind == "BEI":
            builder.bei(text)
        elif kind in ("BED", "GTD", "FTD", "ETD", "ESD"):
            builder.descriptions_file(kind, text)
        elif kind == "ETL":
            builder.etl(text)
        elif kind == "SQL":
            builder.sql(text)
        else:
            builder.cut_sets(kind, text)
    return builder.finish([n for n, _, _ in texts], ignored)
