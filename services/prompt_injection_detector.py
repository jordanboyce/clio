"""Prompt injection detection for OCR-extracted and document text.

Checks extracted text for signals commonly used in indirect prompt injection
attacks — adversarial content embedded in documents to manipulate downstream
LLM processing (reranking, synthesis, chat).

Detection categories:
  - instruction_override: "ignore previous instructions", "forget everything", etc.
  - persona_injection:    "act as", "pretend you are", DAN jailbreaks, etc.
  - model_token:          ChatML / Llama / role-header format tokens
  - context_manipulation: "your real/hidden instructions:", "confidential prompt:", etc.
  - exfiltration:         "reveal your system prompt", "print your instructions", etc.
  - bypass_attempt:       "bypass safety filters", "without restrictions", etc.
  - encoding_trick:       zero-width chars, direction-overrides, homoglyphs
  - meta_injection:       self-referential terms ("prompt injection", "jailbreak")
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class InjectionFinding:
    """A single detected injection signal."""
    category: str       # e.g. "instruction_override"
    pattern_name: str   # human-readable identifier
    matched_text: str   # excerpt (capped at 120 chars)
    severity: str       # "low" | "medium" | "high"
    char_offset: int    # byte offset in the scanned string
    downgraded: bool = False  # severity lowered because the match is quoted / an example


@dataclass
class InjectionScanResult:
    """Result of scanning one piece of text for injection signals."""
    is_flagged: bool
    risk_score: float                              # 0.0 (clean) … 1.0 (very likely injection)
    findings: List[InjectionFinding] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "is_flagged": self.is_flagged,
            "risk_score": round(self.risk_score, 3),
            "finding_count": len(self.findings),
            "findings": [
                {
                    "category": f.category,
                    "pattern_name": f.pattern_name,
                    "matched_text": f.matched_text,
                    "severity": f.severity,
                    "char_offset": f.char_offset,
                    **({"downgraded": True} if f.downgraded else {}),
                }
                for f in self.findings
            ],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Severity → score contribution
# ---------------------------------------------------------------------------
_SEVERITY_WEIGHTS = {"low": 0.15, "medium": 0.35, "high": 0.60}

# ---------------------------------------------------------------------------
# Pattern registry: (category, name, regex_source, severity)
# ---------------------------------------------------------------------------
_PATTERNS: List[Tuple[str, str, str, str]] = [

    # ── Instruction override ────────────────────────────────────────────────
    ("instruction_override", "ignore_instructions",
     r"\bignore\b.{0,40}\b(previous|prior|all|above)\b.{0,40}"
     r"\b(instructions?|directives?|prompts?|rules?|constraints?)\b",
     "high"),

    ("instruction_override", "disregard_instructions",
     r"\bdisregard\b.{0,40}\b(previous|prior|all|your|the)\b.{0,50}"
     r"\b(instructions?|directives?|prompts?|rules?|constraints?)\b",
     "high"),

    ("instruction_override", "forget_instructions",
     r"\bforget\b.{0,40}\b(everything|all|what\s+you|the\s+above|previous)\b",
     "high"),

    ("instruction_override", "new_instructions_header",
     r"\bnew\s+(instructions?|directives?|task|role|objective|system)\s*[:=]",
     "high"),

    ("instruction_override", "override_instructions",
     r"\boverride\b.{0,40}\b(previous|all|your|the)\b.{0,40}"
     r"\b(instructions?|settings?|directives?|rules?)\b",
     "high"),

    ("instruction_override", "your_real_instructions",
     r"\byour\s+(new|real|actual|true|secret)\s+(instructions?|role|purpose|task|goal|directive)\b",
     "high"),

    ("instruction_override", "from_now_on",
     r"\bfrom\s+now\s+on\b.{0,80}\byou\s+(are|must|should|will|have\s+to)\b",
     "medium"),

    ("instruction_override", "you_are_now",
     r"\byou\s+are\s+now\b.{0,60}",
     "medium"),

    ("instruction_override", "stop_being",
     r"\bstop\s+(being|acting\s+as|pretending\s+to\s+be)\b.{0,60}",
     "medium"),

    # ── Persona injection ───────────────────────────────────────────────────
    ("persona_injection", "act_as",
     r"\bact\s+as\s+(if|a|an|the|though)\b",
     "medium"),

    ("persona_injection", "pretend_you_are",
     r"\bpretend\s+(you\s+are|to\s+be|that\s+you|you're)\b",
     "medium"),

    ("persona_injection", "roleplay_as",
     r"\broleplay\s+as\b",
     "medium"),

    ("persona_injection", "ai_without_restrictions",
     r"\byou\s+are\s+(an?\s+)?ai\b.{0,100}"
     r"\bwithout\b.{0,60}\b(restriction|filter|safety|limit|guardrail|moral|ethical)\b",
     "high"),

    ("persona_injection", "dan_jailbreak",
     r"\bDAN\b.{0,80}"
     r"\b(do\s+anything\s+now|no\s+restriction|without\s+(limit|filter|safety))\b",
     "high"),

    ("persona_injection", "developer_mode",
     r"\b(developer|dev|jailbreak|jail[\s\-]*break)\s+mode\b",
     "medium"),

    ("persona_injection", "unrestricted_mode",
     r"\b(unrestricted|uncensored|unfiltered)\s+(mode|version|ai|assistant|model)\b",
     "medium"),

    # ── Model / chat-format tokens ──────────────────────────────────────────
    ("model_token", "chatml_tokens",
     r"<\|im_(start|end)\|>",
     "high"),

    ("model_token", "llama_inst_tokens",
     r"\[/?INST\]",
     "high"),

    ("model_token", "llama2_sys_tokens",
     r"<</?SYS>>",
     "high"),

    ("model_token", "chat_role_pipe_tags",
     r"<\|(system|user|assistant|human)\|>",
     "high"),

    ("model_token", "system_xml_tag",
     r"</?system>",
     "high"),

    ("model_token", "markdown_role_headers",
     r"^###\s+(System|Human|Assistant|User)\s*:",
     "medium"),

    ("model_token", "role_colon_lines",
     r"^(SYSTEM|ASSISTANT|USER|HUMAN)\s*:",
     "medium"),

    ("model_token", "bos_eos_tokens",
     r"</?s>|<\|begin_of_text\|>|<\|end_of_text\|>",
     "medium"),

    # ── Context manipulation ────────────────────────────────────────────────
    ("context_manipulation", "actual_system_prompt",
     r"\b(actual|real|true|hidden|secret)\s+(system\s+prompt|instructions?|directives?)\b",
     "high"),

    ("context_manipulation", "confidential_instructions",
     r"\b(confidential|secret|hidden)\s+(instructions?|prompt|directives?|rules?)\s*:",
     "high"),

    ("context_manipulation", "your_training_says",
     r"\byour\s+(training|knowledge)\s+(includes?|says?|states?|tells?)\b",
     "medium"),

    ("context_manipulation", "note_to_ai",
     r"\bnote\s+to\s+(the\s+)?(ai|model|assistant|llm)\b",
     "medium"),

    ("context_manipulation", "ai_reading_this",
     r"\b(ai|model|assistant|llm)\s+(reading|processing|parsing)\s+this\b",
     "medium"),

    # ── Exfiltration attempts ───────────────────────────────────────────────
    # Needs "your ..." or an explicit system/hidden prompt: "show the instructions"
    # is what every installation guide says.
    ("exfiltration", "reveal_system_prompt",
     r"\b(reveal|print|repeat|show|output|display|tell\s+me|write\s+out)\b.{0,50}"
     r"\b(your\s+(system\s+)?(prompt|instructions?|directives?|constraints?|rules?)"
     r"|(the\s+)?(system|initial|hidden|original)\s+(prompt|instructions?|message))\b",
     "high"),

    ("exfiltration", "what_are_your_instructions",
     r"\bwhat\s+are\s+your\s+(instructions?|directives?|rules?|constraints?|system\s+prompt)\b",
     "medium"),

    ("exfiltration", "summarize_your_prompt",
     r"\b(summarize|describe|explain|list)\s+(your\s+)?(prompt|instructions?|system\s+message)\b",
     "medium"),

    # ── Bypass / disable attempts ───────────────────────────────────────────
    # Aimed at the assistant ("your safety filters") it is an attack; without
    # that it is as likely a machinery manual or a spam-filter how-to.
    ("bypass_attempt", "bypass_your_safety",
     r"\b(bypass|disable|circumvent|override|ignore|remove|turn\s+off)\b.{0,30}"
     r"\b(your|the\s+(ai|model|assistant|llm)'?s?)\b.{0,30}"
     r"\b(safety|filter|guardrail|restriction|limitation|constraint|ethical|moral)",
     "high"),

    ("bypass_attempt", "bypass_safety",
     r"\b(bypass|disable|circumvent|override|ignore|remove|turn\s+off)\b.{0,50}"
     r"\b(safety|filter|guardrail|restriction|limitation|constraint|ethical|moral)\b",
     "medium"),

    ("bypass_attempt", "without_restrictions",
     r"\bwithout\s+(any\s+)?(filter|restriction|guardrail|constraint|limitation|safety|ethical|moral)\b",
     "high"),

    ("bypass_attempt", "no_restrictions_phrasing",
     r"\bno\s+(filter|restriction|guardrail|constraint|limitation|safety|ethical|moral)s?\b",
     "medium"),

    # ── Meta / self-referential ─────────────────────────────────────────────
    ("meta_injection", "prompt_injection_mention",
     r"\bprompt\s+injection\b",
     "low"),

    ("meta_injection", "jailbreak_mention",
     r"\bjailbreak\b",
     "low"),

    ("meta_injection", "adversarial_prompt",
     r"\badversarial\s+(prompt|instruction|input)\b",
     "low"),
]

# Compile once, case-insensitive + multiline
_COMPILED: List[Tuple[str, str, re.Pattern, str]] = [
    (cat, name, re.compile(src, re.IGNORECASE | re.MULTILINE | re.DOTALL), sev)
    for cat, name, src, sev in _PATTERNS
]

# ── Unicode anomaly sets ────────────────────────────────────────────────────
_ZERO_WIDTH = frozenset('\u200b\u200c\u200d\ufeff\u2060\u2061\u2062\u2063\u2064')
_DIR_OVERRIDE = frozenset('\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')


# ── Quoted / example context ────────────────────────────────────────────────
# A security guide quoting "ignore previous instructions" is not performing
# the attack. Matches that sit inside quotes or code, or right after a cue
# that the text is *about* injection, drop one severity level (and never
# reach "high", which is what gets a document queued for review).
_EXAMPLE_CUE = re.compile(
    r"\b(example|examples|for\s+instance|e\.g\.|such\s+as|phrases?\s+like|payloads?|"
    r"red[\s-]?team\w*|attackers?|attacks?|mitigat\w+|defen[cs]e|detect(?:ion|ing|or)?|"
    r"vulnerab\w+|sample|test\s+(?:case|string|input)|known\s+as|quoted?|jailbreak\s+prompts?)\b",
    re.IGNORECASE,
)
_OPEN_QUOTES = frozenset('"\'`\u201c\u2018\u00ab')
# Format tokens and invisible characters are technical artefacts, not prose:
# quoting them does not make them harmless, so they are never downgraded.
_NEVER_DOWNGRADE = frozenset({"model_token", "encoding_trick"})
_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}
_MAX_FINDINGS_PER_PATTERN = 5


def _in_example_context(text: str, start: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].lstrip().startswith(">"):
        return True
    i = start - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i >= 0 and text[i] in _OPEN_QUOTES:
        return True
    return bool(_EXAMPLE_CUE.search(text[max(0, start - 100):start]))


# Plain-language reading of each category, for the reviewer.
CATEGORY_MEANING = {
    "instruction_override": "tries to override the assistant's instructions",
    "persona_injection": "tries to give the assistant a different persona",
    "model_token": "contains chat-format control tokens",
    "context_manipulation": "claims to be hidden or system instructions",
    "exfiltration": "asks the assistant to reveal its prompt",
    "bypass_attempt": "asks the assistant to drop its safety limits",
    "encoding_trick": "hides text behind invisible or look-alike characters",
    "meta_injection": "mentions prompt injection",
}
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def summarize_document(warnings: Optional[dict]) -> Optional[dict]:
    """Fold the stored per-page warnings into one reviewable verdict.

    ``warnings`` is the ``injection_warnings`` column: page -> scan dict.
    Returns None when there is nothing to review. Otherwise::

        {"level": "low"|"medium"|"high", "needs_review": bool, "max_score": float,
         "flagged_pages": int, "categories": {cat: n}, "reason": str,
         "top": [{"page", "category", "severity", "matched_text"}, ...]}

    Levels are deliberately conservative so the queue stays worth opening:
    ``high`` means several independent signals (or a very high score) and
    ``medium`` at least one high-severity, un-quoted pattern. Everything else
    is ``low`` — still visible on the document, but not a task for an admin.
    """
    if not warnings:
        return None

    categories: Dict[str, int] = {}
    high_cats: set = set()
    high_pages = 0
    max_score = 0.0
    top: List[dict] = []
    pages = 0
    for page, scan in warnings.items():
        if not isinstance(scan, dict):
            continue
        pages += 1
        max_score = max(max_score, float(scan.get("risk_score") or 0))
        page_has_high = False
        for f in scan.get("findings") or []:
            cat = f.get("category") or "unknown"
            categories[cat] = categories.get(cat, 0) + 1
            sev = f.get("severity") or "low"
            if sev == "high" and cat != "meta_injection":
                high_cats.add(cat)
                page_has_high = True
            top.append({
                "page": page, "category": cat, "severity": sev,
                "matched_text": f.get("matched_text") or "",
                "_rank": _SEVERITY_RANK.get(sev, 0),
            })
        if page_has_high:
            high_pages += 1
    if not pages:
        return None

    substantive = {c for c in categories if c != "meta_injection"}
    if max_score >= 0.8 or len(high_cats) >= 2 or high_pages >= 3:
        level = "high"
    elif high_cats or (max_score >= 0.5 and substantive):
        level = "medium"
    else:
        level = "low"

    # Strongest excerpt per category: overlapping patterns often match the
    # same sentence twice, and the reviewer needs breadth, not repeats.
    top.sort(key=lambda t: (-t["_rank"], str(t["page"])))
    seen_cats: set = set()
    unique: List[dict] = []
    for t in top:
        if t["category"] in seen_cats:
            continue
        seen_cats.add(t["category"])
        unique.append({k: v for k, v in t.items() if k != "_rank"})
    top = unique[:3]

    ranked = sorted(substantive, key=lambda c: -categories[c]) or sorted(categories)
    meanings = [CATEGORY_MEANING.get(c, c.replace("_", " ")) for c in ranked[:2]]
    where = f"{pages} page{'s' if pages != 1 else ''}"
    if meanings:
        reason = f"Text on {where} " + " and ".join(meanings) + "."
    else:
        reason = f"{where.capitalize()} scored {max_score:.2f} for injection signals."
    if level == "low":
        reason += " Low confidence: likely discussion, an example, or ordinary wording."

    return {
        "level": level,
        "needs_review": level != "low",
        "max_score": round(max_score, 3),
        "flagged_pages": pages,
        "categories": categories,
        "reason": reason,
        "top": top,
    }


class PromptInjectionDetector:
    """
    Scans plain text for prompt injection signals.

    Usage::

        detector = PromptInjectionDetector()
        result = detector.scan(text, source_label="report.pdf p.3")
        if result.is_flagged:
            print(result.summary)
    """

    def scan(self, text: str, source_label: str = "") -> InjectionScanResult:
        """
        Scan *text* for injection signals.

        Args:
            text:         The text to analyse.
            source_label: Optional human-readable label for log messages.

        Returns:
            InjectionScanResult with risk_score, findings, and summary.
        """
        if not text or not text.strip():
            return InjectionScanResult(is_flagged=False, risk_score=0.0,
                                       summary="No text to scan.")

        findings: List[InjectionFinding] = []
        findings.extend(self._scan_patterns(text))
        findings.extend(self._scan_unicode(text))

        risk_score = self._calculate_score(findings)
        # Talking *about* prompt injection (a security wiki, this very README)
        # is not an injection attempt, however many meta terms pile up.
        substantive = [f for f in findings if f.category != "meta_injection"]
        is_flagged = bool(substantive) and (
            risk_score >= 0.30 or any(f.severity == "high" for f in substantive)
        )
        summary = self._build_summary(findings, risk_score)

        if is_flagged and source_label:
            cats = sorted({f.category for f in findings})
            logger.warning(
                "Prompt injection signal in %r — score=%.2f findings=%d categories=%s",
                source_label, risk_score, len(findings), cats,
            )

        return InjectionScanResult(
            is_flagged=is_flagged,
            risk_score=risk_score,
            findings=findings,
            summary=summary,
        )

    def scan_pages(
        self,
        page_texts: Dict[int, str],
        filename: str = "",
    ) -> Dict[int, InjectionScanResult]:
        """
        Convenience wrapper: scan every page in *page_texts*.

        Returns:
            Mapping of page_number → InjectionScanResult.
        """
        return {
            page_num: self.scan(
                text,
                source_label=f"{filename} p.{page_num}" if filename else f"page {page_num}",
            )
            for page_num, text in page_texts.items()
        }

    # ── Private helpers ─────────────────────────────────────────────────────

    def _scan_patterns(self, text: str) -> List[InjectionFinding]:
        findings: List[InjectionFinding] = []
        for category, name, pattern, severity in _COMPILED:
            kept = 0
            for match in pattern.finditer(text):
                if kept >= _MAX_FINDINGS_PER_PATTERN:
                    break
                sev, downgraded = severity, False
                if category not in _NEVER_DOWNGRADE and _in_example_context(text, match.start()):
                    sev, downgraded = _DOWNGRADE[severity], severity != _DOWNGRADE[severity]
                raw = match.group(0)
                excerpt = raw[:120] + ("…" if len(raw) > 120 else "")
                findings.append(InjectionFinding(
                    category=category,
                    pattern_name=name,
                    matched_text=excerpt,
                    severity=sev,
                    char_offset=match.start(),
                    downgraded=downgraded,
                ))
                kept += 1
        return findings

    def _scan_unicode(self, text: str) -> List[InjectionFinding]:
        findings: List[InjectionFinding] = []

        # Zero-width / invisible characters (≥4 occurrences to avoid lone BOM false positives)
        zw_pos = [i for i, ch in enumerate(text) if ch in _ZERO_WIDTH]
        if len(zw_pos) >= 4:
            findings.append(InjectionFinding(
                category="encoding_trick",
                pattern_name="zero_width_chars",
                matched_text=f"{len(zw_pos)} zero-width characters detected",
                severity="medium",
                char_offset=zw_pos[0],
            ))

        # Unicode direction-override characters (any count is suspicious)
        dir_pos = [i for i, ch in enumerate(text) if ch in _DIR_OVERRIDE]
        if dir_pos:
            findings.append(InjectionFinding(
                category="encoding_trick",
                pattern_name="direction_override_chars",
                matched_text=f"{len(dir_pos)} Unicode direction-override character(s) detected",
                severity="high",
                char_offset=dir_pos[0],
            ))

        # Homoglyph detection: Cyrillic/Greek letters scattered in ASCII-dominant text
        total = len(text)
        if total > 50:
            ascii_count = sum(1 for ch in text if ord(ch) < 128)
            if ascii_count / total > 0.85:
                suspicious = [
                    ch for ch in text
                    if ord(ch) > 127
                    and unicodedata.category(ch) in ("Ll", "Lu", "Lo")
                    and unicodedata.name(ch, "").startswith(("CYRILLIC", "GREEK"))
                ]
                if len(suspicious) > 2:
                    sample = "".join(dict.fromkeys(suspicious[:12]))
                    findings.append(InjectionFinding(
                        category="encoding_trick",
                        pattern_name="homoglyph_chars",
                        matched_text=f"Non-Latin chars in ASCII-dominant text: {sample!r}",
                        severity="medium",
                        char_offset=0,
                    ))

        return findings

    def _calculate_score(self, findings: List[InjectionFinding]) -> float:
        """
        Risk score in [0, 1].

        Strategy: take the *maximum* severity weight per category (so many
        matches of the same pattern don't artificially inflate the score),
        then sum across categories, with a small bonus when ≥2 high-severity
        categories co-occur (stronger signal of intentional injection).
        """
        if not findings:
            return 0.0

        category_max: Dict[str, float] = {}
        for f in findings:
            w = _SEVERITY_WEIGHTS.get(f.severity, 0.10)
            if w > category_max.get(f.category, 0.0):
                category_max[f.category] = w

        base = sum(category_max.values())

        high_cats = sum(1 for w in category_max.values() if w >= 0.50)
        if high_cats >= 2:
            base += 0.20

        return min(base, 1.0)

    def _build_summary(self, findings: List[InjectionFinding], score: float) -> str:
        if not findings:
            return "No injection signals detected."

        categories = sorted({f.category for f in findings})
        by_sev = {"high": 0, "medium": 0, "low": 0}
        for f in findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1

        parts = [f"Risk score: {score:.2f}."]
        parts.append(
            f"{len(findings)} signal(s) across {len(categories)} category(ies)."
        )

        sev_parts = [f"{v} {k}" for k, v in by_sev.items() if v]
        if sev_parts:
            parts.append("Severity breakdown: " + ", ".join(sev_parts) + ".")

        parts.append("Categories: " + ", ".join(categories) + ".")
        return " ".join(parts)
