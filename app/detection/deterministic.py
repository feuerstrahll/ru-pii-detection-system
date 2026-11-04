"""Deterministic candidate generation and validators for v1 entities."""

import re

from app.core.config import DetectorConfig
from app.schemas.models import (
    DetectorEvidence,
    DocumentSegment,
    EntityCandidate,
    EntityType,
    EvidenceSignal,
    OriginalRange,
    TextSpan,
)


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


class RegexCandidateGenerator:
    """Generates offset-preserving candidates from configured taxonomy patterns."""

    detector_id = "configured_regex"

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config
        self._patterns = {kind: re.compile(pattern) for kind, pattern in config.patterns.items()}

    def detect(self, segment: DocumentSegment) -> list[EntityCandidate]:
        candidates: list[EntityCandidate] = []
        for entity_type, pattern in self._patterns.items():
            for match in pattern.finditer(segment.original_text):
                value = match.group(0)
                span = TextSpan(
                    document_id=segment.document_id,
                    segment_id=segment.segment_id,
                    start=match.start(),
                    end=match.end(),
                    text=value,
                    original_ranges=[OriginalRange(start=match.start(), end=match.end())],
                )
                candidates.append(EntityCandidate(
                    entity_type=entity_type,
                    span=span,
                    normalized_value=_digits(value) if entity_type not in {EntityType.EMAIL} else value.casefold(),
                    evidence=[DetectorEvidence(
                        detector_id=self.detector_id,
                        detector_version=self._config.version,
                        signal=EvidenceSignal.REGEX,
                        status="candidate",
                        score=1.0,
                        attributes={"pattern_entity": entity_type.value},
                    )],
                ))
        return candidates


class StructuredValidator:
    """Returns validation evidence without deleting the original candidate."""

    detector_id = "structured_validator"

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config

    def validate(self, candidate: EntityCandidate) -> list[DetectorEvidence]:
        value = candidate.normalized_value or ""
        return self._evidence(candidate.entity_type, value)

    def _evidence(self, entity_type: EntityType, value: str) -> list[DetectorEvidence]:
        format_valid = self._format_valid(entity_type, value)
        evidence = [self._item("format_valid" if format_valid else "invalid", "format")]
        if not format_valid or entity_type in {EntityType.EMAIL, EntityType.PHONE, EntityType.PASSPORT_RF, EntityType.KPP}:
            return evidence
        checksum_status = self._checksum_status(entity_type, value)
        evidence.append(self._item(checksum_status, "checksum"))
        return evidence

    def _item(self, status: str, stage: str) -> DetectorEvidence:
        return DetectorEvidence(
            detector_id=self.detector_id,
            detector_version=self._config.version,
            signal=EvidenceSignal.VALIDATOR,
            status=status,
            score=1.0 if status in {"format_valid", "format_valid_only", "checksum_valid"} else 0.0,
            attributes={"stage": stage},
        )

    @staticmethod
    def _format_valid(entity_type: EntityType, value: str) -> bool:
        if entity_type == EntityType.EMAIL:
            return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))
        if entity_type == EntityType.PHONE:
            return len(value) == 11 and value[0] in "78"
        if entity_type == EntityType.SOCIAL_SECURITY_ID:
            return len(value) == 11 and value.isdigit()
        if entity_type == EntityType.TAX_ID:
            return len(value) in {10, 12} and value.isdigit()
        if entity_type == EntityType.PASSPORT_RF:
            return len(value) == 10 and value.isdigit() and 1 <= int(value[:2]) <= 99
        if entity_type == EntityType.ORG_REGISTRATION_ID:
            return len(value) in {13, 15} and value.isdigit()
        if entity_type == EntityType.BANK_CARD:
            return 13 <= len(value) <= 19 and value.isdigit()
        if entity_type == EntityType.KPP:
            return len(value) == 9 and value.isdigit()
        raise ValueError(f"No validator configured for {entity_type}")

    @staticmethod
    def _checksum_status(entity_type: EntityType, value: str) -> str:
        if entity_type == EntityType.SOCIAL_SECURITY_ID:
            return _validate_snils(value)
        if entity_type == EntityType.TAX_ID:
            return "checksum_valid" if _validate_inn(value) else "invalid"
        if entity_type == EntityType.ORG_REGISTRATION_ID:
            return "checksum_valid" if _validate_ogrn(value) else "invalid"
        if entity_type == EntityType.BANK_CARD:
            return "checksum_valid" if _validate_luhn(value) else "invalid"
        raise ValueError(f"No checksum configured for {entity_type}")


def _validate_snils(value: str) -> str:
    if len(value) != 11 or not value.isdigit():
        return "invalid"
    if int(value[:9]) < 1_001_998:
        return "format_valid_checksum_unverifiable"
    total = sum(int(digit) * weight for digit, weight in zip(value[:9], range(9, 0, -1)))
    if total < 100:
        control = total
    elif total in (100, 101):
        control = 0
    else:
        remainder = total % 101
        control = 0 if remainder == 100 else remainder
    return "checksum_valid" if control == int(value[-2:]) else "invalid"


def _validate_inn(value: str) -> bool:
    if len(value) == 10 and value.isdigit():
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(int(x) * y for x, y in zip(value[:9], weights)) % 11 % 10 == int(value[9])
    if len(value) == 12 and value.isdigit():
        w11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        w12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        n11 = sum(int(x) * y for x, y in zip(value[:10], w11)) % 11 % 10
        n12 = sum(int(x) * y for x, y in zip(value[:11], w12)) % 11 % 10
        return n11 == int(value[10]) and n12 == int(value[11])
    return False


def _validate_ogrn(value: str) -> bool:
    if len(value) == 13 and value.isdigit():
        return (int(value[:12]) % 11) % 10 == int(value[12])
    if len(value) == 15 and value.isdigit():
        return (int(value[:14]) % 13) % 10 == int(value[14])
    return False


def _validate_luhn(value: str) -> bool:
    if not value.isdigit() or not 13 <= len(value) <= 19:
        return False
    total = 0
    parity = len(value) % 2
    for index, char in enumerate(value):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


class ContextEvidenceProducer:
    """Produces reproducible local-keyword evidence from configured terms."""

    detector_id = "configured_context"

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config
        self._window = config.context_window_characters

    def analyze(self, candidate: EntityCandidate, segment: DocumentSegment) -> list[DetectorEvidence]:
        terms = self._config.context_terms.get(candidate.entity_type)
        conflicting_terms = self._config.conflicting_context_terms.get(candidate.entity_type, ())
        if not terms and not conflicting_terms:
            return []
        start = max(0, candidate.span.start - self._window)
        end = min(len(segment.original_text), candidate.span.end + self._window)
        context = segment.original_text[start:end].casefold()
        output: list[DetectorEvidence] = []
        matched, distance = self._nearest_term(terms or (), context, candidate.span.start - start, candidate.span.end - start)
        output.append(DetectorEvidence(
            detector_id=self.detector_id,
            detector_version=self._config.version,
            signal=EvidenceSignal.CONTEXT,
            status="strong_context" if matched and distance <= self._config.strong_context_max_distance else "context_absent",
            score=1.0 if matched and distance <= self._config.strong_context_max_distance else 0.0,
            attributes={"matched_term": matched or "", "distance": distance},
        ))
        conflict, conflict_distance = self._nearest_term(conflicting_terms, context, candidate.span.start - start, candidate.span.end - start)
        if conflict:
            output.append(DetectorEvidence(
                detector_id=self.detector_id,
                detector_version=self._config.version,
                signal=EvidenceSignal.CONTEXT,
                status="conflicting_context",
                score=1.0,
                attributes={"matched_term": conflict, "distance": conflict_distance},
            ))
        return output

    @staticmethod
    def _nearest_term(terms: tuple[str, ...], context: str, candidate_start: int, candidate_end: int) -> tuple[str | None, int]:
        closest: tuple[str | None, int] = (None, -1)
        for term in terms:
            offset = context.find(term.casefold())
            while offset >= 0:
                term_end = offset + len(term)
                distance = candidate_start - term_end if term_end <= candidate_start else offset - candidate_end if offset >= candidate_end else 0
                if closest[0] is None or distance < closest[1]:
                    closest = (term, distance)
                offset = context.find(term.casefold(), offset + 1)
        return closest


class OCRConfidenceEvidenceProducer:
    """Records low OCR confidence as evidence instead of conflating it with model failure."""

    detector_id = "ocr_confidence"

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config

    def analyze(self, segment: DocumentSegment) -> DetectorEvidence | None:
        if segment.ocr_confidence is None:
            return None
        low = segment.ocr_confidence < self._config.ocr_confidence_threshold
        return DetectorEvidence(
            detector_id=self.detector_id,
            detector_version=self._config.version,
            signal=EvidenceSignal.OCR,
            status="low_ocr_confidence" if low else "ocr_confidence_ok",
            score=segment.ocr_confidence,
            attributes={"ocr_confidence": segment.ocr_confidence},
        )
