"""Composition root for the deterministic detection baseline."""

from app.core.config import DetectorConfig
from app.core.parsers import ParseResult
from app.detection.aggregation import aggregate_candidates
from app.detection.conflicts import IdentifierConflictEvidenceProducer
from app.detection.deterministic import ContextEvidenceProducer, OCRConfidenceEvidenceProducer, RegexCandidateGenerator, StructuredValidator
from app.detection.scoring import EngineeredEvidenceScorer
from app.detection.semantic import SemanticModelAdapter
from app.schemas.models import (
    DetectionResult, DetectedEntity, DetectorEvidence, DocumentSegment, EntityCandidate, EntityType,
    EvidenceSignal, OriginalRange, TextSpan,
)


class DeterministicPipeline:
    """Runs configured rules, validators, context, aggregation, and scoring."""

    def __init__(self, config: DetectorConfig) -> None:
        self._config = config
        self._generator = RegexCandidateGenerator(config)
        self._validator = StructuredValidator(config)
        self._context = ContextEvidenceProducer(config)
        self._ocr = OCRConfidenceEvidenceProducer(config)
        self._conflicts = IdentifierConflictEvidenceProducer(config)
        self._scorer = EngineeredEvidenceScorer(config)

    def detect(self, segments: list[DocumentSegment]) -> DetectionResult:
        enriched: list[EntityCandidate] = []
        for segment in segments:
            for candidate in self._generator.detect(segment):
                evidence = [*candidate.evidence, *self._validator.validate(candidate), *self._context.analyze(candidate, segment)]
                ocr = self._ocr.analyze(segment)
                if ocr:
                    evidence.append(ocr)
                enriched.append(candidate.model_copy(update={"evidence": evidence}))
        entities = [self._scorer.score(group) for group in aggregate_candidates(self._conflicts.analyze(enriched))]
        document_id = segments[0].document_id if segments else ""
        return DetectionResult(
            document_id=document_id,
            entities=entities,
            candidate_count=len(enriched),
            config_version=self._config.version,
        )

    def detect_parsed(self, parsed: ParseResult) -> DetectionResult:
        """Run the normal detector path while retaining ingestion status."""
        return self.detect(parsed.segments).model_copy(update={
            "document_id": parsed.document_id,
            "processing_status": parsed.status,
            "errors": parsed.errors,
        })


class HybridPipeline:
    """Add frozen semantic PERSON detections to the deterministic result."""

    def __init__(self, config: DetectorConfig, semantic_config: dict[str, object], adapter: SemanticModelAdapter) -> None:
        if (semantic_config.get("inference_labels") != ["ru_pii_person", "ru_pii_location"]
                or semantic_config.get("label_mapping") != {"ru_pii_person": "PERSON"}
                or semantic_config.get("address_decision") != "not_enabled"
                or adapter.model_id != semantic_config.get("candidate_id")
                or adapter.threshold != semantic_config.get("threshold")
                or EntityType.PERSON in config.enabled_entities):
            raise ValueError("hybrid PERSON adapter does not match the frozen inference contract")
        self._deterministic = DeterministicPipeline(config)
        self._semantic_config = semantic_config
        self._adapter = adapter

    def detect(self, segments: list[DocumentSegment]) -> DetectionResult:
        result = self._deterministic.detect(segments)
        semantic_entities: list[DetectedEntity] = []
        seen: set[tuple[str, str, int, int]] = set()
        for segment in segments:
            for prediction in self._adapter.predict(segment.original_text, self._semantic_config["inference_labels"]):
                if prediction.label != "ru_pii_person":
                    continue
                if (prediction.model_id != self._adapter.model_id
                        or prediction.raw_score < self._adapter.threshold
                        or not 0 <= prediction.start < prediction.end <= len(segment.original_text)
                        or segment.original_text[prediction.start:prediction.end] != prediction.text):
                    raise ValueError("semantic PERSON prediction does not match its source segment")
                key = (segment.document_id, segment.segment_id, prediction.start, prediction.end)
                if key in seen:
                    continue
                seen.add(key)
                span = TextSpan(document_id=segment.document_id, segment_id=segment.segment_id,
                                start=prediction.start, end=prediction.end, text=prediction.text,
                                original_ranges=[OriginalRange(start=prediction.start, end=prediction.end)])
                candidate = EntityCandidate(entity_type=EntityType.PERSON, span=span, evidence=[DetectorEvidence(
                    detector_id=self._adapter.model_id,
                    detector_version=str(self._semantic_config["revision"]),
                    signal=EvidenceSignal.NER, status="semantic_person", score=prediction.raw_score,
                    attributes={"label": prediction.label},
                )])
                semantic_entities.append(DetectedEntity(
                    entity_type=EntityType.PERSON, span=span, confidence=prediction.raw_score,
                    raw_score=prediction.raw_score, score_contributions={"semantic_person": prediction.raw_score},
                    decision="review", evidence=candidate.evidence, candidate_ids=[candidate.candidate_id],
                ))
        # Valid structured identifiers take precedence over a PERSON span that
        # includes them; rejected rule candidates cannot suppress PERSON.
        semantic_candidate_count = len(semantic_entities)
        structured = [entity for entity in result.entities if entity.decision in {"accept", "review"}]
        semantic_entities = [person for person in semantic_entities if not any(
            item.span.segment_id == person.span.segment_id
            and item.span.document_id == person.span.document_id
            and item.span.start < person.span.end and person.span.start < item.span.end
            for item in structured
        )]
        return result.model_copy(update={
            "entities": [*result.entities, *semantic_entities],
            "candidate_count": result.candidate_count + semantic_candidate_count,
            "config_version": f"{result.config_version}+{self._semantic_config['version']}",
        })

    def detect_parsed(self, parsed: ParseResult) -> DetectionResult:
        return self.detect(parsed.segments).model_copy(update={
            "document_id": parsed.document_id,
            "processing_status": parsed.status,
            "errors": parsed.errors,
        })
