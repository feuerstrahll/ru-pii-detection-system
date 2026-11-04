import pytest

from app.detection.deterministic import _validate_inn, _validate_luhn, _validate_ogrn, _validate_snils, StructuredValidator
from app.schemas.models import EntityType
from tests.unit.fixtures import ru_pii_generators as generators


class FixedFaker:
    def __init__(self) -> None:
        self._digit = 0

    def random_digit(self) -> int:
        self._digit = (self._digit + 1) % 10
        return self._digit

    def random_int(self, minimum: int, maximum: int) -> int:
        return minimum

    def passport_series(self) -> str:
        return "4508"

    def passport_number(self) -> str:
        return "123456"


@pytest.mark.parametrize(
    ("valid", "invalid", "validator"),
    [
        (lambda fake: generators.generate_valid_snils(fake, formatted=False), lambda fake: generators.generate_invalid_snils(fake, formatted=False), lambda value: _validate_snils(value) == "checksum_valid"),
        (lambda fake: generators.generate_valid_inn(fake, 10), lambda fake: generators.generate_invalid_inn(fake, 10), _validate_inn),
        (lambda fake: generators.generate_valid_inn(fake, 12), lambda fake: generators.generate_invalid_inn(fake, 12), _validate_inn),
        (lambda fake: generators.generate_valid_ogrn(fake), lambda fake: generators.generate_invalid_ogrn(fake), _validate_ogrn),
        (lambda fake: generators.generate_valid_ogrnip(fake), lambda fake: generators.generate_invalid_ogrnip(fake), _validate_ogrn),
        (lambda fake: generators.generate_valid_bank_card(fake, formatted=False), lambda fake: generators.generate_invalid_bank_card(fake, formatted=False), _validate_luhn),
    ],
)
def test_checksum_generators_produce_valid_and_invalid_pairs(valid, invalid, validator):
    assert validator(valid(FixedFaker()))
    assert not validator(invalid(FixedFaker()))


def test_passport_generator_obeys_format_without_claiming_a_checksum():
    passport = generators.generate_valid_passport(FixedFaker())
    assert StructuredValidator._format_valid(EntityType.PASSPORT_RF, passport.replace(" ", ""))
    assert passport == "45 08 123456"
