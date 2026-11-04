"""Benchmark local NER candidates and the deterministic baseline on one golden dataset."""

import argparse
import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import psutil

from app.core import load_config
from app.core.pipeline import DeterministicPipeline
from app.detection.ner.adapters import GLiNERAdapter, ModelUnavailableError, NERAdapter, SpacyAdapter, TransformersAdapter
from app.schemas.models import DocumentSegment, EntityCandidate, EntityType
from evaluation.datasets import load_document
from evaluation.metrics import Annotation, score_exact


class DeterministicBenchmarkAdapter:
    candidate_id = "deterministic_baseline"
    model_ref = "configured_rules"

    def __init__(self, config_path: Path) -> None:
        self._pipeline = DeterministicPipeline(load_config(config_path))

    def predict(self, segments: list[DocumentSegment]) -> list[EntityCandidate]:
        return [
            EntityCandidate(entity_type=entity.entity_type, span=entity.span, normalized_value=entity.normalized_value,
                            evidence=entity.evidence)
            for entity in self._pipeline.detect(segments).entities if entity.decision != "reject"
        ]

    def model_size_bytes(self) -> int:
        return 0


def _build_adapter(spec: dict[str, Any], models_dir: Path) -> NERAdapter | DeterministicBenchmarkAdapter:
    adapter = spec["adapter"]
    candidate_id = spec["id"]
    if adapter == "deterministic":
        return DeterministicBenchmarkAdapter(Path(spec["config"]))
    if adapter == "spacy":
        return SpacyAdapter(candidate_id, spec["model_ref"], _map_labels(spec["label_mapping"]))
    model_path = models_dir / candidate_id
    if adapter == "transformers":
        return TransformersAdapter(candidate_id, model_path, spec["model_ref"], _map_labels(spec["label_mapping"]))
    if adapter == "gliner":
        labels = {EntityType(kind): label for kind, label in spec["labels"].items()}
        return GLiNERAdapter(candidate_id, model_path, spec["model_ref"], labels, float(spec["threshold"]))
    raise ValueError(f"unsupported adapter {adapter!r}")


def _map_labels(mapping: dict[str, str]) -> dict[str, EntityType]:
    return {label: EntityType(entity_type) for label, entity_type in mapping.items()}


def _hardware() -> dict[str, object]:
    result: dict[str, object] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "ram_total_bytes": psutil.virtual_memory().total,
        "execution_device": "cpu",
    }
    try:
        import torch
        result["torch_version"] = torch.__version__
        result["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            result["gpu_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        result["torch_version"] = None
        result["cuda_available"] = False
    return result


def benchmark(dataset: Path, benchmark_config: Path, models_dir: Path) -> dict[str, object]:
    raw_config = json.loads(benchmark_config.read_text(encoding="utf-8"))
    entity_types = {EntityType(item) for item in raw_config["entity_mapping"]}
    documents = [load_document(path) for path in sorted(dataset.rglob("*.json"))]
    expected = {annotation for _, annotations in documents for annotation in annotations if annotation.entity_type in entity_types}
    process = psutil.Process()
    reports: list[dict[str, object]] = []
    for spec in raw_config["candidates"]:
        rss_before = process.memory_info().rss
        load_started = time.perf_counter()
        try:
            _reset_vram_peak()
            adapter = _build_adapter(spec, models_dir)
        except (ModelUnavailableError, ValueError) as error:
            reports.append({"candidate_id": spec["id"], "status": "unavailable", "reason": str(error)})
            continue
        load_seconds = time.perf_counter() - load_started
        rss_after_load = process.memory_info().rss
        predicted: set[Annotation] = set()
        document_latencies: list[float] = []
        failure_cases: list[dict[str, str]] = []
        try:
            for segments, _ in documents:
                started = time.perf_counter()
                candidates = adapter.predict(segments)
                document_latencies.append(time.perf_counter() - started)
                predicted |= {
                    Annotation(entity.span.document_id, entity.span.segment_id, entity.span.start, entity.span.end,
                               entity.entity_type)
                    for entity in candidates if entity.entity_type in entity_types
                }
        except Exception as error:  # adapter/model faults are benchmark outcomes, not hidden retries
            failure_cases.append({"stage": "inference", "error": f"{type(error).__name__}: {error}"})
        metrics = score_exact(expected, predicted, entity_types)
        reports.append({
            "candidate_id": spec["id"],
            "status": "completed" if not failure_cases else "failed",
            "model_ref": adapter.model_ref,
            "model_size_bytes": adapter.model_size_bytes(),
            "load_seconds": load_seconds,
            "latency_per_document_ms": (sum(document_latencies) / len(document_latencies) * 1000) if document_latencies else None,
            "documents_completed": len(document_latencies),
            "ram_rss_before_bytes": rss_before,
            "ram_rss_after_load_bytes": rss_after_load,
            "ram_rss_after_inference_bytes": process.memory_info().rss,
            "vram_peak_bytes": _vram_peak_bytes(),
            "metrics": metrics["per_class"],
            "failure_cases": failure_cases,
            "errors": metrics["errors"],
        })
    return {
        "benchmark_config_version": raw_config["version"],
        "dataset_document_count": len(documents),
        "preprocessing": "immutable original DocumentSegment text; no candidate-specific normalization or truncation",
        "matching": "exact segment-local half-open span and canonical EntityType",
        "hardware": _hardware(),
        "candidates": reports,
    }


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/ner_benchmark_v1.json"))
    parser.add_argument("--models-dir", type=Path, default=Path("models/ner"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.dataset, args.config, args.models_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for candidate in report["candidates"]:
        print(f"{candidate['candidate_id']}: {candidate['status']}")


if __name__ == "__main__":
    main()
