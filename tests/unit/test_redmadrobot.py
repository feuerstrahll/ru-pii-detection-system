import csv
import hashlib
import json
from pathlib import Path

import pytest

from evaluation.datasets.redmadrobot import (
    bio_spans,
    load_test,
    merge_person,
    token_offsets,
)


@pytest.mark.parametrize(("text", "tokens", "tags", "expected"), [
    ("Иван Петров", ["Иван", "Петров"], ["B-FIRST_NAME", "B-LAST_NAME"], [(0, 4, "FIRST_NAME"), (5, 11, "LAST_NAME")]),
    ("ИНН 123 456", ["ИНН", "123", "456"], ["O", "B-INN", "I-INN"], [(4, 11, "INN")]),
    ("www.X", ["WWW", ".", "X"], ["B-URL", "I-URL", "I-URL"], [(0, 5, "URL")]),
])
def test_bio_to_source_spans(text, tokens, tags, expected):
    assert [(item.start, item.end, item.label) for item in bio_spans(text, tokens, tags)] == expected
    assert all(text[item.start:item.end] for item in bio_spans(text, tokens, tags))


@pytest.mark.parametrize(("text", "tokens", "tags", "message"), [
    ("Иван", ["Иван"], ["I-FIRST_NAME"], "orphan"),
    ("Иван Петров", ["Иван", "Петров"], ["B-FIRST_NAME", "I-LAST_NAME"], "mismatched"),
    ("Иван", ["Иван"], ["B-UNKNOWN"], "unknown"),
    ("Иван", ["Иван"], [], "count differs"),
    ("Иван", ["Пётр"], ["B-FIRST_NAME"], "cannot be aligned"),
])
def test_invalid_bio_or_offset_fails(text, tokens, tags, message):
    with pytest.raises(ValueError, match=message):
        bio_spans(text, tokens, tags)


@pytest.mark.parametrize(("text", "tokens", "tags", "expected"), [
    ("Иван  Петров", ["Иван", "Петров"], ["B-FIRST_NAME", "B-LAST_NAME"], [(0, 12)]),
    ("Петров, Иван", ["Петров", ",", "Иван"], ["B-LAST_NAME", "O", "B-FIRST_NAME"], [(0, 6), (8, 12)]),
    ("Иван Москва Петров", ["Иван", "Москва", "Петров"], ["B-FIRST_NAME", "B-CITY", "B-LAST_NAME"], [(0, 4), (12, 18)]),
])
def test_name_merge_respects_only_whitespace_and_adjacent_components(text, tokens, tags, expected):
    names = [item for item in merge_person(text, bio_spans(text, tokens, tags)) if item.label in ("FIRST_NAME", "LAST_NAME", "MIDDLE_NAME")]
    assert [(item.start, item.end) for item in names] == expected


def test_empty_trailing_source_token_is_allowed_only_outside_entity():
    assert token_offsets("ПАО ", ["ПАО", ""]) == [(0, 3), (4, 4)]
    with pytest.raises(ValueError, match="empty entity token"):
        bio_spans("ПАО ", ["ПАО", ""], ["O", "B-CITY"])


def test_test_dev_exact_duplicate_is_excluded_and_frozen_configs_are_unchanged(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "one.json").write_text(json.dumps({"segments": [{"text": "Иван"}]}), encoding="utf-8")
    source = tmp_path / "test.csv"
    with source.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["text", "tokens", "ner_tags"])
        writer.writeheader()
        for text, tokens, tags in (("Иван", ["Иван"], ["B-FIRST_NAME"]),
                                   ("Петров", ["Петров"], ["B-LAST_NAME"])):
            writer.writerow({"text": text, "tokens": json.dumps(tokens), "ner_tags": json.dumps(tags)})
    config = json.loads(Path("configs/redmadrobot_frozen_v1.json").read_text(encoding="utf-8"))
    before = {key: hashlib.sha256(Path(config[key]).read_bytes()).hexdigest()
              for key in ("deterministic_config", "semantic_config")}
    rows, coverage = load_test(source, dev)
    after = {key: hashlib.sha256(Path(config[key]).read_bytes()).hexdigest()
             for key in before}
    assert before == after
    assert before["deterministic_config"] == config["deterministic_config_sha256"]
    assert before["semantic_config"] == config["semantic_config_sha256"]
    assert coverage["dev_duplicate_row_count"] == 1
    assert coverage["excluded_dev_duplicate_rows"] == [0]
    assert len(rows) == 1 and len(rows[0][2]) == 1
