"""Ingest-time content-policy screening.

Runs beside the prompt-injection scan and has the same shape: every page
of extracted text is scanned by local regex rule packs, the hits are
scored, and the result is stored on the document row. What differs is
what happens next. Injection warnings are advisory. Policy findings feed a
decision — clear, flagged, quarantined, or rejected — controlled by
``CONTENT_POLICY_ACTION`` and enforced at the same choke points that
enforce collection access (search, chat retrieval, MCP tools, document
serving).

Design constraints, in order:

1. Nothing leaves the machine. The rule packs are regexes; the optional
   LLM second opinion is off by default and, when on, uses a provider the
   deployment already trusts for chat.
2. Flag and review, don't silently block. Most categories are contextual —
   a threat-intelligence team indexing extremist manifestos, a legal team
   indexing evidence, an HR team indexing a harassment complaint — so the
   scanner surfaces, an admin decides. Only ``reject`` mode refuses at the
   door, and only two categories (``csae`` material indicators, attack
   planning / incitement) are treated as critical and quarantined even in
   ``flag`` mode.
3. Be honest about coverage. This detects text signals. It cannot detect
   child sexual abuse imagery (that needs licensed hash databases which are
   not available for self-hosting), and it will miss anything phrased to
   evade keywords. Attribution, the audit trail and fast admin removal are
   the real controls; this is the tripwire.

Two secondary outputs ride along: ``suggested_sensitivity`` (a document
that contains private keys, a credential dump or dense personal data should
default to "confidential" regardless of policy status) and a per-category
hit count the admin review queue displays.

Personal data is the ``pii`` category (``services/pii_detector.py``:
Presidio when installed, built-in regexes otherwise). It has its own knob,
``PII_SCAN_ACTION``: ``label`` records it and suggests "confidential" without
flagging, ``flag`` and ``quarantine`` make a dense page a policy finding
with that status even when ``CONTENT_POLICY_ACTION`` is milder.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services import pii_detector

logger = logging.getLogger(__name__)


class BlockedContentError(ValueError):
    """The file's hash is on the admin blocklist."""


class ContentRejectedError(ValueError):
    """CONTENT_POLICY_ACTION=reject and the scan flagged the document."""


POLICY_STATUSES = ("clear", "flagged", "quarantined", "approved", "rejected")
HIDDEN_STATUSES = ("quarantined",)

# Categories that escalate straight to quarantine even in "flag" mode.
CRITICAL_CATEGORIES = frozenset({"csae", "violent_extremism"})

# Categories that only describe *sensitivity*: they never change policy
# status on their own, they suggest a label. A credential dump is both —
# it suggests "confidential" AND is unacceptable content.
SENSITIVITY_ONLY_CATEGORIES = frozenset({"secrets"})
SENSITIVITY_HINT_CATEGORIES = frozenset({"secrets", "credential_dump", "pii"})
PII_CATEGORY = "pii"

_SEVERITY_WEIGHTS = {"low": 0.15, "medium": 0.35, "high": 0.60, "critical": 1.0}


@dataclass
class PolicyFinding:
    category: str
    pattern_name: str
    matched_text: str
    severity: str        # low | medium | high | critical
    char_offset: int


@dataclass
class PolicyScanResult:
    is_flagged: bool
    risk_score: float
    findings: List[PolicyFinding] = field(default_factory=list)
    categories: Dict[str, int] = field(default_factory=dict)
    has_critical: bool = False
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "is_flagged": self.is_flagged,
            "risk_score": round(self.risk_score, 3),
            "has_critical": self.has_critical,
            "categories": dict(self.categories),
            "finding_count": len(self.findings),
            "findings": [
                {
                    "category": f.category,
                    "pattern_name": f.pattern_name,
                    "matched_text": f.matched_text,
                    "severity": f.severity,
                    "char_offset": f.char_offset,
                }
                for f in self.findings[:40]
            ],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Rule packs: (category, name, regex, severity, min_hits)
#
# min_hits is the number of matches of THIS pattern a page needs before it
# counts. It is the main false-positive control: one explicit word in a
# novel is nothing; a page with eight is a different document.
# ---------------------------------------------------------------------------

_MINOR = r"(?:child(?:ren)?|kids?|minors?|underage|under-age|preteens?|pre-teens?|toddlers?|infants?|young\s+(?:boys?|girls?)|little\s+(?:boys?|girls?)|schoolgirls?|schoolboys?)"
_SEXUAL = r"(?:porn\w*|sex(?:ual|ually)?|nude|naked|erotic\w*|molest\w*|rape[ds]?|raping|fuck\w*|orgasm\w*|masturbat\w*)"
_DISTRIBUTE = r"(?:trade|trading|swap|share|sharing|download|links?|collection|vids?|videos?|pics?|photos?|images?|set|album|mega|telegram|tor|onion|cp)"

_PROTECTED_GROUPS = r"(?:jews?|jewish|muslims?|christians?|blacks?|whites?|asians?|arabs?|hispanics?|latinos?|gays?|lesbians?|trans(?:gender)?\s*(?:people|men|women)?|immigrants?|refugees?|infidels?|kafirs?)"
_EXPLOSIVE = r"(?:pipe\s+bomb|nail\s+bomb|car\s+bomb|ied|improvised\s+explosive|explosive\s+device|thermite\s+charge|pressure\s+cooker\s+bomb|detonator)"
_SELL = r"(?:buy|buying|sell|selling|for\s+sale|order|ordering|ship|shipping|vendor|supplier|wholesale|price\s+list|menu)"
_DRUGS = r"(?:cocaine|coke|heroin|fentanyl|fent|meth(?:amphetamine)?|crystal|mdma|molly|ecstasy|lsd|acid\s+tabs|ketamine|xanax|alprazolam|oxycontin|oxycodone|oxy|percocet|adderall|carfentanil|dmt)"
_EXPLICIT_TERMS = r"(?:cock|dick|pussy|cum(?:ming|shot)?|blowjob|handjob|anal\s+sex|gangbang|creampie|deepthroat|hardcore\s+porn|xxx|porn(?:o|star)?|nude[sz]?|tits)"

_RULES: List[Tuple[str, str, str, str, int]] = [
    # ── Child sexual abuse material indicators ─────────────────────────
    # "material" = minor term + sexual term + distribution/solicitation term
    # in close proximity. Discussion of the topic (a safeguarding policy, a
    # news report) trips only the mention rule below.
    ("csae", "csam_solicitation",
     rf"\b{_MINOR}\b.{{0,60}}\b{_SEXUAL}\b.{{0,60}}\b{_DISTRIBUTE}\b"
     rf"|\b{_SEXUAL}\b.{{0,60}}\b{_MINOR}\b.{{0,60}}\b{_DISTRIBUTE}\b",
     "critical", 1),
    ("csae", "csam_slang",
     r"\b(?:loli(?:con)?|shota(?:con)?|pedo(?:phile|philia)?s?|paedo(?:phile|philia)?s?|p3do|pthc|hurtcore|cheese\s+pizza)\b",
     "critical", 1),
    ("csae", "csae_mention",
     rf"\b{_MINOR}\b.{{0,40}}\b(?:sexual\s+abuse|exploitation|grooming|pornograph\w*)\b",
     "medium", 1),

    # ── Violent extremism / attack planning ────────────────────────────
    ("violent_extremism", "genocidal_incitement",
     rf"\b(?:kill|murder|slaughter|exterminate|gas|hang|lynch|shoot|wipe\s+out)\s+(?:all|every|the)\s+(?:the\s+)?{_PROTECTED_GROUPS}\b",
     "critical", 1),
    ("violent_extremism", "explosive_instructions",
     rf"\b(?:how\s+to|instructions?\s+(?:for|on|to)|recipe\s+for|steps?\s+to|guide\s+to)\s+(?:build|make|assemble|construct|wire)\s+(?:an?\s+|your\s+own\s+)?{_EXPLOSIVE}\b",
     "critical", 1),
    ("violent_extremism", "attack_planning",
     r"\b(?:plan(?:ning)?|target(?:ing)?|scout(?:ing)?)\s+(?:the\s+|a\s+)?(?:mass\s+shooting|massacre|school\s+shooting|bombing|attack\s+on\s+(?:the\s+)?(?:crowd|mosque|synagogue|church|school|parade|rally))\b",
     "critical", 1),
    ("violent_extremism", "terror_affiliation",
     r"\b(?:join|joining|support(?:ing)?|pledge(?:d)?\s+(?:allegiance|bay'?ah)\s+to|fight(?:ing)?\s+for)\s+(?:the\s+)?(?:isis|isil|daesh|islamic\s+state|al[-\s]?qa[ie]da|the\s+caliphate|atomwaffen|the\s+base|boogaloo)\b",
     "high", 1),
    ("violent_extremism", "extremist_slogans",
     r"\b(?:race\s+war|white\s+genocide|great\s+replacement|day\s+of\s+the\s+rope|total\s+(?:war|extermination)|accelerationis[mt]|siege\s+culture|(?<![\d.-]\s)(?<![\d.-])1488(?!\s?[\d.-])|14\s+words)\b",
     "medium", 2),

    # ── Weapons trafficking ────────────────────────────────────────────
    ("weapons_trafficking", "illegal_modification",
     r"\b(?:auto\s+sear|full[-\s]?auto\s+(?:conversion|switch|kit)|glock\s+switch|drop[-\s]in\s+auto|3d[-\s]?printed\s+(?:gun|receiver|lower|suppressor|silencer)|ghost\s+guns?|untraceable\s+(?:firearms?|guns?|pistols?|rifles?)|solvent\s+trap\s+(?:kit|suppressor))\b",
     "high", 1),
    ("weapons_trafficking", "no_paperwork_sale",
     rf"\b{_SELL}\b.{{0,60}}\b(?:no\s+(?:ffl|background\s+check|paperwork|serial|questions\s+asked)|off\s+the\s+books|cash\s+only.{{0,30}}(?:gun|pistol|rifle|firearm))\b",
     "high", 1),

    # ── Drug trade ─────────────────────────────────────────────────────
    ("drug_trade", "drug_sale",
     rf"\b{_SELL}\b.{{0,40}}\b{_DRUGS}\b|\b{_DRUGS}\b.{{0,40}}\b(?:for\s+sale|price\s+(?:list|per)|per\s+(?:gram|g|oz|ounce|kilo|kg|pill|tab)|\$\d)",
     "high", 1),
    ("drug_trade", "darknet_market",
     r"\b(?:darknet|dark\s+web|dnm)\s+(?:market(?:place)?|vendor|shop|listing)s?\b|\b(?:stealth\s+shipping|decoy\s+packaging|reship(?:ping)?\s+service|escrow\s+(?:in\s+)?(?:monero|xmr|btc|bitcoin))\b",
     "medium", 1),

    # ── Explicit sexual content (adult) ────────────────────────────────
    # Needs several hits on a page — one word in a novel is not
    # pornography. Once over the threshold it flags (high); whether an
    # adult-content corpus is acceptable is the deployment's policy call,
    # which is exactly what the review queue is for.
    ("sexual_explicit", "explicit_terms",
     rf"\b{_EXPLICIT_TERMS}\b",
     "high", 4),

    # ── Credential dumps (sensitivity + acceptability) ─────────────────
    ("credential_dump", "email_password_pairs",
     r"[\w.+-]+@[\w-]+\.[\w.-]+\s*[:;|,\t]\s*[^\s:;|,]{6,}",
     "high", 8),
    ("credential_dump", "ssn_bulk",
     r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
     "high", 8),
    ("credential_dump", "card_dump_terms",
     r"\b(?:combo\s?lists?|fullz|cvv\s+dumps?|card\s+dumps?|carding\s+(?:forum|tutorial|method)|dumps?\s+\+\s+pin|track\s*[12]\s+data)\b",
     "high", 1),

    # ── Secrets (sensitivity only) ─────────────────────────────────────
    ("secrets", "private_key_block",
     r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
     "high", 1),
    ("secrets", "cloud_access_key",
     r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{35}\b|\bghp_[A-Za-z0-9]{36}\b|\bgh[ousr]_[A-Za-z0-9]{36}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}\b|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b|\bsk_live_[A-Za-z0-9]{16,}\b",
     "high", 1),
    ("secrets", "assigned_secret",
     r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|auth[_-]?token|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{16,}['\"]?",
     "medium", 2),
]

_COMPILED: List[Tuple[str, str, re.Pattern, str, int]] = [
    (cat, name, re.compile(src, re.IGNORECASE | re.MULTILINE | re.DOTALL), sev, min_hits)
    for cat, name, src, sev, min_hits in _RULES
]

# Rule packs that make sense over source code. Everything else is noise on
# code (variable names, test fixtures, string tables), so code files only
# get the sensitivity packs.
_CODE_CATEGORIES = frozenset({"secrets", "credential_dump"})

# Card numbers are validated with Luhn so invoice numbers and part codes
# don't count. Counted separately from the regex table above.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


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


class ContentPolicyScanner:
    """Scans plain text for content-policy signals. Stateless; reuse freely."""

    def scan(self, text: str, source_label: str = "", code: bool = False) -> PolicyScanResult:
        if not text or not text.strip():
            return PolicyScanResult(is_flagged=False, risk_score=0.0, summary="No text to scan.")

        findings: List[PolicyFinding] = []
        categories: Dict[str, int] = {}
        rules_on = rule_packs_enabled()

        for category, name, pattern, severity, min_hits in _COMPILED:
            if not rules_on:
                break
            if code and category not in _CODE_CATEGORIES:
                continue
            matches = list(pattern.finditer(text))
            if len(matches) < min_hits:
                continue
            categories[category] = categories.get(category, 0) + len(matches)
            # Keep a few excerpts per pattern, not every hit: the admin
            # needs to see what tripped it, not read the page twice.
            for match in matches[:3]:
                raw = match.group(0)
                excerpt = raw[:120] + ("…" if len(raw) > 120 else "")
                findings.append(PolicyFinding(
                    category=category,
                    pattern_name=name,
                    matched_text=excerpt,
                    severity=severity,
                    char_offset=match.start(),
                ))

        # Card numbers: Luhn-valid runs, 5+ per page → a dump, not an invoice.
        cards = [m for m in _CARD_RE.finditer(text)
                 if _luhn_ok(re.sub(r"[ -]", "", m.group(0)))] if rules_on else []
        if len(cards) >= 5:
            categories["credential_dump"] = categories.get("credential_dump", 0) + len(cards)
            findings.append(PolicyFinding(
                category="credential_dump",
                pattern_name="card_number_bulk",
                matched_text=f"{len(cards)} Luhn-valid card numbers on one page",
                severity="high",
                char_offset=cards[0].start(),
            ))

        # Personal data: a *dense* page (PII_FLAG_THRESHOLD entities) is a
        # finding; a letterhead with one phone number is not recorded at all.
        # Code only gets the sensitivity packs, and identifiers in code are
        # not people, so it is skipped there.
        pii_dense = False
        if not code and pii_detector.scanning_enabled():
            try:
                pii = pii_detector.scan(text)
            except Exception as e:  # noqa: BLE001 - never let the tripwire break ingest
                logger.warning(f"PII scan failed for {source_label or 'page'}: {e}")
                pii = None
            if pii is not None and pii.dense:
                pii_dense = True
                categories[PII_CATEGORY] = pii.total
                previews = ", ".join(p["preview"] for p in pii.previews[:3])
                findings.append(PolicyFinding(
                    category=PII_CATEGORY,
                    pattern_name=f"personal_data:{pii.engine}",
                    matched_text=pii.describe() + (f" ({previews})" if previews else ""),
                    severity="high" if pii_detector.queues_for_review() else "medium",
                    char_offset=pii.first_offset,
                ))

        risk_score = self._score(findings)
        has_critical = any(f.severity == "critical" for f in findings)
        # Sensitivity-only categories never flag a document on their own; the
        # pii category joins them unless PII_SCAN_ACTION says to flag/hold.
        sensitivity_only = set(SENSITIVITY_ONLY_CATEGORIES)
        if not pii_detector.queues_for_review():
            sensitivity_only.add(PII_CATEGORY)
        acceptability = [f for f in findings if f.category not in sensitivity_only]
        is_flagged = has_critical or any(f.severity == "high" for f in acceptability) or (
            self._score(acceptability) >= 0.5
        ) or (pii_dense and pii_detector.queues_for_review())
        summary = self._summary(findings, risk_score)

        if is_flagged and source_label:
            logger.warning(
                "Content policy signal in %r — score=%.2f categories=%s",
                source_label, risk_score, sorted(categories),
            )

        return PolicyScanResult(
            is_flagged=is_flagged,
            risk_score=risk_score,
            findings=findings,
            categories=categories,
            has_critical=has_critical,
            summary=summary,
        )

    def scan_pages(
        self,
        page_texts: Dict[int, str],
        filename: str = "",
        code: bool = False,
    ) -> Dict[int, PolicyScanResult]:
        return {
            page: self.scan(
                text,
                source_label=f"{filename} p.{page}" if filename else f"page {page}",
                code=code,
            )
            for page, text in page_texts.items()
        }

    @staticmethod
    def _score(findings: Iterable[PolicyFinding]) -> float:
        category_max: Dict[str, float] = {}
        for f in findings:
            w = _SEVERITY_WEIGHTS.get(f.severity, 0.10)
            if w > category_max.get(f.category, 0.0):
                category_max[f.category] = w
        if not category_max:
            return 0.0
        base = sum(category_max.values())
        if sum(1 for w in category_max.values() if w >= 0.5) >= 2:
            base += 0.20
        return min(base, 1.0)

    @staticmethod
    def _summary(findings: List[PolicyFinding], score: float) -> str:
        if not findings:
            return "No policy signals detected."
        cats = sorted({f.category for f in findings})
        return (
            f"Risk score: {score:.2f}. {len(findings)} signal(s) across "
            f"{len(cats)} category(ies): {', '.join(cats)}."
        )


_scanner = ContentPolicyScanner()


def scan_pages(page_texts: Dict[int, str], filename: str = "", code: bool = False) -> Dict[int, PolicyScanResult]:
    """Module-level convenience over the shared scanner instance."""
    return _scanner.scan_pages(page_texts, filename=filename, code=code)


def rule_packs_enabled() -> bool:
    from config import settings

    return settings.content_policy_action != "off"


def scanning_enabled() -> bool:
    """Whether ingest runs the policy scan at all: rule packs or the PII scan."""
    return rule_packs_enabled() or pii_detector.scanning_enabled()


# ---------------------------------------------------------------------------
# Optional LLM second opinion
# ---------------------------------------------------------------------------

_LLM_CATEGORIES = [
    "csae", "violent_extremism", "weapons_trafficking", "drug_trade",
    "sexual_explicit", "credential_dump", "harassment_doxxing", "none",
]

_LLM_PROMPT = """You are a content-policy classifier for a document search appliance.
Classify the SAMPLE below into zero or more of these categories:
- csae: sexual content involving minors, or solicitation/trading of such material
- violent_extremism: incitement to violence against a group, attack planning, or instructions for weapons/explosives intended to harm people
- weapons_trafficking: illegal sale or modification of firearms
- drug_trade: sale or distribution of controlled substances
- sexual_explicit: pornographic text (adults)
- credential_dump: bulk leaked credentials, card numbers, or identity numbers
- harassment_doxxing: targeted harassment or publishing a private person's home address, phone, or workplace to intimidate
Legitimate discussion, reporting, research, policy documents, fiction with mature themes, or security documentation about these topics is NOT a match: only content that itself does or solicits the thing counts.

Respond with JSON only: {{"categories": [..], "confidence": 0.0-1.0, "rationale": "one sentence"}}
Use an empty list when nothing applies.

SAMPLE:
{sample}
"""


def _sample_pages(page_texts: Dict[int, str], max_pages: int, max_chars: int = 2000) -> str:
    pages = sorted(page_texts)
    if not pages:
        return ""
    if len(pages) <= max_pages:
        chosen = pages
    else:
        # First two pages plus evenly spaced pages through the rest.
        step = max(1, (len(pages) - 2) // max(1, max_pages - 2))
        chosen = pages[:2] + pages[2::step][: max_pages - 2]
    parts = []
    for p in chosen:
        text = (page_texts.get(p) or "").strip()
        if text:
            parts.append(f"[page {p}]\n{text[:max_chars]}")
    return "\n\n".join(parts)


def _resolve_review_provider():
    """The (provider, model) pair for the LLM tier, or (None, None).

    Explicit CONTENT_POLICY_LLM_PROVIDER wins; otherwise the deployment's
    default chat provider (Settings → AI). Keys come from the team key
    store, with the Ollama Cloud key falling back to the app-wide one, the
    same way vision OCR resolves its key.
    """
    from config import settings

    from services.ai_service import resolve_default_provider

    return resolve_default_provider(
        provider=(settings.content_policy_llm_provider or "").strip(),
        model=(settings.content_policy_llm_model or "").strip() or None,
        purpose="Content policy LLM review",
    )


def llm_review(page_texts: Dict[int, str], filename: str = "") -> Optional[Dict[str, Any]]:
    """Ask the configured LLM for a second opinion on a page sample.

    Returns {"categories": [...], "confidence": float, "rationale": str,
    "model": str} or None when the tier is off or unavailable. Never raises.
    """
    from config import settings

    # Offline mode is honoured by create_provider (cloud providers refuse to
    # construct), so a local Ollama reviewer still works air-gapped.
    if not settings.content_policy_llm_review:
        return None
    try:
        import json

        provider, model = _resolve_review_provider()
        if provider is None:
            return None
        sample = _sample_pages(page_texts, max(1, settings.content_policy_llm_sample_pages))
        if not sample:
            return None
        prompt = _LLM_PROMPT.format(sample=sample)
        response = provider.complete(prompt, max_tokens=300, model=model)
        text = (response or {}).get("text") or ""
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return None
        parsed = json.loads(text[start:end + 1])
        cats = [c for c in parsed.get("categories", []) if c in _LLM_CATEGORIES and c != "none"]
        return {
            "categories": cats,
            "confidence": float(parsed.get("confidence", 0) or 0),
            "rationale": str(parsed.get("rationale", ""))[:300],
            "model": ((response or {}).get("usage") or {}).get("model") or model or "",
        }
    except Exception as e:
        logger.warning(f"Content policy LLM review failed for {filename or 'document'}: {e}")
        return None


# ---------------------------------------------------------------------------
# Document-level decision
# ---------------------------------------------------------------------------

def decide(
    page_results: Dict[int, PolicyScanResult],
    *,
    llm: Optional[Dict[str, Any]] = None,
    action: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """Fold per-page scans (and the optional LLM verdict) into one decision.

    Returns ``(policy_status, policy_flags, suggested_sensitivity)``:

    - policy_status: "clear" | "flagged" | "quarantined" | "rejected"
    - policy_flags: the stored JSON summary (None when clear and nothing
      of note was found)
    - suggested_sensitivity: "confidential" when secrets or credential
      material were seen, else None
    """
    from config import settings

    action = action or settings.content_policy_action
    flagged_pages = {p: r for p, r in page_results.items() if r.is_flagged}
    categories: Dict[str, int] = {}
    for r in page_results.values():
        for cat, n in r.categories.items():
            categories[cat] = categories.get(cat, 0) + n
    has_critical = any(r.has_critical for r in page_results.values())
    max_score = max((r.risk_score for r in page_results.values()), default=0.0)

    suggested = "confidential" if any(c in SENSITIVITY_HINT_CATEGORIES for c in categories) else None

    llm_flagged = bool(llm and llm.get("categories") and (llm.get("confidence") or 0) >= 0.5)
    llm_critical = bool(llm and any(c in CRITICAL_CATEGORIES for c in llm.get("categories", []))
                        and (llm.get("confidence") or 0) >= 0.7)
    # Flagged only for personal data: PII_SCAN_ACTION decides, not the
    # general knob, so a customer list can be held while a swear word is not.
    pii_only = bool(flagged_pages) and not llm_flagged and all(
        not (set(r.categories) - SENSITIVITY_ONLY_CATEGORIES - {PII_CATEGORY})
        for r in flagged_pages.values()
    )

    flags: Dict[str, Any] = {
        "action": action,
        "max_score": round(max_score, 3),
        "critical": has_critical or llm_critical,
        "categories": categories,
        "flagged_pages": {str(p): r.to_dict() for p, r in list(flagged_pages.items())[:25]},
        "flagged_page_count": len(flagged_pages),
    }
    if llm is not None:
        flags["llm"] = llm

    if not flagged_pages and not llm_flagged:
        # Nothing objectionable; keep sensitivity hints so the label suggestion
        # survives, but no policy flags.
        return "clear", (flags if categories else None), suggested

    if pii_only:
        flags["action"] = pii_detector.action()
        status = "quarantined" if pii_detector.action() == "quarantine" else "flagged"
        return status, flags, suggested

    if action == "off":
        return "clear", None, suggested

    if has_critical or llm_critical:
        status = "rejected" if action == "reject" else "quarantined"
    elif action == "reject":
        status = "rejected"
    elif action == "quarantine":
        status = "quarantined"
    else:
        status = "flagged"
    return status, flags, suggested


def is_hash_blocked(content_hash: str) -> bool:
    """Admin blocklist lookup; False on any error so ingest never hangs on it."""
    if not content_hash:
        return False
    try:
        from services.app_database import app_db

        return app_db.is_hash_blocked(content_hash)
    except Exception as e:
        logger.warning(f"Blocklist lookup failed: {e}")
        return False
