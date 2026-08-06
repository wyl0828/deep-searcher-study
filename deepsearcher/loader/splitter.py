## Sentence Window splitting strategy, ref:
#  https://github.com/milvus-io/bootcamp/blob/master/bootcamp/RAG/advanced_rag/sentence_window_with_langchain.ipynb

import hashlib
from typing import Any, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


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


def split_docs_to_chunks(
    documents: List[Document], chunk_size: int = 1500, chunk_overlap=100
) -> List[Chunk]:
    """
    Split documents into chunks with context windows.

    This function splits a list of documents into smaller chunks with overlapping text,
    and adds context windows to each chunk by including text before and after the chunk.

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
    for doc in documents:
        for section_document in _section_documents(doc):
            document_key = (
                section_document.metadata.get("document_id")
                or section_document.metadata.get("reference")
                or id(doc)
            )
            split_docs = text_splitter.split_documents([section_document])
            split_chunks = _sentence_window_split(
                split_docs,
                section_document,
                offset=300,
            )
            chunk_index = next_chunk_index.get(document_key, 0)
            for chunk in split_chunks:
                chunk.metadata["chunk_index"] = chunk_index
                chunk_index += 1
            next_chunk_index[document_key] = chunk_index
            all_chunks.extend(split_chunks)
    return all_chunks
