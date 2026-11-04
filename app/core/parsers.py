"""Parser protocol and registration point for ingestion adapters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.schemas.models import DocumentSegment, ProcessingStatus


@dataclass(frozen=True)
class ParseResult:
    """Page-level parser output, including recoverable ingestion failures."""

    document_id: str
    segments: list[DocumentSegment]
    status: ProcessingStatus
    errors: list[dict[str, str | int]]


class DocumentParser(Protocol):
    supported_suffixes: frozenset[str]

    def parse(self, document_id: str, source: Path) -> ParseResult: ...


class ParserRegistry:
    """Selects one registered parser by source suffix."""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser) -> None:
        for suffix in parser.supported_suffixes:
            key = suffix.casefold()
            if key in self._parsers:
                raise ValueError(f"parser already registered for {suffix}")
            self._parsers[key] = parser

    def parse(self, document_id: str, source: Path) -> ParseResult:
        try:
            parser = self._parsers[source.suffix.casefold()]
        except KeyError as error:
            raise ValueError(f"no parser registered for {source.suffix or 'extensionless source'}") from error
        return parser.parse(document_id, source)
