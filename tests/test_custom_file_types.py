"""Custom file types: JSON-like files with extensions only one product uses.

A profile is declarative data (format, where the records are, which fields
matter, how to label them). It never contains code, so a profile written by an
admin or proposed by a model can be reviewed and cannot do anything but change
which text is indexed. Built-in types always win, and every extracted page
still goes through the injection and content-policy scans.
"""

import json

import pytest

from services import custom_file_types as cft
from services.custom_file_types import ProfileError


# ── lenient parsing ────────────────────────────────────────────────────

def test_strict_json_parses():
    assert cft.parse_lenient('{"a": [1, 2]}') == {"a": [1, 2]}


def test_comments_trailing_commas_and_bom_are_tolerated():
    text = '\ufeff// header\n{\n  "a": 1, /* why */\n  "b": [1, 2,],\n  "url": "http://x//y", \n}\n'
    assert cft.parse_lenient(text) == {"a": 1, "b": [1, 2], "url": "http://x//y"}


def test_comment_markers_inside_strings_are_left_alone():
    assert cft.parse_lenient('{"a": "/* not a comment */ // nor this"}') == {
        "a": "/* not a comment */ // nor this"}


def test_json_lines_and_concatenated_values_become_a_list():
    assert cft.parse_lenient('{"a":1}\n{"a":2}\n') == [{"a": 1}, {"a": 2}]
    assert cft.parse_lenient('{"a":1}{"a":2}') == [{"a": 1}, {"a": 2}]


def test_unparseable_text_names_the_line():
    with pytest.raises(ProfileError, match="line 2"):
        cft.parse_lenient('{"a": 1,\n  oops}')


def test_deeply_nested_input_is_refused_not_a_crash():
    with pytest.raises(ProfileError, match="nest"):
        cft.parse_lenient("[" * 5000 + "]" * 5000)


# ── profile validation ─────────────────────────────────────────────────

def test_profile_is_normalised_and_defaults_filled():
    p = cft.validate_profile(".ACME", {"label": "  Acme config "})
    assert p["extension"] == ".acme" and p["label"] == "Acme config"
    assert p["format"] == "auto" and p["kind"] == "data"
    assert p["records_per_section"] == 10 and p["include"] == [] and p["exclude"] == []


@pytest.mark.parametrize("ext", [".pdf", ".json", ".py", ".md", "pdf", ".PDF"])
def test_builtin_extensions_cannot_be_redefined(ext):
    with pytest.raises(ProfileError, match="built-in"):
        cft.validate_profile(ext, {})


@pytest.mark.parametrize("ext", ["", ".", "acme!", ".a b", ".../x", ".x" * 20, ".exe", ".env", ".dll"])
def test_bad_or_dangerous_extensions_are_refused(ext):
    with pytest.raises(ProfileError):
        cft.validate_profile(ext, {})


def test_extension_without_dot_is_accepted():
    assert cft.validate_profile("acme", {})["extension"] == ".acme"


@pytest.mark.parametrize("bad", [
    {"format": "xml"}, {"kind": "media"}, {"records_per_section": 0}, {"records_per_section": 101},
    {"include": "name"}, {"include": [1]}, {"exclude": ["x" * 300]}, {"labels": {"a": 1}},
    {"title_field": 5}, {"max_value_chars": 5},
])
def test_invalid_fields_are_rejected(bad):
    with pytest.raises(ProfileError):
        cft.validate_profile(".acme", bad)


def test_unknown_keys_are_dropped_not_stored():
    p = cft.validate_profile(".acme", {"label": "x", "exec": "rm -rf /", "__proto__": {}})
    assert "exec" not in p and "__proto__" not in p


# ── rendering ──────────────────────────────────────────────────────────

DEVICES = {
    "schema": "acme/1",
    "devices": [
        {"id": "9f1c2d3e-4b5a-6789-abcd-ef0123456789", "name": "Pump A", "location": {"site": "North", "bay": 3},
         "tags": ["critical", "water"], "notes": None, "enabled": True},
        {"id": "0a1b2c3d-4b5a-6789-abcd-ef0123456789", "name": "Pump B", "location": {"site": "South", "bay": 1},
         "tags": [], "notes": "", "enabled": False},
    ],
}


def _profile(**over):
    return cft.validate_profile(".acme", {"label": "Acme", **over})


def test_records_are_found_automatically_and_rendered_readably():
    pages = cft.render(DEVICES, _profile())
    text = "\n".join(pages.values())
    assert "Pump A" in text and "location.site: North" in text and "location.bay: 3" in text
    assert "tags: critical, water" in text
    assert "enabled: true" in text and "enabled: false" in text
    assert "notes" not in text                          # null and empty values add nothing
    assert "schema: acme/1" in text                     # sibling metadata is kept, once


def test_title_field_heads_each_record():
    pages = cft.render(DEVICES, _profile(title_field="name"))
    text = pages[1]
    assert text.index("Pump A") < text.index("location.site: North")
    assert text.count("Pump A") == 1                   # the title is not repeated as a field


def test_record_path_selects_the_array():
    data = {"meta": {"x": 1}, "payload": {"rows": [{"n": 1}, {"n": 2}]}, "other": [{"n": 99}]}
    text = "\n".join(cft.render(data, _profile(record_path="payload.rows")).values())
    assert "n: 1" in text and "n: 2" in text and "99" not in text


def test_missing_record_path_is_a_clear_error():
    with pytest.raises(ProfileError, match="record_path"):
        cft.render(DEVICES, _profile(record_path="nope.rows"))


def test_include_and_exclude_use_dotted_paths_without_indices():
    text = "\n".join(cft.render(DEVICES, _profile(include=["name", "location.*"])).values())
    assert "Pump A" in text and "location.site" in text and "tags" not in text and "enabled" not in text
    text = "\n".join(cft.render(DEVICES, _profile(exclude=["id", "location.bay"])).values())
    assert "9f1c2d3e" not in text and "location.bay" not in text and "location.site" in text


def test_labels_rename_fields_for_search():
    text = "\n".join(cft.render(DEVICES, _profile(labels={"location.bay": "Bay number"})).values())
    assert "Bay number: 3" in text and "location.bay" not in text


def test_records_are_grouped_into_sections():
    data = [{"n": i} for i in range(25)]
    pages = cft.render(data, _profile(records_per_section=10))
    assert sorted(pages) == [1, 2, 3]
    assert "n: 24" in pages[3] and "n: 0" in pages[1]


def test_root_object_without_records_becomes_one_section_per_top_level_key():
    data = {"network": {"host": "10.0.0.1", "port": 80}, "logging": {"level": "debug"}}
    pages = cft.render(data, _profile())
    assert len(pages) == 2
    assert "network" in pages[1] and "host: 10.0.0.1" in pages[1]
    assert "logging" in pages[2] and "level: debug" in pages[2]


def test_nested_lists_of_objects_keep_their_fields():
    data = [{"name": "Line 1", "stops": [{"at": "A", "min": 5}, {"at": "B", "min": 7}]}]
    text = "\n".join(cft.render(data, _profile()).values())
    assert "stops[1].at: A" in text and "stops[2].min: 7" in text


def test_long_values_are_truncated_with_a_marker():
    text = "\n".join(cft.render([{"blob": "x" * 5000}], _profile(max_value_chars=200)).values())
    assert "…" in text and len(text) < 600


def test_scalar_and_empty_documents_still_render():
    assert cft.render({}, _profile()) == {1: "{}"}
    assert cft.render([], _profile()) == {1: "[]"}
    assert cft.render("just text", _profile()) == {1: "just text"}


def test_rendering_is_deterministic():
    assert cft.render(DEVICES, _profile()) == cft.render(json.loads(json.dumps(DEVICES)), _profile())


# ── registry ───────────────────────────────────────────────────────────

@pytest.fixture()
def registry(tmp_path):
    return cft.CustomFileTypeRegistry(lambda: tmp_path / "custom_file_types.json", ttl=0)


def test_registry_round_trip_and_lookup(registry, tmp_path):
    registry.put(".acme", {"label": "Acme"}, user="admin@example.com")
    assert registry.lookup("plant/pumps.ACME")["label"] == "Acme"
    assert registry.lookup("pumps.json") is None and registry.lookup("Makefile") is None
    saved = json.loads((tmp_path / "custom_file_types.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1 and saved["types"][".acme"]["updated_by"] == "admin@example.com"
    assert (tmp_path / "custom_file_types.json").read_bytes().endswith(b"\n")


def test_registry_delete_and_listing(registry):
    registry.put(".b", {"label": "B"})
    registry.put(".a", {"label": "A"})
    assert [p["extension"] for p in registry.all()] == [".a", ".b"]
    assert registry.delete(".a") is True and registry.delete(".a") is False
    assert [p["extension"] for p in registry.all()] == [".b"]


def test_disabled_types_are_not_indexed(registry):
    registry.put(".acme", {"label": "x", "enabled": False})
    assert registry.lookup("a.acme") is None
    assert registry.all()[0]["enabled"] is False        # still listed so it can be re-enabled


def test_corrupt_registry_file_is_ignored_not_fatal(registry, tmp_path):
    (tmp_path / "custom_file_types.json").write_text("{not json", encoding="utf-8")
    assert registry.lookup("a.acme") is None and registry.all() == []
    registry.put(".acme", {"label": "x"})               # and it can be repaired by saving
    assert registry.lookup("a.acme") is not None


def test_a_hand_edited_invalid_entry_is_skipped_but_others_load(registry, tmp_path):
    (tmp_path / "custom_file_types.json").write_text(json.dumps({"version": 1, "types": {
        ".good": {"label": "ok"}, ".pdf": {"label": "evil"}, ".bad": {"records_per_section": 0}}}), encoding="utf-8")
    assert [p["extension"] for p in registry.all()] == [".good"]


def test_changes_made_by_another_process_are_seen(tmp_path):
    a = cft.CustomFileTypeRegistry(lambda: tmp_path / "t.json", ttl=0)
    b = cft.CustomFileTypeRegistry(lambda: tmp_path / "t.json", ttl=0)
    assert a.lookup("x.acme") is None
    b.put(".acme", {"label": "x"})
    assert a.lookup("x.acme") is not None
    b.delete(".acme")
    assert a.lookup("x.acme") is None
