"""Check that TXT extraction preserves exact Unicode offsets and reports decode failures."""

from app.ingestion.txt_adapter import TextDocumentParser
from app.schemas.models import ProcessingStatus


def test_utf8_text_and_invalid_bytes(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_bytes("Почта: ivan@example.ru".encode("utf-8-sig"))
    parsed = TextDocumentParser().parse("doc", source)
    assert parsed.status == ProcessingStatus.COMPLETED
    assert parsed.segments[0].original_text == "Почта: ivan@example.ru"
    assert parsed.segments[0].original_text[7:22] == "ivan@example.ru"

    source.write_bytes(b"\xff")
    failed = TextDocumentParser().parse("doc", source)
    assert failed.status == ProcessingStatus.FAILED
    assert failed.errors[0]["stage"] == "txt_extract"
