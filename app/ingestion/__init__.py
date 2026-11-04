"""Source parsers registered at application composition."""

from app.core.config import OCRConfig
from app.core.parsers import ParserRegistry
from .ocr_adapter import OCRDocumentParser


def register_ocr_parser(registry: ParserRegistry, config: OCRConfig) -> None:
    """Register the one production OCR parser with the composition registry."""
    registry.register(OCRDocumentParser(config))


__all__ = ["OCRDocumentParser", "register_ocr_parser"]
