from pathlib import Path

from app.detection.semantic import SemanticPrediction
from app.schemas.models import DocumentSegment, EntityType
from evaluation.benchmark_nerel import _overlap_score, benchmark, word_chunks
from evaluation.metrics import Annotation


def test_word_windows_preserve_source_offsets() -> None:
    text = "  " + " ".join(f"w{index}" for index in range(10)) + "  "
    chunks = word_chunks(text, 4, 1)
    assert [text[start:end].split() for start, end in chunks] == [
        ["w0", "w1", "w2", "w3"], ["w3", "w4", "w5", "w6"],
        ["w6", "w7", "w8", "w9"],
    ]
    assert word_chunks("  Иван  ", 384, 64) == [(0, 8)]


def test_overlap_matching_is_one_to_one_for_nested_spans() -> None:
    gold = {Annotation("doc", "body", 0, 4, EntityType.PERSON),
            Annotation("doc", "body", 0, 9, EntityType.PERSON)}
    predicted = {Annotation("doc", "body", 0, 5, EntityType.PERSON)}
    assert _overlap_score(gold, predicted)["tp"] == 1
    assert _overlap_score(gold, predicted)["fn"] == 1


def test_benchmark_reuses_frozen_adapter_and_reports_boundary(monkeypatch, tmp_path: Path) -> None:
    import evaluation.benchmark_nerel as module

    class FakeAdapter:
        model_id = "ru_pii_ner_gliner2_5"
        threshold = 0.7

        def predict(self, text: str, labels: list[str]) -> list[SemanticPrediction]:
            assert labels == ["ru_pii_person", "ru_pii_location"]
            start = text.index("Иван")
            return [SemanticPrediction(text[start:start + 9], start, start + 9,
                                       "ru_pii_person", 0.9, self.model_id)]

    segment = DocumentSegment(document_id="doc", segment_id="body", original_text="Иван Иванов",
                              source_locator={"test": "fixture"})
    gold = {Annotation("doc", "body", 0, 4, EntityType.PERSON)}
    monkeypatch.setattr(module, "load_test", lambda *args, **kwargs: ([(segment, gold)], {"person_annotations": 1}))
    monkeypatch.setattr(module, "_snapshot_hash", lambda path: "48238a57dd048f6e00b157684fc84c0dd35a8dec22b32a588084d260cd66a293")
    monkeypatch.setattr(module, "_hardware", lambda device: {"execution_device": device})
    models_dir = tmp_path / "models"
    (models_dir / FakeAdapter.model_id).mkdir(parents=True)
    result = benchmark(Path("configs/nerel_person_frozen_v1.json"), tmp_path, models_dir, "cpu", adapter=FakeAdapter())
    assert result["exact"]["tp"] == 0
    assert result["exact"]["fp"] == result["exact"]["fn"] == 1
    assert result["overlap_diagnostic"]["tp"] == 1
    assert {item["boundary_relation"] for item in result["errors"]} == {"prediction_wider"}
