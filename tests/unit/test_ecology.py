"""Check candidate accounting and unavailable-metric handling."""

from datetime import datetime, timezone

from app.storage.catalog import SourceObject
from app.storage.ecology import report


NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
POLICY = {"dark_days": 365, "retention_days_by_category": {"records": 100},
          "index_weights": {"duplicate_byte_share": .25, "dark_document_share": .25,
                            "missing_metadata_share": .25, "retention_exceeded_share": .25}}


def obj(identifier: int, name: str, digest: str, days_old: int, access_days: int | None) -> SourceObject:
    from datetime import timedelta

    return SourceObject(id=identifier, relative_path=f"folder/{name}", name=name, file_type="txt",
                        size_bytes=10, owner="team", category="records", sha256=digest,
                        modified_at=NOW - timedelta(days=days_old),
                        accessed_at=NOW - timedelta(days=access_days) if access_days is not None else None,
                        origin="directory", availability="present", inventoried_at=NOW)


def test_report_counts_each_candidate_once_and_separates_actions() -> None:
    items = [obj(1, "case.txt", "a" * 64, 400, 500),
             obj(2, "case_v2.txt", "a" * 64, 300, 500),
             obj(3, "case_v3.txt", "b" * 64, 200, None)]
    result = report(items, [], POLICY, NOW)
    assert result["redundancy"]["duplicate_count"] == 1
    assert result["redundancy"]["duplicate_bytes"] == 10
    assert result["redundancy"]["possible_reclaim_bytes_upper_bound"] == 30
    assert len(result["redundancy"]["probable_versions"]) == 1
    assert len(result["candidates"]) == 3
    assert result["performed_operations"] == []
    assert result["redundancy"]["actual_reclaimed_bytes"] == 0
    assert result["currency"]["access_unknown_count"] == 1


def test_unknown_values_do_not_become_dark_or_retention_violations() -> None:
    item = obj(1, "case.txt", "c" * 64, 1, None)
    item.modified_at = None
    item.category = None
    result = report([item], [], POLICY, NOW)
    assert result["currency"]["dark_count"] == 0
    assert result["currency"]["retention_exceeded_count"] == 0
    assert result["index"]["status"] == "partial"
    assert result["quality_and_pii"]["storage_rule_violation_share"] is None


def test_estimated_effect_uses_configured_coefficients_once() -> None:
    policy = {**POLICY, "cost_per_gib_month": 1024 ** 3,
              "kwh_per_gib_month": 1024 ** 3, "archive_cost_fraction": .5,
              "archive_energy_fraction": .25}
    items = [obj(1, "keep.txt", "a" * 64, 1, 1),
             obj(2, "copy.txt", "a" * 64, 1, 1),
             obj(3, "dark.txt", "b" * 64, 1, 500)]
    result = report(items, [], policy, NOW)["economics"]
    assert result["estimated_volume_reduction_bytes"] == 10
    assert result["estimated_archive_moved_bytes"] == 10
    assert result["estimated_cost_saving_per_month"] == 15
    assert result["estimated_kwh_saving_per_month"] == 17.5
