from pathlib import Path

import pytest

from evaluation.datasets.nerel import _read_annotations


@pytest.mark.parametrize(("annotation", "error"), [
    ("T1\tPERSON 0 4\tИван", None),
    ("T1\tPERSON 0 4\tПётр", "source text differs"),
    ("T1\tPERSON 0 9\tИван", "outside text"),
    ("T1\tPERSON 0 2;2 4\tИван", "discontinuous PERSON"),
    ("T1\tUNKNOWN 0 4\tИван", "unknown NEREL label"),
    ("T1 PERSON 0 4 Иван", "invalid NEREL text annotation"),
])
def test_brat_person_offsets_and_malformed_annotations(tmp_path: Path, annotation: str, error: str | None) -> None:
    path = tmp_path / "sample.ann"
    path.write_text(annotation + "\n", encoding="utf-8")
    if error:
        with pytest.raises(ValueError, match=error):
            _read_annotations(path, "Иван", {"PERSON", "LOCATION"}, "sample")
    else:
        person, spans, counts, skipped = _read_annotations(path, "Иван", {"PERSON", "LOCATION"}, "sample")
        assert [(item.start, item.end) for item in person] == [(0, 4)]
        assert spans == [(0, 4, "PERSON")]
        assert counts == {"PERSON": 1} and not skipped
