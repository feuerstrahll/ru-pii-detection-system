"""Run reproducible pretrained semantic-model comparisons on Tier 0 only."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import psutil

from app.detection.semantic import (
    GLiNER2SemanticAdapter,
    GLiNERSemanticAdapter,
    ModelUnavailableError,
    SemanticModelAdapter,
)
from app.schemas.models import EntityType
from evaluation.datasets import load_document
from evaluation.metrics import Annotation, score_exact, score_overlap


def _documents(dataset: Path, validation_fraction: float) -> tuple[list, list]:
    validation, test = [], []
    for path in sorted(dataset.rglob("*.json")):
        document = load_document(path)
        bucket = int(hashlib.sha256(document[0][0].document_id.encode()).hexdigest(), 16) % 10_000
        (validation if bucket < validation_fraction * 10_000 else test).append(document)
    if not validation or not test:
        raise ValueError("Tier 0 split must contain both validation and evaluation documents")
    return validation, test


def _build(spec: dict[str, Any], models_dir: Path, threshold: float) -> SemanticModelAdapter:
    path = models_dir / spec["id"]
    if spec["adapter"] == "gliner":
        return GLiNERSemanticAdapter(spec["id"], path, threshold, spec.get("device", "cpu"))
    if spec["adapter"] == "gliner2":
        return GLiNER2SemanticAdapter(spec["id"], path, threshold, spec.get("device", "cpu"))
    raise ValueError(f"unsupported semantic adapter: {spec['adapter']}")


def _predict(adapter: SemanticModelAdapter, documents: list, label_mapping: dict[str, EntityType],
             inference_labels: tuple[str, ...] | None = None) -> tuple[set[Annotation], list[dict[str, object]], list[float]]:
    predicted: set[Annotation] = set()
    raw: list[dict[str, object]] = []
    latencies: list[float] = []
    labels = list(inference_labels if inference_labels is not None else label_mapping)
    for segments, _ in documents:
        started = time.perf_counter()
        for segment in segments:
            for item in adapter.predict(segment.original_text, labels):
                entity_type = label_mapping.get(item.label)
                if entity_type is None:
                    continue
                predicted.add(Annotation(segment.document_id, segment.segment_id, item.start, item.end, entity_type))
                raw.append({"document_id": segment.document_id, "segment_id": segment.segment_id,
                            "start": item.start, "end": item.end, "label": item.label,
                            "raw_score": item.raw_score, "model_id": item.model_id,
                            "text_sha256": hashlib.sha256(item.text.encode()).hexdigest()})
        latencies.append(time.perf_counter() - started)
    return predicted, raw, latencies


def _macro_f1(metrics: dict[str, dict[str, float | int]]) -> float:
    return sum(float(item["f1"]) for item in metrics.values()) / len(metrics) if metrics else 0.0


def _hardware(device: str) -> dict[str, object]:
    result: dict[str, object] = {"platform": platform.platform(), "python": platform.python_version(),
                                 "cpu_count": os.cpu_count(), "execution_device": device}
    try:
        import torch
        result.update({"torch_version": torch.__version__, "cuda_available": torch.cuda.is_available()})
        if torch.cuda.is_available():
            result["gpu_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        result.update({"torch_version": None, "cuda_available": False})
    return result


def _library_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _vram_peak_bytes() -> int | None:
    try:
        import torch
        return torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
    except ImportError:
        return None


def _reset_vram_peak() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass


def benchmark(dataset: Path, benchmark_config: Path, models_dir: Path, device: str | None = None) -> dict[str, object]:
    config = json.loads(benchmark_config.read_text(encoding="utf-8"))
    if device:
        config["device"] = device
        for spec in config["candidates"]:
            spec["device"] = device
    validation, test = _documents(dataset, float(config["validation_fraction"]))
    entity_types = {EntityType(value) for value in config["entity_mapping"]}
    expected = {item for _, annotations in test for item in annotations if item.entity_type in entity_types}
    process = psutil.Process()
    reports: list[dict[str, object]] = []
    for spec in config["candidates"]:
        labels = {label: EntityType(entity_type) for label, entity_type in spec["label_mapping"].items()}
        if set(labels.values()) != entity_types:
            raise ValueError(f"{spec['id']} must map every benchmark entity type")
        calibration: list[dict[str, object]] = []
        best: tuple[float, float] | None = None
        try:
            for threshold in spec["threshold_grid"]:
                adapter = _build(spec, models_dir, float(threshold))
                predicted, _, _ = _predict(adapter, validation, labels)
                expected_validation = {item for _, annotations in validation for item in annotations if item.entity_type in entity_types}
                metrics = score_exact(expected_validation, predicted, entity_types)["per_class"]
                value = _macro_f1(metrics)
                calibration.append({"threshold": threshold, "macro_exact_f1": value, "metrics": metrics})
                if best is None or value > best[1] or (value == best[1] and threshold > best[0]):
                    best = (float(threshold), value)
            assert best is not None
            rss_before = process.memory_info().rss
            _reset_vram_peak()
            adapter = _build(spec, models_dir, best[0])
            rss_after_load = process.memory_info().rss
            predicted, raw, latencies = _predict(adapter, test, labels)
        except (ModelUnavailableError, ValueError) as error:
            reports.append({"candidate_id": spec["id"], "status": "unavailable", "reason": str(error)})
            continue
        exact = score_exact(expected, predicted, entity_types)
        reports.append({"candidate_id": spec["id"], "status": "completed", "model_ref": spec["model_ref"],
                        "selected_threshold": best[0], "calibration": calibration,
                        "model_size_bytes": adapter.model_size_bytes(), "documents_completed": len(test),
                        "latency_per_document_ms": sum(latencies) / len(latencies) * 1000,
                        "ram_rss_before_bytes": rss_before, "ram_rss_after_load_bytes": rss_after_load,
                        "ram_rss_after_inference_bytes": process.memory_info().rss,
                        "vram_peak_bytes": _vram_peak_bytes(),
                        "exact_metrics": exact["per_class"],
                        "overlap_metrics": score_overlap(expected, predicted, entity_types)["per_class"],
                        "raw_predictions": raw, "errors": exact["errors"]})
    return {"experiment": "SEM-NER-01", "research_question": config["research_question"],
            "dataset": str(dataset), "validation_document_count": len(validation), "evaluation_document_count": len(test),
            "preprocessing": "immutable original DocumentSegment text; no candidate-specific normalization or truncation",
            "matching": "exact and same-label overlapping segment-local half-open spans",
            "library_versions": {"gliner": _library_version("gliner"), "gliner2": _library_version("gliner2"),
                                 "torch": _library_version("torch"), "transformers": _library_version("transformers")},
            "hardware": _hardware(config.get("device", "cpu")), "candidates": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/ner_semantic_benchmark_v1.json"))
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.dataset, args.config, args.models_dir, args.device)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for candidate in report["candidates"]:
        print(f"{candidate['candidate_id']}: {candidate['status']}")


if __name__ == "__main__":
    main()
