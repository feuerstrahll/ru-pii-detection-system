from pathlib import Path

import pytest

from app.core.config import OCRConfig
from app.ingestion.ocr_adapter import OCRDocumentParser
from app.schemas.models import ProcessingStatus, SegmentKind


FIXTURES = Path(__file__).parent / "fixtures" / "ocr"


@pytest.fixture
def parser() -> OCRDocumentParser:
    return OCRDocumentParser(OCRConfig("ocr-test", 200, "eng", 0.8, ""))


def test_ocr_data_creates_auditable_segment_and_flags_low_confidence(parser, tmp_path):
    segment = parser._segment_from_ocr_data("doc", 1, {
        "text": ["Email", "test@example.com", "uncertain"],
        "conf": ["98", "91", "30"],
        "left": [10, 60, 200], "top": [20, 20, 20],
        "width": [40, 120, 60], "height": [15, 15, 15],
    }, tmp_path / "scan.png")

    assert segment.kind == SegmentKind.OCR_BLOCK
    assert segment.original_text == "Email test@example.com uncertain"
    assert segment.ocr_confidence == pytest.approx(0.73)
    assert segment.bounding_box == (10.0, 20.0, 260.0, 35.0)
    assert segment.source_locator["words"][2]["low_confidence"] is True
    assert segment.source_locator["words"][1]["start"] == len("Email ")


@pytest.mark.parametrize("fixture_name", ["clean.png", "rotated.png", "low_resolution.png"])
def test_image_sources_produce_page_segments(parser, monkeypatch, fixture_name):
    source = FIXTURES / fixture_name

    def fake_ocr(document_id, page_number, image_bytes, path):
        return parser._segment_from_ocr_data(document_id, page_number, {
            "text": ["hello"], "conf": ["95"], "left": [0], "top": [0], "width": [10], "height": [10],
        }, path)

    monkeypatch.setattr(parser, "_ocr_image", fake_ocr)
    result = parser.parse("doc", source)

    assert result.status == ProcessingStatus.COMPLETED
    assert len(result.segments) == 1
    assert result.segments[0].page_number == 1


def test_multipage_image_keeps_successful_pages_when_one_page_ocr_fails(parser, tmp_path, monkeypatch):
    pillow = pytest.importorskip("PIL.Image")
    source = tmp_path / "scan.tiff"
    first = pillow.new("RGB", (20, 20), "white")
    first.save(source, save_all=True, append_images=[pillow.new("RGB", (20, 20), "white")])
    calls = 0

    def fake_ocr(document_id, page_number, image_bytes, path):
        nonlocal calls
        calls += 1
        if page_number == 2:
            raise RuntimeError("engine unavailable")
        return parser._segment_from_ocr_data(document_id, page_number, {
            "text": ["hello"], "conf": ["95"], "left": [0], "top": [0], "width": [10], "height": [10],
        }, path)

    monkeypatch.setattr(parser, "_ocr_image", fake_ocr)
    result = parser.parse("doc", source)

    assert result.status == ProcessingStatus.PARTIAL
    assert len(result.segments) == 1
    assert len(result.errors) == 1
    assert calls == 2


def test_corrupted_source_is_a_failed_result(parser, tmp_path):
    source = tmp_path / "corrupted.png"
    source.write_bytes(b"not an image")

    result = parser.parse("doc", source)

    assert result.status == ProcessingStatus.FAILED
    assert result.segments == []
    assert result.errors[0]["page_number"] == 0


def test_digital_pdf_words_align_with_entity_offsets(parser, tmp_path):
    fitz = pytest.importorskip("fitz")
    from app.api.main import _entity_view
    from app.core.config import load_config
    from app.core.pipeline import DeterministicPipeline

    source = tmp_path / "page.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((50, 80), "Email: ivan@example.ru", fontsize=20)
    pdf.save(source)
    pdf.close()
    parsed = parser.parse("doc", source)
    entity = DeterministicPipeline(load_config(Path("configs/deterministic_v3.json"))).detect_parsed(parsed).entities[0]
    view = _entity_view(entity, parsed.segments[0])
    assert view["text"] == "ivan@example.ru"
    assert view["coordinate_system"] == "pdf_points"
    assert view["bounding_box"] is not None
