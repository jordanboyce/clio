"""H1: study cards from a report's questions and answers.

Only a report that holds pairs can be turned into cards. The FAQ report
does: its prompt asks for "a bold question followed by its answer"
(services/artifacts.py), which this reads back. The study guide does not:
its review questions are written without answers, so it would have to store
each question with its answer (and the passages behind the answer) before
cards could be made from it.

Two files: a tab-separated file Anki imports (front, back; with Anki's
header lines saying so), and JSON for a quiz. A citation in an answer
becomes the source's name and page. The text is the model's and the
sources', so in Anki's file it is written as escaped HTML: Anki renders a
card as HTML, and nothing in an answer may become markup.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List

_QUESTION = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+|#{1,4}\s+)?\*\*(.+?)\*\*\s*(.*)$")
_SOURCE = re.compile(r"\[Source\s+(\d+)\]", re.IGNORECASE)


def pairs(content: str, sources: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The (question, answer) pairs in an FAQ report's Markdown, in order."""
    found: List[Dict[str, str]] = []
    question, answer = None, []

    def done() -> None:
        if question and " ".join(answer).strip():
            found.append({"question": question, "answer": _plain(" ".join(answer), sources)})

    for line in (content or "").replace("\r\n", "\n").split("\n"):
        m = _QUESTION.match(line)
        if m and m.group(1).rstrip().endswith("?"):
            done()
            question, answer = m.group(1).strip().rstrip(":").strip(), [m.group(2).strip()]
        elif question is not None:
            if line.strip().startswith("#"):
                done()
                question, answer = None, []
            else:
                answer.append(line.strip())
    done()
    return found


def _plain(text: str, sources: List[Dict[str, Any]]) -> str:
    def cite(m: re.Match) -> str:
        n = int(m.group(1))
        if not 1 <= n <= len(sources):
            return ""
        s = sources[n - 1]
        page = f" p. {s['page_number']}" if s.get("page_number") else ""
        return f"({s.get('filename')}{page})"

    text = _SOURCE.sub(cite, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(.+?)\*(?![\w*])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([.,;:])", r"\1", text).strip()


def anki_tsv(cards: List[Dict[str, str]], deck: str) -> str:
    """Anki's text import: a header saying tab-separated HTML fields, then front<TAB>back."""
    def field(s: str) -> str:
        return html.escape(s, quote=False).replace("\t", " ").replace("\n", "<br>")

    lines = ["#separator:tab", "#html:true", f"#deck:{deck.replace(chr(10), ' ')}", "#columns:Front\tBack"]
    lines += [f"{field(c['question'])}\t{field(c['answer'])}" for c in cards]
    return "\n".join(lines) + "\n"


def quiz_json(cards: List[Dict[str, str]], title: str) -> str:
    return json.dumps({"title": title, "cards": cards}, ensure_ascii=False, indent=2) + "\n"
