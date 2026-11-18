"""Read PRA model files into a PraModel: Open-PSA MEF and SAPHIRE MAR-D.

    is_pra_file(path)        does this file hold a PRA model? (by content, not name alone)
    read_pra_file(path)      one file, or a zip export, as a model
    read_pra_bundle(paths)   several files of one model (a MAR-D project, an MEF set)

Which files are PRA:

* the MAR-D extensions (``.ftl .bei .bed .ftd .gtd .etl .etd .esd .sqc .ftc
  .esc``, any case) when they start like MAR-D;
* ``.sql`` only with the MAR-D sequence-logic signature; any other .sql stays
  SQL code;
* ``.xml`` only when the root element is ``<opsa-mef>`` (or an MEF
  ``<report>`` holding ``<sum-of-products>``); any other .xml stays what it
  was. ``.opsa``, ``.mef`` and ``.opsa-mef`` are the names SCRAM's and KUREAS's
  file dialogs offer for MEF, and are read the same way;
* a ``.zip`` whose every member (folders, a README and macOS litter aside) is
  one of the above, MAR-D files of the kinds a SAPHIRE export also carries
  (.BEA, .FTA, ...) included. It is checked with the archive limits before
  anything inside is read, and becomes one model.

Everything is read from bytes in memory with the size and element limits of
``base.limits()``; nothing is unpacked to disk.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, List, Optional, Union

from services.pra.model import PraModel
from services.pra.readers.base import PraFormatError, Source, check_size, decode, limits
from services.pra.readers.mard import OTHER_KINDS, READ_KINDS, looks_like_mard, read_mard
from services.pra.readers.mef import read_mef

__all__ = [
    "PraFormatError", "MARD_EXTENSIONS", "MEF_EXTENSIONS", "NAMED_EXTENSIONS", "SNIFFED_EXTENSIONS",
    "is_pra_file", "is_pra_upload", "pra_kind", "read_pra_file", "read_pra_bundle", "read_sources",
]

# MAR-D kinds read by name (content still checked); .sql is sniffed instead.
MARD_EXTENSIONS = frozenset("." + k.lower() for k in READ_KINDS)
MEF_EXTENSIONS = frozenset({".xml", ".opsa", ".mef", ".opsa-mef"})
# Accepted by name alone at an upload gate; the reader checks the content.
NAMED_EXTENSIONS = (MARD_EXTENSIONS - {".sql"}) | {".opsa", ".mef", ".opsa-mef"}
# Something else unless the content says PRA.
SNIFFED_EXTENSIONS = frozenset({".sql", ".xml", ".zip"})

_HEAD_BYTES = 8192
_ROOT = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)")
_README = re.compile(r"^(readme|license|licence)(\.[a-z0-9]+)?$", re.IGNORECASE)
_JUNK = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$|desktop\.ini$)", re.IGNORECASE)

PathLike = Union[str, Path]


def _ext(name: str) -> str:
    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    return ".opsa-mef" if name.lower().endswith(".opsa-mef") else suffix


def _xml_root(head: bytes) -> Optional[str]:
    """The root element's local name, skipping the prolog (declaration, comments)."""
    text = head
    if text.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = text.decode("utf-16", errors="ignore").encode("utf-8")
        except LookupError:
            return None
    pos = 0
    while True:
        start = text.find(b"<", pos)
        if start < 0 or start + 1 >= len(text):
            return None
        nxt = text[start + 1:start + 2]
        if nxt == b"?":
            pos = text.find(b"?>", start) + 2 if text.find(b"?>", start) >= 0 else len(text)
            continue
        if text.startswith(b"<!--", start):
            end = text.find(b"-->", start)
            if end < 0:
                return None
            pos = end + 3
            continue
        if nxt == b"!":
            return None  # a DOCTYPE: not something Clio reads as MEF
        m = _ROOT.match(text, start)
        return m.group(1).decode("ascii", "ignore") if m else None


def _is_mef_head(head: bytes) -> bool:
    root = _xml_root(head)
    if root == "opsa-mef":
        return True
    return root == "report" and b"sum-of-products" in head


def pra_kind(name: str, head: bytes) -> Optional[str]:
    """'mef', 'mard' or None for a file (not a zip) judged by name and first bytes."""
    ext = _ext(name)
    if ext in MEF_EXTENSIONS:
        return "mef" if _is_mef_head(head) else None
    if ext in MARD_EXTENSIONS:
        return "mard" if looks_like_mard(decode(head), ext[1:].upper()) else None
    return None


def _member_kind(name: str, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Optional[str]:
    """What one zip member is: 'mef', 'mard', 'other-mard' (accepted, not read), 'skip' or None."""
    base = PurePosixPath(name).name
    if info.is_dir() or _JUNK.search(name) or not base:
        return "skip"
    if _README.match(base):
        return "skip"
    kind = PurePosixPath(base).suffix.lstrip(".").upper()
    if kind in OTHER_KINDS:
        return "other-mard"
    ext = _ext(base)
    if ext not in MEF_EXTENSIONS and ext not in MARD_EXTENSIONS:
        return None
    try:
        with zf.open(info) as member:
            head = member.read(_HEAD_BYTES)
    except (RuntimeError, zipfile.BadZipFile, NotImplementedError, OSError, EOFError):
        return None  # encrypted or damaged: not something to accept by name
    return pra_kind(base, head)


def _zip_members(zf: zipfile.ZipFile) -> Optional[List[tuple]]:
    """[(info, kind)] when the zip is a PRA export, else None."""
    members, readable = [], False
    kinds = set()
    for info in zf.infolist():
        kind = _member_kind(info.filename, zf, info)
        if kind is None:
            return None
        if kind == "skip":
            continue
        if kind in ("mef", "mard"):
            readable = True
            kinds.add(kind)
        else:
            kinds.add("mard")
        members.append((info, kind))
    if not readable or len(kinds) > 1:
        return None
    return members


def _zip_is_pra(source: Union[PathLike, BinaryIO]) -> bool:
    try:
        with zipfile.ZipFile(source) as zf:
            return _zip_members(zf) is not None
    except (zipfile.BadZipFile, OSError, EOFError, ValueError):
        return False


def is_pra_file(path: PathLike) -> bool:
    """Whether ``path`` holds a PRA model Clio reads, judged by its name and content."""
    path = Path(path)
    ext = _ext(path.name)
    if ext == ".zip":
        return _zip_is_pra(path)
    if ext not in MEF_EXTENSIONS and ext not in MARD_EXTENSIONS:
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return False
    return pra_kind(path.name, head) is not None


def is_pra_upload(filename: str, fileobj: BinaryIO) -> bool:
    """``is_pra_file`` for an upload not yet on disk; leaves the stream at its start."""
    ext = _ext(filename or "")
    if ext not in SNIFFED_EXTENSIONS and ext not in NAMED_EXTENSIONS:
        return False
    try:
        fileobj.seek(0)
        if ext == ".zip":
            return _zip_is_pra(fileobj)
        return pra_kind(filename, fileobj.read(_HEAD_BYTES)) is not None
    except (OSError, ValueError):
        return False
    finally:
        try:
            fileobj.seek(0)
        except (OSError, ValueError):
            pass


def _zip_sources(path: Path) -> List[Source]:
    from services.format_readers import check_zip_container

    check_zip_container(path, ".zip")
    cap = limits()["bytes"]
    try:
        with zipfile.ZipFile(path) as zf:
            members = _zip_members(zf)
            if members is None:
                raise PraFormatError(
                    f"{path.name} is not a PRA export: every file in it must be an Open-PSA (.xml) or "
                    f"SAPHIRE MAR-D file of one model.")
            total = sum(info.file_size for info, _ in members)
            if cap and total > cap:
                raise PraFormatError(
                    f"{path.name} unpacks to {total / (1024 * 1024):.1f} MB, more than the "
                    f"{cap // (1024 * 1024)} MB Clio reads for one PRA model (PRA_MAX_FILE_MB).")
            return [(PurePosixPath(info.filename).name, zf.read(info)) for info, _ in members]
    except zipfile.BadZipFile:
        raise PraFormatError(f"{path.name} is not a valid zip file.") from None
    except RuntimeError as e:  # an encrypted member
        raise PraFormatError(f"{path.name} could not be read: {e}") from None


def read_sources(sources: List[Source]) -> PraModel:
    """One model from in-memory files, all MEF or all MAR-D."""
    if not sources:
        raise PraFormatError("No PRA files were given.")
    check_size(sources)
    kinds = set()
    for name, data in sources:
        kind = PurePosixPath(name).suffix.lstrip(".").upper()
        if kind in OTHER_KINDS:
            kinds.add("mard")
            continue
        found = pra_kind(name, data[:_HEAD_BYTES])
        ext = _ext(name)
        if found is None and ext in MARD_EXTENSIONS:
            raise PraFormatError(
                f"{name} does not read as a SAPHIRE MAR-D .{ext[1:].upper()} file (it should start with "
                f"a 'PROJECT =' header, as SAPHIRE writes it).")
        if found is None and ext in MEF_EXTENSIONS:
            raise PraFormatError(f"{name} is not an Open-PSA model: its root element is not <opsa-mef>.")
        if found is None:
            raise PraFormatError(
                f"{name} is not an Open-PSA model (an XML file whose root is <opsa-mef>) or a SAPHIRE "
                f"MAR-D export (.FTL, .BEI, .ETL, ... starting with a 'PROJECT =' header).")
        kinds.add(found)
    if len(kinds) > 1:
        raise PraFormatError("These files mix Open-PSA and SAPHIRE MAR-D; one model is one or the other.")
    return read_mef(sources) if kinds == {"mef"} else read_mard(sources)


def read_pra_file(path: PathLike) -> PraModel:
    """The model in one file: an MEF or MAR-D file, or a zip export of either."""
    path = Path(path)
    if _ext(path.name) == ".zip":
        return read_sources(_zip_sources(path))
    cap = limits()["bytes"]
    size = path.stat().st_size
    if cap and size > cap:
        raise PraFormatError(
            f"{path.name} is {size / (1024 * 1024):.1f} MB, more than the {cap // (1024 * 1024)} MB "
            f"Clio reads for one PRA model (PRA_MAX_FILE_MB).")
    return read_sources([(path.name, path.read_bytes())])


def read_pra_bundle(paths: Iterable[PathLike]) -> PraModel:
    """One model from several files (zips included) of the same project."""
    sources: List[Source] = []
    for p in paths:
        p = Path(p)
        sources.extend(_zip_sources(p) if _ext(p.name) == ".zip" else [(p.name, p.read_bytes())])
        check_size(sources)
    return read_sources(sources)
