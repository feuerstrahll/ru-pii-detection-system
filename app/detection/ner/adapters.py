"""Local-only adapters that convert NER outputs to domain candidates."""

from pathlib import Path
from typing import Protocol

from app.schemas.models import (
    DetectorEvidence,
    DocumentSegment,
    EntityCandidate,
    EntityType,
    EvidenceSignal,
    OriginalRange,
    TextSpan,
)


class ModelUnavailableError(RuntimeError):
    """Raised when a benchmark dependency or explicitly local model is unavailable."""


class NERAdapter(Protocol):
    candidate_id: str
    model_ref: str

    def predict(self, segments: list[DocumentSegment]) -> list[EntityCandidate]: ...

    def model_size_bytes(self) -> int: ...


def _candidate(segment: DocumentSegment, start: int, end: int, entity_type: EntityType,
               candidate_id: str, model_ref: str, score: float) -> EntityCandidate:
    text = segment.original_text[start:end]
    return EntityCandidate(
        entity_type=entity_type,
        span=TextSpan(
            document_id=segment.document_id,
            segment_id=segment.segment_id,
            start=start,
            end=end,
            text=text,
            original_ranges=[OriginalRange(start=start, end=end)],
        ),
        evidence=[DetectorEvidence(
            detector_id=candidate_id,
            detector_version=model_ref,
            signal=EvidenceSignal.NER,
            status="candidate",
            score=max(0.0, min(1.0, float(score))),
        )],
    )


def _directory_size(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class SpacyAdapter:
    def __init__(self, candidate_id: str, model_ref: str, label_mapping: dict[str, EntityType]) -> None:
        self.candidate_id, self.model_ref, self._mapping = candidate_id, model_ref, label_mapping
        try:
            import spacy
            self._nlp = spacy.load(model_ref)
        except (ImportError, OSError) as error:
            raise ModelUnavailableError(f"spaCy model {model_ref!r} is unavailable locally") from error

    def predict(self, segments: list[DocumentSegment]) -> list[EntityCandidate]:
        output: list[EntityCandidate] = []
        for segment in segments:
            for entity in self._nlp(segment.original_text).ents:
                entity_type = self._mapping.get(entity.label_)
                if entity_type:
                    output.append(_candidate(segment, entity.start_char, entity.end_char, entity_type,
                                             self.candidate_id, self.model_ref, 1.0))
        return output

    def model_size_bytes(self) -> int:
        return _directory_size(Path(self._nlp.path))


class TransformersAdapter:
    def __init__(self, candidate_id: str, model_path: Path, model_ref: str,
                 label_mapping: dict[str, EntityType]) -> None:
        self.candidate_id, self.model_ref, self._mapping = candidate_id, model_ref, label_mapping
        if not model_path.is_dir():
            raise ModelUnavailableError(f"local model snapshot is missing: {model_path}")
        try:
            from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
            tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
            model = AutoModelForTokenClassification.from_pretrained(model_path, local_files_only=True)
            self._pipeline = pipeline("token-classification", model=model, tokenizer=tokenizer,
                                      aggregation_strategy="simple", device=-1)
        except ImportError as error:
            raise ModelUnavailableError("transformers is not installed") from error
        self._model_path = model_path

    def predict(self, segments: list[DocumentSegment]) -> list[EntityCandidate]:
        output: list[EntityCandidate] = []
        for segment in segments:
            for item in self._pipeline(segment.original_text):
                entity_type = self._mapping.get(str(item["entity_group"]))
                if entity_type:
                    output.append(_candidate(segment, int(item["start"]), int(item["end"]), entity_type,
                                             self.candidate_id, self.model_ref, float(item["score"])))
        return output

    def model_size_bytes(self) -> int:
        return _directory_size(self._model_path)


class GLiNERAdapter:
    def __init__(self, candidate_id: str, model_path: Path, model_ref: str,
                 labels: dict[EntityType, str], threshold: float) -> None:
        self.candidate_id, self.model_ref, self._labels, self._threshold = candidate_id, model_ref, labels, threshold
        if not model_path.is_dir():
            raise ModelUnavailableError(f"local model snapshot is missing: {model_path}")
        try:
            from gliner import GLiNER
            self._model = GLiNER.from_pretrained(model_path)
        except ImportError as error:
            raise ModelUnavailableError("gliner is not installed") from error
        self._model_path = model_path

    def predict(self, segments: list[DocumentSegment]) -> list[EntityCandidate]:
        reverse_labels = {label: entity_type for entity_type, label in self._labels.items()}
        output: list[EntityCandidate] = []
        for segment in segments:
            for item in self._model.predict_entities(segment.original_text, list(reverse_labels), threshold=self._threshold):
                entity_type = reverse_labels[item["label"]]
                output.append(_candidate(segment, int(item["start"]), int(item["end"]), entity_type,
                                         self.candidate_id, self.model_ref, float(item["score"])))
        return output

    def model_size_bytes(self) -> int:
        return _directory_size(self._model_path)
