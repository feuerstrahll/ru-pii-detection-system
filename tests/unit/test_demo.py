"""The demo highlights source spans and escapes uploaded document text."""

from pathlib import Path

import pytest

from app.api.demo import _digital_words, _result_html
from app.core.config import load_config, load_ocr_config
from app.core.parsers import ParseResult
from app.core.pipeline import DeterministicPipeline
from app.ingestion.ocr_adapter import OCRDocumentParser
from app.schemas.models import DocumentSegment, ProcessingStatus


def test_result_shows_exact_source_span_without_rendering_markup():
    segment = DocumentSegment(document_id="demo", segment_id="paragraph-1",
                              original_text="<script>alert(1)</script> Почта: ivan@example.ru",
                              source_locator={"paragraph": 1})
    parsed = ParseResult("demo", [segment], ProcessingStatus.COMPLETED, [])
    result = DeterministicPipeline(load_config(Path("configs/deterministic_v3.json"))).detect_parsed(parsed)

    html = _result_html("sample.docx", parsed, result)

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<mark class="accept" title="Электронная почта">ivan@example.ru</mark>' in html
    assert "Абзац 1" in html
    assert "символы 34–48" in html


@pytest.mark.parametrize("rotation", [0, 90])
def test_pdf_preview_places_boxes_on_source_page(tmp_path, rotation):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "page.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Contact test@example.com")
        page.set_rotation(rotation)
        document.save(source)
    parsed = OCRDocumentParser(load_ocr_config("configs/ocr_v1.json")).parse("demo", source)
    result = DeterministicPipeline(load_config("configs/deterministic_v3.json")).detect_parsed(parsed)

    html = _result_html("page.pdf", parsed, result, source)

    assert parsed.status == ProcessingStatus.COMPLETED
    assert 'src="data:image/png;base64,' in html
    assert '<a id="finding-' in html
    assert 'class="bbox accept"' in html
    assert 'title="Электронная почта: test@example.com"' in html
    assert 'href="#finding-' in html


def test_image_preview_uses_ocr_word_coordinates(tmp_path):
    image = pytest.importorskip("PIL.Image")
    source = tmp_path / "page.png"
    image.new("RGB", (200, 100), "white").save(source)
    segment = DocumentSegment(document_id="demo", segment_id="ocr-page-1", page_number=1,
                              original_text="test@example.com", source_locator={"words": [
                                  {"start": 0, "end": 16, "bounding_box": (20, 30, 180, 50)}
                              ]})
    parsed = ParseResult("demo", [segment], ProcessingStatus.COMPLETED, [])
    result = DeterministicPipeline(load_config("configs/deterministic_v3.json")).detect_parsed(parsed)

    html = _result_html("page.png", parsed, result, source)

    assert 'src="data:image/jpeg;base64,' in html
    assert 'style="left:10.0000%;top:30.0000%;width:80.0000%;height:20.0000%"' in html
    assert 'class="bbox accept"' in html


def test_pdf_word_mapping_keeps_repeated_occurrences_and_stops_on_mismatch():
    words = [(0, 0, 10, 10, "id"), (20, 0, 30, 10, "id")]
    assert [(start, end) for start, end, _ in _digital_words("id id", words)] == [(0, 2), (3, 5)]
    assert _digital_words("other id", words) == []
