"""Whether the posture checks think another machine can open the port.

Inside a container HOST is always 0.0.0.0, so the published interface that
compose passes as BIND_ADDRESS has to count, or every loopback-only Docker
install is warned about a network it is not on.
"""

import pytest

from config import settings
from services import readiness


@pytest.mark.parametrize("host, bind, expected", [
    ("127.0.0.1", "", False),
    ("localhost", "0.0.0.0", False),      # HOST on loopback wins outright
    ("0.0.0.0", "", True),                # plain python main.py on the network
    ("0.0.0.0", "127.0.0.1", False),      # compose default: published on loopback
    ("0.0.0.0", "::1", False),
    ("0.0.0.0", "0.0.0.0", True),         # published on every interface
    ("0.0.0.0", "192.168.1.20", True),    # published on one LAN interface
])
def test_reachable_beyond_host(monkeypatch, host, bind, expected):
    monkeypatch.setattr(settings, "host", host)
    monkeypatch.setattr(settings, "bind_address", bind)
    assert readiness.reachable_beyond_host() is expected


def test_loopback_publish_passes_access_control_without_a_password(monkeypatch):
    monkeypatch.setattr(settings, "host", "0.0.0.0")
    monkeypatch.setattr(settings, "bind_address", "127.0.0.1")
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "identity_provider", "")
    auth = next(c for c in readiness._posture_checks() if c["id"] == "auth")
    assert auth["status"] == readiness.PASS
    assert "loopback" in auth["detail"]
