"""Configuration management for Clio API."""

import os
from pathlib import Path
from typing import Literal
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloud providers that authenticate with a simple API key, passed per-request
# via the X-AI-Key header or stored server-side as a team key via
# /api/agent/config. AWS Bedrock is intentionally absent: it uses the AWS
# SigV4 credential chain (configured server-side), not a header key.
CLOUD_AI_PROVIDERS = ("anthropic", "openai", "grok", "google", "github", "openrouter", "ollama_cloud", "openai_compatible")
ALL_AI_PROVIDERS = CLOUD_AI_PROVIDERS + ("ollama",)


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Air-gapped / offline mode. When True:
    #   - Cloud AI providers (Anthropic, OpenAI, Grok, Google, GitHub,
    #     OpenRouter, Ollama Cloud, Bedrock) are disabled — only local Ollama
    #     and self-hosted OpenAI-compatible endpoints can be used.
    #   - HuggingFace hub access is disabled (HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE),
    #     so models load from the local cache only and never attempt a download.
    # The app makes NO outbound network connections beyond the endpoints you
    # explicitly configure on your own network. See docs/AIRGAP.md.
    offline_mode: bool = False

    # Data storage
    data_dir: Path = Path("./data")

    # Embedding configuration — where document chunks and queries become
    # vectors. The full catalog (labels, costs, models, endpoints) lives in
    # services/embedding_providers.py; ids as of writing:
    #   local              sentence-transformers in-process (default)
    #   google | mistral | voyage | jina | openrouter | openai
    #                      hosted APIs speaking the OpenAI embeddings shape;
    #                      chunks are sent out at index time and queries at
    #                      search time, so pick these only when that egress
    #                      is acceptable (OFFLINE_MODE refuses them)
    #   ollama             a self-hosted Ollama daemon at OLLAMA_BASE_URL
    #   openai_compatible  any OpenAI-style endpoint at EMBEDDING_BASE_URL
    #   ollama_cloud | xai hidden from the picker: neither serves an
    #                      embedding model today (ollama.com rejects the
    #                      call; xAI documents the route but lists no model)
    embedding_provider: str = "local"
    # Model for the "local" provider (a sentence-transformers name).
    embedding_model: str = "all-MiniLM-L6-v2"
    # Which in-process runtime serves the "local" provider:
    #   auto                  fastembed (ONNX, no PyTorch) when it is installed
    #                         and knows the model, else sentence-transformers
    #   fastembed             only fastembed (`pip install fastembed`)
    #   sentence-transformers only the PyTorch backend
    #                         (`pip install -r requirements-torch.txt`)
    # Both backends run the same weights, so switching never forces a
    # re-index (see services/embedder.py: embedding_signature).
    local_embedding_backend: Literal["auto", "fastembed", "sentence-transformers"] = "auto"
    # Model for every non-local provider; empty = the provider's default.
    # OLLAMA_EMBEDDING_MODEL is the pre-2026-09 name and still works.
    remote_embedding_model: str = Field(
        "", validation_alias=AliasChoices("REMOTE_EMBEDDING_MODEL", "OLLAMA_EMBEDDING_MODEL")
    )
    # Base URL for embedding_provider="openai_compatible" (…/v1).
    embedding_base_url: str = ""
    # Key for the selected embedding provider. Empty falls back to the team
    # key saved on the matching AI Providers card (Google, OpenAI,
    # OpenRouter, …), so a key added for chat is reused. Providers without a
    # chat card (Mistral, Voyage, Jina) need this set.
    embedding_api_key: str = ""
    # Ollama Cloud API key, used by embedding_provider="ollama_cloud" and as
    # the automatic key for vision OCR when VISION_OCR_PROVIDER=ollama_cloud.
    # Accepted under either env name (people reasonably write both). Empty
    # falls back to the team key saved on the Ollama Cloud provider card
    # (Settings → AI Providers).
    ollama_cloud_api_key: str = Field(
        "", validation_alias=AliasChoices("OLLAMA_CLOUD_API_KEY", "OLLAMA_CLOUD_TOKEN")
    )
    ollama_base_url: str = "http://localhost:11434"   # used for embeddings and inference

    # ── Deployment default AI provider ───────────────────────────────────
    # The LLM this deployment uses when a caller does not bring its own.
    # Without it, every person configures a provider in their own browser
    # (keys and endpoint URLs live in localStorage) — workable for a laptop
    # install, wrong for an on-prem appliance where the operator owns the
    # model and nobody should be typing an internal URL into a settings
    # form. Set these and chat, artifacts, vision OCR, schema inference and
    # the content-policy reviewer all reach the same endpoint out of the
    # box; the frontend offers it as an already-connected provider.
    #
    #   AI_PROVIDER=openai_compatible   any OpenAI-shaped server you run —
    #                                   vLLM, LM Studio, llama.cpp, TGI,
    #                                   Ollama's /v1, LiteLLM, a gateway
    #   AI_PROVIDER=ollama              an Ollama daemon (AI_BASE_URL or
    #                                   OLLAMA_BASE_URL)
    #   AI_PROVIDER=anthropic|openai|…  a cloud provider, key in AI_API_KEY
    #
    # AI_BASE_URL is required for openai_compatible (the app refuses to
    # start without it) and optional elsewhere. AI_API_KEY may be empty for
    # endpoints that need no auth. A per-request X-AI-* header still wins,
    # so a person can bring their own key on a deployment that has one.
    ai_provider: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    # What the UI calls it. Empty falls back to the provider id, which reads
    # badly for a private endpoint ("openai_compatible" is not a product
    # anyone recognises); name it after the thing people know.
    ai_provider_label: str = ""

    # Semantic answer cache: single-turn chat questions that closely match a
    # previously answered one are candidate cache hits. The chat layer also
    # requires matching question text and currently accessible sources. Query
    # embeddings use the configured local or hosted embedding provider. Entries are invalidated when any source document the
    # answer cited changes or disappears, and a request with use_cache=false
    # (the Regenerate button) bypasses and replaces the entry. Note the
    # deliberate scope of invalidation: NEW unrelated documents don't evict
    # existing answers, so a cached answer reflects the corpus as of when it
    # was generated until its sources change or it's regenerated.
    enable_answer_cache: bool = True
    answer_cache_threshold: float = 0.9   # default reuse similarity; ChatRequest.cache_threshold overrides per turn
    answer_cache_max_per_scope: int = 200  # LRU cap per collection/scope

    # Share invitations by email via Resend (https://resend.com). When the
    # key is set, a collection owner can email a share invitation directly
    # from the share dialog: the recipient gets the share token and a join
    # link. RESEND_FROM must be a sender your Resend account may use — an
    # address on a domain you verified there (e.g. "Clio
    # <clio@your-domain>"); the default onboarding sender only delivers
    # to your own Resend account's email, so it's for testing. Disabled in
    # OFFLINE_MODE like every outbound integration.
    resend_api_key: str = ""
    resend_from: str = "Clio <onboarding@resend.dev>"

    # Text chunking configuration
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # Search configuration
    default_top_k: int = 10
    max_top_k: int = 50

    # Local cross-encoder reranker (second-stage relevance reordering).
    # Runs locally (no API tokens, no PII egress) and applies to every search
    # caller, including the MCP tools. Opt-in because it downloads/loads a model
    # on first use. When enabled, retrieval fetches a wider candidate pool
    # (top_k * reranker_candidate_multiplier, capped) and the cross-encoder
    # reorders it down to top_k.
    enable_reranker: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_candidate_multiplier: int = 5

    # Entity-graph retrieval boost (services/entity_graph.py). Entities are
    # extracted heuristically per chunk at index time (proper nouns, acronyms,
    # capitalized phrases — no LLM pass and no egress) and stored in the
    # collection database. At query time, chunks that share entities with the
    # query are fused into the semantic/hybrid ranking: cross-document matches
    # enter as candidates and chunks sharing >= 2 distinct query entities get
    # a saturating score bonus, rarer entities weighing more. This is the
    # cheap approximation to GraphRAG's entity-step — it fixes "who worked
    # with X on Y?" questions where the decisive chunk names the entities but
    # doesn't resemble the question. Disable if the heuristic extractor is
    # too noisy on a corpus of e.g. sentence-case acronyms.
    enable_entity_boost: bool = True
    entity_boost_weight: float = 0.2       # max bonus added to a chunk's score
    entity_boost_max_candidates: int = 25  # graph-only chunks fused into the pool


    # Server configuration. The default binds loopback only: the app has no
    # auth unless AUTH_PASSWORD is set, so reaching the network has to be a
    # deliberate act. Docker and PaaS images set HOST=0.0.0.0 explicitly
    # because there the container boundary is what limits exposure.
    host: str = "127.0.0.1"
    port: int = 8473

    # CORS: comma-separated list of allowed browser origins, or "*" for any.
    # Empty (the default) sends no CORS headers, so only the same origin can
    # read a response — the bundled frontend is served same-origin and the Vite
    # dev server proxies to the backend, so neither needs this. Set it only for
    # a separate web app on another origin, and prefer listing that origin over
    # "*": with "*" any page the user visits can read this API. Credentialed
    # requests are allowed only for explicitly listed origins, never for "*".
    cors_allow_origins: str = ""

    # HTTPS / TLS. If both files exist, uvicorn serves HTTPS on `port`.
    # Leave empty to run plain HTTP (default). Paths are relative to the
    # project root unless absolute. Use `certs/generate-cert.sh` to create
    # a dev self-signed pair.
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    # MCP configuration
    enable_mcp: bool = True
    mcp_server_id: str = "clio"
    mcp_default_collection: str = "default"
    # Single-shot search defaults for agents. Hybrid with 8 passages: an agent
    # that reaches for search_collection instead of research_documents still
    # sees enough to notice when the corpus disagrees with its first guess.
    mcp_top_k: int = 8
    mcp_mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    mcp_semantic_weight: float = 0.7
    mcp_include_sources: bool = True
    mcp_max_source_length: int = 500
    mcp_ai_provider: str = "none"   # AI provider for MCP synthesis: "none" | "anthropic" | "openai" | "ollama"
    mcp_ollama_model: str = ""      # Ollama model to use when mcp_ai_provider = "ollama"

    # Record every MCP tool call in the audit trail, not just writes. Off by
    # default: on a personal or small-team appliance it is noise that grows
    # the audit table for no accountability gain. Turn it ON for any
    # deployment that has to answer "which credential read which document,
    # when" — the AU-family control every regulated or government install is
    # measured against. Recorded per call: tool, credential, collection,
    # document ids and result counts. Never the passage text or the query's
    # results, so the trail cannot itself become a copy of the corpus.
    # AUDIT_RETENTION_DAYS applies as it does to every other audit row.
    mcp_audit_tool_calls: bool = False

    # Database backend for app metadata (collections, shares, jobs, usage).
    # "postgresql" moves ONLY app.db — per-collection vector/BM25/metadata
    # stores, structured tables, the answer cache, and faiss.index all stay
    # as local files, so this is not HA and does not enable multi-replica.
    # See docs/DEPLOYMENT.md "Capacity & scaling".
    db_backend: Literal["sqlite", "postgresql"] = "sqlite"
    postgres_url: str = ""  # e.g. postgresql://user:pass@localhost:5432/clio

    # Multi-user mode is NOT supported and the app refuses to start with it on.
    # The flag only ever filtered the collection list — search, document
    # retrieval, chat and the /mcp tools take a collection_id and never checked
    # ownership — so it looked like an isolation boundary without being one.
    # The setting stays here (rather than being deleted) so existing .env files
    # fail loudly instead of silently changing behaviour. The supported
    # replacement is PRIVATE_COLLECTIONS below.
    enable_multi_user: bool = False
    default_user_id: str = "default"  # Owner recorded on every collection

    # Private collections: per-person ownership and sharing, enforced at every
    # entry point that takes a collection_id (search, documents, chat, /mcp).
    # Requires a verified identity source — IDENTITY_PROVIDER below, which is
    # Cloudflare Access, your own OIDC provider, or an authenticating reverse
    # proxy. The app refuses to start with this flag on and no identity source
    # configured, because without one the boundary would be cosmetic.
    #
    # Semantics when on:
    #   - Collections owned by a person are visible only to the owner and to
    #     users who accepted a share link (read or readwrite).
    #   - Collections owned by "default" (everything created before this mode,
    #     and anything created by password-authenticated callers) are team
    #     collections: visible and writable to everyone, unchanged behaviour.
    #   - AUTH_PASSWORD callers have no identity: they see team collections
    #     only. MCP clients get an identity via Access service tokens (the
    #     token's common_name), so collections can be shared to them by name.
    private_collections: bool = False

    # Shared-secret auth. When set, every request (except /health) must present
    # this password via HTTP Basic auth (any username) or
    # `Authorization: Bearer <password>`. Browsers prompt natively, so no login
    # UI is needed. Use AUTH_REQUIRE_IDENTITY instead when a verified proxy
    # identity is the intended gate.
    auth_password: str = ""
    # Require a verified identity even when AUTH_PASSWORD is unset.
    auth_require_identity: bool = False

    # ── Verified identity (services/identity.py) ─────────────────────────
    # Which source establishes *who* a request is. AUTH_REQUIRE_IDENTITY can
    # also make this source the app's admission gate.
    #
    #   ""                 auto — cloudflare_access when CF_ACCESS_* are set,
    #                      otherwise no verified identity (team appliance).
    #   cloudflare_access  Cf-Access-Jwt-Assertion from the edge.
    #   oidc               a bearer JWT from your own IdP (Keycloak, Entra ID,
    #                      Okta, PingFederate). The on-prem / air-gapped answer.
    #   trusted_header     a header set by an authenticating reverse proxy —
    #                      mTLS terminator, SSO proxy, Kerberos front end.
    #
    # See docs/IDENTITY.md. Misconfiguration is a startup failure, never a
    # silent downgrade to "everyone is anonymous".
    identity_provider: str = ""

    # OIDC (IDENTITY_PROVIDER=oidc). OIDC_AUDIENCE is not optional: without
    # it, any token the IdP ever issued for any application would be accepted
    # here. OIDC_JWKS_URL skips discovery for an install that cannot reach the
    # issuer's .well-known document. OIDC_REQUIRED_CLAIMS narrows admission
    # further, e.g. "groups=clio-users" — comma-separated name=value
    # pairs, each matching a scalar claim or a member of a list claim.
    oidc_issuer: str = ""            # e.g. "https://sso.agency.gov/realms/main"
    oidc_audience: str = ""          # comma-separated client ids / API audiences
    oidc_jwks_url: str = ""
    oidc_identity_claim: str = "email"
    oidc_required_claims: str = ""

    # Trusted reverse-proxy header (IDENTITY_PROVIDER=trusted_header). The
    # header is forgeable by anything that can reach the port directly, so
    # the app refuses to start without a guard that holds: either HOST is
    # loopback (your proxy is the only route in) or TRUSTED_HEADER_SECRET is
    # set, so the proxy proves the identity with HMAC-SHA256 over the value,
    # hex-encoded, in TRUSTED_HEADER_SIGNATURE_NAME.
    #
    # TRUSTED_HEADER_PROXIES is defence in depth, never the sole guard:
    # uvicorn rewrites the peer address from X-Forwarded-For for connections
    # from FORWARDED_ALLOW_IPS, so the address checked is not always the real
    # socket peer. See docs/IDENTITY.md.
    trusted_header_name: str = "X-Forwarded-User"
    trusted_header_proxies: str = ""  # comma-separated CIDRs or addresses
    trusted_header_secret: str = ""
    trusted_header_signature_name: str = "X-Forwarded-User-Signature"

    # Trust Cloudflare Access authentication. When both are set, a request
    # carrying a valid Cf-Access-Jwt-Assertion (signed by the team's keys,
    # audience = one of the listed Access app AUD tags) is authenticated
    # WITHOUT the shared password — no browser Basic-auth prompt after SSO,
    # and service-token MCP clients drop the bearer header. AUTH_PASSWORD
    # still works as a fallback for direct/non-Access access paths.
    cf_access_team_domain: str = ""  # e.g. "yourteam.cloudflareaccess.com"
    cf_access_aud: str = ""  # comma-separated Access application AUD tags

    # Automated edge admission for share invitations. Sharing by email
    # delivers a share token, but the recipient is still stopped at the
    # Cloudflare Access login until their address is on the Access policy —
    # which otherwise means an admin editing the dashboard by hand for every
    # new person. With these set, inviting someone also admits them at the
    # edge, so the invitation is genuinely self-serve (pair it with the
    # one-time PIN login method and guests need no account in your IdP).
    #
    # CF_API_TOKEN needs exactly one permission: Account / Access: Apps and
    # Policies / Edit. That is write access to the deployment's front door,
    # so do not reuse a broader token. CF_ACCESS_POLICY_ID is the reusable
    # policy attached to the app; scripts/provision_cloudflare.py creates it
    # and writes all three values.
    #
    # ADMIN_EMAILS gates the privileged half: an admission grants edge access
    # to the whole deployment, not just the collection being shared (the
    # private-collections layer is what confines the person to that). Any
    # owner may still share; only an admin's invite admits a stranger. Empty
    # means nobody, so this fails closed rather than open.
    cf_api_token: str = ""
    cf_account_id: str = ""
    cf_access_policy_id: str = ""
    admin_emails: str = ""  # comma-separated

    # ── Load protection ──────────────────────────────────────────────────
    # These exist so one person can't starve everyone else on a shared
    # deployment — the shared free-tier LLM key and the single embedding
    # model are common resources. All limits are per identity (Cloudflare
    # Access email when present, else client IP), 0 disables that limit.

    # Requests per minute, by expense class. Chat turns can cost up to ~11
    # provider round-trips each; search is a local embed + FAISS scan;
    # everything else is cheap metadata traffic.
    rate_limit_enabled: bool = True
    rate_limit_chat_per_minute: int = 6
    rate_limit_search_per_minute: int = 30
    rate_limit_default_per_minute: int = 120
    # The public /api/register form has no identity to key on, so its
    # budget is per IP and deliberately small.
    rate_limit_register_per_minute: int = 5
    # /mcp is unlimited by default: an agent turn fans out several tool calls
    # at once and handles 429s badly (immediate retry or abandoning the
    # question). Set a per-token budget only for abuse protection on a shared
    # deployment; it is keyed on the personal token, not the IP.
    rate_limit_mcp_per_minute: int = 0

    # research_documents runs up to seven retrieval branches against the
    # vector index and metadata store. Parallel research calls compete for
    # CPU on large collections, so callers queue for a slot instead of
    # slowing each other down; a call that waits longer than the timeout is
    # refused with a retryable error. 0 = no gate.
    research_max_concurrent: int = 3
    research_queue_timeout_seconds: float = 60.0

    # Provider tokens (input+output) one identity may spend on chat per UTC
    # day. 0 = unlimited. Cached answers are free and still served once the
    # budget is spent. Anonymous password callers share a single budget.
    chat_daily_token_budget: int = 0

    # Indexing jobs that may run concurrently across all collections. Every
    # job funnels through one process-wide embedding lock anyway, so more
    # parallel jobs mostly shuffle the queue while starving live search.
    max_concurrent_index_jobs: int = 2

    # Storage a single collection may hold, in bytes, measured as the sum of
    # its indexed source files (uploads, staged copies, in-place references
    # and agent-written sources alike). Every ingest path checks it before
    # accepting a file, so one collection cannot fill the disk or the
    # embedding queue for everyone else. 0 = unlimited. Default 5 GiB.
    collection_storage_limit_bytes: int = 5 * 1024 * 1024 * 1024

    # ── Online registration ──────────────────────────────────────────────
    # Lets a person ask for access from a public page (/register) instead of
    # waiting for an admin to type their address in. Requires edge admission
    # (CF_API_TOKEN / CF_ACCOUNT_ID / CF_ACCESS_POLICY_ID) because approving
    # a request *is* an admission at the Cloudflare Access edge, and the
    # /register page plus /api/register must be exempted from Access
    # (scripts/provision_cloudflare.py adds the bypass; see
    # docs/DEPLOYMENT.md).
    #   off      — the page says registration is closed.
    #   approval — requests queue for an admin (Admin → Access → Requests).
    #   open     — matching requests are admitted immediately.
    registration_mode: Literal["off", "approval", "open"] = "off"
    # Comma-separated email domains allowed to register (e.g.
    # "example.com, partner.org"). Empty = any domain. Enforced in both
    # modes; a domain miss is refused before it ever reaches the queue.
    registration_allowed_domains: str = ""
    # Refuse new registrations once this many addresses are admitted at the
    # edge. Defaults to Cloudflare Zero Trust's free tier so a public form
    # cannot quietly turn into a bill. 0 = no cap beyond Cloudflare's own.
    registration_max_seats: int = 50

    # Retention (days). search_history stores result snippets, so it is a
    # privacy liability with no expiry; chat_usage is small but unbounded.
    search_history_retention_days: int = 30
    usage_retention_days: int = 180

    # ── Content governance ───────────────────────────────────────────────
    # What happens when the ingest-time content-policy scanner flags a
    # document. "flag" indexes it and shows a badge (the same posture as
    # the prompt-injection scan); "quarantine" indexes it but hides it from
    # search, chat and MCP until an admin approves; "reject" refuses to
    # index it at all; "off" skips the scan. A finding in a critical
    # category (child sexual abuse material indicators, attack planning or
    # incitement) is always at least quarantined under "flag" — the knob is
    # a floor, not a ceiling. Rule packs are local regex; nothing leaves the
    # machine unless CONTENT_POLICY_LLM_REVIEW is on.
    content_policy_action: Literal["off", "flag", "quarantine", "reject"] = "flag"

    # Second opinion from an LLM: a sample of pages is classified against
    # the same categories through the provider named here (or, when empty,
    # the deployment's default chat provider). Sends content to a provider
    # the deployment already trusts for chat, but it costs tokens per
    # upload, so it is off by default. Its verdict can escalate a document
    # to flagged/quarantined; it never clears a rule-pack finding.
    content_policy_llm_review: bool = False
    content_policy_llm_provider: str = ""
    content_policy_llm_model: str = ""
    content_policy_llm_sample_pages: int = 6

    # Acceptable-use acknowledgement. When on, every identity must accept
    # the policy (once per version) before adding sources; bump the version
    # to re-prompt everyone. Acceptance is recorded per identity, so this
    # only takes effect under PRIVATE_COLLECTIONS, where every request has
    # one. Empty text uses the built-in default policy (markdown).
    aup_required: bool = False
    aup_version: str = "1"
    aup_text: str = ""

    # Audit events (uploads, deletions, shares, tokens, policy decisions,
    # admin actions) are the accountability trail and are kept far longer
    # than the search log. 0 = keep forever.
    audit_retention_days: int = 365

    # Extra Host header values the embedded /mcp endpoint accepts, comma-
    # separated (e.g. "clio.example.com"). The MCP SDK ships DNS-rebinding
    # protection that only trusts localhost Hosts by default; when the app is
    # served through a tunnel or reverse proxy under a public hostname, list
    # that hostname here. Localhost stays allowed either way.
    mcp_allowed_hosts: str = ""

    # ── OAuth 2.1 on /mcp (docs/IDENTITY.md "OAuth for MCP clients") ─────
    # Connector-style MCP clients (Claude Desktop, claude.ai, ChatGPT, and
    # Claude Code without a pasted token) discover an authorization server
    # from the /mcp 401 challenge and RFC 9728 metadata, sign the person in
    # there, and present the resulting bearer JWT. The app is only ever the
    # resource server: the site's own IdP is the authorization server, and
    # the JWT is verified by the same OIDC backend a browser session uses,
    # so private-collection scoping and the audit actor follow for free.
    #
    # With IDENTITY_PROVIDER=oidc nothing else is required - OIDC_ISSUER /
    # OIDC_AUDIENCE are advertised and enforced on /mcp as they are
    # everywhere. The MCP_OAUTH_* values below override them for /mcp only,
    # which is how a Cloudflare Access deployment (whose browser identity
    # is not a bearer JWT) names an IdP for its MCP clients.
    #
    # MCP_PUBLIC_URL is the canonical URL clients reach the endpoint at,
    # e.g. https://clio.agency.gov/mcp. It is the RFC 9728 `resource`,
    # and a token whose audience is exactly that URL is accepted on /mcp
    # (RFC 8707 resource indicators). Empty = derived from MCP_ALLOWED_HOSTS,
    # else from the request; set it explicitly for anything but localhost.
    mcp_public_url: str = ""
    mcp_oauth_issuer: str = ""        # empty = OIDC_ISSUER when IDENTITY_PROVIDER=oidc
    mcp_oauth_audience: str = ""      # comma-separated; required with MCP_OAUTH_ISSUER
    mcp_oauth_jwks_url: str = ""
    # A scope the token must carry (space-separated `scope` claim, or `scp`)
    # to be accepted on /mcp, advertised as scopes_supported and in the 401
    # challenge. Empty = any token valid for the app is valid for /mcp.
    mcp_oauth_scope: str = ""

    # OCR configuration — deliberately minimal: an on/off switch and an engine.
    # Scanned pages either go through a vision-capable LLM (best quality) or the
    # free local engine (Docling/Tesseract) when no provider is set. Rendering
    # and image-prep details are fixed at sensible defaults in the extractor.
    enable_ocr: bool = False  # OCR scanned PDFs during indexing
    ocr_max_pages: int = 25  # Cost guard: skip OCR for PDFs with more pages (0 = no limit; env-only)
    ocr_max_file_mb: int = 50  # Cost guard: skip OCR for files larger than this in MB (0 = no limit; env-only)

    # "auto" = zero-config: auto-pick a vision model from the local Ollama
    # install (no API key, no model selection needed). "none" = local OCR.
    vision_ocr_provider: str = "none"  # "auto" | "anthropic" | "openai" | "ollama" | "none"
    vision_ocr_model: str = ""  # Leave empty with "auto"/"ollama" to auto-detect the Ollama model
    vision_ocr_api_key: str = ""  # Stored locally so background indexing can use it

    # Local Ollama context window (num_ctx). Ollama defaults to a small context
    # (~2048 tokens) and SILENTLY truncates anything longer — which drops most of
    # the retrieved documents in a RAG prompt. We set num_ctx explicitly on every
    # Ollama call so local models actually see the context. Raise this for big
    # documents (bounded by the model's trained max and your VRAM).
    ollama_num_ctx: int = 8192

    # LLM-assisted column role inference (P0.5)
    # When enabled, columns that can't be mapped by vendor profiles or regex
    # heuristics are sent (with column names and sample values) to an LLM
    # for role assignment.  Requires mcp_ai_provider != "none" and a valid key.
    enable_llm_schema_inference: bool = False
    llm_schema_inference_threshold: float = 0.5  # Trigger when ≥ this fraction of columns are unmapped

    # UI feature flags
    enable_chat_tab: bool = True  # Show/hide the Chat tab in the frontend

    # v3.0: CSV indexing configuration
    csv_row_level_indexing: bool = True  # Index CSV rows individually

    # Bulk ingest tuning (upload/index jobs over many small files). Chunks are
    # accumulated across files until bulk_flush_chunks, then embedded together
    # (so EMBED_BATCH_SIZE batches actually fill) and persisted in one
    # SQLite/BM25/FAISS pass. Extraction runs in a small worker pool so it
    # overlaps embedding; embedding itself stays serialized on the encode lock.
    bulk_flush_chunks: int = 256
    bulk_extract_workers: int = 4

    # Link indexing: "Add link" fetches the page or file behind a URL into
    # the collection's documents directory and indexes the saved copy like
    # an upload. The server makes the request, so by default it refuses
    # hosts that resolve to private, loopback, or link-local addresses
    # (a shared appliance must not become a proxy into its own network);
    # LINK_ALLOW_PRIVATE_NETWORKS=1 relaxes that for intranet wikis.
    # OFFLINE_MODE disables the feature outright.
    link_indexing_enabled: bool = True
    link_allow_private_networks: bool = False
    link_max_bytes: int = 25 * 1024 * 1024
    link_fetch_timeout_seconds: float = 30.0

    # Audio transcription (meeting recordings via local Whisper)
    # Model size: tiny | base | small | medium | large-v3
    #   tiny   ~39MB   fastest, lowest accuracy
    #   base   ~142MB  good default for CPU
    #   small  ~466MB  better accuracy, ~2x slower than base
    #   medium ~1.5GB  much better, GPU recommended
    whisper_model: str = "base"
    whisper_device: str = "cpu"          # "cpu" or "cuda"
    whisper_compute_type: str = "int8"   # "int8" (fast, CPU) | "float16" (GPU) | "float32"
    whisper_language: str = ""           # "" = auto-detect, else ISO code like "en"

    # v3.0: Schema version (for data persistence)
    schema_version: str = "3.0"

    # The env file is overridable so the test suite can run against the
    # shipped defaults instead of whatever .env sits next to the checkout
    # (a developer's real deployment posture). Not a documented knob.
    model_config = SettingsConfigDict(
        env_file=os.environ.get("CLIO_ENV_FILE") or os.environ.get("ASYMPTOTE_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Fail at startup, not on the first upload, when EMBEDDING_PROVIDER
        # names something the catalog doesn't know.
        from services.embedding_providers import PROVIDER_IDS
        if self.embedding_provider not in PROVIDER_IDS:
            raise ValueError(
                f"EMBEDDING_PROVIDER={self.embedding_provider!r} is not one of "
                f"{', '.join(PROVIDER_IDS)}"
            )
        # Ensure data directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "documents").mkdir(exist_ok=True)
        (self.data_dir / "indexes").mkdir(exist_ok=True)
        (self.data_dir / "backups").mkdir(exist_ok=True)  # v3.0: Backup directory

        # Air-gap enforcement must happen before huggingface_hub/transformers
        # are imported anywhere (they read these at import time). config is the
        # first app module imported by main.py, so this is early enough.
        if self.offline_mode:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# Global settings instance
settings = Settings()
