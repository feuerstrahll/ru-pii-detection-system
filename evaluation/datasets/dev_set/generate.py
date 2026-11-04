"""Generate marked Tier 0 documents from canonical Faker test fixtures."""

import json
from pathlib import Path

from app.schemas.models import EntityType
from tests.unit.fixtures import ru_pii_generators as values


def generate_dev_set(output: Path, count: int = 2_000, seed: int = 20260922) -> None:
    """Write deterministic, sentence-level dev documents and hard negatives."""
    if count < 1:
        raise ValueError("count must be positive")
    output.mkdir(parents=True, exist_ok=True)
    faker = values.new_faker(seed)
    for index in range(count):
        templates = _templates(faker)
        text, entity_type, value = templates[index % len(templates)]
        entities = [] if entity_type is None else [{
            "segment_id": "body", "start": text.index(value), "end": text.index(value) + len(value), "type": entity_type.value,
        }]
        document = {
            "tier": "dev",
            "document_id": f"dev-{index:05d}",
            "segments": [{"segment_id": "body", "text": text}],
            "entities": entities,
        }
        (output / f"dev-{index:05d}.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _templates(faker) -> list[tuple[str, EntityType | None, str]]:
    person = values.generate_person(faker)
    address = values.generate_address(faker)
    email = values.generate_email(faker)
    phone = values.generate_phone(faker)
    snils = values.generate_valid_snils(faker)
    inn = values.generate_valid_inn(faker)
    passport = values.generate_valid_passport(faker)
    ogrn = values.generate_valid_ogrn(faker)
    card = values.generate_valid_bank_card(faker)
    kpp = values.generate_valid_kpp(faker)
    cvc = values.generate_valid_cvc(faker)
    token = values.generate_valid_token(faker)
    invalid = [
        values.generate_invalid_snils(faker), values.generate_invalid_inn(faker), values.generate_invalid_ogrn(faker),
        values.generate_invalid_passport(faker), values.generate_invalid_bank_card(faker), values.generate_invalid_kpp(faker),
        values.generate_invalid_cvc(faker), values.generate_invalid_token(faker),
    ]
    positives = [
        (f"Сотрудник {person}", EntityType.PERSON, person),
        (f"Адрес доставки: {address}", EntityType.ADDRESS, address),
        (f"Почта: {email}", EntityType.EMAIL, email),
        (f"Телефон: {phone}", EntityType.PHONE, phone),
        (f"СНИЛС: {snils}", EntityType.SOCIAL_SECURITY_ID, snils),
        (f"ИНН: {inn}", EntityType.TAX_ID, inn),
        (f"Паспорт: {passport}", EntityType.PASSPORT, passport),
        (f"ОГРН: {ogrn}", EntityType.ORG_REGISTRATION_ID, ogrn),
        (f"Карта: {card} активна", EntityType.BANK_CARD, card),
        (f"КПП: {kpp}", EntityType.KPP, kpp),
        (f"Код CVC для карты: {cvc}", EntityType.CVC, cvc),
        (f"Персональный token: {token}", EntityType.TOKEN, token),
    ]
    negatives = [(f"Заказ № {item} не является персональным идентификатором", None, item) for item in invalid]
    negatives.append(("Компания Иванов и партнёры заключила договор", None, ""))
    return positives + negatives
