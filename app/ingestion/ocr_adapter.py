"""Page-aware OCR parser for image-only PDF pages and image sources."""

from io import BytesIO
import os
from pathlib import Path
import shutil
from statistics import fmean
from typing import Any

from app.core.config import OCRConfig
from app.core.parsers import ParseResult
from app.schemas.models import DocumentSegment, ProcessingStatus, SegmentKind


class OCRDocumentParser:
    """Extract digital PDF text or OCR rasterized pages with word provenance."""

    supported_suffixes = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"})

    def __init__(self, config: OCRConfig) -> None:
        self._config = config

    def parse(self, document_id: str, source: Path) -> ParseResult:
        try:
            if source.suffix.casefold() == ".pdf":
                segments, errors = self._parse_pdf(document_id, source)
            else:
                segments, errors = self._parse_image(document_id, source)
        except Exception as error:
            return ParseResult(document_id, [], ProcessingStatus.FAILED, [self._error(0, "source_open", error)])
        status = ProcessingStatus.COMPLETED if not errors else ProcessingStatus.PARTIAL if segments else ProcessingStatus.FAILED
        return ParseResult(document_id, segments, status, errors)

    def _parse_pdf(self, document_id: str, source: Path) -> tuple[list[DocumentSegment], list[dict[str, str | int]]]:
        try:
            import fitz
        except ImportError as error:
            raise RuntimeError("PyMuPDF is required for PDF OCR ingestion") from error
        segments: list[DocumentSegment] = []
        errors: list[dict[str, str | int]] = []
        with fitz.open(source) as document:
            for index, page in enumerate(document, start=1):
                try:
                    text = page.get_text("text").strip()
                    if text:
                        words = []
                        cursor = 0
                        for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                            start = text.find(word, cursor)
                            if start < 0 or text[cursor:start].strip():
                                break
                            end = start + len(word)
                            box = tuple(fitz.Rect(x0, y0, x1, y1) * page.rotation_matrix)
                            words.append({"start": start, "end": end, "bounding_box": box})
                            cursor = end
                        segments.append(DocumentSegment(
                            document_id=document_id,
                            segment_id=f"page-{index}",
                            original_text=text,
                            kind=SegmentKind.PAGE,
                            page_number=index,
                            bounding_box=(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1),
                            source_locator={"source": str(source), "extraction": "digital_text", "words": words},
                        ))
                        continue
                    pixmap = page.get_pixmap(dpi=self._config.dpi)
                    segments.append(self._ocr_image(document_id, index, pixmap.tobytes("png"), source))
                except Exception as error:
                    errors.append(self._error(index, "page_extract_or_ocr", error))
        return segments, errors

    def _parse_image(self, document_id: str, source: Path) -> tuple[list[DocumentSegment], list[dict[str, str | int]]]:
        try:
            from PIL import Image, ImageSequence
        except ImportError as error:
            raise RuntimeError("Pillow is required for image OCR ingestion") from error
        segments: list[DocumentSegment] = []
        errors: list[dict[str, str | int]] = []
        with Image.open(source) as image:
            for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
                try:
                    buffer = BytesIO()
                    frame.convert("RGB").save(buffer, format="PNG")
                    segments.append(self._ocr_image(document_id, index, buffer.getvalue(), source))
                except Exception as error:
                    errors.append(self._error(index, "image_ocr", error))
        return segments, errors

    def _ocr_image(self, document_id: str, page_number: int, image_bytes: bytes, source: Path) -> DocumentSegment:
        try:
            from PIL import Image
            import pytesseract
            from pytesseract import Output
        except ImportError as error:
            raise RuntimeError("install the ocr extra to enable Tesseract ingestion") from error
        command = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if command is None and os.name == "nt":
            installed = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe"
            command = str(installed) if installed.is_file() else None
        if command is None:
            raise RuntimeError("Tesseract executable is unavailable; set TESSERACT_CMD")
        pytesseract.pytesseract.tesseract_cmd = command
        language_dir = Path(__file__).resolve().parents[2] / "models" / "ocr"
        config = self._config.tesseract_config
        if all((language_dir / f"{language}.traineddata").is_file() for language in self._config.language.split("+")):
            config = f'--tessdata-dir {language_dir.as_posix()} {config}'
        with Image.open(BytesIO(image_bytes)) as image:
            data = pytesseract.image_to_data(
                image,
                lang=self._config.language,
                config=config,
                output_type=Output.DICT,
            )
        return self._segment_from_ocr_data(document_id, page_number, data, source)

    def _segment_from_ocr_data(self, document_id: str, page_number: int, data: dict[str, list[Any]], source: Path) -> DocumentSegment:
        words: list[dict[str, Any]] = []
        text_parts: list[str] = []
        confidences: list[float] = []
        for index, raw_text in enumerate(data.get("text", [])):
            text = str(raw_text).strip()
            confidence = self._confidence(data.get("conf", []), index)
            if not text:
                continue
            start = sum(len(part) for part in text_parts) + len(text_parts)
            end = start + len(text)
            box = self._box(data, index)
            low_confidence = confidence is None or confidence < self._config.low_confidence_threshold
            words.append({
                "text": text,
                "start": start,
                "end": end,
                "bounding_box": box,
                "confidence": confidence,
                "low_confidence": low_confidence,
            })
            text_parts.append(text)
            if confidence is not None:
                confidences.append(confidence)
        return DocumentSegment(
            document_id=document_id,
            segment_id=f"ocr-page-{page_number}",
            original_text=" ".join(text_parts),
            kind=SegmentKind.OCR_BLOCK,
            page_number=page_number,
            ocr_confidence=fmean(confidences) if confidences else 0.0,
            bounding_box=self._enclosing_box(words),
            source_locator={
                "source": str(source),
                "extraction": "tesseract",
                "ocr_config_version": self._config.version,
                "ocr_dpi": self._config.dpi,
                "low_confidence_threshold": self._config.low_confidence_threshold,
                "words": words,
            },
        )

    @staticmethod
    def _confidence(raw_confidences: list[Any], index: int) -> float | None:
        try:
            value = float(raw_confidences[index])
        except (IndexError, TypeError, ValueError):
            return None
        return value / 100 if value >= 0 else None

    @staticmethod
    def _box(data: dict[str, list[Any]], index: int) -> tuple[float, float, float, float]:
        left, top, width, height = (float(data[name][index]) for name in ("left", "top", "width", "height"))
        return left, top, left + width, top + height

    @staticmethod
    def _enclosing_box(words: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
        boxes = [word["bounding_box"] for word in words]
        if not boxes:
            return None
        return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)

    @staticmethod
    def _error(page_number: int, stage: str, error: Exception) -> dict[str, str | int]:
        return {"page_number": page_number, "stage": stage, "error": f"{type(error).__name__}: {error}"}
