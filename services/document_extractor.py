"""Generic document text extraction service supporting multiple file formats."""

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Union
import logging
import re

# PDF extraction
import pdfplumber
from pypdf import PdfReader

# pdfminer.six (pdfplumber's backend) logs a WARNING per glyph for PDFs with a
# malformed font descriptor — e.g. "Could not get FontBBox ... cannot be parsed
# as 4 floats". These are benign: text extraction proceeds normally and the
# glyphs are still read. Left at WARNING they flood the console (hundreds of
# lines per file) and bury real indexing errors, so quiet them to ERROR.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Code extraction
from services.code_extractor import (
    CodeExtractor, CodeChunk, CodeLanguage,
    SUPPORTED_CODE_EXTENSIONS, is_code_file
)

# OCR engine abstraction
from services.ocr_engine import create_ocr_engine

# Audio transcription (meeting recordings)
from services.audio_transcriber import AUDIO_EXTENSIONS, is_audio_file

# Prompt injection detection
from services.prompt_injection_detector import PromptInjectionDetector, InjectionScanResult
# Content-policy screening (same shape, feeds the ingest decision)
from services.content_policy import (
    PolicyScanResult,
    scan_pages as scan_policy_pages,
    scanning_enabled as policy_scanning_enabled,
)

logger = logging.getLogger(__name__)


# Vision-OCR providers that require an API key. Local Ollama, Bedrock (AWS
# credential chain), "auto" (resolves to local Ollama), and "none" do not.
_KEY_REQUIRED_PROVIDERS = frozenset(
    {"anthropic", "openai", "grok", "google", "github", "openrouter", "ollama_cloud"}
)


def _provider_needs_key(provider_name: str) -> bool:
    return provider_name in _KEY_REQUIRED_PROVIDERS


# ---------------------------------------------------------------------------
# Tabular header sniffing (v4.1 P0.1)
#
# Brokerage / bank / CRM exports almost always carry N preamble rows
# (`As Of`, `Currency`, blank lines, …) before the real column headers.
# These helpers detect the first row that looks like text labels followed by
# a data-like row, so the rest of the ingestion pipeline can bind to clean
# column names instead of `Unnamed: 0..N`.
# ---------------------------------------------------------------------------

_DATE_LIKE_RE = re.compile(
    r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(\s+\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|am|pm)?)?$"
)


def _is_numeric_like(value: str) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    cleaned = s.replace(",", "").replace("$", "").replace("%", "").replace(" ", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if cleaned and cleaned[-1] in ("K", "M", "B", "k", "m", "b"):
        cleaned = cleaned[:-1]
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _is_date_like(value: str) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    return bool(_DATE_LIKE_RE.match(s))


def _row_cells(row: List[Any]) -> Tuple[List[str], List[str]]:
    """Return (all_cells_as_str, non_empty_cells)."""
    cells: List[str] = []
    for c in row:
        if c is None:
            cells.append("")
            continue
        s = str(c).strip()
        # pandas turns blanks into 'nan' when dtype=str — filter that out
        if s.lower() == "nan":
            s = ""
        cells.append(s)
    non_empty = [c for c in cells if c]
    return cells, non_empty


def _looks_like_header_row(row: List[Any]) -> bool:
    cells, non_empty = _row_cells(row)
    if len(non_empty) < 3:
        return False
    if len(non_empty) / max(len(cells), 1) < 0.5:
        return False
    text_cells = sum(
        1 for c in non_empty
        if not _is_numeric_like(c) and not _is_date_like(c) and len(c) < 80
    )
    return text_cells / len(non_empty) >= 0.8


def _looks_like_data_row(row: List[Any]) -> bool:
    _, non_empty = _row_cells(row)
    if len(non_empty) < 3:
        return False
    typed = sum(1 for c in non_empty if _is_numeric_like(c) or _is_date_like(c))
    return typed / len(non_empty) >= 0.4


def _detect_header_row(raw_rows: List[List[Any]], max_scan: int = 30) -> int:
    """Return the 0-indexed row that looks like the real header, or 0 if unclear."""
    if len(raw_rows) < 2:
        return 0
    limit = min(max_scan, len(raw_rows) - 1)
    for i in range(limit):
        if _looks_like_header_row(raw_rows[i]) and _looks_like_data_row(raw_rows[i + 1]):
            return i
    return 0


def _extract_preamble_metadata(preamble_rows: List[List[Any]]) -> Dict[str, Any]:
    """
    Pull key/value pairs from the rows above the detected header.

    Handles two common preamble styles:
      - Two-cell rows: ``As Of,01/15/2026`` (Schwab-style)
      - Single-cell "Key: Value": ``As Of: Apr 11, 2026 4:13 PM EDT`` (Pershing-style)
    """
    metadata: Dict[str, Any] = {}
    raw_lines: List[str] = []
    for row in preamble_rows:
        _, non_empty = _row_cells(row)
        if not non_empty:
            continue
        raw_lines.append(" | ".join(non_empty))
        if len(non_empty) == 2:
            key = non_empty[0].rstrip(":").strip()
            if key and len(key) < 80:
                metadata[key] = non_empty[1]
        elif len(non_empty) == 1 and ": " in non_empty[0]:
            key, _, value = non_empty[0].partition(": ")
            key = key.strip()
            value = value.strip()
            if key and len(key) < 80:
                metadata[key] = value
    if raw_lines:
        metadata["_raw_preamble"] = raw_lines
    return metadata


def _sniff_csv_header(csv_path: Path) -> Tuple[int, Dict[str, Any]]:
    """
    Read up to 30 raw rows via the csv stdlib (which tolerates ragged rows
    that would break pandas' C tokenizer), find the header row, and extract
    preamble metadata.
    """
    import csv
    raw_rows: List[List[str]] = []
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(f, dialect)
            for i, row in enumerate(reader):
                if i >= 30:
                    break
                raw_rows.append(row)
    except Exception:
        return 0, {}
    if not raw_rows:
        return 0, {}
    header_idx = _detect_header_row(raw_rows)
    if header_idx == 0:
        return 0, {}
    return header_idx, _extract_preamble_metadata(raw_rows[:header_idx])


def _sniff_dataframe_header(raw_df) -> Tuple[int, Dict[str, Any]]:
    """Header detection for an already-loaded raw (header=None) DataFrame — used for XLSX sheets."""
    if raw_df is None or raw_df.empty:
        return 0, {}
    raw_rows = raw_df.head(30).fillna("").astype(str).values.tolist()
    header_idx = _detect_header_row(raw_rows)
    if header_idx == 0:
        return 0, {}
    return header_idx, _extract_preamble_metadata(raw_rows[:header_idx])


# Docling availability flag (free OCR fallback)
DOCLING_AVAILABLE = False

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    pass


class ExtractionResult:
    """Result of text extraction with metadata about the extraction method used."""

    def __init__(self, page_texts: Dict[int, str], method: str = "text",
                 ocr_pages: List[int] = None, cleanup_pages: List[int] = None,
                 injection_warnings: Dict[int, "InjectionScanResult"] = None,
                 policy_warnings: Dict[int, "PolicyScanResult"] = None):
        self.page_texts = page_texts
        self.method = method
        self.ocr_pages = ocr_pages or []
        self.cleanup_pages = cleanup_pages or []
        self.injection_warnings = injection_warnings or {}  # page_num -> InjectionScanResult
        self.policy_warnings = policy_warnings or {}        # page_num -> PolicyScanResult

    def __getitem__(self, key):
        return self.page_texts[key]

    def __iter__(self):
        return iter(self.page_texts)

    def items(self):
        return self.page_texts.items()

    def keys(self):
        return self.page_texts.keys()

    def values(self):
        return self.page_texts.values()

    def __len__(self):
        return len(self.page_texts)


class DocumentExtractor:
    """Extracts text from various document formats (PDF, TXT, DOCX, CSV, MD, JSON, JSONL) and code files."""

    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif'}
    SUPPORTED_EXTENSIONS = {'.pdf', '.txt', '.docx', '.csv', '.xlsx', '.xls', '.md', '.html', '.htm', '.json', '.jsonl'} | SUPPORTED_CODE_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS
    TABULAR_EXTENSIONS = {'.csv', '.xlsx', '.xls'}
    AUDIO_EXTENSIONS = AUDIO_EXTENSIONS

    # Fixed rendering defaults for vision OCR. These used to be user settings,
    # but modern vision models handle ordinary scans fine at these values —
    # the only decisions worth surfacing are on/off and which engine.
    VISION_OCR_DPI = 150
    VISION_OCR_ENHANCE_IMAGE = True

    def __init__(self, enable_ocr: bool = False,
                 ocr_max_pages: int = 25, ocr_max_file_mb: int = 50,
                 vision_ocr_provider: str = "none",
                 vision_ocr_model: str = "",
                 vision_ocr_api_key: str = "",
                 ollama_base_url: str = "http://localhost:11434"):
        self.enable_ocr = enable_ocr
        self.ocr_max_pages = ocr_max_pages
        self.ocr_max_file_mb = ocr_max_file_mb
        self.vision_ocr_provider = vision_ocr_provider
        self.vision_ocr_model = vision_ocr_model
        self.vision_ocr_api_key = vision_ocr_api_key
        self.ollama_base_url = ollama_base_url
        self._vision_ocr_engine_instance = None
        self._code_extractor = CodeExtractor()
        self._injection_detector = PromptInjectionDetector()

    @staticmethod
    def _looks_like_noise_line(line: str) -> bool:
        """Detect short OCR artifact lines from stamps, specks, and margin noise."""
        normalized = re.sub(r"\s+", " ", (line or "")).strip()
        if not normalized:
            return True

        if re.fullmatch(r"[|+\-=_:.~,'`\"/\\]+", normalized):
            return True

        protected_patterns = (
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b",
            r"\b(?:docket|license|operating|appendix|region|dear|mr|mrs|ms|dr|ro|ler|pdr|nrc|office)\b",
        )
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in protected_patterns):
            return False

        letters = sum(ch.isalpha() for ch in normalized)
        digits = sum(ch.isdigit() for ch in normalized)
        alnum = sum(ch.isalnum() for ch in normalized)
        symbols = sum(not ch.isalnum() and not ch.isspace() for ch in normalized)
        tokens = re.findall(r"[A-Za-z0-9]+", normalized)
        single_char_tokens = sum(len(token) == 1 for token in tokens)

        if len(normalized) <= 2:
            return True
        if len(normalized) <= 4 and letters <= 1 and digits <= 2:
            return True
        if len(normalized) <= 8 and letters == 0 and digits > 0:
            return True
        if tokens and len(tokens) >= 3 and (single_char_tokens / len(tokens)) >= 0.75 and len(normalized) <= 18:
            return True
        if alnum <= 4 and symbols >= 2 and len(normalized) <= 16:
            return True
        if letters <= 2 and digits <= 4 and symbols >= 1 and len(normalized) <= 12:
            return True

        return False

    @staticmethod
    def normalize_ocr_text(text: str) -> str:
        """Cleanup OCR output with extra filtering for noisy scanned pages."""
        cleaned_lines: List[str] = []

        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if re.fullmatch(r"[|+\-=_:.~]{3,}", line):
                continue

            if "|" in line and line.count("|") >= 2:
                cells = [re.sub(r"\s+", " ", cell).strip(" :-_") for cell in line.split("|")]
                cells = [cell for cell in cells if cell]
                if not cells:
                    continue
                if all(re.fullmatch(r"[-=:.~]{2,}", cell) for cell in cells):
                    continue
                line = " | ".join(cells)

            line = re.sub(r"[ 	]+", " ", line)
            line = re.sub(r"([|+\-=_:.~])\1{3,}", r"\1", line)

            if re.fullmatch(r"[|+\-=_:.~]{2,}", line):
                continue

            alpha_num_chars = sum(ch.isalnum() for ch in line)
            symbol_chars = sum(not ch.isalnum() and not ch.isspace() for ch in line)
            if alpha_num_chars == 0 and symbol_chars >= 3:
                continue

            if DocumentExtractor._looks_like_noise_line(line):
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    def _resolve_ollama_vision_model(self) -> Optional[str]:
        """Pick a vision-capable model from the running Ollama instance.

        Used by the 'auto' OCR provider so the user doesn't have to configure a
        model separately — if they already pulled a multimodal model (gemma3,
        llava, llama3.2-vision, qwen-vl, …) we just use it. An explicitly
        configured `vision_ocr_model` always wins. Returns None if Ollama is
        unreachable or has no vision model.
        """
        if self.vision_ocr_model:
            return self.vision_ocr_model

        base = (self.ollama_base_url or "http://localhost:11434").rstrip("/")
        # Name patterns for the common multimodal families on Ollama.
        vision_patterns = (
            "llava", "vl", "vision", "minicpm-v", "moondream", "bakllava",
            "cogvlm", "internvl", "gemma3", "llama3.2-vision", "qwen2-vl",
            "qwen2.5vl", "qwen2.5-vl",
        )
        try:
            import httpx

            with httpx.Client(timeout=5.0) as client:
                tags = client.get(f"{base}/api/tags").json()
                models = [m.get("name") for m in tags.get("models", []) if m.get("name")]
                if not models:
                    logger.warning("Vision OCR 'auto': Ollama has no models installed")
                    return None

                # Prefer authoritative detection: a multimodal Ollama model lists
                # 'clip' or 'mllama' in its architecture families.
                for name in models:
                    try:
                        show = client.post(f"{base}/api/show", json={"name": name}).json()
                        families = (show.get("details") or {}).get("families") or []
                        if "clip" in families or "mllama" in families:
                            return name
                    except Exception:
                        continue

                # Fall back to a name heuristic.
                for name in models:
                    if any(p in name.lower() for p in vision_patterns):
                        return name

                logger.warning(
                    "Vision OCR 'auto': no vision-capable model found in Ollama "
                    f"(installed: {', '.join(models)})"
                )
                return None
        except Exception as e:
            logger.warning(f"Vision OCR 'auto': could not reach Ollama at {base}: {e}")
            return None

    def _resolve_vision_api_key(self, provider_name: str) -> str:
        """The key for the vision provider, with sensible fallbacks.

        An explicit VISION_OCR_API_KEY always wins. For ollama_cloud the key
        falls back to the Ollama Cloud key already configured for the rest of
        the app (OLLAMA_CLOUD_API_KEY / OLLAMA_CLOUD_TOKEN in .env, then the
        team key saved in Settings → AI Providers) — one key, not one per
        feature.
        """
        if self.vision_ocr_api_key:
            return self.vision_ocr_api_key
        if provider_name == "ollama_cloud":
            try:
                from config import settings
                if settings.ollama_cloud_api_key:
                    return settings.ollama_cloud_api_key
            except Exception:
                pass
            try:
                from services.app_database import app_db
                return app_db.get_agent_api_key("ollama_cloud") or ""
            except Exception:
                return ""
        return ""

    def _get_vision_ocr_engine(self) -> Optional["OCREngine"]:
        """Get or create a VisionOCREngine if vision OCR is configured."""
        if self._vision_ocr_engine_instance is not None:
            return self._vision_ocr_engine_instance

        if not self.vision_ocr_provider or self.vision_ocr_provider == "none":
            return None

        # 'auto' = zero-config: use whatever vision model the local Ollama has.
        provider_name = self.vision_ocr_provider
        model = self.vision_ocr_model
        if provider_name == "auto":
            resolved = self._resolve_ollama_vision_model()
            if not resolved:
                logger.warning(
                    "Vision OCR 'auto' could not find an Ollama vision model; "
                    "falling back to free local OCR (Docling/Tesseract) if available."
                )
                return None
            provider_name = "ollama"
            model = resolved
            logger.info(f"Vision OCR 'auto' resolved to Ollama model: {model}")

        api_key = self._resolve_vision_api_key(provider_name)
        if _provider_needs_key(provider_name) and not api_key:
            logger.warning(f"Vision OCR provider '{provider_name}' requires an API key")
            return None

        try:
            from services.ai_service import create_provider
            from services.ocr_engine import VisionOCREngine

            provider_kwargs: dict = {}
            if provider_name == "ollama":
                provider_kwargs["base_url"] = self.ollama_base_url
                provider_kwargs["model"] = model

            provider = create_provider(
                provider_name=provider_name,
                api_key=api_key,
                **provider_kwargs,
            )

            self._vision_ocr_engine_instance = VisionOCREngine(
                provider=provider,
                model=model,
                cleanup_pass=False,
                dpi=self.VISION_OCR_DPI,
                max_pages=self.ocr_max_pages,
                enhance_image=self.VISION_OCR_ENHANCE_IMAGE,
            )
            logger.info(f"Vision OCR engine initialized: {provider_name}/{model}")
        except Exception as e:
            logger.warning(f"Failed to initialize Vision OCR engine: {e}")
            return None

        return self._vision_ocr_engine_instance

    def extract_text(self, file_path: Path, force_ocr: bool = False) -> ExtractionResult:
        """
        Extract text from a document file, returning a mapping of page/section numbers to text.

        Args:
            file_path: Path to the document file
            force_ocr: If True, skip native PDF text extraction and go directly to vision OCR.
                       Useful for scanned PDFs that have a corrupt/junk embedded text layer
                       which would otherwise clear the auto-trigger threshold.

        Returns:
            ExtractionResult with page texts and extraction method info

        Raises:
            ValueError: If file type is not supported
            Exception: If text extraction fails
        """
        file_ext = file_path.suffix.lower()

        if not is_supported_filename(file_path.name):
            raise ValueError(
                f"Unsupported file type: {file_ext or file_path.name}. "
                f"Supported types: {', '.join(sorted(self.SUPPORTED_EXTENSIONS))}"
            )

        logger.info(f"Extracting text from {file_ext or file_path.name} file: {file_path.name}")

        # Code files (by suffix or well-known basename such as CMakeLists.txt)
        # are skipped for injection scanning (high false-positive rate).
        if is_code_file(file_path.name):
            return self._extract_code(file_path)

        # Audio files route through Whisper for transcription and then flow
        # through the normal indexing pipeline as a single-"page" document.
        # A transcript is text like any other for the policy scan.
        if file_ext in AUDIO_EXTENSIONS:
            result = self._extract_audio(file_path)
            self._scan_policy(result, file_path)
            return result

        # Images become a single-"page" document holding the vision model's
        # description + transcription (local OCR text as the fallback). The
        # injection scan below still applies — text photographed into an image
        # is no more trustworthy than text typed into a file.
        if file_ext in self.IMAGE_EXTENSIONS:
            result = self._extract_image(file_path)
            result.injection_warnings = self._injection_detector.scan_pages(
                result.page_texts, filename=file_path.name
            )
            self._scan_policy(result, file_path)
            return result

        if file_ext == '.pdf':
            result = self._extract_pdf(file_path, force_ocr=force_ocr)
        elif file_ext == '.txt':
            result = ExtractionResult(self._extract_txt(file_path), method="text")
        elif file_ext == '.docx':
            result = ExtractionResult(self._extract_docx(file_path), method="text")
        elif file_ext == '.csv':
            result = ExtractionResult(self._extract_csv(file_path), method="text")
        elif file_ext in ('.xlsx', '.xls'):
            result = ExtractionResult(self._extract_xlsx(file_path), method="text")
        elif file_ext == '.md':
            result = ExtractionResult(self._extract_markdown(file_path), method="text")
        elif file_ext in ('.html', '.htm'):
            result = ExtractionResult(self._extract_html(file_path), method="text")
        elif file_ext == '.json':
            result = ExtractionResult(self._extract_json(file_path), method="text")
        elif file_ext == '.jsonl':
            result = ExtractionResult(self._extract_jsonl(file_path), method="text")
        else:
            raise ValueError(f"Unsupported file extension: {file_ext}")

        result.injection_warnings = self._injection_detector.scan_pages(
            result.page_texts, filename=file_path.name
        )
        self._scan_policy(result, file_path)

        return result

    @staticmethod
    def _scan_policy(result: ExtractionResult, file_path: Path) -> None:
        """Attach per-page content-policy findings (no-op when the scan is off).

        The decision — clear / flagged / quarantined / rejected — is taken by
        the indexer, which knows the collection and the uploader; extraction
        only reports what it saw.
        """
        if not policy_scanning_enabled():
            return
        try:
            result.policy_warnings = scan_policy_pages(result.page_texts, filename=file_path.name)
        except Exception as e:  # never let the tripwire break ingest
            logger.warning(f"Content policy scan failed for {file_path.name}: {e}")

    def _extract_pdf(self, pdf_path: Path, force_ocr: bool = False) -> ExtractionResult:
        """
        Extract text from PDF file using pdfplumber with pypdf and OCR fallback.

        Args:
            force_ocr: Skip native extraction and go directly to vision OCR.

        Returns:
            ExtractionResult with page texts and extraction method info
        """
        page_texts = {}
        ocr_pages = []
        cleanup_pages = []
        method = "text"

        if force_ocr and self.enable_ocr:
            logger.info(f"Force OCR enabled for {pdf_path.name} — skipping native extraction")
        else:
            try:
                # Try pdfplumber first (better for complex layouts)
                page_texts = self._extract_pdf_with_pdfplumber(pdf_path)
            except Exception as e:
                logger.warning(f"pdfplumber failed for {pdf_path.name}: {e}. Trying pypdf...")
                try:
                    # Fallback to pypdf
                    page_texts = self._extract_pdf_with_pypdf(pdf_path)
                except Exception as e2:
                    logger.warning(f"pypdf failed for {pdf_path.name}: {e2}")
                    page_texts = {}

        # Check if we need OCR (no text, mostly empty pages, or force_ocr requested)
        if self.enable_ocr:
            num_pages = len(page_texts) if page_texts else self._get_pdf_page_count(pdf_path)

            # Cost guards. The page-count cap applies only to FULL-document
            # OCR — the selective path below already limits itself to the weak
            # pages, so a long, mostly-texty document with a few scanned pages
            # still gets those pages OCR'd.
            ocr_page_budget_ok = not (self.ocr_max_pages > 0 and num_pages > self.ocr_max_pages)
            if not ocr_page_budget_ok:
                logger.info(f"Full-document OCR unavailable for {pdf_path.name}: {num_pages} pages exceeds limit of {self.ocr_max_pages}")
                if not page_texts:
                    raise Exception(f"Failed to extract text from PDF {pdf_path.name}: text extraction failed and OCR skipped (too many pages)")

            file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
            if self.ocr_max_file_mb > 0 and file_size_mb > self.ocr_max_file_mb:
                logger.info(f"Skipping OCR for {pdf_path.name}: {file_size_mb:.1f}MB exceeds limit of {self.ocr_max_file_mb}MB")
                if not page_texts:
                    raise Exception(f"Failed to extract text from PDF {pdf_path.name}: text extraction failed and OCR skipped (file too large)")
                return ExtractionResult(page_texts, method="text")

            # Full-document OCR when native extraction failed or is weak overall
            # (avg < 50 chars/page) or when forced. Otherwise, OCR just the
            # individual pages native extraction left (nearly) empty — the
            # scanned or figure-only pages inside an otherwise texty document,
            # which the old whole-document threshold silently skipped.
            total_chars = sum(len((t or "").strip()) for t in page_texts.values())
            avg_chars = total_chars / max(len(page_texts), 1) if page_texts else 0
            full_ocr = force_ocr or not page_texts or avg_chars < 50
            weak_pages = sorted(
                p for p, t in page_texts.items() if len((t or "").strip()) < 50
            )

            if full_ocr and ocr_page_budget_ok:
                try:
                    ocr_result = self._extract_pdf_with_ocr(pdf_path)
                    if ocr_result:
                        ocr_texts, ocr_pages, cleanup_pages = ocr_result
                        for page_num, ocr_text in ocr_texts.items():
                            if ocr_text and ocr_text.strip():
                                page_texts[page_num] = ocr_text
                        if ocr_pages:
                            method = "hybrid" if any(p not in ocr_pages for p in page_texts.keys()) else "ocr"
                except MemoryError:
                    logger.error(f"OCR out of memory for {pdf_path.name}")
                except Exception as e:
                    logger.warning(f"OCR failed for {pdf_path.name}: {e}")
            elif weak_pages:
                targets = weak_pages
                if self.ocr_max_pages > 0 and len(targets) > self.ocr_max_pages:
                    logger.info(
                        f"Selective OCR for {pdf_path.name}: {len(targets)} weak pages "
                        f"exceeds limit, processing first {self.ocr_max_pages}"
                    )
                    targets = targets[: self.ocr_max_pages]
                logger.info(
                    f"Selective OCR for {pdf_path.name}: native extraction is good "
                    f"({avg_chars:.0f} avg chars/page) but page(s) "
                    f"{targets} are empty — running OCR on just those"
                )
                try:
                    ocr_result = self._extract_pdf_with_ocr(pdf_path, pages=targets)
                    if ocr_result:
                        ocr_texts, _, cleanup_pages = ocr_result
                        for p in targets:
                            ocr_text = ocr_texts.get(p)
                            if ocr_text and ocr_text.strip():
                                page_texts[p] = ocr_text
                                ocr_pages.append(p)
                        if ocr_pages:
                            method = "hybrid"
                except MemoryError:
                    logger.error(f"OCR out of memory for {pdf_path.name}")
                except Exception as e:
                    logger.warning(f"Selective OCR failed for {pdf_path.name}: {e}")

        if not page_texts:
            raise Exception(f"Failed to extract text from PDF {pdf_path.name}: all methods failed")

        if not any(text and text.strip() for text in page_texts.values()):
            if self.enable_ocr:
                raise Exception(f"Failed to extract text from PDF {pdf_path.name}: OCR returned no text")
            raise Exception(f"Failed to extract text from PDF {pdf_path.name}: PDF appears to be image-only and OCR is disabled")

        return ExtractionResult(page_texts, method=method, ocr_pages=ocr_pages, cleanup_pages=cleanup_pages)

    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        """Get the number of pages in a PDF without extracting text."""
        try:
            reader = PdfReader(str(pdf_path))
            return len(reader.pages)
        except Exception:
            # Fallback: try pdfplumber
            try:
                with pdfplumber.open(pdf_path) as pdf:
                    return len(pdf.pages)
            except Exception:
                return 0

    def _extract_pdf_with_pdfplumber(self, pdf_path: Path) -> Dict[int, str]:
        """Extract text using pdfplumber (handles complex layouts better)."""
        page_texts = {}

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Isolate per-page failures: a single page with a broken font or
                # malformed content stream shouldn't abandon the whole document
                # (which would force a fall-back to pypdf or OCR for every page).
                try:
                    text = page.extract_text() or ""
                except Exception as e:
                    logger.warning(
                        f"pdfplumber: page {page_num} of {pdf_path.name} failed "
                        f"to extract ({e}); leaving it empty"
                    )
                    text = ""
                page_texts[page_num] = text.strip()

        return page_texts

    def _extract_pdf_with_pypdf(self, pdf_path: Path) -> Dict[int, str]:
        """Extract text using pypdf (fallback method)."""
        page_texts = {}

        reader = PdfReader(str(pdf_path))
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            page_texts[page_num] = text.strip()

        return page_texts

    def _extract_pdf_with_ocr(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> Optional[Tuple[Dict[int, str], List[int], List[int]]]:
        """Extract text from PDF using Vision AI OCR or Docling fallback.

        Args:
            pages: Optional 1-based page numbers to restrict OCR to (the
                selective path). The vision engine renders only those pages;
                the Docling fallback always processes the whole document, so
                callers in selective mode must merge only the pages they asked
                for.

        Returns:
            Tuple of (page_texts, ocr_pages, cleanup_pages) or None.
            cleanup_pages lists page numbers where an LLM cleanup pass was applied.
        """
        # Try Vision AI OCR first if configured
        engine = self._get_vision_ocr_engine()
        if engine:
            try:
                logger.info(f"Running Vision AI OCR on {pdf_path.name}")
                result = engine.extract_text(str(pdf_path), pages=pages)
                page_texts = {p: t for p, t in result.to_page_dict().items()}
                ocr_pages = [p for p, t in page_texts.items() if t and t.strip()]
                cleanup_pages = result.metadata.get("cleanup_pages", [])
                if ocr_pages:
                    total_chars = sum(len(t) for t in page_texts.values())
                    logger.info(
                        f"Vision OCR extracted {total_chars} chars from {len(ocr_pages)} page(s)"
                        + (f", cleanup applied to {len(cleanup_pages)} page(s)" if cleanup_pages else "")
                    )
                    return page_texts, ocr_pages, cleanup_pages
                logger.warning("Vision OCR returned empty text")
            except Exception as e:
                logger.warning(f"Vision OCR failed for {pdf_path.name}: {e}")

        # Fallback: try Docling if available (free, no API key needed)
        if DOCLING_AVAILABLE:
            try:
                docling_engine = create_ocr_engine(engine_name="docling", fallback=False)
                if docling_engine:
                    logger.info(f"Running Docling OCR on {pdf_path.name}")
                    result = docling_engine.extract_text(str(pdf_path))
                    page_texts = {
                        p: self.normalize_ocr_text(t)
                        for p, t in result.to_page_dict().items()
                    }
                    ocr_pages = [p for p, t in page_texts.items() if t and t.strip()]
                    if ocr_pages:
                        return page_texts, ocr_pages, []  # Docling has no LLM cleanup
            except Exception as e:
                logger.warning(f"Docling OCR failed for {pdf_path.name}: {e}")

        logger.warning(f"No OCR engine available or all failed for {pdf_path.name}")
        return None

    def _extract_image(self, image_path: Path) -> ExtractionResult:
        """Index a standalone image as a one-page document.

        Preferred: the vision model describes the picture and transcribes its
        text, so photos, screenshots, diagrams, and whiteboards are findable
        by content. Fallback (no vision model configured/reachable): local
        Docling/Tesseract OCR recovers any printed text — no description, but
        nothing to configure and no network egress.
        """
        engine = self._get_vision_ocr_engine()
        if engine is not None:
            try:
                text = engine.describe_image(str(image_path))
                if text:
                    return ExtractionResult({1: text}, method="vision", ocr_pages=[1])
                logger.warning(f"Vision model returned no text for image {image_path.name}")
            except Exception as e:
                logger.warning(f"Vision description failed for {image_path.name}: {e}")

        if DOCLING_AVAILABLE:
            try:
                docling_engine = create_ocr_engine(engine_name="docling", fallback=False)
                if docling_engine:
                    logger.info(f"Running Docling OCR on image {image_path.name}")
                    result = docling_engine.extract_text(str(image_path))
                    text = "\n".join(
                        t for t in result.to_page_dict().values() if t and t.strip()
                    )
                    if text.strip():
                        return ExtractionResult(
                            {1: self.normalize_ocr_text(text)}, method="ocr", ocr_pages=[1]
                        )
            except Exception as e:
                logger.warning(f"Docling OCR failed for image {image_path.name}: {e}")

        raise Exception(
            f"Cannot index image {image_path.name}: no vision model produced a "
            f"description and local OCR found no readable text. Configure a vision "
            f"provider (Settings → OCR — a local Ollama vision model or a cloud "
            f"one) to index pictures by their content."
        )

    def is_ocr_available(self) -> bool:
        """Check if OCR is available with current configuration."""
        if self.vision_ocr_provider and self.vision_ocr_provider != "none":
            return not _provider_needs_key(self.vision_ocr_provider) or bool(
                self._resolve_vision_api_key(self.vision_ocr_provider)
            )
        return DOCLING_AVAILABLE

    def get_ocr_engine_name(self) -> Optional[str]:
        """Get the name of the active OCR engine."""
        if self.vision_ocr_provider and self.vision_ocr_provider != "none":
            return f"vision_ai/{self.vision_ocr_provider}"
        if DOCLING_AVAILABLE:
            return "docling"
        return None

    def _extract_txt(self, txt_path: Path) -> Dict[int, str]:
        """
        Extract text from plain text file.

        Returns:
            Dictionary with single entry (page 1) containing all text
        """
        try:
            # Try UTF-8 first
            with open(txt_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            # Fallback to latin-1 for broader compatibility
            logger.warning(f"UTF-8 decoding failed for {txt_path.name}, trying latin-1")
            with open(txt_path, 'r', encoding='latin-1') as f:
                text = f.read()

        # Return as single "page"
        return {1: text.strip()}

    def _extract_docx(self, docx_path: Path) -> Dict[int, str]:
        """
        Extract text from DOCX file.

        Returns:
            Dictionary mapping page numbers to text (pages are estimated by paragraphs)
        """
        try:
            from docx import Document
        except ImportError:
            raise ImportError(
                "python-docx is required for DOCX support. "
                "Install it with: pip install python-docx"
            )

        doc = Document(str(docx_path))

        # Extract all paragraphs
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]

        if not paragraphs:
            logger.warning(f"No text found in {docx_path.name}")
            return {1: ""}

        # Group paragraphs into "pages" (every ~10 paragraphs = 1 page)
        # This is a rough approximation since DOCX doesn't have explicit pages
        paragraphs_per_page = 10
        page_texts = {}

        for i in range(0, len(paragraphs), paragraphs_per_page):
            page_num = (i // paragraphs_per_page) + 1
            page_content = '\n\n'.join(paragraphs[i:i + paragraphs_per_page])
            page_texts[page_num] = page_content.strip()

        return page_texts

    def _extract_csv(self, csv_path: Path) -> Dict[int, str]:
        """
        Extract text from CSV file (legacy format for backward compatibility).

        Returns:
            Dictionary with sections (every ~50 rows = 1 section) containing formatted text
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for CSV support. "
                "Install it with: pip install pandas"
            )

        # Read CSV — sniff header row to skip brokerage-style preamble lines
        header_idx, doc_metadata = _sniff_csv_header(csv_path)
        try:
            df = pd.read_csv(csv_path, skiprows=header_idx)
        except Exception as e:
            logger.error(f"Failed to read CSV {csv_path.name}: {e}")
            raise Exception(f"Failed to read CSV file: {e}")

        if header_idx > 0:
            logger.info(
                f"CSV {csv_path.name}: detected header at row {header_idx} "
                f"(skipped {header_idx} preamble row(s))"
            )

        if df.empty:
            logger.warning(f"Empty CSV file: {csv_path.name}")
            return {1: ""}

        # Convert DataFrame to text representation
        # Group rows into "pages" (every 50 rows = 1 page)
        rows_per_page = 50
        page_texts = {}

        for start_idx in range(0, len(df), rows_per_page):
            page_num = (start_idx // rows_per_page) + 1
            end_idx = min(start_idx + rows_per_page, len(df))

            # Get chunk of dataframe
            df_chunk = df.iloc[start_idx:end_idx]

            # Convert to text with column headers
            lines = []

            # Add header row for first page
            if page_num == 1:
                header = " | ".join(str(col) for col in df.columns)
                lines.append(header)
                lines.append("-" * len(header))

            # Add data rows
            for _, row in df_chunk.iterrows():
                row_text = " | ".join(str(val) for val in row.values)
                lines.append(row_text)

            page_texts[page_num] = "\n".join(lines)

        return page_texts

    def _extract_xlsx(self, xlsx_path: Path) -> Dict[int, str]:
        """
        Extract text from an Excel workbook (fallback/semantic path).

        Each sheet becomes its own "page". Output uses the same `col: val | ...`
        shape as CSV extraction so downstream chunking and search treat it
        identically.
        """
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for XLSX support. Install with: pip install pandas openpyxl"
            ) from exc

        try:
            excel = pd.ExcelFile(xlsx_path)
        except Exception as e:
            logger.error(f"Failed to open XLSX {xlsx_path.name}: {e}")
            raise Exception(f"Failed to open Excel file: {e}")

        page_texts: Dict[int, str] = {}
        for page_num, sheet_name in enumerate(excel.sheet_names, start=1):
            try:
                raw = excel.parse(sheet_name, header=None)
            except Exception as e:
                logger.warning(f"Failed to parse sheet '{sheet_name}' in {xlsx_path.name}: {e}")
                continue
            header_idx, _ = _sniff_dataframe_header(raw)
            try:
                df = excel.parse(sheet_name, header=header_idx)
            except Exception as e:
                logger.warning(f"Failed to parse sheet '{sheet_name}' in {xlsx_path.name}: {e}")
                continue
            if df.empty:
                continue
            if header_idx > 0:
                logger.info(
                    f"XLSX {xlsx_path.name} sheet '{sheet_name}': "
                    f"detected header at row {header_idx}"
                )
            lines = [f"[Sheet: {sheet_name}]"]
            header = " | ".join(str(col) for col in df.columns)
            lines.append(header)
            lines.append("-" * min(len(header), 80))
            for _, row in df.iterrows():
                lines.append(" | ".join("" if pd.isna(v) else str(v) for v in row.values))
            page_texts[page_num] = "\n".join(lines)

        if not page_texts:
            logger.warning(f"No data extracted from {xlsx_path.name}")
            return {1: ""}
        logger.info(f"Extracted {len(page_texts)} sheet(s) from XLSX {xlsx_path.name}")
        return page_texts

    def extract_tabular_sheets(self, file_path: Path) -> List[Dict[str, Any]]:
        """
        Extract tabular data from a CSV or Excel file as a list of sheets.

        Returns a list of dicts, each containing:
            - sheet_name: str ('' for CSV single sheet; sheet title for XLSX)
            - columns: list of original column names
            - rows: list of dicts keyed by original column names
            - row_texts: list of "col: val | ..." text representations (for embedding)

        This method normalizes CSV and XLSX to the same output shape so the
        indexing pipeline can treat them uniformly. Values are returned as
        Python scalars (not pandas NaN) with blanks coerced to empty strings.
        """
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for tabular ingestion. "
                "Install with: pip install pandas openpyxl"
            ) from exc

        ext = file_path.suffix.lower()

        if ext == '.csv':
            header_idx, doc_metadata = _sniff_csv_header(file_path)
            try:
                df = pd.read_csv(file_path, skiprows=header_idx)
            except Exception as e:
                raise Exception(f"Failed to read CSV file: {e}")
            if header_idx > 0:
                logger.info(
                    f"CSV {file_path.name}: detected header at row {header_idx}, "
                    f"preamble fields: {[k for k in doc_metadata if not k.startswith('_')]}"
                )
            sheet = self._dataframe_to_sheet(df, sheet_name='', document_metadata=doc_metadata)
            self._init_sheet_overrides(sheet)
            return [sheet]

        if ext in ('.xlsx', '.xls'):
            try:
                excel = pd.ExcelFile(file_path)
            except Exception as e:
                raise Exception(f"Failed to open Excel file: {e}")

            sheets: List[Dict[str, Any]] = []
            for sheet_name in excel.sheet_names:
                try:
                    raw = excel.parse(sheet_name, header=None)
                except Exception as e:
                    logger.warning(f"Skipping unreadable sheet '{sheet_name}' in {file_path.name}: {e}")
                    continue
                header_idx, doc_metadata = _sniff_dataframe_header(raw)
                try:
                    df = excel.parse(sheet_name, header=header_idx)
                except Exception as e:
                    logger.warning(f"Skipping unreadable sheet '{sheet_name}' in {file_path.name}: {e}")
                    continue
                if df.empty:
                    continue
                if header_idx > 0:
                    logger.info(
                        f"XLSX {file_path.name} sheet '{sheet_name}': detected header at row "
                        f"{header_idx}, preamble fields: "
                        f"{[k for k in doc_metadata if not k.startswith('_')]}"
                    )
                sheet = self._dataframe_to_sheet(
                    df, sheet_name=str(sheet_name), document_metadata=doc_metadata
                )
                self._init_sheet_overrides(sheet)
                sheets.append(sheet)
            return sheets

        raise ValueError(f"extract_tabular_sheets: unsupported extension {ext}")

    def _init_sheet_overrides(self, sheet: Dict[str, Any]) -> None:
        """Initialize the role/type override slots the structured store reads.

        Column roles and types are inferred downstream by the structured store;
        the generic build carries no vendor-specific ingest profiles, so these
        start empty.
        """
        sheet.setdefault('role_overrides', {})
        sheet.setdefault('type_overrides', {})
        sheet.setdefault('vendor_profile', None)

    def _dataframe_to_sheet(self, df, sheet_name: str,
                            document_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convert a pandas DataFrame into our sheet dict shape."""
        import pandas as pd

        columns = [str(col) for col in df.columns]
        # Build a set of column names (lowercased) to detect repeated header rows.
        # Multi-account brokerage exports (Fidelity, Schwab, etc.) often embed the
        # header row again at the start of each account section, e.g.:
        #   Symbol, Description, Quantity, ...   ← real header (used by pandas)
        #   AAPL,   Apple Inc,   10, ...
        #   Symbol, Description, Quantity, ...   ← repeated header ← must skip
        #   MSFT,   Microsoft,   5, ...
        col_name_set = {c.lower().strip() for c in columns}
        rows: List[Dict[str, Any]] = []
        row_texts: List[str] = []

        for _, row in df.iterrows():
            row_dict: Dict[str, Any] = {}
            text_parts: List[str] = []
            for col in columns:
                val = row[col]
                if pd.isna(val):
                    display = ""
                    row_dict[col] = None
                else:
                    # Preserve native types (int/float/bool) for structured store inference
                    if isinstance(val, (int, float, bool)):
                        row_dict[col] = val
                    else:
                        row_dict[col] = str(val)
                    display = str(val)
                text_parts.append(f"{col}: {display}")

            # Skip rows whose non-null string values are all column header names
            # (repeated header rows from multi-account CSV exports).
            non_null_str = [
                str(v).lower().strip()
                for v in row_dict.values()
                if v is not None and str(v).strip()
            ]
            if len(non_null_str) >= 3:
                matching = sum(1 for v in non_null_str if v in col_name_set)
                if matching / len(non_null_str) >= 0.6:
                    logger.debug("Skipped repeated header row in CSV sheet '%s'", sheet_name)
                    continue

            rows.append(row_dict)
            prefix = f"[Sheet: {sheet_name}] " if sheet_name else ""
            row_texts.append(prefix + " | ".join(text_parts))

        return {
            'sheet_name': sheet_name,
            'columns': columns,
            'rows': rows,
            'row_texts': row_texts,
            'document_metadata': document_metadata or {},
        }

    def _extract_markdown(self, md_path: Path) -> Dict[int, str]:
        """
        Extract text from Markdown file, chunking by headers.

        Returns:
            Dictionary mapping section numbers to text (split on h1/h2 headers)
        """
        import re

        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 decoding failed for {md_path.name}, trying latin-1")
            with open(md_path, 'r', encoding='latin-1') as f:
                content = f.read()

        if not content.strip():
            logger.warning(f"Empty markdown file: {md_path.name}")
            return {1: ""}

        # Split on h1 (# ) or h2 (## ) headers
        # Keep the header with the section
        header_pattern = r'(?=^#{1,2}\s+)'
        sections = re.split(header_pattern, content, flags=re.MULTILINE)

        # Filter out empty sections
        sections = [s.strip() for s in sections if s.strip()]

        if not sections:
            return {1: content.strip()}

        page_texts = {}
        for i, section in enumerate(sections, start=1):
            page_texts[i] = section

        return page_texts

    def _extract_html(self, html_path: Path) -> Dict[int, str]:
        """
        Extract text from an HTML file, chunking by h1/h2 headings.

        Mirrors _extract_markdown's section shape so citations point at a
        heading-delimited section rather than one giant page. Old report
        exports are frequently malformed; BeautifulSoup's html.parser backend
        tolerates that and detects the encoding from <meta> tags / BOMs.
        Tables are flattened to one pipe-separated line per row so their
        contents stay searchable.

        Returns:
            Dictionary mapping section numbers to text (split on h1/h2)
        """
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(html_path.read_bytes(), "html.parser")

        for tag in soup(["script", "style", "noscript", "template", "iframe", "svg", "canvas"]):
            tag.decompose()

        self._strip_html_boilerplate(soup)

        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if any(cells):
                    rows.append(" | ".join(cells))
            table.replace_with(NavigableString("\n" + "\n".join(rows) + "\n"))

        title = soup.title.get_text(strip=True) if soup.title else ""

        # Sentinel before each h1/h2 marks a section boundary; it can't occur
        # in text because NUL bytes never survive parsing.
        marker = "\x00SECTION\x00"
        for heading in soup.find_all(["h1", "h2"]):
            heading.insert_before(NavigableString(marker))

        body = soup.body or soup
        text = "\n".join(line.strip() for line in body.get_text("\n").splitlines())

        sections = []
        for part in text.split(marker):
            part = re.sub(r"\n{3,}", "\n\n", part).strip()
            if part:
                sections.append(part)

        if not sections:
            if not title:
                logger.warning(f"No extractable text in HTML file: {html_path.name}")
                return {1: ""}
            sections = [title]
        elif title and title.lower() not in sections[0][:300].lower():
            sections[0] = f"{title}\n\n{sections[0]}"

        return {i: section for i, section in enumerate(sections, start=1)}

    @staticmethod
    def _strip_html_boilerplate(soup) -> None:
        """Remove the parts of a web page that are not its content.

        Pages fetched from a link carry navigation, cookie banners, footers
        and sidebars that would otherwise be indexed as if they were the
        article. When the page marks its content with <main> or a single
        <article>, everything outside it goes; either way, landmark
        elements (nav, footer, aside, forms, page-level headers) are
        dropped. A hand-written report with no landmarks is untouched.
        """
        body = soup.body
        if body is None:
            return

        content = soup.find("main") or soup.find(attrs={"role": "main"})
        if content is None:
            articles = soup.find_all("article")
            if len(articles) == 1:
                content = articles[0]
        if content is not None:
            content_text = content.get_text(" ", strip=True)
            # Only trust the landmark when it holds the bulk of the page:
            # some templates wrap a teaser in <article> and the body in divs.
            if len(content_text) >= 0.4 * len(body.get_text(" ", strip=True)):
                body.clear()
                body.append(content)

        # Nothing that holds most of the page's text is chrome, whatever it
        # is called: WebForms pages wrap everything in one <form>, and a
        # <div class="menu"> can be a restaurant's actual menu.
        body_len = len(body.get_text(" ", strip=True))

        def is_bulk(tag) -> bool:
            return body_len > 0 and len(tag.get_text(" ", strip=True)) >= 0.6 * body_len

        def drop(tag) -> None:
            if not is_bulk(tag):
                tag.decompose()

        for tag in soup.find_all(["nav", "footer", "aside", "form", "button", "select"]):
            drop(tag)
        for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo",
                                                  "complementary", "search", "dialog"]}):
            drop(tag)
        # A page-level <header> is site chrome; one inside an article is its byline.
        for tag in soup.find_all("header"):
            if tag.find_parent(["article", "main", "section"]) is None:
                drop(tag)
        # Class/id names that templates without landmarks use for chrome.
        # Exact tokens only: "footer" yes, "footnote" no.
        chrome = {"footer", "site-footer", "page-footer", "sidebar", "site-header",
                  "navbar", "nav", "menu", "breadcrumb", "breadcrumbs", "cookie-banner",
                  "cookie-consent", "skip-link", "sphinxsidebar", "related"}
        for tag in soup.find_all(True):
            # A tag inside something dropped above is already gone.
            if getattr(tag, "decomposed", False) or tag.name in ("body", "html", "main", "article"):
                continue
            tokens = set(tag.get("class") or [])
            if tag.get("id"):
                tokens.add(tag["id"])
            if tokens & chrome:
                drop(tag)

    def _extract_json(self, json_path: Path) -> Dict[int, str]:
        """
        Extract text from JSON file.

        Returns:
            Dictionary with sections based on top-level keys or array items
        """
        import json

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 decoding failed for {json_path.name}, trying latin-1")
            with open(json_path, 'r', encoding='latin-1') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {json_path.name}: {e}")
            raise Exception(f"Failed to parse JSON file: {e}")

        page_texts = {}

        if isinstance(data, dict):
            # For objects, each top-level key becomes a section
            if not data:
                return {1: "{}"}

            for i, (key, value) in enumerate(data.items(), start=1):
                # Format as "key: value" for better semantic search
                value_str = json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)
                page_texts[i] = f"{key}: {value_str}"

        elif isinstance(data, list):
            # For arrays, chunk items (10 items per section)
            if not data:
                return {1: "[]"}

            items_per_page = 10
            for start_idx in range(0, len(data), items_per_page):
                page_num = (start_idx // items_per_page) + 1
                end_idx = min(start_idx + items_per_page, len(data))
                chunk = data[start_idx:end_idx]

                # Format each item
                lines = []
                for j, item in enumerate(chunk, start=start_idx):
                    item_str = json.dumps(item, indent=2) if isinstance(item, (dict, list)) else str(item)
                    lines.append(f"[{j}]: {item_str}")

                page_texts[page_num] = "\n\n".join(lines)
        else:
            # Primitive value
            page_texts[1] = str(data)

        return page_texts

    def _extract_jsonl(self, jsonl_path: Path) -> Dict[int, str]:
        """
        Extract text from JSON Lines file (one JSON object per line).

        Returns:
            Dictionary with sections (every ~10 lines = 1 section)
        """
        import json

        lines = []
        try:
            with open(jsonl_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # Format as JSON for readability
                        formatted = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
                        lines.append(f"[{line_num}]: {formatted}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping invalid JSON at line {line_num} in {jsonl_path.name}: {e}")
                        lines.append(f"[{line_num}]: {line}")
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 decoding failed for {jsonl_path.name}, trying latin-1")
            with open(jsonl_path, 'r', encoding='latin-1') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        formatted = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
                        lines.append(f"[{line_num}]: {formatted}")
                    except json.JSONDecodeError:
                        lines.append(f"[{line_num}]: {line}")

        if not lines:
            logger.warning(f"Empty JSONL file: {jsonl_path.name}")
            return {1: ""}

        # Group lines into sections (10 lines per section)
        items_per_page = 10
        page_texts = {}

        for start_idx in range(0, len(lines), items_per_page):
            page_num = (start_idx // items_per_page) + 1
            end_idx = min(start_idx + items_per_page, len(lines))
            page_texts[page_num] = "\n\n".join(lines[start_idx:end_idx])

        return page_texts

    def _extract_audio(self, audio_path: Path) -> ExtractionResult:
        """Transcribe an audio recording with Whisper and return it as text.

        Each ~4-minute chunk of the transcript becomes its own "page" so
        retrieval can surface the relevant portion of a long meeting instead
        of returning the entire recording as a single blob.
        """
        from config import settings
        from services.audio_transcriber import get_transcriber, format_transcript_with_timestamps

        transcriber = get_transcriber(
            model_size=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )

        language = settings.whisper_language or None
        result = transcriber.transcribe(audio_path, language=language)

        if not result.segments:
            logger.warning(f"No speech detected in {audio_path.name}")
            return ExtractionResult({1: ""}, method="whisper")

        # Group segments into ~4-minute pages so long meetings chunk nicely.
        page_seconds = 240
        pages: Dict[int, List[str]] = {}
        for seg in result.segments:
            page_num = int(seg["start"] // page_seconds) + 1
            mm = int(seg["start"] // 60)
            ss = int(seg["start"] % 60)
            line = f"[{mm:02d}:{ss:02d}] {seg['text']}"
            pages.setdefault(page_num, []).append(line)

        page_texts = {p: "\n".join(lines) for p, lines in sorted(pages.items())}

        logger.info(
            f"Transcribed {audio_path.name} into {len(page_texts)} page(s), "
            f"language={result.language}, duration={result.duration:.1f}s"
        )

        return ExtractionResult(page_texts, method="whisper")

    def get_page_count(self, file_path: Path) -> int:
        """
        Get the number of pages/sections in a document.

        Args:
            file_path: Path to the document file

        Returns:
            Number of pages/sections
        """
        try:
            page_texts = self.extract_text(file_path)
            return len(page_texts)
        except Exception as e:
            logger.error(f"Failed to get page count for {file_path.name}: {e}")
            return 0

    def _extract_code(self, code_path: Path) -> ExtractionResult:
        """
        Extract text from code files (Pascal/Delphi/Modula-2/Assembly).

        For code files, we return sections based on major code blocks
        (unit header, interface section, implementation section, etc.)
        This is for backward compatibility with the page-based extraction model.

        For symbol-aware extraction, use extract_code_chunks() instead.

        Returns:
            ExtractionResult with code sections
        """
        content, language, unit_name = self._code_extractor.extract_file(str(code_path))

        # Split code into logical sections for basic page-based indexing
        page_texts = {}
        lines = content.split('\n')

        # For now, use ~100 lines per "page" for code
        lines_per_page = 100
        for start_idx in range(0, len(lines), lines_per_page):
            page_num = (start_idx // lines_per_page) + 1
            end_idx = min(start_idx + lines_per_page, len(lines))
            page_texts[page_num] = '\n'.join(lines[start_idx:end_idx])

        return ExtractionResult(page_texts, method="text")

    def extract_code_chunks(
        self,
        file_path: Path,
        document_id: str,
    ) -> List[CodeChunk]:
        """
        Extract code file as symbol-aware chunks (v3.0 code indexing).

        This method provides intelligent code-aware chunking that preserves
        symbol boundaries (procedures, functions, classes, etc.) for
        better RAG performance with legacy codebases.

        Args:
            file_path: Path to code file
            document_id: Document ID to use for chunks

        Returns:
            List of CodeChunk objects with symbol metadata
        """
        if not is_code_file(str(file_path)):
            raise ValueError(f"Not a supported code file: {file_path}")

        content, language, unit_name = self._code_extractor.extract_file(str(file_path))
        if not content.strip():
            raise ValueError(f"Code file {file_path.name} is empty")

        chunks = self._code_extractor.chunk_code(
            content=content,
            document_id=document_id,
            filename=file_path.name,
            language=language,
            unit_name=unit_name,
        )

        logger.info(
            f"Extracted {len(chunks)} chunks from code file {file_path.name} "
            f"(language: {language.value}, unit: {unit_name or 'N/A'})"
        )

        return chunks

    def is_code_file(self, file_path: Union[str, Path]) -> bool:
        """Check if a file is a supported code file."""
        return is_code_file(str(file_path))

    @staticmethod
    def is_supported_filename(name: Union[str, Path]) -> bool:
        """Whether a filename can be indexed, by suffix or well-known basename."""
        return is_supported_filename(name)


def is_supported_filename(name: Union[str, Path]) -> bool:
    """Whether a file can be indexed, judged by its name alone.

    True for every suffix in ``DocumentExtractor.SUPPORTED_EXTENSIONS`` and
    for the extension-less code files recognised by basename (``Makefile``,
    ``Dockerfile``, ``CMakeLists.txt``, ``.gitignore``, ...). ``.env`` is
    not supported: it holds secrets. Upload gates should use this instead
    of a bare suffix lookup.
    """
    path = Path(str(name))
    if path.suffix.lower() in DocumentExtractor.SUPPORTED_EXTENSIONS:
        return True
    return is_code_file(path.name)

