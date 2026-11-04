"""Configuration loading for the deterministic baseline."""

import json
from dataclasses import dataclass
from pathlib import Path

from app.schemas.models import EntityType


@dataclass(frozen=True)
class DetectorConfig:
    version: str
    enabled_entities: frozenset[EntityType]
    patterns: dict[EntityType, str]
    context_terms: dict[EntityType, tuple[str, ...]]
    conflicting_context_terms: dict[EntityType, tuple[str, ...]]
    context_window_characters: int
    contributions: dict[str, float]
    strong_context_max_distance: int
    ocr_confidence_threshold: float
    accept_threshold: float
    review_threshold: float
    require_context_for: frozenset[EntityType]
    force_review_statuses: frozenset[str]
    conflict_decisions: dict[EntityType, dict[str, str]]


@dataclass(frozen=True)
class OCRConfig:
    version: str
    dpi: int
    language: str
    low_confidence_threshold: float
    tesseract_config: str


def load_config(path: str | Path) -> DetectorConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    enabled = frozenset(EntityType(value) for value in raw["enabled_entities"])
    patterns = {EntityType(key): value for key, value in raw["patterns"].items()}
    if set(patterns) != enabled:
        raise ValueError("patterns and enabled_entities must name the same entity types")
    return DetectorConfig(
        version=raw["version"],
        enabled_entities=enabled,
        patterns=patterns,
        context_terms={EntityType(key): tuple(value) for key, value in raw["context_terms"].items()},
        conflicting_context_terms={EntityType(key): tuple(value) for key, value in raw["conflicting_context_terms"].items()},
        context_window_characters=int(raw["context_window_characters"]),
        contributions={key: float(value) for key, value in raw["engineered_scoring"]["contributions"].items()},
        strong_context_max_distance=int(raw["engineered_scoring"]["strong_context_max_distance"]),
        ocr_confidence_threshold=float(raw["engineered_scoring"]["ocr_confidence_threshold"]),
        accept_threshold=float(raw["engineered_scoring"]["accept_threshold"]),
        review_threshold=float(raw["engineered_scoring"]["review_threshold"]),
        require_context_for=frozenset(EntityType(value) for value in raw["engineered_scoring"]["require_context_for"]),
        force_review_statuses=frozenset(raw["engineered_scoring"]["force_review_statuses"]),
        conflict_decisions={
            EntityType(entity_type): {str(status): str(decision) for status, decision in decisions.items()}
            for entity_type, decisions in raw.get("conflict_decisions", {}).items()
        },
    )


def load_ocr_config(path: str | Path) -> OCRConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    threshold = float(raw["low_confidence_threshold"])
    if int(raw["dpi"]) <= 0:
        raise ValueError("dpi must be positive")
    if not 0 <= threshold <= 1:
        raise ValueError("low_confidence_threshold must be between zero and one")
    return OCRConfig(
        version=str(raw["version"]),
        dpi=int(raw["dpi"]),
        language=str(raw["language"]),
        low_confidence_threshold=threshold,
        tesseract_config=str(raw.get("tesseract_config", "")),
    )
