"""Upload size cap, applied before the body is read.

FastAPI parses a multipart body (and Starlette spools every file to disk)
before the endpoint runs, so an endpoint can only see an oversized upload
after the server has taken all of it. This middleware answers the document
upload routes with 413 as soon as the request says it is larger than
``MAX_UPLOAD_FILE_MB`` (plus room for the multipart framing), and stops a body
that grows past that without saying so (chunked transfer, or a wrong
Content-Length) while it streams in. The endpoints check each file again
before staging it.

Registered inside authentication and rate limiting (see main.py), so a caller
who may not upload is told so first. Other routes are untouched: a
collection bundle import, for one, is legitimately larger than any document.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import HTTPException

import config

# Room for the multipart boundaries and part headers around one file.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

# POST routes that receive documents as multipart uploads.
_UPLOAD_PATHS = re.compile(
    r"^(?:/documents/upload(?:-staged)?|/api/v1/collections/[^/]+/documents)/?$"
)


def max_upload_file_bytes() -> int:
    """The per-file cap in bytes; 0 means no cap."""
    return max(0, config.settings.max_upload_file_mb) * 1024 * 1024


def too_large_message(limit_bytes: Optional[int] = None) -> str:
    limit = limit_bytes if limit_bytes is not None else max_upload_file_bytes()
    return (
        f"Uploads are limited to {limit // (1024 * 1024)} MB per file, and per request "
        f"(MAX_UPLOAD_FILE_MB). Send large files one at a time."
    )


def _declared_length(scope) -> Optional[int]:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class UploadSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not _UPLOAD_PATHS.match(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return
        per_file = max_upload_file_bytes()
        if not per_file:
            await self.app(scope, receive, send)
            return
        limit = per_file + MULTIPART_OVERHEAD_BYTES
        message = too_large_message(per_file)

        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_413(send, message)
            return

        received = 0
        tripped = False
        started = False
        replaced = False

        async def counted_receive():
            nonlocal received, tripped
            event = await receive()
            if event.get("type") == "http.request":
                received += len(event.get("body") or b"")
                if received > limit:
                    tripped = True
                    raise HTTPException(status_code=413, detail=message)
            return event

        async def guarded_send(event):
            nonlocal started, replaced
            if replaced:
                return  # the rest of the app's own answer
            if event.get("type") == "http.response.start":
                if tripped:
                    # Whatever the app made of the aborted body (FastAPI's form
                    # parser answers 400), the caller hears the same 413.
                    replaced = True
                    await _send_413(send, message)
                    return
                started = True
            await send(event)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except HTTPException:
            if not (tripped and not started and not replaced):
                raise
            replaced = True
            await _send_413(send, message)


async def _send_413(send, message: str) -> None:
    body = json.dumps({"detail": message, "code": "upload_too_large"}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
