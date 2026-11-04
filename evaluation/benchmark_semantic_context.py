"""Compare isolated deterministic/keyword/semantic context policy arms on Tier 0 cases."""

import argparse
import json
from pathlib import Path
from typing import Any

from app.core import load_config
from app.detection.aggregation import aggregate_candidates
from app.detection.conflicts import IdentifierConflictEvidenceProducer
from app.detection.deterministic import ContextEvidenceProducer, RegexCandidateGenerator, StructuredValidator
from app.detection.semantic import GLiNER2SemanticAdapter, GLiNERSemanticAdapter, SemanticContextEvidenceProducer
from app.schemas.models import DocumentSegment, EntityCandidate, EntityType
from evaluation.metrics import Annotation, score_exact


ARMS = ("deterministic_only", "keyword_context", "semantic_context", "keyword_and_semantic_context")


def _semantic_adapter(spec: dict[str, Any], models_dir: Path, threshold: float):
    path = models_dir / spec["id"]
    if spec["adapter"] == "gliner":
        return GLiNERSemanticAdapter(spec["id"], path, threshold, spec.get("device", "cpu"))
    if spec["adapter"] == "gliner2":
        return GLiNER2SemanticAdapter(spec["id"], path, threshold, spec.get("device", "cpu"))
    raise ValueError(f"unsupported semantic adapter: {spec['adapter']}")


def _candidates(segment: DocumentSegment, config, semantic: SemanticContextEvidenceProducer | None,
                include_keyword: bool, include_semantic: bool) -> list[EntityCandidate]:
    generator, validator = RegexCandidateGenerator(config), StructuredValidator(config)
    keyword = ContextEvidenceProducer(config)
    enriched = []
    for candidate in generator.detect(segment):
        evidence = [*candidate.evidence, *validator.validate(candidate)]
        if include_keyword:
            evidence.extend(keyword.analyze(candidate, segment))
        if include_semantic and semantic:
            evidence.extend(semantic.analyze(candidate, segment))
        enriched.append(candidate.model_copy(update={"evidence": evidence}))
    return IdentifierConflictEvidenceProducer(config).analyze(enriched)


def _accepted(group: list[EntityCandidate], config) -> bool:
    entity_type = group[0].entity_type
    statuses = {evidence.status for candidate in group for evidence in candidate.evidence}
    if "invalid" in statuses:
        return False
    if any(config.conflict_decisions.get(entity_type, {}).get(status) == "reject" for status in statuses):
        return False
    if entity_type in config.require_context_for:
        return "strong_context" in statuses or "semantic_context_positive" in statuses
    return True


def benchmark(cases: Path, deterministic_config: Path, semantic_spec: dict[str, Any], models_dir: Path,
              threshold: float, device: str | None = None) -> dict[str, object]:
    config = load_config(deterministic_config)
    raw_cases = json.loads(cases.read_text(encoding="utf-8"))
    documents: list[tuple[DocumentSegment, set[Annotation]]] = []
    for item in raw_cases["documents"]:
        segment = DocumentSegment(document_id=item["document_id"], segment_id="body", original_text=item["text"])
        expected = {Annotation(segment.document_id, "body", entity["start"], entity["end"], EntityType(entity["type"]))
                    for entity in item["entities"]}
        documents.append((segment, expected))
    entity_types = {EntityType(item) for item in raw_cases["entity_mapping"]}
    spec = {**semantic_spec, "device": device or semantic_spec.get("device", "cpu")}
    semantic = SemanticContextEvidenceProducer(
        _semantic_adapter(spec, models_dir, threshold), spec["labels"],
        {key: tuple(value) for key, value in spec["conflicting_labels"].items()},
        int(spec["context_window_characters"]),
    )
    reports = []
    for arm in ARMS:
        keyword = arm in {"keyword_context", "keyword_and_semantic_context"}
        semantic_enabled = arm in {"semantic_context", "keyword_and_semantic_context"}
        expected = set()
        predicted = set()
        for segment, annotations in documents:
            expected |= annotations
            for group in aggregate_candidates(_candidates(segment, config, semantic, keyword, semantic_enabled)):
                if _accepted(group, config):
                    candidate = group[0]
                    predicted.add(Annotation(segment.document_id, candidate.span.segment_id, candidate.span.start,
                                             candidate.span.end, candidate.entity_type))
        metrics = score_exact(expected, predicted, entity_types)
        reports.append({"arm": arm, "metrics": metrics["per_class"], "errors": metrics["errors"]})
    return {"experiment": "SEM-CONTEXT-01", "research_question": raw_cases["research_question"],
            "semantic_model": semantic_spec["model_ref"], "semantic_threshold": threshold,
            "decision_protocol": "experimental accept policy; semantic evidence never mutates candidates or production policy",
            "arms": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evaluation/semantic_context_cases.json"))
    parser.add_argument("--config", type=Path, default=Path("configs/semantic_context_benchmark_v1.json"))
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--threshold", type=float, required=True, help="Selected on SEM-NER-01 validation split.")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    report = benchmark(args.cases, Path(raw["deterministic_config"]), raw["semantic_model"], args.models_dir,
                       args.threshold, args.device)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for arm in report["arms"]:
        print(arm["arm"])


if __name__ == "__main__":
    main()
