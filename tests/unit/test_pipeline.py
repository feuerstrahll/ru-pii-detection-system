"""Check PERSON composition and source offsets in the production pipeline."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core import load_config
from app.core.pipeline import HybridPipeline
from app.detection.semantic import SemanticPrediction
from app.schemas.models import DocumentSegment, EntityType, EvidenceSignal


@dataclass
class FakeAdapter:
    model_id: str = "ru_pii_ner_gliner2_5"
    threshold: float = 0.7
    wrong_text: bool = False
    span_text: str = "Иван Петров"

    def predict(self, text, labels):
        assert labels == ["ru_pii_person", "ru_pii_location"]
        start = text.index(self.span_text)
        value = self.span_text if not self.wrong_text else "other"
        return [SemanticPrediction(value, start, start + len(self.span_text), "ru_pii_person", 0.9, self.model_id),
                SemanticPrediction("Москва", 0, 6, "ru_pii_location", 0.8, self.model_id)]

    def model_size_bytes(self):
        return 0


def test_hybrid_pipeline_preserves_person_provenance_and_deterministic_result():
    config = load_config(Path("configs/deterministic_v3.json"))
    semantic = {"version": "semantic_person_frozen_v2", "candidate_id": "ru_pii_ner_gliner2_5", "threshold": 0.7,
                "revision": "fixed", "inference_labels": ["ru_pii_person", "ru_pii_location"],
                "label_mapping": {"ru_pii_person": "PERSON"}, "address_decision": "not_enabled"}
    segment = DocumentSegment(document_id="doc", segment_id="body", original_text="Иван Петров ivan@example.ru")
    result = HybridPipeline(config, semantic, FakeAdapter()).detect([segment])
    person = next(item for item in result.entities if item.entity_type == EntityType.PERSON)

    assert {item.entity_type for item in result.entities} == {EntityType.PERSON, EntityType.EMAIL}
    assert result.candidate_count == 2
    assert person.span.text == segment.original_text[person.span.start:person.span.end]
    assert person.span.original_ranges[0].start == person.span.start
    assert person.evidence[0].signal == EvidenceSignal.NER
    assert person.evidence[0].detector_version == "fixed"
    assert person.decision == "review"

    with pytest.raises(ValueError, match="source segment"):
        HybridPipeline(config, semantic, FakeAdapter(wrong_text=True)).detect([segment])

    semantic["inference_labels"] = ["ru_pii_person"]
    with pytest.raises(ValueError, match="frozen inference contract"):
        HybridPipeline(config, semantic, FakeAdapter())


def test_hybrid_suppresses_person_over_structured_identifier():
    config = load_config(Path("configs/deterministic_v3.json"))
    semantic = {"version": "semantic_person_frozen_v2", "candidate_id": "ru_pii_ner_gliner2_5", "threshold": 0.7,
                "revision": "fixed", "inference_labels": ["ru_pii_person", "ru_pii_location"],
                "label_mapping": {"ru_pii_person": "PERSON"}, "address_decision": "not_enabled"}
    segment = DocumentSegment(document_id="doc", segment_id="body", original_text="Почта: ivan@example.ru")
    result = HybridPipeline(config, semantic, FakeAdapter(span_text="ivan@example.ru")).detect([segment])
    assert [item.entity_type for item in result.entities if item.decision in {"accept", "review"}] == [EntityType.EMAIL]
    assert result.candidate_count == 2
