"""Capture the README screenshots from a running Clio.

Drives the installed Edge (or Chrome) headlessly through Playwright at
1600x1000, 2x, in the light and dark themes, and writes the PNGs the README
embeds. Nothing here touches the server beyond ordinary API calls.

Setup, once:

    pip install playwright            # the browser channel below needs no download

Then, against a Clio that has a collection to show (the welcome screen's
sample set is what the README uses, plus the two files ``--make-sources``
writes: a two-page PDF with a bar chart on each page and a PNG diagram, so
the page thumbnails have something to draw):

    python scripts/screenshots.py --make-sources ./shot-sources
    # add those two files and the sample set to a collection, then:
    python scripts/screenshots.py --base http://localhost:8473 --collection <id> --out docs/screenshots

The Ask shot needs a model: ``--model`` names an Ollama model reachable from
the server (the provider is preset in the browser's local storage, nothing is
saved on the server). ``--only ask,find`` limits the run; ``--theme light``
skips the dark set.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

QUESTION = "Which region had the most checkouts, and does the field-team amendment change the return deadline?"
FIND = "checkouts by region and the depot workflow"


# ── the two extra sources ─────────────────────────────────────────────


def _page_stream(title: str, lines: list[str], bars=None) -> bytes:
    parts = [f"BT /F1 20 Tf 72 720 Td ({title}) Tj ET"]
    y = 690
    for line in lines:
        parts.append(f"BT /F1 12 Tf 72 {y} Td ({line}) Tj ET")
        y -= 18
    if bars:
        x = 90
        parts.append("0.2 0.2 0.2 RG 1 w 80 300 m 80 560 l S 80 300 m 520 300 l S")
        colours = ["0.16 0.45 0.85", "0.95 0.55 0.15", "0.25 0.70 0.35", "0.75 0.25 0.55"]
        for i, (label, value) in enumerate(bars):
            h = value * 2.4
            parts.append(f"{colours[i % 4]} rg {x} 300 60 {h} re f")
            parts.append(f"0 g BT /F1 10 Tf {x} 285 Td ({label}) Tj ET")
            parts.append(f"BT /F1 10 Tf {x + 10} {300 + h + 6} Td ({value}) Tj ET")
            x += 100
    return "\n".join(parts).encode("latin-1")


def make_sources(out: Path) -> None:
    """A hand-written PDF (real text, drawn charts) and a PNG diagram."""
    out.mkdir(parents=True, exist_ok=True)
    pages = [
        _page_stream("Regional totals 2026", [
            "Figure 1 compares equipment checkouts by region for the first half of 2026.",
            "North leads with 96 checkouts; West trails at 41 after the depot closure in March.",
            "Each bar counts checkouts recorded in the register, late returns included.",
        ], bars=[("North", 96), ("East", 72), ("South", 58), ("West", 41)]),
        _page_stream("Late returns by quarter", [
            "Figure 2 counts returns logged after their due date, by quarter.",
            "Late returns fell from 18 in Q1 to 9 in Q2 once the five-day reminder started.",
            "The field-team amendment applies from Q2 and extends those loans to ten working days.",
            "Extensions still need approval from the operations desk before the return date.",
        ], bars=[("Q1", 18), ("Q2", 9)]),
    ]
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_num = 3 + len(pages) * 2
    for i, stream in enumerate(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + i * 2} 0 R "
                    f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(pdf))
        pdf += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (out / "Regional totals 2026.pdf").write_bytes(pdf)

    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1000, 640), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 960, 600), outline="#333", width=4)
    d.text((70, 70), "Depot workflow: checkout, use, return", fill="#111")
    x = 90
    for label, colour in [("Checkout desk", "#2a74d9"), ("Field team", "#f28c28"), ("Return bin", "#3fa34d")]:
        d.rounded_rectangle((x, 250, x + 220, 380), radius=18, fill=colour)
        d.text((x + 30, 305), label, fill="white")
        if x + 300 < 960:
            d.line((x + 220, 315, x + 300, 315), fill="#333", width=5)
            d.polygon([(x + 300, 315), (x + 280, 303), (x + 280, 327)], fill="#333")
        x += 300
    img.save(out / "Depot workflow.png")
    print("wrote", sorted(p.name for p in out.iterdir()))


# ── capture ───────────────────────────────────────────────────────────


def _preset(theme: str, tab: str, collection: str, model: str) -> str:
    state = {
        "theme": theme,
        "onboarding_dismissed": "true",
        "active_tab": tab,
        "clio_current_collection": collection,
        "clio_storage_migrated": "true",
        "sources_sidebar_open": "true",
        "ai_providers_config": json.dumps([{"id": "ollama", "model": model,
                                            "baseUrl": "http://localhost:11434", "available": True}]),
        "ai_settings": json.dumps({"provider": "ollama", "rerank": False, "synthesize": False}),
    }
    return "".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in state.items())


def capture(base: str, collection: str, out: Path, themes: list[str], only: set[str] | None,
            model: str, channel: str) -> int:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    failures = 0

    def shoot(page, name, theme):
        page.wait_for_timeout(600)
        path = out / f"{name}{'' if theme == 'light' else '-dark'}.png"
        page.screenshot(path=str(path))
        print("wrote", path)

    def open_tab(context, theme, tab):
        page = context.new_page()
        page.add_init_script(_preset(theme, tab, collection, model))
        page.goto(f"{base}/?tab={tab}", wait_until="load")
        page.wait_for_timeout(2000)
        return page

    def ask(context, theme):
        page = open_tab(context, theme, "chat")
        box = page.get_by_placeholder("Ask about your sources")
        box.click()
        box.fill(QUESTION)
        box.press("Enter")
        page.wait_for_selector("div[role=list][aria-label=Sources]", timeout=300_000)
        page.get_by_text("Review evidence").first.click()
        page.wait_for_selector("li img", timeout=20_000)
        page.wait_for_timeout(1200)
        shoot(page, "01-ask-cited-answer", theme)
        page.close()

    def find(context, theme):
        page = open_tab(context, theme, "search")
        box = page.get_by_placeholder("Ask a question or find a passage")
        box.click()
        box.fill(FIND)
        box.press("Enter")
        page.wait_for_selector("article", timeout=60_000)
        page.wait_for_timeout(2500)
        shoot(page, "02-find-results", theme)
        page.close()

    def settings(context, theme):
        page = open_tab(context, theme, "settings")
        page.get_by_text("AI Providers", exact=True).first.click()
        page.wait_for_timeout(1500)
        shoot(page, "06-settings-providers", theme)
        page.close()

    def simple(name, tab):
        def run(context, theme):
            page = open_tab(context, theme, tab)
            page.wait_for_timeout(1500)
            shoot(page, name, theme)
            page.close()
        return run

    shots = {
        "ask": ask,
        "find": find,
        "connect": simple("03-connect-agents", "mcp"),
        "collections": simple("04-collections", "collections"),
        "reports": simple("05-reports", "generate"),
        "settings": settings,
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=True)
        for theme in themes:
            context = browser.new_context(viewport={"width": 1600, "height": 1000},
                                          device_scale_factor=2, color_scheme=theme)
            for key, fn in shots.items():
                if only and key not in only:
                    continue
                try:
                    fn(context, theme)
                except Exception as e:  # report and carry on with the rest
                    failures += 1
                    print(f"FAILED {key} ({theme}): {e}", file=sys.stderr)
            context.close()
        browser.close()
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make-sources", metavar="DIR", help="write the two extra sample sources and exit")
    ap.add_argument("--base", default="http://localhost:8473")
    ap.add_argument("--collection", default="default")
    ap.add_argument("--out", default="docs/screenshots")
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    ap.add_argument("--only", help="comma-separated: ask,find,connect,collections,reports,settings")
    ap.add_argument("--model", default="gemma3:4b", help="Ollama model for the Ask shot")
    ap.add_argument("--channel", default="msedge", help="Playwright browser channel (msedge, chrome)")
    a = ap.parse_args()
    if a.make_sources:
        make_sources(Path(a.make_sources))
        return 0
    themes = ["light", "dark"] if a.theme == "both" else [a.theme]
    only = set(a.only.split(",")) if a.only else None
    return 1 if capture(a.base, a.collection, Path(a.out), themes, only, a.model, a.channel) else 0


if __name__ == "__main__":
    raise SystemExit(main())
