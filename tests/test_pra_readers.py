"""PRA readers: Open-PSA MEF and SAPHIRE MAR-D into the shared PraModel.

The fixtures under tests/fixtures/pra/ are synthetic: an aerospace MEF model
(flight-control hydraulics and a thrust-loss event tree) with a report, and a
small SAPHIRE project written in the MAR-D layout (CRLF, a BOM on the .ETL,
Latin-1 in the .BED).
"""

import io
import math
import zipfile
from pathlib import Path

import pytest

import config
from services.format_readers import ArchiveLimitError
from services.pra.readers import (PraFormatError, is_pra_file, is_pra_upload, read_pra_bundle,
                                  read_pra_file)
from services.pra.readers.mard import read_mard
from services.pra.readers.mef import read_mef
from services.pra.tables import model_sheets
from services.pra.text import product_probability, render_pages

FIXTURES = Path(__file__).parent / "fixtures" / "pra"
MEF = FIXTURES / "flight_hydraulics.xml"
REPORT = FIXTURES / "flight_hydraulics_report.xml"
MARD = FIXTURES / "mard"


def _mard(kind: str) -> Path:
    return MARD / f"MINI-PWR.{kind}"


def _mef(body: str) -> bytes:
    return f'<?xml version="1.0"?>\n<opsa-mef name="T">{body}</opsa-mef>'.encode()


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.fixture()
def limits(monkeypatch):
    def _set(**values):
        for name, value in values.items():
            monkeypatch.setattr(config.settings, name, value)
    return _set


# ── Open-PSA MEF ───────────────────────────────────────────────────────────


def test_mef_fault_trees_gates_and_nested_formulas():
    m = read_pra_file(MEF)
    assert m.source_format == "open-psa-mef" and m.name == "FlightHydraulics"
    ft = m.fault_trees["LossOfHydraulics"]
    assert ft.top == "LOSS-HYD" and ft.description.startswith("Loss of all hydraulic")
    assert m.gates["LOSS-HYD"].type == "and"
    # A nested formula becomes a named sub-gate; NOT of an event is "/NAME".
    assert m.gates["SYS-A-FAILS"].inputs == ["EDP-A-FTR", "RES-A-LEAK", "SYS-A-FAILS.1"]
    assert m.gates["SYS-A-FAILS.1"].inputs == ["ENGINE-1-OUT", "/RAT-ARMED"]
    vote = m.gates["SYS-B-FAILS.1"]
    assert (vote.type, vote.k, vote.tree) == ("atleast", 2, "LossOfHydraulics")
    assert "SYS-B-FAILS.1" in ft.gates
    # imply(a, b) is or(not a, b)
    assert m.gates["NO-RESTART.1"].type == "or"
    assert m.gates["NO-RESTART.1"].inputs == ["/APU-UNAVAILABLE", "WINDMILL-START-FAILS"]
    assert m.house_events["RAT-ARMED"].state is True
    assert m.kind_of("/RAT-ARMED") == "house_event"


def test_mef_expressions():
    m = read_pra_file(MEF)
    edp = m.basic_events["EDP-A-FTR"]
    assert edp.probability == pytest.approx(1 - math.exp(-2e-5 * 10))
    assert (edp.failure_rate, edp.mission_time) == (2e-5, 10.0)
    assert m.basic_events["RES-B-LEAK"].probability == pytest.approx(1e-6)
    acmp = m.basic_events["ACMP-1-FTS"]
    assert acmp.probability == pytest.approx(3e-4)
    assert acmp.uncertainty == "lognormal mean=0.0003 EF=3 level=0.95"
    assert m.basic_events["ENG-1-FLAMEOUT"].mission_time == 8760.0
    assert m.basic_events["FUEL-PUMP-1-FTR"].probability is None
    warnings = " ".join(m.warnings)
    assert "periodic-test" in warnings and "FUEL-PUMP-1-FTR" in warnings
    assert "8760 hours" in warnings and "ENG-1-FLAMEOUT" in warnings


def test_mef_ccf_members_are_kept_with_a_warning():
    m = read_pra_file(MEF)
    assert m.basic_events["BRAKE-ACC-1"].probability == pytest.approx(1e-4)
    assert m.basic_events["BRAKE-ACC-1"].attributes["ccf_group"] == "BRAKE-ACCUMULATORS"
    assert any("Common-cause groups are not expanded (BRAKE-ACCUMULATORS)" in w for w in m.warnings)


def test_mef_event_tree():
    m = read_pra_file(MEF)
    et = m.event_trees["ThrustLoss"]
    assert et.initiating_event == "DUAL-ENGINE-ROLLBACK"
    assert et.functional_events == ["RESTART", "HYDRAULICS"]
    assert et.functional_event_descriptions["RESTART"] == "Crew restarts an engine"
    by_name = {s.name: s for s in et.sequences}
    loc = by_name["LOSS-OF-CONTROL"]
    assert [(p.functional_event, p.state) for p in loc.path] == [("RESTART", "failure"), ("HYDRAULICS", "failure")]
    assert loc.logic == ["NO-RESTART", "LOSS-HYD"] and loc.end_state == "LOSS-OF-CONTROL"
    assert by_name["SAFE-FLIGHT"].logic == ["/NO-RESTART"]
    assert m.end_states["FORCED-LANDING"] == "Forced landing"
    assert m.initiating_events["DUAL-ENGINE-ROLLBACK"].description == "Both engines roll back in cruise"


def test_mef_set_with_a_report_carries_cut_sets():
    m = read_pra_bundle([MEF, REPORT])
    cut = m.cut_sets_for("LossOfHydraulics")
    assert (cut.kind, cut.source) == ("fault_tree", "model")
    assert cut.cut_sets[0] == ["EDP-A-FTR", "EDP-B-FTR"]
    assert cut.cut_sets[2][-1] == "/ACMP-2-FTS"
    assert product_probability(m, cut.cut_sets[2]) == pytest.approx(1e-6 * 3e-4 * (1 - 3e-4))


def test_mef_private_names_and_more_formulas():
    m = read_mef([("t.xml", _mef("""
      <define-fault-tree name="FT">
        <define-component name="PUMPS" role="private">
          <define-gate name="G"><or><basic-event name="A"/><basic-event name="B"/></or></define-gate>
        </define-component>
        <define-gate name="TOP">
          <and>
            <gate name="PUMPS.G"/>
            <iff><basic-event name="A"/><basic-event name="C"/></iff>
            <cardinality min="1" max="2">
              <basic-event name="A"/><basic-event name="B"/><basic-event name="C"/>
            </cardinality>
          </and>
        </define-gate>
      </define-fault-tree>
      <model-data>
        <define-basic-event name="A"><float value="0.1"/></define-basic-event>
        <define-basic-event name="B"><beta-deviate><float value="1"/><float value="3"/></beta-deviate></define-basic-event>
        <define-basic-event name="C"><sub><float value="1"/><neg><float value="-0.5"/></neg></sub></define-basic-event>
      </model-data>"""))])
    assert "FT.PUMPS.G" in m.gates and m.gates["TOP"].inputs[0] == "FT.PUMPS.G"
    iff = m.gates[m.gates["TOP"].inputs[1]]
    assert iff.type == "not" and m.gates[iff.inputs[0]].type == "xor"
    card = m.gates[m.gates["TOP"].inputs[2]]
    assert card.type == "and"
    assert [m.gates[i.lstrip("/")].k for i in card.inputs] == [1, 3] and card.inputs[1].startswith("/")
    assert m.basic_events["B"].probability == pytest.approx(0.25)
    assert m.basic_events["C"].probability == pytest.approx(0.5)
    assert m.fault_trees["FT"].top == "TOP"


@pytest.mark.parametrize("prolog", [
    '<!DOCTYPE opsa-mef [<!ENTITY a "aaaa">]>',
    '<!DOCTYPE opsa-mef SYSTEM "http://example.invalid/x.dtd">',
])
def test_mef_refuses_dtds_and_entities(prolog):
    data = f'<?xml version="1.0"?>{prolog}<opsa-mef><model-data/></opsa-mef>'.encode()
    with pytest.raises(PraFormatError, match="DTD or XML entities"):
        read_mef([("bad.xml", data)])


def test_mef_limits(limits):
    many = "".join(f'<define-basic-event name="E{i}"><float value="0.1"/></define-basic-event>'
                   for i in range(200))
    limits(pra_max_elements=100)
    with pytest.raises(PraFormatError, match="PRA_MAX_ELEMENTS"):
        read_mef([("big.xml", _mef(f"<model-data>{many}</model-data>"))])
    limits(pra_max_elements=0, pra_max_file_mb=1)
    with pytest.raises(PraFormatError, match="PRA_MAX_FILE_MB"):
        read_mef([("big.xml", _mef("<!--" + "x" * (2 * 1024 * 1024) + "-->"))])


def test_mef_errors_are_readable():
    with pytest.raises(PraFormatError, match="not well-formed"):
        read_mef([("x.xml", b"<opsa-mef><define-gate></opsa-mef>")])
    with pytest.raises(PraFormatError, match="root is <scram>"):
        read_mef([("x.xml", b"<scram><model/></scram>")])
    with pytest.raises(PraFormatError, match="define no fault trees"):
        read_mef([("x.xml", _mef(""))])


# ── SAPHIRE MAR-D ──────────────────────────────────────────────────────────


def test_mard_ftl_alone_gives_trees_and_gates():
    m = read_pra_file(_mard("FTL"))
    assert m.source_format == "saphire-mard" and m.name == "MINI-PWR"
    assert set(m.fault_trees) == {"AFW", "AFW-SUPPLY", "HPI"}
    assert m.fault_trees["AFW"].top == "AFW"
    assert m.gates["AFW-SUCTION"].type == "atleast" and m.gates["AFW-SUCTION"].k == 2
    # The TRAN line is replaced by the real definition from the AFW-SUPPLY tree.
    assert m.gates["AFW-SUPPLY"].type == "or" and m.gates["AFW-SUPPLY"].tree == "AFW-SUPPLY"
    assert "/HE-CROSSTIE" in m.gates["AFW-TRAIN-B"].inputs
    assert m.kind_of("AFW-MDP-A-FS") == "basic_event"
    assert any("no .BEI file was read" in w for w in m.warnings)


def test_mard_bei_alone_gives_events():
    m = read_pra_file(_mard("BEI"))
    fs = m.basic_events["AFW-MDP-A-FS"]
    assert (fs.probability, fs.calc_type, fs.uncertainty) == (7.94e-4, "1", "beta b=2780")
    assert fs.attributes["correlation_class"] == "MDP-FTS"
    fr = m.basic_events["AFW-MDP-A-FR"]
    assert (fr.probability, fr.failure_rate, fr.mission_time, fr.uncertainty) == (2.88e-4, 1.2e-5, 24.0,
                                                                                  "lognormal EF=3")
    assert m.house_events["HE-CROSSTIE"].state is False
    assert m.initiating_events["IE-TRANS"].probability == pytest.approx(0.676)
    assert "IE-TRANS" not in m.basic_events
    assert any("common-cause (1)" in w for w in m.warnings)


def test_mard_descriptions_alone():
    m = read_pra_file(_mard("BED"))
    assert m.basic_events["AFW-MDP-A-FS"].description == "AFW MOTOR-DRIVEN PUMP A FAILS TO START, ON DEMAND"
    assert m.basic_events["AFW-CST-EMPTY"].description.startswith("Réservoir CST vide")  # Latin-1
    assert read_pra_file(_mard("ESD")).end_states == {"OK": "NO CORE DAMAGE", "CD": "CORE DAMAGE"}
    assert read_pra_file(_mard("FTD")).fault_trees["AFW-SUPPLY"].description == "AFW WATER SUPPLY FAILS"


def test_mard_etl_alone():
    m = read_pra_file(_mard("ETL"))  # starts with a UTF-8 BOM
    et = m.event_trees["TRANS"]
    assert et.initiating_event == "IE-TRANS" and et.functional_events == ["RPS", "AFW", "HPI"]
    assert et.functional_event_descriptions["HPI"] == "HIGH PRESSURE INJECTION"
    assert m.initiating_events["IE-TRANS"].description == "GENERAL TRANSIENT"
    seqs = {s.name: s for s in et.sequences}
    assert [(p.functional_event, p.state) for p in seqs["03"].path] == [
        ("RPS", "success"), ("AFW", "failure"), ("HPI", "failure")]
    assert seqs["03"].end_state == "CD" and seqs["03"].description == "AFW AND HPI LOST"
    assert [(p.functional_event, p.state) for p in seqs["01"].path] == [("RPS", "success"), ("AFW", "success")]
    assert seqs["04"].transfer == "ATWS" and seqs["04"].end_state is None


def test_mard_sequence_logic_and_cut_sets_alone():
    m = read_pra_file(_mard("SQL"))
    assert m.event_trees["TRANS"].sequences[0].logic == ["/RPS", "AFW", "HPI"]
    sqc = read_pra_file(_mard("SQC")).cut_sets[0]
    assert (sqc.context, sqc.kind, sqc.event_tree) == ("03", "sequence", "TRANS")
    # The second cut set is wrapped over two lines ending in "*".
    assert sqc.cut_sets == [["AFW-CCF-FS", "HPI-MDP-FS"], ["AFW-MDP-A-FS", "AFW-MDP-B-FS", "HPI-MOV-CC"]]
    ftc = read_pra_file(_mard("FTC")).cut_sets_for("AFW")
    assert ftc.kind == "fault_tree" and ftc.cut_sets[-1] == ["AFW-MDP-A-FS", "AFW-MDP-B-FS"]


def test_mard_project_combines_into_one_model():
    m = read_pra_bundle(sorted(MARD.iterdir()))
    assert m.counts() == {"fault_trees": 3, "gates": 7, "basic_events": 11, "house_events": 1,
                          "event_trees": 1, "sequences": 4, "cut_set_lists": 2}
    assert m.gates["AFW-TRAIN-A"].description == "AFW TRAIN A FAILS"
    assert m.gates["AFW"].description == "AUXILIARY FEEDWATER FAILS"   # the tree's, from .FTD
    assert m.house_events["HE-CROSSTIE"].description == "AFW TRAIN CROSS-TIE IS OPEN"
    et = m.event_trees["TRANS"]
    assert et.description == "GENERAL TRANSIENT EVENT TREE"
    assert next(s for s in et.sequences if s.name == "03").logic == ["/RPS", "AFW", "HPI"]
    assert not any("no data" in w for w in m.warnings)


def test_mard_unresolved_transfers_and_continued_lines():
    ftl = ("P, TOP =\r\n"
           "TOP                OR   A B\r\n"
           "                        C\r\n"
           "OTHER-FT           TRAN\r\n"
           "^EOS\r\n").encode("latin-1")
    m = read_mard([("P.FTL", ftl)])
    assert m.gates["TOP"].inputs == ["A", "B", "C"]
    assert m.gates["OTHER-FT"].type == "transfer" and m.gates["OTHER-FT"].inputs == ["OTHER-FT"]
    assert any("transfers point at fault trees that are not in these files: OTHER-FT" in w for w in m.warnings)


def test_mard_refuses_what_is_not_mard():
    with pytest.raises(PraFormatError, match="does not read as a SAPHIRE MAR-D"):
        read_mard([("x.FTL", b"<html>not a fault tree</html>")])
    with pytest.raises(PraFormatError, match="not a SAPHIRE MAR-D file"):
        read_mard([("x.PDF", b"%PDF")])


def test_mard_line_limit(limits):
    limits(pra_max_elements=5)
    with pytest.raises(PraFormatError, match="PRA_MAX_ELEMENTS"):
        read_pra_file(_mard("BEI"))


# ── Sniffing, zips, uploads ────────────────────────────────────────────────


SQL_SCRIPT = b"-- schema\nCREATE TABLE pumps (id INTEGER PRIMARY KEY, name TEXT);\nSELECT * FROM pumps;\n"


def test_sniffing_by_content(tmp_path):
    assert is_pra_file(MEF) and is_pra_file(REPORT) and is_pra_file(_mard("SQL"))
    upper = tmp_path / "MINI.FTL"
    upper.write_bytes(_mard("FTL").read_bytes())
    assert is_pra_file(upper)
    (tmp_path / "schema.sql").write_bytes(SQL_SCRIPT)
    (tmp_path / "settings.sql").write_bytes(b"SET search_path =\nfoo;\n")
    (tmp_path / "pom.xml").write_bytes(b"<?xml version='1.0'?><project><name>x</name></project>")
    (tmp_path / "doctype.xml").write_bytes(b"<!DOCTYPE opsa-mef><opsa-mef/>")
    (tmp_path / "fake.ftl").write_bytes(b"<#macro freemarker>")
    for name in ("schema.sql", "settings.sql", "pom.xml", "doctype.xml", "fake.ftl"):
        assert not is_pra_file(tmp_path / name), name
    model = tmp_path / "model.opsa"
    model.write_bytes(MEF.read_bytes())
    assert is_pra_file(model)


def test_zip_export_becomes_one_model(tmp_path):
    members = {f"MINI-PWR_Subs/{p.name}": p.read_bytes() for p in MARD.iterdir()}
    members["README.txt"] = b"SAPHIRE export"
    members["MINI-PWR_Subs/MINI-PWR.BEA"] = b"MINI-PWR =\r\n"
    members["MINI-PWR_Subs/"] = b""
    path = _zip(tmp_path / "mini.zip", members)
    assert is_pra_file(path)
    m = read_pra_file(path)
    assert m.counts()["gates"] == 7 and m.counts()["cut_set_lists"] == 2
    assert any("MINI-PWR.BEA" in w and "not read" in w for w in m.warnings)
    mef = _zip(tmp_path / "mef.zip", {"model/flight.xml": MEF.read_bytes(), "model/report.xml": REPORT.read_bytes()})
    assert read_pra_file(mef).cut_sets_for("LossOfHydraulics") is not None


def test_other_zips_are_not_pra(tmp_path):
    cases = {
        "notes.zip": {"MINI-PWR.FTL": _mard("FTL").read_bytes(), "notes.txt": b"hello"},
        "mixed.zip": {"MINI-PWR.FTL": _mard("FTL").read_bytes(), "flight.xml": MEF.read_bytes()},
        "pom.zip": {"pom.xml": b"<project/>"},
        "readme.zip": {"README.md": b"# nothing else"},
    }
    for name, members in cases.items():
        path = _zip(tmp_path / name, members)
        assert not is_pra_file(path), name
        with pytest.raises(PraFormatError):
            read_pra_file(path)
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04 not really")
    assert not is_pra_file(tmp_path / "broken.zip")


def test_zip_is_checked_with_the_archive_limits(tmp_path, limits):
    path = _zip(tmp_path / "mini.zip", {p.name: p.read_bytes() for p in MARD.iterdir()})
    limits(archive_max_members=3)
    with pytest.raises(ArchiveLimitError, match="ARCHIVE_MAX_MEMBERS"):
        read_pra_file(path)


def test_upload_sniffing_rewinds_the_stream(tmp_path):
    path = _zip(tmp_path / "mini.zip", {p.name: p.read_bytes() for p in MARD.iterdir()})
    stream = io.BytesIO(path.read_bytes())
    stream.seek(10)
    assert is_pra_upload("mini.zip", stream) and stream.tell() == 0
    assert not is_pra_upload("schema.sql", io.BytesIO(SQL_SCRIPT))
    assert is_pra_upload("MINI-PWR.SQL", io.BytesIO(_mard("SQL").read_bytes()))


# ── What reaches the index ─────────────────────────────────────────────────


def test_text_pages_read_as_answers():
    pages = render_pages(read_pra_bundle(sorted(MARD.iterdir())))
    text = "\n".join(pages.values())
    assert pages[1].startswith("# PRA model MINI-PWR")
    assert ("Gate AFW-TRAIN-A (OR) in fault tree AFW — AFW TRAIN A FAILS — fails if any of: "
            "AFW-MDP-A-FS — AFW MOTOR-DRIVEN PUMP A FAILS TO START, ON DEMAND, p=7.94E-04") in text
    assert "Gate AFW-SUCTION (2-OF-3)" in text and "fails if at least 2 of" in text
    assert "NOT HE-CROSSTIE" in text
    assert "Sequences of TRANS ending in CD (CORE DAMAGE): 03." in text
    assert "Sequence 04 of event tree TRANS transfers to event tree ATWS" in text
    assert ("Basic event AFW-MDP-A-FS — AFW MOTOR-DRIVEN PUMP A FAILS TO START, ON DEMAND: "
            "probability 7.94E-04") in text
    assert "Cut sets of fault tree AFW from the model (3)" in text


def test_tables_have_fixed_columns_and_cut_set_products():
    m = read_pra_bundle(sorted(MARD.iterdir()))
    sheets = {s["sheet_name"]: s for s in model_sheets(m)}
    assert set(sheets) == {"basic_events", "gates", "sequences", "cut_sets"}
    assert sheets["sequences"]["type_overrides"]["name"] == "text"
    gate = next(r for r in sheets["gates"]["rows"] if r["name"] == "AFW-SUCTION")
    assert (gate["type"], gate["k"], gate["inputs"]) == ("atleast", 2, "AFW-XV-1 AFW-XV-2 AFW-XV-3")
    kinds = {r["name"]: r["kind"] for r in sheets["basic_events"]["rows"]}
    assert (kinds["IE-TRANS"], kinds["HE-CROSSTIE"], kinds["HPI-MOV-CC"]) == ("initiating", "house", "basic")
    pair = next(r for r in sheets["cut_sets"]["rows"] if r["events"] == "AFW-MDP-A-FS * AFW-MDP-B-FS")
    assert pair["order"] == 2 and pair["probability"] == pytest.approx(7.94e-4 ** 2)


def test_a_custom_type_registered_earlier_keeps_its_non_pra_files(tmp_path, monkeypatch):
    from services.custom_file_types import registry
    from services.document_extractor import is_pra_document

    genomics = tmp_path / "peaks.bed"
    genomics.write_bytes(b"chr1\t100\t200\tpeak1\n")
    assert is_pra_document(genomics)  # refused by the reader, with its reason
    monkeypatch.setattr(registry, "lookup", lambda name, include_disabled=False:
                        {"kind": "data"} if name.endswith(".bed") else None)
    assert not is_pra_document(genomics)
    assert is_pra_document(_mard("BED"))


def test_models_stored_before_the_optional_fields_still_load():
    from services.pra.model import PraModel

    data = read_pra_bundle(sorted(MARD.iterdir())).to_dict()
    for et in data["event_trees"].values():
        et.pop("functional_event_descriptions")
        for s in et["sequences"]:
            s.pop("transfer")
    again = PraModel.from_dict(data)
    assert again.event_trees["TRANS"].sequences[3].transfer is None
    assert again.event_trees["TRANS"].functional_event_descriptions == {}
