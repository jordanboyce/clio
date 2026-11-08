"""Models from a mirror of huggingface.co (HF_ENDPOINT).

Many networks block huggingface.co but allow a mirror of it (an Artifactory or
Nexus remote repository). The Hugging Face libraries already download from
HF_ENDPOINT; what Clio adds must follow it too, or the setup screen turns away a
download that would work.
"""

import os

import pytest

import services.embedder as embedder
from services.embedder import DEFAULT_HUB_ENDPOINT, hub_endpoint, hub_host

MIRROR = "https://artifactory.corp.example/api/huggingfaceml/hf-remote"


@pytest.fixture()
def mirror(monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", MIRROR + "/")
    return MIRROR


def test_the_default_source_is_huggingface(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    assert hub_endpoint() == DEFAULT_HUB_ENDPOINT
    assert hub_host() == "huggingface.co"


def test_a_mirror_is_read_from_the_environment(mirror):
    assert hub_endpoint() == MIRROR  # trailing slash dropped
    assert hub_host() == "artifactory.corp.example"


def test_the_setting_reaches_the_environment_without_overriding_it(monkeypatch, tmp_path):
    """HF_ENDPOINT in .env under `python main.py` is a setting, not an
    environment variable; config puts it where the libraries read it."""
    import config

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    config.Settings(data_dir=tmp_path, hf_endpoint=MIRROR + "/")
    assert os.environ["HF_ENDPOINT"] == MIRROR

    monkeypatch.setenv("HF_ENDPOINT", "https://already.set")
    config.Settings(data_dir=tmp_path, hf_endpoint=MIRROR)
    assert os.environ["HF_ENDPOINT"] == "https://already.set"


def test_the_reachability_probe_asks_the_mirror(mirror, monkeypatch):
    import httpx

    from api import system

    asked = []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def head(self, url):
            asked.append(url)
            return httpx.Response(404)  # a mirror's root: present, nothing there

    monkeypatch.setattr(httpx, "Client", Client)
    assert system._huggingface_reachable() is True
    assert asked == [MIRROR]


def _failed_download(monkeypatch, tmp_path):
    def blocked(fe_id, cache_dir, retries=1):
        raise RuntimeError("Could not load model from any source.")

    monkeypatch.setattr(embedder, "fastembed_model_cached", lambda *a, **k: False)
    monkeypatch.setattr(embedder, "_fastembed_prefetch", blocked)
    with pytest.raises(RuntimeError) as err:
        embedder.FastEmbedService("all-MiniLM-L6-v2", cache_dir=tmp_path / "models")
    return str(err.value)


def test_a_failed_download_names_the_mirror(mirror, monkeypatch, tmp_path):
    message = _failed_download(monkeypatch, tmp_path)
    assert "artifactory.corp.example" in message
    assert "huggingface.co" not in message


def test_without_a_mirror_the_failure_suggests_one(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    message = _failed_download(monkeypatch, tmp_path)
    assert "huggingface.co" in message
    assert "HF_ENDPOINT" in message


def test_the_readiness_report_names_the_mirror_and_still_counts_it_outbound(mirror):
    from services import readiness

    row = next(p for p in readiness.egress_paths() if p["kind"] == "Model downloads")
    assert row["destination"] == "artifactory.corp.example"
    # A mirror may be public; the posture report must not call it internal.
    assert row["state"] == "open"
