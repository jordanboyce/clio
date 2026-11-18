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


class ExtractionLimitExceeded(ValueError):
    """A file whose parse ran out of time or memory, or crashed its parser."""

    code = "extraction_limit"


# ---------------------------------------------------------------------------
# Tasks (run inside the helper)
# ---------------------------------------------------------------------------

def _pages(result: Dict[int, str]) -> Dict[str, str]:
    return {str(k): v for k, v in result.items()}


def _run_task(extractor, task: str, path: Path) -> Any:
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
_PAGE_TASKS = {"docx", "pptx", "excel_pages", "document", "pdf_native"}


def _decode(task: str, result: Any) -> Any:
    if task in _PAGE_TASKS:
        return {int(k): v for k, v in result.items()}
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
    # The answers own the real stdout; anything a parser prints goes to stderr.
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    from services.document_extractor import DocumentExtractor

    for module in ("docx", "pptx", "pandas", "openpyxl", "pypdfium2", "pdfplumber", "bs4"):
        try:
            __import__(module)  # loaded before the cap, so it counts as baseline
        except ImportError:
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
            result = _run_task(extractor, request["task"], Path(request["path"]))
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
            if type(e).__name__ in _unreadable_file_errors():
                kind = type(e).__name__  # rebuilt as itself on the server side
            else:
                kind = "ValueError" if isinstance(e, ValueError) else (
                    "ImportError" if isinstance(e, ImportError) else "Exception")
            answer({"ok": False, "type": kind, "error": str(e)})


# ---------------------------------------------------------------------------
# The server side
# ---------------------------------------------------------------------------

class _Helper:
    def __init__(self, memory_limit_mb: int):
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "services.extract_sandbox", str(memory_limit_mb)],
            cwd=str(_REPO_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, **extra,
        )
        self.memory_limit_mb = memory_limit_mb
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

    def call(self, task: str, path: Path, timeout: Optional[float]) -> dict:
        self.tasks += 1
        request = json.dumps({"task": task, "path": str(path)}).encode("utf-8") + b"\n"
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

    def acquire(self, memory_limit_mb: int) -> Optional[_Helper]:
        with self._lock:
            while True:
                while self._idle:
                    helper = self._idle.pop()
                    if helper.usable() and helper.memory_limit_mb == memory_limit_mb:
                        self._busy += 1
                        return helper
                    helper.close()
                if self._busy < self.size():
                    self._busy += 1
                    break
                self._lock.wait()
        try:
            return _Helper(memory_limit_mb)
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


def run(task: str, path: Path, in_process: Callable[[], T]) -> T:
    """``task`` on ``path`` in a helper, or ``in_process()`` when isolation is off."""
    import config

    settings = config.settings
    if not settings.extract_isolation or not _pool.available():
        return in_process()
    helper = _pool.acquire(max(0, settings.extract_memory_limit_mb))
    if helper is None:
        return in_process()
    name = Path(path).name
    timeout = settings.extract_timeout_seconds or None
    keep = False
    try:
        reply = helper.call(task, Path(path).resolve(), timeout)
        if reply.get("timeout"):
            raise ExtractionLimitExceeded(
                f"Reading {name} took longer than {timeout:g} seconds, so it was stopped "
                f"(EXTRACT_TIMEOUT_SECONDS)."
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
        kind, error = reply.get("type"), reply.get("error") or "unknown error"
        if kind == "MemoryError":
            raise ExtractionLimitExceeded(
                f"Reading {name} needs more than {helper.memory_limit_mb} MB of memory, so it "
                f"was stopped (EXTRACT_MEMORY_LIMIT_MB)."
            )
        keep = True  # an ordinary parse error leaves the helper fine
        unreadable = _unreadable_file_errors().get(kind)
        if unreadable is not None:
            # The file itself is at fault: keep the type, so the upload can say
            # so (422) instead of reporting a server failure.
            raise unreadable(error)
        if kind == "ValueError":
            raise ValueError(error)
        if kind == "ImportError":
            raise ImportError(error)
        raise Exception(error)
    finally:
        _pool.release(helper, keep)


if __name__ == "__main__":
    _helper_main(sys.argv[1:])
