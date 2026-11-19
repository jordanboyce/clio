"""Numeric settings are checked when the server starts and when they are
saved, with a message that names the setting and the range (E5)."""

import logging

import pytest

import config
from services import log_format
from services.config_manager import config_manager


def test_the_shipped_defaults_are_in_range():
    assert config.setting_problems({n: getattr(config.settings, n) for n in config.SETTING_RANGES}) == []


def test_an_overlap_as_large_as_the_chunk_stops_the_start(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE", "500")
    monkeypatch.setenv("CHUNK_OVERLAP", "600")

    with pytest.raises(ValueError) as refused:
        config.Settings()

    assert "CHUNK_OVERLAP must be smaller than CHUNK_SIZE (they are 600 and 500)" in str(refused.value)


@pytest.mark.parametrize("name, value, expected", [
    ("WORKER_THREADS", "0", "WORKER_THREADS must be between 1 and 4096 (it is 0)."),
    ("MAX_CONCURRENT_INDEX_JOBS", "0", "MAX_CONCURRENT_INDEX_JOBS must be between 1 and 64"),
    ("CHUNK_SIZE", "0", "CHUNK_SIZE must be between 50 and 100000"),
    ("DEFAULT_TOP_K", "0", "DEFAULT_TOP_K must be between 1 and 1000"),
    ("MCP_SEMANTIC_WEIGHT", "1.5", "MCP_SEMANTIC_WEIGHT must be between 0.0 and 1.0"),
    ("AUDIT_RETENTION_DAYS", "-1", "AUDIT_RETENTION_DAYS must be at least 0"),
    ("LOG_LEVEL", "LOUD", "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"),
])
def test_each_out_of_range_value_is_named_with_its_range(monkeypatch, name, value, expected):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="Settings out of range") as refused:
        config.Settings()

    assert expected in str(refused.value)


def test_every_problem_is_reported_at_once(monkeypatch):
    monkeypatch.setenv("WORKER_THREADS", "0")
    monkeypatch.setenv("PORT", "70000")

    with pytest.raises(ValueError) as refused:
        config.Settings()

    assert "WORKER_THREADS" in str(refused.value) and "PORT" in str(refused.value)


def test_zero_where_zero_means_off_is_accepted(monkeypatch):
    for name in ("RATE_LIMIT_MCP_PER_MINUTE", "AUDIT_RETENTION_DAYS", "MAX_UPLOAD_FILE_MB",
                 "SCAN_MAX_CONCURRENT", "SOURCE_WATCH_TICK_SECONDS", "MCP_TOKEN_MAX_DAYS"):
        monkeypatch.setenv(name, "0")

    config.Settings()


def test_postgres_needs_an_address(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    monkeypatch.setenv("POSTGRES_URL", "")

    with pytest.raises(ValueError, match="POSTGRES_URL is required when DB_BACKEND is postgresql"):
        config.Settings()


def test_a_save_in_settings_is_checked_against_what_is_in_force(monkeypatch):
    monkeypatch.setattr(config.settings, "chunk_size", 800)

    result = config_manager.update_config({"chunk_overlap": 800})

    assert not result["success"]
    assert result["errors"] == ["chunk_overlap must be smaller than chunk_size (they are 800 and 800)."]
    assert config.settings.chunk_overlap != 800


def test_a_save_out_of_range_names_the_field(monkeypatch):
    result = config_manager.update_config({"mcp_top_k": 0})

    assert not result["success"]
    assert result["errors"] == ["mcp_top_k must be between 1 and 1000 (it is 0)."]


def test_log_level_sets_the_root_level(monkeypatch):
    root = logging.getLogger()
    before = root.level
    try:
        log_format.configure("text", "warning")
        assert root.level == logging.WARNING
        assert config.Settings(log_level="debug").log_level == "debug"
    finally:
        root.setLevel(before)


def test_dead_settings_are_gone():
    assert "schema_version" not in config.Settings.model_fields
    assert "mcp_ai_provider" not in config.Settings.model_fields
    assert "mcp_ai_provider" not in config_manager.get_current_config()


def test_every_setting_is_documented_somewhere():
    """Each setting appears, under one of its names, in an example file or the docs."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sources = [*root.glob(".env*.example"), *root.glob("docs/**/*.md"), root / "README.md",
               *root.glob("docker-compose*.yml"), root / "Dockerfile"]
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in sources if p.is_file())
    missing = []
    for name, field in config.Settings.model_fields.items():
        names = {name.upper()}
        if field.validation_alias is not None and hasattr(field.validation_alias, "choices"):
            names |= {str(c).upper() for c in field.validation_alias.choices}
        if not any(n in text for n in names):
            missing.append(name)
    assert missing == []
