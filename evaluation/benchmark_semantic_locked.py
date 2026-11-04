"""Evaluate the frozen PERSON model on both pii-bench views without calibration."""

import argparse
import hashlib
import json
from pathlib import Path

import psutil

from app.schemas.models import EntityType
from evaluation.benchmark_semantic import (
    _build,
    _hardware,
    _library_version,
    _predict,
    _reset_vram_peak,
    _vram_peak_bytes,
)
from evaluation.datasets.pii_bench import load_pii_bench_views
from evaluation.metrics import score_exact, score_overlap
from scripts.fetch_semantic_models import _snapshot_hash


def benchmark(dataset: Path, config_path: Path, lock_path: Path, models_dir: Path, device: str) -> dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["adapter"] != "gliner2" or config["label_mapping"] != {"ru_pii_person": "PERSON"}:
        raise ValueError("locked benchmark supports only the frozen GLiNER2 PERSON label")
    if config.get("inference_labels") != ["ru_pii_person", "ru_pii_location"]:
        raise ValueError("locked benchmark must reproduce the dev-set inference labels")
    if config["address_decision"] != "not_enabled":
        raise ValueError("ADDRESS must remain disabled in this locked benchmark")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    matching = [item for item in lock["candidates"] if item["id"] == config["candidate_id"]]
    if len(matching) != 1 or any(matching[0].get(key) != config[key] for key in ("revision", "snapshot_sha256")):
        raise ValueError("model lock does not match the frozen revision and snapshot hash")
    if matching[0]["status"] != "ready" or matching[0]["model_name"] != config["model_ref"]:
        raise ValueError("frozen model is not ready in models.lock")
    if device == "cuda" and not _hardware(device)["cuda_available"]:
        raise RuntimeError("CUDA was requested but is unavailable")
    snapshot = models_dir / config["candidate_id"]
    if not snapshot.is_dir() or _snapshot_hash(snapshot) != config["snapshot_sha256"]:
        raise ValueError("local model snapshot does not match the frozen hash")

    views = load_pii_bench_views(dataset)
    adapter = _build({"id": config["candidate_id"], "adapter": config["adapter"], "device": device},
                     models_dir, float(config["threshold"]))
    process = psutil.Process()
    reports = {}
    for view, documents in views.items():
        _reset_vram_peak()
        expected = {item for _, annotations in documents for item in annotations if item.entity_type == EntityType.PERSON}
        predicted, raw, latencies = _predict(adapter, documents, {"ru_pii_person": EntityType.PERSON},
                                            tuple(config["inference_labels"]))
        segments = {(segment.document_id, segment.segment_id): segment for group, _ in documents for segment in group}
        for item in raw:
            segment = segments[(item["document_id"], item["segment_id"])]
            start, end = int(item["start"]), int(item["end"])
            if not 0 <= start < end <= len(segment.original_text):
                raise ValueError("semantic prediction has invalid source offsets")
            if hashlib.sha256(segment.original_text[start:end].encode()).hexdigest() != item["text_sha256"]:
                raise ValueError("semantic prediction text does not match source offsets")
        exact = score_exact(expected, predicted, {EntityType.PERSON})
        reports[view] = {
            "documents": len(documents),
            "gold_person_spans": len(expected),
            "exact_metrics": exact["per_class"]["PERSON"],
            "overlap_metrics": score_overlap(expected, predicted, {EntityType.PERSON})["per_class"]["PERSON"],
            "latency_per_document_ms": sum(latencies) / len(latencies) * 1000,
            "ram_rss_after_inference_bytes": process.memory_info().rss,
            "vram_peak_bytes": _vram_peak_bytes(),
            "raw_predictions": raw,
            "errors": exact["errors"],
            "dataset_sha256": hashlib.sha256((dataset / f"{view}.jsonl").read_bytes()).hexdigest(),
        }
    return {
        "experiment": "PII-BENCH-SEMANTIC-PERSON-02",
        "candidate_id": config["candidate_id"],
        "model_ref": config["model_ref"],
        "revision": config["revision"],
        "snapshot_sha256": config["snapshot_sha256"],
        "threshold": config["threshold"],
        "inference_labels": config["inference_labels"],
        "label_mapping": config["label_mapping"],
        "address_decision": config["address_decision"],
        "source_experiment_sha256": config["source_experiment_sha256"],
        "model_size_bytes": adapter.model_size_bytes(),
        "hardware": _hardware(device),
        "library_versions": {name: _library_version(name) for name in ("gliner2", "torch", "transformers")},
        "views": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/semantic_person_frozen_v2.json"))
    parser.add_argument("--lock", type=Path, default=Path("models.lock"))
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.dataset, args.config, args.lock, args.models_dir, args.device)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for view, result in report["views"].items():
        print(f"{view}: PERSON exact F1={result['exact_metrics']['f1']:.4f}")


if __name__ == "__main__":
    main()
