"""Tasks the extraction helper runs only in tests (services/extract_sandbox.TEST_TASKS_ENV).

A real hostile scan or recording is large and needs Tesseract or Whisper
installed; these stand in for the work so the helper's limits and the way
its answers cross the process boundary can be tested anywhere. An entry may
replace a real task (``transcribe``, ``ocr_local``, ``figures``) so the
server side runs exactly as it does in production.
"""

import os
import time


def _hang(extractor, path, args):
    time.sleep(3600)


def _hog(extractor, path, args):
    held = []
    while True:
        held.append(bytearray(16 * 1024 * 1024))


def _raise_pra(extractor, path, args):
    from services.pra.readers.base import PraFormatError

    raise PraFormatError("not a SAPHIRE export: the header line is missing")


def _raise_model(extractor, path, args):
    from services.audio_transcriber import TranscriptionModelUnavailable

    raise TranscriptionModelUnavailable("Whisper model 'base' is not on this server")


def _ocr_pages(extractor, path, args):
    return {"1": f"read in pid {os.getpid()}", "2": f"pages asked: {args.get('pages')}"}


def _transcript(extractor, path, args):
    return {"text": "", "language": args.get("language") or "en", "duration": 253.0,
            "segments": [{"start": 5.0, "end": 8.0, "text": f"model {args['model_size']}"},
                         {"start": 250.0, "end": 253.0, "text": f"pid {os.getpid()}"}]}


def _pid(extractor, path, args):
    return os.getpid()


# What a test may ask for: replace with an override per test through MODE.
_MODES = {
    "hang": {"transcribe": _hang, "ocr_local": _hang, "figures": _hang},
    "hog": {"transcribe": _hog, "ocr_local": _hog, "figures": _hog},
    "answer": {"transcribe": _transcript, "ocr_local": _ocr_pages},
}

TASKS = {"hang": _hang, "hog": _hog, "raise_pra": _raise_pra, "raise_model": _raise_model, "pid": _pid,
         **_MODES.get(os.environ.get("CLIO_EXTRACT_TEST_MODE", ""), {})}
