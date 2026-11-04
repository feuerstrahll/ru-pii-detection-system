"""Load evaluation documents with immutable, segment-local annotations."""

import json
from pathlib import Path

from app.schemas.models import DocumentSegment, EntityType
from evaluation.metrics import Annotation


def load_document(path: Path) -> tuple[list[DocumentSegment], set[Annotation]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    document_id = raw["document_id"]
    segments = [DocumentSegment(
        document_id=document_id,
        segment_id=item["segment_id"],
        original_text=item["text"],
        page_number=item.get("page_number"),
        source_locator=item.get("source_locator", {}),
    ) for item in raw["segments"]]
    annotations = {Annotation(
        document_id=document_id,
        segment_id=item["segment_id"],
        start=item["start"],
        end=item["end"],
        entity_type=EntityType(item["type"]),
    ) for item in raw["entities"]}
    return segments, annotations


__all__ = ["load_document"]
