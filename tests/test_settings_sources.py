"""A value saved in Settings that overrides the environment is said at start
and listed in Settings, beside everything the environment set (E4, U10)."""

import logging

import pytest
from fastapi.testclient import TestClient

import config
from services import settings_sources
from services.app_database import SQLiteBackend, app_db


@pytest.fixture()
def fresh_db(tmp_path):
    old = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    settings_sources.reset()
    yield app_db
    app_db.db_path = old
    settings_sources.reset()


@pytest.fixture()
def started(fresh_db, monkeypatch):
    """Start the way the server does: a Settings from this environment, then
    the values saved in Settings applied over it."""
    import services.indexer_manager as im

    def start(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        cfg = config.Settings()
        monkeypatch.setattr(im, "settings", cfg)
        im.indexer_manager._apply_db_config()
        return cfg

    return start


def test_an_override_of_the_environment_is_logged_and_listed(started, caplog):
    app_db.set_config("chunk_size", 1200)
    app_db.set_config("default_top_k", 7)  # not set in the environment: no override

    with caplog.at_level(logging.WARNING, logger="services.indexer_manager"):
        cfg = started(CHUNK_SIZE="900")

    assert cfg.chunk_size == 1200 and cfg.default_top_k == 7
    lines = [r.getMessage() for r in caplog.records if r.name == "services.indexer_manager"]
    assert any("CHUNK_SIZE is 900 in the environment, but 1200, saved in Settings, is in force." in line
               for line in lines)
    assert not any("DEFAULT_TOP_K" in line for line in lines)
    assert settings_sources.overrides() == [
        {"name": "CHUNK_SIZE", "field": "chunk_size", "environment": 900, "saved": 1200}]
    listed = {r["name"]: r for r in settings_sources.from_environment(cfg)}
    assert listed["CHUNK_SIZE"]["overridden_in_settings"] is True


def test_a_saved_value_equal_to_the_environment_is_not_an_override(started, caplog):
    app_db.set_config("chunk_size", 900)

    with caplog.at_level(logging.WARNING):
        started(CHUNK_SIZE="900")

    assert settings_sources.overrides() == []
    assert not any("CHUNK_SIZE" in r.getMessage() for r in caplog.records)


def test_a_saved_value_out_of_range_is_skipped_by_name(started, caplog):
    app_db.set_config("chunk_size", 0)

    with caplog.at_level(logging.WARNING):
        cfg = started()

    assert cfg.chunk_size == config.Settings.model_fields["chunk_size"].default
    assert any("Ignoring the value saved in Settings for chunk_size" in r.getMessage() for r in caplog.records)


def test_a_credential_is_never_shown(started):
    app_db.set_config("ai_base_url", "http://saved.internal/v1")
    cfg = started(AI_API_KEY="sk-secret-value", POSTGRES_URL="postgresql://clio:hunter2@db:5432/clio",
                  AI_BASE_URL="http://env.internal/v1")

    listed = {r["name"]: r["value"] for r in settings_sources.from_environment(cfg)}
    assert listed["AI_API_KEY"] == "(set)"
    assert listed["POSTGRES_URL"] == "postgresql://db:5432/clio"
    assert "hunter2" not in str(settings_sources.from_environment(cfg))
    assert settings_sources.overrides()[0]["name"] == "AI_BASE_URL"


def test_saving_in_settings_starts_and_ends_an_override(started, monkeypatch):
    from services import config_manager as cm

    cfg = started(CHUNK_OVERLAP="150")
    monkeypatch.setattr(cm, "settings", cfg)

    assert cm.config_manager.update_config({"chunk_overlap": 120})["success"]
    assert [o["name"] for o in settings_sources.overrides()] == ["CHUNK_OVERLAP"]

    assert cm.config_manager.update_config({"chunk_overlap": 150})["success"]
    assert settings_sources.overrides() == []


def test_the_list_is_served_to_an_admin(fresh_db):
    import main

    r = TestClient(main.app).get("/api/system/settings-sources")

    assert r.status_code == 200
    names = {row["name"] for row in r.json()["environment"]}
    assert "ALLOWED_HOSTS" in names  # set by the test suite's environment
    assert r.json()["overridden"] == []
