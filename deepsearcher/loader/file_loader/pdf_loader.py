from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, List

from langchain_core.documents import Document

from deepsearcher.loader.file_loader.base import BaseLoader
from deepsearcher.loader.file_loader.ocr import create_ocr_engine

PDF_PARSER_VERSION = "pdfplumber-layout-v2+rapidocr-v3"


@dataclass(frozen=True)
class _LayoutLine:
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float
    bold: bool = False
    kind: str = "text"


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\x00", "").replace("\u00ad", "")
    text = re.sub(r"\(cid:\d+\)", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _normalize_text(value: Any) -> str:
    """Edge-noise identity: collapse whitespace, strip, lowercase (for latin)."""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _bbox_union(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _normalized_bbox(
    bbox: tuple[float, float, float, float],
    *,
    page_width: float,
    page_height: float,
) -> list[float]:
    width = max(page_width, 1.0)
    height = max(page_height, 1.0)
    return [
        round(max(0.0, min(1.0, bbox[0] / width)), 6),
        round(max(0.0, min(1.0, bbox[1] / height)), 6),
        round(max(0.0, min(1.0, bbox[2] / width)), 6),
        round(max(0.0, min(1.0, bbox[3] / height)), 6),
    ]


def _inside_bbox(word: dict, bbox: tuple[float, float, float, float]) -> bool:
    center_x = (_float(word.get("x0")) + _float(word.get("x1"))) / 2
    center_y = (_float(word.get("top")) + _float(word.get("bottom"))) / 2
    return bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]


def _markdown_table(rows: list[list[Any]], table_index: int) -> str:
    width = max((len(row) for row in rows), default=0)
    normalized = []
    for row in rows:
        cells = [
            _clean_text(cell).replace("|", "\\|").replace("\n", " ")
            for cell in list(row) + [""] * (width - len(row))
        ]
        normalized.append(cells)
    header = normalized[0]
    body = normalized[1:]
    lines = [
        f"[Table {table_index}]",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _extract_tables(page) -> list[_LayoutLine]:
    try:
        tables = page.find_tables()
    except Exception:
        return []
    if not isinstance(tables, list):
        return []
    extracted = []
    for table in tables:
        try:
            rows = table.extract()
            bbox = tuple(float(value) for value in table.bbox)
        except (AttributeError, TypeError, ValueError):
            continue
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        column_count = max(
            (len(row) for row in rows if isinstance(row, list)),
            default=0,
        )
        populated_rows = sum(
            1
            for row in rows
            if isinstance(row, list) and sum(bool(_clean_text(cell)) for cell in row) >= 2
        )
        if column_count < 2 or populated_rows < 2:
            continue
        extracted.append(
            _LayoutLine(
                text=_markdown_table(rows, len(extracted) + 1),
                bbox=bbox,
                font_size=10.0,
                kind="table",
            )
        )
    return extracted


def _word_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for word in sorted(
        words,
        key=lambda item: (_float(item.get("top")), _float(item.get("x0"))),
    ):
        if not _clean_text(word.get("text")):
            continue
        if rows:
            row_top = median(_float(item.get("top")) for item in rows[-1])
            tolerance = max(2.0, _float(word.get("height"), 8.0) * 0.35)
            if abs(_float(word.get("top")) - row_top) <= tolerance:
                rows[-1].append(word)
                continue
        rows.append([word])
    return rows


def _segments_from_words(words: list[dict], page_width: float) -> list[_LayoutLine]:
    lines = []
    split_gap = max(24.0, page_width * 0.045)
    for row in _word_rows(words):
        ordered = sorted(row, key=lambda item: _float(item.get("x0")))
        segments: list[list[dict]] = [[]]
        for word in ordered:
            if segments[-1]:
                previous_x1 = _float(segments[-1][-1].get("x1"))
                if _float(word.get("x0")) - previous_x1 > split_gap:
                    segments.append([])
            segments[-1].append(word)
        for segment in segments:
            text = _clean_text(" ".join(_clean_text(item.get("text")) for item in segment))
            if not text:
                continue
            bbox = (
                min(_float(item.get("x0")) for item in segment),
                min(_float(item.get("top")) for item in segment),
                max(_float(item.get("x1")) for item in segment),
                max(_float(item.get("bottom")) for item in segment),
            )
            font_sizes = [_float(item.get("size"), 10.0) for item in segment]
            bold_words = sum("bold" in str(item.get("fontname") or "").lower() for item in segment)
            lines.append(
                _LayoutLine(
                    text=text,
                    bbox=bbox,
                    font_size=max(font_sizes, default=10.0),
                    bold=bold_words >= max(1, math.ceil(len(segment) * 0.5)),
                )
            )
    return lines


def _detect_column_gutter(
    lines: list[_LayoutLine],
    page_width: float,
) -> float | None:
    text_lines = [line for line in lines if line.kind == "text"]
    if len(text_lines) < 6:
        return None
    best: tuple[float, float] | None = None
    for step in range(31):
        candidate = page_width * (0.35 + step * 0.01)
        left = sum(line.bbox[2] < candidate for line in text_lines)
        right = sum(line.bbox[0] > candidate for line in text_lines)
        crossing = len(text_lines) - left - right
        if left < 3 or right < 3:
            continue
        crossing_ratio = crossing / len(text_lines)
        if crossing_ratio > 0.14:
            continue
        balance_penalty = abs(left - right) / len(text_lines)
        score = crossing_ratio + balance_penalty * 0.1
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1] if best is not None else None


def _order_lines(
    lines: list[_LayoutLine],
    page_width: float,
) -> tuple[list[_LayoutLine], str]:
    gutter = _detect_column_gutter(lines, page_width)
    if gutter is None:
        return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0])), "single_column"

    left = sorted(
        (line for line in lines if line.bbox[2] <= gutter),
        key=lambda line: (line.bbox[1], line.bbox[0]),
    )
    right = sorted(
        (line for line in lines if line.bbox[0] >= gutter),
        key=lambda line: (line.bbox[1], line.bbox[0]),
    )
    spanning = sorted(
        (line for line in lines if line.bbox[0] < gutter < line.bbox[2]),
        key=lambda line: (line.bbox[1], line.bbox[0]),
    )
    ordered: list[_LayoutLine] = []
    lower_bound = -1.0
    for divider in spanning:
        ordered.extend(line for line in left if lower_bound <= line.bbox[1] < divider.bbox[1])
        ordered.extend(line for line in right if lower_bound <= line.bbox[1] < divider.bbox[1])
        ordered.append(divider)
        lower_bound = divider.bbox[1]
    ordered.extend(line for line in left if line.bbox[1] >= lower_bound)
    ordered.extend(line for line in right if line.bbox[1] >= lower_bound)
    return ordered, "multi_column"


def _heading_level(line: _LayoutLine, body_size: float) -> int | None:
    if line.kind != "text":
        return None
    text = line.text.strip()
    if not text or len(text) > 160 or len(text.split()) > 20:
        return None
    sentence_ending = text.endswith((".", "。", "!", "！", "?", "？", ";", "；"))
    ratio = line.font_size / max(body_size, 1.0)
    if ratio >= 1.9:
        return 1
    if ratio >= 1.5:
        return 2
    if ratio >= 1.2 and not sentence_ending:
        return 3
    if line.bold and ratio >= 0.98 and len(text) <= 100 and not sentence_ending:
        return 3
    return None


def _body_font_size(lines: list[_LayoutLine]) -> float:
    body_candidates = [
        line.font_size for line in lines if line.kind == "text" and len(line.text) >= 40
    ]
    if not body_candidates:
        body_candidates = [line.font_size for line in lines if line.kind == "text"]
    return median(body_candidates) if body_candidates else 10.0


def _update_heading_stack(
    stack: list[str],
    *,
    level: int,
    title: str,
) -> None:
    del stack[max(0, level - 1) :]
    while len(stack) < level - 1:
        stack.append(stack[-1] if stack else title)
    stack.append(title)


def _page_content_and_spans(
    lines: list[_LayoutLine],
    *,
    page_width: float,
    page_height: float,
    display_name: str,
    heading_stack: list[str],
) -> tuple[str, list[dict], list[dict]]:
    body_size = _body_font_size(lines)
    content = ""
    line_spans = []
    for line in lines:
        if not line.text:
            continue
        if content:
            content += "\n"
        start = len(content)
        content += line.text
        end = len(content)
        level = _heading_level(line, body_size)
        if level is not None:
            _update_heading_stack(
                heading_stack,
                level=level,
                title=line.text[:255],
            )
        section_path = list(heading_stack) if heading_stack else [display_name]
        line_spans.append(
            {
                "char_start": start,
                "char_end": end,
                "bbox": _normalized_bbox(
                    line.bbox,
                    page_width=page_width,
                    page_height=page_height,
                ),
                "kind": line.kind,
                "heading_level": level,
                "section_title": section_path[-1],
                "section_path": section_path,
            }
        )

    section_spans = []
    for line_span in line_spans:
        path = line_span["section_path"]
        starts_new_section = (
            not section_spans
            or line_span["heading_level"] is not None
            or section_spans[-1]["section_path"] != path
        )
        if starts_new_section:
            section_spans.append(
                {
                    "char_start": line_span["char_start"],
                    "char_end": line_span["char_end"],
                    "section_title": line_span["section_title"],
                    "section_path": path,
                    "heading_level": line_span["heading_level"],
                    "bbox": line_span["bbox"],
                }
            )
            continue
        section = section_spans[-1]
        section["char_end"] = line_span["char_end"]
        section["bbox"] = list(
            _bbox_union(
                [
                    tuple(section["bbox"]),
                    tuple(line_span["bbox"]),
                ]
            )
        )
    return content, line_spans, section_spans


class PDFLoader(BaseLoader):
    """Adaptive PDF loader with layout metadata and OCR fallback."""

    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        ocr_min_chars: int = 24,
        ocr_resolution: int = 180,
        ocr_min_confidence: float = 0.45,
        ocr_engine_factory: Callable[[], Any] | None = None,
        edge_band_ratio: float = 0.08,
        edge_min_pages: int = 3,
        edge_repeat_ratio: float = 0.2,
        edge_max_len: int = 40,
    ):
        self.ocr_enabled = bool(ocr_enabled)
        self.ocr_min_chars = max(0, int(ocr_min_chars))
        self.ocr_resolution = max(72, min(400, int(ocr_resolution)))
        self.ocr_min_confidence = max(0.0, min(1.0, float(ocr_min_confidence)))
        self.edge_band_ratio = max(0.01, min(0.3, float(edge_band_ratio)))
        self.edge_min_pages = max(2, int(edge_min_pages))
        self.edge_repeat_ratio = max(0.0, min(1.0, float(edge_repeat_ratio)))
        self.edge_max_len = max(2, int(edge_max_len))
        self._ocr_engine_factory = ocr_engine_factory
        self._ocr_engine = None

    def _get_ocr_engine(self):
        if self._ocr_engine is None:
            self._ocr_engine = create_ocr_engine(self._ocr_engine_factory)
        return self._ocr_engine

    def _ocr_lines(self, page) -> tuple[list[_LayoutLine], float | None]:
        import numpy as np

        image = page.to_image(
            resolution=self.ocr_resolution,
            antialias=True,
        ).original.convert("RGB")
        result = self._get_ocr_engine()(np.asarray(image))
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        if boxes is None:
            return [], None
        lines = []
        accepted_scores = []
        scale_x = page.width / max(image.width, 1)
        scale_y = page.height / max(image.height, 1)
        for box, text, score in zip(boxes, texts, scores):
            cleaned = _clean_text(text)
            confidence = _float(score)
            if not cleaned or confidence < self.ocr_min_confidence:
                continue
            xs = [_float(point[0]) for point in box]
            ys = [_float(point[1]) for point in box]
            bbox = (
                min(xs) * scale_x,
                min(ys) * scale_y,
                max(xs) * scale_x,
                max(ys) * scale_y,
            )
            lines.append(
                _LayoutLine(
                    text=cleaned,
                    bbox=bbox,
                    font_size=max(1.0, bbox[3] - bbox[1]),
                )
            )
            accepted_scores.append(confidence)
        mean_confidence = sum(accepted_scores) / len(accepted_scores) if accepted_scores else None
        return lines, mean_confidence

    @staticmethod
    def _text_lines(page, tables: list[_LayoutLine]) -> list[_LayoutLine]:
        try:
            deduped = page.dedupe_chars()
            words = deduped.extract_words(
                extra_attrs=["fontname", "size"],
                use_text_flow=False,
            )
        except Exception:
            words = []
        if not isinstance(words, list):
            words = []
        table_boxes = [table.bbox for table in tables]
        words = [
            word for word in words if not any(_inside_bbox(word, bbox) for bbox in table_boxes)
        ]
        lines = _segments_from_words(words, float(page.width))
        if lines:
            return lines
        plain_text = page.extract_text() or ""
        fallback_lines = [_clean_text(line) for line in plain_text.splitlines()]
        fallback_lines = [line for line in fallback_lines if line]
        if not fallback_lines:
            return []
        line_height = float(page.height) / max(len(fallback_lines), 1)
        return [
            _LayoutLine(
                text=line,
                bbox=(
                    0.0,
                    index * line_height,
                    float(page.width),
                    min(float(page.height), (index + 1) * line_height),
                ),
                font_size=10.0,
            )
            for index, line in enumerate(fallback_lines)
        ]

    def _edge_noise_norms(self, file) -> tuple[set[str], set[str]]:
        """Document-level: collect edge-band short text across distinct pages (no OCR).

        A line counts as a header/footer candidate only when it sits in an edge band,
        is short, and its normalized text repeats across a minimum number of distinct
        pages AND a minimum fraction of the document. Header vs footer are collected
        separately so a top-of-body sentence is never conflated with a bottom footer.
        This is a conservative rule: it suppresses real page furniture without deleting
        short text that legitimately appears in only a few body pages.
        """
        import pdfplumber

        total_pages = len(file.pages)
        edge_pages: dict[tuple[str, str], set[int]] = {}
        for page_number, page in enumerate(file.pages, start=1):
            try:
                stat_lines = self._text_lines(page, [])
            except Exception:
                stat_lines = []
            for line in stat_lines:
                text = _clean_text(line.text)
                if not text or len(text) > self.edge_max_len:
                    continue
                nb = _normalized_bbox(
                    line.bbox,
                    page_width=float(page.width),
                    page_height=float(page.height),
                )
                # pdfplumber 坐标自底向上：顶部 y 大、底部 y 小。
                if nb[3] > 1.0 - self.edge_band_ratio:
                    band = "top"
                elif nb[1] < self.edge_band_ratio:
                    band = "bottom"
                else:
                    continue
                key = (band, _normalize_text(text))
                edge_pages.setdefault(key, set()).add(page_number)
            page.close()
        header_norms: set[str] = set()
        footer_norms: set[str] = set()
        for (band, norm), pages in edge_pages.items():
            enough_pages = len(pages) >= self.edge_min_pages
            enough_ratio = total_pages == 0 or len(pages) / total_pages >= self.edge_repeat_ratio
            if enough_pages and enough_ratio:
                if band == "top":
                    header_norms.add(norm)
                else:
                    footer_norms.add(norm)
        return header_norms, footer_norms

    def _is_edge_noise(
        self,
        line,
        *,
        page_width: float,
        page_height: float,
        header_norms: set[str],
        footer_norms: set[str],
    ) -> bool:
        text = _clean_text(line.text)
        if not text or len(text) > self.edge_max_len:
            return False
        nb = _normalized_bbox(line.bbox, page_width=page_width, page_height=page_height)
        # pdfplumber 坐标自底向上：顶部 y 大、底部 y 小。
        if nb[3] > 1.0 - self.edge_band_ratio:
            band, norms = "top", header_norms
        elif nb[1] < self.edge_band_ratio:
            band, norms = "bottom", footer_norms
        else:
            return False
        return _normalize_text(text) in norms

    def _load_pdf(self, file_path: str) -> List[Document]:
        import pdfplumber

        with open(file_path, "rb") as source:
            document_id = hashlib.sha256(source.read()).hexdigest()
        display_name = os.path.basename(file_path)
        documents = []
        heading_stack: list[str] = []
        with pdfplumber.open(file_path) as file:
            total_pages = len(file.pages)
            header_norms, footer_norms = self._edge_noise_norms(file)
            for page_number, page in enumerate(file.pages, start=1):
                plain_text = page.extract_text() or ""
                use_ocr = (
                    self.ocr_enabled and len(re.sub(r"\s+", "", plain_text)) < self.ocr_min_chars
                )
                tables: list[_LayoutLine] = []
                confidence = None
                if use_ocr:
                    lines, confidence = self._ocr_lines(page)
                    extraction_method = "ocr"
                else:
                    tables = _extract_tables(page)
                    lines = self._text_lines(page, tables)
                    lines.extend(tables)
                    extraction_method = "text+tables" if tables else "text"
                ordered_lines, layout_type = _order_lines(
                    lines,
                    float(page.width),
                )
                filtered_lines = []
                suppressed_edges = 0
                for line in ordered_lines:
                    if self._is_edge_noise(
                        line,
                        page_width=float(page.width),
                        page_height=float(page.height),
                        header_norms=header_norms,
                        footer_norms=footer_norms,
                    ):
                        suppressed_edges += 1
                        continue
                    filtered_lines.append(line)
                content, line_spans, section_spans = _page_content_and_spans(
                    filtered_lines,
                    page_width=float(page.width),
                    page_height=float(page.height),
                    display_name=display_name,
                    heading_stack=heading_stack,
                )
                documents.append(
                    Document(
                        page_content=content,
                        metadata={
                            "reference": file_path,
                            "document_id": document_id,
                            "display_name": display_name,
                            "page_number": page_number,
                            "page_label": str(page_number),
                            "total_pages": total_pages,
                            "parser_version": PDF_PARSER_VERSION,
                            "extraction_method": extraction_method,
                            "extraction_confidence": (
                                round(confidence, 6) if confidence is not None else None
                            ),
                            "layout_type": layout_type,
                            "page_width": round(float(page.width), 3),
                            "page_height": round(float(page.height), 3),
                            "edge_noise_suppressed": suppressed_edges,
                            "_line_spans": line_spans,
                            "_section_spans": section_spans,
                        },
                    )
                )
                page.close()
        return documents

    def load_file(self, file_path: str) -> List[Document]:
        """Load one supported file into page-aware documents."""
        lower_path = file_path.lower()
        if lower_path.endswith(".pdf"):
            return self._load_pdf(file_path)
        if lower_path.endswith((".txt", ".md")):
            with open(file_path, "r", encoding="utf-8") as file:
                return [
                    Document(
                        page_content=file.read(),
                        metadata={"reference": file_path},
                    )
                ]
        raise ValueError(f"Unsupported file type: {file_path}")

    @property
    def supported_file_types(self) -> List[str]:
        return ["pdf", "md", "txt"]
