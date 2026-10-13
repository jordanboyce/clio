# Identity: who a request is

Clio has two separate questions, and they are often confused:

- **Can this caller get in at all?** Either the network (loopback, a VPN, a
  firewall), `AUTH_PASSWORD` (a shared-secret gate), or an identity: with
  `IDENTITY_PROVIDER=local` every request needs a signed-in account, and
  `AUTH_REQUIRE_IDENTITY=true` makes the other providers require a verified
  assertion too. See [DEPLOYMENT.md](DEPLOYMENT.md).
- **Who is this caller?** `IDENTITY_PROVIDER`. This page.

The second one only matters when something acts on it. Two things do:
`PRIVATE_COLLECTIONS` (per-person ownership and sharing) and the audit trail
(`actor` on every recorded event). Without a verified identity the app is an
open appliance: everyone who can reach it sees the whole corpus, which is the
right model for a machine only you use and the wrong one for a group with
different access needs.

The app **refuses to start** on an identity configuration it cannot enforce.
That is deliberate: a boundary that is advertised but not enforced is worse
than no boundary, because people put things behind it.

## Pick a provider

| `IDENTITY_PROVIDER` | Use when | Identity comes from |
| --- | --- | --- |
| *(empty)* | The default. | Auto: `local` when `PRIVATE_COLLECTIONS=true`; otherwise nobody (an open appliance, optionally behind `AUTH_PASSWORD`) |
| `local` | **Most self-hosted installs.** A laptop, home server or team box with no identity provider of its own | Built-in accounts: email + password, a session cookie |
| `oidc` | **On-prem / air-gapped.** You run Keycloak, Entra ID, Okta, PingFederate, ADFS | `Authorization: Bearer <JWT>` |
| `trusted_header` | An authenticating proxy already fronts the app (mTLS, SPNEGO/Kerberos, a site SSO proxy) | A header that proxy sets |

If you do not already run an identity provider, use `local`: set
`PRIVATE_COLLECTIONS=true` and the rest happens in the browser.

---

## `local` - built-in accounts

Clio can keep its own accounts, so a self-hosted install needs no identity
provider, no cloud service and no outbound connection to sign people in.

```bash
PRIVATE_COLLECTIONS=true          # implies IDENTITY_PROVIDER=local
# IDENTITY_PROVIDER=local         # explicit form; also works without
#                                 # PRIVATE_COLLECTIONS, as a login in front
#                                 # of one shared corpus
# SETUP_CODE=                     # optional, see First run
ADMIN_EMAILS=                     # optional break-glass administrators
```

### First run

A fresh install has no accounts, so something has to prevent whoever reaches
it first from claiming it. That something is a one-time **setup code**,
printed to the server log:

```bash
docker logs clio          # or read the terminal running `python main.py`
```

The first visit to the app shows a setup screen. Enter the code, an email
address and a password (at least 10 characters) and that account becomes the
first administrator, signed in. Setup then closes: once any account exists
the code is dead and the screen never comes back.

For an unattended install, choose the code yourself with `SETUP_CODE=` in
`.env` instead of reading it from the log. Treat it like a password until the
first account exists, and remove it afterwards.

### Adding people

There is no public sign-up and no email requirement. An administrator opens
**Admin -> People**, enters an address and gets an **invite link**. Send it
to the person yourself (chat, email, in person); opening it lets them choose
a password and signs them in. Links are single use, expire after 7 days, and
are stored hashed, so the link shown once is the only copy.

A person who forgot their password does not need a mail server either: an
administrator creates a **reset link** for them in the same place (valid 24
hours, single use). Invite and reset links are not emailed automatically
yet; you hand them over yourself.

### Roles

- **Administrator** - the `is_admin` flag on an account, granted or removed in
  Admin -> People. Administrators manage people, deployment-wide settings,
  the audit trail and content review.
- **Everyone else** - signs in, owns the collections they create, and sees
  what others share with them or publish.
- **`ADMIN_EMAILS`** - an always-on, environment-level administrator list.
  Use it as a break-glass route (it works even if the database flag is lost)
  and, for `oidc` and `trusted_header` deployments, as the only way to name
  administrators. The last-admin guard stops anyone from demoting, disabling
  or deleting the final administrator through the UI.

### Sharing with an account holder

An owner shares a collection by link or by email address, exactly as in
[DEPLOYMENT.md](DEPLOYMENT.md#private-collections). The recipient needs an
account first, so an administrator invites them; the share response reports
`has_account` so the owner can tell when an invitation is still outstanding.

### Sessions and agents

Signing in sets an `HttpOnly`, `SameSite=Lax` cookie named `clio_session`
(marked `Secure` automatically when the request arrived over https or with
`X-Forwarded-Proto: https`). The session lives on the server; the browser
holds only an opaque token. Agent clients do not use the cookie: mint a
**personal MCP token** in Settings -> MCP (prefix `asy_mcp_`). It carries the
identity of the person who minted it, is the simplest way to attach Claude
Code, Codex and similar clients, and works against `/mcp` only. OAuth sign-in
for connector-style clients still needs an OIDC provider; see
[OAuth for MCP clients](#oauth-for-mcp-clients).

### What is protected

- **Passwords** are hashed with scrypt, with a per-account salt, minimum
  length 10. Nothing reversible is stored.
- **Sessions** are random server-side tokens, hashed at rest. They expire
  after 14 days idle (sliding) and never outlive 60 days from sign-in.
- **Invite and reset links** are single use, hashed at rest, and expire (7
  days / 24 hours).
- **Guessing** is throttled: 5 failed sign-ins per address and email per 15
  minutes, and 40 per address. Behind a reverse proxy this needs real client
  addresses, so trust the proxy's forwarding headers only from the proxy
  itself (`FORWARDED_ALLOW_IPS`; see [DEPLOYMENT.md](DEPLOYMENT.md)).
- **Cross-site requests**: writes that arrive with the session cookie must
  carry a same-origin `Origin` / `Sec-Fetch-Site`; others are refused.
- **Credential changes end sessions**: changing your password signs out your
  other devices; disabling or deleting an account ends all its sessions.
- **Last-admin guard**: the final active administrator cannot be removed.
- **Audit**: setup, sign-ins (and failed ones), password changes,
  invitations, resets, role changes and deletions are recorded
  (Admin -> Audit).

### Where it lives, and backing it up

Accounts are stored in `accounts.db` in the data directory, a SQLite file
separate from the app database even when `DB_BACKEND=postgres`. It is part of
the data directory backup; restoring the directory restores every account.
Without it the install is back at first run (a new setup code is printed),
and the collections on disk have no sign-ins until the accounts are
recreated with the same email addresses.

### Coming from another identity provider

Built-in accounts are keyed by **email address**, the same key earlier
deployments used for collection ownership. If your existing collections are
owned by `jordan@example.com`, creating the first administrator (or inviting
a person) with that exact address gives them those collections back. Nothing
is migrated or rewritten. Collections owned by `default` stay the shared
team tier.

### What it is not

Built-in accounts are deliberately small. There is no single sign-on, no
multi-factor authentication, no self-service password reset by email and no
sign-up form. If you need those, put your identity provider in front with
`oidc` or `trusted_header`; see the next sections. They are listed in the
[roadmap](ROADMAP.md).

---

## `oidc` — your own identity provider

The on-prem answer. Tokens your IdP already issues become identities here.

```bash
IDENTITY_PROVIDER=oidc
OIDC_ISSUER=https://sso.agency.gov/realms/main
OIDC_AUDIENCE=clio               # the client id / API audience
OIDC_IDENTITY_CLAIM=email             # default; preferred_username and sub are fallbacks
PRIVATE_COLLECTIONS=true
```

**`OIDC_AUDIENCE` is not optional.** Without it, any token your IdP ever
issued — for any application in the realm — would be accepted here. The app
refuses to start without it.

What is verified on every request: the signature against the issuer's JWKS,
the issuer, the audience, and the expiry. Asymmetric algorithms only
(`RS*`/`ES*`/`PS*`) — an `HS256` token is refused even when it names a known
key id, because otherwise the published JWKS modulus becomes a signing key
anyone can use.

### Narrowing admission by claim

```bash
OIDC_REQUIRED_CLAIMS=groups=clio-users
```

Comma-separated `name=value` pairs, all required. Each matches a scalar
claim or membership in a list claim, so one IdP group can gate the whole
deployment. Have your IdP release that claim to this client.

### Air-gapped: skip discovery

By default the issuer's `/.well-known/openid-configuration` is fetched to
find the JWKS URL. If that is unreachable, name the key set directly:

```bash
OIDC_JWKS_URL=https://sso.internal/realms/main/protocol/openid-connect/certs
```

Keys are cached for an hour and refreshed once on an unknown key id, so IdP
key rotation needs no restart. If the JWKS cannot be fetched at all, requests
are **refused**, never admitted.

### Certificates

An internal IdP usually presents a private CA certificate. Mount the CA at
`/certs/ca` — the container installs it into both the system store and
certifi's bundle at start up. See [ONPREM.md](ONPREM.md).

### What the agent clients do

Two ways in, both ending as the same person:

- **Personal MCP tokens** (Settings → MCP) for clients that take a static
  bearer header — Codex, VS Code, AnythingLLM, Claude Code with a pasted
  token. Minted by someone already signed in; carries their identity.
- **OAuth sign-in** for connector-style clients — Claude Desktop, claude.ai,
  ChatGPT, and `claude mcp login`. The client sends the person to this IdP
  and presents the token it gets; nothing to paste. Set `MCP_PUBLIC_URL`
  and see [OAuth for MCP clients](#oauth-for-mcp-clients) below.

---

## `trusted_header` — an authenticating proxy

For sites whose front door is already a proxy that authenticates: an mTLS
terminator passing the certificate subject, a Kerberos/SPNEGO front end, or
an existing SSO proxy.

```bash
IDENTITY_PROVIDER=trusted_header
TRUSTED_HEADER_NAME=X-Forwarded-User
HOST=127.0.0.1                 # the guard: your proxy is the only route in
PRIVATE_COLLECTIONS=true
```

The header is forgeable by anything that can open a socket to the app, so
one of two guards is **mandatory** — the app will not start otherwise:

1. **`HOST` is loopback.** Only a process on the host can connect, so the
   proxy is the only route in. Simplest, and correct for the usual
   nginx/Apache-in-front shape.
2. **`TRUSTED_HEADER_SECRET`.** The proxy proves the identity with
   HMAC-SHA256 over the header value, hex-encoded, in
   `TRUSTED_HEADER_SIGNATURE_NAME`. Holds regardless of network path — use
   it when the proxy is on another host.

```bash
TRUSTED_HEADER_SECRET=<32+ random bytes>
TRUSTED_HEADER_SIGNATURE_NAME=X-Forwarded-User-Signature
```

The signature covers the identity value exactly:

```
signature = hex( HMAC-SHA256( TRUSTED_HEADER_SECRET, "analyst@agency.gov" ) )
```

Signing the value — not just sending a shared secret — is what stops a proxy
bug, or anything that learns the secret from a log, from claiming to be
someone else.

### Why `TRUSTED_HEADER_PROXIES` is not a guard by itself

```bash
TRUSTED_HEADER_PROXIES=10.8.0.0/24    # defence in depth, never the only guard
```

uvicorn's proxy-header handling rewrites the peer address from
`X-Forwarded-For` for connections from `FORWARDED_ALLOW_IPS` (127.0.0.1 by
default). So the address this check sees is not always the real socket peer,
and a check the caller can influence is not a boundary. The app enforces
this: a configuration whose only guard is a CIDR list refuses to start.

### mTLS

Terminate TLS at the proxy, then pass the subject:

```nginx
# nginx
proxy_set_header X-Forwarded-User $ssl_client_s_dn_cn;
proxy_set_header X-Forwarded-User-Signature $hmac;   # njs/Lua, if not loopback
```

Make sure the proxy **strips any inbound `X-Forwarded-User`** before setting
its own. `proxy_set_header` replaces rather than appends, which covers the
usual case.

---

## OAuth for MCP clients

Connector-style MCP clients — Claude Desktop, claude.ai, ChatGPT, and
Claude Code when you run `claude mcp login` instead of pasting a token —
follow the MCP authorization spec: they treat `/mcp` as an OAuth 2.1
*resource server*, discover its *authorization server* from RFC 9728
metadata, send the person through a browser sign-in there, and present the
bearer token they get back.

Clio implements exactly the resource-server half. **The authorization
server is your IdP.** There is deliberately no embedded one: an agency
already runs an IdP, an embedded authorization server would be a second
copy of every identity, and it is precisely the component a security review
rejects.

### With `oidc`: one more setting

`IDENTITY_PROVIDER=oidc` already names the issuer and audience. Add the URL
clients reach the endpoint at:

```bash
MCP_PUBLIC_URL=https://clio.agency.gov/mcp
```

That is the RFC 9728 `resource`. Clients always ask the IdP for a token
*for that resource* (RFC 8707), and an IdP that honours the request mints a
token whose audience is that URL — which is accepted, alongside
`OIDC_AUDIENCE`, for IdPs that ignore the resource parameter (Entra ID
among them). Left empty, the URL is derived from `MCP_ALLOWED_HOSTS`, and
failing that from each request's `Host` header; a derived value is only
ever advertised, never accepted as an audience.

What happens when a client connects:

1. `POST /mcp` with no token → `401` with
   `WWW-Authenticate: Bearer resource_metadata="https://…/.well-known/oauth-protected-resource/mcp"`
   (plus `scope="…"` if one is required).
2. The client reads that document: `authorization_servers: ["<OIDC_ISSUER>"]`.
   Both `/.well-known/oauth-protected-resource` and the `/mcp`-suffixed form
   are served, unauthenticated, and answer 404 when no authorization server
   applies.
3. The client fetches the IdP's own metadata
   (`/.well-known/oauth-authorization-server` or
   `/.well-known/openid-configuration`), obtains a client id (next section),
   and runs the PKCE authorization-code flow in the browser.
4. It presents the token on every request. The token is verified by the
   **same code path as a browser session** — signature against the JWKS,
   issuer, audience, `exp`, `OIDC_REQUIRED_CLAIMS`, asymmetric algorithms
   only — so the caller *is* that person: private collections scope to
   them, the audit trail names them, and `ADMIN_EMAILS` applies.

### Client registration is the IdP's job

The metadata names no registration endpoint on purpose. The current MCP
spec (2026-07-28) deprecates dynamic client registration and tells clients
to prefer, in order: a pre-registered client id, a Client ID Metadata
Document (the client identifies itself by an HTTPS URL the IdP fetches),
then dynamic registration. Every client that matters accepts a
pre-registered id, so nothing here depends on the IdP supporting more.

| IdP | Zero-touch path | Otherwise |
| --- | --- | --- |
| Keycloak | Anonymous dynamic registration is built in (`registration_endpoint` is advertised). Allow the client's host in the realm's *Client registration → Anonymous access policies → Trusted Hosts*, or hand out an initial access token. | Create a public client with the client's redirect URI; give people its client id. |
| Okta, Auth0 | Dynamic registration where enabled on the tenant; both are adding Client ID Metadata Document support. | An *Application* of type native/SPA (public, PKCE) with the client's redirect URI. |
| Entra ID | None — no dynamic registration. | An *App registration* (public client, PKCE) with the client's redirect URI; set `OIDC_AUDIENCE` to its Application ID URI or client id, since Entra ignores `resource`. |

Where the client id goes: Claude Desktop / claude.ai — *Add custom
connector → Advanced settings*; ChatGPT — the OAuth client fields of the
connector; Claude Code — `claude mcp add --transport http --client-id <id>
[--callback-port <port>] clio https://…/mcp`. Redirect URIs the clients
use (confirm in the client's docs, they change): Claude Code
`http://localhost:<port>/callback` (random port unless `--callback-port`),
claude.ai / Claude Desktop `https://claude.ai/api/mcp/auth_callback`,
ChatGPT `https://chatgpt.com/connector_platform_oauth_redirect`. The hosted
clients connect from Anthropic's and OpenAI's servers, so both the endpoint
and the IdP must be reachable from the internet for them.

### Requiring a scope

```bash
MCP_OAUTH_SCOPE=clio-mcp
```

Then a token must carry that scope (`scope` or `scp` claim, string or
list) to be accepted on `/mcp`, while the same token stays a fine browser
session. It is advertised as `scopes_supported` and in the 401 challenge,
so clients request it; a token that is valid for the app but lacks it gets
`403` with `WWW-Authenticate: Bearer error="insufficient_scope", scope="…"`,
which is the step-up signal the spec defines. Define the scope at the IdP
(a Keycloak client scope, an Entra *Expose an API* scope). Leave it empty
unless you want MCP access to be a separately granted permission.

One consequence worth knowing before you turn it on: a client that
registers itself dynamically asks the IdP for exactly the scope in the
challenge, and Keycloak then gives that client *only* that scope. The
token it mints carries no audience and no email unless the scope itself
provides them, and the app refuses it — correctly. So on Keycloak, put the
audience mapper and the identity-claim mapper on the MCP client scope, not
only on the realm defaults; `scripts/oauth_smoke/keycloak_realm.py` shows
the shape. Pre-registered clients are configured by hand and do not have
this problem.

### A different IdP for `/mcp` only

With `local` accounts (or `trusted_header`) the browser identity is not a
bearer JWT, so the app has no authorization server to advertise. If you do
run an OIDC provider, name it for `/mcp` only:

```bash
MCP_OAUTH_ISSUER=https://sso.agency.gov/realms/main
MCP_OAUTH_AUDIENCE=clio-mcp
MCP_OAUTH_JWKS_URL=                                    # optional, skips discovery
MCP_PUBLIC_URL=https://clio.agency.gov/mcp
```

Tokens from that issuer are accepted on `/mcp` and nowhere else, and the
browser identity keeps working beside them. Anything in front of the app
must let `/mcp` and `/.well-known/oauth-protected-resource` through
unauthenticated, so the 401 challenge and the metadata reach the client. Use
the identity claim that matches how people are known to the app (their
email), so a person is the same person on both paths.

### What is refused

Wrong audience, wrong issuer, expired, no `exp`, a signature the JWKS does
not verify, `HS256` even with a known key id, a token for another
resource, a token without the required scope (403, above). At startup:
`MCP_OAUTH_ISSUER` without `MCP_OAUTH_AUDIENCE`, a plaintext issuer,
`MCP_OAUTH_SCOPE` with no authorization server to enforce it, and an
`MCP_PUBLIC_URL` that is not an absolute http(s) URL.

### Checking it

```bash
curl -si -X POST https://clio.agency.gov/mcp | grep -i www-authenticate
curl -s https://clio.agency.gov/.well-known/oauth-protected-resource/mcp
claude mcp add --transport http clio https://clio.agency.gov/mcp
claude mcp login clio          # opens the browser at your IdP
```

Settings → MCP shows the same URL and per-client steps whenever an
authorization server applies. For a full rehearsal against a throwaway
Keycloak — dynamic registration, a pre-registered client, the SDK's
reference client, the scope step-up — see
[scripts/oauth_smoke/README.md](../scripts/oauth_smoke/README.md).

---

## Auditing what agents read

Writes have always been audited. Reads over MCP are not, unless you ask:

```bash
MCP_AUDIT_TOOL_CALLS=true
```

Then every MCP tool call appends an `mcp.tool_call` event: the tool, the
verified identity, the personal token id, the collection, the documents
touched and how many results came back. Export it from Admin → Audit.

Deliberately **not** recorded: passage text, queries, and result contents.
The audit table is read by more people and kept longer than the corpus it
describes, so it must not become a second copy of it.

Retention follows `AUDIT_RETENTION_DAYS` (0 = forever). Expect roughly one
row per tool call — an agent turn is typically several.

---

## Checklist

- [ ] `IDENTITY_PROVIDER` set (or `PRIVATE_COLLECTIONS=true` for built-in accounts)
- [ ] The app starts — a refusal here names exactly what is missing
- [ ] `OIDC_AUDIENCE` is this app's client id, not a shared or wildcard value
- [ ] `PRIVATE_COLLECTIONS=true` if people should not see each other's sources
- [ ] For `trusted_header`: loopback bind or HMAC secret, and the proxy
      strips inbound copies of the identity header
- [ ] `MCP_AUDIT_TOOL_CALLS=true` where reads must be accountable
- [ ] `ADMIN_EMAILS` lists identities as this provider reports them (the
      email claim, the certificate CN — whatever `OIDC_IDENTITY_CLAIM` or the
      proxy header actually contains). For `local`, grant administrators in
      Admin -> People instead and keep `ADMIN_EMAILS` as break-glass
- [ ] For `local`: the setup code is used (or `SETUP_CODE` removed), and the
      data directory including `accounts.db` is backed up
