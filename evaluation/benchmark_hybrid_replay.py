"""Score deterministic rules plus the frozen PERSON predictions saved by pii-bench."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.core import load_config
from app.core.pipeline import HybridPipeline
from app.detection.semantic import SemanticPrediction
from app.schemas.models import DocumentSegment, EntityType
from evaluation.datasets.pii_bench import load_pii_bench_views
from evaluation.metrics import Annotation, score_exact


class PredictionReplay:
    """Feed recorded predictions to the production composition without model inference."""

    def __init__(self, model_id: str, threshold: float, predictions: list[dict[str, object]]) -> None:
        self.model_id = model_id
        self.threshold = threshold
        self._by_document: dict[str, list[dict[str, object]]] = {}
        for item in predictions:
            self._by_document.setdefault(str(item["document_id"]), []).append(item)
        self._segment: DocumentSegment | None = None

    def select(self, segment: DocumentSegment) -> None:
        self._segment = segment

    def predict(self, text: str, labels: list[str]) -> list[SemanticPrediction]:
        if self._segment is None or text != self._segment.original_text:
            raise ValueError("replay segment was not selected")
        if labels != ["ru_pii_person", "ru_pii_location"]:
            raise ValueError("replay inference labels differ from the frozen experiment")
        output = []
        for item in self._by_document.get(self._segment.document_id, []):
            start, end = int(item["start"]), int(item["end"])
            if (item["segment_id"] != self._segment.segment_id or not 0 <= start < end <= len(text)
                    or item["label"] != "ru_pii_person" or item["model_id"] != self.model_id):
                raise ValueError("recorded PERSON prediction has inconsistent provenance")
            value = text[start:end]
            if hashlib.sha256(value.encode()).hexdigest() != item["text_sha256"]:
                raise ValueError("recorded PERSON prediction text hash differs from the dataset")
            score = float(item["raw_score"])
            if score < self.threshold:
                raise ValueError("recorded PERSON prediction is below the frozen threshold")
            output.append(SemanticPrediction(value, start, end, "ru_pii_person", score, self.model_id))
        return output

    def model_size_bytes(self) -> int:
        return 0


def _summarize_error_patterns(errors: list[dict[str, object]], gold: set[Annotation],
                              predicted: set[Annotation]) -> dict[str, int]:
    distribution: Counter[str] = Counter()
    for error in errors:
        span = (error["document_id"], error["segment_id"], error["start"], error["end"])
        same_type = gold if error["kind"] == "FP" else predicted
        pattern = "unclassified"
        if any((item.document_id, item.segment_id) == span[:2] and item.entity_type.value == error["type"]
               and item.start < span[3] and span[2] < item.end for item in same_type):
            pattern = "same_type_overlap"
        elif error["kind"] == "FP" and any(
            (item.document_id, item.segment_id, item.start, item.end) == span
            and item.entity_type.value != error["type"] for item in gold
        ):
            pattern = "exact_other_gold"
        distribution[pattern] += 1
    return dict(distribution)


def benchmark(dataset: Path, deterministic_path: Path, semantic_path: Path, report_path: Path) -> dict[str, object]:
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    recorded = json.loads(report_path.read_text(encoding="utf-8"))
    for key in ("candidate_id", "model_ref", "revision", "snapshot_sha256", "threshold",
                "inference_labels", "label_mapping", "address_decision", "source_experiment_sha256"):
        if recorded[key] != semantic[key]:
            raise ValueError(f"recorded PERSON report differs from frozen config: {key}")
    if recorded["experiment"] != "PII-BENCH-SEMANTIC-PERSON-02":
        raise ValueError("replay requires the corrected second PERSON report")
    deterministic = load_config(deterministic_path)
    scored_types = deterministic.enabled_entities | {EntityType.PERSON}
    views = load_pii_bench_views(dataset)
    reports = {}
    for view, documents in views.items():
        source_hash = hashlib.sha256((dataset / f"{view}.jsonl").read_bytes()).hexdigest()
        source = recorded["views"][view]
        if source_hash != source["dataset_sha256"] or len(documents) != source["documents"]:
            raise ValueError(f"{view} dataset differs from the recorded PERSON run")
        replay = PredictionReplay(str(semantic["candidate_id"]), float(semantic["threshold"]),
                                  source["raw_predictions"])
        pipeline = HybridPipeline(deterministic, semantic, replay)
        expected: set[Annotation] = set()
        all_gold: set[Annotation] = set()
        predicted: set[Annotation] = set()
        for segments, annotations in documents:
            if len(segments) != 1:
                raise ValueError("pii-bench replay expects one segment per document")
            replay.select(segments[0])
            result = pipeline.detect(segments)
            all_gold.update(annotations)
            expected.update(item for item in annotations if item.entity_type in scored_types)
            predicted.update(Annotation(item.span.document_id, item.span.segment_id, item.span.start,
                                        item.span.end, item.entity_type)
                             for item in result.entities if item.decision != "reject" and item.entity_type in scored_types)
        exact = score_exact(expected, predicted, set(scored_types))
        per_class = exact["per_class"]
        tp = sum(int(item["tp"]) for item in per_class.values())
        fp = sum(int(item["fp"]) for item in per_class.values())
        fn = sum(int(item["fn"]) for item in per_class.values())
        distribution = _summarize_error_patterns(exact["errors"], all_gold, predicted)
        reports[view] = {
            "documents": len(documents), "dataset_sha256": source_hash,
            "per_class": per_class,
            "macro_f1": sum(float(item["f1"]) for item in per_class.values()) / len(per_class),
            "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
            "totals": {"tp": tp, "fp": fp, "fn": fn},
            "observed_error_patterns": distribution,
            "errors": exact["errors"],
        }
    return {
        "experiment": "PII-BENCH-HYBRID-REPLAY-01",
        "method": "recorded frozen PERSON predictions plus current deterministic pipeline; no model inference",
        "decision_policy": "accept and review predictions are scored; reject is excluded",
        "deterministic_config": deterministic.version,
        "semantic_config": semantic["version"],
        "person_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "scored_types": sorted(item.value for item in scored_types),
        "excluded_types": sorted(item.value for item in EntityType if item not in scored_types),
        "views": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/pii_bench"))
    parser.add_argument("--deterministic-config", type=Path, default=Path("configs/deterministic_v3.json"))
    parser.add_argument("--semantic-config", type=Path, default=Path("configs/semantic_person_frozen_v2.json"))
    parser.add_argument("--person-report", type=Path, default=Path("artifacts/pii_bench_semantic_person_v2.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.dataset, args.deterministic_config, args.semantic_config, args.person_report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for view, result in report["views"].items():
        print(f"{view}: micro exact F1={result['micro_f1']:.4f}; macro exact F1={result['macro_f1']:.4f}")


if __name__ == "__main__":
    main()
