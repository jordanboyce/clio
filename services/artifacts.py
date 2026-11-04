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

from typing import Dict, List, Optional

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
