# OAuth on `/mcp`: smoke check against a real IdP and real clients

What [docs/IDENTITY.md](../../docs/IDENTITY.md#oauth-for-mcp-clients)
describes, run for real on one machine. Use it after touching
`services/identity.py`, `services/mcp_oauth.py` or the auth middleware,
and as the reproducible part of a security review.

## 1. A throwaway Keycloak

```bash
docker run -d --name asy-keycloak -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.3 start-dev
python scripts/oauth_smoke/keycloak_realm.py     # realm, user, scopes, DCR, one pre-registered client
```

## 2. The app, with that realm as its IdP

Its own data directory, never a real one.

```bash
IDENTITY_PROVIDER=oidc OIDC_ISSUER=http://localhost:8080/realms/clio OIDC_AUDIENCE=clio \
MCP_PUBLIC_URL=http://localhost:8473/mcp MCP_ALLOWED_HOSTS=localhost PRIVATE_COLLECTIONS=true \
MCP_AUDIT_TOOL_CALLS=true AUTH_PASSWORD= DATA_DIR=/tmp/asy-oauth \
python main.py
```

Expect at startup: `OAuth on /mcp: authorization server http://localhost:8080/realms/clio, resource http://localhost:8473/mcp`.

```bash
curl -si -X POST localhost:8473/mcp/ | grep -i www-authenticate
#  Bearer resource_metadata="http://localhost:8473/.well-known/oauth-protected-resource/mcp"
curl -s localhost:8473/.well-known/oauth-protected-resource/mcp
#  {"resource":"http://localhost:8473/mcp","authorization_servers":["http://localhost:8080/realms/clio"],...}
```

## 3. Clients

**Claude Code, dynamic registration** (what Claude Desktop and claude.ai do):

```bash
claude mcp add --transport http -s user asy-oauth http://localhost:8473/mcp
claude mcp login asy-oauth            # browser at Keycloak: analyst / analyst
claude -p "Call asy-oauth's list_collections tool and reply with its raw JSON" --allowedTools mcp__asy-oauth__list_collections
```

**Claude Code, pre-registered client id** (an IdP without dynamic registration):

```bash
claude mcp add --transport http --client-id claude-code --callback-port 8123 -s user asy-prereg http://localhost:8473/mcp
claude mcp login asy-prereg
```

**The MCP SDK's reference client**:

```bash
python scripts/oauth_smoke/sdk_client.py
```

Then check the trail: Admin → Audit, or the `audit_events` table in the
app database, shows `mcp.tool_call` rows with `actor = analyst@agency.test`.

**Required scope, step-up.** Restart the app with `MCP_OAUTH_SCOPE=clio-mcp`.
The stored token lacks the scope, so the next call answers
`403 ... error="insufficient_scope", scope="clio-mcp"`; `claude mcp login asy-oauth`
again requests the scope (it is in the challenge) and calls succeed.

## 4. Clean up

```bash
claude mcp remove -s user asy-oauth; claude mcp remove -s user asy-prereg
docker rm -f asy-keycloak
```

## Keycloak note

Keycloak parses `name:value` scopes as *dynamic scopes*, so a scope called
`clio:mcp` requested together with a scope called `clio` is
rejected as `invalid_scope`. Name the MCP scope without a colon
(`clio-mcp`) when the realm also has a scope named `clio`.

## What this does not cover

claude.ai, Claude Desktop and ChatGPT connect from Anthropic's and OpenAI's
servers, so they need the endpoint and the IdP on public HTTPS. They speak
the same flow the two clients above exercise; the remaining variable is
client registration at your IdP, which IDENTITY.md covers per vendor.
