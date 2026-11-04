"""Exact-span metrics shared by deterministic and NER experiments."""

from dataclasses import dataclass

from app.schemas.models import EntityType


@dataclass(frozen=True)
class Annotation:
    document_id: str
    segment_id: str
    start: int
    end: int
    entity_type: EntityType


def score_exact(expected: set[Annotation], predicted: set[Annotation], entity_types: set[EntityType]) -> dict[str, object]:
    scored_types = entity_types | {item.entity_type for item in expected | predicted}
    counts: dict[EntityType, dict[str, int]] = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in scored_types}
    errors: list[dict[str, object]] = []
    for item in predicted & expected:
        counts[item.entity_type]["tp"] += 1
    for item in predicted - expected:
        counts[item.entity_type]["fp"] += 1
        errors.append(_error("FP", item))
    for item in expected - predicted:
        counts[item.entity_type]["fn"] += 1
        errors.append(_error(
            "FN",
            item,
            root_cause="no_detector_configured" if item.entity_type not in entity_types else None,
        ))
    per_class: dict[str, dict[str, float | int]] = {}
    for kind, values in sorted(counts.items(), key=lambda item: item[0].value):
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_class[kind.value] = {**values, "precision": precision, "recall": recall,
                                 "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    return {"per_class": per_class, "errors": errors}


def score_overlap(expected: set[Annotation], predicted: set[Annotation], entity_types: set[EntityType]) -> dict[str, object]:
    """Score same-label spans that overlap, for boundary-error diagnosis only."""
    counts: dict[EntityType, dict[str, int]] = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in entity_types}
    for kind in entity_types:
        remaining = set(item for item in expected if item.entity_type == kind)
        for item in (item for item in predicted if item.entity_type == kind):
            match = next((expected_item for expected_item in remaining if _overlaps(item, expected_item)), None)
            if match:
                counts[kind]["tp"] += 1
                remaining.remove(match)
            else:
                counts[kind]["fp"] += 1
        counts[kind]["fn"] += len(remaining)
    return {"per_class": _per_class_metrics(counts)}


def _overlaps(left: Annotation, right: Annotation) -> bool:
    return left.document_id == right.document_id and left.segment_id == right.segment_id and left.start < right.end and right.start < left.end


def _per_class_metrics(counts: dict[EntityType, dict[str, int]]) -> dict[str, dict[str, float | int]]:
    per_class: dict[str, dict[str, float | int]] = {}
    for kind, values in sorted(counts.items(), key=lambda item: item[0].value):
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_class[kind.value] = {**values, "precision": precision, "recall": recall,
                                 "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    return per_class


def _error(kind: str, item: Annotation, root_cause: str | None = None) -> dict[str, object]:
    error = {"kind": kind, "document_id": item.document_id, "segment_id": item.segment_id,
            "start": item.start, "end": item.end, "type": item.entity_type.value,
            "suggested_category": "unclassified", "review_required": True}
    if root_cause:
        error["root_cause"] = root_cause
    return error
