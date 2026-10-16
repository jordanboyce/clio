"""CLIO_PROFILE=secure, the readiness report and the preflight script."""

import json

import pytest

import config
from config import settings
from services import readiness


def _fresh_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return config.Settings()


@pytest.fixture
def secure(monkeypatch, tmp_path):
    """The live settings object, switched to a well-formed secure deployment."""
    monkeypatch.setattr(settings, "clio_profile", "secure")
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "cors_allow_origins", "")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return settings


# ── Profile defaults ─────────────────────────────────────────────────────

def test_standard_profile_changes_nothing(monkeypatch):
    s = _fresh_settings(monkeypatch)
    assert s.clio_profile == "standard"
    assert s.offline_mode is False and s.link_indexing_enabled is True


def test_secure_profile_sets_hardened_defaults(monkeypatch):
    s = _fresh_settings(monkeypatch, CLIO_PROFILE="secure")
    assert s.offline_mode is True
    assert s.mcp_audit_tool_calls is True
    assert s.link_indexing_enabled is False


def test_an_explicit_setting_beats_the_profile_default(monkeypatch):
    s = _fresh_settings(monkeypatch, CLIO_PROFILE="secure", LINK_INDEXING_ENABLED="true", MCP_AUDIT_TOOL_CALLS="false")
    assert s.link_indexing_enabled is True      # the operator chose it
    assert s.mcp_audit_tool_calls is False
    assert s.offline_mode is True               # not chosen, so the profile decides


def test_unknown_profile_is_rejected(monkeypatch):
    with pytest.raises(Exception):
        _fresh_settings(monkeypatch, CLIO_PROFILE="paranoid")


# ── Startup guard ────────────────────────────────────────────────────────

def test_secure_profile_is_clean_on_a_well_formed_deployment(secure):
    assert readiness.secure_profile_errors() == []


def test_standard_profile_never_reports_fatal_errors(secure, monkeypatch):
    monkeypatch.setattr(settings, "clio_profile", "standard")
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "cors_allow_origins", "*")
    assert readiness.secure_profile_errors() == []


def test_network_exposure_without_a_gate_is_fatal(secure, monkeypatch):
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    errors = readiness.secure_profile_errors()
    assert len(errors) == 1 and "AUTH_PASSWORD" in errors[0]
    monkeypatch.setattr(settings, "auth_password", "a-strong-secret")
    assert readiness.secure_profile_errors() == []


def test_wildcard_cors_without_a_gate_is_fatal_in_secure_only(secure, monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "*")
    assert any("CORS" in e for e in readiness.secure_profile_errors())


def test_offline_mode_forced_off_is_fatal(secure, monkeypatch):
    monkeypatch.setattr(settings, "offline_mode", False)
    assert any("OFFLINE_MODE" in e for e in readiness.secure_profile_errors())


def test_hosted_embedding_and_chat_providers_are_fatal(secure, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    errors = " ".join(readiness.secure_profile_errors())
    assert "EMBEDDING_PROVIDER=openai" in errors and "AI_PROVIDER=anthropic" in errors


def test_own_endpoints_are_fine(secure, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    assert readiness.secure_profile_errors() == []


def test_startup_refuses_an_unsafe_secure_deployment(secure, monkeypatch):
    import main

    monkeypatch.setattr(settings, "host", "0.0.0.0")
    with pytest.raises(RuntimeError, match="CLIO_PROFILE=secure refuses to start"):
        main._check_security_posture()
    monkeypatch.setattr(settings, "auth_password", "a-strong-secret")
    main._check_security_posture()  # now fine


def test_standard_profile_only_warns_when_exposed(secure, monkeypatch):
    import main

    monkeypatch.setattr(settings, "clio_profile", "standard")
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    main._check_security_posture()  # logs a warning, does not raise


# ── Report ───────────────────────────────────────────────────────────────

def _by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_report_shape_and_counts(secure):
    report = readiness.run_checks()
    assert set(report) >= {"ready", "profile", "offline_mode", "counts", "checks", "egress", "probed"}
    assert report["profile"] == "secure" and report["probed"] is False
    assert sum(report["counts"].values()) == len(report["checks"])
    assert all(c["status"] in ("pass", "warn", "fail") for c in report["checks"])
    assert {"posture", "models", "storage"} <= {c["group"] for c in report["checks"]}


def test_missing_embedding_model_offline_is_not_ready(secure, monkeypatch):
    from services import embedder

    monkeypatch.setattr(embedder, "local_model_cached", lambda *a, **k: False)
    monkeypatch.setattr(embedder, "resolve_local_backend", lambda *a, **k: "fastembed")
    report = readiness.run_checks()
    check = _by_id(report)["embedding"]
    assert check["status"] == "fail" and "OFFLINE_BUNDLE" in check["fix"]
    assert report["ready"] is False


def test_cached_embedding_model_passes(secure, monkeypatch):
    from services import embedder

    monkeypatch.setattr(embedder, "local_model_cached", lambda *a, **k: True)
    monkeypatch.setattr(embedder, "resolve_local_backend", lambda *a, **k: "fastembed")
    assert _by_id(readiness.run_checks())["embedding"]["status"] == "pass"


def test_unwritable_data_dir_fails(secure, monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "data_dir", blocker / "sub")
    assert _by_id(readiness.run_checks())["data_dir"]["status"] == "fail"


def test_reranker_gap_is_a_warning_not_a_failure(secure, monkeypatch):
    from services import reranker

    monkeypatch.setattr(settings, "enable_reranker", None)
    monkeypatch.setattr(reranker, "_locally_available", lambda name: False)
    check = _by_id(readiness.run_checks())["reranker"]
    assert check["status"] == "warn"


def test_exposure_checks_downgrade_to_warnings_outside_the_secure_profile(secure, monkeypatch):
    monkeypatch.setattr(settings, "clio_profile", "standard")
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "offline_mode", False)
    checks = _by_id(readiness.run_checks())
    assert checks["auth"]["status"] == "warn" and checks["offline"]["status"] == "warn"


def test_ai_probe_reports_reachability(secure, monkeypatch):
    import httpx

    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", "http://llm.internal:8000/v1")
    seen = []

    class Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.append(url) or Resp())
    assert _by_id(readiness.run_checks(probe=False))["ai"]["status"] == "pass"
    assert seen == []                                   # no network without probe
    assert _by_id(readiness.run_checks(probe=True))["ai"]["status"] == "pass"
    assert seen == ["http://llm.internal:8000/v1/models"]

    def refuse(url, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", refuse)
    check = _by_id(readiness.run_checks(probe=True))["ai"]
    assert check["status"] == "fail" and "ConnectError" in check["detail"]


def test_a_crashing_check_becomes_a_failed_check(secure, monkeypatch):
    monkeypatch.setattr(readiness, "_storage_checks", lambda: 1 / 0)
    report = readiness.run_checks()
    assert report["ready"] is False
    assert any(c["group"] == "errors" for c in report["checks"])


# ── Egress ───────────────────────────────────────────────────────────────

def _egress(kind):
    return next(e for e in readiness.egress_paths() if e["kind"] == kind)


def test_egress_reflects_offline_mode(secure, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "offline_mode", True)
    assert _egress("Embeddings")["state"] == "blocked"
    monkeypatch.setattr(settings, "offline_mode", False)
    assert _egress("Embeddings")["state"] == "open"
    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert _egress("Embeddings")["state"] == "none"


def test_egress_lists_configured_internal_destinations(secure, monkeypatch):
    monkeypatch.setattr(settings, "git_allowed_hosts", "git.corp.example")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://chat.corp.example/hooks/x")
    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", "http://llm.internal:8000/v1")
    git = _egress("Git remotes")
    assert git["state"] == "internal" and git["destination"] == "git.corp.example"
    assert _egress("Change alerts")["destination"] == "chat.corp.example"
    assert _egress("Chat model")["destination"] == "http://llm.internal:8000/v1"
    assert _egress("Telemetry / analytics")["state"] == "none"


# ── Endpoints and script ─────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def test_readiness_endpoint(client, secure):
    r = client.get("/api/system/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] == "secure" and body["checks"] and body["egress"]


def test_health_reports_the_profile(client, secure):
    assert client.get("/health").json()["profile"] == "secure"


def test_readiness_endpoint_is_admin_only_when_the_gate_is_active(client, secure, monkeypatch):
    from services import admins

    monkeypatch.setattr(admins, "admin_gate_active", lambda: True)
    monkeypatch.setattr(admins, "is_admin", lambda user: False)
    r = client.get("/api/system/readiness")
    assert r.status_code in (401, 403)


def test_preflight_script_json_and_exit_codes(secure, monkeypatch, capsys):
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "preflight", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "preflight.py")
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)

    def report(fail=0, warn=0):
        return {"ready": fail == 0, "profile": "secure", "offline_mode": True,
                "counts": {"pass": 3, "warn": warn, "fail": fail}, "probed": False,
                "checks": [{"id": "x", "group": "posture", "title": "T", "status": "pass", "detail": "d", "fix": ""}],
                "egress": [{"kind": "Embeddings", "destination": "in-process", "state": "none", "note": ""}]}

    monkeypatch.setattr(readiness, "run_checks", lambda probe=False: report())
    assert preflight.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True

    monkeypatch.setattr(readiness, "run_checks", lambda probe=False: report(warn=1))
    assert preflight.main([]) == 0
    assert "READY" in capsys.readouterr().out
    assert preflight.main(["--strict"]) == 1

    monkeypatch.setattr(readiness, "run_checks", lambda probe=False: report(fail=1))
    assert preflight.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out
