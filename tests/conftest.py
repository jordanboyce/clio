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
# A leaked identity provider from the host environment would change which
# verifier a test exercises.
os.environ["IDENTITY_PROVIDER"] = ""
# Point the data directory at a throwaway so tests never read or create state
# in the developer's real ./data.
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

# TestClient names its Host "testserver", which a loopback install would
# refuse (middleware/boundary.py). The suite lists it the way an operator
# lists a proxy's hostname; Host allowlist tests set their own.
os.environ["ALLOWED_HOSTS"] = "testserver"

# The reranker turns itself on when its model happens to be cached on the
# developer's machine; that would reorder results in unrelated ranking tests.
# Reranker tests set it (or the auto-detection) explicitly.
os.environ["ENABLE_RERANKER"] = "false"

# Trust no one is off by default, which turns the prompt-injection and PII
# scans off; most governance tests are about those scans. The switch's own
# tests (tests/test_trust_no_one.py) set it both ways.
os.environ["TRUST_NO_ONE"] = "true"


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


@pytest.fixture(autouse=True)
def _settings_saves_stay_out_of_the_real_env_file(tmp_path, monkeypatch):
    """Saving settings mirrors them into ./.env, which here is the developer's own."""
    from services.config_manager import config_manager

    monkeypatch.setattr(config_manager, "env_file", tmp_path / ".env.test")


@pytest.fixture(autouse=True)
def _no_index_job_outlives_its_test(monkeypatch):
    """Wait for the index jobs a test started before its patches are undone
    (see tests/job_threads.py). Asking for `monkeypatch` makes this tear down
    first, while the test's own database path and indexers still apply. A
    job still running after a minute is a hung test, reported as such."""
    from tests.job_threads import drain_index_jobs

    yield
    stuck = drain_index_jobs(timeout=60.0)
    if stuck:
        pytest.fail(f"index job threads still running after the test: {stuck}")


@pytest.fixture(autouse=True)
def _failed_sign_ins_are_not_shared_between_tests():
    """Every TestClient call comes from one address; one test's wrong
    passwords must not spend the next test's failed sign-in budget."""
    from middleware.auth_failures import failed_auth_limiter

    failed_auth_limiter.reset()
    yield
    failed_auth_limiter.reset()


@pytest.fixture(autouse=True)
def _model_listings_are_not_shared_between_tests():
    """/api/ai/models caches listings per provider and key; one test's fake
    provider must not answer the next test's request."""
    from services import model_catalog

    model_catalog.clear_cache()
    yield
    model_catalog.clear_cache()


@pytest.fixture(autouse=True)
def _review_badge_counts_are_not_shared_between_tests():
    """The review badge summary is cached process-wide for 30 seconds
    (services/governance.py), so a test that read it would otherwise hand its
    count to the next test in the same worker."""
    from services import governance

    governance.invalidate_review_summary()
    yield
    governance.invalidate_review_summary()
