import json
from pathlib import Path

import pytest

from evaluation.run import evaluate
from evaluation.datasets.pii_bench import load_pii_bench_views


def test_evaluation_reports_per_class_exact_span_metrics(tmp_path):
    text = "Почта: ivan.petrov@example.ru"
    start = text.index("ivan.petrov@example.ru")
    document = {
        "document_id": "golden-1",
        "segments": [{"segment_id": "body", "text": text}],
        "entities": [{"segment_id": "body", "start": start, "end": start + len("ivan.petrov@example.ru"), "type": "EMAIL"}],
    }
    (tmp_path / "document.json").write_text(json.dumps(document), encoding="utf-8")
    report = evaluate(tmp_path, Path("configs/deterministic_v1.json"))
    assert report["per_class"]["EMAIL"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_pii_bench_loader_keeps_both_views_and_maps_taxonomy_v2_labels(tmp_path):
    entity = {"id": "entity-1", "domain": "INN", "text": "ИНН 7707083893", "entities": [
        {"type": "INN", "start": 4, "end": 14, "text": "7707083893"},
    ]}
    domain = {"id": "domain-1", "domain": "S-HR", "text": "Иван Петров", "entities": [
        {"type": "NAME", "start": 0, "end": 11, "text": "Иван Петров"},
    ]}
    (tmp_path / "entity.jsonl").write_text(json.dumps(entity, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "domain.jsonl").write_text(json.dumps(domain, ensure_ascii=False) + "\n", encoding="utf-8")

    views = load_pii_bench_views(tmp_path)

    assert {item.entity_type.value for item in views["entity"][0][1]} == {"TAX_ID"}
    assert {item.entity_type.value for item in views["domain"][0][1]} == {"PERSON"}


def test_pii_bench_loader_fails_for_an_unmapped_label(tmp_path):
    unknown = {"id": "bad", "domain": "UNKNOWN", "text": "x", "entities": [
        {"type": "UNKNOWN", "start": 0, "end": 1, "text": "x"},
    ]}
    for view in ("entity", "domain"):
        (tmp_path / f"{view}.jsonl").write_text(json.dumps(unknown) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmapped pii-bench label: UNKNOWN"):
        load_pii_bench_views(tmp_path)
