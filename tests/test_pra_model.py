"""The shared PRA model contract: lookups and a lossless JSON round trip."""

import json

import pytest

from services.pra.model import (BasicEvent, CutSetList, EventTree, FaultTree, Gate, PraModel,
                                Sequence, SequencePathStep)
from services.pra.store import delete_model, load_model, save_model


def _model():
    m = PraModel(name="PUMPS", source_format="open-psa-mef", source_files=["pumps.xml"])
    m.fault_trees["PUMPS-FT"] = FaultTree("PUMPS-FT", top="TOP", gates=["TOP", "TRAIN-A"])
    m.gates["TOP"] = Gate("TOP", "and", ["TRAIN-A", "PUMP-B"], tree="PUMPS-FT")
    m.gates["TRAIN-A"] = Gate("TRAIN-A", "atleast", ["PUMP-A", "VALVE-A", "/HOUSE"], k=2, tree="PUMPS-FT")
    m.basic_events["PUMP-A"] = BasicEvent("PUMP-A", "Pump A fails to run", probability=1e-3,
                                          failure_rate=1e-5, mission_time=100.0, attributes={"cat": "MDP"})
    m.basic_events["PUMP-B"] = BasicEvent("PUMP-B", probability=2e-3)
    m.event_trees["LOOP"] = EventTree("LOOP", initiating_event="IE-LOOP", functional_events=["PUMPS-FT"],
                                      sequences=[Sequence("LOOP-2", end_state="CD",
                                                          path=[SequencePathStep("PUMPS-FT", "failure")],
                                                          logic=["PUMPS-FT"])])
    m.cut_sets.append(CutSetList("PUMPS-FT", cut_sets=[["PUMP-A", "PUMP-B"]], truncation=1e-12))
    m.warnings.append("example")
    return m


def test_round_trip_is_lossless(tmp_path):
    m = _model()
    again = PraModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert again == m
    save_model(tmp_path, "abc123", m)
    assert load_model(tmp_path, "abc123") == m
    assert delete_model(tmp_path, "abc123") and load_model(tmp_path, "abc123") is None


def test_lookups():
    m = _model()
    assert m.kind_of("TRAIN-A") == "gate" and m.kind_of("/PUMP-A") == "basic_event"
    assert m.kind_of("PUMPS-FT") == "fault_tree" and m.kind_of("NOPE") is None
    assert m.top_gate("PUMPS-FT") == "TOP"
    assert m.cut_sets_for("PUMPS-FT").cut_sets == [["PUMP-A", "PUMP-B"]]
    assert m.counts()["sequences"] == 1


def test_store_refuses_path_like_ids_and_survives_bad_files(tmp_path):
    with pytest.raises(ValueError):
        save_model(tmp_path, "../x", _model())
    (tmp_path / "pra").mkdir()
    (tmp_path / "pra" / "bad.json").write_text("{not json")
    assert load_model(tmp_path, "bad") is None


def test_newer_schema_is_refused():
    d = _model().to_dict()
    d["schema_version"] = 99
    with pytest.raises(ValueError):
        PraModel.from_dict(d)
