import json
from pathlib import Path

from evaluation.benchmark_ner import benchmark
from evaluation.benchmark_semantic import benchmark as semantic_benchmark


def test_ner_benchmark_keeps_baseline_and_unavailable_models_in_one_report(tmp_path):
    text = "Почта: ivan.petrov@example.ru"
    start = text.index("ivan.petrov@example.ru")
    document = {
        "document_id": "golden-ner-1",
        "segments": [{"segment_id": "body", "text": text}],
        "entities": [{"segment_id": "body", "start": start, "end": start + len("ivan.petrov@example.ru"), "type": "EMAIL"}],
    }
    (tmp_path / "document.json").write_text(json.dumps(document), encoding="utf-8")
    report = benchmark(tmp_path, Path("configs/ner_benchmark_v1.json"), tmp_path / "models")
    baseline = next(item for item in report["candidates"] if item["candidate_id"] == "deterministic_baseline")
    assert baseline["status"] == "completed"
    assert baseline["metrics"]["EMAIL"]["f1"] == 1.0
    assert any(item["status"] == "unavailable" for item in report["candidates"] if item["candidate_id"] != "deterministic_baseline")


def test_semantic_benchmark_splits_tier_zero_and_reports_missing_local_snapshots(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for index in range(12):
        text = "Сотрудник Иван Петров"
        start = text.index("Иван")
        (dataset / f"semantic-{index}.json").write_text(json.dumps({
            "document_id": f"semantic-{index}", "segments": [{"segment_id": "body", "text": text}],
            "entities": [{"segment_id": "body", "start": start, "end": len(text), "type": "PERSON"}],
        }, ensure_ascii=False), encoding="utf-8")
    config = tmp_path / "semantic.json"
    config.write_text(json.dumps({
        "research_question": "q", "validation_fraction": 0.2, "device": "cpu", "entity_mapping": ["PERSON"],
        "candidates": [{"id": "missing", "adapter": "gliner", "model_ref": "missing", "label_mapping": {"person": "PERSON"}, "threshold_grid": [0.5]}],
    }), encoding="utf-8")

    report = semantic_benchmark(dataset, config, tmp_path / "models")

    assert report["validation_document_count"] > 0
    assert report["evaluation_document_count"] > 0
    assert report["candidates"] == [{"candidate_id": "missing", "status": "unavailable", "reason": f"local model snapshot is missing: {tmp_path / 'models' / 'missing'}"}]
