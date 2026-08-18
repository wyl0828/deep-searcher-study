"""ImageLoader tests (OCR-only; fake engine injection)."""

from __future__ import annotations

from deepsearcher.loader.file_loader.image_loader import ImageLoader


class _Result:
    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class _FakeOcr:
    def __init__(self, result):
        self._result = result

    def __call__(self, _array):
        return self._result


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_ocr_text_and_mean_confidence(tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(_png_bytes())
    fake = _FakeOcr(
        _Result(
            boxes=[
                [[0, 0], [4, 0], [4, 4], [0, 4]],
                [[0, 6], [4, 6], [4, 10], [0, 10]],
            ],
            txts=["标题", "内容行"],
            scores=[0.9, 0.8],
        )
    )
    loader = ImageLoader(ocr_engine_factory=lambda: fake)
    documents = loader.load_file(str(path))
    assert len(documents) == 1
    assert documents[0].page_content == "标题\n内容行"
    assert documents[0].metadata["extraction_method"] == "ocr"
    assert documents[0].metadata["extraction_confidence"] == 0.85


def test_low_confidence_lines_dropped(tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(_png_bytes())
    fake = _FakeOcr(
        _Result(
            boxes=[[[0, 0], [4, 0], [4, 4], [0, 4]]],
            txts=["保留", "丢弃"],
            scores=[0.9, 0.2],
        )
    )
    loader = ImageLoader(ocr_min_confidence=0.5, ocr_engine_factory=lambda: fake)
    documents = loader.load_file(str(path))
    assert len(documents) == 1
    assert documents[0].page_content == "保留"


def test_no_text_returns_empty_list(tmp_path):
    path = tmp_path / "blank.png"
    path.write_bytes(_png_bytes())
    fake = _FakeOcr(_Result(boxes=None, txts=None, scores=None))
    loader = ImageLoader(ocr_engine_factory=lambda: fake)
    assert loader.load_file(str(path)) == []
