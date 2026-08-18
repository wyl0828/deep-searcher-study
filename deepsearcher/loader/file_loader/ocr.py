"""Shared OCR engine factory (extracted from PDFLoader, behavior unchanged).

PDFLoader and ImageLoader share one RapidOCR initialisation path so OCR behavior
stays identical across formats. An optional engine factory lets tests inject a
deterministic fake engine.
"""

from __future__ import annotations

from typing import Any, Callable


def create_ocr_engine(engine_factory: Callable[[], Any] | None = None) -> Any:
    """Return a configured RapidOCR engine (or an injected factory result)."""
    if engine_factory is not None:
        return engine_factory()
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "OCR is required for image-only documents. Install rapidocr and onnxruntime."
        ) from exc
    return RapidOCR()
