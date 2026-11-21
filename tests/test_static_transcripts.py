"""H4: transcripts that play: WebVTT, cues, and <clio-transcript>."""

import json
import sqlite3
import wave

from services import static_export as se
from services.collection_service import collection_service
from services.static_export import transcripts
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import _add

LINES = "\n".join(f"[{m:02d}:{s:02d}] Line {i} of the meeting, about item {i}, with <b>care</b> & detail."
                  for i, (m, s) in enumerate([(0, 0), (0, 7), (0, 15), (1, 2), (1, 40), (2, 5), (2, 30), (3, 1),
                                              (3, 45), (4, 10), (4, 59), (5, 20)]))


def _recording(cid, name="meeting.wav"):
    """A transcribed recording, as indexing leaves one: timed lines in its text."""
    doc_id = _add(cid, "meeting.txt", LINES)
    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET filename=? WHERE document_id=?", (name, doc_id))
        conn.execute("UPDATE chunks SET filename=? WHERE document_id=?", (name, doc_id))
    return doc_id


def test_cues_come_from_the_timed_lines_once_each_in_order():
    passages = [{"id": "a", "page": 1, "text": "[00:00] One.\n[00:07] Two."},
                {"id": "b", "page": 1, "text": "[00:07] Two.\n[00:15] Three."}]
    cues = transcripts.cues(passages, 208)
    assert [(c["start"], c["end"], c["text"], c["passage"]) for c in cues] == [
        (0, 7, "One.", "a"), (7, 15, "Two.", "a"), (15, 20, "Three.", "b")]
    vtt = transcripts.webvtt([{"start": 75, "end": 80, "text": "a <b> & --> c"}])
    assert vtt == "WEBVTT\n\n1\n00:01:15.000 --> 00:01:20.000\na &lt;b&gt; &amp; -&gt; c\n"


def test_a_recording_gets_a_vtt_and_a_cue_file(site, tmp_path):
    cid, _ = _public(site)
    doc_id = _recording(cid)
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "transcripts")
    assert offer["available"] and offer["note"].startswith("1 recording")
    manifest, _ = se.write_export(cid, tmp_path / "out", confirm_public=True, include="transcripts")
    entry = manifest["additions"]["transcripts"]["sources"][doc_id]
    assert entry == {"vtt": "transcripts/meeting.vtt", "cues": "transcripts/meeting.json", "kind": "audio",
                     "count": 12}
    vtt = (tmp_path / "out" / entry["vtt"]).read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT\n") and vtt.count(" --> ") == 12 and "&lt;b&gt;care&lt;/b&gt;" in vtt


def test_the_player_seeks_to_a_line_and_opens_at_a_passage(kit, site, browser, tmp_path):
    cid, _ = _public(site)
    doc_id = _recording(cid)
    se.save_addresses(cid, files_base_url="/media/")
    out = tmp_path / "site"
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, include="reader,transcripts")
    link = next(d for d in manifest["documents"] if d["id"] == doc_id)["link"]
    assert link == {"url": "/media/meeting.wav", "from": "files", "time": True}
    (out / "media").mkdir()
    with wave.open(str(out / "media" / "meeting.wav"), "wb") as w:  # six minutes of silence
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(800)
        w.writeframes(b"\x80" * 800 * 360)
    cues = json.loads((out / "search/transcripts/meeting.json").read_text(encoding="utf-8"))["cues"]
    with static_server(out) as server:
        page = browser.page()
        t = "document.querySelector('clio-transcript').shadowRoot"
        page.goto(server.url("search/read/meeting.html"))
        page.wait_for(f"{t}.querySelectorAll('li button').length === 12")
        assert page.eval(f"{t}.querySelector('audio').src") == server.url("media/meeting.wav")
        assert "<b>" not in page.eval(f"{t}.querySelector('li').innerHTML") and \
            "<b>care</b>" in page.eval(f"{t}.querySelector('li').textContent")
        page.eval(f"{t}.querySelectorAll('li button')[4].click()")
        assert page.eval("document.querySelector('clio-transcript').position") == cues[4]["start"]
        assert page.eval(f"{t}.querySelectorAll('li button')[4].getAttribute('aria-current')") == "true"
        # Opened at a passage: the transcript starts at that passage's first line.
        passage = cues[-1]["passage"]
        page.goto(server.url(f"search/read/meeting.html#p-{passage}"))
        page.wait_for(f"{t}.querySelectorAll('li button').length === 12")
        first = next(c["start"] for c in cues if c["passage"] == passage)
        assert page.wait_for(f"document.querySelector('clio-transcript').position === {first}")
        page.goto(server.url("search/read/meeting.html#t=95"))
        assert page.wait_for("document.querySelector('clio-transcript').position === 95")
        page.close()
