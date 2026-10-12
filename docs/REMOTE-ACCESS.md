# Remote Access: Clio behind Cloudflare Tunnel + Access

This is the concrete recipe for deployment shape **B** from
[DEPLOYMENT.md](DEPLOYMENT.md): host Clio on any Docker machine (a home
box, a VPS, a lab server) and reach it securely from anywhere at
`https://clio.<your-domain>` — browser access via email PIN, headless MCP
clients via service tokens. The host needs **no open inbound ports**; the
tunnel dials out to Cloudflare.

Three layers, so no single mistake exposes the corpus:

1. **Cloudflare Access** authenticates every request at the edge (an
   explicit email allowlist plus one-time PIN for browsers, service tokens
   for agents).
2. **The tunnel** is the only route in — the compose override publishes no
   port on the host.
3. **Clio verifies the Access JWT** and rejects requests without a verified
   identity. No shared app password or second login prompt is needed.

## One-time Cloudflare setup (~15 minutes, needs your Cloudflare login)

Prerequisite: your domain is on Cloudflare (free plan is fine; Zero Trust
free tier covers up to 50 users).

> **Scripted alternative:** [scripts/provision_cloudflare.py](../scripts/provision_cloudflare.py)
> performs steps 1–3 (plus the DNS record and secrets bookkeeping) in one
> idempotent run, given an API token with *Cloudflare Tunnel: Edit*,
> *Access: Apps and Policies: Edit*, *Access: Service Tokens: Edit*, and
> *Zone DNS: Edit* permissions. The dashboard steps below are the manual
> equivalent.

1. **Create the tunnel.** Zero Trust dashboard → *Networks → Tunnels →
   Create a tunnel* (Cloudflared connector). Name it `clio`. Copy the
   token from the install command — that's `TUNNEL_TOKEN`.
2. **Route the hostname.** In the tunnel's *Public Hostname* tab, add
   `clio.<your-domain>` → service `http://clio:8473`
   (the compose service name; cloudflared runs on the same Docker network).
3. **Gate it with Access.** Zero Trust → *Access → Applications → Add an
   application* (Self-hosted), domain `clio.<your-domain>`.
   - **Browser policy** (Allow): include only the email addresses you want
     to admit, including `jordan.boyce@cyberlion.dev`. Jordan is the sole
     Clio administrator; do not add other addresses to `ADMIN_EMAILS`.
     Enable *One-time PIN* under *Integrations → Identity providers*;
     Cloudflare emails each admitted person a sign-in code. They do not need
     an account with your identity provider.
   - **MCP/agent policy** (Service Auth): create a service token under
     *Access → Service Auth → Service Tokens*, then add a policy with action
     **Service Auth** that includes that token.
   - **Personal-token policy** (Bypass), on the `/mcp` application only and
     placed *below* the Service Auth policy: action **Bypass**, include
     *Everyone*. Access cannot verify a token the app minted, so without this
     every personal-token client is refused at the edge with a 403 before
     Clio sees the request. Cloudflare evaluates Service Auth and Bypass
     top-down, so service-token clients still get their signed identity, and
     everyone else falls through to the app, which answers 401 unless the
     request carries a valid personal token. The browser
     application keeps only the intended email Allow policies and stays
     Access-gated. Trade-off:
     `/mcp` is reachable from the internet (the app's rate limits apply) and
     Access does not log bypassed requests. `scripts/provision_cloudflare.py`
     adds this policy for you.

## Deploy (on the Docker host)

```bash
# .env next to docker-compose.yml:
#   TUNNEL_TOKEN=<from step 1>
#   MCP_ALLOWED_HOSTS=clio.<your-domain>
docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build
```

Before running `scripts/provision_cloudflare.py`, set
`CLIO_ALLOW_EMAILS=jordan.boyce@cyberlion.dev,student@example.edu` in the
shell to seed the Access allowlist with Jordan and the people you are inviting.
The default is Jordan only. Re-running the script adds newly configured
addresses; remove addresses from the `clio-invited` policy under Zero Trust →
Access → Policies. The browser app's separate bootstrap `owner` policy is
restricted to Jordan.

Keep `REGISTRATION_MODE=off`; this hosted setup does not expose public
registration. Give people who need access the contact page URL,
`https://clio.<your-domain>/request-access`. It is a public contact-only page
that opens an email to Jordan. The provisioning script creates a narrowly
scoped Access Bypass app for this route only. If an older setup already has a separate
**Clio public (registration)** Access application with a Bypass policy, remove
that application in the Cloudflare dashboard. Check that the root Clio
application has no other broad Allow or Bypass policies; those can grant
access outside the email allowlist.

The remote compose override sets `ADMIN_EMAILS=jordan.boyce@cyberlion.dev`,
so only Jordan can use Clio's admin console or manage the Access allowlist,
even when students share the default team collections. To manage the list
from Clio's Settings → Access panel, add a separate, least-privilege
Cloudflare API token to `.env` as `CF_API_TOKEN`. The provisioning script
writes `CF_ACCOUNT_ID` and `CF_ACCESS_POLICY_ID`; without the API token, the
allowlist is still manageable directly in Cloudflare. Keep the token out of
source control.

`MCP_ALLOWED_HOSTS` matters: the MCP SDK ships DNS-rebinding protection that
only trusts localhost `Host` headers, and the tunnel forwards the public
hostname. Without this setting the UI works but every `/mcp` request is
rejected with `Invalid Host header`.

## Verify the door is actually closed

```bash
# 1. Direct access must FAIL (no published port):
curl -sS --max-time 5 http://<host-ip>:8473/health && echo "REACHABLE — fix this"

# 2. The public URL without credentials must redirect to the Access login,
#    not reach the app:
curl -sS -o /dev/null -w "%{http_code}\n" https://clio.<your-domain>/api/collections
# expect 302 (Access login), never 200/401 from the app itself

# 3. With the service token, the MCP handshake must succeed
#    (the token is scoped to /mcp — it will NOT open /health or the UI):
curl -sS -X POST https://clio.<your-domain>/mcp/ \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}'
# expect a JSON-RPC result with "serverInfo": {"name": "Clio", ...}
```

## Pointing MCP clients at it

**Easiest: a personal access token.** Once the app is reachable (the setup
above), log in through the browser and open Settings → MCP → Personal access
tokens. Generate one and copy the ready-made Claude Code / Codex / Copilot
snippet it produces — the token and URL are already filled in, and it works
whether or not `PRIVATE_COLLECTIONS` is on. This skips everything below
entirely: no service token, no editing `.env` on every machine that wants a
connection. The one edge prerequisite is the Bypass policy on the `/mcp`
Access application from step 3 (the provisioning script creates it); without
it Cloudflare returns 403 to every bearer token it did not issue. Revoke a token from the same screen
when a laptop is retired.

The rest of this section covers the alternative — a Cloudflare Access
service token — for cases with no browser to log in from at all (a CI runner,
a fully headless box provisioning its own credentials).

Headless clients authenticate with the Cloudflare service token. For clients
that support custom headers (Claude Code, most MCP SDKs), configure the
`/mcp` endpoint with:

```
URL:     https://clio.<your-domain>/mcp/
Headers: CF-Access-Client-Id: <token-id>.access
         CF-Access-Client-Secret: <token-secret>
```

For example, from Claude Code:

```bash
claude mcp add --transport http clio https://clio.<your-domain>/mcp/ \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>"
```

Rotate a leaked service token in the Zero Trust dashboard (per-client
revocation is the reason to prefer tokens over a shared credential).

## Optional: private collections

With the JWT trust settings the script writes (`CF_ACCESS_TEAM_DOMAIN`,
`CF_ACCESS_AUD`), you can add `PRIVATE_COLLECTIONS=true` to `.env` and
restart: new collections become private to whoever created them (your Access
SSO email), shareable via read/readwrite links, while everything that already
existed stays in a team tier everyone sees. The MCP service token is an
identity too (`clio-mcp`) — share a collection to that name to grant
agents access; to accept on the token's behalf, call the accept endpoint with
its credentials:

```bash
curl -sS -X POST https://clio.<your-domain>/api/shares/<share-token>/accept \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>"
```

Note the service token is admitted only by the `/mcp` Access app; the accept
endpoint sits under the root app, so this call works only after you add a
Service Auth policy for the token to the root application as well (Zero Trust
→ Access → Applications → Clio → Policies). If you'd rather not widen
the root app, skip sharing to agents and keep what they need in the team
tier.

Full semantics: [DEPLOYMENT.md](DEPLOYMENT.md#private-collections).

## Browser access

Open `https://clio.<your-domain>`, enter an allowlisted email address, and
use the one-time code Cloudflare sends. That is the only browser login; Clio
verifies the Access identity without asking for a second password.

## In-app Access administration

The Access panel can add and remove addresses from the reusable
`clio-invited` policy. Configure:

```
CF_API_TOKEN=<token with Account / Access: Apps and Policies / Edit>
CF_ACCOUNT_ID=<written by scripts/provision_cloudflare.py>
CF_ACCESS_POLICY_ID=<written by scripts/provision_cloudflare.py>
ADMIN_EMAILS=jordan.boyce@cyberlion.dev
```

The Access panel can now update the allowlist directly. The provisioning
script also enables the **one-time PIN** login method, which means
an invited guest needs no account in your identity provider at all — Cloudflare
emails them a code, they sign in, and the `?share_token=` deep link is applied
for them.

Revocation is symmetric: revoking someone's last share withdraws their edge
admission too, so the allowlist does not grow forever.

Once configured, an **Access** section appears in Settings for Jordan only. It
lists everyone on the policy with the number of shares each holds and when
they last signed in, tracks seats used against the free 50, and lets you
admit or withdraw an address directly. It also reconciles the two ways the
list drifts out of step with reality:

- **Shared with, but cannot sign in** — they hold a share and are not
  admitted, so their invitation stops at the Cloudflare login. One click
  admits them.
- **Admitted with nothing shared** — they can sign in but hold no
  collection, usually access that was never withdrawn. Each still occupies
  a seat, so this is where reclaimable seats show up.

You cannot withdraw your own access from inside the app; that would leave
nobody able to undo it.

Three things worth knowing before you turn it on:

- **`CF_API_TOKEN` is write access to your front door.** Scope it to that one
  permission and nothing else. It is not the same token as, and should not be
  reused from, the one used for provisioning.
- **Admission is deployment-wide, not per-collection.** Being on the Access
  policy lets someone reach the app; it is `PRIVATE_COLLECTIONS=true` that
  confines them to what was actually shared. Because of that, only
  `ADMIN_EMAILS` can trigger an admission — any owner may still share, but a
  non-admin's invitation assumes the recipient can already get in. An empty
  `ADMIN_EMAILS` means nobody, so this fails closed.
- **Watch the seat count.** Cloudflare Zero Trust is free up to 50 users and
  every person who signs in consumes a seat; past that it bills per user
  (about $3/user/month for standalone Access at time of writing). Cloudflare
  also caps a policy rule at 1,000 email addresses — the app refuses at that
  cap rather than silently dropping someone.

## Alternative: Tailscale

If everything that needs Clio (laptops, phone, agent hosts) can join
your tailnet, `tailscale serve https / http://localhost:8473` on the host is
simpler: no public hostname at all, TLS handled for you, and `AUTH_PASSWORD`
optional. The trade-off is that nothing outside the tailnet — e.g. a cloud
agent — can ever reach it. The Cloudflare shape above is the default for
"from anywhere".
