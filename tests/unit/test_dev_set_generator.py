import json

import pytest

from evaluation.datasets.dev_set import generate_dev_set


@pytest.mark.parametrize("count", [21, 42])
def test_dev_set_is_marked_and_includes_positive_and_hard_negative_templates(tmp_path, count):
    generate_dev_set(tmp_path, count=count, seed=7)
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(tmp_path.glob("*.json"))]

    assert len(documents) == count
    assert {item["tier"] for item in documents} == {"dev"}
    assert any(not item["entities"] for item in documents)
    assert {entity["type"] for item in documents for entity in item["entities"]} >= {
        "PERSON", "ADDRESS", "BANK_CARD", "KPP",
    }
