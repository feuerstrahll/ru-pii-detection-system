"""Extract DOCX paragraphs as source-linked segments for local detection."""

from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app.core.parsers import ParseResult
from app.schemas.models import DocumentSegment, ProcessingStatus


_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_XML_BYTES = 20 * 1024 * 1024


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(
        node.text or "" if node.tag == f"{_NS}t" else "\t" if node.tag == f"{_NS}tab" else "\n"
        for node in paragraph.iter()
        if node.tag in {f"{_NS}t", f"{_NS}tab", f"{_NS}br"}
    )


def preview_blocks(content: bytes) -> list[dict]:
    """Return DOCX paragraphs and table cells for a local visual review, without converting files."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            member = archive.getinfo("word/document.xml")
            if member.file_size > _MAX_XML_BYTES:
                raise ValueError("DOCX text is too large")
            root = ElementTree.fromstring(archive.read(member))
    except (BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise ValueError("DOCX preview is invalid") from error
    body = root.find(f"{_NS}body")
    if body is None:
        raise ValueError("DOCX has no document body")
    numbers = {paragraph: index for index, paragraph in enumerate(body.iter(f"{_NS}p"), start=1)}

    def paragraph_view(paragraph: ElementTree.Element) -> dict:
        style = paragraph.find(f"{_NS}pPr/{_NS}pStyle")
        runs = []
        offset = 0
        for run in paragraph.iter(f"{_NS}r"):
            text = _paragraph_text(run)
            if text:
                props = run.find(f"{_NS}rPr")
                runs.append({"start": offset, "end": offset + len(text),
                             "bold": props is not None and props.find(f"{_NS}b") is not None,
                             "italic": props is not None and props.find(f"{_NS}i") is not None})
                offset += len(text)
        return {"segment_id": f"paragraph-{numbers[paragraph]}", "text": _paragraph_text(paragraph),
                "style": style.get(f"{_NS}val") if style is not None else None, "runs": runs}

    blocks = []
    for child in body:
        if child.tag == f"{_NS}p":
            blocks.append({"kind": "paragraph", "paragraph": paragraph_view(child)})
        elif child.tag == f"{_NS}tbl":
            rows = []
            for row in child.findall(f"{_NS}tr"):
                rows.append([[paragraph_view(paragraph) for paragraph in cell.iter(f"{_NS}p")]
                             for cell in row.findall(f"{_NS}tc")])
            blocks.append({"kind": "table", "rows": rows})
    return blocks


class DocxDocumentParser:
    supported_suffixes = frozenset({".docx"})

    def parse(self, document_id: str, source: Path) -> ParseResult:
        try:
            with ZipFile(source) as archive:
                member = archive.getinfo("word/document.xml")
                if member.file_size > _MAX_XML_BYTES:
                    raise ValueError("DOCX text is too large for the demo")
                xml = archive.read(member)
            root = ElementTree.fromstring(xml)
            body = root.find(f"{_NS}body")
            if body is None:
                raise ValueError("DOCX has no document body")
            segments: list[DocumentSegment] = []
            for paragraph_number, paragraph in enumerate(body.iter(f"{_NS}p"), start=1):
                text = _paragraph_text(paragraph)
                if text.strip():
                    segments.append(DocumentSegment(
                        document_id=document_id,
                        segment_id=f"paragraph-{paragraph_number}",
                        original_text=text,
                        source_locator={"paragraph": paragraph_number, "source": str(source)},
                    ))
            return ParseResult(document_id, segments, ProcessingStatus.COMPLETED, [])
        except (BadZipFile, KeyError, ValueError, ElementTree.ParseError, OSError) as error:
            return ParseResult(document_id, [], ProcessingStatus.FAILED, [
                {"page_number": 0, "stage": "docx_extract", "error": f"{type(error).__name__}: {error}"}
            ])
