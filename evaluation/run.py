"""Run exact-span deterministic-baseline evaluation against annotated JSON documents."""

import argparse
import json
from pathlib import Path

from app.core import load_config
from app.core.pipeline import DeterministicPipeline
from app.schemas.models import EntityType
from evaluation.datasets import load_document
from evaluation.metrics import Annotation, score_exact


def evaluate(dataset: Path, config_path: Path) -> dict[str, object]:
    config = load_config(config_path)
    pipeline = DeterministicPipeline(config)
    expected_all: set[Annotation] = set()
    predicted_all: set[Annotation] = set()
    for path in sorted(dataset.rglob("*.json")):
        segments, expected = load_document(path)
        predicted = {
            Annotation(
                document_id=segments[0].document_id,
                segment_id=entity.span.segment_id,
                start=entity.span.start,
                end=entity.span.end,
                entity_type=entity.entity_type,
            )
            for entity in pipeline.detect(segments).entities
            if entity.decision != "reject"
        }
        expected_all |= expected
        predicted_all |= predicted
    return {"config_version": config.version,
            **score_exact(expected_all, predicted_all, set(config.enabled_entities))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--errors-out", type=Path)
    args = parser.parse_args()
    report = evaluate(args.dataset, args.config)
    print("PII Detection Benchmark")
    print(f"{'Entity':<24} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>7} {'R':>7} {'F1':>7}")
    for entity, values in report["per_class"].items():
        print(f"{entity:<24} {values['tp']:>4} {values['fp']:>4} {values['fn']:>4} "
              f"{values['precision']:>7.3f} {values['recall']:>7.3f} {values['f1']:>7.3f}")
    if args.errors_out:
        args.errors_out.write_text(json.dumps(report["errors"], ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
