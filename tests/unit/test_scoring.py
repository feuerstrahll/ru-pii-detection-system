from dataclasses import replace
from pathlib import Path

import pytest

from app.core import load_config
from app.core.pipeline import DeterministicPipeline
from app.detection.aggregation import aggregate_candidates
from app.detection.deterministic import ContextEvidenceProducer, RegexCandidateGenerator, StructuredValidator
from app.detection.scoring import EngineeredEvidenceScorer
from app.schemas.models import DetectorEvidence, DocumentSegment, EntityCandidate, EntityType, EvidenceSignal


def pipeline(config=None):
    return DeterministicPipeline(config or load_config(Path("configs/deterministic_v1.json")))


def entity(text: str, entity_type: EntityType, *, ocr_confidence: float | None = None):
    result = pipeline().detect([DocumentSegment(document_id="d", segment_id="s", original_text=text, ocr_confidence=ocr_confidence)])
    return next(item for item in result.entities if item.entity_type == entity_type)


def test_checksum_and_nearby_context_are_separate_explainable_contributions():
    detected = entity("ИНН: 7707083893", EntityType.TAX_ID)
    assert detected.raw_score == pytest.approx(1.05)
    assert detected.confidence == 1.0
    assert detected.score_contributions == {
        "regex_candidate": 0.35,
        "format_valid": 0.20,
        "checksum_valid": 0.25,
        "strong_context": 0.25,
    }
    context = next(item for item in detected.evidence if item.status == "strong_context")
    assert context.attributes == {"matched_term": "инн", "distance": 2}


def test_passport_format_gets_lower_weight_and_no_checksum_contribution():
    detected = entity("Паспорт: 45 08 123456", EntityType.PASSPORT_RF)
    assert detected.raw_score == pytest.approx(0.70)
    assert detected.decision == "review"
    assert detected.score_contributions["passport_format_valid"] == 0.10
    assert "checksum_valid" not in detected.score_contributions


def test_low_ocr_confidence_is_retained_as_a_penalty_not_a_model_error():
    detected = entity("ИНН: 7707083893", EntityType.TAX_ID, ocr_confidence=0.50)
    assert detected.score_contributions["low_ocr_confidence"] == -0.15
    assert any(item.status == "low_ocr_confidence" for item in detected.evidence)


def test_conflicting_context_is_configured_evidence_not_a_hidden_heuristic():
    config = replace(load_config(Path("configs/deterministic_v1.json")), conflicting_context_terms={EntityType.TAX_ID: ("order",)})
    entities = DeterministicPipeline(config).detect([
        DocumentSegment(document_id="d", segment_id="s", original_text="order; ИНН: 7707083893")
    ]).entities
    detected = next(item for item in entities if item.entity_type == EntityType.TAX_ID)
    assert detected.score_contributions["conflicting_context"] == -0.30
    assert any(item.status == "conflicting_context" for item in detected.evidence)


def test_ner_adds_agreement_only_when_its_candidate_has_matching_non_ner_evidence():
    config = load_config(Path("configs/deterministic_v1.json"))
    segment = DocumentSegment(document_id="d", segment_id="s", original_text="ИНН: 7707083893")
    candidate = next(item for item in RegexCandidateGenerator(config).detect(segment) if item.entity_type == EntityType.TAX_ID)
    enriched = candidate.model_copy(update={"evidence": [
        *candidate.evidence,
        *StructuredValidator(config).validate(candidate),
        *ContextEvidenceProducer(config).analyze(candidate, segment),
    ]})
    ner = EntityCandidate(
        entity_type=EntityType.TAX_ID,
        span=candidate.span,
        normalized_value=candidate.normalized_value,
        evidence=[DetectorEvidence(detector_id="ner", detector_version="test", signal=EvidenceSignal.NER,
                                   status="candidate", score=0.8)],
    )
    detected = EngineeredEvidenceScorer(config).score(aggregate_candidates([enriched, ner])[0])
    assert detected.score_contributions["ner_agreement"] == 0.20
