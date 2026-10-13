"""Shared test environment guards.

The developer/production .env next to the repo can carry live deployment
posture (PRIVATE_COLLECTIONS=true, AUTH_PASSWORD, IDENTITY_PROVIDER, ...). Tests must
run against the shipped defaults regardless, so the suite points Settings
at an env file that does not exist: every `importlib.reload(config)` then
reads real env vars and defaults only. Pinning individual variables is not
enough on its own — a fixture that `monkeypatch.delenv`s AUTH_PASSWORD
before its restoring reload would otherwise pull the real password back
out of .env, and every later test using `main.app` would answer 401.
Tests that want a mode on set it explicitly (fixture or
monkeypatch.setenv), which still wins.
"""

import os
import pathlib
import tempfile

os.environ["CLIO_ENV_FILE"] = str(
    pathlib.Path(__file__).resolve().parent / ".env.does-not-exist"
)
os.environ["PRIVATE_COLLECTIONS"] = "false"
os.environ["AUTH_PASSWORD"] = ""
# Identity source and first-run setup code: with PRIVATE_COLLECTIONS on and no
# provider named the app selects the built-in accounts, so a leaked value from
# the host environment would change which verifier a test exercises.
os.environ["IDENTITY_PROVIDER"] = ""
os.environ["SETUP_CODE"] = ""
# Built-in accounts (accounts.db) live in the data directory. Point it at a
# throwaway directory so tests that resolve an admin or look up an account
# never read or create state in the developer's real ./data.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="clio-tests-")
# A deployment default LLM (an on-prem operator's private endpoint) would
# otherwise leak into every provider-construction test through the factory.
os.environ["AI_PROVIDER"] = ""
os.environ["AI_BASE_URL"] = ""
os.environ["AI_MODEL"] = ""
os.environ["AI_API_KEY"] = ""

# The rate limiter is always-on middleware with process-wide buckets; left
# enabled, hundreds of suite requests from one TestClient host would trip
# 429s in unrelated tests. Rate-limit tests re-enable it explicitly.
os.environ["RATE_LIMIT_ENABLED"] = "false"


import pytest  # noqa: E402

import config as _config  # noqa: E402

_IMPORT_TIME_SETTINGS = _config.settings


@pytest.fixture(autouse=True)
def _restore_import_time_settings():
    """Keep `config.settings` the object every module imported.

    Fixtures that `importlib.reload(config)` leave a fresh Settings behind;
    modules that bound `settings` at import time (services.admins,
    middleware.rate_limit, ...) then disagree with tests that patch
    `config.settings`, and results depend on test order. Put the original back
    after every test so the objects always agree.
    """
    yield
    _config.settings = _IMPORT_TIME_SETTINGS
