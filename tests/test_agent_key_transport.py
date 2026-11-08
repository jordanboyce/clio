"""A team API key travels in the request body, not the URL.

A key in a query string is written to every reverse proxy's and load
balancer's access log on its way in. POST /api/agent/config takes it as JSON;
the older query form still works for scripts written against it, but says it
is deprecated.
"""

import pytest
from fastapi.testclient import TestClient

import api.chat as chat_api
from services.config_manager import config_manager


class _Accepting:
    def validate(self):
        return True


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main
    from services.app_database import app_db

    monkeypatch.setattr(config_manager, "env_file", tmp_path / ".env")
    monkeypatch.setattr(chat_api, "create_provider", lambda provider, key: _Accepting())
    stored = {}
    monkeypatch.setattr(app_db, "set_agent_api_key", lambda provider, key: stored.__setitem__(provider, key))
    c = TestClient(main.app)
    c.stored = stored
    return c


def test_the_key_is_taken_from_the_json_body(client):
    r = client.post("/api/agent/config", json={"provider": "openai", "api_key": "sk-body"})
    assert r.status_code == 200, r.text
    assert client.stored == {"openai": "sk-body"}
    assert "deprecation" not in {k.lower() for k in r.headers}


def test_the_query_form_still_works_but_says_it_is_deprecated(client):
    r = client.post("/api/agent/config", params={"provider": "openai", "api_key": "sk-query"})
    assert r.status_code == 200, r.text
    assert client.stored == {"openai": "sk-query"}
    assert r.headers.get("Deprecation") == "true"


def test_no_key_at_all_is_a_validation_error(client):
    r = client.post("/api/agent/config")
    assert r.status_code == 422
    assert client.stored == {}


def test_an_unknown_provider_is_still_refused(client):
    r = client.post("/api/agent/config", json={"provider": "not-a-provider", "api_key": "x"})
    assert r.status_code == 400
    assert client.stored == {}


def test_the_spec_marks_the_query_parameters_deprecated(client):
    op = client.get("/openapi.json").json()["paths"]["/api/agent/config"]["post"]
    params = {p["name"]: p for p in op.get("parameters", [])}
    assert params["api_key"].get("deprecated") is True
    assert "requestBody" in op
