"""Provision Cloudflare Tunnel + Access for Clio (docs/REMOTE-ACCESS.md).

One idempotent run creates everything deployment shape B needs:

  1. a remotely-managed tunnel named `clio`
  2. tunnel ingress:  <hostname> -> http://clio:8473
  3. a proxied CNAME  <hostname> -> <tunnel-id>.cfargotunnel.com
  4. Access app on the root hostname   (Allow: the reusable policy below)
  5. Access app on <hostname>/mcp      (Service Auth: the token below,
                                        plus Bypass so personal tokens reach the app)
  6. an Access service token for headless MCP clients
  7. a reusable Access policy `clio-invited` holding the admitted
     addresses, plus the one-time PIN login method so invited guests need
     no account in your identity provider
  8. public Access bypasses for access requests and MCP protected-resource metadata

Secrets (TUNNEL_TOKEN, CF_ACCESS_CLIENT_ID/SECRET) are written into the
repo-local .env — which is gitignored — and never printed. The JWT trust
settings (CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD — the Access apps' AUD tags)
and MCP_ALLOWED_HOSTS are written too, so Clio can enforce Access logins and
PRIVATE_COLLECTIONS=true can be enabled without further lookup.
CF_ACCOUNT_ID and CF_ACCESS_POLICY_ID are written as well, so the app can
manage admissions when CF_API_TOKEN is configured. The remote compose
deployment restricts app administration to jordan.boyce@cyberlion.dev.
Without a separate CF_API_TOKEN in .env, manage the email policy in Cloudflare.

Usage:
    CLIO_ALLOW_EMAILS=jordan.boyce@cyberlion.dev,student@example.edu \
      CLOUDFLARE_API_TOKEN=... python scripts/provision_cloudflare.py
    CLOUDFLARE_API_TOKEN=... python scripts/provision_cloudflare.py
    # or put CLOUDFLARE_API_TOKEN=... in .env first, then run with no env var

The API token needs: Account/Cloudflare Tunnel/Edit,
Account/Access: Apps and Policies/Edit, Zone/DNS/Edit (zone: the apex below).
Safe to re-run: existing objects are found and reused, not duplicated.
"""

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")  # required: your Cloudflare account ID
HOSTNAME = os.environ.get("CLIO_HOSTNAME", "clio.example.com")
ADMIN_EMAIL = "jordan.boyce@cyberlion.dev"
ALLOW_EMAILS = list(dict.fromkeys(
    email.strip().lower()
    for email in os.environ.get(
        "CLIO_ALLOW_EMAILS", os.environ.get("CLIO_ALLOW_EMAIL", ADMIN_EMAIL)
    ).split(",")
    if email.strip()
))
TUNNEL_NAME = "clio"
SERVICE_TOKEN_NAME = "clio-mcp"
MCP_BYPASS_POLICY_NAME = "mcp-app-token-gate"
MCP_METADATA_BYPASS_POLICY_NAME = "mcp-resource-metadata"
INVITE_POLICY_NAME = "clio-invited"
API = "https://api.cloudflare.com/client/v4"
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

APEX = ".".join(HOSTNAME.split(".")[-2:])


def read_env():
    if not os.path.exists(ENV_PATH):
        return {}
    vals = {}
    for line in open(ENV_PATH, encoding="utf-8"):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


def set_env(updates):
    """Update or append KEY=VALUE lines in .env without disturbing the rest."""
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line.strip())
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            seen.add(m.group(1))
    missing = [k for k in updates if k not in seen]
    if missing:
        lines += ["", "# ── Remote access (written by scripts/provision_cloudflare.py) ──"]
        lines += [f"{k}={updates[k]}" for k in missing]
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN") or read_env().get("CLOUDFLARE_API_TOKEN", "")
if not TOKEN:
    sys.exit("CLOUDFLARE_API_TOKEN not found in environment or .env — create one per the header comment.")


def cf(method, path, body=None, ok_codes=(), optional=False):
    """Call the Cloudflare API. Fatal on failure unless `optional`, in which
    case the caller gets None and the run continues — used for steps that
    need a broader token scope than the rest and are not worth blocking on."""
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
        codes = [err.get("code") for err in data.get("errors", [])]
        if not any(c in ok_codes for c in codes):
            if optional:
                print(f"  skipped: {method} {path} -> {data.get('errors')}")
                return None
            sys.exit(f"{method} {path} failed: {data.get('errors')}")
    if not data.get("success", False):
        if optional:
            print(f"  skipped: {method} {path} -> {data.get('errors')}")
            return None
        sys.exit(f"{method} {path} failed: {data.get('errors')}")
    return data["result"]


def main():
    if not ACCOUNT_ID:
        sys.exit("CF_ACCOUNT_ID is required — set it to your Cloudflare account ID "
                 "(dashboard right-sidebar, or `wrangler whoami`).")
    if not ALLOW_EMAILS:
        sys.exit("Set CLIO_ALLOW_EMAILS to at least one email address.")
    if len(ALLOW_EMAILS) > 1000:
        sys.exit("CLIO_ALLOW_EMAILS exceeds Cloudflare's 1,000-email rule limit.")
    if any(not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) for email in ALLOW_EMAILS):
        sys.exit("CLIO_ALLOW_EMAILS must contain valid, comma-separated email addresses.")
    who = cf("GET", "/user/tokens/verify")
    print(f"token ok (status: {who['status']})")

    zones = cf("GET", f"/zones?name={APEX}")
    if not zones:
        sys.exit(f"zone {APEX} not found on this account — is the domain on Cloudflare?")
    zone_id = zones[0]["id"]
    print(f"zone {APEX}: {zone_id}")

    # 1. Tunnel (remotely managed) ------------------------------------------
    tunnels = cf("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel?name={TUNNEL_NAME}&is_deleted=false")
    tunnel = tunnels[0] if tunnels else cf(
        "POST", f"/accounts/{ACCOUNT_ID}/cfd_tunnel",
        {"name": TUNNEL_NAME, "config_src": "cloudflare"},
    )
    tid = tunnel["id"]
    print(f"tunnel '{TUNNEL_NAME}': {tid}")
    tunnel_token = cf("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/token")

    # 2. Ingress: hostname -> the compose service ---------------------------
    cf("PUT", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/configurations", {
        "config": {"ingress": [
            {"hostname": HOSTNAME, "service": "http://clio:8473"},
            {"service": "http_status:404"},
        ]}
    })
    print(f"ingress: {HOSTNAME} -> http://clio:8473")

    # 3. DNS ----------------------------------------------------------------
    target = f"{tid}.cfargotunnel.com"
    recs = cf("GET", f"/zones/{zone_id}/dns_records?name={HOSTNAME}")
    body = {"type": "CNAME", "name": HOSTNAME, "content": target, "proxied": True,
            "comment": "Clio tunnel (scripts/provision_cloudflare.py)"}
    if recs:
        cf("PUT", f"/zones/{zone_id}/dns_records/{recs[0]['id']}", body)
    else:
        cf("POST", f"/zones/{zone_id}/dns_records", body)
    print(f"dns: {HOSTNAME} CNAME {target} (proxied)")

    # 4. Service token for MCP clients --------------------------------------
    env = read_env()
    existing = [t for t in cf("GET", f"/accounts/{ACCOUNT_ID}/access/service_tokens") or []
                if t["name"] == SERVICE_TOKEN_NAME]
    if existing and env.get("CF_ACCESS_CLIENT_ID"):
        st_id, client_id, client_secret = existing[0]["id"], env["CF_ACCESS_CLIENT_ID"], env["CF_ACCESS_CLIENT_SECRET"]
        print(f"service token '{SERVICE_TOKEN_NAME}': reusing (secret already in .env)")
    else:
        # The secret is only revealed at creation; if a token exists but we
        # don't hold its secret, mint a fresh one rather than orphaning MCP.
        name = SERVICE_TOKEN_NAME if not existing else f"{SERVICE_TOKEN_NAME}-{secrets.token_hex(3)}"
        st = cf("POST", f"/accounts/{ACCOUNT_ID}/access/service_tokens", {"name": name, "duration": "8760h"})
        st_id, client_id, client_secret = st["id"], st["client_id"], st["client_secret"]
        print(f"service token '{name}': created")

    # 5. Access apps --------------------------------------------------------
    apps = {a.get("domain"): a for a in cf("GET", f"/accounts/{ACCOUNT_ID}/access/apps") or []}

    def ensure_app(domain, name, policy):
        app = apps.get(domain)
        if app is None:
            app = cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps", {
                "name": name, "domain": domain, "type": "self_hosted",
                "session_duration": "24h", "http_only_cookie_attribute": True,
            })
            cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps/{app['id']}/policies", policy)
            print(f"access app '{name}' on {domain}: created")
        else:
            print(f"access app on {domain}: exists, leaving as-is")
        return app

    def ensure_bypass_app(domain, name, policy_name):
        app = ensure_app(domain, name, {
            "name": policy_name,
            "decision": "bypass",
            "precedence": 1,
            "include": [{"everyone": {}}],
        })
        app_policies = cf(
            "GET", f"/accounts/{ACCOUNT_ID}/access/apps/{app['id']}/policies"
        ) or []
        if not any(p.get("decision") == "bypass" for p in app_policies):
            cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps/{app['id']}/policies", {
                "name": policy_name,
                "decision": "bypass",
                "precedence": 1,
                "include": [{"everyone": {}}],
            })
            print(f"access app '{domain}': bypass policy '{policy_name}' added")
        return app

    # 5a. Reusable policy holding everyone admitted to the browser app.
    # Reusable rather than inline so the running app can edit exactly this one
    # object by ID when someone is invited, without touching the application
    # or any of its other policies.
    policies = {p.get("name"): p for p in cf("GET", f"/accounts/{ACCOUNT_ID}/access/policies") or []}
    invite_policy = policies.get(INVITE_POLICY_NAME)
    if invite_policy is None:
        invite_policy = cf("POST", f"/accounts/{ACCOUNT_ID}/access/policies", {
            "name": INVITE_POLICY_NAME, "decision": "allow",
            "include": [{"email": {"email": email}} for email in ALLOW_EMAILS],
        })
        print(f"reusable policy '{INVITE_POLICY_NAME}': created")
    else:
        include = invite_policy.get("include") or []
        existing_emails = {
            str(rule["email"].get("email", "")).strip().lower()
            for rule in include
            if isinstance(rule.get("email"), dict) and rule["email"].get("email")
        }
        additions = [
            {"email": {"email": email}}
            for email in ALLOW_EMAILS
            if email not in existing_emails
        ]
        email_rule_count = sum(
            1 for rule in include
            if isinstance(rule.get("email"), dict) and rule["email"].get("email")
        )
        if email_rule_count + len(additions) > 1000:
            sys.exit(f"Access policy '{INVITE_POLICY_NAME}' exceeds Cloudflare's 1,000-email rule limit.")
        if additions:
            body = {
                "name": invite_policy.get("name") or INVITE_POLICY_NAME,
                "decision": invite_policy.get("decision") or "allow",
                "include": include + additions,
            }
            for optional in ("exclude", "require", "session_duration"):
                if invite_policy.get(optional):
                    body[optional] = invite_policy[optional]
            invite_policy = cf(
                "PUT",
                f"/accounts/{ACCOUNT_ID}/access/policies/{invite_policy['id']}",
                body,
            )
            print(f"reusable policy '{INVITE_POLICY_NAME}': added {len(additions)} address(es)")
        else:
            print(f"reusable policy '{INVITE_POLICY_NAME}': already contains configured addresses")
    policy_id = invite_policy["id"]

    ui_app = ensure_app(HOSTNAME, "Clio", {
        "name": "owner", "decision": "allow", "precedence": 1,
        "include": [{"email": {"email": ADMIN_EMAIL}}],
    })

    ui_policies = cf(
        "GET", f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}/policies"
    ) or []
    owner_policy = next((p for p in ui_policies if p.get("name") == "owner"), None)
    if owner_policy is None:
        owner_policy = cf(
            "POST",
            f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}/policies",
            {
                "name": "owner",
                "decision": "allow",
                "precedence": 1,
                "include": [{"email": {"email": ADMIN_EMAIL}}],
            },
        )
        print(f"bootstrap Access policy created for {ADMIN_EMAIL}")
    else:
        owner_include = owner_policy.get("include") or []
        owner_emails = {
            str(rule["email"].get("email", "")).strip().lower()
            for rule in owner_include
            if isinstance(rule.get("email"), dict) and rule["email"].get("email")
        }
        if (
            owner_emails != {ADMIN_EMAIL}
            or owner_policy.get("decision") != "allow"
            or owner_policy.get("precedence") != 1
        ):
            body = {
                "name": owner_policy.get("name") or "owner",
                "decision": "allow",
                "precedence": 1,
                "include": [{"email": {"email": ADMIN_EMAIL}}],
            }
            cf(
                "PUT",
                f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}/policies/{owner_policy['id']}",
                body,
            )
            print(f"bootstrap Access policy restricted to {ADMIN_EMAIL}")

    # Attach the reusable policy. It cannot go through the app's policies
    # subresource — that endpoint only accepts inline policy definitions and
    # rejects a bare ID with "unrecognized policy decision". A reusable policy
    # is referenced by ID on the application itself, and the apps endpoint has
    # no PATCH, so this is a full PUT echoing the current configuration back
    # with the policy list extended. Read-only fields are dropped; `aud` in
    # particular must survive untouched or CF_ACCESS_AUD stops matching and
    # JWT verification fails.
    attached = cf("GET", f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}/policies") or []
    if any(p.get("id") == policy_id for p in attached):
        print(f"reusable policy already attached to '{HOSTNAME}'")
    else:
        full = cf("GET", f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}")
        readonly = {"id", "uid", "aud", "created_at", "updated_at", "policies"}
        body = {k: v for k, v in full.items() if k not in readonly and v is not None}
        body["policies"] = (
            [{"id": p["id"], "precedence": i + 1} for i, p in enumerate(attached)]
            + [{"id": policy_id, "precedence": len(attached) + 1}]
        )
        cf("PUT", f"/accounts/{ACCOUNT_ID}/access/apps/{ui_app['id']}", body)
        print(f"reusable policy attached to '{HOSTNAME}'")
    mcp_app = ensure_app(f"{HOSTNAME}/mcp", "Clio MCP", {
        "name": "mcp-service-tokens", "decision": "non_identity", "precedence": 1,
        "include": [{"service_token": {"token_id": st_id}}],
    })

    # 5c. Let personal access tokens (Settings -> MCP) through the edge.
    # Access has no way to check an app-minted bearer token, so a Service
    # Auth-only app rejects every personal-token client with a 403 before
    # the request reaches Clio. A Bypass policy *below* the Service
    # Auth one fixes that: Cloudflare evaluates Service Auth and Bypass
    # top-down, so a client presenting the service token still gets its
    # JWT, and everything else falls through to the app's own gate, which
    # answers 401 without a valid token or AUTH_PASSWORD (see require_auth
    # in main.py). Only /mcp is affected; the UI app stays Access-gated.
    # Applied to pre-existing apps too, since ensure_app leaves those alone.
    mcp_policies = cf("GET", f"/accounts/{ACCOUNT_ID}/access/apps/{mcp_app['id']}/policies") or []
    if not any(p.get("decision") == "bypass" for p in mcp_policies):
        cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps/{mcp_app['id']}/policies", {
            "name": MCP_BYPASS_POLICY_NAME, "decision": "bypass",
            "precedence": max([p.get("precedence", 0) for p in mcp_policies] + [0]) + 1,
            "include": [{"everyone": {}}],
        })
        print(f"access app '{HOSTNAME}/mcp': bypass policy '{MCP_BYPASS_POLICY_NAME}' added (personal tokens reach the app)")
    else:
        print(f"access app '{HOSTNAME}/mcp': bypass policy present")

    # MCP clients discover the OAuth resource metadata before presenting a
    # credential. Keep this one metadata path public without bypassing the
    # login requirement for Clio's registration page or other UI routes.
    ensure_bypass_app(
        f"{HOSTNAME}/.well-known/oauth-protected-resource",
        "Clio MCP resource metadata",
        MCP_METADATA_BYPASS_POLICY_NAME,
    )
    ensure_bypass_app(
        f"{HOSTNAME}/request-access",
        "Clio access requests",
        "clio-access-request-contact",
    )

    # 6. JWT trust: team domain + both apps' AUD tags -----------------------
    # With these set the app verifies the Cf-Access-Jwt-Assertion the edge
    # attaches: Access logins authenticate directly, MCP service tokens
    # carry their name as identity, and PRIVATE_COLLECTIONS=true becomes
    # possible (it refuses to start without them).
    # 5b. One-time PIN login: Cloudflare emails a code to any address the
    # policy admits, so an invited guest needs no account in your IdP.
    # Needs a broader token scope than everything above (Access:
    # Organizations, Identity Providers, and Groups / Edit). Optional so a
    # narrower token still provisions everything else; without OTP, invited
    # guests need an account in a configured IdP, because Cloudflare no
    # longer adds one-time PIN to new organizations automatically.
    idps = cf("GET", f"/accounts/{ACCOUNT_ID}/access/identity_providers", optional=True)
    if idps is None:
        print("one-time PIN login: could not check (token lacks Identity Providers scope)")
        print("  enable it at Zero Trust -> Integrations -> Identity providers -> One-time PIN")
    elif any(i.get("type") == "onetimepin" for i in idps):
        print("one-time PIN login: already enabled")
    elif cf("POST", f"/accounts/{ACCOUNT_ID}/access/identity_providers",
            {"name": "One-time PIN", "type": "onetimepin", "config": {}}, optional=True):
        print("one-time PIN login: enabled")
    else:
        print("one-time PIN login: NOT enabled — add it at Zero Trust ->")
        print("  Integrations -> Identity providers -> One-time PIN, or grant the")
        print("  token Access: Organizations, Identity Providers, and Groups / Edit")

    # Same broader scope as the IdP step. The team domain never changes once
    # the organization exists, so fall back to whatever a previous run wrote
    # rather than blanking a working setting.
    org = cf("GET", f"/accounts/{ACCOUNT_ID}/access/organizations", optional=True)
    auth_domain = (org or {}).get("auth_domain", "") or env.get("CF_ACCESS_TEAM_DOMAIN", "")
    auds = ",".join(a for a in {ui_app.get("aud", ""), mcp_app.get("aud", "")} if a)
    print(f"access jwt trust: team={auth_domain} auds={len(auds.split(','))} app(s)")

    # 7. Secrets + trust settings -> .env (gitignored) ----------------------
    updates = {
        "TUNNEL_TOKEN": tunnel_token,
        "CF_ACCESS_CLIENT_ID": client_id,
        "CF_ACCESS_CLIENT_SECRET": client_secret,
        "MCP_ALLOWED_HOSTS": HOSTNAME,
        "CF_ACCOUNT_ID": ACCOUNT_ID,
        "CF_ACCESS_POLICY_ID": policy_id,
    }
    # Never overwrite a working trust setting with an empty string: a partial
    # run would otherwise silently break JWT verification (and with it
    # PRIVATE_COLLECTIONS, which refuses to start without these).
    if auth_domain:
        updates["CF_ACCESS_TEAM_DOMAIN"] = auth_domain
    if auds:
        updates["CF_ACCESS_AUD"] = auds
    set_env(updates)
    print(f"\nsecrets written to {ENV_PATH} (TUNNEL_TOKEN, CF_ACCESS_CLIENT_ID/SECRET, "
          "CF_ACCESS_TEAM_DOMAIN/AUD, MCP_ALLOWED_HOSTS, CF_ACCOUNT_ID, CF_ACCESS_POLICY_ID)")
    print("\nnext: docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build")
    print("browser login: Cloudflare Access One-time PIN, restricted to the configured email allowlist")
    print("add or remove admitted addresses in the Clio Access panel or Zero Trust -> Access -> Policies")
    print("optional: add PRIVATE_COLLECTIONS=true to .env for per-person collections")


if __name__ == "__main__":
    main()
