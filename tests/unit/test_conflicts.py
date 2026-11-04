from pathlib import Path

import pytest

from app.core import load_config
from app.core.pipeline import DeterministicPipeline
from app.schemas.models import DocumentSegment, EntityType, EvidenceSignal


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("1157746789012", "ОГРН"),
        ("305420717425070", "ОГРНИП"),
    ],
)
def test_checksum_valid_ogrn_and_card_collision_preserves_candidates_and_rejects_card(value, label):
    result = DeterministicPipeline(load_config(Path("configs/deterministic_v3.json"))).detect([
        DocumentSegment(document_id="doc", segment_id="body", original_text=f"{label}: {value}")
    ])
    ogrn = next(item for item in result.entities if item.entity_type == EntityType.ORG_REGISTRATION_ID)
    card = next(item for item in result.entities if item.entity_type == EntityType.BANK_CARD)

    assert ogrn.span.start == card.span.start and ogrn.span.end == card.span.end
    assert any(item.status == "checksum_valid" for item in ogrn.evidence)
    assert any(item.status == "checksum_valid" for item in card.evidence)
    conflict = next(item for item in card.evidence if item.signal == EvidenceSignal.CONFLICT)
    assert conflict.attributes == {
        "type": "IDENTIFIER_OVERLAP",
        "candidates": ["ORG_REGISTRATION_ID", "BANK_CARD"],
        "exact_span_overlap": True,
        "checks": {"ogrn_checksum_valid": True, "luhn_valid": True},
    }
    assert any(item.signal == EvidenceSignal.CONFLICT for item in ogrn.evidence)
    assert card.score_contributions["identifier_overlap"] == -0.60
    assert card.decision == "reject"
    assert ogrn.decision == "accept"
