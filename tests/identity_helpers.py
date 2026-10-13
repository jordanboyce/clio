"""Shared fake identity source for tests that need a "signed-in person".

The real verifiers (OIDC JWTs, trusted proxy headers) each have their own
tests. Tests of ownership, sharing, governance
and admin gating only need the auth middleware to map a request to an
identity, so they install this verifier in place of the real one and send
``x-test-user: <email>``.
"""

HEADER = "x-test-user"


class FakeVerifier:
    """The header value is the identity (lowercased); no header, no identity."""

    via = "test"

    def has_candidate(self, headers):
        return bool(headers.get(HEADER))

    def verify_request(self, headers, client_ip):
        value = (headers.get(HEADER) or "").strip()
        return value.lower() or None


def install(monkeypatch):
    """Route the auth middleware's identity lookup to FakeVerifier.

    Also reports an identity source as configured, so the startup check that
    refuses PRIVATE_COLLECTIONS without one lets the reloaded app start.
    """
    import services.identity as identity

    monkeypatch.setattr(identity, "get_identity_verifier", lambda path=None: FakeVerifier())
    monkeypatch.setattr(identity, "active_provider", lambda: "trusted_header")
    monkeypatch.setattr(identity, "validate_config", lambda: None)


def as_user(email):
    """Request headers that sign a call in as ``email``."""
    return {HEADER: email}
