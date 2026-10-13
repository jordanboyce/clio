"""Verified request identity, from whichever source the site actually runs.

``PRIVATE_COLLECTIONS`` enforces per-person ownership, and it is only as
trustworthy as the identity it enforces against. A self-hosted install may
have nothing in front of it at all, or may sit inside an organisation that
already runs its own identity provider; this module serves both.

This module is the seam. One verifier per deployment, chosen by
``IDENTITY_PROVIDER``:

  local              Built-in accounts (services/local_auth.py): email and
                     password, a session cookie, invite links. The default
                     whenever PRIVATE_COLLECTIONS is on and nothing else is
                     named, and the answer for a laptop or a small team's box.
  oidc               Authorization: Bearer <JWT> from the site's own IdP
                     (Keycloak, Entra ID, Okta, PingFederate). Signature,
                     issuer, audience and expiry are verified against the
                     issuer's JWKS; an optional claim requirement narrows
                     admission further (e.g. groups=clio-users).
  trusted_header     An authenticating reverse proxy (mTLS terminator, SSO
                     proxy, SPNEGO/Kerberos front end) has already
                     authenticated the caller and passes the result in a
                     header.

The trusted-header mode is the one that can be forged, so it refuses to
start without a guard that actually holds. Two do:

  * the app is bound to loopback, so nothing but a process on the host —
    i.e. the proxy — can open a connection to it at all; or
  * the proxy signs the identity with a shared secret (HMAC-SHA256), which
    holds no matter what the network path looks like.

``TRUSTED_HEADER_PROXIES`` is available on top of those, but never as the
only guard, and the distinction matters: uvicorn's proxy-header handling
rewrites the peer address from ``X-Forwarded-For`` for connections from
``FORWARDED_ALLOW_IPS`` (127.0.0.1 by default), so the address this code
sees is not always the real socket peer. A check that an attacker can
influence is defence in depth, not a boundary. This is the same reason the
rest of the app refuses half-boundaries rather than shipping them.

Every backend answers the same question: given this request's headers and
peer address, who is this? A string identity, or None. Callers never see
raw claims; ``middleware/user_context.py`` and the ownership choke points
consume the string.

The /mcp subtree is the one place a second *configuration* of the OIDC
backend applies: OAuth 2.1 for MCP clients (docs/IDENTITY.md). The app is
only the resource server there - the site's IdP is the authorization
server - and the bearer JWT it receives is verified by the same
``OIDCIdentity`` code path, with the MCP resource URL accepted as an
audience and an optional required scope. ``get_identity_verifier(path)``
picks that verifier for /mcp so the middleware still asks one question in
one place.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600

# Providers that establish identity. "" means auto-detect: built-in accounts
# when private collections are on, otherwise no verified identity at all.
IDENTITY_PROVIDERS = ("local", "oidc", "trusted_header")


def is_mcp_path(path: str) -> bool:
    """The /mcp subtree: the only paths OAuth bearer tokens are challenged
    for and the only paths personal MCP tokens are accepted on."""
    return path == "/mcp" or path.startswith("/mcp/")


class InsufficientScope(Exception):
    """A token that IS valid for this app but lacks the scope /mcp requires.

    Raised instead of returning None so the middleware can answer the 403
    ``insufficient_scope`` challenge the MCP authorization spec defines,
    which is what lets a client step up to the right scope rather than
    loop on a 401 it cannot fix.
    """

    def __init__(self, scope: str):
        super().__init__(f"token lacks the required scope {scope!r}")
        self.scope = scope


class IdentityVerifier(Protocol):
    """Resolves a request to a verified identity string, or None."""

    via: str

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        """Cheap, I/O-free: could this request carry an identity at all?

        The auth middleware runs ``verify_request`` in a worker thread, since
        verifying can refresh a key set over the network. That threadpool is
        shared with chat and indexing, so a request carrying no credential
        must not spend a slot to learn it carries no credential.
        """
        ...

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        """The verified identity, or None. May raise InsufficientScope."""
        ...


# ── Shared JWKS cache ───────────────────────────────────────────────────────
# Both JWT backends need the same thing: fetch a key set, cache it, and
# refresh once on an unknown key id so the IdP can rotate keys without a
# restart. Kept here rather than duplicated per backend.


class JWKSCache:
    def __init__(self, url: str):
        self.url = url
        self._keys: Dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _load(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._keys and time.time() - self._fetched_at < _JWKS_TTL_SECONDS:
                return
            from jwt.algorithms import RSAAlgorithm, ECAlgorithm

            with urllib.request.urlopen(self.url, timeout=10) as resp:
                jwks = json.load(resp)
            keys: Dict[str, Any] = {}
            for key in jwks.get("keys", []):
                kid = key.get("kid")
                if not kid:
                    continue
                kty = key.get("kty")
                try:
                    if kty == "RSA":
                        keys[kid] = RSAAlgorithm.from_jwk(json.dumps(key))
                    elif kty == "EC":
                        keys[kid] = ECAlgorithm.from_jwk(json.dumps(key))
                except Exception as e:
                    logger.warning(f"JWKS key {kid} could not be parsed: {e}")
            if keys:
                self._keys = keys
                self._fetched_at = time.time()
                logger.info(f"JWKS refreshed: {len(keys)} keys from {self.url}")

    def get(self, kid: str) -> Optional[Any]:
        """Return the signing key for ``kid``, refreshing once if unknown."""
        try:
            self._load()
        except Exception as e:
            logger.warning(f"JWKS fetch failed for {self.url}: {e}")
            if not self._keys:
                return None
        if kid not in self._keys:
            try:
                self._load(force=True)
            except Exception as e:
                logger.warning(f"JWKS refresh failed for {self.url}: {e}")
                return None
        return self._keys.get(kid)


# ── Generic OIDC / OAuth 2 bearer ───────────────────────────────────────────


class OIDCIdentity:
    """Validate a bearer JWT minted by the site's own identity provider.

    The token arrives as ``Authorization: Bearer <jwt>`` — the same header
    the shared password and personal MCP tokens use, so this backend only
    considers values that look like a JWS (three dot-separated segments).
    A password or an ``asy_mcp_`` token is left for main.py's other
    branches, which is what lets all three coexist on one deployment.
    """

    via = "oidc"

    def __init__(
        self,
        issuer: str,
        audiences: list[str],
        jwks_url: str = "",
        identity_claim: str = "email",
        required_claims: str = "",
        required_scope: str = "",
    ):
        self.issuer = issuer.strip().rstrip("/")
        self.audiences = [a.strip() for a in audiences if a.strip()]
        self.identity_claim = identity_claim.strip() or "email"
        self.required = _parse_required_claims(required_claims)
        # An OAuth scope the token must carry (the /mcp verifier). Checked
        # after signature, issuer, audience and expiry: a token that fails
        # any of those is simply invalid, whereas a valid token without the
        # scope is the one case that deserves a different answer.
        self.required_scope = required_scope.strip()
        self._explicit_jwks_url = jwks_url.strip()
        self._jwks: Optional[JWKSCache] = None
        self._discovery_lock = threading.Lock()

    def _jwks_cache(self) -> Optional[JWKSCache]:
        if self._jwks is not None:
            return self._jwks
        with self._discovery_lock:
            if self._jwks is not None:
                return self._jwks
            url = self._explicit_jwks_url
            if not url:
                # Standard OIDC discovery. An air-gapped install that cannot
                # reach the discovery document can set OIDC_JWKS_URL directly.
                discovery = f"{self.issuer}/.well-known/openid-configuration"
                try:
                    with urllib.request.urlopen(discovery, timeout=10) as resp:
                        url = json.load(resp).get("jwks_uri", "")
                except Exception as e:
                    logger.warning(f"OIDC discovery failed at {discovery}: {e}")
                    return None
            if not url:
                logger.warning(f"OIDC discovery at {self.issuer} advertised no jwks_uri")
                return None
            self._jwks = JWKSCache(url)
            return self._jwks

    @staticmethod
    def _bearer_jws(headers: Mapping[str, str]) -> str:
        """The bearer value, but only when it is shaped like a JWS.

        A password or an ``asy_mcp_`` token rides the same header and belongs
        to main.py's other branches; leaving those alone is what lets all
        three credential kinds coexist on one deployment.
        """
        scheme, _, value = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return ""
        token = value.strip()
        return token if token.count(".") == 2 else ""

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool(self._bearer_jws(headers))

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        token = self._bearer_jws(headers)
        return self._verify_token(token) if token else None

    def _verify_token(self, token: str) -> Optional[str]:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        alg = header.get("alg", "")
        if not kid or not alg.startswith(("RS", "ES", "PS")):
            # Symmetric or unsigned tokens are never accepted: the key would
            # have to be shared with every client that mints one.
            return None

        cache = self._jwks_cache()
        if cache is None:
            return None
        key = cache.get(kid)
        if key is None:
            return None

        claims = None
        for aud in self.audiences:
            try:
                claims = jwt.decode(
                    token,
                    key=key,
                    algorithms=[alg],
                    audience=aud,
                    issuer=self.issuer,
                    # A token with no expiry would be valid forever; PyJWT
                    # only checks `exp` when it is present, so require it.
                    options={"require": ["exp"]},
                )
                break
            except jwt.InvalidAudienceError:
                continue
            except jwt.PyJWTError:
                return None
        if claims is None:
            return None
        if not _claims_satisfy(claims, self.required):
            return None
        if self.required_scope and self.required_scope not in _token_scopes(claims):
            raise InsufficientScope(self.required_scope)
        return self.identity_from_claims(claims)

    def identity_from_claims(self, claims: Dict[str, Any]) -> Optional[str]:
        value = claims.get(self.identity_claim)
        if not value:
            # preferred_username and sub are the usual fallbacks when the
            # deployment's IdP does not release email to this client.
            value = claims.get("preferred_username") or claims.get("sub")
        return str(value) if value else None


def _parse_required_claims(spec: str) -> list[tuple[str, str]]:
    """Parse "groups=clio-users, dept=research" into pairs."""
    pairs: list[tuple[str, str]] = []
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause or "=" not in clause:
            continue
        name, _, wanted = clause.partition("=")
        name, wanted = name.strip(), wanted.strip()
        if name and wanted:
            pairs.append((name, wanted))
    return pairs


def _token_scopes(claims: Mapping[str, Any]) -> set[str]:
    """The scopes a token carries, whichever way the IdP spells them.

    RFC 8693 / Keycloak / Auth0 use a space-separated ``scope`` string;
    Entra ID and Okta use ``scp``, as a string or a list.
    """
    scopes: set[str] = set()
    for name in ("scope", "scp"):
        value = claims.get(name)
        if isinstance(value, str):
            scopes.update(value.split())
        elif isinstance(value, (list, tuple, set)):
            scopes.update(str(v) for v in value)
    return scopes


def _claims_satisfy(claims: Mapping[str, Any], required: list[tuple[str, str]]) -> bool:
    """Every required claim must be present, matching scalar or list member."""
    for name, wanted in required:
        actual = claims.get(name)
        if actual is None:
            return False
        if isinstance(actual, (list, tuple, set)):
            if wanted not in {str(v) for v in actual}:
                return False
        elif str(actual) != wanted:
            return False
    return True


# ── Trusted reverse-proxy header ────────────────────────────────────────────


class TrustedHeaderIdentity:
    """Accept an identity a front-end proxy already authenticated.

    This is the mode that covers mTLS (the terminator passes the certificate
    subject), Kerberos/SPNEGO, and any SSO proxy the site already operates.
    The header is trivially forgeable by anything that can reach the app
    directly, so a guard is mandatory — enforced at startup by
    ``validate_config``, not here, so a misconfiguration fails loudly rather
    than silently admitting everyone.

    The optional CIDR check is defence in depth only: see this module's
    docstring on why the peer address is not always the real socket peer.
    """

    via = "trusted-header"

    def __init__(
        self,
        header_name: str,
        trusted_proxies: str = "",
        shared_secret: str = "",
        signature_header: str = "",
    ):
        self.header_name = (header_name or "").strip().lower()
        self.networks = _parse_networks(trusted_proxies)
        self.shared_secret = (shared_secret or "").strip()
        self.signature_header = (signature_header or "").strip().lower()

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return bool((headers.get(self.header_name) or "").strip())

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        identity = (headers.get(self.header_name) or "").strip()
        if not identity:
            return None

        if self.networks and not _ip_in_networks(client_ip, self.networks):
            logger.warning(
                "Rejected %s header from %s: not a trusted proxy address.",
                self.header_name,
                client_ip or "unknown",
            )
            return None

        if self.shared_secret:
            presented = (headers.get(self.signature_header) or "").strip()
            expected = hmac.new(
                self.shared_secret.encode("utf-8"),
                identity.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not presented or not hmac.compare_digest(presented.lower(), expected):
                logger.warning(
                    "Rejected %s header: signature missing or invalid.", self.header_name
                )
                return None

        return identity


def _parse_networks(spec: str) -> list:
    networks = []
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning(f"TRUSTED_HEADER_PROXIES entry {raw!r} is not a valid CIDR or address")
    return networks


def _ip_in_networks(client_ip: Optional[str], networks: list) -> bool:
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


# ── Selection ───────────────────────────────────────────────────────────────


def active_provider() -> str:
    """The provider this deployment uses: an explicit choice, or auto-detect.

    Private collections need an identity to enforce against, so turning them
    on without naming a provider selects the built-in accounts: the common
    self-hosted install then needs one setting, not a second system to run.
    """
    from config import settings

    explicit = (settings.identity_provider or "").strip().lower()
    if explicit:
        return explicit
    if settings.private_collections:
        return "local"
    return ""


def requires_identity() -> bool:
    """True when every request must carry a verified identity to be served."""
    from config import settings

    return settings.auth_require_identity or active_provider() == "local"


def validate_config() -> None:
    """Raise RuntimeError on an identity configuration that cannot be trusted.

    Called from main.py's startup posture check, next to the other
    deployment-shape refusals, so a broken identity source is a boot failure
    rather than a silent downgrade to "everyone is anonymous".
    """
    from config import settings

    provider = active_provider()
    if not provider:
        _validate_mcp_oauth()
        return
    if provider not in IDENTITY_PROVIDERS:
        raise RuntimeError(
            f"IDENTITY_PROVIDER={provider!r} is not a provider this app knows. "
            f"Use one of: {', '.join(IDENTITY_PROVIDERS)}."
        )

    if provider == "local":
        pass  # accounts live in the data directory; nothing to configure

    elif provider == "oidc":
        if not settings.oidc_issuer:
            raise RuntimeError(
                "IDENTITY_PROVIDER=oidc needs OIDC_ISSUER - the issuer URL of "
                "your identity provider, e.g. "
                "https://sso.agency.gov/realms/main. See docs/IDENTITY.md."
            )
        if not settings.oidc_audience:
            raise RuntimeError(
                "IDENTITY_PROVIDER=oidc needs OIDC_AUDIENCE - the client id or "
                "API audience tokens for this app carry. Without it any token "
                "your IdP ever issued, for any application, would be accepted "
                "here. See docs/IDENTITY.md."
            )
        if not settings.oidc_issuer.lower().startswith("https://") and not _is_local_issuer(
            settings.oidc_issuer
        ):
            raise RuntimeError(
                "OIDC_ISSUER must be https:// - token signatures are fetched "
                "from it, so a plaintext issuer lets anyone on the path mint "
                "identities. Use https, or a loopback issuer for local testing."
            )

    elif provider == "trusted_header":
        if not settings.trusted_header_name:
            raise RuntimeError(
                "IDENTITY_PROVIDER=trusted_header needs TRUSTED_HEADER_NAME - "
                "the header your proxy sets, e.g. X-Forwarded-User. See "
                "docs/IDENTITY.md."
            )
        # A guard that actually holds: loopback binding (only a process on
        # this host can connect) or an HMAC the proxy computes. A CIDR
        # allowlist is NOT sufficient on its own - see the module docstring.
        bound_loopback = _is_loopback_host(settings.host)
        if not bound_loopback and not settings.trusted_header_secret:
            raise RuntimeError(
                "IDENTITY_PROVIDER=trusted_header needs a guard that holds. "
                f"This deployment binds {settings.host!r}, so anything that can "
                "reach the port can set "
                f"{settings.trusted_header_name} and become anyone. Either set "
                "HOST=127.0.0.1 so your proxy is the only route in, or set "
                "TRUSTED_HEADER_SECRET so the proxy signs the identity. "
                "TRUSTED_HEADER_PROXIES alone does not count: uvicorn rewrites "
                "the peer address from X-Forwarded-For, so the value checked "
                "is not always the real socket peer. See docs/IDENTITY.md."
            )
        if settings.trusted_header_secret and not settings.trusted_header_signature_name:
            raise RuntimeError(
                "TRUSTED_HEADER_SECRET is set but TRUSTED_HEADER_SIGNATURE_NAME "
                "is empty - the signature would never be read. Name the header "
                "your proxy puts the HMAC in."
            )
        if bound_loopback and not settings.trusted_header_secret:
            logger.info(
                "Identity: trusted header %s, guarded by the loopback bind. "
                "Anything able to run on this host can impersonate any user; "
                "set TRUSTED_HEADER_SECRET to close that too.",
                settings.trusted_header_name,
            )

    _validate_mcp_oauth()


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip().strip("[]").lower()
    if host in {"localhost", ""}:
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_issuer(issuer: str) -> bool:
    lowered = issuer.lower()
    return lowered.startswith("http://localhost") or lowered.startswith("http://127.0.0.1")


# ── OAuth 2.1 on /mcp ───────────────────────────────────────────────────────
# The app is the OAuth *resource server* for its MCP endpoint and nothing
# more: it names the site's IdP as the authorization server (RFC 9728
# metadata, served by api/mcp.py from services/mcp_oauth.py) and verifies
# the bearer JWT that comes back. An embedded authorization server is
# exactly what a security review rejects, and would be a second copy of
# the identity the IdP already holds.


@dataclass(frozen=True)
class MCPOAuthConfig:
    """What /mcp advertises and enforces. Built from settings each call."""

    issuer: str
    audiences: tuple[str, ...]
    jwks_url: str
    scope: str
    identity_claim: str
    required_claims: str
    # Canonical MCP URL from settings ("" = derive from the request; only
    # the advertised metadata is ever derived, never an accepted audience).
    public_url: str


def mcp_public_url() -> str:
    """The canonical /mcp URL as settings know it, or "" if only a request
    can tell. MCP_PUBLIC_URL wins; MCP_ALLOWED_HOSTS - already required for
    any hostname deployment - is the fallback."""
    from config import settings

    explicit = (settings.mcp_public_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    for host in (settings.mcp_allowed_hosts or "").split(","):
        host = host.strip()
        if host:
            scheme = "http" if _is_loopback_host(host.rsplit(":", 1)[0]) else "https"
            return f"{scheme}://{host}/mcp"
    return ""


def mcp_oauth_config() -> Optional[MCPOAuthConfig]:
    """The authorization server that applies to /mcp, or None.

    MCP_OAUTH_ISSUER names one explicitly (any deployment shape); otherwise
    an ``oidc`` identity provider is reused as-is, so an OIDC deployment
    admits connector clients with no further settings. A local-accounts
    or trusted-header deployment without MCP_OAUTH_ISSUER has no OAuth on
    /mcp - its MCP clients keep using personal tokens.
    """
    from config import settings

    issuer = (settings.mcp_oauth_issuer or "").strip().rstrip("/")
    if issuer:
        audience_spec = settings.mcp_oauth_audience
        jwks_url = settings.mcp_oauth_jwks_url
    elif active_provider() == "oidc":
        issuer = (settings.oidc_issuer or "").strip().rstrip("/")
        audience_spec = settings.oidc_audience
        jwks_url = settings.oidc_jwks_url
    else:
        return None
    if not issuer:
        return None

    audiences = [a.strip() for a in (audience_spec or "").split(",") if a.strip()]
    public_url = mcp_public_url()
    # RFC 8707: a client asks the IdP for a token for `resource=<this URL>`,
    # and an IdP that honours it mints exactly that audience. Our own
    # canonical URL is, by definition, us - but only the configured one; a
    # value derived from the request's Host header would let a token minted
    # for some other host be replayed here.
    if public_url and public_url not in audiences:
        audiences.append(public_url)
    return MCPOAuthConfig(
        issuer=issuer,
        audiences=tuple(audiences),
        jwks_url=(jwks_url or "").strip(),
        scope=(settings.mcp_oauth_scope or "").strip(),
        identity_claim=settings.oidc_identity_claim,
        required_claims=settings.oidc_required_claims,
        public_url=public_url,
    )


def _validate_mcp_oauth() -> None:
    """Startup refusals for the /mcp OAuth settings, same instinct as above:
    a challenge that names an authorization server we could not verify
    tokens from would send people through a login that admits nobody, or
    - without an audience - everybody."""
    from config import settings

    issuer = (settings.mcp_oauth_issuer or "").strip()
    if issuer:
        if not (settings.mcp_oauth_audience or "").strip():
            raise RuntimeError(
                "MCP_OAUTH_ISSUER needs MCP_OAUTH_AUDIENCE - the client id or "
                "API audience tokens for the MCP endpoint carry. Without it "
                "any token that IdP ever issued, for any application, would "
                "be accepted on /mcp. See docs/IDENTITY.md."
            )
        if not issuer.lower().startswith("https://") and not _is_local_issuer(issuer):
            raise RuntimeError(
                "MCP_OAUTH_ISSUER must be https:// - token signatures are "
                "fetched from it, so a plaintext issuer lets anyone on the "
                "path mint identities for /mcp. Use https, or a loopback "
                "issuer for local testing."
            )

    public = (settings.mcp_public_url or "").strip()
    if public:
        parts = urlsplit(public)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise RuntimeError(
                f"MCP_PUBLIC_URL={public!r} is not a usable resource URL. It "
                "must be the absolute http(s) URL MCP clients reach the "
                "endpoint at, with no query or fragment, e.g. "
                "https://clio.agency.gov/mcp."
            )
        if parts.scheme == "http" and not _is_loopback_host(parts.hostname or ""):
            logger.warning(
                "MCP_PUBLIC_URL is plaintext http (%s): OAuth bearer tokens "
                "for /mcp would travel unencrypted. Use https for anything "
                "but a loopback address.",
                public,
            )

    config = mcp_oauth_config()
    if (settings.mcp_oauth_scope or "").strip() and config is None:
        raise RuntimeError(
            "MCP_OAUTH_SCOPE is set but no authorization server applies to "
            "/mcp. Set IDENTITY_PROVIDER=oidc, or name one with "
            "MCP_OAUTH_ISSUER + MCP_OAUTH_AUDIENCE. See docs/IDENTITY.md."
        )
    if config is not None:
        if not config.public_url:
            logger.warning(
                "OAuth on /mcp: MCP_PUBLIC_URL is empty, so the resource URL "
                "in the metadata is derived from each request's Host header "
                "and is NOT accepted as a token audience. Set MCP_PUBLIC_URL "
                "(e.g. https://clio.agency.gov/mcp) for a deployment "
                "reachable under a hostname."
            )
        logger.info(
            "OAuth on /mcp: authorization server %s, resource %s, audiences %s%s",
            config.issuer,
            config.public_url or "(from request)",
            ", ".join(config.audiences),
            f", required scope {config.scope}" if config.scope else "",
        )


class _ChainedVerifier:
    """Try each verifier that sees a candidate credential, first hit wins.

    Used on /mcp for a deployment whose browser identity is not a bearer
    JWT (a session cookie, a trusted proxy header) but whose MCP clients
    sign in at an IdP: the OAuth verifier looks only at a JWS-shaped bearer,
    the deployment's own verifier only at its own header, so the two never
    contend for one request. InsufficientScope propagates untouched.
    """

    via = "chained"

    def __init__(self, verifiers: list[IdentityVerifier]):
        self._verifiers = verifiers

    def has_candidate(self, headers: Mapping[str, str]) -> bool:
        return any(v.has_candidate(headers) for v in self._verifiers)

    def verify_request(
        self, headers: Mapping[str, str], client_ip: Optional[str]
    ) -> Optional[str]:
        for verifier in self._verifiers:
            if verifier.has_candidate(headers):
                who = verifier.verify_request(headers, client_ip)
                if who:
                    self.via = verifier.via
                    return who
        return None


_mcp_verifier: Optional[OIDCIdentity] = None
_mcp_signature: Optional[MCPOAuthConfig] = None


def get_mcp_oauth_verifier() -> Optional[OIDCIdentity]:
    """The verifier for OAuth bearer tokens on /mcp, or None when no
    authorization server applies there. Cached on its config, like the
    deployment OIDC verifier, because it owns a JWKS cache."""
    global _mcp_verifier, _mcp_signature

    config = mcp_oauth_config()
    if config is None:
        return None
    if _mcp_verifier is None or _mcp_signature != config:
        verifier = OIDCIdentity(
            issuer=config.issuer,
            audiences=list(config.audiences),
            jwks_url=config.jwks_url,
            identity_claim=config.identity_claim,
            required_claims=config.required_claims,
            required_scope=config.scope,
        )
        verifier.via = "oauth"
        _mcp_verifier, _mcp_signature = verifier, config
    return _mcp_verifier


# Only the OIDC backend is cached, and only because it owns a JWKS cache
# worth keeping warm across requests. The other two are cheap value objects
# built per request on purpose, so a config reload or a test that swaps the
# underlying store is never shadowed by a stale wrapper.
_oidc_verifier: Optional[OIDCIdentity] = None
_oidc_signature: Optional[tuple] = None


def get_identity_verifier(path: str = "") -> Optional[IdentityVerifier]:
    """The verifier for a request at ``path``, or None when nothing applies.

    Everywhere but /mcp that is the deployment's identity source. On /mcp
    an OAuth bearer JWT from the authorization server the endpoint
    advertises is verified too: it *replaces* an ``oidc`` deployment's
    verifier there (same IdP, plus the MCP audience and scope rules - a
    token lacking the scope must not fall through to a verifier that would
    admit it), and sits in front of any other provider's verifier, whose
    credential rides a different header.
    """
    global _oidc_verifier, _oidc_signature
    from config import settings

    provider = active_provider()
    if path and is_mcp_path(path):
        mcp_verifier = get_mcp_oauth_verifier()
        if mcp_verifier is not None:
            if provider == "oidc":
                return mcp_verifier
            base = get_identity_verifier()
            return _ChainedVerifier([mcp_verifier, base]) if base is not None else mcp_verifier
    if not provider:
        return None

    if provider == "local":
        from services.local_auth import LocalSessionIdentity

        return LocalSessionIdentity()

    if provider == "oidc":
        signature = (
            settings.oidc_issuer,
            settings.oidc_audience,
            settings.oidc_jwks_url,
            settings.oidc_identity_claim,
            settings.oidc_required_claims,
        )
        if _oidc_verifier is None or _oidc_signature != signature:
            _oidc_verifier = OIDCIdentity(
                issuer=settings.oidc_issuer,
                audiences=settings.oidc_audience.split(","),
                jwks_url=settings.oidc_jwks_url,
                identity_claim=settings.oidc_identity_claim,
                required_claims=settings.oidc_required_claims,
            )
            _oidc_signature = signature
        return _oidc_verifier

    if provider == "trusted_header":
        return TrustedHeaderIdentity(
            header_name=settings.trusted_header_name,
            trusted_proxies=settings.trusted_header_proxies,
            shared_secret=settings.trusted_header_secret,
            signature_header=settings.trusted_header_signature_name,
        )

    return None
