"""Evaluate frozen semantic PERSON on pinned NEREL v1.1 test documents."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.detection.semantic import SemanticModelAdapter
from app.schemas.models import EntityType
from evaluation.benchmark_semantic import _build, _hardware, _library_version
from evaluation.datasets import nerel
from evaluation.datasets.nerel import load_test
from evaluation.metrics import Annotation, score_exact
from scripts.fetch_semantic_models import _snapshot_hash


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def word_chunks(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return original-text windows with a fixed whitespace-word overlap."""
    words = list(re.finditer(r"\S+", text))
    if len(words) <= size:
        return [(0, len(text))] if text else []
    chunks: list[tuple[int, int]] = []
    step = size - overlap
    for first in range(0, len(words), step):
        last = min(first + size, len(words))
        chunks.append((words[first].start(), words[last - 1].end()))
        if last == len(words):
            break
    return chunks


def _augment(prediction_index: int, edges: list[list[int]], assignments: dict[int, int],
             visited: set[int]) -> bool:
    for expected_index in edges[prediction_index]:
        if expected_index in visited:
            continue
        visited.add(expected_index)
        if expected_index not in assignments or _augment(assignments[expected_index], edges, assignments, visited):
            assignments[expected_index] = prediction_index
            return True
    return False


def _overlap_score(gold: set[Annotation], predicted: set[Annotation]) -> dict[str, float | int]:
    """Maximum one-to-one overlap matching, deterministic for nested spans."""
    matched = 0
    for doc_id in sorted({item.document_id for item in gold | predicted}):
        expected = sorted((item for item in gold if item.document_id == doc_id),
                          key=lambda item: (item.start, item.end))
        found = sorted((item for item in predicted if item.document_id == doc_id),
                       key=lambda item: (item.start, item.end))
        edges = [[index for index, item in enumerate(expected)
                  if item.start < prediction.end and prediction.start < item.end]
                 for prediction in found]
        assignments: dict[int, int] = {}

        matched += sum(_augment(index, edges, assignments, set()) for index in range(len(found)))
    fp, fn = len(predicted) - matched, len(gold) - matched
    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(gold) if gold else 0.0
    return {"tp": matched, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def benchmark(config_path: Path, dataset_root: Path, models_dir: Path, device: str,
              *, adapter: SemanticModelAdapter | None = None) -> dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config["experiment"] != "NEREL-PERSON-01" or config["dataset_version"] != "1.1"
            or config["split"] != "test" or config["entity_type"] != "PERSON"
            or (config["chunk_words"], config["chunk_overlap_words"]) != (384, 64)):
        raise ValueError("NEREL evaluation protocol differs from its frozen configuration")
    semantic_path = Path(config["semantic_config"])
    if _sha256(semantic_path) != config["semantic_config_sha256"]:
        raise ValueError("frozen semantic detector configuration changed")
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    if (semantic["adapter"] != "gliner2" or semantic["label_mapping"] != {"ru_pii_person": "PERSON"}
            or semantic["inference_labels"] != ["ru_pii_person", "ru_pii_location"]
            or semantic["address_decision"] != "not_enabled"):
        raise ValueError("semantic PERSON contract differs from the frozen model")
    snapshot = models_dir / semantic["candidate_id"]
    if not snapshot.is_dir() or _snapshot_hash(snapshot) != semantic["snapshot_sha256"]:
        raise ValueError("local model snapshot does not match the frozen hash")
    if device == "cuda" and not _hardware(device)["cuda_available"]:
        raise ValueError("CUDA was requested but is unavailable")
    documents, coverage = load_test(dataset_root, Path(config["dev_dir"]),
                                    expected_manifest=config["manifest_sha256"])
    if adapter is None:
        adapter = _build({"id": semantic["candidate_id"], "adapter": "gliner2", "device": device},
                         models_dir, float(semantic["threshold"]))
    if adapter.model_id != semantic["candidate_id"] or adapter.threshold != semantic["threshold"]:
        raise ValueError("semantic adapter parameters differ from the frozen configuration")

    gold: set[Annotation] = set()
    predictions: dict[Annotation, dict[str, object]] = {}
    chunks_processed = 0
    for segment, annotations in documents:
        gold.update(annotations)
        text = segment.original_text
        for chunk_start, chunk_end in word_chunks(text, config["chunk_words"], config["chunk_overlap_words"]):
            chunks_processed += 1
            chunk = text[chunk_start:chunk_end]
            for item in adapter.predict(chunk, semantic["inference_labels"]):
                if (item.model_id != adapter.model_id or item.raw_score < adapter.threshold
                        or item.label not in semantic["inference_labels"]
                        or not 0 <= item.start < item.end <= len(chunk)
                        or chunk[item.start:item.end] != item.text):
                    raise ValueError(f"semantic prediction differs from source text: {segment.document_id}")
                if item.label != "ru_pii_person":
                    continue
                start, end = chunk_start + item.start, chunk_start + item.end
                annotation = Annotation(segment.document_id, "body", start, end, EntityType.PERSON)
                previous = predictions.get(annotation)
                if previous is None or item.raw_score > previous["raw_score"]:
                    predictions[annotation] = {"document_id": segment.document_id, "segment_id": "body",
                                               "start": start, "end": end, "type": "PERSON", "label": item.label,
                                               "model_id": item.model_id, "raw_score": item.raw_score,
                                               "text_sha256": hashlib.sha256(item.text.encode("utf-8")).hexdigest()}
    predicted = set(predictions)
    scored = score_exact(gold, predicted, {EntityType.PERSON})
    errors = sorted(scored["errors"], key=lambda item: (item["document_id"], item["start"], item["kind"]))
    error_summary: Counter[str] = Counter()
    for error in errors:
        counterpart = gold - predicted if error["kind"] == "FP" else predicted - gold
        overlaps = [item for item in counterpart if item.document_id == error["document_id"]
                    and item.start < error["end"] and error["start"] < item.end]
        category = "entity_boundary" if overlaps else "unclassified"
        error["suggested_category"] = category
        if overlaps:
            best = max(overlaps, key=lambda item: (min(item.end, error["end"]) - max(item.start, error["start"]),
                                                   -item.start, -item.end))
            gold_span = (best.start, best.end) if error["kind"] == "FP" else (error["start"], error["end"])
            prediction_span = (error["start"], error["end"]) if error["kind"] == "FP" else (best.start, best.end)
            if prediction_span[0] <= gold_span[0] and prediction_span[1] >= gold_span[1]:
                relation = "prediction_wider"
            elif prediction_span[0] >= gold_span[0] and prediction_span[1] <= gold_span[1]:
                relation = "prediction_narrower"
            else:
                relation = "prediction_shifted"
            error["boundary_relation"] = relation
        error_summary[f"{error['kind']}:{category}"] += 1
    return {"experiment": config["experiment"], "status": "completed",
            "dataset": config["dataset_repo"], "dataset_commit": config["dataset_commit"],
            "dataset_version": "1.1", "split": "test", "manifest_sha256": config["manifest_sha256"],
            "config_sha256": _sha256(config_path), "semantic_config_sha256": _sha256(semantic_path),
            "adapter_code_sha256": _sha256(Path(__file__)),
            "dataset_adapter_code_sha256": _sha256(Path(nerel.__file__)),
            "model_revision": semantic["revision"], "model_snapshot_sha256": semantic["snapshot_sha256"],
            "threshold": semantic["threshold"], "inference_labels": semantic["inference_labels"],
            "chunk_words": config["chunk_words"], "chunk_overlap_words": config["chunk_overlap_words"],
            "chunks_processed": chunks_processed,
            "matching": "exact document-local half-open spans; maximum one-to-one overlap diagnostic",
            "hardware": _hardware(device),
            "library_versions": {name: _library_version(name) for name in ("gliner2", "torch", "transformers")},
            "coverage": coverage, "exact": scored["per_class"]["PERSON"],
            "micro_exact_f1": scored["per_class"]["PERSON"]["f1"],
            "macro_exact_f1": scored["per_class"]["PERSON"]["f1"],
            "overlap_diagnostic": _overlap_score(gold, predicted),
            "error_summary": dict(sorted(error_summary.items())), "errors": errors,
            "predictions": [predictions[item] for item in sorted(predicted, key=lambda item: (item.document_id, item.start, item.end))]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/nerel_person_frozen_v1.json"))
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark(args.config, args.dataset_root, args.models_dir, args.device)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.out_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["type", "tp", "fp", "fn", "precision", "recall", "f1"])
        writer.writeheader()
        writer.writerow({"type": "PERSON", **result["exact"]})
    (args.out_dir / "coverage.json").write_text(json.dumps(result["coverage"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out_dir / "errors.json").write_text(json.dumps({"summary": result["error_summary"], "items": result["errors"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"NEREL PERSON exact F1: {result['exact']['f1']:.4f}; overlap diagnostic F1: {result['overlap_diagnostic']['f1']:.4f}")


if __name__ == "__main__":
    main()
