"""Response headers a corporate security scan expects, on every response.

Applied from middleware/request_meta.py rather than as a middleware of its own:
that one is already outermost, so 401s and 429s from the inner layers get them
too, and every further BaseHTTPMiddleware layer costs each streamed response.

A response that already carries a header keeps it, which is how the API docs
page brings the policy its inline start-up script needs.
"""

from config import settings

# A PDF opens in the browser's own viewer, and a policy on the response can
# stop that viewer from loading. A PDF runs no page script, so it loses nothing.
_NO_CSP_TYPES = ("application/pdf",)


def apply(request, response) -> None:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "same-origin")

    policy = settings.content_security_policy.strip()
    content_type = headers.get("content-type", "")
    if policy and not content_type.startswith(_NO_CSP_TYPES):
        headers.setdefault("Content-Security-Policy", policy)

    # request.url.scheme is https for a direct TLS connection, and for a proxy's
    # X-Forwarded-Proto only when that proxy is in FORWARDED_ALLOW_IPS. Sent over
    # plain HTTP it would be ignored at best, and could lock a site out at worst.
    if settings.hsts_max_age_seconds > 0 and request.url.scheme == "https":
        headers.setdefault("Strict-Transport-Security", f"max-age={int(settings.hsts_max_age_seconds)}")

    # An app's answers are its own: a proxy on the way must not keep or reuse them.
    if request.url.path.startswith("/api/v1/"):
        headers.setdefault("Cache-Control", "no-store")
