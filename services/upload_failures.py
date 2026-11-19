"""Why a file could not be added, and what to do about it (work order D1).

Every upload path reports a failed file as ``{filename, reason, fix}``: the
reason in plain words, the fix as the next step a person can take. The raw
error stays in the server log (and in ``error`` for older clients); it can
name a path or a library's internals, and says nothing a person can act on.
"""

from __future__ import annotations

from typing import Dict


def explain(filename: str, error: BaseException | str) -> Dict[str, str]:
    """``{filename, reason, fix}`` for one failed file."""
    reason, fix = _classify(error)
    return {"filename": filename, "reason": reason, "fix": fix}


def retryable(error: BaseException | str) -> bool:
    """False for a refusal the same file meets again: blocked, or rejected by policy."""
    from services.content_policy import BlockedContentError, ContentRejectedError
    return not isinstance(error, (BlockedContentError, ContentRejectedError))


def _classify(error: BaseException | str) -> tuple:
    name = type(error).__name__ if isinstance(error, BaseException) else ""
    text = str(error or "").lower()

    if "image-only" in text or ("ocr" in text and ("disabled" in text or " off" in text)):
        return ("It is a scan with no text to read, and reading scans (OCR) is off on this server.",
                "Turn on OCR in Settings, Features (or choose a vision model there), then retry.")
    if name in ("StorageLimitExceeded",) or "storage limit" in text or "collection is full" in text:
        return ("The collection has reached its storage limit.",
                "Remove sources you no longer need, or ask an admin to raise the limit.")
    if name in ("UploadTooLarge",) or "too large" in text or "larger than" in text:
        return ("The file is larger than this server accepts.",
                "Split it into smaller files, or ask an admin to raise the upload limit.")
    if name in ("BlockedContentError", "ContentRejectedError") or "refused by" in text or "blocked" in text:
        return ("This server's content rules refused it.",
                "Ask an admin what the rules allow, or remove the part they flag.")
    if "password" in text or "encrypted" in text:
        return ("The file is protected with a password.",
                "Save a copy without the password, then add that copy.")
    if "unsupported" in text or "not supported" in text or "no reader" in text or "unknown file type" in text:
        return ("Clio cannot read this kind of file.",
                "Save it as PDF, Word or plain text, or register its type under Settings, Custom file types.")
    if name in ("ExtractionLimitExceeded", "ArchiveLimitError") or "limit" in text and "pars" in text:
        return ("Reading it took more time or memory than this server allows for one file.",
                "Split it into smaller files, then add those.")
    if name in ("PraFormatError",) or "corrupt" in text or "damaged" in text or "not a valid" in text \
            or "could not be read" in text or "failed to extract" in text or "no text" in text:
        return ("The file could not be read: it may be damaged, or not the format its name says.",
                "Open it to check it, save it again, then retry.")
    if name == "LinkError" or (" from http" in text and "http " in text):
        return ("The page could not be fetched: the site answered with an error or did not answer.",
                "Check that the address opens in a browser, then retry.")
    if "embedding" in text and ("unavailable" in text or "not available" in text):
        return ("The search model was not available.",
                "Retry once the search model is ready (the footer says when).")
    return ("It could not be indexed.",
            "Retry. If it fails again, an admin can find the reason in the server log.")
