from pathlib import Path

from app.core import load_config
from app.detection.aggregation import aggregate_candidates
from app.detection.deterministic import RegexCandidateGenerator
from app.schemas.models import DocumentSegment


def test_aggregation_merges_only_identical_candidates_and_keeps_overlaps():
    config = load_config(Path("configs/deterministic_v1.json"))
    generator = RegexCandidateGenerator(config)
    candidates = generator.detect(DocumentSegment(document_id="d", segment_id="s", original_text="4508123456"))
    duplicate = candidates[0].model_copy()
    groups = aggregate_candidates([*candidates, duplicate])
    assert sorted(len(group) for group in groups) == [1, 2]
