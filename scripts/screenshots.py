"""Capture the README screenshots from a running Clio.

Drives the installed Edge (or Chrome) headlessly through Playwright at
1600x1000, 2x, in the light and dark themes, and writes the PNGs the README
embeds. Nothing here touches the server beyond ordinary API calls.

Setup, once:

    pip install playwright            # the browser channel below needs no download

Then, against a Clio that has a collection to show (the welcome screen's
sample set is what the README uses, plus the three files ``--make-sources``
writes: a two-page PDF with a chart image on each page, a DOCX with a
diagram in it, and that diagram as a PNG, so the page thumbnails and the
extracted figures have something to show; this step needs Pillow and
python-docx, which the project's venv has):

    python scripts/screenshots.py --make-sources ./shot-sources
    # add those three files and the sample set to a collection, then:
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


# ── the extra sources ─────────────────────────────────────────────────
#
# Hand-written so no PDF library is needed: a two-page PDF whose charts are
# embedded raster images (the figure extractor keeps those; a chart drawn
# with PDF operators has no picture to pull out), a DOCX with the depot
# diagram embedded, and the diagram itself as a PNG. Pillow and python-docx
# are in the project's venv.


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no scalable default
        return ImageFont.load_default()


def _bar_chart(title: str, bars, colours, size=(900, 560)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((30, 20), title, fill="#111", font=_font(26))
    base, left = size[1] - 70, 80
    d.line((left, 60, left, base), fill="#333", width=3)
    d.line((left, base, size[0] - 40, base), fill="#333", width=3)
    top = max(v for _, v in bars)
    x = left + 40
    w = (size[0] - left - 120) // len(bars) - 30
    for (label, value), colour in zip(bars, colours):
        h = int((base - 90) * value / top)
        d.rectangle((x, base - h, x + w, base), fill=colour)
        d.text((x + 6, base - h - 26), str(value), fill="#111", font=_font(20))
        d.text((x + 6, base + 10), label, fill="#111", font=_font(20))
        x += w + 30
    return img


def _jpeg(img) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _page_stream(title: str, lines: list[str], image_ref: str, size, caption: str) -> bytes:
    """Title, a few lines, the picture (468 pt wide) and its caption under it."""
    parts = [f"BT /F1 20 Tf 72 720 Td ({title}) Tj ET"]
    y = 690
    for line in lines:
        parts.append(f"BT /F1 12 Tf 72 {y} Td ({line}) Tj ET")
        y -= 18
    w, h = 468, int(468 * size[1] / size[0])
    top = y - 10
    parts.append(f"q {w} 0 0 {h} 72 {top - h} cm {image_ref} Do Q")
    parts.append(f"BT /F1 11 Tf 72 {top - h - 18} Td ({caption}) Tj ET")
    return "\n".join(parts).encode("latin-1")


def _pdf(pages) -> bytes:
    """pages: (stream, jpeg bytes, (width, height)) per page; one image each."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    n = len(pages)
    kids = " ".join(f"{3 + i * 3} 0 R" for i in range(n))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_num = 3 + n * 3
    for i, (stream, jpeg, (w, h)) in enumerate(pages):
        content_num, image_num = 4 + i * 3, 5 + i * 3
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_num} 0 R "
                    f"/Resources << /Font << /F1 {font_num} 0 R >> /XObject << /Im1 {image_num} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode()
                    + jpeg + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(pdf))
        pdf += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(pdf)


def _diagram():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1000, 640), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 960, 600), outline="#333", width=4)
    d.text((70, 70), "Depot workflow: checkout, use, return", fill="#111", font=_font(28))
    x = 90
    for label, colour in [("Checkout desk", "#2a74d9"), ("Field team", "#f28c28"), ("Return bin", "#3fa34d")]:
        d.rounded_rectangle((x, 250, x + 220, 380), radius=18, fill=colour)
        d.text((x + 24, 300), label, fill="white", font=_font(24))
        if x + 300 < 960:
            d.line((x + 220, 315, x + 300, 315), fill="#333", width=5)
            d.polygon([(x + 300, 315), (x + 280, 303), (x + 280, 327)], fill="#333")
        x += 300
    return img


def make_sources(out: Path) -> None:
    """The PDF with a chart image on each page, the DOCX with the diagram in
    it, and the diagram on its own as a PNG."""
    out.mkdir(parents=True, exist_ok=True)
    regions = _bar_chart("Equipment checkouts by region, first half of 2026",
                         [("North", 96), ("East", 72), ("South", 58), ("West", 41)],
                         ["#2a74d9", "#f28c28", "#3fa34d", "#b23a9c"])
    quarters = _bar_chart("Late returns by quarter", [("Q1", 18), ("Q2", 9)], ["#d94a2a", "#28a0f2"])
    pages = [
        (_page_stream("Regional totals 2026", [
            "The chart below compares equipment checkouts by region for the first half of 2026.",
            "North leads with 96 checkouts; West trails at 41 after the depot closure in March.",
            "Each bar counts checkouts recorded in the register, late returns included.",
        ], "/Im1", regions.size, "Figure 1: Equipment checkouts by region, first half of 2026."),
         _jpeg(regions), regions.size),
        (_page_stream("Late returns by quarter", [
            "The chart below counts returns logged after their due date, by quarter.",
            "Late returns fell from 18 in Q1 to 9 in Q2 once the five-day reminder started.",
            "The field-team amendment applies from Q2 and extends those loans to ten working days.",
            "Extensions still need approval from the operations desk before the return date.",
        ], "/Im1", quarters.size, "Figure 2: Late returns by quarter, 2026."),
         _jpeg(quarters), quarters.size),
    ]
    (out / "Regional totals 2026.pdf").write_bytes(_pdf(pages))

    diagram = _diagram()
    diagram.save(out / "Depot workflow.png")

    import docx
    from docx.shared import Inches
    d = docx.Document()
    d.add_heading("Depot workflow notes", 0)
    d.add_paragraph("Every piece of equipment moves through three stations: the checkout desk records the loan, "
                    "the field team uses the item on site, and the return bin closes the loan when the item "
                    "comes back. The diagram below shows the depot workflow from checkout to return.")
    d.add_picture(str(out / "Depot workflow.png"), width=Inches(5.5))
    d.add_paragraph("Figure 1: The depot workflow from checkout desk to return bin.")
    d.add_paragraph("A loan that misses its return date is logged as a late return and counted in the "
                    "quarterly totals. The operations desk reviews late returns every Monday.")
    d.save(out / "Depot workflow notes.docx")
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
