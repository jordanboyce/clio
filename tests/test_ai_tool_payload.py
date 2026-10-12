import json

import httpx
from openai import OpenAI

from services.ai_service import OpenAICompatibleProvider


def test_openai_compatible_omits_only_empty_tool_definitions():
    payloads = []

    def handle_request(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload.get("tools") == []:
            return httpx.Response(
                400,
                json={"error": {
                    "message": "`tools` must not be an empty array. Either provide at least one tool or omit the field entirely.",
                    "type": "BadRequestError",
                    "param": "tools",
                    "code": 400,
                }},
                request=request,
            )
        return httpx.Response(200, json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "private-chat-model",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hi"},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }, request=request)

    provider = object.__new__(OpenAICompatibleProvider)
    provider.client = OpenAI(
        api_key="example-key",
        base_url="https://internal.example/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle_request)),
    )

    result = provider.complete_with_tools(
        messages=[{"role": "user", "content": "Summarize the evidence."}],
        tools=[], max_tokens=256, model="private-chat-model",
    )
    assert result["text"] == "Hi"
    assert "tools" not in payloads[0]

    tool = {"type": "function", "function": {"name": "search_documents", "parameters": {"type": "object"}}}
    provider.complete_with_tools(
        messages=[{"role": "user", "content": "Search the evidence."}],
        tools=[tool], max_tokens=256, model="private-chat-model",
    )
    assert payloads[1]["tools"] == [tool]
    provider.client.close()
