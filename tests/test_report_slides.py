"""H3: a report as slides for Marp or Reveal.js."""

from services import report_slides
from tests.test_collection_bundle import _source_collection, env  # noqa: F401
from tests.test_report_file import _report, client  # noqa: F401

BRIEFING = """# Equipment briefing

## Context

The policy EQ-17 sets a return period [Source 1].

## Key Points

- Five working days for most staff [Source 1].
- Field teams get ten [Source 2].
- Extensions need approval.
- Point four.
- Point five.
- Point six.
- Point seven.
- Point eight <script>alert(1)</script>.

## Open Questions

- Is there a late fee?
"""


def test_a_slide_for_each_section_long_ones_split_and_the_sources_last():
    art = {"title": "Equipment briefing", "content": BRIEFING, "created_at": "2026-11-20T10:00:00",
           "sources": [{"filename": "policy.md", "page_number": 1}, {"filename": "amendment.md", "page_number": 2}]}
    marp = report_slides.slides(art, "Sample", "marp")
    assert marp.startswith("---\nmarp: true\npaginate: true\n---\n# Equipment briefing\n\nSample · 2026-11-20\n")
    slides = marp.split("\n---\n")[2:]
    titles = [s.strip().splitlines()[0] for s in slides]
    assert titles == ["## Context", "## Key Points", "## Key Points (continued)", "## Open Questions", "## Sources"]
    assert "- Field teams get ten [2]." in marp and "[Source" not in marp
    assert "&lt;script&gt;" in marp and "<script>" not in marp
    assert "1. policy.md, page 1\n2. amendment.md, page 2" in marp
    reveal = report_slides.slides(art, "Sample", "reveal")
    assert reveal.startswith("# Equipment briefing") and "marp:" not in reveal


def test_http_slides(env, client):
    cid, ids = _source_collection(env)
    _report(cid, ids, BRIEFING)
    r = client.get(f"/api/artifacts/rep000000001/slides?collection_id={cid}&format=reveal")
    assert r.status_code == 200 and "staff-faq.slides.md" in r.headers["content-disposition"]
    assert "## Key Points" in r.text
    assert client.get(f"/api/artifacts/rep000000001/slides?collection_id={cid}&format=pptx").status_code == 422
