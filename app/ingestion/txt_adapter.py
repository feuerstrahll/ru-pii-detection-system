"""Extract UTF-8 plain text without changing character offsets."""

from pathlib import Path

from app.core.parsers import ParseResult
from app.schemas.models import DocumentSegment, ProcessingStatus


class TextDocumentParser:
    supported_suffixes = frozenset({".txt"})

    def parse(self, document_id: str, source: Path) -> ParseResult:
        try:
            text = source.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError) as error:
            return ParseResult(document_id, [], ProcessingStatus.FAILED, [
                {"page_number": 0, "stage": "txt_extract", "error": f"{type(error).__name__}: {error}"}
            ])
        segments = [DocumentSegment(document_id=document_id, segment_id="body", original_text=text,
                                    source_locator={"extraction": "utf8_text"})] if text else []
        return ParseResult(document_id, segments, ProcessingStatus.COMPLETED, [])
