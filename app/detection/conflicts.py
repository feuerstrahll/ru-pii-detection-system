"""Cross-candidate evidence for checksum-valid identifier collisions."""

from collections import defaultdict

from app.core.config import DetectorConfig
from app.schemas.models import DetectorEvidence, EntityCandidate, EntityType, EvidenceSignal


class IdentifierConflictEvidenceProducer:
    """Annotates exact OGRN/OGRNIP and card collisions without removing candidates."""

    detector_id = "identifier_conflict"
    status = "identifier_overlap_ogrn_card"

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config

    def analyze(self, candidates: list[EntityCandidate]) -> list[EntityCandidate]:
        if not self._config.conflict_decisions:
            return candidates
        grouped: dict[tuple[str, str, int, int], list[int]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            span = candidate.span
            grouped[(span.document_id, span.segment_id, span.start, span.end)].append(index)
        conflicted: set[int] = set()
        for indexes in grouped.values():
            cards = [index for index in indexes if candidates[index].entity_type == EntityType.BANK_CARD and self._is_valid(candidates[index], "checksum_valid")]
            ogrns = [index for index in indexes if candidates[index].entity_type == EntityType.ORG_REGISTRATION_ID and self._is_valid(candidates[index], "checksum_valid")]
            if cards and ogrns:
                conflicted.update(cards)
                conflicted.update(ogrns)
        return [self._with_conflict(candidate) if index in conflicted else candidate for index, candidate in enumerate(candidates)]

    def _with_conflict(self, candidate: EntityCandidate) -> EntityCandidate:
        evidence = DetectorEvidence(
            detector_id=self.detector_id,
            detector_version=self._config.version,
            signal=EvidenceSignal.CONFLICT,
            status=self.status,
            score=1.0,
            attributes={
                "type": "IDENTIFIER_OVERLAP",
                "candidates": [EntityType.ORG_REGISTRATION_ID.value, EntityType.BANK_CARD.value],
                "exact_span_overlap": True,
                "checks": {"ogrn_checksum_valid": True, "luhn_valid": True},
            },
        )
        return candidate.model_copy(update={"evidence": [*candidate.evidence, evidence]})

    @staticmethod
    def _is_valid(candidate: EntityCandidate, status: str) -> bool:
        return any(item.signal == EvidenceSignal.VALIDATOR and item.status == status for item in candidate.evidence)
