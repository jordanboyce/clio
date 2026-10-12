"""AI enhancement service supporting Anthropic, OpenAI, and Ollama providers.

Provides optional AI-powered search enhancements:
- Result reranking: reorder results by actual relevance using LLM judgment
- Result synthesis: generate a coherent answer with citations from top results

Users provide their own API keys for cloud providers (Anthropic, OpenAI).
Ollama runs locally and requires no API key.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Dict

logger = logging.getLogger(__name__)


def detect_ollama(base_url: str = "http://localhost:11434") -> Dict:
    """Detect if Ollama is running and list available models.

    Returns:
        dict with keys:
            - available (bool): Whether Ollama is accessible
            - models (list): List of available model dicts with 'name' and 'size'
            - error (str, optional): Error message if detection failed
    """
    import httpx

    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = []
            for model in data.get("models", []):
                models.append({
                    "name": model.get("name", ""),
                    "size": model.get("size", 0),
                    "modified_at": model.get("modified_at", ""),
                })

            return {
                "available": True,
                "models": models,
                "base_url": base_url,
            }

    except httpx.ConnectError:
        return {
            "available": False,
            "models": [],
            "error": "Ollama is not running or not accessible",
        }
    except Exception as e:
        return {
            "available": False,
            "models": [],
            "error": str(e),
        }


class AIProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        """Send a prompt and return {'text': str, 'usage': {'input_tokens': int, 'output_tokens': int, 'model': str}}."""

    @abstractmethod
    def validate(self) -> bool:
        """Check if the API key is valid."""

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        """Send a prompt with an image and return the same dict as complete().

        Override in providers that support vision. Default raises NotImplementedError.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support vision/image input")

    def supports_native_tools(self) -> bool:
        """Whether this provider implements `complete_with_tools`."""
        return False

    def list_models(self) -> List[str]:
        """Return available model ids for this provider, best effort.

        Providers that can't enumerate models return []; the frontend falls
        back to its curated recommendations.
        """
        return []

    def complete_with_tools(
        self,
        messages: list,
        tools: list,
        max_tokens: int,
        model: str,
        system: str | None = None,
    ) -> dict:
        """Run one turn of a tool-calling conversation.

        `messages` is the ongoing message list in each provider's native shape
        (appended to across the loop). `tools` is the provider-native tool list.
        Returns a dict:
          {
            "stop_reason": "tool_use" | "end_turn" | "stop",
            "text": str (concatenated assistant text; may be "" when only tool_use),
            "tool_calls": [{"id": str, "name": str, "input": dict}, ...],
            "assistant_message": dict (provider-native message to append verbatim),
            "usage": {"input_tokens": int, "output_tokens": int, "model": str},
          }
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support native tool calling")


class AnthropicProvider(AIProvider):
    """Anthropic Claude provider."""

    PROVIDER_LABEL = "Anthropic"
    KEY_HINT = "https://console.anthropic.com/settings/keys"
    FAST_MODEL = "claude-haiku-4-5-20251001"
    QUALITY_MODEL = "claude-sonnet-5"

    def __init__(self, api_key: str, model: Optional[str] = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "text": response.content[0].text.strip(),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return {
            "text": response.content[0].text.strip(),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": model,
            },
        }

    def supports_native_tools(self) -> bool:
        return True

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None):
        # Prompt caching on system + tool schemas. Both are reused across every
        # iteration of the agent loop and across turns of the same chat, so
        # caching them drops the per-iteration input cost by ~90% and keeps a
        # tool-heavy turn from blowing the org's input-tokens-per-minute budget.
        # One breakpoint on the last tool caches the entire tools array; a
        # second on the system text block caches through the system.
        tools_for_call = tools
        if tools:
            tools_for_call = list(tools[:-1]) + [
                {**tools[-1], "cache_control": {"type": "ephemeral"}}
            ]

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools_for_call,
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        response = self.client.messages.create(**kwargs)

        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        if cache_read or cache_write:
            logger.info(
                "[anthropic] cache read=%d write=%d input=%d output=%d",
                cache_read, cache_write,
                response.usage.input_tokens, response.usage.output_tokens,
            )

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input or {}),
                })
        assistant_message = {
            "role": "assistant",
            # Anthropic expects the raw content blocks back on replay
            "content": [
                (
                    {"type": "text", "text": b.text}
                    if getattr(b, "type", None) == "text"
                    else {
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": dict(b.input or {}),
                    }
                )
                for b in response.content
                if getattr(b, "type", None) in ("text", "tool_use")
            ],
        }
        return {
            "stop_reason": response.stop_reason,
            "text": "".join(text_parts).strip(),
            "tool_calls": tool_calls,
            "assistant_message": assistant_message,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": model,
            },
        }

    def validate(self) -> bool:
        import anthropic
        try:
            self.client.messages.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except anthropic.AuthenticationError:
            logger.warning("%s authentication failed", self.PROVIDER_LABEL)
            return False
        except anthropic.RateLimitError as e:
            # Quota/billing issues mean the key is valid but account has no credits
            logger.warning(f"Anthropic key valid but quota/rate limit: {e}")
            return True  # Key is valid, just no credits or rate limited
        except Exception as e:
            logger.error(f"Anthropic validation error: {e}")
            raise

    def list_models(self) -> List[str]:
        try:
            page = self.client.models.list(limit=100)
            # API returns newest first — keep that order
            return [m.id for m in page.data]
        except Exception as e:
            logger.warning(f"Anthropic model listing failed: {e}")
            return []


class OpenAIProvider(AIProvider):

    PROVIDER_LABEL = "OpenAI"
    KEY_HINT = "https://platform.openai.com/api-keys"
    """OpenAI provider."""

    FAST_MODEL = "gpt-4o-mini"
    QUALITY_MODEL = "gpt-4o"

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: Optional[str] = None):
        from openai import OpenAI
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._base_url = base_url
        self.client = OpenAI(**client_kwargs)
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "text": response.choices[0].message.content.strip(),
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def list_models(self) -> List[str]:
        """List models via the OpenAI-style /models endpoint.

        Inherited by every OpenAI-compatible subclass (Grok, Google, GitHub,
        Ollama Cloud, OpenRouter, custom endpoints) — vLLM, LM Studio, and
        LiteLLM all implement /models too.
        """
        try:
            return sorted(m.id for m in self.client.models.list())
        except Exception as e:
            logger.warning(f"{self.__class__.__name__} model listing failed: {e}")
            return []

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        from openai import OpenAI
        # Use a dedicated client with longer timeouts for vision (large image payloads)
        vision_kwargs: dict = {"api_key": self.client.api_key, "timeout": 120.0}
        if getattr(self, '_base_url', None):
            vision_kwargs["base_url"] = self._base_url
        vision_client = OpenAI(**vision_kwargs)
        response = vision_client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return {
            "text": response.choices[0].message.content.strip(),
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def supports_native_tools(self) -> bool:
        return True

    def complete_with_tools(self, messages, tools, max_tokens, model, system=None):
        import json as _json
        payload_messages = list(messages)
        if system and (not payload_messages or payload_messages[0].get("role") != "system"):
            payload_messages = [{"role": "system", "content": system}] + payload_messages
        request_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": payload_messages,
        }
        if tools:
            request_kwargs["tools"] = tools
        response = self.client.chat.completions.create(**request_kwargs)
        choice = response.choices[0]
        message = choice.message
        text = (message.content or "").strip()
        tool_calls_raw = getattr(message, "tool_calls", None) or []
        tool_calls: list[dict] = []
        for tc in tool_calls_raw:
            args_str = tc.function.arguments or "{}"
            try:
                args = _json.loads(args_str)
            except Exception:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})

        # Rebuild the assistant message in OpenAI's native shape so it can be
        # appended back for the next turn.
        assistant_message: dict = {"role": "assistant", "content": message.content}
        if tool_calls_raw:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in tool_calls_raw
            ]

        stop_map = {"tool_calls": "tool_use", "stop": "end_turn", "length": "end_turn"}
        return {
            "stop_reason": stop_map.get(choice.finish_reason, choice.finish_reason),
            "text": text,
            "tool_calls": tool_calls,
            "assistant_message": assistant_message,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "model": model,
            },
        }

    def validate(self) -> bool:
        # Shared by every OpenAI-compatible subclass (Grok, Google, GitHub,
        # Ollama Cloud, OpenRouter, custom endpoints). Pings FAST_MODEL, which
        # __init__ overrides with the user's selected model when one is chosen —
        # so validation exercises the model that chat will actually use.
        from openai import AuthenticationError, RateLimitError
        try:
            self.client.chat.completions.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except AuthenticationError:
            logger.warning("%s authentication failed", self.PROVIDER_LABEL)
            return False
        except RateLimitError as e:
            # Quota/billing issues mean the key is valid but account has no credits
            logger.warning(f"{self.PROVIDER_LABEL} key valid but rate/quota limited: {e}")
            return True
        except Exception as e:
            logger.error(f"{self.PROVIDER_LABEL} validation error: {e}")
            raise


class OllamaProvider(AIProvider):
    """Ollama provider for local LLM inference."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2",
                 num_ctx: Optional[int] = None):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            model: Model name to use (e.g., llama3.2, mistral, phi3)
            num_ctx: Context window in tokens. Ollama defaults to a small window
                and silently truncates longer prompts, so we always send this.
                Falls back to settings.ollama_num_ctx, then 8192.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx if (num_ctx and num_ctx > 0) else self._default_num_ctx()
        # Ollama uses the model name for both fast and quality
        # Users can override by specifying different models
        self.FAST_MODEL = model
        self.QUALITY_MODEL = model

    @staticmethod
    def _default_num_ctx() -> int:
        """Resolve the configured Ollama context window, defaulting to 8192."""
        try:
            from config import settings
            value = int(getattr(settings, "ollama_num_ctx", 8192) or 8192)
            return value if value > 0 else 8192
        except Exception:
            return 8192

    def list_models(self) -> List[str]:
        result = detect_ollama(self.base_url)
        return [m["name"] for m in result.get("models", [])]

    def _openai_client(self):
        """Return an OpenAI client pointed at Ollama's OpenAI-compatible /v1/ endpoint."""
        from openai import OpenAI
        return OpenAI(
            base_url=f"{self.base_url}/v1",
            api_key="ollama",  # Ollama ignores the key but the client requires a non-empty value
            timeout=300.0,
        )

    def complete(self, prompt: str, max_tokens: int, model: str) -> dict:
        """Generate completion using Ollama's native /api/chat endpoint."""
        import httpx
        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    # num_ctx is essential: without it Ollama uses a tiny default
                    # window and silently truncates the prompt, dropping context.
                    "options": {"num_predict": max_tokens, "num_ctx": self.num_ctx},
                },
            )
            response.raise_for_status()
            data = response.json()

        text = (data.get("message", {}).get("content") or "").strip()
        return {
            "text": text,
            "usage": {
                "input_tokens": data.get("prompt_eval_count", len(prompt) // 4),
                "output_tokens": data.get("eval_count", len(text) // 4),
                "model": model,
            },
        }

    def complete_with_image(self, prompt: str, image_base64: str, media_type: str, max_tokens: int, model: str) -> dict:
        """Send prompt + image using Ollama's native /api/chat endpoint.

        The OpenAI-compatible /v1/chat/completions endpoint defaults to
        num_ctx=2048, which is far too small for page images and causes the
        model to silently return empty output.  The native endpoint lets us
        set num_ctx explicitly, the same way complete() does.
        """
        import httpx

        # Native Ollama API takes raw base64 (no data-URI prefix)
        raw_b64 = image_base64.split(",", 1)[-1] if "," in image_base64 else image_base64

        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": prompt,
                            "images": [raw_b64],
                        }],
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            # Page images need a large window; never go below 16384
                            # even if the configured text num_ctx is smaller.
                            "num_ctx": max(self.num_ctx, 16384),
                        },
                    },
                )
                if response.status_code == 404:
                    raise ValueError(
                        f"Ollama model '{model}' not found. Run: ollama pull {model}"
                    )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama vision request failed: {exc}") from exc

        text = (data.get("message", {}).get("content") or "").strip()
        return {
            "text": text,
            "usage": {
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", len(text) // 4),
                "model": model,
            },
        }

    def validate(self) -> bool:
        """Check if Ollama is running and model is available."""
        import httpx

        try:
            with httpx.Client(timeout=5.0) as client:
                # Check if Ollama is running
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()

                # Check if the specified model is available
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]

                # Ollama model names can have tags (e.g., "llama3.2:latest")
                # Check if our model exists with or without tag
                model_exists = any(
                    self.model in model_name or model_name.startswith(f"{self.model}:")
                    for model_name in models
                )

                if not model_exists:
                    logger.warning(f"Ollama model '{self.model}' not found. Available models: {models}")
                    return False

                logger.info(f"Ollama validation successful. Model '{self.model}' is available.")
                return True

        except httpx.ConnectError:
            logger.warning("Ollama is not running or not accessible at {self.base_url}")
            return False
        except Exception as e:
            logger.error(f"Ollama validation error: {e}")
            return False


class GrokProvider(OpenAIProvider):
    """Grok (xAI) provider — OpenAI-compatible API."""

    PROVIDER_LABEL = "Grok (xAI)"
    KEY_HINT = "https://console.x.ai"
    FAST_MODEL = "grok-3-mini"
    QUALITY_MODEL = "grok-3"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(api_key, model=model, base_url="https://api.x.ai/v1")


class GoogleProvider(OpenAIProvider):
    """Google Gemini provider via the OpenAI-compatible endpoint."""

    PROVIDER_LABEL = "Google Gemini"
    KEY_HINT = "https://aistudio.google.com/apikey"
    FAST_MODEL = "gemini-2.0-flash"
    QUALITY_MODEL = "gemini-2.5-pro-preview-03-25"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(
            api_key,
            model=model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )


class GitHubProvider(OpenAIProvider):
    """GitHub Models provider — OpenAI-compatible API.

    Lets users access models hosted on the GitHub Models marketplace
    (gpt-4o, Llama, Phi, Mistral, DeepSeek, etc.) via a GitHub Personal
    Access Token. Available to anyone with a GitHub account; rate limits
    are higher for paid Copilot subscribers.

    Auth: GitHub PAT with the `models:read` scope.
    Endpoint: https://models.github.ai/inference (OpenAI-compatible).
    Model IDs are namespaced like `openai/gpt-4o`, `meta/Llama-3.3-70B-Instruct`.
    """

    PROVIDER_LABEL = "GitHub Models"
    KEY_HINT = "https://github.com/settings/tokens"
    FAST_MODEL = "openai/gpt-4o-mini"
    QUALITY_MODEL = "openai/gpt-4o"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(
            api_key,
            model=model,
            base_url="https://models.github.ai/inference",
        )


class OllamaCloudProvider(OpenAIProvider):
    """Ollama Cloud provider — hosted OSS models via OpenAI-compatible API.

    Auth: API key from ollama.com (Bearer token; OpenAI SDK handles this).
    Endpoint: https://ollama.com/v1 (OpenAI-compatible chat completions).
    Model tags use plain names (e.g. `gpt-oss:120b`, `gemma4:31b`) — the
    `:cloud` suffix is only used when routing through a local Ollama daemon.
    Supports native function calling and vision (inherited from OpenAIProvider).
    """

    PROVIDER_LABEL = "Ollama Cloud"
    KEY_HINT = "https://ollama.com/settings/keys"
    FAST_MODEL = "gpt-oss:20b"
    QUALITY_MODEL = "gpt-oss:120b"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(api_key, model=model, base_url="https://ollama.com/v1")


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider — one OpenAI-compatible endpoint, hundreds of models.

    OpenRouter proxies Anthropic, OpenAI, Google, Mistral, DeepSeek, Qwen,
    Llama and more behind a single OpenAI-compatible API, so users get broad
    model choice (and automatic failover/cheapest-routing) without us
    integrating each vendor's native SDK.

    Auth: API key from openrouter.ai (Bearer token; OpenAI SDK handles it).
    Endpoint: https://openrouter.ai/api/v1.
    Model IDs are namespaced like `anthropic/claude-3.5-sonnet`,
    `openai/gpt-4o`, `meta-llama/llama-3.3-70b-instruct`.
    """

    PROVIDER_LABEL = "OpenRouter"
    KEY_HINT = "https://openrouter.ai/keys"
    FAST_MODEL = "anthropic/claude-3.5-haiku"
    QUALITY_MODEL = "anthropic/claude-3.5-sonnet"

    def __init__(self, api_key: str, model: Optional[str] = None):
        super().__init__(api_key, model=model, base_url="https://openrouter.ai/api/v1")


class BedrockProvider(AnthropicProvider):
    """AWS Bedrock provider for Anthropic Claude models.

    Reuses the Anthropic SDK's `AnthropicBedrock` client, which speaks the same
    Messages API as the direct Anthropic provider — so every method inherited
    from `AnthropicProvider` (complete, complete_with_tools, native tool use,
    prompt caching, vision) works unchanged. The only difference is the
    transport: requests are signed with AWS SigV4 and billed through the
    customer's AWS account, which is the whole point — it lets enterprises
    consume Claude through an existing AWS contract instead of a separate
    Anthropic bill.

    Auth: standard AWS credential chain (env vars, shared config, or an
    instance/role profile) plus a region. Pass `region` via kwargs or set
    `AWS_REGION`. There is no simple API key, so this provider is configured
    server-side rather than through the per-request BYO-key header flow.

    Requires `boto3` (installed via `pip install "anthropic[bedrock]"`). The
    import is deferred so the rest of the app runs without it.

    Bedrock model IDs differ from the public ones, e.g.
    `anthropic.claude-3-5-sonnet-20241022-v2:0`.
    """

    FAST_MODEL = "anthropic.claude-3-5-haiku-20241022-v1:0"
    QUALITY_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def __init__(
        self,
        model: Optional[str] = None,
        region: Optional[str] = None,
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
    ):
        try:
            from anthropic import AnthropicBedrock
        except ImportError as e:
            raise RuntimeError(
                "AWS Bedrock support requires boto3. Install it with "
                '`pip install "anthropic[bedrock]"`.'
            ) from e

        client_kwargs: dict = {}
        if region:
            client_kwargs["aws_region"] = region
        if aws_access_key and aws_secret_key:
            client_kwargs["aws_access_key"] = aws_access_key
            client_kwargs["aws_secret_key"] = aws_secret_key
        # Note: we intentionally do NOT call super().__init__ — that would build
        # a direct Anthropic client. We only need to swap in the Bedrock client;
        # every inherited method drives `self.client.messages` agnostically.
        self.client = AnthropicBedrock(**client_kwargs)
        if model:
            self.FAST_MODEL = model
            self.QUALITY_MODEL = model

    def validate(self) -> bool:
        try:
            self.client.messages.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as e:
            logger.error(f"AWS Bedrock validation error: {e}")
            raise


class OpenAICompatibleProvider(OpenAIProvider):
    """Generic OpenAI-compatible provider for custom endpoints (vLLM, LM Studio, Groq, etc.)."""

    def __init__(self, api_key: str, base_url: str, model: str = ""):
        # Proxy settings often supply a host rather than the OpenAI API root.
        # Do not alter URLs with a path: gateways can expose custom prefixes.
        base_url = base_url.strip().rstrip("/")
        if base_url.startswith(("http://", "https://")) and "/" not in base_url.split("://", 1)[1]:
            base_url += "/v1"
        super().__init__(api_key or "none", model=model or None, base_url=base_url)
        self._model_configured = bool(model)
        self.FAST_MODEL = model
        self.QUALITY_MODEL = model

    def validate(self) -> bool:
        if not self._model_configured:
            # Connecting an endpoint before choosing a model should check the
            # catalog, not send a completion to a made-up model named "default".
            if not list(self.client.models.list()):
                raise ValueError("The endpoint returned no models; specify a chat model ID.")
        else:
            self.client.chat.completions.create(
                model=self.FAST_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
            )
        # Let the API distinguish authentication, routing, model and TLS errors.
        return True


def apply_deployment_defaults(provider_name: str, api_key, kwargs: dict):
    """Fill in the deployment's configured model/endpoint/key.

    Applies only when the requested provider IS the deployment default
    (AI_PROVIDER), and only to values the caller left empty. That keeps two
    properties an on-prem install depends on:

    - A browser that has configured nothing still chats, against the
      operator's endpoint, with no per-user setup.
    - A caller that brings its own key, model or base URL overrides the
      default rather than silently talking to someone else's endpoint.

    Returns the (api_key, kwargs) to build with.
    """
    from config import settings

    default = (settings.ai_provider or "").strip()
    if not default or provider_name != default:
        return api_key, kwargs

    kwargs = dict(kwargs)
    if not kwargs.get("base_url") and settings.ai_base_url:
        kwargs["base_url"] = settings.ai_base_url
    if not kwargs.get("model") and settings.ai_model:
        kwargs["model"] = settings.ai_model
    if not api_key and settings.ai_api_key:
        api_key = settings.ai_api_key
    return api_key, kwargs


def create_provider(provider_name: str, api_key: str = None, **kwargs) -> AIProvider:
    """Create an AI provider instance.

    Args:
        provider_name: Provider name — anthropic, openai, grok, google, github, openrouter, bedrock, openai_compatible, ollama, ollama_cloud
        api_key: API key for cloud providers (not needed for local Ollama, Bedrock's AWS chain, or key-less endpoints)
        **kwargs:
            model         – model name override
            base_url      – custom base URL (for openai, openai_compatible, ollama)
            region        – AWS region (for bedrock)
            aws_access_key, aws_secret_key – explicit AWS creds (for bedrock; omit to use the default chain)

    Anything the caller leaves out is filled from the deployment default
    (AI_PROVIDER / AI_BASE_URL / AI_MODEL / AI_API_KEY) when it names this
    same provider — see apply_deployment_defaults below.
    """
    # Air-gap enforcement: this factory is the single chokepoint every AI
    # surface goes through (chat, artifacts, vision OCR, schema inference,
    # connection tests). In offline mode only providers that stay on the
    # user's own network are allowed — local Ollama and self-hosted
    # OpenAI-compatible endpoints (vLLM, LM Studio, llama.cpp, …).
    from config import settings
    if settings.offline_mode and provider_name not in ("ollama", "openai_compatible"):
        raise ValueError(
            f"Provider '{provider_name}' is disabled: this deployment runs in "
            f"offline (air-gapped) mode. Use 'ollama' or a self-hosted "
            f"'openai_compatible' endpoint, or unset OFFLINE_MODE."
        )

    # Deployment defaults: an on-prem operator configures the model once in
    # the environment instead of every person configuring it in their own
    # browser. Only fills what the caller omitted, so a per-request header
    # (bring-your-own key or a different model) still wins.
    api_key, kwargs = apply_deployment_defaults(provider_name, api_key, kwargs)

    if provider_name == "anthropic":
        if not api_key:
            raise ValueError("API key required for Anthropic")
        return AnthropicProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "openai":
        if not api_key:
            raise ValueError("API key required for OpenAI")
        return OpenAIProvider(api_key, model=kwargs.get("model"), base_url=kwargs.get("base_url"))
    elif provider_name == "grok":
        if not api_key:
            raise ValueError("API key required for Grok")
        return GrokProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "google":
        if not api_key:
            raise ValueError("API key required for Google")
        return GoogleProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "github":
        if not api_key:
            raise ValueError("API key required for GitHub Models (GitHub PAT with models:read scope)")
        return GitHubProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "openai_compatible":
        base_url = kwargs.get("base_url")
        if not base_url:
            raise ValueError("base_url required for openai_compatible provider")
        return OpenAICompatibleProvider(
            api_key or "none",
            base_url=base_url,
            model=kwargs.get("model") or "",
        )
    elif provider_name == "ollama":
        # The fallback is the configured daemon, not a hardcoded localhost:
        # inside a container "localhost" is the container itself, and the
        # browser only sends X-AI-Base-URL when it differs from the default,
        # so a Docker deployment pointed at host.docker.internal used to get
        # its own loopback here.
        return OllamaProvider(
            base_url=kwargs.get("base_url") or settings.ollama_base_url,
            model=kwargs.get("model", "llama3.2"),
            num_ctx=kwargs.get("num_ctx"),
        )
    elif provider_name == "ollama_cloud":
        if not api_key:
            raise ValueError("API key required for Ollama Cloud (get one at https://ollama.com)")
        return OllamaCloudProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "openrouter":
        if not api_key:
            raise ValueError("API key required for OpenRouter (get one at https://openrouter.ai/keys)")
        return OpenRouterProvider(api_key, model=kwargs.get("model"))
    elif provider_name == "bedrock":
        # Bedrock uses the AWS credential chain, not a simple API key.
        return BedrockProvider(
            model=kwargs.get("model"),
            region=kwargs.get("region"),
            aws_access_key=kwargs.get("aws_access_key"),
            aws_secret_key=kwargs.get("aws_secret_key"),
        )
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


class AIService:
    """Provider-agnostic AI service for search enhancements."""

    def __init__(self, provider: AIProvider):
        self.provider = provider

    @property
    def fast_model(self) -> str:
        return self.provider.FAST_MODEL

    @property
    def quality_model(self) -> str:
        return self.provider.QUALITY_MODEL

    def rerank_results(
        self, query: str, results: List[dict], top_k: int
    ) -> dict:
        """Rerank search results by true relevance to the query.

        This is where AI adds genuine value over cosine similarity:
        vectors measure semantic nearness, but an LLM can judge whether
        a chunk actually answers the question.
        """
        if not results:
            return {"reranked_indices": [], "usage": None}

        snippets = []
        for r in results:
            snippets.append(f"[{r['index']}] (file: {r['filename']}) {r['text_snippet'][:200]}")
        snippet_text = "\n\n".join(snippets)

        prompt = (
            "You are a search result reranking assistant. Given a query and numbered "
            "search results, return the indices of the most relevant results ordered "
            "from most to least relevant.\n\n"
            "Rules:\n"
            "- Return ONLY a JSON array of index numbers, e.g. [3, 1, 7, 2]\n"
            f"- Return at most {top_k} indices\n"
            "- Rank by actual relevance to the query, not just keyword overlap\n"
            "- Exclude results that are not relevant at all\n\n"
            f"Query: {query}\n\n"
            f"Results:\n{snippet_text}"
        )

        start = time.time()
        response = self.provider.complete(prompt, max_tokens=300, model=self.fast_model)
        elapsed = time.time() - start

        raw = response["text"]
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            indices = json.loads(raw)
            if not isinstance(indices, list):
                raise ValueError("Expected a JSON array")
            indices = [int(i) for i in indices]
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse rerank response: {e}. Using original order.")
            indices = [r["index"] for r in results[:top_k]]

        logger.info(f"Reranked {len(results)} results in {elapsed:.2f}s -> top {len(indices)}")

        return {
            "reranked_indices": indices,
            "usage": response["usage"],
        }

    def reformulate_query(self, conversation_history: List[dict], latest_question: str) -> str:
        """Rewrite the user's latest question into a self-contained search query.

        Follow-up questions like "what date was that?" or "tell me more about maverick"
        reference entities established earlier in the conversation. Without reformulation
        the vector search has no access to that prior context and will fail to retrieve
        the right chunks. This step expands the question using conversation history so the
        search query is fully self-contained.

        Returns the reformulated query string (falls back to latest_question on any error).
        """
        if not conversation_history:
            return latest_question

        history_lines = []
        for msg in conversation_history:
            prefix = "User" if msg.get("role") == "user" else "Assistant"
            history_lines.append(f"{prefix}: {msg.get('content', '')}")
        history_text = "\n".join(history_lines)

        prompt = (
            "You are a query reformulation assistant. Your job is to rewrite a follow-up "
            "question into a standalone, search-optimized query by resolving any pronouns "
            "or references using the conversation history.\n\n"
            "Rules:\n"
            "- Output ONLY the reformulated search query — no explanation, no quotes\n"
            "- Expand abbreviations and pronouns using context (e.g. 'that receipt' → "
            "'Maverick Adventure First Stop receipt')\n"
            "- Keep it concise (10–20 words max)\n"
            "- If the question is already self-contained, return it unchanged\n\n"
            f"Conversation history:\n{history_text}\n\n"
            f"Follow-up question: {latest_question}\n\n"
            "Reformulated search query:"
        )

        try:
            response = self.provider.complete(prompt, max_tokens=80, model=self.fast_model)
            reformulated = response["text"].strip().strip('"').strip("'")
            if reformulated:
                logger.info(f"Query reformulated: {latest_question!r} → {reformulated!r}")
                return reformulated
        except Exception as e:
            logger.warning(f"Query reformulation failed, using original: {e}")

        return latest_question

    def synthesize_results(
        self,
        query: str,
        results: List[dict],
        collection_overview: Optional[str] = None,
        structured_context: Optional[str] = None,
    ) -> dict:
        """Generate a coherent answer from search results with citations.

        This is the highest-value AI feature: it turns "here are 10 document
        chunks" into "here is the answer to your question, with sources."

        If `collection_overview` is provided, it is injected into the prompt so
        the model can also answer meta-questions about the collection itself
        (file counts, available documents, date ranges) that aren't answerable
        from the retrieved chunks alone.

        If `structured_context` is provided, it contains full JSONL dumps of
        small CSV/XLSX tables in scope — the model must treat that as the
        authoritative source for any numeric/aggregation question about those
        files, since top-K chunk retrieval would silently truncate the data.
        """
        if not results and not collection_overview and not structured_context:
            return {"synthesis": "", "usage": None}

        context_parts = []
        for i, r in enumerate(results):
            context_parts.append(
                f"[Source {i + 1}: {r['filename']}, page {r['page_number']}]\n"
                f"{r['text_snippet']}"
            )
        context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant passages found."

        overview_section = (
            f"COLLECTION OVERVIEW:\n{collection_overview}\n\n"
            if collection_overview
            else ""
        )
        structured_section = (
            f"{structured_context}\n\n"
            if structured_context
            else ""
        )

        prompt = (
            "You are a research assistant. Based on the information below, "
            "provide a clear, concise answer to the user's query. Cite your sources "
            "using [Source N] notation when referencing retrieved passages — just the "
            "number, e.g. [Source 2], not the filename or page.\n\n"
            "Rules:\n"
            "- Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
            "(file counts, available documents, date ranges, what's in the collection).\n"
            "- If STRUCTURED TABLES are provided, those JSONL dumps are the complete and "
            "authoritative contents of the listed CSV/XLSX files. For any numeric, "
            "aggregation, sum, count, average, filter, date-range, or ranking question "
            "about those files, answer EXCLUSIVELY from the JSONL rows — every row is "
            "present, do not estimate from chunks and do not assume data is missing. "
            "Show your arithmetic when summing so the user can verify.\n"
            "- Use the RETRIEVED SOURCES for questions about prose/document content, and cite "
            "specific sources with [Source N].\n"
            "- If none of the sections contain enough info, say so.\n"
            "- Be concise but thorough.\n"
            "- Use plain language.\n\n"
            f"{overview_section}"
            f"{structured_section}"
            f"Query: {query}\n\n"
            f"RETRIEVED SOURCES:\n{context}"
        )

        start = time.time()
        response = self.provider.complete(prompt, max_tokens=1024, model=self.quality_model)
        elapsed = time.time() - start

        logger.info(f"Synthesized answer in {elapsed:.2f}s ({len(response['text'])} chars)")

        return {
            "synthesis": response["text"],
            "usage": response["usage"],
        }
