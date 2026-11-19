"""Text chunking service for splitting documents into searchable chunks."""

from typing import List, Optional
import logging
import re

from models.schemas import ChunkMetadata

logger = logging.getLogger(__name__)


class TextChunker:
    """Splits text into overlapping chunks suitable for embedding."""

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 100, continuous: bool = False):
        """
        Initialize the text chunker.

        Args:
            chunk_size: Target size of each chunk in characters
            chunk_overlap: Number of characters to overlap between chunks
            continuous: Read a document as one text rather than separate
                pages (B2, indexing recipe 2): the current heading carries
                over to the next page, and a sentence cut by a page break is
                finished on the page it starts on.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.continuous = continuous

        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")

    def chunk_document(
        self,
        page_texts: dict[int, str],
        document_id: str,
        filename: str,
        source_format: Optional[str] = None,
        extraction_method: Optional[str] = None,
    ) -> List[ChunkMetadata]:
        """
        Split a document's pages into overlapping chunks.

        Args:
            page_texts: Dictionary mapping page numbers to text content
            document_id: Unique identifier for the document
            filename: Original filename
            source_format: Source file format (pdf, txt, etc.)
            extraction_method: How text was extracted (text, ocr, hybrid)

        Returns:
            List of ChunkMetadata objects
        """
        all_chunks = []
        if self.continuous:
            page_texts = self._finish_sentences_across_pages(page_texts)
        heading: Optional[str] = None

        for page_num, text in page_texts.items():
            if not text.strip():
                logger.debug(f"Skipping empty page {page_num} in {filename}")
                continue

            if self.continuous:
                page_chunks = self._chunk_text(text, heading)
                heading = self._last_heading(text, heading)
            else:
                page_chunks = self._chunk_text(text)

            for chunk_idx, chunk_text in enumerate(page_chunks):
                chunk_id = f"{document_id}_p{page_num}_c{chunk_idx}"

                chunk_metadata = ChunkMetadata(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    filename=filename,
                    page_number=page_num,
                    chunk_index=chunk_idx,
                    text=chunk_text,
                    source_format=source_format,
                    extraction_method=extraction_method,
                )
                all_chunks.append(chunk_metadata)

        return all_chunks

    # A page whose text ends like this has finished its sentence.
    _SENTENCE_END = re.compile(r"[.!?:;)\]\"'\u201d\u2019]\s*$")
    # The rest of a cut sentence at the top of the next page: up to the first
    # sentence end, within a bound so a page with no full stop is not swallowed.
    _REST_OF_SENTENCE = re.compile(r"^(.{1,400}?[.!?][\"'\u201d\u2019)]?)(?=\s|$)", re.DOTALL)

    def _finish_sentences_across_pages(self, page_texts: dict) -> dict:
        """Move the end of a sentence cut by a page break back to the page
        it starts on, so one chunk holds the whole sentence."""
        pages = dict(page_texts)
        numbers = list(pages)
        for prev, nxt in zip(numbers, numbers[1:]):
            before, after = pages[prev] or "", pages[nxt] or ""
            if not before.strip() or not after.strip() or self._SENTENCE_END.search(before):
                continue
            first_line = after.lstrip().split("\n", 1)[0].strip()
            if self._is_heading_line(first_line):
                continue
            rest = self._REST_OF_SENTENCE.match(after.lstrip())
            if not rest or "\n\n" in rest.group(1):
                continue
            pages[prev] = before.rstrip() + " " + rest.group(1).strip()
            pages[nxt] = after.lstrip()[rest.end():].lstrip()
        return pages

    def _last_heading(self, text: str, default: Optional[str]) -> Optional[str]:
        """The heading in force at the end of ``text``."""
        heading = default
        for block in re.split(r"\n\s*\n+", text):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) == 1 and self._is_heading_line(lines[0]):
                heading = lines[0]
        return heading

    def _chunk_text(self, text: str, heading: Optional[str] = None) -> List[str]:
        """Split text with heading/step-aware boundaries before falling back to windows.

        ``heading``: the heading in force where this text starts (a page that
        continues a section from the page before). It is put in front of
        whatever comes before the text's own first heading."""
        if heading and self._starts_with_heading(text):
            heading = None
        if len(text) <= self.chunk_size:
            return [f"{heading}\n{text}" if heading else text]

        segments = self._split_into_structured_segments(text, heading)
        if len(segments) > 1:
            structured_chunks = self._chunk_structured_segments(segments)
            if structured_chunks:
                return structured_chunks

        windows = self._chunk_text_window(text)
        if heading and windows:
            windows[0] = f"{heading}\n{windows[0]}"
        return windows

    def _starts_with_heading(self, text: str) -> bool:
        first = text.lstrip().split("\n\n", 1)[0].strip()
        return "\n" not in first and self._is_heading_line(first)

    def _split_into_structured_segments(self, text: str, heading: Optional[str] = None) -> List[str]:
        """Split text into structural blocks such as headings, steps, and paragraphs."""
        blocks = [block.strip() for block in re.split(r"\n\s*\n+", text) if block.strip()]
        segments: List[str] = []
        current_heading: Optional[str] = heading

        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue

            if len(lines) == 1 and self._is_heading_line(lines[0]):
                current_heading = lines[0]
                continue

            if self._looks_like_step_block(lines):
                segments.extend(self._split_step_block(lines, current_heading))
                continue

            paragraph = "\n".join(lines)
            if current_heading and not paragraph.startswith(current_heading):
                paragraph = f"{current_heading}\n{paragraph}"
            segments.append(paragraph)

        return segments

    def _is_heading_line(self, line: str) -> bool:
        """Heuristic heading detector for manuals and technical documents."""
        candidate = line.strip().rstrip(":")
        if len(candidate) < 4 or len(candidate) > 120:
            return False

        word_count = len(candidate.split())
        if word_count > 12:
            return False

        if candidate.startswith("#"):
            return True

        if re.match(r"^(section|chapter|appendix|overview|introduction|summary)\b", candidate, re.IGNORECASE):
            return True

        if re.match(r"^(\d+(?:\.\d+){0,4}|[A-Z]\.|[IVXLC]+\.)\s+[A-Z]", candidate):
            return True

        if candidate.isupper() and word_count <= 10:
            return True

        if word_count <= 8 and candidate == candidate.title() and not re.search(r"[.!?]$", candidate) and "," not in candidate:
            return True

        return False

    def _is_step_line(self, line: str) -> bool:
        """Detect procedure/list steps in manuals."""
        return bool(re.match(r"^((step\s+)?\d+[\).:-]|[a-zA-Z][\).]|[-*])\s+\S", line.strip(), re.IGNORECASE))

    def _looks_like_step_block(self, lines: List[str]) -> bool:
        """Detect whether a block is primarily procedural content."""
        step_hits = sum(1 for line in lines if self._is_step_line(line))
        if step_hits >= 2:
            return True
        return step_hits == 1 and len(lines) <= 4 and len("\n".join(lines)) <= self.chunk_size

    def _split_step_block(self, lines: List[str], heading: Optional[str]) -> List[str]:
        """Split a step block into per-step segments while preserving the current heading."""
        steps: List[List[str]] = []
        current_step: List[str] = []

        for line in lines:
            if self._is_step_line(line) and current_step:
                steps.append(current_step)
                current_step = [line]
            else:
                current_step.append(line)

        if current_step:
            steps.append(current_step)

        segments = []
        for step in steps:
            segment = "\n".join(step).strip()
            if heading and not segment.startswith(heading):
                segment = f"{heading}\n{segment}"
            segments.append(segment)

        return segments

    def _chunk_structured_segments(self, segments: List[str]) -> List[str]:
        """Merge structured segments into chunks without breaking headings and steps apart unnecessarily."""
        chunks: List[str] = []
        current_segments: List[str] = []

        for segment in segments:
            if len(segment) > self.chunk_size:
                if current_segments:
                    chunks.append("\n\n".join(current_segments).strip())
                    current_segments = []
                chunks.extend(self._chunk_text_window(segment))
                continue

            candidate_segments = current_segments + [segment]
            candidate_text = "\n\n".join(candidate_segments).strip()

            if current_segments and len(candidate_text) > self.chunk_size:
                chunks.append("\n\n".join(current_segments).strip())
                overlap_seed = self._build_overlap_seed(current_segments)
                current_segments = overlap_seed.copy()
                candidate_segments = current_segments + [segment]
                candidate_text = "\n\n".join(candidate_segments).strip()

                if current_segments and len(candidate_text) > self.chunk_size:
                    chunks.append("\n\n".join(current_segments).strip())
                    current_segments = []
                    candidate_text = segment
                    candidate_segments = [segment]

            current_segments = candidate_segments

        if current_segments:
            chunks.append("\n\n".join(current_segments).strip())

        return [chunk for chunk in chunks if chunk]

    def _build_overlap_seed(self, segments: List[str]) -> List[str]:
        """Carry a small amount of structured context into the next chunk."""
        if not segments or self.chunk_overlap <= 0:
            return []

        last_segment = segments[-1].strip()
        if not last_segment:
            return []

        if len(last_segment) <= max(self.chunk_overlap, 120):
            return [last_segment]

        tail = last_segment[-self.chunk_overlap:].strip()
        return [tail] if tail else []

    def _chunk_text_window(self, text: str) -> List[str]:
        """Fallback sliding-window chunking for very large or unstructured blocks."""
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size

            # If this is not the last chunk, try to break at a sentence or word boundary.
            if end < len(text):
                boundary_search_start = end - int(self.chunk_size * 0.2)
                chunk_preview = text[boundary_search_start:end]

                for delimiter in [". ", "! ", "? ", "\n\n", "\n"]:
                    last_delimiter = chunk_preview.rfind(delimiter)
                    if last_delimiter != -1:
                        end = boundary_search_start + last_delimiter + len(delimiter)
                        break
                else:
                    last_space = chunk_preview.rfind(" ")
                    if last_space != -1:
                        end = boundary_search_start + last_space + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - self.chunk_overlap

            if start < 0:
                start = 0

            if len(chunks) > 0 and start <= end - self.chunk_size:
                start = end

        return chunks

