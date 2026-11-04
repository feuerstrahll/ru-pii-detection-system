"""Load pii-bench entity and domain JSONL exports using taxonomy-v2 labels."""

import json
from pathlib import Path

from app.schemas.models import DocumentSegment, EntityType
from evaluation.metrics import Annotation


PII_BENCH_LABEL_MAPPING: dict[str, EntityType] = {
    "NAME": EntityType.PERSON,
    "PHONE_NUMBER": EntityType.PHONE,
    "EMAIL": EntityType.EMAIL,
    "ADDRESS": EntityType.ADDRESS,
    "BANK_CARD_NUMBER": EntityType.BANK_CARD,
    "CVC": EntityType.CVC,
    "INN": EntityType.TAX_ID,
    "KPP": EntityType.KPP,
    "OGRN": EntityType.ORG_REGISTRATION_ID,
    "OGRNIP": EntityType.ORG_REGISTRATION_ID,
    "SNILS": EntityType.SOCIAL_SECURITY_ID,
    "PASSPORT_NUMBER": EntityType.PASSPORT,
    "TOKEN": EntityType.TOKEN,
}


def load_pii_bench_views(dataset: Path) -> dict[str, list[tuple[list[DocumentSegment], set[Annotation]]]]:
    """Return the official entity and domain views from local JSONL exports."""
    return {view: _load_view(dataset / f"{view}.jsonl", view) for view in ("entity", "domain")}


def _load_view(path: Path, view: str) -> list[tuple[list[DocumentSegment], set[Annotation]]]:
    if not path.is_file():
        raise FileNotFoundError(f"pii-bench {view} view is missing: {path}")
    documents: list[tuple[list[DocumentSegment], set[Annotation]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            documents.append(_convert_row(row, view))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid pii-bench {view} row at line {line_number}: {error}") from error
    return documents


def _convert_row(row: dict[str, object], view: str) -> tuple[list[DocumentSegment], set[Annotation]]:
    document_id = str(row["id"])
    text = str(row["text"])
    segment_id = "body"
    segment = DocumentSegment(
        document_id=document_id,
        segment_id=segment_id,
        original_text=text,
        source_locator={"dataset": "hivetrace/pii-bench", "view": view, "domain": str(row["domain"])},
    )
    annotations: set[Annotation] = set()
    for entity in row["entities"]:  # type: ignore[union-attr]
        if not isinstance(entity, dict):
            raise ValueError("entity must be an object")
        label = str(entity["type"])
        try:
            entity_type = PII_BENCH_LABEL_MAPPING[label]
        except KeyError as error:
            raise ValueError(f"unmapped pii-bench label: {label}") from error
        start, end = int(entity["start"]), int(entity["end"])
        if not (0 <= start < end <= len(text)):
            raise ValueError(f"invalid entity offsets for {label}: [{start}, {end})")
        annotations.add(Annotation(document_id, segment_id, start, end, entity_type))
    return [segment], annotations
