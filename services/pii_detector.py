"""Personal data (PII) in document text: find it, summarise it, mask it.

Two engines behind one interface:

* **Presidio** (`presidio-analyzer`, requirements-pii.txt) when it is
  installed, with the largest English spaCy model on the machine. Context-
  aware, many entity types (names, addresses, national ids, bank numbers).
* **Built-in regexes** otherwise: email addresses, phone numbers, Luhn-valid
  card numbers, IBANs, US social-security numbers, IP addresses. Enough to
  notice a customer export or an HR spreadsheet.

Either way nothing leaves the machine, and nothing stored is the personal
data itself: a scan result carries counts per entity type and a few *masked*
previews (``j***@a***.com``), never the values.

The scan rides on the content-policy pipeline (``services/content_policy.py``)
as the ``pii`` category, so the finding lands in ``policy_flags`` with the
usual badge, review-queue entry, approve / remove actions and audit trail.
``PII_SCAN_ACTION`` says what a dense page does: ``label`` (record it and
default the document to confidential), ``flag`` (also queue it),
``quarantine`` (hide it until approved) or ``off``.

``redact()`` masks personal data in text that leaves retrieval (search
results, chat context, MCP excerpts) when ``PII_REDACT_OUTPUTS`` is on.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_ENTITIES = (
    "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN",
    "US_PASSPORT", "US_DRIVER_LICENSE", "US_BANK_NUMBER", "UK_NHS", "IP_ADDRESS",
)
# What the regex engine can do without Presidio.
REGEX_ENTITIES = ("EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN", "IP_ADDRESS")

# Plain words for the review UI and the policy reason line.
ENTITY_LABELS = {
    "EMAIL_ADDRESS": "email addresses",
    "PHONE_NUMBER": "phone numbers",
    "CREDIT_CARD": "card numbers",
    "IBAN_CODE": "IBANs",
    "US_SSN": "social-security numbers",
    "US_PASSPORT": "passport numbers",
    "US_DRIVER_LICENSE": "driver-licence numbers",
    "US_BANK_NUMBER": "bank account numbers",
    "UK_NHS": "NHS numbers",
    "IP_ADDRESS": "IP addresses",
    "PERSON": "names",
    "LOCATION": "addresses or places",
    "DATE_TIME": "dates",
    "NRP": "nationalities or groups",
    "MEDICAL_LICENSE": "medical licence numbers",
    "URL": "URLs",
    "CRYPTO": "crypto wallet addresses",
}

# Below this Presidio confidence a hit is ignored (its defaults range 0.01–1.0).
MIN_SCORE = 0.4
MAX_PREVIEWS = 6
MAX_SPAN_CHARS = 120_000  # a page larger than this is scanned in slices


@dataclass
class PIISpan:
    entity_type: str
    start: int
    end: int
    score: float


@dataclass
class PIIScanResult:
    entities: Dict[str, int] = field(default_factory=dict)   # type -> count
    total: int = 0
    dense: bool = False                                       # reached the threshold
    previews: List[Dict[str, Any]] = field(default_factory=list)  # [{type, preview, score}]
    first_offset: int = 0
    engine: str = "regex"

    def describe(self) -> str:
        """``"12 email addresses, 8 phone numbers"`` — the top three types."""
        parts = [f"{n} {ENTITY_LABELS.get(t, t.lower().replace('_', ' '))}"
                 for t, n in sorted(self.entities.items(), key=lambda kv: -kv[1])[:3]]
        return ", ".join(parts)


# ── Engines ─────────────────────────────────────────────────────────────────

def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class _RegexEngine:
    """Six entity types, no models, no dependencies."""

    name = "regex"
    model = None

    _PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
        ("EMAIL_ADDRESS", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
        # Separators are required so part numbers and invoice ids do not count.
        ("PHONE_NUMBER", re.compile(
            r"(?<![\w.-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w-])")),
        ("US_SSN", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
        ("IBAN_CODE", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")),
        ("IP_ADDRESS", re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    )
    _CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

    def analyze(self, text: str, entities: Sequence[str]) -> List[PIISpan]:
        wanted = set(entities)
        spans: List[PIISpan] = []
        for etype, pattern in self._PATTERNS:
            if etype not in wanted:
                continue
            for m in pattern.finditer(text):
                spans.append(PIISpan(etype, m.start(), m.end(), 0.6))
        if "CREDIT_CARD" in wanted:
            for m in self._CARD.finditer(text):
                digits = re.sub(r"[ -]", "", m.group(0))
                if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                    spans.append(PIISpan("CREDIT_CARD", m.start(), m.end(), 0.8))
        return spans


class _PresidioEngine:
    name = "presidio"

    def __init__(self):
        from presidio_analyzer import AnalyzerEngine  # type: ignore
        from presidio_analyzer.nlp_engine import NlpEngineProvider  # type: ignore

        self.model = next(
            (m for m in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm")
             if importlib.util.find_spec(m) is not None),
            None,
        )
        if self.model is None:
            raise RuntimeError("Presidio is installed but no English spaCy model is "
                               "(python -m spacy download en_core_web_lg)")
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": self.model}],
        })
        self._analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        self.supported = set(self._analyzer.get_supported_entities(language="en"))

    def analyze(self, text: str, entities: Sequence[str]) -> List[PIISpan]:
        wanted = [e for e in entities if e in self.supported] or None
        spans: List[PIISpan] = []
        for offset in range(0, len(text), MAX_SPAN_CHARS):
            slice_ = text[offset:offset + MAX_SPAN_CHARS]
            for r in self._analyzer.analyze(text=slice_, language="en", entities=wanted,
                                            score_threshold=MIN_SCORE):
                spans.append(PIISpan(r.entity_type, r.start + offset, r.end + offset, float(r.score)))
        return spans


_engine_lock = threading.Lock()
_engine: Any = None
_engine_error: Optional[str] = None


def presidio_installed() -> bool:
    return importlib.util.find_spec("presidio_analyzer") is not None


def _get_engine():
    """Presidio when importable and loadable, else the regex engine. Loaded once."""
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if presidio_installed():
            try:
                _engine = _PresidioEngine()
                logger.info(f"PII detection: Presidio with spaCy model {_engine.model}")
                return _engine
            except Exception as e:  # noqa: BLE001 - fall back, say why
                _engine_error = str(e)[:300]
                logger.warning(f"Presidio unavailable, using the built-in PII regexes: {e}")
        _engine = _RegexEngine()
        return _engine


def reset_engine_for_tests() -> None:
    global _engine, _engine_error
    with _engine_lock:
        _engine = None
        _engine_error = None


# ── Settings ────────────────────────────────────────────────────────────────

def action() -> str:
    from config import settings

    a = getattr(settings, "pii_scan_action", "label")
    return a if a in ("off", "label", "flag", "quarantine") else "label"


def scanning_enabled() -> bool:
    return action() != "off"


def queues_for_review() -> bool:
    """Whether a dense page is a content-policy finding (flag / quarantine)."""
    return action() in ("flag", "quarantine")


def threshold() -> int:
    from config import settings

    try:
        return max(1, int(getattr(settings, "pii_flag_threshold", 10)))
    except (TypeError, ValueError):
        return 10


def entities() -> List[str]:
    from config import settings

    raw = (getattr(settings, "pii_entities", "") or "").strip()
    if not raw:
        return list(DEFAULT_ENTITIES)
    return [e.strip().upper() for e in raw.split(",") if e.strip()]


def redaction_enabled() -> bool:
    from config import settings

    return bool(getattr(settings, "pii_redact_outputs", False))


def engine_status() -> Dict[str, Any]:
    """For the admin console: which engine is answering and with what."""
    installed = presidio_installed()
    eng = _engine
    if eng is None and installed:
        # Report what would load without paying for the load on a status call.
        return {"engine": "presidio", "loaded": False, "installed": True, "model": None,
                "entities": entities(), "error": _engine_error,
                "regex_only": [e for e in entities() if e not in REGEX_ENTITIES]}
    name = getattr(eng, "name", "regex")
    return {
        "engine": name,
        "loaded": eng is not None,
        "installed": installed,
        "model": getattr(eng, "model", None),
        "entities": entities(),
        "error": _engine_error,
        # Entities the regex engine cannot see: the UI says so.
        "regex_only": [] if name == "presidio" else [e for e in entities() if e not in REGEX_ENTITIES],
    }


# ── Scan and redact ─────────────────────────────────────────────────────────

def mask(value: str) -> str:
    """``jane.doe@acme.com`` → ``ja*************om``. Never shows the middle."""
    v = (value or "").strip()
    if len(v) <= 4:
        return "*" * max(4, len(v))
    if "@" in v:
        local, _, domain = v.partition("@")
        return f"{local[:1]}{'*' * max(2, len(local) - 1)}@{domain[:1]}{'*' * max(2, len(domain) - 4)}{domain[-3:]}"
    return f"{v[:2]}{'*' * (len(v) - 4)}{v[-2:]}"


def _dedupe(spans: List[PIISpan]) -> List[PIISpan]:
    """Overlapping hits (Presidio reports several recognisers): keep the best."""
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start), -s.score))
    kept: List[PIISpan] = []
    for s in spans:
        if kept and s.start < kept[-1].end:
            if s.score > kept[-1].score and (s.end - s.start) >= (kept[-1].end - kept[-1].start):
                kept[-1] = s
            continue
        kept.append(s)
    return kept


def analyze(text: str, wanted: Optional[Sequence[str]] = None) -> List[PIISpan]:
    """Deduplicated spans of personal data in *text*. Never raises."""
    if not text:
        return []
    try:
        return _dedupe(_get_engine().analyze(text, list(wanted or entities())))
    except Exception as e:  # noqa: BLE001 - a tripwire must not break ingest
        logger.warning(f"PII analysis failed: {e}")
        return []


def scan(text: str, *, dense_threshold: Optional[int] = None) -> PIIScanResult:
    """Count personal data on one page; ``dense`` when it reaches the threshold."""
    result = PIIScanResult(engine=getattr(_get_engine(), "name", "regex"))
    if not text or not text.strip():
        return result
    spans = analyze(text)
    if not spans:
        return result
    for s in spans:
        result.entities[s.entity_type] = result.entities.get(s.entity_type, 0) + 1
    result.total = len(spans)
    result.first_offset = spans[0].start
    result.dense = result.total >= (dense_threshold if dense_threshold is not None else threshold())
    seen_types: Dict[str, int] = {}
    for s in spans:
        if len(result.previews) >= MAX_PREVIEWS:
            break
        if seen_types.get(s.entity_type, 0) >= 2:
            continue
        seen_types[s.entity_type] = seen_types.get(s.entity_type, 0) + 1
        result.previews.append({
            "type": s.entity_type,
            "preview": mask(text[s.start:s.end]),
            "score": round(s.score, 2),
        })
    return result


def redact(text: Optional[str], wanted: Optional[Sequence[str]] = None) -> str:
    """Replace every personal-data span with ``<ENTITY_TYPE>``."""
    if not text:
        return text or ""
    spans = analyze(text, wanted)
    if not spans:
        return text
    out: List[str] = []
    pos = 0
    for s in spans:
        if s.start < pos:
            continue
        out.append(text[pos:s.start])
        out.append(f"<{s.entity_type}>")
        pos = s.end
    out.append(text[pos:])
    return "".join(out)


def redact_if_enabled(text: Optional[str]) -> Optional[str]:
    """``redact()`` under PII_REDACT_OUTPUTS, else the text unchanged."""
    if text is None or not redaction_enabled():
        return text
    try:
        return redact(text)
    except Exception as e:  # noqa: BLE001 - never fail a search over a mask
        logger.warning(f"PII redaction failed: {e}")
        return text


def redact_results(results: list) -> list:
    """Mask ``text_snippet`` (and CSV values) on retrieval result objects in place."""
    if not results or not redaction_enabled():
        return results
    for r in results:
        snippet = getattr(r, "text_snippet", None)
        if snippet:
            r.text_snippet = redact_if_enabled(snippet)
        values = getattr(r, "csv_values", None)
        if isinstance(values, dict):
            r.csv_values = {k: (redact_if_enabled(v) if isinstance(v, str) else v) for k, v in values.items()}
    return results
