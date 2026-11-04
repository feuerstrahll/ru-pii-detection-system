from pathlib import Path

import pytest

from app.core import load_config
from app.core.pipeline import DeterministicPipeline
from app.schemas.models import DocumentSegment, EntityType


CONFIG = Path("configs/deterministic_v1.json")
CONFIG_V2 = Path("configs/deterministic_v2.json")


def detect(text: str, config: Path = CONFIG):
    pipeline = DeterministicPipeline(load_config(config))
    return pipeline.detect([DocumentSegment(document_id="doc", segment_id="s1", original_text=text)])


@pytest.mark.parametrize(
    ("entity_type", "text", "value", "decision"),
    [
        (EntityType.EMAIL, "Почта: ivan.petrov@example.ru.", "ivan.petrov@example.ru", "accept"),
        (EntityType.PHONE, "Телефон:\n+7 (912) 123-45-67.", "+7 (912) 123-45-67", "accept"),
        (EntityType.SOCIAL_SECURITY_ID, "СНИЛС: 112-233-445 95", "112-233-445 95", "accept"),
        (EntityType.TAX_ID, "ИНН: 7707083893", "7707083893", "accept"),
        (EntityType.PASSPORT_RF, "Паспорт серия 45 08 123456", "45 08 123456", "review"),
        (EntityType.ORG_REGISTRATION_ID, "ОГРН: 1027700132195", "1027700132195", "accept"),
        (EntityType.ORG_REGISTRATION_ID, "ОГРНИП: 304500116000157", "304500116000157", "accept"),
        (EntityType.BANK_CARD, "Карта Visa: 4111-1111-1111-1111", "4111-1111-1111-1111", "accept"),
    ],
)
def test_supported_entities_have_explainable_positive_path(entity_type, text, value, decision):
    result = detect(text)
    entity = next(item for item in result.entities if item.entity_type == entity_type and item.span.text == value)
    assert entity.decision == decision
    assert {evidence.signal.value for evidence in entity.evidence} >= {"regex", "validator"}
    assert entity.span.text == text[entity.span.start:entity.span.end]


@pytest.mark.parametrize(
    ("text", "entity_type"),
    [
        ("СНИЛС: 112-233-445 94", EntityType.SOCIAL_SECURITY_ID),
        ("ИНН: 7707083894", EntityType.TAX_ID),
        ("Паспорт: 00 00 123456", EntityType.PASSPORT_RF),
        ("ОГРН: 1027700132194", EntityType.ORG_REGISTRATION_ID),
        ("Карта: 4111 1111 1111 1112", EntityType.BANK_CARD),
    ],
)
def test_invalid_structured_candidates_are_retained_with_rejection_evidence(text, entity_type):
    entity = next(item for item in detect(text).entities if item.entity_type == entity_type)
    assert entity.decision == "reject"
    assert any(evidence.status == "invalid" for evidence in entity.evidence)


@pytest.mark.parametrize(
    "text",
    [
        "not-an-email@",                 # malformed email
        "Тел: +7 912 123-45",             # malformed phone
        "prefix11223344595suffix",        # embedded long numeric string
        "812345678901",                   # unsupported numeric length
        "Заказ 12345678901234567890",     # longer than supported card/ID formats
    ],
)
def test_malformed_or_embedded_values_do_not_produce_accepted_entities(text):
    assert all(entity.decision != "accept" for entity in detect(text).entities)


@pytest.mark.parametrize(
    "text",
    [
        "СНИЛС\u00a0112-233-445\u00a095;",  # NBSP and punctuation
        "ИНН\n7707083893,",                    # multiline label
        "Телефон: +7 (912) 123-45-67; карта: 4111 1111 1111 1111.",
    ],
)
def test_unicode_punctuation_and_multiline_contexts_keep_original_offsets(text):
    result = detect(text)
    assert any(entity.decision == "accept" for entity in result.entities)
    for entity in result.entities:
        assert entity.span.text == text[entity.span.start:entity.span.end]


def test_checksum_valid_numeric_identifier_without_context_is_reviewed_not_autoaccepted():
    entity = next(item for item in detect("7707083893").entities if item.entity_type == EntityType.TAX_ID)
    assert entity.decision == "review"
    assert any(evidence.status == "context_absent" for evidence in entity.evidence)


def test_overlapping_candidates_are_preserved():
    result = detect("4508123456")
    assert result.candidate_count == 2
    assert {entity.entity_type for entity in result.entities} == {EntityType.PASSPORT_RF, EntityType.TAX_ID}


def test_bank_card_pattern_does_not_capture_a_trailing_separator():
    detected = next(item for item in detect("Карта: 4111 1111 1111 1111 ", CONFIG_V2).entities if item.entity_type == EntityType.BANK_CARD)
    assert detected.span.text == "4111 1111 1111 1111"


def test_kpp_is_accepted_only_in_the_versioned_v2_configuration():
    detected = next(item for item in detect("КПП: 770943001", CONFIG_V2).entities if item.entity_type == EntityType.KPP)
    assert detected.decision == "accept"
