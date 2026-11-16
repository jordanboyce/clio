"""Generic keyed OpenAI-compatible gateways: local HTTP repro, no live credentials."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api.chat import list_provider_models, validate_api_key
from services.ai_service import create_provider


@pytest.fixture()
def gateway():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never print Authorization headers.

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_request(self):
            auth = self.headers.get("Authorization")
            requests.append((self.command, self.path, auth))
            if auth != "Bearer example-key":
                self.respond(401, {"error": {"message": "Invalid API key", "type": "authentication_error"}})
            elif self.path == "/v1/models" and self.command == "GET":
                self.respond(200, {"object": "list", "data": [{"id": "private-chat-model", "object": "model"}]})
            elif self.path == "/v1/chat/completions" and self.command == "POST":
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length))
                if request.get("model") != "private-chat-model":
                    self.respond(404, {"error": {"message": "Model not found", "type": "invalid_request_error"}})
                else:
                    self.respond(200, {
                        "id": "chatcmpl-test", "object": "chat.completion", "model": "private-chat-model",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Hi"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    })
            else:
                self.respond(404, {"error": {"message": "Route not found"}})

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_custom_connection_with_key_and_no_model_discovers_models(gateway):
    root, requests = gateway
    result = validate_api_key(
        x_ai_provider="openai_compatible", x_ai_key="example-key", x_ai_base_url=root,
        x_ai_model="", x_ollama_model="",
    )
    assert result == {"valid": True, "error": None}
    assert requests == [("GET", "/v1/models", "Bearer example-key")]


def test_custom_connection_with_explicit_model_checks_chat(gateway):
    root, requests = gateway
    result = validate_api_key(
        x_ai_provider="openai_compatible", x_ai_key="example-key", x_ai_base_url=f"{root}/v1/",
        x_ai_model="private-chat-model", x_ollama_model="",
    )
    assert result == {"valid": True, "error": None}
    assert requests == [("POST", "/v1/chat/completions", "Bearer example-key")]


def test_invalid_custom_key_is_reported_as_auth_failure(gateway):
    root, _ = gateway
    result = validate_api_key(
        x_ai_provider="openai_compatible", x_ai_key="wrong-key", x_ai_base_url=root,
        x_ai_model="", x_ollama_model="",
    )
    assert result["valid"] is False
    assert "API key" in result["error"]
    assert "OpenAI rejected" not in result["error"]


def test_custom_provider_preserves_nonroot_api_paths(gateway):
    root, requests = gateway
    provider = create_provider(
        "openai_compatible", "example-key", base_url=f"{root}/v1", model="private-chat-model",
    )
    assert provider.list_models() == ["private-chat-model"]
    assert requests == [("GET", "/v1/models", "Bearer example-key")]


def test_deployment_endpoint_lists_models_with_server_key(gateway, monkeypatch):
    from config import settings

    root, requests = gateway
    monkeypatch.setattr(settings, "ai_provider", "openai_compatible")
    monkeypatch.setattr(settings, "ai_base_url", root)
    monkeypatch.setattr(settings, "ai_api_key", "example-key")

    result = list_provider_models(
        x_ai_provider="openai_compatible", x_ai_base_url="", x_ai_key="",
    )
    assert result["models"] == ["private-chat-model"] and result["error"] is None
    assert [r["id"] for r in result["records"]] == ["private-chat-model"]
    assert requests == [("GET", "/v1/models", "Bearer example-key")]
