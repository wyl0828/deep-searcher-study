"""Image loader using OCR only (P2-A).

OCR-only extraction: page_content is the normalized OCR text and no caption /
description model is introduced (future image understanding is out of scope for
P2-A). OCR with no recognized text returns an empty document list. Confidence is
the mean of the non-empty per-line OCR scores.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Callable, List

from langchain_core.documents import Document

from deepsearcher.loader.file_loader.base import BaseLoader
from deepsearcher.loader.file_loader.ocr import create_ocr_engine

IMAGE_PARSER_VERSION = "rapidocr-v3-image"


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\x00", "").replace("\u00ad", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class ImageLoader(BaseLoader):
    """OCR-only image loader for png/jpg/jpeg (one Document per image)."""

    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        ocr_min_confidence: float = 0.45,
        ocr_engine_factory: Callable[[], Any] | None = None,
    ):
        self.ocr_enabled = bool(ocr_enabled)
        self.ocr_min_confidence = max(0.0, min(1.0, float(ocr_min_confidence)))
        self._ocr_engine_factory = ocr_engine_factory
        self._ocr_engine = None

    def _get_ocr_engine(self) -> Any:
        if self._ocr_engine is None:
            self._ocr_engine = create_ocr_engine(self._ocr_engine_factory)
        return self._ocr_engine

    def _ocr_image(self, file_path: str) -> tuple[list[str], float | None, int, int]:
        import numpy as np
        from PIL import Image

        with Image.open(file_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            array = np.asarray(rgb)

        result = self._get_ocr_engine()(array)
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        if boxes is None:
            return [], None, width, height

        lines: list[str] = []
        accepted: list[float] = []
        for box, text, score in zip(boxes, texts, scores):
            cleaned = _clean_text(text)
            confidence = _float(score)
            if not cleaned or confidence < self.ocr_min_confidence:
                continue
            lines.append(cleaned)
            accepted.append(confidence)
        mean = sum(accepted) / len(accepted) if accepted else None
        return lines, mean, width, height

    def load_file(self, file_path: str) -> List[Document]:
        lower_path = file_path.lower()
        if not lower_path.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError(f"Unsupported image file type: {file_path}")
        if not self.ocr_enabled:
            return []

        with open(file_path, "rb") as source:
            document_id = hashlib.sha256(source.read()).hexdigest()
        lines, confidence, width, height = self._ocr_image(file_path)
        if not lines:
            return []
        return [
            Document(
                page_content="\n".join(lines),
                metadata={
                    "reference": file_path,
                    "document_id": document_id,
                    "display_name": os.path.basename(file_path),
                    "parser_version": IMAGE_PARSER_VERSION,
                    "extraction_method": "ocr",
                    "extraction_confidence": round(confidence, 6) if confidence else None,
                    "width": width,
                    "height": height,
                },
            )
        ]

    @property
    def supported_file_types(self) -> List[str]:
        return ["png", "jpg", "jpeg"]
