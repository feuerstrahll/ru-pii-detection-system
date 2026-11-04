"""Explainable engineered scorer for aggregated detector evidence."""

from app.core.config import DetectorConfig
from app.schemas.models import DetectedEntity, EntityCandidate, EntityType, EvidenceSignal


class EngineeredEvidenceScorer:
    """Scores evidence features configured in deterministic_v1.json; it is not calibrated."""

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config

    def score(self, group: list[EntityCandidate]) -> DetectedEntity:
        representative = group[0]
        evidence = [item for candidate in group for item in candidate.evidence]
        statuses = {item.status for item in evidence}
        contributions = self._contributions(representative.entity_type, evidence, statuses)
        raw_score = sum(contributions.values())
        confidence = min(1.0, max(0.0, raw_score))
        decision = self._decision(representative.entity_type, statuses, raw_score)
        return DetectedEntity(
            entity_type=representative.entity_type,
            span=representative.span,
            normalized_value=representative.normalized_value,
            confidence=confidence,
            raw_score=raw_score,
            score_contributions=contributions,
            decision=decision,
            evidence=evidence,
            candidate_ids=[candidate.candidate_id for candidate in group],
        )

    def _contributions(self, entity_type: EntityType, evidence: list, statuses: set[str]) -> dict[str, float]:
        configured = self._config.contributions
        result: dict[str, float] = {}
        if any(item.signal == EvidenceSignal.REGEX for item in evidence):
            result["regex_candidate"] = configured["regex_candidate"]
        if "format_valid" in statuses:
            key = (
                "passport_format_valid" if entity_type == EntityType.PASSPORT_RF
                else "email_format_valid" if entity_type == EntityType.EMAIL
                else "format_valid"
            )
            result[key] = configured[key]
        if "checksum_valid" in statuses:
            result["checksum_valid"] = configured["checksum_valid"]
        if "strong_context" in statuses:
            result["strong_context"] = configured["strong_context"]
        if "conflicting_context" in statuses:
            result["conflicting_context"] = configured["conflicting_context"]
        if "low_ocr_confidence" in statuses:
            result["low_ocr_confidence"] = configured["low_ocr_confidence"]
        if entity_type == EntityType.BANK_CARD and "identifier_overlap_ogrn_card" in statuses:
            result["identifier_overlap"] = configured["identifier_overlap"]
        has_ner = any(item.signal == EvidenceSignal.NER for item in evidence)
        has_independent_signal = any(item.signal in {EvidenceSignal.REGEX, EvidenceSignal.VALIDATOR, EvidenceSignal.CONTEXT}
                                     for item in evidence)
        if has_ner and has_independent_signal:
            result["ner_agreement"] = configured["ner_agreement"]
        return result

    def _decision(self, entity_type: EntityType, statuses: set[str], raw_score: float) -> str:
        if "invalid" in statuses:
            return "reject"
        for status in statuses:
            decision = self._config.conflict_decisions.get(entity_type, {}).get(status)
            if decision:
                return decision
        if statuses & self._config.force_review_statuses:
            return "review"
        if entity_type in self._config.require_context_for and "strong_context" not in statuses:
            return "review"
        if raw_score >= self._config.accept_threshold:
            return "accept"
        if raw_score >= self._config.review_threshold:
            return "review"
        return "reject"
