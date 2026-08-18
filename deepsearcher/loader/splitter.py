## Sentence Window splitting strategy, ref:
#  https://github.com/milvus-io/bootcamp/blob/master/bootcamp/RAG/advanced_rag/sentence_window_with_langchain.ipynb

import hashlib
import re
from typing import Any, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from deepsearcher.loader.file_loader.table_text import (
    render_key_value_row,
    render_key_value_rows,
    render_markdown_table,
)


class Chunk:
    """
    Represents a chunk of text with associated metadata and embedding.

    A chunk is a segment of text extracted from a document, along with its reference
    information, metadata, and optional embedding vector.

    Attributes:
        text: The text content of the chunk.
        reference: A reference to the source of the chunk (e.g., file path, URL).
        metadata: Additional metadata associated with the chunk.
        embedding: The vector embedding of the chunk, if available.
    """

    def __init__(
        self,
        text: str,
        reference: str,
        metadata: dict = None,
        embedding: List[float] = None,
        vector_text: str = None,
    ):
        """
        Initialize a Chunk object.

        Args:
            text: The text content of the chunk.
            reference: A reference to the source of the chunk.
            metadata: Additional metadata associated with the chunk. Defaults to an empty dict.
            embedding: The vector embedding of the chunk. Defaults to None.
        """
        self.text = text
        self.reference = reference
        self.metadata = metadata or {}
        self.embedding = embedding or None
        self.vector_text = vector_text or None


def _sentence_window_split(
    split_docs: List[Document], original_document: Document, offset: int = 200
) -> List[Chunk]:
    """
    Create chunks with context windows from split documents.

    This function takes documents that have been split into smaller pieces and
    adds context from the original document by including text before and after
    each split piece, up to the specified offset.

    Args:
        split_docs: List of documents that have been split.
        original_document: The original document before splitting.
        offset: Number of characters to include before and after each split piece.

    Returns:
        A list of Chunk objects with context windows.
    """
    chunks = []
    original_text = original_document.page_content
    line_spans = (
        original_document.metadata.get("_line_spans")
        if isinstance(original_document.metadata, dict)
        else None
    )
    if not isinstance(line_spans, list):
        line_spans = []
    page_char_start = int(original_document.metadata.get("page_char_start", 0) or 0)
    for doc in split_docs:
        doc_text = doc.page_content
        start_index = doc.metadata.pop("start_index", None)
        if not isinstance(start_index, int) or start_index < 0:
            start_index = original_text.find(doc_text)
        if start_index < 0:
            start_index = 0
        end_index = start_index + len(doc_text)
        wider_text = original_text[
            max(0, start_index - offset) : min(len(original_text), end_index + offset)
        ]
        reference = doc.metadata.pop("reference", "")
        doc.metadata["wider_text"] = wider_text
        char_start = page_char_start + start_index
        char_end = page_char_start + end_index
        doc.metadata["char_start"] = char_start
        doc.metadata["char_end"] = char_end
        intersecting_boxes = [
            span.get("bbox")
            for span in line_spans
            if isinstance(span, dict)
            and isinstance(span.get("char_start"), int)
            and isinstance(span.get("char_end"), int)
            and span["char_end"] > char_start
            and span["char_start"] < char_end
            and isinstance(span.get("bbox"), list)
            and len(span["bbox"]) == 4
        ]
        if intersecting_boxes:
            doc.metadata["bbox"] = [
                min(float(box[0]) for box in intersecting_boxes),
                min(float(box[1]) for box in intersecting_boxes),
                max(float(box[2]) for box in intersecting_boxes),
                max(float(box[3]) for box in intersecting_boxes),
            ]
        document_id = str(
            doc.metadata.get("document_id") or doc.metadata.get("display_name") or reference
        )
        page_number = doc.metadata.get("page_number")
        section_title = str(doc.metadata.get("section_title") or "")
        locator_payload = f"{document_id}|{page_number}|{char_start}|{char_end}|{section_title}"
        doc.metadata["location_id"] = hashlib.sha256(locator_payload.encode("utf-8")).hexdigest()[
            :24
        ]
        doc.metadata["source_locator"] = f"page={page_number or 1}&char={char_start}-{char_end}"
        doc.metadata.pop("_line_spans", None)
        doc.metadata.pop("_section_spans", None)
        chunk = Chunk(text=doc_text, reference=reference, metadata=doc.metadata)
        chunks.append(chunk)
    return chunks


def _section_documents(document: Document) -> list[Document]:
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    section_spans = metadata.get("_section_spans")
    if not isinstance(section_spans, list) or not section_spans:
        plain_metadata = dict(metadata)
        plain_metadata.setdefault("page_char_start", 0)
        return [
            Document(
                page_content=document.page_content,
                metadata=plain_metadata,
            )
        ]

    documents = []
    for span in section_spans:
        if not isinstance(span, dict):
            continue
        start = span.get("char_start")
        end = span.get("char_end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(document.page_content)
        ):
            continue
        section_text = document.page_content[start:end].strip()
        if not section_text:
            continue
        leading_trim = len(document.page_content[start:end]) - len(
            document.page_content[start:end].lstrip()
        )
        section_metadata: dict[str, Any] = dict(metadata)
        section_metadata["page_char_start"] = start + leading_trim
        section_metadata["section_title"] = span.get("section_title")
        section_metadata["section_path"] = span.get("section_path")
        section_metadata["heading_level"] = span.get("heading_level")
        section_metadata["section_bbox"] = span.get("bbox")
        documents.append(
            Document(
                page_content=section_text,
                metadata=section_metadata,
            )
        )
    return documents or [
        Document(
            page_content=document.page_content,
            metadata={**metadata, "page_char_start": 0},
        )
    ]


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^\s`~]*)\s*$")
_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")


def _document_key(document: Document) -> Any:
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    return metadata.get("document_id") or metadata.get("reference") or id(document)


def _next_chunk_indices(next_chunk_index: dict, document_key: Any, chunks: List[Chunk]) -> None:
    chunk_index = next_chunk_index.get(document_key, 0)
    for chunk in chunks:
        chunk.metadata["chunk_index"] = chunk_index
        chunk_index += 1
    next_chunk_index[document_key] = chunk_index


def _split_plain_document(
    document: Document,
    *,
    text_splitter: RecursiveCharacterTextSplitter,
    next_chunk_index: dict,
) -> List[Chunk]:
    """Original path: section-aware split + sentence-window context (unchanged)."""
    chunks = []
    for section_document in _section_documents(document):
        document_key = (
            section_document.metadata.get("document_id")
            or section_document.metadata.get("reference")
            or id(document)
        )
        split_docs = text_splitter.split_documents([section_document])
        split_chunks = _sentence_window_split(
            split_docs,
            section_document,
            offset=300,
        )
        _next_chunk_indices(next_chunk_index, document_key, split_chunks)
        chunks.extend(split_chunks)
    return chunks


def _chunk_table(
    headers: List[str],
    rows: List[List[str]],
    base_metadata: dict,
    *,
    chunk_size: int,
) -> List[Chunk]:
    """TableChunker equivalent: whole table first, row-group split when too long.

    Display text keeps the markdown table; vector_text uses ``column: value`` rows.
    Each split group carries the full header row and single rows are atomic.
    """
    if not headers and not rows:
        return []
    max_rows = max(1, chunk_size // 30)
    tolerance = min(max(chunk_size * 3, 1), 8192)
    if len(rows) <= max_rows and len(render_key_value_rows(headers, rows)) <= tolerance:
        groups = [rows]
    else:
        budget = max(1, chunk_size)
        groups: List[List[List[str]]] = []
        group: List[List[str]] = []
        group_cost = 0
        for row in rows:
            row_cost = len(render_key_value_row(headers, row))
            if len(group) >= max_rows or (group and group_cost + row_cost > budget):
                groups.append(group)
                group = []
                group_cost = 0
            group.append(row)
            group_cost += row_cost
        if group:
            groups.append(group)
    reference = base_metadata.get("reference", "")
    chunks = []
    for group in groups:
        vector_text = render_key_value_rows(headers, group)
        chunks.append(
            Chunk(
                text=render_markdown_table(headers, group),
                reference=reference,
                metadata=dict(base_metadata),
                vector_text=vector_text or None,
            )
        )
    return chunks


def _split_table_document(
    document: Document,
    *,
    chunk_size: int,
    next_chunk_index: dict,
) -> List[Chunk]:
    metadata = dict(document.metadata) if isinstance(document.metadata, dict) else {}
    headers = metadata.pop("_table_headers", None) or []
    rows = metadata.pop("_table_rows", None) or []
    metadata.pop("kind", None)
    chunks = _chunk_table(headers, rows, metadata, chunk_size=chunk_size)
    _next_chunk_indices(next_chunk_index, _document_key(document), chunks)
    return chunks


def _split_code_by_lines(code: str, max_chars: int) -> List[str]:
    """Line-boundary split: a single oversized line stays whole (CodeChunker)."""
    segments: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in code.split("\n"):
        addition = len(line) if not current else current_len + 1 + len(line)
        if current and addition > max_chars:
            segments.append("\n".join(current))
            current = []
            current_len = 0
        if current:
            current_len += 1 + len(line)
        else:
            current_len = len(line)
        current.append(line)
    if current:
        segments.append("\n".join(current))
    return segments or [code]


def _chunk_code(
    code: str,
    language: str,
    base_metadata: dict,
    *,
    chunk_size: int,
) -> List[Chunk]:
    """CodeChunker equivalent: whole block first, line-boundary split when huge."""
    tolerance = min(max(chunk_size * 3, 1), 8192)
    segments = [code] if len(code) <= tolerance else _split_code_by_lines(code, chunk_size)
    reference = base_metadata.get("reference", "")
    chunks = []
    for segment in segments:
        if language:
            markdown = f"```{language}\n{segment}\n```"
        else:
            markdown = f"```\n{segment}\n```"
        chunks.append(
            Chunk(
                text=markdown,
                reference=reference,
                metadata=dict(base_metadata),
                vector_text=segment,
            )
        )
    return chunks


def _split_code_document(
    document: Document,
    *,
    chunk_size: int,
    next_chunk_index: dict,
) -> List[Chunk]:
    metadata = dict(document.metadata) if isinstance(document.metadata, dict) else {}
    code = document.page_content
    language = metadata.pop("_code_language", None) or ""
    metadata.pop("kind", None)
    chunks = _chunk_code(code, language, metadata, chunk_size=chunk_size)
    _next_chunk_indices(next_chunk_index, _document_key(document), chunks)
    return chunks


def _extract_fenced_code(text: str) -> List[dict[str, str]]:
    """Extract fenced code blocks before any section/window splitting.

    Only reliably-closed fenced blocks are treated as code; everything else stays
    prose. Returns [] when the document has no fenced code block, so plain
    documents take the exact original path.
    """
    lines = text.split("\n")
    segments: List[dict[str, str]] = []
    prose_start = 0
    index = 0
    while index < len(lines):
        match = _FENCE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        fence_char = match.group(1)[0]
        fence_len = len(match.group(1))
        language = match.group(2)
        code_lines: List[str] = []
        cursor = index + 1
        closed = False
        while cursor < len(lines):
            close = _CLOSE_RE.match(lines[cursor])
            if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_len:
                closed = True
                break
            code_lines.append(lines[cursor])
            cursor += 1
        if not closed:
            index += 1
            continue
        segments.append(
            {
                "prose": "\n".join(lines[prose_start:index]),
                "language": language,
                "code": "\n".join(code_lines),
            }
        )
        index = cursor + 1
        prose_start = index
    if not segments:
        return []
    tail = "\n".join(lines[prose_start:])
    if tail.strip():
        segments.append({"prose": tail, "language": "", "code": ""})
    return segments


def _split_ordinary_document(
    document: Document,
    *,
    text_splitter: RecursiveCharacterTextSplitter,
    chunk_size: int,
    next_chunk_index: dict,
) -> List[Chunk]:
    """Protect fenced code first, then let prose take the original path."""
    base_metadata = dict(document.metadata) if isinstance(document.metadata, dict) else {}
    segments = _extract_fenced_code(document.page_content)
    if not segments:
        return _split_plain_document(
            document,
            text_splitter=text_splitter,
            next_chunk_index=next_chunk_index,
        )
    chunks: List[Chunk] = []
    for segment in segments:
        prose = segment["prose"]
        if prose and prose.strip():
            prose_doc = Document(page_content=prose, metadata=dict(base_metadata))
            chunks.extend(
                _split_plain_document(
                    prose_doc,
                    text_splitter=text_splitter,
                    next_chunk_index=next_chunk_index,
                )
            )
        code = segment["code"]
        if code.strip():
            code_chunks = _chunk_code(
                code.rstrip("\n"),
                segment["language"],
                base_metadata,
                chunk_size=chunk_size,
            )
            _next_chunk_indices(next_chunk_index, _document_key(document), code_chunks)
            chunks.extend(code_chunks)
    return chunks


def split_docs_to_chunks(
    documents: List[Document], chunk_size: int = 1500, chunk_overlap=100
) -> List[Chunk]:
    """
    Split documents into chunks with context windows.

    Public signature and plain-document behavior are unchanged. Internally the
    splitter dispatches by kind: table/code blocks keep their structure (with an
    embedding-only ``vector_text``), while ordinary text protects fenced code
    before section/window splitting.

    Args:
        documents: List of documents to split.
        chunk_size: Size of each chunk in characters.
        chunk_overlap: Number of characters to overlap between chunks.

    Returns:
        A list of Chunk objects with context windows.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )
    all_chunks = []
    next_chunk_index = {}
    for document in documents:
        kind = document.metadata.get("kind") if isinstance(document.metadata, dict) else None
        if kind == "table":
            chunks = _split_table_document(
                document,
                chunk_size=chunk_size,
                next_chunk_index=next_chunk_index,
            )
        elif kind == "code":
            chunks = _split_code_document(
                document,
                chunk_size=chunk_size,
                next_chunk_index=next_chunk_index,
            )
        else:
            chunks = _split_ordinary_document(
                document,
                text_splitter=text_splitter,
                chunk_size=chunk_size,
                next_chunk_index=next_chunk_index,
            )
        all_chunks.extend(chunks)
    return all_chunks
