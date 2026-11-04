import pytest
from pydantic import ValidationError

from app.schemas.models import OriginalRange, TextSpan


@pytest.mark.parametrize("start,end", [(0, 0), (2, 1)])
def test_original_range_rejects_empty_or_reversed_offsets(start, end):
    with pytest.raises(ValidationError):
        OriginalRange(start=start, end=end)


def test_text_span_rejects_empty_offsets():
    with pytest.raises(ValidationError):
        TextSpan(document_id="d", segment_id="s", start=2, end=2, text="", original_ranges=[OriginalRange(start=0, end=1)])
