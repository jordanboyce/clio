"""Every feature the footer reports can be changed from Settings, and a change
there is what the footer then says."""

import pytest

from config import settings
from services import feature_status as fs
from services.config_manager import config_manager

FIELDS = ("enable_reranker", "extract_figures", "enable_transcription", "whisper_model",
          "research_lookup_enabled", "research_contact_email", "link_indexing_enabled", "git_allowed_hosts")


@pytest.fixture(autouse=True)
def _put_settings_back():
    from services.app_database import app_db

    before = {name: getattr(settings, name) for name in FIELDS}
    yield
    for name, value in before.items():
        setattr(settings, name, value)
        app_db.delete_config(name)


def _save(**updates):
    return config_manager.update_config(updates)


@pytest.mark.parametrize("field, check", [
    ("extract_figures", fs._figures),
    ("enable_transcription", fs._transcription),
    ("link_indexing_enabled", fs._links),
])
def test_a_feature_switched_off_in_settings_reports_off(field, check):
    assert _save(**{field: False})["success"]

    report = check()

    assert report["state"] == fs.OFF
    assert "Settings" in report["fix"]


def test_lookups_switched_off_limit_research_and_say_where_to_turn_them_on():
    assert _save(research_lookup_enabled=False)["success"]

    report = fs._research()

    assert report["state"] == fs.LIMITED and "Settings" in report["fix"]


def test_every_fix_points_at_settings_not_at_an_environment_variable(monkeypatch):
    monkeypatch.setattr(settings, "offline_mode", False)
    for name in ("extract_figures", "enable_transcription", "research_lookup_enabled", "link_indexing_enabled"):
        monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(settings, "enable_reranker", False)
    monkeypatch.setattr(settings, "enable_mcp", False)
    monkeypatch.setattr(settings, "pii_scan_action", "off")
    monkeypatch.setattr(settings, "ai_provider", "")

    for feature in fs.feature_status():
        if feature["id"] in ("reranker", "transcription", "figures", "ai", "research", "links", "mcp", "pii"):
            assert "Settings" in feature["fix"], feature
            assert "=" not in feature["fix"], feature


def test_the_reranker_can_go_back_to_automatic():
    assert _save(enable_reranker=False)["success"]
    assert settings.enable_reranker is False

    assert _save(enable_reranker="auto")["success"]

    assert settings.enable_reranker is None
    assert config_manager.get_current_config()["enable_reranker"] is None


def test_automatic_does_not_leave_a_line_the_next_start_cannot_read():
    """ENABLE_RERANKER=None in .env would stop the server starting."""
    _save(enable_reranker=True)
    assert "ENABLE_RERANKER=True" in config_manager.env_file.read_text()

    _save(enable_reranker=None)

    assert "ENABLE_RERANKER" not in config_manager.env_file.read_text()


@pytest.mark.parametrize("updates", [
    {"extract_figures": "yes"},
    {"enable_reranker": "sometimes"},
    {"whisper_model": "/etc"},
    {"research_contact_email": "not an address"},
    {"git_allowed_hosts": "https://git.example/repo"},
])
def test_a_value_of_the_wrong_kind_is_refused_and_nothing_changes(updates):
    before = {name: getattr(settings, name) for name in updates}

    result = _save(**updates)

    assert not result["success"] and result["errors"]
    assert {name: getattr(settings, name) for name in updates} == before


def test_git_hosts_are_saved_tidied():
    assert _save(git_allowed_hosts=" Git.Corp.Example ,*.mirror.example,")["success"]

    assert settings.git_allowed_hosts == "git.corp.example, *.mirror.example"


def test_an_open_server_with_a_git_token_keeps_its_host_list(monkeypatch):
    """The token goes to every allowed host, and here anyone can open Settings."""
    monkeypatch.setattr(settings, "git_http_token", "ghp_secret")
    monkeypatch.setattr(settings, "private_collections", False)
    monkeypatch.setattr(settings, "admin_emails", "")

    result = _save(git_allowed_hosts="attacker.example")

    assert not result["success"]
    assert settings.git_allowed_hosts != "attacker.example"


def test_a_recording_is_refused_with_a_reason_when_transcription_is_off(tmp_path):
    from services.document_extractor import DocumentExtractor

    _save(enable_transcription=False)
    recording = tmp_path / "meeting.mp3"
    recording.write_bytes(b"not really audio")

    with pytest.raises(RuntimeError, match="transcription is turned off"):
        DocumentExtractor()._extract_audio(recording)


def test_choosing_another_speech_model_replaces_the_loaded_one():
    from services import audio_transcriber

    first = audio_transcriber.get_transcriber(model_size="base")
    assert audio_transcriber.get_transcriber(model_size="base") is first

    assert audio_transcriber.get_transcriber(model_size="small").model_size == "small"


def test_settings_do_not_show_where_a_model_folder_is(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "whisper_model", str(tmp_path / "models" / "whisper-custom"))

    shown = config_manager.get_current_config()["whisper_model"]

    assert shown == "whisper-custom"
