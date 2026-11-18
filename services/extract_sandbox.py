"""Document parsers in a helper process, with a deadline and a memory cap.

python-docx, python-pptx, openpyxl (through pandas), the ODF/EPUB readers,
pdfium/pdfplumber and the PRA model readers run on files that anyone with
upload rights chooses. Inside the server a pathological file held an
extraction thread for minutes, took gigabytes, or (a native bug) could take
the whole process down. Here each of those parses runs in a helper process,
one file at a time:

* the server waits at most ``EXTRACT_TIMEOUT_SECONDS`` for the answer, then
  kills the helper;
* on Linux the helper's address space is capped at ``EXTRACT_MEMORY_LIMIT_MB``
  beyond what it needed to start, so a file that balloons gets a MemoryError
  in the helper rather than the OOM killer in the server;
* a helper that crashes is noticed by its closed pipe.

Each of those fails the one file with :class:`ExtractionLimitExceeded`, which
the upload job reports like any other per-file error, and the next file gets a
fresh helper.

Helpers are ``python -m services.extract_sandbox`` started with ``subprocess``,
not ``fork``: the server has threads (and native libraries) that do not
survive a fork, and a fresh interpreter never re-imports the server's own
``main`` module. Starting one costs about a second of imports, so a helper is
reused for up to ``_MAX_TASKS_PER_HELPER`` files and exits on its own after
``_IDLE_SECONDS`` without work. Requests and answers are JSON lines: the helper
parses hostile input, so what it sends back is decoded as data, never
unpickled.

Three kinds of helper, each with limits of its own, because each kind of work
needs different room:

* ``parse``: the document parsers above and figure extraction
  (``EXTRACT_TIMEOUT_SECONDS``, ``EXTRACT_MEMORY_LIMIT_MB``);
* ``ocr``: the local OCR engines, Docling and Tesseract, which take minutes on
  a long scan (``OCR_TIMEOUT_SECONDS``, ``OCR_MEMORY_LIMIT_MB``);
* ``audio``: Whisper transcription, which runs for about as long as a
  recording on a CPU and keeps its model loaded between files
  (``TRANSCRIBE_TIMEOUT_SECONDS``, ``TRANSCRIBE_MEMORY_LIMIT_MB``).

The vision OCR engine stays in the server: it is a call to a model endpoint,
and the page images it sends are rendered by poppler's own process.

``EXTRACT_ISOLATION=false`` parses in-process, as before; so does a platform
where the helper cannot start (logged once).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAX_TASKS_PER_HELPER = 200
_IDLE_SECONDS = 300
# The server stops handing work to a helper a little before it would exit idle.
_IDLE_MARGIN_SECONDS = 30
_STARTUP_TIMEOUT_SECONDS = 60
# After a helper fails to start, parse in-process for this long before retrying.
_UNAVAILABLE_BACKOFF_SECONDS = 300


def _unreadable_file_errors() -> dict:
    """Exception types that mean "this file cannot be read", by class name.

    The helper reports an error as its class name and message; these are
    rebuilt as themselves so callers can tell a bad file from a bug.
    """
    from services.format_readers import ArchiveLimitError
    from services.pra.readers.base import PraFormatError

    return {"ArchiveLimitError": ArchiveLimitError, "PraFormatError": PraFormatError}


def _rebuilt_errors() -> dict:
    """Every error type an answer may name and the server rebuilds as itself.

    The unreadable-file types above, plus the server's own reasons a task
    could not run (a transcription model that is not on this server), so a
    caller handles them exactly as when the work ran in-process.
    """
    from services.audio_transcriber import TranscriptionModelUnavailable

    return {**_unreadable_file_errors(), "TranscriptionModelUnavailable": TranscriptionModelUnavailable,
            "ValueError": ValueError, "ImportError": ImportError, "RuntimeError": RuntimeError}


# What each kind of helper is for, and the settings that limit it.
KINDS = {
    "parse": {"what": "Reading", "timeout": "extract_timeout_seconds", "memory": "extract_memory_limit_mb",
              "timeout_name": "EXTRACT_TIMEOUT_SECONDS", "memory_name": "EXTRACT_MEMORY_LIMIT_MB"},
    "ocr": {"what": "Running OCR on", "timeout": "ocr_timeout_seconds", "memory": "ocr_memory_limit_mb",
            "timeout_name": "OCR_TIMEOUT_SECONDS", "memory_name": "OCR_MEMORY_LIMIT_MB"},
    "audio": {"what": "Transcribing", "timeout": "transcribe_timeout_seconds", "memory": "transcribe_memory_limit_mb",
              "timeout_name": "TRANSCRIBE_TIMEOUT_SECONDS", "memory_name": "TRANSCRIBE_MEMORY_LIMIT_MB"},
}
# Modules each kind loads before its memory cap is set, so they count as the
# helper's baseline rather than against the file.
_PRELOAD = {
    "parse": ("docx", "pptx", "pandas", "openpyxl", "pypdfium2", "pdfplumber", "bs4", "PIL"),
    "ocr": ("pdf2image", "pytesseract", "PIL", "pypdfium2", "docling.document_converter"),
    "audio": ("faster_whisper", "ctranslate2", "av"),
}
# A test seam: the module this environment variable names may add tasks
# (a dict TASKS of name -> function(extractor, path, args)). Unset outside tests.
TEST_TASKS_ENV = "CLIO_EXTRACT_TEST_TASKS"


class ExtractionLimitExceeded(ValueError):
    """A file whose parse ran out of time or memory, or crashed its parser."""

    code = "extraction_limit"


# ---------------------------------------------------------------------------
# Tasks (run inside the helper)
# ---------------------------------------------------------------------------

def _pages(result: Dict[int, str]) -> Dict[str, str]:
    return {str(k): v for k, v in result.items()}


def _figure_to_json(figure) -> Dict[str, Any]:
    import base64

    return {"page_number": figure.page_number, "position": figure.position,
            "data": base64.b64encode(figure.data).decode("ascii"), "ext": figure.ext,
            "width": figure.width, "height": figure.height, "caption": figure.caption,
            "content_hash": figure.content_hash, "description": figure.description}


def _figure_from_json(data: Dict[str, Any]):
    import base64

    from services.figure_extractor import ExtractedFigure

    return ExtractedFigure(
        page_number=int(data["page_number"]), position=int(data["position"]),
        data=base64.b64decode(data["data"]), ext=str(data["ext"]), width=int(data["width"]),
        height=int(data["height"]), caption=str(data.get("caption") or ""),
        content_hash=str(data.get("content_hash") or ""), description=data.get("description"),
    )


def _extra_tasks() -> Dict[str, Callable]:
    module = os.environ.get(TEST_TASKS_ENV)
    if not module:
        return {}
    import importlib

    return dict(getattr(importlib.import_module(module), "TASKS", {}))


def _run_task(extractor, task: str, path: Path, args: Optional[Dict[str, Any]] = None) -> Any:
    args = args or {}
    extra = _extra_tasks().get(task)
    if extra is not None:  # tests only (TEST_TASKS_ENV): may stand in for a real task
        return extra(extractor, path, args)
    if task == "figures":
        from services.figure_extractor import extract_figures

        page_texts = {int(k): v for k, v in (args.get("page_texts") or {}).items()} or None
        found = extract_figures(path, args.get("source_format"), page_texts,
                                fetch_remote=bool(args.get("fetch_remote")))
        return [_figure_to_json(f) for f in found]
    if task == "ocr_local":
        return _pages(extractor._local_ocr_here(path, pages=args.get("pages")))
    if task == "transcribe":
        from services.audio_transcriber import get_transcriber

        transcriber = get_transcriber(model_size=args["model_size"], device=args["device"],
                                      compute_type=args["compute_type"])
        result = transcriber.transcribe(path, language=args.get("language"))
        return {"text": result.text, "segments": result.segments, "language": result.language,
                "duration": result.duration}
    if task == "docx":
        return _pages(extractor._extract_docx(path))
    if task == "pptx":
        return _pages(extractor._extract_pptx(path))
    if task == "excel_pages":
        return _pages(extractor._extract_xlsx(path))
    if task == "excel_sheets":
        return extractor._excel_sheets(path)
    if task == "document":
        return _pages(extractor._extract_document_format(path, path.suffix.lower()))
    if task == "pdf_native":
        return _pages(extractor._extract_pdf_native(path))
    if task == "pra":
        from services.pra.readers import read_pra_file

        return read_pra_file(path).to_dict()
    raise ValueError(f"Unknown extraction task: {task}")


# How the server turns each task's JSON back into what the extractor returns.
_PAGE_TASKS = {"docx", "pptx", "excel_pages", "document", "pdf_native", "ocr_local"}


def _decode(task: str, result: Any) -> Any:
    if task in _PAGE_TASKS:
        return {int(k): v for k, v in result.items()}
    if task == "figures":
        return [_figure_from_json(f) for f in result]
    if task == "transcribe":
        from services.audio_transcriber import TranscriptionResult

        return TranscriptionResult(text=result["text"], segments=result["segments"],
                                   language=result["language"], duration=float(result["duration"]))
    if task == "pra":
        from services.pra.model import PraModel

        return PraModel.from_dict(result)
    return result


def _address_space_bytes() -> Optional[int]:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def _peak_address_space_bytes() -> Optional[int]:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmPeak:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _cap_memory(limit_mb: int) -> Optional[int]:
    """RLIMIT_AS at what the helper uses now plus ``limit_mb`` (Linux only).

    Returns the cap in bytes, or None when there is none.
    """
    if limit_mb <= 0:
        return None
    try:
        import resource
    except ImportError:  # Windows
        return None
    current = _address_space_bytes()
    if current is None:  # no /proc (macOS ignores RLIMIT_AS anyway)
        return None
    cap = current + limit_mb * 1024 * 1024
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            cap = min(cap, hard)
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    except (ValueError, OSError) as e:
        logger.warning("Extraction helper could not cap its memory: %s", e)
        return None
    return cap


def _near_cap(cap: Optional[int]) -> bool:
    """Whether this helper has come close to its memory cap.

    Not every parser turns a failed allocation into MemoryError (lxml reports
    "unknown error"), so a failure is judged by how much address space the
    helper reached. A helper that came close is retired even after a success,
    so the next file's failure cannot be blamed on this one's peak.
    """
    peak = _peak_address_space_bytes()
    return cap is not None and peak is not None and peak >= cap * 0.9


def _helper_main(argv: List[str]) -> None:
    """The helper's loop: one JSON request per line in, one answer per line out."""
    limit_mb = int(argv[0]) if argv else 0
    kind = argv[1] if len(argv) > 1 else "parse"
    # The answers own the real stdout; anything a parser prints goes to stderr.
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    from services.document_extractor import DocumentExtractor

    for module in _PRELOAD.get(kind, ()):
        try:
            __import__(module)  # loaded before the cap, so it counts as baseline
        except Exception:  # noqa: BLE001 - an optional engine that is absent or broken
            pass
    extractor = DocumentExtractor(enable_ocr=False)
    cap = _cap_memory(limit_mb)

    def answer(message: dict) -> None:
        out.write(json.dumps(message).encode("utf-8") + b"\n")
        out.flush()

    answer({"ready": True})
    stdin = sys.stdin.buffer
    while True:
        if os.name == "posix":
            import select

            readable, _, _ = select.select([stdin], [], [], _IDLE_SECONDS)
            if not readable:
                return  # idle; the server starts a new helper when it needs one
        line = stdin.readline()
        if not line:
            return  # the server went away
        request = json.loads(line)
        try:
            result = _run_task(extractor, request["task"], Path(request["path"]), request.get("args"))
            retire = _near_cap(cap)  # said in the answer, so the server does not reuse it
            answer({"ok": True, "result": result, "retire": retire})
            if retire:
                return
        except MemoryError:
            answer({"ok": False, "type": "MemoryError", "error": "out of memory"})
            return  # a parser that hit the cap may have left state half-built
        except BaseException as e:  # noqa: BLE001 - every failure goes back to the server
            if _near_cap(cap):
                answer({"ok": False, "type": "MemoryError", "error": str(e)})
                return
            answer({"ok": False, "type": _error_name(e), "error": str(e)})


def _error_name(e: BaseException) -> str:
    """The name the server rebuilds this error as: its own, or the nearest it knows."""
    known = _rebuilt_errors()
    if type(e).__name__ in known:
        return type(e).__name__
    for name in ("ValueError", "ImportError", "RuntimeError"):
        if isinstance(e, known[name]):
            return name
    return "Exception"


# ---------------------------------------------------------------------------
# The server side
# ---------------------------------------------------------------------------

class _Helper:
    def __init__(self, memory_limit_mb: int, kind: str = "parse"):
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "services.extract_sandbox", str(memory_limit_mb), kind],
            cwd=str(_REPO_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, **extra,
        )
        self.memory_limit_mb = memory_limit_mb
        self.kind = kind
        self.tasks = 0
        self.last_used = time.monotonic()
        ready = self._read(_STARTUP_TIMEOUT_SECONDS)
        if ready != b'{"ready": true}\n':
            self.kill()
            raise OSError("the extraction helper did not start")

    def _read(self, timeout: Optional[float]) -> Optional[bytes]:
        """One answer line; None on timeout. b"" means the helper exited."""
        box: "queue.Queue[bytes]" = queue.Queue(maxsize=1)
        threading.Thread(
            target=lambda: box.put(self.proc.stdout.readline()), daemon=True,
            name="extract-helper-read",
        ).start()
        try:
            return box.get(timeout=timeout)
        except queue.Empty:
            return None

    def call(self, task: str, path: Path, timeout: Optional[float], args: Optional[dict] = None) -> dict:
        self.tasks += 1
        request = json.dumps({"task": task, "path": str(path), "args": args or {}}).encode("utf-8") + b"\n"
        try:
            self.proc.stdin.write(request)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return {"exited": True}
        line = self._read(timeout)
        self.last_used = time.monotonic()
        if line is None:
            return {"timeout": True}
        if not line:
            return {"exited": True}
        try:
            reply = json.loads(line)
        except ValueError:
            reply = None
        return reply if isinstance(reply, dict) else {"exited": True}

    def usable(self) -> bool:
        return (
            self.proc.poll() is None
            and self.tasks < _MAX_TASKS_PER_HELPER
            and time.monotonic() - self.last_used < _IDLE_SECONDS - _IDLE_MARGIN_SECONDS
        )

    def exit_code(self) -> Optional[int]:
        try:
            return self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - already gone
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - a request left unsent in a dead pipe
                pass

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # the helper exits on end of input
        except Exception:  # noqa: BLE001
            pass


class _Pool:
    """Helpers handed out one file at a time, at most ``size()`` at once."""

    def __init__(self):
        self._lock = threading.Condition()
        self._idle: List[_Helper] = []
        self._busy = 0
        self._unavailable_until = 0.0

    @staticmethod
    def size() -> int:
        import config

        return max(2, config.settings.bulk_extract_workers)

    def available(self) -> bool:
        return time.monotonic() >= self._unavailable_until

    def acquire(self, memory_limit_mb: int, kind: str = "parse") -> Optional[_Helper]:
        with self._lock:
            while True:
                for helper in list(self._idle):
                    if not helper.usable():
                        self._idle.remove(helper)
                        helper.close()
                    elif helper.memory_limit_mb == memory_limit_mb and helper.kind == kind:
                        self._idle.remove(helper)
                        self._busy += 1
                        return helper
                if self._idle and self._busy + len(self._idle) >= self.size():
                    self._idle.pop(0).close()  # make room: a helper of another kind
                    continue
                if self._busy < self.size():
                    self._busy += 1
                    break
                self._lock.wait()
        try:
            return _Helper(memory_limit_mb, kind)
        except OSError as e:
            with self._lock:
                self._busy -= 1
                self._unavailable_until = time.monotonic() + _UNAVAILABLE_BACKOFF_SECONDS
                self._lock.notify()
            logger.warning(f"Extraction helper unavailable ({e}); parsing in-process for now")
            return None

    def release(self, helper: _Helper, keep: bool) -> None:
        if not keep:
            helper.kill()
        with self._lock:
            self._busy -= 1
            if keep and helper.usable():
                self._idle.append(helper)
            elif keep:
                helper.close()
            self._lock.notify()

    def shutdown(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for helper in idle:
            helper.close()


_pool = _Pool()
atexit.register(_pool.shutdown)


def run(task: str, path: Path, in_process: Callable[[], T], *, args: Optional[dict] = None,
        kind: str = "parse") -> T:
    """``task`` on ``path`` in a helper of ``kind``, or ``in_process()`` when isolation is off.

    ``args`` travel to the helper as JSON. The kind's own settings (``KINDS``)
    limit the helper's time and memory.
    """
    import config

    settings = config.settings
    limits = KINDS[kind]
    if not settings.extract_isolation or not _pool.available():
        return in_process()
    helper = _pool.acquire(max(0, int(getattr(settings, limits["memory"]) or 0)), kind)
    if helper is None:
        return in_process()
    name = Path(path).name
    timeout = int(getattr(settings, limits["timeout"]) or 0) or None
    keep = False
    try:
        reply = helper.call(task, Path(path).resolve(), timeout, args)
        if reply.get("timeout"):
            raise ExtractionLimitExceeded(
                f"{limits['what']} {name} took longer than {timeout:g} seconds, so it was stopped "
                f"({limits['timeout_name']})."
            )
        if reply.get("exited"):
            code = helper.exit_code()
            raise ExtractionLimitExceeded(
                f"The parser stopped while reading {name} (exit code {code}); the file is "
                f"damaged or was built to crash it."
            )
        if reply.get("ok"):
            result = _decode(task, reply.get("result"))
            keep = not reply.get("retire")
            return result
        error_kind, error = reply.get("type"), reply.get("error") or "unknown error"
        if error_kind == "MemoryError":
            raise ExtractionLimitExceeded(
                f"{limits['what']} {name} needs more than {helper.memory_limit_mb} MB of memory, so it "
                f"was stopped ({limits['memory_name']})."
            )
        keep = True  # an ordinary error leaves the helper fine
        # Rebuilt as its own type: a file at fault stays a 422 rather than a
        # server failure, and a missing model stays a missing model.
        raise _rebuilt_errors().get(error_kind, Exception)(error)
    finally:
        _pool.release(helper, keep)


if __name__ == "__main__":
    _helper_main(sys.argv[1:])
