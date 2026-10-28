"""Custom file types: index JSON-like files that carry a product-specific extension.

A *profile* is declarative data: how to parse the file, where its records are,
which fields are worth indexing, and how to label them. It holds no code, so a
profile an admin writes (or a model proposes) can be read in full before it is
saved and can do nothing except change which text reaches the index. Built-in
types always win, and the text a profile produces still goes through the
injection and content-policy scans like any other source.

Profiles live in ``<data_dir>/custom_file_types.json``: one file, so it is
backed up with the data directory, can be shipped by configuration management,
and is picked up by every worker process without a restart.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
MAX_FILE_BYTES = 100 * 1024 * 1024
FORMATS = ("auto", "json", "jsonl", "text")
KINDS = ("data", "docs", "code")
SOURCES = ("manual", "ai", "heuristic")

_EXT_RE = re.compile(r"^\.[a-z0-9][a-z0-9_+\-]{0,19}$")
# Executables and key material are never indexable, whatever a profile says.
_FORBIDDEN_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".msi", ".sys", ".bin", ".com", ".scr",
    ".env", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx",
})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ProfileError(ValueError):
    """A profile, an extension or a file cannot be accepted; the message says why."""


# ── lenient parsing ────────────────────────────────────────────────────

def _clean_jsonc(text: str) -> str:
    """Blank out comments and trailing commas, leaving strings and offsets intact.

    Everything replaced becomes a space (newlines survive), so a parse error
    reported against the cleaned text points at the same line and column as in
    the file the person is looking at.
    """
    out: List[str] = list(text)
    n = len(text)
    i = 0
    in_string = False
    last_sig = -1                       # index of the last significant (non-space) output char
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
                last_sig = i
            i += 1
            continue
        if c == '"':
            in_string = True
            last_sig = i
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        if c in "]}" and last_sig >= 0 and out[last_sig] == ",":
            out[last_sig] = " "
        if not c.isspace():
            last_sig = i
        i += 1
    return "".join(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, max(0, pos)) + 1


def parse_lenient(text: str, fmt: str = "auto") -> Any:
    """Parse JSON, JSON with comments / trailing commas, or a stream of JSON values.

    ``auto`` and ``json`` accept a single document or several concatenated
    values (JSON Lines is the common case); ``jsonl`` reads one value per line
    and keeps an unparseable line as ``{"_raw": line}`` rather than dropping it.
    """
    text = text.lstrip("\ufeff")
    if fmt == "jsonl":
        records: List[Any] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(json.loads(_clean_jsonc(line)))
            except (ValueError, RecursionError):
                records.append({"_raw": line})
        return records

    cleaned = _clean_jsonc(text)
    decoder = json.JSONDecoder()
    values: List[Any] = []
    pos = 0
    n = len(cleaned)
    try:
        while True:
            while pos < n and cleaned[pos].isspace():
                pos += 1
            if pos >= n:
                break
            value, pos = decoder.raw_decode(cleaned, pos)
            values.append(value)
    except RecursionError:
        raise ProfileError("The file nests too deeply to read") from None
    except json.JSONDecodeError as e:
        raise ProfileError(f"Not valid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from None
    if not values:
        raise ProfileError("The file is empty")
    if len(values) == 1:
        return values[0]
    if fmt == "json":
        raise ProfileError("Expected one JSON document but found several; use the jsonl format")
    return values


def read_text(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ProfileError(
            f"{path.name} is {size // (1024 * 1024)} MB; custom file types are limited to "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB"
        )
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ── profiles ───────────────────────────────────────────────────────────

def _builtin_extensions() -> frozenset:
    from services.code_extractor import SUPPORTED_CODE_EXTENSIONS
    from services.document_extractor import DocumentExtractor
    return frozenset(DocumentExtractor.SUPPORTED_EXTENSIONS) | frozenset(SUPPORTED_CODE_EXTENSIONS)


def normalize_extension(ext: Any) -> str:
    if not isinstance(ext, str):
        raise ProfileError("The extension must be text such as .acme")
    ext = ext.strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    if not _EXT_RE.match(ext):
        raise ProfileError(
            f"'{ext}' is not a usable extension: use a dot followed by 1-20 letters, digits, "
            f"'_', '+' or '-' (for example .acme)"
        )
    if ext in _builtin_extensions():
        raise ProfileError(f"{ext} is a built-in type and cannot be redefined")
    if ext in _FORBIDDEN_EXTENSIONS:
        raise ProfileError(f"{ext} files are never indexed (executables and key material)")
    return ext


def _text_field(raw: Dict[str, Any], key: str, limit: int, default: str = "") -> str:
    value = raw.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ProfileError(f"'{key}' must be text")
    value = value.strip()
    if len(value) > limit:
        raise ProfileError(f"'{key}' is longer than {limit} characters")
    if _CONTROL_RE.search(value):
        raise ProfileError(f"'{key}' contains control characters")
    return value


def _pattern_list(raw: Dict[str, Any], key: str) -> List[str]:
    value = raw.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProfileError(f"'{key}' must be a list of field names")
    if len(value) > 100:
        raise ProfileError(f"'{key}' has more than 100 entries")
    out: List[str] = []
    for v in value:
        v = v.strip()
        if not v:
            continue
        if len(v) > 200 or _CONTROL_RE.search(v):
            raise ProfileError(f"'{key}' contains an invalid field name")
        if v not in out:
            out.append(v)
    return out


def _bounded_int(raw: Dict[str, Any], key: str, lo: int, hi: int, default: int) -> int:
    value = raw.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ProfileError(f"'{key}' must be a whole number from {lo} to {hi}")
    return value


def validate_profile(extension: str, raw: Any) -> Dict[str, Any]:
    """Return the canonical profile for `raw`, or raise ProfileError.

    Unknown keys are dropped so a hand-edited or model-written profile cannot
    smuggle anything into the registry file.
    """
    ext = normalize_extension(extension)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ProfileError("A profile must be an object")

    fmt = raw.get("format", "auto")
    if fmt not in FORMATS:
        raise ProfileError(f"'format' must be one of {', '.join(FORMATS)}")
    kind = raw.get("kind", "data")
    if kind not in KINDS:
        raise ProfileError(f"'kind' must be one of {', '.join(KINDS)}")
    source = raw.get("source", "manual")
    if source not in SOURCES:
        raise ProfileError(f"'source' must be one of {', '.join(SOURCES)}")

    labels_raw = raw.get("labels", {})
    if labels_raw is None:
        labels_raw = {}
    if not isinstance(labels_raw, dict) or len(labels_raw) > 200:
        raise ProfileError("'labels' must map field names to display names (at most 200)")
    labels: Dict[str, str] = {}
    for k, v in labels_raw.items():
        if not isinstance(k, str) or not isinstance(v, str) or not k.strip() or len(k) > 200 \
                or len(v) > 80 or _CONTROL_RE.search(k) or _CONTROL_RE.search(v):
            raise ProfileError("'labels' must map field names to display names (at most 80 characters)")
        labels[k.strip()] = v.strip()

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ProfileError("'enabled' must be true or false")

    return {
        "extension": ext,
        "label": _text_field(raw, "label", 80) or ext,
        "description": _text_field(raw, "description", 500),
        "format": fmt,
        "kind": kind,
        "record_path": _text_field(raw, "record_path", 200),
        "title_field": _text_field(raw, "title_field", 200),
        "include": _pattern_list(raw, "include"),
        "exclude": _pattern_list(raw, "exclude"),
        "labels": labels,
        "records_per_section": _bounded_int(raw, "records_per_section", 1, 100, 10),
        "max_value_chars": _bounded_int(raw, "max_value_chars", 50, 20000, 2000),
        "enabled": enabled,
        "source": source,
        "updated_at": _text_field(raw, "updated_at", 40),
        "updated_by": _text_field(raw, "updated_by", 200),
        "reading_updated_at": _text_field(raw, "reading_updated_at", 40),
    }


# The fields that decide what text a file produces. Everything else on a
# profile (label, description, kind, source, who/when) can change without
# making an already-indexed file out of date.
READING_FIELDS = (
    "format", "record_path", "title_field", "include", "exclude", "labels",
    "records_per_section", "max_value_chars", "enabled",
)


def reading_changed(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> bool:
    """Whether `new` reads files differently from `old` (None means a new type)."""
    if old is None:
        return True
    return any(old.get(k) != new.get(k) for k in READING_FIELDS)


# ── rendering ──────────────────────────────────────────────────────────

Field = Tuple[str, str, str]            # (display path, pattern path, value text)


def _scalar_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip() if not isinstance(value, str) else value.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _flatten(value: Any, display: str, pattern: str, limit: int, out: List[Field], depth: int = 0) -> None:
    if depth > 40:
        out.append((display, pattern, _scalar_text(json.dumps(value, default=str), limit)))
        return
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k)
            _flatten(v, f"{display}.{key}" if display else key,
                     f"{pattern}.{key}" if pattern else key, limit, out, depth + 1)
    elif isinstance(value, list):
        if all(_is_scalar(v) for v in value):
            text = ", ".join(t for t in (_scalar_text(v, limit) for v in value) if t)
            if text:
                out.append((display, pattern, text[:limit] + ("…" if len(text) > limit else "")))
        else:
            for i, item in enumerate(value, start=1):
                _flatten(item, f"{display}[{i}]", pattern, limit, out, depth + 1)
    else:
        text = _scalar_text(value, limit)
        if text:
            out.append((display, pattern, text))


def _matches(pattern_path: str, patterns: List[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(pattern_path, p) or pattern_path.startswith(p + ".")
        for p in patterns
    )


def _lookup(record: Any, dotted: str) -> Any:
    cur = record
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _render_record(record: Any, profile: Dict[str, Any], title: Optional[str] = None) -> str:
    limit = profile["max_value_chars"]
    if _is_scalar(record):
        return _scalar_text(record, limit)

    heading = title or ""
    title_field = profile["title_field"]
    if title_field and not heading:
        found = _lookup(record, title_field)
        if _is_scalar(found):
            heading = _scalar_text(found, limit)

    fields: List[Field] = []
    _flatten(record, "", "", limit, fields)
    include, exclude, labels = profile["include"], profile["exclude"], profile["labels"]
    lines: List[str] = []
    for display, pattern, text in fields:
        if title_field and pattern == title_field:
            continue
        if include and not _matches(pattern, include):
            continue
        if exclude and _matches(pattern, exclude):
            continue
        name = display
        if pattern in labels:
            name = labels[pattern] + "".join(re.findall(r"\[\d+\]", display))
        lines.append(f"{name}: {text}")
    if heading:
        lines.insert(0, heading)
    return "\n".join(lines)


def _navigate(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise ProfileError(f"record_path '{dotted}' was not found in the file (stopped at '{part}')")
    return cur


def _find_dominant_list(data: Dict[str, Any]) -> Optional[str]:
    """Dotted path of the largest list of objects within three levels, if any."""
    best: Tuple[int, str] = (1, "")

    def walk(node: Any, path: str, depth: int) -> None:
        nonlocal best
        if depth > 3 or not isinstance(node, dict):
            return
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, list) and len(v) > best[0] and all(isinstance(x, dict) for x in v):
                best = (len(v), p)
            elif isinstance(v, dict):
                walk(v, p, depth + 1)

    walk(data, "", 1)
    return best[1] or None


def _without(data: Dict[str, Any], dotted: str) -> Dict[str, Any]:
    head, _, rest = dotted.partition(".")
    out = {k: v for k, v in data.items() if k != head}
    if rest and isinstance(data.get(head), dict):
        out[head] = _without(data[head], rest)
        if not out[head]:
            del out[head]
    return out


def render(data: Any, profile: Dict[str, Any]) -> Dict[int, str]:
    """Turn parsed content into ``{section number: text}`` per the profile."""
    return render_with_count(data, profile)[0]


def render_with_count(data: Any, profile: Dict[str, Any]) -> Tuple[Dict[int, str], int]:
    """Like `render`, also returning how many records went into the sections."""
    if isinstance(data, str):
        return {1: data}, 1
    if data == {} or data == []:
        return {1: json.dumps(data)}, 0

    entries: List[str] = []
    per = profile["records_per_section"]
    if profile["record_path"]:
        target = _navigate(data, profile["record_path"])
        if isinstance(target, dict):
            entries = [_render_record(v, profile, title=str(k)) for k, v in target.items()]
        elif isinstance(target, list):
            entries = [_render_record(v, profile) for v in target]
        else:
            entries = [_render_record(target, profile)]
    elif isinstance(data, list):
        entries = [_render_record(v, profile) for v in data]
    elif isinstance(data, dict):
        path = _find_dominant_list(data)
        if path:
            header = _render_record(_without(data, path), profile)
            entries = ([header] if header else []) + [
                _render_record(v, profile) for v in _navigate(data, path)]
        else:
            # A settings-style object: each top-level key reads best as its own section.
            per = 1
            entries = [_render_record(v, profile, title=str(k)) if not _is_scalar(v)
                       else f"{k}: {_scalar_text(v, profile['max_value_chars'])}"
                       for k, v in data.items()]
    else:
        entries = [_scalar_text(data, profile["max_value_chars"])]

    entries = [e for e in entries if e.strip()]
    if not entries:
        raise ProfileError(
            "Nothing is left to index: the include/exclude rules remove every field, "
            "or the file holds no values"
        )
    pages = {
        (i // per) + 1: "\n\n".join(entries[i:i + per])
        for i in range(0, len(entries), per)
    }
    return pages, len(entries)


def extract_pages(path: Path, profile: Dict[str, Any]) -> Dict[int, str]:
    """Read `path` and return its sections. Raises ProfileError with a reason."""
    text = read_text(path)
    if profile["format"] == "text":
        return {1: text}
    return render(parse_lenient(text, profile["format"]), profile)


# ── registry ───────────────────────────────────────────────────────────

_ABSENT: Tuple[int, int] = (-1, -1)     # "no registry file", distinct from never having looked


class CustomFileTypeRegistry:
    """Profiles on disk, read lazily and shared across processes by mtime."""

    def __init__(self, path_fn: Callable[[], Path], ttl: float = 1.0):
        self._path_fn = path_fn
        self._ttl = ttl
        self._lock = threading.Lock()
        self._stamp: Optional[Tuple[int, int]] = None
        self._types: Dict[str, Dict[str, Any]] = {}
        self._checked = 0.0

    @property
    def path(self) -> Path:
        return Path(self._path_fn())

    def _load(self) -> Dict[str, Dict[str, Any]]:
        now = time.monotonic()
        if self._ttl and self._stamp is not None and now - self._checked < self._ttl:
            return self._types
        self._checked = now
        path = self.path
        try:
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = _ABSENT
        if stamp == self._stamp:
            return self._types
        types: Dict[str, Dict[str, Any]] = {}
        if stamp != _ABSENT:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                entries = doc.get("types", {}) if isinstance(doc, dict) else {}
                for ext, raw in entries.items():
                    try:
                        profile = validate_profile(ext, raw)
                    except ProfileError as e:
                        logger.warning(f"Ignoring custom file type {ext!r} in {path}: {e}")
                        continue
                    types[profile["extension"]] = profile
            except (OSError, ValueError) as e:
                logger.warning(f"Could not read {path}; custom file types are off until it is fixed: {e}")
        self._types, self._stamp = types, stamp
        return self._types

    def all(self) -> List[Dict[str, Any]]:
        return [dict(self._load()[k]) for k in sorted(self._load())]

    def get(self, ext: str) -> Optional[Dict[str, Any]]:
        try:
            ext = normalize_extension(ext)
        except ProfileError:
            return None
        profile = self._load().get(ext)
        return dict(profile) if profile else None

    def lookup(self, filename: str, include_disabled: bool = False) -> Optional[Dict[str, Any]]:
        """The enabled profile for a filename, judged by its last suffix.

        ``include_disabled`` also returns a switched-off profile, for code that
        describes files already indexed (their kind does not change because
        the type stopped accepting new uploads).
        """
        suffix = Path(str(filename)).suffix.lower()
        if not suffix:
            return None
        profile = self._load().get(suffix)
        if profile is None or not (profile["enabled"] or include_disabled):
            return None
        return dict(profile)

    def extensions(self, kind: Optional[str] = None, include_disabled: bool = False) -> set:
        return {
            ext for ext, p in self._load().items()
            if (p["enabled"] or include_disabled) and (kind is None or p["kind"] == kind)
        }

    def _write(self, types: Dict[str, Dict[str, Any]]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": REGISTRY_VERSION, "types": {k: types[k] for k in sorted(types)}}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cft-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._stamp = None
        self._checked = 0.0

    def put(self, ext: str, raw: Any, user: Optional[str] = None) -> Dict[str, Any]:
        profile = validate_profile(ext, raw)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        profile["updated_at"] = now
        profile["updated_by"] = user or ""
        with self._lock:
            self._stamp, self._checked = None, 0.0
            types = dict(self._load())
            previous = types.get(profile["extension"])
            # Moves only when the text this profile produces changes, so the
            # UI can tell files indexed before that point are out of date.
            if previous is None or reading_changed(previous, profile):
                profile["reading_updated_at"] = now
            else:
                profile["reading_updated_at"] = (
                    previous.get("reading_updated_at") or previous.get("updated_at") or now)
            types[profile["extension"]] = profile
            self._write(types)
        return dict(profile)

    def delete(self, ext: str) -> bool:
        ext = normalize_extension(ext)
        with self._lock:
            self._stamp, self._checked = None, 0.0
            types = dict(self._load())
            if ext not in types:
                return False
            del types[ext]
            self._write(types)
        return True


def _data_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "custom_file_types.json"


registry = CustomFileTypeRegistry(_data_path)
