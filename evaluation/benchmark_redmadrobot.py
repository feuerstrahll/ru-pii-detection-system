"""Run the frozen hybrid detector on the official redmadrobot test split."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.core.config import load_config
from app.core.pipeline import HybridPipeline
from app.schemas.models import DocumentSegment, EntityType
from evaluation.benchmark_semantic import _build, _hardware, _library_version
from evaluation.datasets import redmadrobot
from evaluation.datasets.redmadrobot import COMPARABLE, load_test
from evaluation.metrics import Annotation, score_exact, score_overlap
from scripts.fetch_semantic_models import _snapshot_hash


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def benchmark(config_path: Path, dataset: Path, models_dir: Path, device: str,
              *, adapter: object | None = None) -> dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["experiment"] != "REDMADROBOT-FROZEN-01" or set(config["scored_types"]) != {item.value for item in COMPARABLE}:
        raise ValueError("benchmark scope differs from the frozen dataset mapping")
    if dataset.name != config["test_file"] or _sha256(dataset) != config["test_sha256"]:
        raise ValueError("test CSV does not match the pinned official split")
    deterministic_path = Path(config["deterministic_config"])
    semantic_path = Path(config["semantic_config"])
    for path, expected in ((deterministic_path, config["deterministic_config_sha256"]),
                           (semantic_path, config["semantic_config_sha256"])):
        if _sha256(path) != expected:
            raise ValueError(f"frozen detector configuration changed: {path}")
    deterministic_config = load_config(deterministic_path)
    semantic_config = json.loads(semantic_path.read_text(encoding="utf-8"))
    snapshot = models_dir / semantic_config["candidate_id"]
    if not snapshot.is_dir() or _snapshot_hash(snapshot) != semantic_config["snapshot_sha256"]:
        raise ValueError("local model snapshot does not match the frozen hash")
    if device == "cuda" and not _hardware(device)["cuda_available"]:
        raise ValueError("CUDA was requested but is unavailable")

    rows, coverage = load_test(dataset, Path(config["dev_dir"]))
    if coverage["source_label_count"] != 21:
        raise ValueError("test BIO taxonomy differs from the frozen 21-label protocol")
    if adapter is None:
        adapter = _build({"id": semantic_config["candidate_id"], "adapter": semantic_config["adapter"],
                          "device": device}, models_dir, float(semantic_config["threshold"]))
    pipeline = HybridPipeline(deterministic_config, semantic_config, adapter)
    expected: set[Annotation] = set()
    predicted: set[Annotation] = set()
    unscored: Counter[str] = Counter()
    for document_id, text, gold in rows:
        segment = DocumentSegment(document_id=document_id, segment_id="body", original_text=text,
                                  source_locator={"dataset": config["dataset_repo"], "split": "test"})
        expected.update(gold)
        for entity in pipeline.detect([segment]).entities:
            if entity.decision not in ("accept", "review"):
                continue
            span = entity.span
            if (span.document_id != document_id or span.segment_id != "body"
                    or not 0 <= span.start < span.end <= len(text)
                    or text[span.start:span.end] != span.text):
                raise ValueError(f"detector returned invalid offsets in {document_id}")
            if entity.entity_type in COMPARABLE:
                predicted.add(Annotation(document_id, "body", span.start, span.end, entity.entity_type))
            else:
                unscored[entity.entity_type.value] += 1

    exact = score_exact(expected, predicted, set(COMPARABLE))
    per_class = exact["per_class"]
    totals = {key: sum(int(values[key]) for values in per_class.values()) for key in ("tp", "fp", "fn")}
    error_counts: Counter[str] = Counter()
    errors = sorted(exact["errors"], key=lambda item: (item["document_id"], item["start"], item["kind"], item["type"]))
    for error in errors:
        counterpart = expected if error["kind"] == "FP" else predicted
        boundary = error["type"] == "PERSON" and any(
            item.entity_type == EntityType.PERSON and item.document_id == error["document_id"]
            and item.start < error["end"] and error["start"] < item.end for item in counterpart
        )
        category = "entity_boundary" if boundary else "unclassified"
        error["suggested_category"] = category
        error_counts[f"{error['kind']}:{category}"] += 1
    coverage["unscored_predictions_by_type"] = dict(sorted(unscored.items()))
    coverage["unscored_predictions"] = sum(unscored.values())
    return {
        "experiment": config["experiment"], "status": "completed", "dataset": config["dataset_repo"],
        "dataset_revision": config["dataset_revision"], "split": "test", "test_sha256": _sha256(dataset),
        "config_sha256": _sha256(config_path),
        "adapter_code_sha256": _sha256(Path(__file__)),
        "dataset_adapter_code_sha256": _sha256(Path(redmadrobot.__file__)),
        "deterministic_config_sha256": _sha256(deterministic_path),
        "semantic_config_sha256": _sha256(semantic_path), "model_revision": semantic_config["revision"],
        "model_snapshot_sha256": semantic_config["snapshot_sha256"], "threshold": semantic_config["threshold"],
        "inference_labels": semantic_config["inference_labels"], "decisions_counted": ["accept", "review"],
        "matching": "exact document-local half-open character spans on original text",
        "hardware": _hardware(device), "library_versions": {name: _library_version(name) for name in ("gliner2", "torch", "transformers")},
        "coverage": coverage, "per_class": per_class, "micro": _rate(**totals),
        "macro_f1": sum(float(item["f1"]) for item in per_class.values()) / len(per_class),
        "person_overlap_diagnostic": score_overlap(expected, predicted, {EntityType.PERSON})["per_class"]["PERSON"],
        "error_summary": dict(sorted(error_counts.items())), "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/redmadrobot_frozen_v1.json"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.config, args.dataset, args.models_dir, args.device)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.out_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["type", "tp", "fp", "fn", "precision", "recall", "f1"])
        writer.writeheader()
        for kind, metrics in sorted(report["per_class"].items()):
            writer.writerow({"type": kind, **metrics})
        writer.writerow({"type": "MICRO", **report["micro"]})
    (args.out_dir / "coverage.json").write_text(json.dumps(report["coverage"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out_dir / "errors.json").write_text(json.dumps({"summary": report["error_summary"], "items": report["errors"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Comparable-scope micro exact F1: {report['micro']['f1']:.4f}; coverage: {report['coverage']['comparable_source_fraction']:.2%}")


if __name__ == "__main__":
    main()
