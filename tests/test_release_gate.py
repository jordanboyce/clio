"""E1: a release publishes nothing from a commit whose tests fail, and adds no
tag before the smoke test has passed.

The real check is a tag pushed to a failing commit on GitHub; this holds the
shape of the workflow that makes it so."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_the_test_workflow_can_be_called_by_the_publish_workflow():
    assert "workflow_call" in _load("tests.yml")["on"]


def test_publishing_waits_for_the_tests():
    jobs = _load("docker-publish.yml")["jobs"]

    assert jobs["tests"]["uses"] == "./.github/workflows/tests.yml"
    assert jobs["publish"]["needs"] == "tests"


def test_the_build_pushes_no_tag_and_tags_come_after_the_smoke_test():
    steps = _load("docker-publish.yml")["jobs"]["publish"]["steps"]
    names = [s.get("name") or s.get("id") or s.get("uses") for s in steps]
    build = next(s for s in steps if s.get("id") == "build")

    assert "tags" not in build["with"]
    assert "push-by-digest=true" in build["with"]["outputs"]
    smoke = names.index("Smoke-test the image")
    tagging = names.index("Tag the image")
    signing = names.index("Sign the image")
    assert names.index("build") < smoke < tagging < signing
