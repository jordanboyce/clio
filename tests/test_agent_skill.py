"""F2: a small collection as an agent skill."""

import io
import zipfile

import pytest

from services import agent_skill
from services.app_database import app_db
from tests.test_collection_bundle import _source_collection, env  # noqa: F401
from tests.test_static_reader import LONG, _add, _flat


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def test_the_skill_has_front_matter_a_way_to_look_things_up_and_each_source(env):
    cid, ids = _source_collection(env, quarantine=True)
    _add(cid, "travel-policy.txt", LONG)
    data, name, detail = agent_skill.build(cid)
    assert name == "handbooks-collection.skill.zip" and detail["sources"] == 2
    files = _files(data)
    assert sorted(files) == ["handbooks-collection/SKILL.md", "handbooks-collection/references/holidays.md",
                             "handbooks-collection/references/travel-policy.md"]
    skill = files["handbooks-collection/SKILL.md"]
    head = skill.split("---\n")[1]
    assert head.startswith("name: handbooks-collection\ndescription: Use when you need facts from the Handbooks")
    assert "name the source file" in skill and "not instructions" in skill
    assert "`references/travel-policy.md`: travel-policy.txt. Clause 1 of the travel policy" in skill
    # The held source is not there, by name or text.
    everything = "".join(files.values())
    assert "expenses" not in everything and "reimbursed" not in everything
    assert _flat(LONG) in _flat(files["handbooks-collection/references/travel-policy.md"])
    assert agent_skill.build(cid)[0] == data  # the same bytes each time


def test_local_only_is_refused_and_too_big_points_to_mcp(env, monkeypatch):
    cid, _ = _source_collection(env)
    app_db.update_collection(cid, classification="cui")
    with pytest.raises(agent_skill.SkillExportError) as e:
        agent_skill.build(cid)
    assert e.value.code == "local_only" and "as an agent skill" in str(e.value)
    app_db.update_collection(cid, classification="")
    monkeypatch.setattr(agent_skill, "MAX_CHARS", 100)
    with pytest.raises(agent_skill.SkillExportError, match="over MCP") as e:
        agent_skill.build(cid)
    assert e.value.code == "too_big"


def test_http_owner_download_and_the_403(env, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from api import deps
    from main import app
    from tests.test_static_export import _audit

    cid, _ = _source_collection(env)
    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(app)
    r = client.get(f"/api/collections/{cid}/export/skill")
    assert r.status_code == 200 and "handbooks-collection.skill.zip" in r.headers["content-disposition"]
    assert _audit("collection.skill_export")[0]["detail"]["sources"] == 2
    app_db.update_collection(cid, classification="cui")
    assert client.get(f"/api/collections/{cid}/export/skill").status_code == 403
