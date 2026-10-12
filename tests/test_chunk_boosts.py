"""Manual per-chunk ranking intervention (chunk_boosts)."""

import pytest


def _store(tmp_path):
    from services.metadata_store import MetadataStore
    return MetadataStore(tmp_path / "meta.db")


def test_set_and_get_boost(tmp_path):
    store = _store(tmp_path)
    store.set_chunk_boost("c1", 0.5, note="important")
    assert store.get_chunk_boosts(["c1"]) == {"c1": 0.5}


def test_boost_clamped(tmp_path):
    store = _store(tmp_path)
    store.set_chunk_boost("c1", 5.0)
    assert store.get_chunk_boosts(["c1"])["c1"] == 1.0
    store.set_chunk_boost("c1", -9.0)
    assert store.get_chunk_boosts(["c1"])["c1"] == -1.0


def test_zero_boost_clears_row(tmp_path):
    store = _store(tmp_path)
    store.set_chunk_boost("c1", 0.7)
    store.set_chunk_boost("c1", 0.0)
    assert store.get_chunk_boosts(["c1"]) == {}


def test_batch_get_omits_missing(tmp_path):
    store = _store(tmp_path)
    store.set_chunk_boost("c1", 0.3)
    assert store.get_chunk_boosts(["c1", "nope"]) == {"c1": 0.3}


def test_get_all_boosts(tmp_path):
    store = _store(tmp_path)
    store.set_chunk_boost("c1", 0.4, note="pin")
    store.set_chunk_boost("c2", -0.6)
    all_boosts = store.get_all_chunk_boosts()
    assert all_boosts["c1"]["boost"] == 0.4
    assert all_boosts["c1"]["note"] == "pin"
    assert all_boosts["c2"]["boost"] == -0.6
