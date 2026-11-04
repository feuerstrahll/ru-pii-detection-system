"""DOCX paragraph extraction keeps exact offsets and source order."""

from zipfile import ZipFile

import pytest

from app.ingestion.docx_adapter import DocxDocumentParser, preview_blocks
from app.schemas.models import ProcessingStatus


def test_docx_paragraphs_include_table_text_and_preserve_offsets(tmp_path):
    source = tmp_path / "sample.docx"
    xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>Почта: </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>ivan@example.ru</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Телефон:</w:t><w:tab/><w:t>+7 912 123-45-67</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>'''.encode("utf-8")
    with ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", xml)

    result = DocxDocumentParser().parse("document", source)

    assert result.status == ProcessingStatus.COMPLETED
    assert [segment.original_text for segment in result.segments] == [
        "Почта: ivan@example.ru", "Телефон:\t+7 912 123-45-67",
    ]
    assert [segment.source_locator["paragraph"] for segment in result.segments] == [1, 2]
    assert result.segments[0].original_text[7:22] == "ivan@example.ru"
    blocks = preview_blocks(source.read_bytes())
    assert [block["kind"] for block in blocks] == ["paragraph", "table"]
    assert blocks[0]["paragraph"]["segment_id"] == result.segments[0].segment_id
    assert blocks[0]["paragraph"]["runs"][1] == {"start": 7, "end": 22, "bold": True, "italic": False}
    assert blocks[1]["rows"][0][0][0]["text"] == result.segments[1].original_text


def test_corrupt_docx_returns_failed_status(tmp_path):
    source = tmp_path / "bad.docx"
    source.write_bytes(b"invalid")
    result = DocxDocumentParser().parse("document", source)
    assert result.status == ProcessingStatus.FAILED
    assert result.segments == []
    assert result.errors[0]["stage"] == "docx_extract"
    with pytest.raises(ValueError, match="DOCX preview is invalid"):
        preview_blocks(source.read_bytes())
