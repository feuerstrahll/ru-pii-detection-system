"""Deterministic exact-span candidate aggregation without removing overlaps."""

from collections import defaultdict

from app.schemas.models import EntityCandidate


def aggregate_candidates(candidates: list[EntityCandidate]) -> list[list[EntityCandidate]]:
    """Group only identical normalized entity spans; overlapping spans stay independent."""
    grouped: dict[tuple[str, str, int, int, str, str | None], list[EntityCandidate]] = defaultdict(list)
    for candidate in candidates:
        span = candidate.span
        key = (
            span.document_id,
            span.segment_id,
            span.start,
            span.end,
            candidate.entity_type.value,
            candidate.normalized_value,
        )
        grouped[key].append(candidate)
    return [grouped[key] for key in sorted(grouped)]
