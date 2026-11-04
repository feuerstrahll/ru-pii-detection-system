"""Stable domain models for deterministic detection."""

from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityType(str, Enum):
    PERSON = "PERSON"
    ADDRESS = "ADDRESS"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SOCIAL_SECURITY_ID = "SOCIAL_SECURITY_ID"
    TAX_ID = "TAX_ID"
    PASSPORT = "PASSPORT"
    PASSPORT_RF = "PASSPORT"  # Backward-compatible source alias; PASSPORT is canonical in taxonomy v2.
    ORG_REGISTRATION_ID = "ORG_REGISTRATION_ID"
    BANK_CARD = "BANK_CARD"
    CVC = "CVC"
    KPP = "KPP"
    TOKEN = "TOKEN"


class EvidenceSignal(str, Enum):
    REGEX = "regex"
    NER = "ner"
    VALIDATOR = "validator"
    CONTEXT = "context"
    OCR = "ocr"
    CONFLICT = "conflict"
    SEMANTIC = "semantic"


class SegmentKind(str, Enum):
    PAGE = "page"
    OCR_BLOCK = "ocr_block"


class ProcessingStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class OriginalRange(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_length(self) -> "OriginalRange":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class DocumentSegment(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: str
    segment_id: str
    original_text: str
    kind: SegmentKind = SegmentKind.PAGE
    page_number: int | None = Field(default=None, ge=1)
    source_locator: dict[str, Any] = Field(default_factory=dict)
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    bounding_box: tuple[float, float, float, float] | None = None


class TextSpan(BaseModel):
    document_id: str
    segment_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    original_ranges: list[OriginalRange]

    @model_validator(mode="after")
    def validate_length(self) -> "TextSpan":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class DetectorEvidence(BaseModel):
    detector_id: str
    detector_version: str
    signal: EvidenceSignal
    status: str
    score: float = Field(ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityCandidate(BaseModel):
    candidate_id: UUID = Field(default_factory=uuid4)
    entity_type: EntityType
    span: TextSpan
    normalized_value: str | None = None
    evidence: list[DetectorEvidence] = Field(min_length=1)


class DetectedEntity(BaseModel):
    entity_id: UUID = Field(default_factory=uuid4)
    entity_type: EntityType
    span: TextSpan
    normalized_value: str | None = None
    confidence: float = Field(ge=0, le=1)
    raw_score: float
    score_contributions: dict[str, float]
    decision: str
    evidence: list[DetectorEvidence]
    candidate_ids: list[UUID]


class DetectionResult(BaseModel):
    document_id: str
    entities: list[DetectedEntity]
    candidate_count: int
    config_version: str
    processing_status: ProcessingStatus = ProcessingStatus.COMPLETED
    errors: list[dict[str, str | int]] = Field(default_factory=list)
