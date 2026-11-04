"""Credentials are encrypted in the database and plain everywhere above it."""

import logging
import os
import sqlite3
import stat
import sys

import pytest

from config import settings
from services import secret_box
from services.app_database import SQLiteBackend

KEY = "sk-test-not-a-real-key"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "secret_encryption_key", "")
    monkeypatch.setattr(secret_box, "_cached", None)
    yield tmp_path
    secret_box._cached = None


@pytest.fixture()
def db(data_dir):
    return SQLiteBackend(data_dir / "app.db")


def _raw(db, table, key):
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def test_seal_round_trip(data_dir):
    sealed = secret_box.seal(KEY)
    assert sealed.startswith(secret_box.SEALED_PREFIX)
    assert KEY not in sealed
    assert secret_box.unseal(sealed) == KEY


def test_empty_and_sealed_values_pass_through(data_dir):
    assert secret_box.seal("") == ""
    assert secret_box.seal(None) is None
    sealed = secret_box.seal(KEY)
    assert secret_box.seal(sealed) == sealed


def test_value_from_before_encryption_reads_as_it_is(data_dir):
    assert secret_box.unseal(KEY) == KEY


def test_empty_values_do_not_create_a_key_file(data_dir):
    secret_box.seal("")
    assert not secret_box.key_path().exists()


def test_generated_key_is_reused(data_dir, monkeypatch):
    sealed = secret_box.seal(KEY)
    assert secret_box.key_path().exists()
    monkeypatch.setattr(secret_box, "_cached", None)
    assert secret_box.unseal(sealed) == KEY


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_generated_key_is_private(data_dir):
    secret_box.seal(KEY)
    assert stat.S_IMODE(os.stat(secret_box.key_path()).st_mode) == 0o600


def test_operator_key_is_used_instead_of_a_file(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "secret_encryption_key", "an-operator-key-of-some-length")
    sealed = secret_box.seal(KEY)
    assert not secret_box.key_path().exists()
    assert secret_box.unseal(sealed) == KEY


def test_wrong_key_reads_empty_rather_than_failing(data_dir, monkeypatch, caplog):
    monkeypatch.setattr(settings, "secret_encryption_key", "an-operator-key-of-some-length")
    sealed = secret_box.seal(KEY)
    monkeypatch.setattr(settings, "secret_encryption_key", "a-different-key-of-some-length")
    with caplog.at_level(logging.WARNING):
        assert secret_box.unseal(sealed) == ""
    assert "cannot be decrypted" in caplog.text


def test_corrupt_value_reads_empty(data_dir):
    assert secret_box.unseal(secret_box.SEALED_PREFIX + "not-a-token") == ""


def test_short_operator_key_refused_at_startup(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "secret_encryption_key", "short")
    with pytest.raises(RuntimeError, match="at least 16"):
        secret_box.validate_config()


def test_no_operator_key_is_fine_at_startup(data_dir):
    secret_box.validate_config()


def test_team_key_is_encrypted_on_disk_and_plain_to_callers(db):
    db.set_agent_api_key("openai", KEY)
    assert KEY not in _raw(db, "user_preferences", "agent_api_key_openai")
    assert db.get_agent_api_key("openai") == KEY
    assert db.get_agent_config()["providers"]["openai"]["configured"] is True


def test_credential_config_field_is_encrypted_on_disk_and_plain_to_callers(db):
    db.set_config("ai_api_key", KEY)
    assert KEY not in _raw(db, "config", "ai_api_key")
    assert db.get_config("ai_api_key") == KEY
    assert db.get_all_config()["ai_api_key"] == KEY


def test_ordinary_config_stays_plain_and_keeps_its_type(db):
    db.set_config("chunk_size", 900)
    db.set_config("ai_model", "llama")
    assert _raw(db, "config", "chunk_size") == "900"
    assert _raw(db, "config", "ai_model") == "llama"
    assert db.get_config("chunk_size") == 900
    assert db.get_config("missing", "fallback") == "fallback"


def test_clearing_a_credential_stores_nothing_to_decrypt(db):
    db.set_config("ai_api_key", "")
    assert _raw(db, "config", "ai_api_key") == ""
    assert db.get_config("ai_api_key") == ""


def test_credentials_from_before_encryption_are_sealed_at_startup(db):
    db._write_config("embedding_api_key", KEY)
    db.set_user_preference("agent_api_key_google", KEY)
    db._write_config("ai_model", "not-a-secret")

    assert db.seal_stored_secrets() == 2

    assert KEY not in _raw(db, "config", "embedding_api_key")
    assert KEY not in _raw(db, "user_preferences", "agent_api_key_google")
    assert db.get_config("embedding_api_key") == KEY
    assert db.get_agent_api_key("google") == KEY
    assert _raw(db, "config", "ai_model") == "not-a-secret"


def test_sealing_twice_changes_nothing(db):
    db._write_config("embedding_api_key", KEY)
    db.seal_stored_secrets()
    first = _raw(db, "config", "embedding_api_key")
    assert db.seal_stored_secrets() == 0
    assert _raw(db, "config", "embedding_api_key") == first


def test_sealing_leaves_other_preferences_alone(db):
    db.set_user_preference("theme", "dark")
    assert db.seal_stored_secrets() == 0
    assert db.get_user_preference("theme") == "dark"


def test_credentials_never_reach_the_log(db, caplog):
    with caplog.at_level(logging.DEBUG):
        db.set_config("ai_api_key", KEY)
        db.set_agent_api_key("openai", KEY)
    assert KEY not in caplog.text


def test_saving_settings_keeps_credentials_out_of_the_env_file(tmp_path, monkeypatch):
    """A plain copy in .env would undo the encryption in the database."""
    from services.app_database import app_db
    from services.config_manager import config_manager

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config_manager, "env_file", env_file)
    previous = app_db.get_all_config()
    try:
        config_manager.update_config({"vision_ocr_api_key": KEY, "vision_ocr_model": "some-model"})
    finally:
        app_db.set_config("vision_ocr_api_key", previous.get("vision_ocr_api_key", ""))
        app_db.set_config("vision_ocr_model", previous.get("vision_ocr_model", ""))
        monkeypatch.setattr(settings, "vision_ocr_api_key", previous.get("vision_ocr_api_key", ""))
        monkeypatch.setattr(settings, "vision_ocr_model", previous.get("vision_ocr_model", ""))

    written = env_file.read_text()
    assert KEY not in written
    assert "VISION_OCR_MODEL=some-model" in written


def test_saving_only_a_credential_leaves_the_env_file_alone(tmp_path, monkeypatch):
    from services.app_database import app_db
    from services.config_manager import config_manager

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config_manager, "env_file", env_file)
    previous = app_db.get_all_config().get("vision_ocr_api_key", "")
    try:
        config_manager.update_config({"vision_ocr_api_key": KEY})
    finally:
        app_db.set_config("vision_ocr_api_key", previous)
        monkeypatch.setattr(settings, "vision_ocr_api_key", previous)

    assert not env_file.exists()
