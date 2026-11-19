"""Generate the hard retrieval fixture (tests/fixtures/retrieval_hard).

The 60-document retrieval_bench fixture scores recall@10 of 1.0 in every
configuration, so it cannot show whether a change to search helps. This
fixture is built to make first-stage retrieval miss, in the ways real
collections do:

  near_duplicate   twelve divisions keep the same policy template with their
                   own figures; the division is named in the title only
  late_page        the answer sits on a later page, under a heading printed
                   on an earlier one, in a sentence that does not repeat it
  cross_page       the answering sentence is cut in two by a page break
  paraphrase       the question shares almost no words with the answer
  identifier       a form or document number
  revision         an older revision states another value and is marked
                   superseded
  cross_collection the question is asked across all four collections, which
                   also hold meeting notes that mention the topic in passing
  unanswerable     a division or topic the corpus does not have

Documents are pages of text, not passages: the benchmark indexes them with
the app's own chunker and labels an answer by the sentence that holds it
(``doc_id::sentence``), so a change to chunking is measured, not assumed.

Everything is generated from a fixed seed; run it again and the files are
byte-identical. Change the generator, bump CORPUS_VERSION, and say so where
the new numbers are quoted.

    venv/Scripts/python scripts/make_retrieval_corpus.py
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

CORPUS_VERSION = 1
SEED = 20261119
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval_hard"
PAGE_CHARS = 1250

DIVISIONS = [
    ("Harbor", "HB"), ("Summit", "SM"), ("Riverside", "RS"), ("Lakeshore", "LS"),
    ("Granite", "GR"), ("Meadow", "MD"), ("Cedar", "CD"), ("Falcon", "FC"),
    ("Juniper", "JN"), ("Orchid", "OR"), ("Pioneer", "PN"), ("Quarry", "QR"),
    ("Aspen", "AS"), ("Birch", "BI"), ("Delta", "DL"), ("Ember", "EM"),
    ("Fjord", "FJ"), ("Garnet", "GA"), ("Heron", "HE"), ("Ivory", "IV"),
]
MISSING_DIVISIONS = ["Westfield", "Bramble", "Copperline", "Sandpiper"]


@dataclass
class Fact:
    """One answerable statement. ``text`` and ``q`` take ``{v}`` (the value)
    and ``{div}``; ``text`` must not name the division."""
    text: str
    values: Sequence[str]
    q: str
    paraphrase: bool = False


@dataclass
class Section:
    heading: str
    facts: List[Fact]
    filler: List[str] = field(default_factory=list)


@dataclass
class DocType:
    key: str
    collection: str
    title: str
    code: str
    form_q: str
    sections: List[Section]
    filler: List[str]


COMMON_FILLER = [
    "Managers make sure their teams know where to find the current version of this document.",
    "Questions about how this applies to a particular case go to the document owner named on the cover page.",
    "Where this document and a contract disagree, the contract wins and the owner is told.",
    "Exceptions are recorded in writing with the reason and the name of the person who agreed to them.",
    "The owner reviews this section each year and after any incident that shows it is unclear.",
    "Local practice may add steps but may not remove any step described here.",
    "Records made under this section are kept with the other records of the same activity.",
    "Training on this section is part of the induction for new staff in the roles concerned.",
    "Anyone who sees this document being ignored should raise it with their manager or the owner.",
    "Contractors working on site follow this document as if they were staff.",
    "Copies printed from the system are uncontrolled and should be checked against the current version.",
    "Audit findings against this document are closed within the period the auditor sets.",
    "A deviation that is likely to repeat is a reason to change the document, not to keep granting exceptions.",
    "The document owner may issue guidance notes that explain this document but cannot change it.",
    "Where a term is not defined here it has its ordinary meaning.",
    "Systems that support this document are listed in the register kept by the owner.",
]

DEFINITIONS = [
    "In this document a manager means the person a member of staff reports to directly.",
    "The owner is the role named on the cover page, not the person who wrote the text.",
    "A working day is any day other than a weekend or a public holiday where the work is done.",
    "Staff includes permanent employees, temporary employees and people on secondment.",
    "Written means on paper or in the document system, with the author identified.",
    "A record is any information created or received in the course of the work and kept as evidence.",
]
RESPONSIBILITIES = [
    "Managers make sure the steps in this document are followed in their area.",
    "The owner keeps the document current and answers questions about it.",
    "Internal audit checks a sample of cases against this document each year.",
    "Every member of staff follows the document and reports anything that stops them doing so.",
    "Department heads confirm each year that their teams have read the current version.",
]

DOC_TYPES: List[DocType] = [
    DocType(
        key="travel", collection="finance", code="TE",
        title="{div} Division Travel and Expense Policy",
        form_q="Which form is used to claim travel expenses at {div}?",
        filler=[
            "Travel should be planned early enough to use the lower fares.",
            "Staff are expected to choose the most economical option that still gets the work done.",
            "Personal travel added to a business trip is paid for by the traveller.",
        ],
        sections=[
            Section("Booking Travel", [
                Fact("Flights shorter than {v} hours are booked in economy class without exception.",
                     ["three", "four", "five", "six"], "Below what flight length must {div} staff fly economy?"),
                Fact("Hotel bookings are made through the travel desk at least {v} days before arrival.",
                     ["five", "seven", "ten", "fourteen"], "How far ahead must {div} employees book hotels?"),
            ]),
            Section("Daily Allowances", [
                Fact("The overnight allowance is {v} dollars per night, which covers meals and incidentals.",
                     ["55", "62", "68", "74", "81"], "What is the {div} per diem for an overnight trip?"),
                Fact("A day trip with no overnight stay attracts {v} dollars for lunch.",
                     ["14", "18", "22", "25"], "How much is the lunch allowance on a {div} day trip?"),
            ]),
            Section("Mileage", [
                Fact("Use of a private car is repaid at {v} cents per mile.",
                     ["58", "61", "64", "67"], "What does {div} reimburse per mile for using my own car?"),
                Fact("Nobody is paid for the first {v} miles of a journey, which count as the normal commute.",
                     ["10", "15", "20", "25"],
                     "When I drive to a client from home, how much of the distance at {div} goes unpaid?",
                     paraphrase=True),
            ]),
            Section("Approval of Claims", [
                Fact("Claims above {v} dollars need a second signature from the budget holder.",
                     ["400", "500", "750", "1000"], "Above what amount does a {div} expense claim need two signatures?"),
                Fact("They must reach accounts payable within {v} days of the trip or they are refused.",
                     ["30", "45", "60", "90"], "What is the deadline for submitting {div} travel claims?"),
            ]),
        ],
    ),
    DocType(
        key="procurement", collection="finance", code="PR",
        title="{div} Division Procurement Approval Policy",
        form_q="What is the purchase requisition form number at {div}?",
        filler=[
            "Buying from a supplier on the approved list is quicker and needs less paperwork.",
            "Splitting one purchase into several smaller orders to avoid a limit is a breach of this policy.",
        ],
        sections=[
            Section("Approval Limits", [
                Fact("A line manager may approve purchases up to {v} dollars.",
                     ["2,500", "5,000", "7,500", "10,000"], "How much can a {div} line manager approve on their own?"),
                Fact("Anything above that needs the finance director and a written business case.",
                     ["-"], ""),
            ]),
            Section("Quotations", [
                Fact("Purchases between {v} dollars and the tender limit need three written quotes.",
                     ["5,000", "10,000", "15,000"], "When are three quotes required for a {div} purchase?"),
                Fact("A formal tender is run for any contract worth more than {v} dollars over its life.",
                     ["50,000", "100,000", "150,000", "250,000"],
                     "Over what lifetime value must {div} put a contract out to tender?"),
            ]),
            Section("New Suppliers", [
                Fact("A new supplier is checked for {v} years of filed accounts before the first order.",
                     ["two", "three"], "How many years of accounts does {div} check for a new vendor?"),
                Fact("The vendor master file is only changed by two people acting together, never one, so bank details cannot be altered alone.",
                     ["-"], "Who can change a supplier's bank details at {div}?", paraphrase=True),
            ]),
        ],
    ),
    DocType(
        key="retention", collection="finance", code="RR",
        title="{div} Division Records Retention Schedule",
        form_q="Which form records the destruction of records at {div}?",
        filler=[
            "Keeping records longer than the schedule allows is as much a breach as destroying them early.",
            "A record under legal hold is kept until the hold is lifted, whatever the schedule says.",
        ],
        sections=[
            Section("Financial Records", [
                Fact("Accounts payable invoices are kept for {v} years after the end of the financial year.",
                     ["six", "seven", "ten"], "How long does {div} keep supplier invoices?"),
                Fact("Bank statements stay on file for {v} years and are then shredded on site.",
                     ["five", "six", "seven"], "For how many years does {div} hold on to bank statements?"),
            ]),
            Section("Personnel Files", [
                Fact("A leaver's file is held for {v} years from the last day of employment.",
                     ["four", "six", "eight"],
                     "Once someone quits {div}, how long before their HR folder can be thrown away?",
                     paraphrase=True),
                Fact("Interview notes for unsuccessful candidates are destroyed after {v} months.",
                     ["six", "nine", "twelve"], "When are interview notes for rejected applicants destroyed at {div}?"),
            ]),
            Section("Destruction", [
                Fact("Paper is destroyed by cross-cut shredding to a particle size of {v} millimetres or less.",
                     ["4", "6", "10"], "What shred size does {div} require for paper records?"),
            ]),
        ],
    ),
    DocType(
        key="calibration", collection="engineering", code="CP",
        title="{div} Site Equipment Calibration Procedure",
        form_q="What is the calibration record form at the {div} site?",
        filler=[
            "Calibration is carried out by a laboratory accredited for the measurement concerned.",
            "Equipment that is dropped or damaged is treated as out of calibration until checked.",
        ],
        sections=[
            Section("Calibration Intervals", [
                Fact("Torque wrenches are calibrated every {v} months or every 5,000 cycles, whichever comes first.",
                     ["three", "six", "twelve"], "How often are torque wrenches calibrated at the {div} site?"),
                Fact("Pressure gauges on test rigs are checked every {v} months.",
                     ["six", "twelve", "eighteen"], "What is the {div} interval for checking test rig pressure gauges?"),
            ]),
            Section("Out of Tolerance Results", [
                Fact("Everything measured with it since the last good check is reviewed within {v} working days.",
                     ["two", "three", "five"],
                     "If a gauge at {div} fails calibration, how soon must earlier measurements be reviewed?"),
                Fact("A red label is fixed to it and it is moved to the quarantine shelf in room {v}.",
                     ["B12", "C04", "A7", "D21"], "Where does failed measuring equipment go at {div}?"),
            ]),
            Section("Records", [
                Fact("Calibration certificates are kept for {v} years after the instrument is scrapped.",
                     ["three", "five", "ten"], "How long are calibration certificates kept at {div}?"),
            ]),
        ],
    ),
    DocType(
        key="change", collection="engineering", code="CC",
        title="{div} Site Engineering Change Control Procedure",
        form_q="Which form raises an engineering change request at {div}?",
        filler=[
            "No change is made to a released drawing without an approved change record.",
            "Urgent changes follow the same steps; only the meeting is brought forward.",
        ],
        sections=[
            Section("Change Board", [
                Fact("The change board meets every {v} and needs four members present to decide.",
                     ["Tuesday", "Wednesday", "Thursday", "Monday"], "On which day does the {div} change board meet?"),
                Fact("A change that affects form, fit or function is classed as major and needs customer approval.",
                     ["-"], ""),
            ]),
            Section("Implementation", [
                Fact("Work in progress built to the old drawing is assessed within {v} days of approval.",
                     ["five", "ten", "fifteen"],
                     "After a drawing change is approved at {div}, how quickly is partly built stock assessed?"),
                Fact("Obsolete copies are stamped and withdrawn from the shop floor by the document controller within {v} hours.",
                     ["24", "48", "72"], "How soon are superseded drawings removed from the {div} shop floor?"),
            ]),
        ],
    ),
    DocType(
        key="access", collection="it", code="AC",
        title="{div} Division IT Access Control Standard",
        form_q="What form requests system access at {div}?",
        filler=[
            "Access is given for the job a person does, not for the person.",
            "Shared accounts are not allowed except for the kiosks listed in the appendix.",
        ],
        sections=[
            Section("Passwords", [
                Fact("Passwords are at least {v} characters long and are never reused across systems.",
                     ["12", "14", "16"], "What is the minimum password length at {div}?"),
                Fact("An account locks after {v} failed sign-in attempts in fifteen minutes.",
                     ["five", "six", "ten"], "How many wrong passwords lock a {div} account?"),
            ]),
            Section("Joiners and Leavers", [
                Fact("A leaver's accounts are disabled within {v} hours of their last working day.",
                     ["4", "8", "24"], "How quickly are accounts switched off when someone leaves {div}?"),
                Fact("Managers confirm the access of each team member every {v} months.",
                     ["three", "six", "twelve"], "How often does {div} review user access rights?"),
            ]),
            Section("Privileged Accounts", [
                Fact("Administrator sessions are recorded and the recordings kept for {v} days.",
                     ["30", "90", "180"], "How long does {div} keep admin session recordings?"),
            ]),
        ],
    ),
    DocType(
        key="backup", collection="it", code="BK",
        title="{div} Division Backup and Recovery Standard",
        form_q="Which form requests a restore from backup at {div}?",
        filler=[
            "A backup that has never been restored is not counted as a backup.",
            "Backups are encrypted before they leave the server they were taken from.",
        ],
        sections=[
            Section("Schedule", [
                Fact("File servers are backed up every night at {v} and the job is checked the next morning.",
                     ["01:00", "02:00", "23:30", "03:30"], "What time do {div} file server backups run?"),
                Fact("Daily copies are kept for {v} days and monthly copies for a year.",
                     ["14", "30", "35"], "How long are daily backups retained at {div}?"),
            ]),
            Section("Restore Testing", [
                Fact("A sample restore is carried out every {v} and the result is logged.",
                     ["month", "quarter", "fortnight"], "How often does {div} test restoring from backup?"),
                Fact("The recovery point objective for the finance system is {v} hours.",
                     ["2", "4", "12", "24"],
                     "What is the most data the {div} finance system could lose in a disaster?", paraphrase=True),
            ]),
        ],
    ),
    DocType(
        key="visitor", collection="facilities", code="VA",
        title="{div} Site Visitor Access Procedure",
        form_q="What is the visitor booking form at the {div} site?",
        filler=[
            "Hosts are responsible for their visitors from arrival until they sign out.",
            "Reception keeps the visitor log and hands it to security at the end of each day.",
        ],
        sections=[
            Section("Booking Visitors", [
                Fact("Visits are booked with reception at least {v} hours in advance.",
                     ["24", "48", "72"], "How much notice does {div} reception need for a visitor?"),
                Fact("Visitors from outside the country also need clearance from security {v} working days ahead.",
                     ["five", "seven", "ten"], "How far in advance must foreign visitors to {div} be cleared?"),
            ]),
            Section("On Site", [
                Fact("Badges are worn above the waist and handed back before leaving; a lost one costs {v} dollars.",
                     ["10", "15", "25"], "What is the charge for losing a visitor badge at {div}?"),
                Fact("Photography is not allowed beyond the {v} without written permission.",
                     ["lobby", "canteen", "training room"], "Where may visitors at {div} take photographs?"),
            ]),
        ],
    ),
    DocType(
        key="vehicle", collection="facilities", code="VU",
        title="{div} Site Vehicle Use Policy",
        form_q="Which form books a pool vehicle at {div}?",
        filler=[
            "Drivers check the vehicle before setting off and report any damage they find.",
            "Fines incurred while using a pool vehicle are paid by the driver.",
        ],
        sections=[
            Section("Pool Vehicles", [
                Fact("Pool cars are booked through the facilities desk up to {v} days ahead.",
                     ["7", "14", "21"], "How far ahead can a {div} pool car be reserved?"),
                Fact("They are returned with at least {v} of a tank of fuel.",
                     ["a quarter", "half", "three quarters"], "How much fuel must be in a {div} pool car when returned?"),
            ]),
            Section("After Hours Use", [
                Fact("Keys go back to the night desk by {v} and the shift lead signs the log.",
                     ["21:00", "22:00", "23:00"], "By what time must vehicle keys be returned after hours at {div}?"),
                Fact("No journey longer than {v} miles may start after 18:00 without a second driver.",
                     ["100", "150", "200"], "When does an evening trip from {div} need two drivers?"),
            ]),
        ],
    ),
    DocType(
        key="incident", collection="facilities", code="IR",
        title="{div} Site Incident Reporting Procedure",
        form_q="What is the incident report form at the {div} site?",
        filler=[
            "Reporting a near miss is encouraged and nobody is blamed for doing so.",
            "The scene is left as it is until the supervisor has seen it, unless that is unsafe.",
        ],
        sections=[
            Section("Reporting", [
                Fact("Every injury is reported to the supervisor before the end of the shift and logged within {v} hours.",
                     ["4", "12", "24"], "How quickly must an injury at {div} be logged?"),
                Fact("A near miss is entered in the log within {v} days.",
                     ["two", "three", "five"], "What is the deadline for recording a near miss at {div}?"),
            ]),
            Section("Investigation", [
                Fact("A lost-time injury is investigated by a team of {v} that includes a worker representative.",
                     ["two", "three", "four"], "How many people investigate a lost-time injury at {div}?"),
                Fact("The findings go to the site manager within {v} working days.",
                     ["five", "ten", "fifteen"], "How soon are {div} incident investigation findings due?"),
            ]),
        ],
    ),
]

# Older revisions: these doc types keep a superseded revision B beside the
# current revision C for the first six divisions.
REVISED_TYPES = {"travel", "access", "vehicle"}
REVISED_DIVISIONS = 8

NOTE_TOPICS = {
    "finance": [
        "The {div} travel policy review was moved to next quarter; the allowance question stays open.",
        "Accounts payable asked {div} to stop sending claims by email; the portal is the only route.",
        "Procurement limits at {div} came up again and the director asked for a paper next month.",
        "The retention schedule for {div} is being compared with the group template.",
    ],
    "engineering": [
        "Calibration overdue items at {div} fell from nine to three since the last meeting.",
        "The {div} change board missed quorum twice; members were asked to send deputies.",
        "Torque wrench availability at {div} is limited while two units are at the laboratory.",
    ],
    "it": [
        "Password reset tickets at {div} rose after the new length rule; the help desk will publish a guide.",
        "The {div} backup job failed twice last week because of a full disk, now resolved.",
        "Access reviews at {div} are behind schedule for two departments.",
    ],
    "facilities": [
        "Visitor badge losses at {div} are up; reception will remind hosts.",
        "Two pool cars at {div} came back nearly empty; drivers will be reminded of the rule.",
        "The {div} incident log shows four near misses in the loading bay this month.",
    ],
}

UNANSWERABLE = [
    "What is the parental leave entitlement at {div}?",
    "How many days of remote work per week are allowed at {div}?",
    "What is the {div} policy on company credit card cashback?",
    "Which hazardous waste contractor does {div} use?",
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Placed:
    sentence: str
    query: str
    paraphrase: bool
    page: int = 0
    heading_page: int = 0
    split: bool = False


def _paragraphs_for_section(rng: random.Random, dt: DocType, section: Section,
                            values: Dict[str, str], div: str) -> Tuple[List[List[str]], List[Placed]]:
    """A section as paragraphs (lists of sentences), facts placed after a
    first paragraph of general text so they tend to fall on a later page."""
    pool = section.filler + dt.filler + COMMON_FILLER
    paragraphs: List[List[str]] = [rng.sample(pool, 4)]
    placed: List[Placed] = []
    for i, fact in enumerate(section.facts):
        v = values[f"{section.heading}/{i}"]
        sentence = fact.text.format(v=v)
        para = rng.sample(pool, 3)
        para.insert(rng.randint(0, 3), sentence)
        paragraphs.append(para)
        if fact.q:
            placed.append(Placed(sentence, fact.q.format(div=div, v=v), fact.paraphrase))
    return paragraphs, placed


def build_document(rng: random.Random, dt: DocType, div: str, code: str, *,
                   revision: str, values: Dict[str, str], form_no: str, effective: str,
                   split_one: bool) -> Tuple[dict, List[Placed], str]:
    title = dt.title.format(div=div)
    blocks: List[Tuple[str, object]] = [
        ("text", f"{title}\n\nDocument {dt.code}-{code}-{rng.randint(100, 999)}, revision {revision}, "
                 f"effective {effective}. Owner: {div} {dt.collection} office."),
        ("heading", "1 Purpose and Scope"),
        ("para", [f"This document sets out how {div} handles the matters below.",
                  *rng.sample(dt.filler + COMMON_FILLER, 3)]),
        ("heading", "Definitions"),
        ("para", rng.sample(DEFINITIONS, 3)),
        ("para", rng.sample(DEFINITIONS + COMMON_FILLER, 3)),
        ("heading", "Responsibilities"),
        ("para", rng.sample(RESPONSIBILITIES, 3)),
    ]
    placed: List[Placed] = []
    for n, section in enumerate(dt.sections, start=2):
        blocks.append(("heading", f"{n} {section.heading}"))
        paras, facts = _paragraphs_for_section(rng, dt, section, values, div)
        blocks.extend(("para", p) for p in paras)
        placed.extend(facts)
    blocks.append(("heading", f"{len(dt.sections) + 2} Forms"))
    form_sentence = f"Claims and requests under this document are made on form {form_no}."
    blocks.append(("para", [form_sentence, *rng.sample(COMMON_FILLER, 2)]))
    placed.append(Placed(form_sentence, dt.form_q.format(div=div), False))

    # Lay the blocks into pages. A page break falls between paragraphs, except
    # once per document (when asked) where it cuts through a fact sentence.
    pages: List[str] = []
    current = ""
    heading_page = 1
    fact_page: Dict[str, Tuple[int, int, bool]] = {}
    split_done = not split_one
    for kind, body in blocks:
        if kind == "heading":
            piece = f"{body}\n\n"
            if len(current) + len(piece) > PAGE_CHARS - 200 and current:
                pages.append(current.rstrip())
                current = ""
            current += piece
            heading_page = len(pages) + 1
            continue
        sentences = [body] if kind == "text" else list(body)
        text = " ".join(sentences) + "\n\n"
        fact_here = next((p for p in placed if p.sentence in sentences and p.sentence not in fact_page), None)
        if len(current) + len(text) > PAGE_CHARS and current:
            if not split_done and fact_here is not None and len(current) < PAGE_CHARS + 200:
                # Cut through the fact sentence: its first half closes this
                # page, the rest opens the next.
                cut_at = text.index(fact_here.sentence) + len(fact_here.sentence) // 2
                cut_at = text.rfind(" ", 0, cut_at)
                current += text[:cut_at]
                pages.append(current.rstrip())
                current = text[cut_at + 1:]
                fact_page[fact_here.sentence] = (len(pages), heading_page, True)
                split_done = True
                continue
            pages.append(current.rstrip())
            current = ""
        current += text
        for p in placed:
            if p.sentence in sentences and p.sentence not in fact_page:
                fact_page[p.sentence] = (len(pages) + 1, heading_page, False)
    if current.strip():
        pages.append(current.rstrip())
    for p in placed:
        p.page, p.heading_page, p.split = fact_page[p.sentence]
    filename = f"{_slug(title)}-rev-{revision.lower()}.pdf"
    return {"title": title, "filename": filename, "pages": pages}, placed, title


def _values(rng: random.Random, dt: DocType) -> Dict[str, str]:
    return {f"{s.heading}/{i}": rng.choice(f.values) for s in dt.sections for i, f in enumerate(s.facts)}


def _changed(rng: random.Random, dt: DocType, current: Dict[str, str]) -> Dict[str, str]:
    """An older revision: every value with an alternative takes another one."""
    old = {}
    for s in dt.sections:
        for i, f in enumerate(s.facts):
            k = f"{s.heading}/{i}"
            others = [v for v in f.values if v != current[k]]
            old[k] = rng.choice(others) if others else current[k]
    return old


def generate() -> Tuple[dict, dict]:
    rng = random.Random(SEED)
    documents: List[dict] = []
    candidates: Dict[str, List[dict]] = {k: [] for k in (
        "near_duplicate", "late_page", "cross_page", "paraphrase", "identifier", "revision")}
    form_numbers = set()

    for dt in DOC_TYPES:
        for d_index, (div, code) in enumerate(DIVISIONS):
            values = _values(rng, dt)
            while True:
                form_no = f"{dt.code}-{rng.randint(1000, 9999)}"
                if form_no not in form_numbers:
                    form_numbers.add(form_no)
                    break
            doc_id = f"{dt.key}-{code.lower()}"
            revised = dt.key in REVISED_TYPES and d_index < REVISED_DIVISIONS
            doc, placed, _title = build_document(
                rng, dt, div, code, revision="C" if revised else "B", values=values, form_no=form_no,
                effective=f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}",
                split_one=rng.random() < 0.5,
            )
            documents.append({"id": doc_id, "collection": dt.collection, **doc})
            old_placed: Dict[str, Placed] = {}
            if revised:
                old_values = _changed(rng, dt, values)
                old_doc, old_list, _ = build_document(
                    rng, dt, div, code, revision="B", values=old_values, form_no=form_no,
                    effective=f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", split_one=False,
                )
                old_id = f"{doc_id}-rev-b"
                documents.append({"id": old_id, "collection": dt.collection, "superseded_by": doc_id, **old_doc})
                old_placed = {p.query: p for p in old_list}
            for p in placed:
                ref = f"{doc_id}::{p.sentence}"
                item = {"query": p.query, "relevant": [ref], "collection": dt.collection}
                old = old_placed.get(p.query)
                if old is not None and old.sentence != p.sentence:
                    candidates["revision"].append({**item, "stale": [f"{doc_id}-rev-b::{old.sentence}"]})
                    continue
                if p.sentence.startswith("Claims and requests under this document"):
                    form_no_in = re.search(r"form ([A-Z]{2}-\d{4})", p.sentence).group(1)
                    candidates["identifier"].append({**item, "query": f"form {form_no_in}"})
                    candidates["near_duplicate"].append(item)
                elif p.split:
                    candidates["cross_page"].append(item)
                elif p.paraphrase:
                    candidates["paraphrase"].append(item)
                elif p.page > p.heading_page:
                    candidates["late_page"].append(item)
                else:
                    candidates["near_duplicate"].append(item)

    for collection, topics in NOTE_TOPICS.items():
        for n in range(20):
            div = DIVISIONS[(n * 5) % len(DIVISIONS)][0]
            lines = [t.format(div=d[0]) for t in topics for d in rng.sample(DIVISIONS, 2)]
            rng.shuffle(lines)
            half = len(lines) // 2
            title = f"{collection.title()} Coordination Meeting Notes {n + 1:02d}"
            pages = [
                f"{title}\n\nAttendees from {div} and two other divisions.\n\n" + "\n\n".join(lines[:half]),
                "Actions\n\n" + "\n\n".join(lines[half:]) + "\n\nNext meeting in four weeks.",
            ]
            documents.append({"id": f"notes-{collection}-{n + 1:02d}", "collection": collection,
                              "title": title, "filename": f"{_slug(title)}.docx", "pages": pages})

    quotas = {"near_duplicate": 40, "late_page": 40, "cross_page": 25, "paraphrase": 25,
              "identifier": 20, "revision": 20}
    queries: List[dict] = []
    for category, quota in quotas.items():
        pool = candidates[category]
        chosen = rng.sample(pool, min(quota, len(pool)))
        for n, item in enumerate(chosen, start=1):
            queries.append({"id": f"{category}-{n:02d}", "category": category, **item})
    # The same kinds of question, asked across every collection at once.
    answerable = [q for q in queries if q["category"] in ("near_duplicate", "late_page", "paraphrase")]
    for n, q in enumerate(rng.sample(answerable, 40), start=1):
        queries.append({**q, "id": f"cross_collection-{n:02d}", "category": "cross_collection", "collection": "*"})
    n = 0
    for template in UNANSWERABLE:
        for div in rng.sample([d for d, _ in DIVISIONS], 3):
            n += 1
            queries.append({"id": f"unanswerable-{n:02d}", "category": "unanswerable",
                            "query": template.format(div=div), "relevant": [], "collection": "*"})
    for div in MISSING_DIVISIONS:
        n += 1
        queries.append({"id": f"unanswerable-{n:02d}", "category": "unanswerable",
                        "query": f"What is the {div} per diem for an overnight trip?", "relevant": [],
                        "collection": "finance"})

    categories = ["near_duplicate", "late_page", "cross_page", "paraphrase", "identifier", "revision",
                  "cross_collection", "unanswerable"]
    corpus = {"version": CORPUS_VERSION, "generator": "scripts/make_retrieval_corpus.py", "seed": SEED,
              "documents": documents}
    query_file = {"version": CORPUS_VERSION, "categories": categories, "queries": queries}
    return corpus, query_file


def main() -> int:
    corpus, queries = generate()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("corpus.json", corpus), ("queries.json", queries)):
        text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        (OUT / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    pages = sum(len(d["pages"]) for d in corpus["documents"])
    chars = sum(len(p) for d in corpus["documents"] for p in d["pages"])
    print(f"{len(corpus['documents'])} documents, {pages} pages, {chars // 1000} k characters, "
          f"{len(queries['queries'])} queries -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
