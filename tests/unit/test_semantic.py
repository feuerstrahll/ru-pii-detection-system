from dataclasses import dataclass
from hashlib import sha256

from app.detection.semantic import GLiNER2SemanticAdapter, SemanticContextEvidenceProducer, SemanticPrediction, snapshot_digest
from app.schemas.models import DetectorEvidence, DocumentSegment, EntityCandidate, EntityType, EvidenceSignal, OriginalRange, TextSpan


def test_snapshot_digest_uses_platform_independent_path_order(tmp_path):
    (tmp_path / "README").write_bytes(b"a")
    (tmp_path / "config").write_bytes(b"b")
    expected = sha256(b"README" + b"a" + b"config" + b"b").hexdigest()
    assert snapshot_digest(tmp_path) == expected


@dataclass
class _FakeSemanticAdapter:
    model_id: str = "fake-model"

    def predict(self, text, labels):
        start = text.index("4111")
        return [SemanticPrediction("4111", start, start + 4, "bank card number", 0.9, self.model_id),
                SemanticPrediction("4111", start, start + 4, "organization registration number", 0.8, self.model_id)]

    def model_size_bytes(self):
        return 0


def test_semantic_context_evidence_preserves_candidate_span_and_records_conflicting_signal():
    text = "ОГРН: 4111"
    candidate = EntityCandidate(
        entity_type=EntityType.BANK_CARD,
        span=TextSpan(document_id="doc", segment_id="body", start=6, end=10, text="4111",
                      original_ranges=[OriginalRange(start=6, end=10)]),
        evidence=[DetectorEvidence(detector_id="regex", detector_version="test", signal=EvidenceSignal.REGEX,
                                   status="candidate", score=1.0)],
    )
    producer = SemanticContextEvidenceProducer(_FakeSemanticAdapter(), {"BANK_CARD": "bank card number"},
                                               {"BANK_CARD": ("organization registration number",)})

    evidence = producer.analyze(candidate, DocumentSegment(document_id="doc", segment_id="body", original_text=text))

    assert candidate.span.text == text[candidate.span.start:candidate.span.end]
    assert {item.status for item in evidence} == {"semantic_context_positive", "semantic_context_conflicting"}
    assert all(item.signal == EvidenceSignal.SEMANTIC for item in evidence)


def test_gliner2_adapter_requests_spans_and_confidence(monkeypatch, tmp_path):
    class FakeExtractor:
        def extract_entities(self, text, labels, **kwargs):
            assert kwargs == {"threshold": 0.5, "include_spans": True, "include_confidence": True}
            return {"entities": {"person": [{"text": "Иван", "start": 0, "end": 4, "confidence": 0.8}]}}

    class FakeAutoExtractor:
        @staticmethod
        def from_pretrained(path):
            return FakeExtractor()

    import sys
    import types
    monkeypatch.setitem(sys.modules, "gliner2", types.SimpleNamespace(AutoExtractor=FakeAutoExtractor))

    adapter = GLiNER2SemanticAdapter("model", tmp_path, 0.5)
    assert adapter.predict("Иван", ["person"]) == [SemanticPrediction("Иван", 0, 4, "person", 0.8, "model")]
