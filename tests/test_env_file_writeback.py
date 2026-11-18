"""Saving in Settings mirrors values into the env file the settings were read
from (E8), not into whatever .env is in the working directory, and says so
once when that file cannot be written."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import config
from services import config_manager as cm

REPO = Path(__file__).resolve().parent.parent


def test_the_mirror_is_the_file_settings_reads():
    manager = cm.ConfigManager()

    assert manager.env_file == Path(config.Settings.model_config["env_file"])
    assert manager.env_file.is_absolute()


def test_a_change_of_working_directory_does_not_move_the_mirror(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert cm.ConfigManager().env_file == config.ENV_FILE != tmp_path / ".env"


def test_a_second_instance_in_the_same_folder_writes_only_its_own_file(tmp_path):
    """The reproduction: CLIO_ENV_FILE names another file, the working folder
    holds a different .env, and a save must land in the named one."""
    run = tmp_path / "run"
    run.mkdir()
    (run / ".env").write_text("CHUNK_SIZE=700\n", encoding="utf-8")
    mine = tmp_path / "mine.env"
    mine.write_text("CHUNK_SIZE=900\n", encoding="utf-8")
    env = {**os.environ, "CLIO_ENV_FILE": str(mine), "DATA_DIR": str(tmp_path / "data"),
           "PYTHONPATH": str(REPO)}
    script = (
        "from config import settings\n"
        "from services.config_manager import config_manager\n"
        "assert settings.chunk_size == 900, settings.chunk_size\n"
        "assert config_manager.update_config({'chunk_overlap': 100})['success']\n"
    )

    subprocess.run([sys.executable, "-c", script], cwd=run, env=env, check=True, timeout=120)

    assert (run / ".env").read_text(encoding="utf-8") == "CHUNK_SIZE=700\n"
    assert "CHUNK_OVERLAP=100" in mine.read_text(encoding="utf-8")


def test_an_unwritable_file_is_skipped_with_one_log_line(tmp_path, monkeypatch, caplog):
    from services.app_database import app_db

    target = tmp_path / "readonly.env"
    manager = cm.ConfigManager(env_file=target)
    monkeypatch.setattr(cm.os, "access", lambda path, mode: False)
    before = config.settings.chunk_overlap
    try:
        with caplog.at_level(logging.INFO, logger=cm.logger.name):
            assert manager.update_config({"chunk_overlap": 77})["success"]
            assert manager.update_config({"chunk_overlap": 78})["success"]
    finally:
        config.settings.chunk_overlap = before
        app_db.delete_config("chunk_overlap")

    assert not target.exists()
    lines = [r for r in caplog.records if "not writable" in r.getMessage()]
    assert len(lines) == 1
    assert str(target) in lines[0].getMessage()
