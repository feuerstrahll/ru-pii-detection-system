"""Local semantic-model contracts and non-decisive context evidence."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol, Sequence

from app.schemas.models import DetectorEvidence, DocumentSegment, EntityCandidate, EvidenceSignal


class ModelUnavailableError(RuntimeError):
    """Raised when an explicitly local semantic runtime or snapshot is unavailable."""


def snapshot_digest(path: Path) -> str:
    """Hash the exact local model files using the experiment's snapshot convention."""
    if not path.is_dir():
        raise ModelUnavailableError(f"local model snapshot is missing: {path}")
    digest = sha256()
    for item in sorted(path.rglob("*"), key=lambda entry: entry.relative_to(path).as_posix()):
        if item.is_file():
            relative = item.relative_to(path)
            if relative.parts[0] == ".cache":
                continue
            digest.update(relative.as_posix().encode())
            with item.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SemanticPrediction:
    text: str
    start: int
    end: int
    label: str
    raw_score: float
    model_id: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("semantic prediction offsets must form a non-empty half-open span")
        if not 0.0 <= self.raw_score <= 1.0:
            raise ValueError("semantic prediction score must be between zero and one")


class SemanticModelAdapter(Protocol):
    model_id: str
    threshold: float

    def predict(self, text: str, labels: Sequence[str]) -> list[SemanticPrediction]: ...

    def model_size_bytes(self) -> int: ...


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class GLiNERSemanticAdapter:
    """Adapter for a local GLiNER v2 snapshot."""

    def __init__(self, model_id: str, model_path: Path, threshold: float, device: str = "cpu") -> None:
        if not model_path.is_dir():
            raise ModelUnavailableError(f"local model snapshot is missing: {model_path}")
        try:
            from gliner import GLiNER
        except ImportError as error:
            raise ModelUnavailableError("gliner is not installed") from error
        self.model_id, self.threshold, self._path = model_id, threshold, model_path
        self._model = GLiNER.from_pretrained(model_path)
        if device != "cpu" and hasattr(self._model, "to"):
            self._model.to(device)

    def predict(self, text: str, labels: Sequence[str]) -> list[SemanticPrediction]:
        return [
            SemanticPrediction(str(item["text"]), int(item["start"]), int(item["end"]), str(item["label"]),
                               float(item["score"]), self.model_id)
            for item in self._model.predict_entities(text, list(labels), threshold=self.threshold)
        ]

    def model_size_bytes(self) -> int:
        return _directory_size(self._path)


class GLiNER2SemanticAdapter:
    """Adapter for a local GLiNER2 snapshot; it fails rather than inventing absent scores."""

    def __init__(self, model_id: str, model_path: Path, threshold: float, device: str = "cpu") -> None:
        if not model_path.is_dir():
            raise ModelUnavailableError(f"local model snapshot is missing: {model_path}")
        try:
            from gliner2 import AutoExtractor
        except ImportError as error:
            raise ModelUnavailableError("GLiNER2 or one of its dependencies could not be imported") from error
        self.model_id, self.threshold, self._path = model_id, threshold, model_path
        self._model = AutoExtractor.from_pretrained(model_path)
        if device != "cpu" and hasattr(self._model, "to"):
            self._model.to(device)

    def predict(self, text: str, labels: Sequence[str]) -> list[SemanticPrediction]:
        result = self._model.extract_entities(
            text, list(labels), threshold=self.threshold, include_spans=True, include_confidence=True,
        )
        entities = result.get("entities", result) if isinstance(result, dict) else result
        output: list[SemanticPrediction] = []
        if not isinstance(entities, dict):
            raise RuntimeError("GLiNER2 returned an unsupported entity payload")
        for label, items in entities.items():
            for item in items:
                if "confidence" not in item:
                    raise RuntimeError("GLiNER2 prediction omitted confidence")
                output.append(SemanticPrediction(str(item["text"]), int(item["start"]), int(item["end"]), str(label),
                                                 float(item["confidence"]), self.model_id))
        return output

    def model_size_bytes(self) -> int:
        return _directory_size(self._path)


class SemanticContextEvidenceProducer:
    """Adds semantic context evidence while preserving the deterministic candidate unchanged."""

    detector_id = "semantic_context"

    def __init__(self, adapter: SemanticModelAdapter, labels: dict[str, str],
                 conflicting_labels: dict[str, tuple[str, ...]], window_characters: int = 64) -> None:
        self._adapter = adapter
        self._labels = labels
        self._conflicting = conflicting_labels
        self._window = window_characters

    def analyze(self, candidate: EntityCandidate, segment: DocumentSegment) -> list[DetectorEvidence]:
        target = self._labels.get(candidate.entity_type.value)
        if not target:
            return []
        conflicts = self._conflicting.get(candidate.entity_type.value, ())
        start = max(0, candidate.span.start - self._window)
        end = min(len(segment.original_text), candidate.span.end + self._window)
        predictions = self._adapter.predict(segment.original_text[start:end], [target, *conflicts])
        candidate_start, candidate_end = candidate.span.start - start, candidate.span.end - start
        overlaps = [item for item in predictions if item.start < candidate_end and candidate_start < item.end]
        best_target = max((item for item in overlaps if item.label == target), key=lambda item: item.raw_score, default=None)
        best_conflict = max((item for item in overlaps if item.label in conflicts), key=lambda item: item.raw_score, default=None)
        evidence: list[DetectorEvidence] = []
        for item, status in ((best_target, "semantic_context_positive"), (best_conflict, "semantic_context_conflicting")):
            if item:
                evidence.append(DetectorEvidence(
                    detector_id=self.detector_id,
                    detector_version=self._adapter.model_id,
                    signal=EvidenceSignal.SEMANTIC,
                    status=status,
                    score=item.raw_score,
                    attributes={"label": item.label, "context_start": start, "context_end": end,
                                "prediction_start": start + item.start, "prediction_end": start + item.end},
                ))
        return evidence
