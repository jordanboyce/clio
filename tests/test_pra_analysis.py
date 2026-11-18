"""PRA screening calculations: point probabilities, cut set quantification,
importance measures and cut set generation, on hand-computable models."""

import itertools
import math

import pytest

from services.pra import analysis
from services.pra.analysis import AnalysisError, generate_cut_sets, importance, quantify_cut_sets
from services.pra.model import BasicEvent, FaultTree, Gate, HouseEvent, PraModel


def _model(gates, probs, trees=None, house=None):
    """gates: {name: (type, inputs[, k])}; the first gate is tree TOP-FT's top."""
    m = PraModel("TEST", "open-psa-mef")
    for name, spec in gates.items():
        m.gates[name] = Gate(name, spec[0], list(spec[1]), k=spec[2] if len(spec) > 2 else None)
    for name, p in probs.items():
        m.basic_events[name] = BasicEvent(name, f"event {name}", probability=p)
    for name, state in (house or {}).items():
        m.house_events[name] = HouseEvent(name, state=state)
    first = next(iter(gates))
    m.fault_trees["TOP-FT"] = FaultTree("TOP-FT", top=first, gates=list(gates))
    for name, top in (trees or {}).items():
        m.fault_trees[name] = FaultTree(name, top=top)
    return m


def _sets(result):
    return sorted(sorted(c) for c in result.cut_sets)


# ── point probabilities ────────────────────────────────────────────────────

def test_event_probability_covers_every_kind():
    m = _model({"TOP": ("or", ["A", "H"])}, {"A": 0.25, "NODATA": None}, house={"H": True, "OFF": False})
    m.initiating_events["IE"] = BasicEvent("IE", probability=2.5)
    p = analysis.event_probability
    assert p(m, "A") == 0.25 and p(m, "/A") == 0.75
    assert p(m, "H") == 1.0 and p(m, "/H") == 0.0 and p(m, "OFF") == 0.0
    assert p(m, "IE") == 2.5
    assert p(m, "NODATA") is None and p(m, "TOP") is None and p(m, "NOPE") is None


# ── quantification ─────────────────────────────────────────────────────────

def test_quantify_sorts_and_totals_two_ways():
    m = _model({"TOP": ("or", ["A"])}, {"A": 0.1, "B": 0.2, "C": 0.3})
    q = quantify_cut_sets(m, [["B", "C"], ["A"], ["A", "/B"]])
    assert [c.events for c in q.cut_sets] == [["A"], ["A", "/B"], ["B", "C"]]
    assert [round(c.probability, 12) for c in q.cut_sets] == [0.1, 0.08, 0.06]
    assert q.rare_event == pytest.approx(0.24)
    assert q.mcub == pytest.approx(1 - 0.9 * 0.92 * 0.94)
    assert q.basis == "mcub" and q.missing_events == [] and q.incomplete_cut_sets == 0


def test_quantify_reports_missing_data_and_frequencies():
    m = _model({"TOP": ("or", ["A"])}, {"A": 0.1, "B": None})
    m.initiating_events["IE"] = BasicEvent("IE", probability=3.0)
    q = quantify_cut_sets(m, [["B", "A"], ["A"], ["UNDEFINED"]])
    assert q.missing_events == ["B", "UNDEFINED"] and q.incomplete_cut_sets == 2
    assert q.cut_sets[0].events == ["A"] and q.cut_sets[-1].probability is None
    assert q.rare_event == pytest.approx(0.1)
    # A sequence cut set with the initiating event's frequency: no MCUB.
    q = quantify_cut_sets(m, [["IE", "A"]])
    assert q.mcub is None and q.basis == "rare_event" and q.total == pytest.approx(0.3)
    assert quantify_cut_sets(m, []).mcub == 0.0 and math.copysign(1, quantify_cut_sets(m, []).mcub) == 1


def test_mcub_keeps_precision_for_tiny_values():
    m = _model({"TOP": ("or", ["A"])}, {"A": 1e-12, "B": 2e-12})
    q = quantify_cut_sets(m, [["A"], ["B"]])
    assert q.mcub == pytest.approx(3e-12, rel=1e-9)


# ── importance ─────────────────────────────────────────────────────────────

def test_importance_on_the_mcub_by_hand():
    # Cut sets {A} = 0.1 and {B, C} = 0.06; F = 1 - 0.9 * 0.94 = 0.154.
    m = _model({"TOP": ("or", ["A"])}, {"A": 0.1, "B": 0.2, "C": 0.3})
    res = importance(m, [["A"], ["B", "C"]])
    assert res.basis == "mcub" and res.total == pytest.approx(0.154)
    rows = {r.event: r for r in res.rows}
    a, b = rows["A"], rows["B"]
    assert a.fussell_vesely == pytest.approx((0.154 - 0.06) / 0.154)
    assert a.raw == pytest.approx(1 / 0.154)
    assert a.rrw == pytest.approx(0.154 / 0.06)
    assert a.birnbaum == pytest.approx(1 - 0.06)
    f_b1 = 1 - 0.9 * 0.7
    assert b.fussell_vesely == pytest.approx((0.154 - 0.1) / 0.154)
    assert b.raw == pytest.approx(f_b1 / 0.154)
    assert b.rrw == pytest.approx(0.154 / 0.1)
    assert b.birnbaum == pytest.approx(f_b1 - 0.1)
    assert a.cut_sets == 1 and b.cut_sets == 1


def test_importance_rare_event_basis_matches_the_two_train_hand_values():
    # Two trains of a pump and a valve: cut sets P1P2, P1V2, P2V1, V1V2; F = 0.012.
    m = _model({"TOP": ("or", ["P1"])}, {"V1": 0.04, "V2": 0.05, "P1": 0.06, "P2": 0.07})
    cuts = [["P1", "P2"], ["P1", "V2"], ["P2", "V1"], ["V1", "V2"]]
    res = importance(m, cuts, basis="rare_event")
    assert res.total == pytest.approx(0.012)
    p1 = {r.event: r for r in res.rows}["P1"]
    assert p1.birnbaum == pytest.approx(0.12)
    assert p1.fussell_vesely == pytest.approx(0.6)
    assert p1.raw == pytest.approx(10.4)
    assert p1.rrw == pytest.approx(2.5)
    with pytest.raises(AnalysisError):
        importance(m, cuts, basis="exact")


def test_importance_handles_zero_certain_missing_and_ubiquitous_events():
    m = _model({"TOP": ("or", ["A"])}, {"A": 0.0, "B": 0.5, "C": 1.0, "D": None, "E": 0.2})
    rows = {r.event: r for r in importance(m, [["A", "B"], ["E"]]).rows}
    assert rows["A"].fussell_vesely == 0.0 and rows["A"].raw == pytest.approx((1 - 0.5 * 0.8) / 0.2)
    # E is in every cut set with data -> removing it removes everything: RRW unbounded.
    m.basic_events["A"].probability = 0.1
    res = importance(m, [["E"], ["E", "B"], ["D", "B"]])
    rows = {r.event: r for r in res.rows}
    assert rows["E"].rrw is None and rows["E"].fussell_vesely == pytest.approx(1.0)
    assert rows["D"].fussell_vesely is None and res.missing_events == ["D"] and res.incomplete_cut_sets == 1
    assert analysis.sort_importance(res.rows, "rrw")[0].event == "E"
    assert analysis.sort_importance(res.rows, "fv")[-1].event == "D"
    # A certain cut set: F = 1 and stays 1.
    res = importance(m, [["C"], ["B"]])
    c = {r.event: r for r in res.rows}["C"]
    assert res.total == 1.0 and c.raw == 1.0 and c.rrw == pytest.approx(1 / 0.5)
    # Nothing at all: F = 0, measures undefined.
    m.basic_events["Q"] = BasicEvent("Q", probability=0.0)
    res = importance(m, [["Q"]])
    q = res.rows[0]
    assert res.total == 0.0 and q.fussell_vesely is None and q.raw is None and q.birnbaum == 1.0


# ── generation: gate types ─────────────────────────────────────────────────

def test_and_or_with_shared_event_and_absorption():
    m = _model({"TOP": ("and", ["G1", "G2"]), "G1": ("or", ["A", "B"]), "G2": ("or", ["C", "A"])},
               {"A": 0.1, "B": 0.2, "C": 0.3})
    r = generate_cut_sets(m, "TOP-FT", truncation=0.0)
    assert _sets(r) == [["A"], ["B", "C"]]
    assert r.cut_sets[0] == ["A"] and r.complete and r.stopped is None and not r.non_coherent
    assert r.top_gate == "TOP"
    m2 = _model({"TOP": ("or", ["A", "G"]), "G": ("and", ["A", "B"])}, {"A": 0.1, "B": 0.2})
    assert _sets(generate_cut_sets(m2, "TOP", truncation=0.0)) == [["A"]]


def test_two_out_of_three():
    m = _model({"TOP": ("atleast", ["A", "B", "C"], 2)}, {"A": 0.1, "B": 0.2, "C": 0.3})
    r = generate_cut_sets(m, "TOP-FT", truncation=0.0)
    assert _sets(r) == [["A", "B"], ["A", "C"], ["B", "C"]]
    assert quantify_cut_sets(m, r.cut_sets).rare_event == pytest.approx(0.02 + 0.03 + 0.06)
    # 3 of 4 over gates, one input satisfied through another branch.
    m = _model({"TOP": ("and", ["V", "K"]), "K": ("atleast", ["G1", "G2", "G3", "D"], 3),
                "G1": ("or", ["A"]), "G2": ("or", ["B", "V"]), "G3": ("or", ["C"])},
               {"A": 0.1, "B": 0.1, "C": 0.1, "D": 0.1, "V": 0.5})
    r = generate_cut_sets(m, "TOP", truncation=0.0)
    assert _sets(r) == [["A", "C", "V"], ["A", "D", "V"], ["C", "D", "V"]]


def test_transfers_resolve_to_the_referenced_trees_top():
    gates = {"TOP": ("and", ["X", "T2REF"]), "T2REF": ("transfer", ["T2"]), "G2": ("or", ["Y", "Z"]),
             "TOP-B": ("and", ["X", "T2"])}
    m = _model(gates, {"X": 0.1, "Y": 0.2, "Z": 0.3}, trees={"T2": "G2"})
    assert _sets(generate_cut_sets(m, "TOP-FT", truncation=0.0)) == [["X", "Y"], ["X", "Z"]]
    # A gate input naming a fault tree directly works the same way.
    assert _sets(generate_cut_sets(m, "TOP-B", truncation=0.0)) == [["X", "Y"], ["X", "Z"]]
    # An unresolvable transfer is an undeveloped event, with a warning.
    m.gates["T2REF"].inputs = ["MISSING-FT"]
    r = generate_cut_sets(m, "TOP-FT", truncation=0.0)
    assert _sets(r) == [["T2REF", "X"]] and any("could not be resolved" in w for w in r.warnings)


def test_house_events_prune_the_logic():
    gates = {"TOP": ("or", ["G1", "G2", "G3", "G4"]),
             "G1": ("and", ["A", "HT"]),        # true in AND drops out -> A
             "G2": ("and", ["B", "HF"]),        # false in AND kills the gate
             "G3": ("or", ["HF", "C"]),         # false in OR drops out -> C
             "G4": ("and", ["D", "/HF", "E"])}  # complemented false house event is true
    m = _model(gates, {"A": 0.1, "B": 0.1, "C": 0.1, "D": 0.1, "E": 0.1}, house={"HT": True, "HF": False})
    r = generate_cut_sets(m, "TOP", truncation=0.0)
    assert _sets(r) == [["A"], ["C"], ["D", "E"]] and not r.non_coherent
    m.gates["TOP"].inputs = ["G2"]
    r = generate_cut_sets(m, "TOP", truncation=0.0)
    assert r.cut_sets == [] and any("cannot occur" in w for w in r.warnings)
    m.gates["TOP"].inputs = ["A", "HT"]
    r = generate_cut_sets(m, "TOP", truncation=0.0)
    assert r.cut_sets == [[]] and any("certain" in w for w in r.warnings)


def test_not_gates_keep_or_drop_success_terms():
    m = _model({"TOP": ("and", ["A", "NB"]), "NB": ("not", ["B"])}, {"A": 0.1, "B": 0.2})
    keep = generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")
    assert keep.cut_sets == [["A", "/B"]] and keep.non_coherent
    assert quantify_cut_sets(m, keep.cut_sets).rare_event == pytest.approx(0.08)
    drop = generate_cut_sets(m, "TOP", truncation=0.0)
    assert drop.cut_sets == [["A"]] and drop.non_coherent and any("upper bound" in w for w in drop.warnings)
    # X and /X together is impossible.
    m = _model({"TOP": ("and", ["A", "NA"]), "NA": ("not", ["A"])}, {"A": 0.1})
    assert generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep").cut_sets == []
    with pytest.raises(AnalysisError):
        generate_cut_sets(m, "TOP", success_terms="maybe")


def test_deleting_success_terms_happens_after_the_exact_expansion():
    # (V1 + P1) * (V2 + P2 + /P1): exactly V1/P1 + V1V2 + V1P2 + P1V2 + P1P2;
    # with /P1 deleted and absorbed: V1 + P1V2 + P1P2 (SCRAM's minimal cut sets).
    m = _model({"TOP": ("and", ["T1", "T2"]), "T1": ("or", ["V1", "P1"]), "T2": ("or", ["V2", "P2", "NP1"]),
                "NP1": ("not", ["P1"])}, {"V1": 0.04, "V2": 0.05, "P1": 0.06, "P2": 0.07})
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0)) == [["P1", "P2"], ["P1", "V2"], ["V1"]]
    keep = generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")
    assert _sets(keep) == [["/P1", "V1"], ["P1", "P2"], ["P1", "V2"], ["P2", "V1"], ["V1", "V2"]]


def test_nand_nor_xor_follow_de_morgan():
    probs = {"A": 0.1, "B": 0.2, "C": 0.3}
    m = _model({"TOP": ("and", ["C", "N"]), "N": ("nand", ["A", "B"])}, probs)
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")) == [["/A", "C"], ["/B", "C"]]
    m = _model({"TOP": ("and", ["C", "N"]), "N": ("nor", ["A", "B"])}, probs)
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")) == [["/A", "/B", "C"]]
    m = _model({"TOP": ("xor", ["A", "B"])}, probs)
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")) == [["/A", "B"], ["/B", "A"]]
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0)) == [["A"], ["B"]]
    # Three-input xor is odd parity.
    m = _model({"TOP": ("xor", ["A", "B", "C"])}, probs)
    got = _sets(generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep"))
    assert got == [["/A", "/B", "C"], ["/A", "/C", "B"], ["/B", "/C", "A"], ["A", "B", "C"]]
    # A complemented gate input is pushed down too: /(A + B) = /A * /B.
    m = _model({"TOP": ("and", ["C", "/G"]), "G": ("or", ["A", "B"])}, probs)
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0, success_terms="keep")) == [["/A", "/B", "C"]]


def test_brute_force_agreement_on_a_small_coherent_tree():
    gates = {"TOP": ("or", ["G1", "G2", "K"]), "G1": ("and", ["A", "G3"]), "G2": ("and", ["B", "G3", "C"]),
             "G3": ("or", ["C", "D", "E"]), "K": ("atleast", ["A", "D", "F", "G4"], 3), "G4": ("and", ["E", "B"])}
    probs = dict.fromkeys("ABCDEF", 0.1)
    m = _model(gates, probs)

    def top(true):
        def v(n):
            if n in m.gates:
                g = m.gates[n]
                ins = [v(i) for i in g.inputs]
                return all(ins) if g.type == "and" else any(ins) if g.type == "or" else sum(ins) >= g.k
            return n in true
        return v("TOP")

    events = sorted(probs)
    implicants = [frozenset(c) for r in range(1, 7) for c in itertools.combinations(events, r) if top(set(c))]
    minimal = sorted(sorted(c) for c in implicants if not any(o < c for o in implicants))
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0)) == minimal


# ── generation: guards ─────────────────────────────────────────────────────

def test_truncation_never_drops_a_cut_set_above_the_cutoff_with_shared_events():
    # A naive product of the two OR gates' bounds (1e-3 * 1e-3) would drop {A}.
    m = _model({"TOP": ("and", ["G1", "G2"]), "G1": ("or", ["A", "X"]), "G2": ("or", ["A", "Y"])},
               {"A": 1e-3, "X": 1e-6, "Y": 1e-6})
    r = generate_cut_sets(m, "TOP", truncation=1e-4)
    assert r.cut_sets == [["A"]] and r.truncation == 1e-4 and r.complete
    r = generate_cut_sets(m, "TOP", truncation=1e-13)
    assert _sets(r) == [["A"], ["X", "Y"]]


def test_truncation_and_max_order():
    m = _model({"TOP": ("or", ["A", "G"]), "G": ("and", ["B", "C"])}, {"A": 1e-3, "B": 1e-4, "C": 1e-5})
    assert _sets(generate_cut_sets(m, "TOP", truncation=1e-6)) == [["A"]]
    assert _sets(generate_cut_sets(m, "TOP", truncation=1e-9)) == [["A"], ["B", "C"]]
    assert _sets(generate_cut_sets(m, "TOP", truncation=0.0, max_order=1)) == [["A"]]
    with pytest.raises(AnalysisError):
        generate_cut_sets(m, "TOP", truncation=2.0)
    with pytest.raises(AnalysisError):
        generate_cut_sets(m, "TOP", max_order=0)


def test_max_cut_sets_raises_the_cutoff_and_keeps_the_most_probable():
    probs = {"A": 0.5, "B": 0.4, "C": 0.3, "D": 0.2}
    m = _model({"TOP": ("or", list(probs) + ["G"]), "G": ("and", ["A", "B"])}, probs)
    r = generate_cut_sets(m, "TOP", truncation=0.0, max_cut_sets=2)
    assert r.cut_sets == [["A"], ["B"]] and r.truncation_raised and r.truncation == 0.4 and r.complete
    assert any("cutoff was raised" in w for w in r.warnings)


def _wide_and(n_gates, p):
    gates = {"TOP": ("and", [f"G{i}" for i in range(n_gates)])}
    probs = {}
    for i in range(n_gates):
        gates[f"G{i}"] = ("or", [f"E{i}{j}" for j in range(3)])
        probs.update({f"E{i}{j}": p for j in range(3)})
    return gates, probs


def _clock_that_jumps(after):
    """Reads 0 for the first `after` calls (the search looks once per row), then far later."""
    ticks = itertools.chain(itertools.repeat(0.0, after), itertools.repeat(1e6))
    return lambda: next(ticks)


def test_time_budget_before_the_first_pass_returns_a_partial_list():
    gates, probs = _wide_and(10, 0.9)  # 3**10 products, all above every cutoff
    m = _model(gates, probs)
    r = generate_cut_sets(m, "TOP", truncation=0.0, time_budget=1.0, clock=_clock_that_jumps(60))
    assert not r.complete and r.stopped == "time_budget"
    assert 0 < len(r.cut_sets) < 3 ** 10
    assert any("partial" in w for w in r.warnings)


def test_time_budget_keeps_the_last_finished_pass():
    gates, probs = _wide_and(10, 0.06)  # products of 0.06**10 ~ 6e-13
    gates["TOP"] = ("and", [f"G{i}" for i in range(10)])
    gates = {"ROOT": ("or", ["A", "TOP"]), **gates}
    m = _model(gates, {**probs, "A": 0.5})
    r = generate_cut_sets(m, "ROOT", truncation=1e-15, time_budget=1.0, clock=_clock_that_jumps(40))
    assert r.complete and r.stopped == "time_budget" and r.cut_sets == [["A"]]
    assert 1e-15 < r.truncation <= 1e-11 and r.requested_truncation == 1e-15
    assert any("complete down to cutoff" in w for w in r.warnings)


def test_bad_logic_is_refused():
    m = _model({"TOP": ("and", ["A", "G"]), "G": ("or", ["B", "TOP"])}, {"A": 0.1, "B": 0.1})
    with pytest.raises(AnalysisError, match="cycle"):
        generate_cut_sets(m, "TOP")
    with pytest.raises(AnalysisError, match="neither"):
        generate_cut_sets(m, "NOPE")
    m = _model({"TOP": ("atleast", [f"E{i}" for i in range(40)], 20)}, {})
    with pytest.raises(AnalysisError, match="combinations"):
        generate_cut_sets(m, "TOP")


def test_undefined_and_unsupported_inputs_become_events_without_data():
    m = _model({"TOP": ("or", ["A", "GHOST", "ODD"]), "ODD": ("other", ["A"])}, {"A": 0.1})
    r = generate_cut_sets(m, "TOP", truncation=0.0)
    assert _sets(r) == [["A"], ["GHOST"], ["ODD"]]
    assert any("not defined" in w for w in r.warnings) and any("cannot be analysed" in w for w in r.warnings)
    q = quantify_cut_sets(m, r.cut_sets)
    assert q.missing_events == ["GHOST", "ODD"] and q.rare_event == pytest.approx(0.1)


def test_a_large_generated_model_stays_within_its_guards():
    # 240 trees of shared support logic, ~1200 gates: must finish or stop cleanly.
    m = PraModel("BIG", "saphire-mard")
    for t in range(240):
        top = f"T{t}"
        inputs = []
        for g in range(5):
            name = f"T{t}G{g}"
            m.gates[name] = Gate(name, "or", [f"E{t}-{g}-{j}" for j in range(3)] + [f"SUP{(t + g) % 7}"], tree=top)
            inputs.append(name)
        m.gates[top] = Gate(top, "atleast", inputs[:4] + ([f"T{t - 1}"] if t else []), k=2, tree=top)
        m.fault_trees[top] = FaultTree(top, top=top, gates=[top] + inputs)
    for s in range(7):
        m.gates[f"SUP{s}"] = Gate(f"SUP{s}", "and", [f"S{s}a", f"S{s}b"])
    for name in {i for g in m.gates.values() for i in g.inputs if i not in m.gates}:
        m.basic_events[name] = BasicEvent(name, probability=1e-3)
    assert len(m.gates) > 1200
    r = generate_cut_sets(m, "T239", truncation=1e-12, max_cut_sets=2000, time_budget=2.0)
    assert r.cut_sets and r.complete and r.stats["elapsed_seconds"] < 4
    assert len(r.cut_sets) <= 2000
    assert all(quantify_cut_sets(m, [c]).rare_event >= r.truncation for c in r.cut_sets[:50])
