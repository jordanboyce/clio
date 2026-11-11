"""One lock for every call into PDFium.

PDFium is not thread-safe: pypdfium2 forbids calling it from two threads at
once, even on different documents. Folder and repo indexing extract several
files in parallel (bulk_extract_workers), and a page preview can render while
an upload is being read, so unguarded calls corrupt PDFium's global state.
That shows up as every later file failing with "Data format error", or as the
whole process dying with a segmentation fault.

Hold PDFIUM_LOCK from opening a PdfDocument until it is closed. It is
re-entrant so a guarded helper may call another.
"""
import threading

PDFIUM_LOCK = threading.RLock()
