"""Canonical Faker-backed generators for fictional Russian PII test values."""

from typing import Any


def new_faker(seed: int | None = None) -> Any:
    """Return the faker-ru-pii Russian provider, optionally with a fixed seed."""
    try:
        from faker_ru_pii import Faker
    except ImportError as error:
        raise RuntimeError("install the test-data extra to use faker-ru-pii generators") from error
    faker = Faker("ru_RU")
    if seed is not None:
        faker.seed_instance(seed)
    return faker


def generate_person(faker: Any | None = None) -> str:
    return (faker or new_faker()).name()


def generate_address(faker: Any | None = None) -> str:
    return (faker or new_faker()).address().replace("\n", ", ")


def generate_email(faker: Any | None = None) -> str:
    return (faker or new_faker()).email()


def generate_phone(faker: Any | None = None) -> str:
    return (faker or new_faker()).phone_number()


def generate_valid_snils(faker: Any | None = None, formatted: bool = True) -> str:
    fake = faker or new_faker()
    body = f"{fake.random_int(1_001_998, 999_999_999):09d}"
    control = _snils_control(body)
    digits = f"{body}{control:02d}"
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:9]} {digits[9:]}" if formatted else digits


def generate_invalid_snils(faker: Any | None = None, formatted: bool = True) -> str:
    valid = generate_valid_snils(faker, formatted=False)
    invalid = f"{valid[:-1]}{(int(valid[-1]) + 1) % 10}"
    return f"{invalid[:3]}-{invalid[3:6]}-{invalid[6:9]} {invalid[9:]}" if formatted else invalid


def generate_valid_inn(faker: Any | None = None, length: int = 10) -> str:
    fake = faker or new_faker()
    if length == 10:
        body = _digits(fake, 9)
        return body + str(_inn_control(body, (2, 4, 10, 3, 5, 9, 4, 6, 8)))
    if length == 12:
        body = _digits(fake, 10)
        first = _inn_control(body, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
        second = _inn_control(body + str(first), (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
        return body + str(first) + str(second)
    raise ValueError("INN length must be 10 or 12")


def generate_invalid_inn(faker: Any | None = None, length: int = 10) -> str:
    return _replace_last_digit(generate_valid_inn(faker, length))


def generate_valid_ogrn(faker: Any | None = None, length: int = 13) -> str:
    fake = faker or new_faker()
    if length == 13:
        body = _digits(fake, 12)
        return body + str((int(body) % 11) % 10)
    if length == 15:
        body = _digits(fake, 14)
        return body + str((int(body) % 13) % 10)
    raise ValueError("OGRN length must be 13 or 15")


def generate_invalid_ogrn(faker: Any | None = None, length: int = 13) -> str:
    return _replace_last_digit(generate_valid_ogrn(faker, length))


def generate_valid_ogrnip(faker: Any | None = None) -> str:
    return generate_valid_ogrn(faker, length=15)


def generate_invalid_ogrnip(faker: Any | None = None) -> str:
    return generate_invalid_ogrn(faker, length=15)


def generate_valid_passport(faker: Any | None = None) -> str:
    fake = faker or new_faker()
    series = str(fake.passport_series())
    while not (len(series) == 4 and series.isdigit() and 1 <= int(series[:2]) <= 99):
        series = f"{fake.random_int(1, 99):02d}{_digits(fake, 2)}"
    number = str(fake.passport_number())
    while not (len(number) == 6 and number.isdigit()):
        number = _digits(fake, 6)
    return f"{series[:2]} {series[2:]} {number}"


def generate_invalid_passport(faker: Any | None = None) -> str:
    valid = generate_valid_passport(faker)
    return f"00{valid[2:]}"


def generate_valid_bank_card(faker: Any | None = None, length: int = 16, formatted: bool = True) -> str:
    if not 13 <= length <= 19:
        raise ValueError("bank-card length must be between 13 and 19")
    fake = faker or new_faker()
    body = _digits(fake, length - 1)
    control = _luhn_control(body)
    digits = body + str(control)
    return " ".join(digits[index:index + 4] for index in range(0, len(digits), 4)) if formatted else digits


def generate_invalid_bank_card(faker: Any | None = None, length: int = 16, formatted: bool = True) -> str:
    valid = generate_valid_bank_card(faker, length, formatted=False)
    invalid = _replace_last_digit(valid)
    return " ".join(invalid[index:index + 4] for index in range(0, len(invalid), 4)) if formatted else invalid


def generate_valid_kpp(faker: Any | None = None) -> str:
    return _digits(faker or new_faker(), 9)


def generate_invalid_kpp(faker: Any | None = None) -> str:
    return _digits(faker or new_faker(), 8)


def generate_valid_cvc(faker: Any | None = None, length: int = 3) -> str:
    if length not in {3, 4}:
        raise ValueError("CVC length must be 3 or 4")
    return _digits(faker or new_faker(), length)


def generate_invalid_cvc(faker: Any | None = None) -> str:
    return _digits(faker or new_faker(), 2)


def generate_valid_token(faker: Any | None = None) -> str:
    return str((faker or new_faker()).sha256())


def generate_invalid_token(faker: Any | None = None) -> str:
    return "not-a-secret"


def _digits(faker: Any, length: int) -> str:
    return "".join(str(faker.random_digit()) for _ in range(length))


def _replace_last_digit(value: str) -> str:
    return f"{value[:-1]}{(int(value[-1]) + 1) % 10}"


def _snils_control(body: str) -> int:
    total = sum(int(digit) * weight for digit, weight in zip(body, range(9, 0, -1)))
    if total < 100:
        return total
    if total in (100, 101):
        return 0
    remainder = total % 101
    return 0 if remainder == 100 else remainder


def _inn_control(body: str, weights: tuple[int, ...]) -> int:
    return sum(int(digit) * weight for digit, weight in zip(body, weights)) % 11 % 10


def _luhn_control(body: str) -> int:
    for control in range(10):
        if _luhn_valid(body + str(control)):
            return control
    raise AssertionError("a Luhn control digit must exist")


def _luhn_valid(value: str) -> bool:
    total = 0
    parity = len(value) % 2
    for index, char in enumerate(value):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
