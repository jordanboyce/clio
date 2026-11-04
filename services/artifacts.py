"""Generated artifacts: turn a collection into a cited document.

The reference product's highest-value surface isn't Q&A — it's *generated
outputs* from your sources (a summary, an FAQ, a study guide, a timeline, a
briefing). This module owns the prompt templates that drive those generations.

It is a thin adapter: each artifact type maps to an instruction message that is
fed through the existing provider-agnostic chat tool loop (the same loop the
in-app chat uses), so grounding, citations, the structured-table tools, and the
sources list all come for free. We add no new retrieval or synthesis engine —
only the framing. See api/artifacts.py for the orchestration and
services/artifact_store.py for how the results are kept.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Appended to every artifact instruction so generations stay grounded and
# citable regardless of type. The chat loop already returns a structured
# `sources` list; this keeps inline attribution consistent too.
_CITATION_CONTRACT = (
    "\n\nGrounding rules:\n"
    "- Use ONLY the collection's documents. Retrieve with the search tools "
    "before writing; do not rely on prior knowledge.\n"
    "- Cite inline as [filename] or [filename p.N] immediately after each claim "
    "that draws on a source.\n"
    "- If the sources do not cover something the artifact would normally "
    "include, say so explicitly rather than inventing it.\n"
    "- Output clean Markdown. Do not preface with 'Here is' or describe what "
    "you are about to do — emit the artifact directly."
)


MAX_TITLE_FOCUS_CHARS = 120

PODCAST_HOSTS = ("Alex", "Sam")
SCRIPT_END = "[END OF SCRIPT]"
# Bracketed anywhere, or bare on a line of its own: a host saying "the end of
# the script" in passing must not end the episode.
_SCRIPT_END_PATTERN = re.compile(r"\[\s*end of script\s*\]|^[*_\s(]*end of script[*_\s).]*$", re.IGNORECASE)
_MIN_SCRIPT_TURNS = 4
# Room for the script plus a reasoning model's thinking, which counts against
# the same limit: a 300-word script took one model 1,800 tokens.
_OUTPUT_TOKENS = {"podcast_script": 8000}

ARTIFACT_TYPES: Dict[str, Dict[str, str]] = {
    "summary": {
        "label": "Executive summary",
        "description": "A structured, citation-first overview of the whole collection.",
        "instruction": (
            "Write an executive summary of this document collection. Open with a "
            "2-3 sentence overview, then use sections with headers for the major "
            "themes. Cover what the collection contains, the key findings or "
            "points, and anything notable or unresolved. Be comprehensive but "
            "tight — aim for what a busy reader needs to know."
        ),
    },
    "faq": {
        "label": "FAQ",
        "description": "10-15 grounded question-and-answer pairs covering the sources.",
        "instruction": (
            "Generate a FAQ for this document collection: 10-15 of the most "
            "useful questions a reader would actually ask, each followed by a "
            "concise grounded answer. Order from most to least important. Format "
            "each as a bold question followed by its answer."
        ),
    },
    "timeline": {
        "label": "Timeline",
        "description": "Chronological list of dated events found in the sources.",
        "instruction": (
            "Extract a chronological timeline of the dated events, milestones, "
            "and changes described in this collection. Format as a Markdown list, "
            "each entry beginning with the date (or best-known period) in bold, "
            "then a one-line description with its citation. Order earliest to "
            "latest. If a date is approximate or inferred, mark it as such. Omit "
            "events that have no date in the sources."
        ),
    },
    "briefing": {
        "label": "Briefing doc",
        "description": "A one-page briefing: context, key points, implications, open questions.",
        "instruction": (
            "Write a one-page briefing document on this collection with these "
            "sections: 'Context' (what this is and why it matters), 'Key Points' "
            "(a bulleted list of the most important facts), 'Implications / "
            "Takeaways', and 'Open Questions'. Keep it scannable."
        ),
    },
    "study_guide": {
        "label": "Study guide",
        "description": "Key concepts, definitions, and review questions for learning the material.",
        "instruction": (
            "Create a study guide for this collection with these sections: 'Key "
            "Concepts' (each a short bolded term followed by a grounded "
            "explanation), 'Definitions' (a glossary of important terms), and "
            "'Review Questions' (8-12 questions to self-test understanding, "
            "without answers). Aim to help someone learn the material thoroughly."
        ),
    },
    "podcast_script": {
        "label": "Podcast script",
        "description": "Two hosts talk through the sources, with citations. About eight minutes read aloud.",
        "instruction": (
            f"Write the script of a podcast episode in which two hosts, {PODCAST_HOSTS[0]} and "
            f"{PODCAST_HOSTS[1]}, talk through what this collection says, for a listener who has "
            f"not read it. {PODCAST_HOSTS[0]} explains; {PODCAST_HOSTS[1]} asks what a curious "
            "listener would ask and draws out why it matters. Search the collection on each major "
            "theme before writing, so the episode covers it rather than the first passage found. "
            "Write 1,000 to 1,200 words: about eight minutes read aloud.\n\n"
            "Format, exactly:\n"
            f"- Each turn is one paragraph starting with **{PODCAST_HOSTS[0]}:** or "
            f"**{PODCAST_HOSTS[1]}:**, with a blank line between turns. Keep turns to one to four "
            "spoken sentences.\n"
            "- Write only what is said aloud: no title, headings, lists, tables, sound effects or "
            "stage directions.\n"
            "- Citations are for the written transcript and are not read aloud, so the hosts never "
            "mention them.\n"
            "- The hosts are not real people. Give them no personal stories, and no facts or "
            "figures the sources do not contain. When the sources do not cover something, a host "
            "says so.\n"
            f"- After the last turn, write {SCRIPT_END} alone on its own line."
        ),
    },
}


def list_artifact_types() -> List[Dict[str, str]]:
    """Public catalog for the UI / API."""
    return [
        {"type": key, "label": spec["label"], "description": spec["description"]}
        for key, spec in ARTIFACT_TYPES.items()
    ]


def is_valid_type(artifact_type: str) -> bool:
    return artifact_type in ARTIFACT_TYPES


def artifact_title(artifact_type: str, focus: Optional[str] = None) -> str:
    label = ARTIFACT_TYPES[artifact_type]["label"]
    # A focus can run to pages; the title is one line in a list.
    focus_line = " ".join((focus or "").split())
    if not focus_line:
        return label
    if len(focus_line) > MAX_TITLE_FOCUS_CHARS:
        focus_line = focus_line[:MAX_TITLE_FOCUS_CHARS].rstrip() + "…"
    return f"{label}: {focus_line}"


def _script_turns(content: str) -> Tuple[List[List[str]], bool]:
    """The [host, words] turns of a script, and whether it reached its end.

    Tolerates what models commonly do to the format: a preamble, headings and
    rules are dropped, labels are normalised, and a loose line continues the
    turn before it. A cut-off answer is not something to tolerate.
    """
    hosts = "|".join(PODCAST_HOSTS)
    label = re.compile(rf"^[*_\s]*({hosts})[*_\s]*:[*_\s]*(.*)$", re.IGNORECASE)
    turns: List[List[str]] = []
    for raw in content.splitlines():
        end = _SCRIPT_END_PATTERN.search(raw)
        # A marker wrapped in bold leaves its opening ** on this side.
        line = raw[:end.start()].rstrip("*_ \t").strip() if end else raw.strip()
        match = label.match(line)
        if match:
            turns.append([match.group(1).capitalize(), match.group(2).strip()])
        elif turns and line and not line.startswith("#") and line.strip("-*_ "):
            turns[-1][1] = f"{turns[-1][1]} {line}".strip()
        if end:
            return turns, True
    return turns, False


def _finish_script(content: str) -> str:
    """A script fit to read and later voice, or a reason it is not."""
    if not content.strip():
        return ""  # the job has its own way of saying the model returned nothing
    turns, ended = _script_turns(content)
    turns = [turn for turn in turns if turn[1]]
    if len(turns) < _MIN_SCRIPT_TURNS or len({host for host, _ in turns}) < len(PODCAST_HOSTS):
        raise ValueError("The model did not write a two-host script. Try again, or try another model.")
    if not ended:
        raise ValueError("The script was cut off before its end, most likely at the model's length "
                         "limit. Try a narrower focus, or another model.")
    return "\n\n".join(f"**{host}:** {words}" for host, words in turns)


_FINISHERS = {"podcast_script": _finish_script}


def output_token_limit(artifact_type: str) -> Optional[int]:
    """How many tokens this type needs per reply; None for the chat defaults."""
    return _OUTPUT_TOKENS.get(artifact_type)


def finish_content(artifact_type: str, content: str) -> str:
    """What to save from a model's answer. Raises ValueError, with a message
    fit to show, when the answer is not usable as this type of artifact."""
    finish = _FINISHERS.get(artifact_type)
    return finish(content) if finish else content


def build_artifact_message(
    artifact_type: str,
    focus: Optional[str] = None,
    custom_instructions: Optional[str] = None,
) -> str:
    """Compose the user-message instruction that drives the chat tool loop.

    Args:
        artifact_type: one of ARTIFACT_TYPES.
        focus: optional subject to narrow the artifact to (e.g. a topic or a
            specific document name). Folded into the instruction.
        custom_instructions: optional freeform extra guidance from the user.
    """
    if artifact_type not in ARTIFACT_TYPES:
        valid = ", ".join(sorted(ARTIFACT_TYPES))
        raise ValueError(f"Unknown artifact type '{artifact_type}'. Valid: {valid}")

    parts = [ARTIFACT_TYPES[artifact_type]["instruction"]]
    if focus and focus.strip():
        parts.append(
            f"Focus specifically on: {focus.strip()}. If the collection has "
            "little on this focus, say so."
        )
    if custom_instructions and custom_instructions.strip():
        parts.append(f"Additional instructions: {custom_instructions.strip()}")
    parts.append(_CITATION_CONTRACT)
    return "\n\n".join(parts)
