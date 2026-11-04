import json

import pytest

from app.detection.semantic import SemanticPrediction
from evaluation import benchmark_semantic_locked as locked
from scripts.fetch_semantic_models import _snapshot_hash


def test_locked_person_benchmark_scores_both_views_and_rejects_model_drift(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    snapshot = models_dir / "ru_pii_ner_gliner2_5"
    snapshot.mkdir(parents=True)
    (snapshot / "weights.bin").write_bytes(b"fixed weights")
    cache = snapshot / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    metadata = cache / "download.metadata"
    metadata.write_text("download metadata", encoding="utf-8")
    config = {
        "candidate_id": "ru_pii_ner_gliner2_5", "adapter": "gliner2", "model_ref": "example/model",
        "revision": "fixed-revision", "snapshot_sha256": _snapshot_hash(snapshot), "threshold": 0.7,
        "inference_labels": ["ru_pii_person", "ru_pii_location"],
        "label_mapping": {"ru_pii_person": "PERSON"}, "address_decision": "not_enabled",
        "source_experiment_sha256": "source-hash",
    }
    metadata.write_text("changed download metadata", encoding="utf-8")
    assert _snapshot_hash(snapshot) == config["snapshot_sha256"]
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    lock_path = tmp_path / "models.lock"
    lock = {"candidates": [{"id": config["candidate_id"], "model_name": config["model_ref"],
                            "revision": config["revision"], "snapshot_sha256": config["snapshot_sha256"],
                            "status": "ready"}]}
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    dataset = tmp_path / "pii_bench"
    dataset.mkdir()
    for view, entity_type in (("entity", "NAME"), ("domain", "EMAIL")):
        row = {"id": view, "domain": view, "text": "Иван", "entities": [
            {"type": entity_type, "start": 0, "end": 4},
        ]}
        (dataset / f"{view}.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    class FakeAdapter:
        def predict(self, text, labels):
            assert labels == ["ru_pii_person", "ru_pii_location"]
            return [SemanticPrediction(text, 0, len(text), label, 0.9, config["candidate_id"])
                    for label in labels]

        def model_size_bytes(self):
            return 10

    def build(spec, models_dir, threshold):
        assert threshold == 0.7
        assert spec["id"] == config["candidate_id"]
        return FakeAdapter()

    monkeypatch.setattr(locked, "_build", build)
    monkeypatch.setattr(locked, "_hardware", lambda device: {"cuda_available": False, "execution_device": device})
    monkeypatch.setattr(locked, "_reset_vram_peak", lambda: None)
    monkeypatch.setattr(locked, "_vram_peak_bytes", lambda: 0)
    report = locked.benchmark(dataset, config_path, lock_path, models_dir, "cpu")
    assert report["views"]["entity"]["exact_metrics"]["tp"] == 1
    assert report["views"]["domain"]["exact_metrics"]["fp"] == 1
    assert report["threshold"] == 0.7
    assert report["inference_labels"] == config["inference_labels"]
    assert len(report["views"]["entity"]["raw_predictions"]) == 1

    config["inference_labels"] = ["ru_pii_person"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="inference labels"):
        locked.benchmark(dataset, config_path, lock_path, models_dir, "cpu")
    config["inference_labels"] = ["ru_pii_person", "ru_pii_location"]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    lock["candidates"][0]["revision"] = "changed"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        locked.benchmark(dataset, config_path, lock_path, models_dir, "cpu")

    lock["candidates"][0]["revision"] = config["revision"]
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    (snapshot / "weights.bin").write_bytes(b"changed weights")
    with pytest.raises(ValueError, match="local model snapshot"):
        locked.benchmark(dataset, config_path, lock_path, models_dir, "cpu")
