"""Calculate read-only storage and PII-risk indicators from catalog metadata."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
from statistics import mean

from app.storage.catalog import AuditEvent, InventorySnapshot, SourceObject
from app.schemas.models import EntityType


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _share(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def report(objects: list[SourceObject], snapshots: list[InventorySnapshot], policy: dict,
           now: datetime, operations: list[AuditEvent] | None = None) -> dict:
    """Return observed metrics, review candidates, and a provisional index."""
    now = _utc(now)
    if now is None:
        raise ValueError("as_of is required")
    dark_days = policy["dark_days"]
    retention = policy["retention_days_by_category"]
    if dark_days <= 0 or any(days <= 0 for days in retention.values()):
        raise ValueError("policy periods must be positive")
    present = sorted((o for o in objects if o.availability == "present" and o.origin == "directory"), key=lambda o: o.id)
    archived = [o for o in objects if o.availability == "archived" and o.origin == "directory"]
    pending_deletion = [o for o in objects if o.availability == "deletion_pending" and o.origin == "directory"]
    n = len(present)
    groups: dict[str, list[SourceObject]] = defaultdict(list)
    for obj in present:
        if obj.sha256 and re.fullmatch(r"[0-9a-f]{64}", obj.sha256):
            groups[obj.sha256].append(obj)
    duplicate_ids: set[int] = set()
    duplicate_groups = []
    for digest, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        keeper = min(members, key=lambda o: ((_utc(o.modified_at) or now), o.id))
        copies = [o for o in members if o.id != keeper.id]
        duplicate_ids.update(o.id for o in copies)
        duplicate_groups.append({"sha256": digest, "keep_id": keeper.id,
                                 "duplicate_ids": [o.id for o in copies]})
    stems: dict[tuple[str, str], list[SourceObject]] = defaultdict(list)
    for obj in present:
        raw_stem = obj.name.rsplit(".", 1)[0]
        stem = re.sub(r"(?i)(?:[ _.-]+(?:v|версия|version)[ _.-]*\d+)$", "", raw_stem).casefold()
        if stem:
            stems[(obj.relative_path.rsplit("/", 1)[0].rsplit("\\", 1)[0], stem)].append(obj)
    versions = []
    for (directory, stem), members in sorted(stems.items()):
        hashes = {o.sha256 for o in members if o.sha256}
        has_marker = any(re.search(r"(?i)(?:[ _.-]+(?:v|версия|version)[ _.-]*\d+)$", o.name.rsplit(".", 1)[0]) for o in members)
        if len(members) > 1 and len(hashes) > 1 and has_marker:
            versions.append({"directory": directory, "base_name": stem,
                             "object_ids": [o.id for o in members],
                             "criterion": "same directory and stem after terminal version marker; different SHA-256"})
    dark_ids, expired_ids, unknown_access = set(), set(), set()
    ages = []
    missing_metadata = 0
    format_bytes: dict[str, int] = defaultdict(int)
    category_bytes: dict[str, int] = defaultdict(int)
    pii_counts: Counter[str] = Counter({kind.value: 0 for kind in EntityType})
    pii_documents = 0
    assessed_pii = 0
    for obj in present:
        size = obj.size_bytes or 0
        format_bytes[obj.file_type or "unknown"] += size
        category_bytes[obj.category or "unknown"] += size
        if not obj.owner or not obj.category or obj.category == "unclassified" or obj.size_bytes is None:
            missing_metadata += 1
        origin_date = _utc(obj.created_at or obj.modified_at)
        if origin_date and origin_date <= now:
            ages.append((now - origin_date).days)
        access = _utc(obj.accessed_at)
        if access is None or access > now:
            unknown_access.add(obj.id)
        elif now - access > timedelta(days=dark_days):
            dark_ids.add(obj.id)
        period = retention.get(obj.category)
        if period and origin_date and origin_date <= now and now - origin_date > timedelta(days=period):
            expired_ids.add(obj.id)
        if obj.pii_counts_json is not None and obj.processing_status == "completed":
            counts = json.loads(obj.pii_counts_json)
            assessed_pii += 1
            pii_documents += bool(sum(counts.values()))
            pii_counts.update(counts)
    candidate_ids = duplicate_ids | dark_ids | expired_ids
    candidates = []
    for obj in present:
        if obj.id not in candidate_ids:
            continue
        reasons = [reason for reason, ids in (("exact_duplicate", duplicate_ids), ("dark", dark_ids),
                                             ("retention_exceeded", expired_ids)) if obj.id in ids]
        candidates.append({"object_id": obj.id, "reasons": reasons,
                           "recommendation": "review_for_deletion" if obj.id in duplicate_ids or obj.id in expired_ids
                           else "review_for_archiving", "status": "candidate", "estimated_bytes": obj.size_bytes})
    known = [o for o in present if o.size_bytes is not None]
    total_bytes = sum(o.size_bytes for o in known)
    archive_known = [o for o in archived if o.size_bytes is not None]
    archive_total_bytes = sum(o.size_bytes for o in archive_known)
    possible_bytes = sum(o.size_bytes or 0 for o in present if o.id in candidate_ids)
    unknown_candidate_sizes = sum(o.id in candidate_ids and o.size_bytes is None for o in present)
    duplicate_bytes = sum(o.size_bytes or 0 for o in present if o.id in duplicate_ids)
    access_known = n - len(unknown_access)
    retention_known = sum(bool(retention.get(o.category) and _utc(o.created_at or o.modified_at)
                               and _utc(o.created_at or o.modified_at) <= now) for o in present)
    indicators = {"duplicate_byte_share": _share(duplicate_bytes, total_bytes) if len(known) == n else None,
                  "dark_document_share": _share(len(dark_ids), access_known),
                  "missing_metadata_share": _share(missing_metadata, n),
                  "retention_exceeded_share": _share(len(expired_ids), retention_known)}
    weights = policy["index_weights"]
    if set(weights) != set(indicators) or any(v < 0 for v in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
        raise ValueError("index weights must cover all indicators and sum to one")
    available = {key: value for key, value in indicators.items() if value is not None}
    weight_sum = sum(weights[key] for key in available)
    index = 100 * (1 - sum(weights[key] * value for key, value in available.items()) / weight_sum) if weight_sum else None
    history = [{"snapshot_id": s.id, "scanned_at": s.scanned_at.isoformat(), "scope": s.scope,
                "object_count": s.object_count, "total_bytes": s.total_bytes}
               for s in sorted(snapshots, key=lambda s: (s.scanned_at, s.id))]
    recommendations = [{"object_id": item["object_id"], "action": item["recommendation"],
                        "status": "proposed", "estimated_bytes": item["estimated_bytes"]}
                       for item in candidates]
    delete_bytes = sum(item["estimated_bytes"] or 0 for item in recommendations
                       if item["action"] == "review_for_deletion")
    archive_bytes = sum(item["estimated_bytes"] or 0 for item in recommendations
                        if item["action"] == "review_for_archiving")
    delete_unknown = any(item["estimated_bytes"] is None for item in recommendations
                         if item["action"] == "review_for_deletion")
    archive_unknown = any(item["estimated_bytes"] is None for item in recommendations
                          if item["action"] == "review_for_archiving")
    archive_cost = policy.get("archive_cost_fraction", 1)
    archive_energy = policy.get("archive_energy_fraction", 1)
    cost = policy.get("cost_per_gib_month")
    energy = policy.get("kwh_per_gib_month")
    if any(not 0 <= fraction <= 1 for fraction in (archive_cost, archive_energy)) or any(
        value is not None and value < 0 for value in (cost, energy)
    ):
        raise ValueError("economic coefficients must be nonnegative and archive fractions in [0, 1]")
    gib = 1024 ** 3
    cost_saving_bytes = delete_bytes + archive_bytes * (1 - archive_cost)
    energy_saving_bytes = delete_bytes + archive_bytes * (1 - archive_energy)
    risk_recommendations = [{"object_id": o.id, "action": "review_access_and_retention_policy",
                             "reason": "PII candidates detected; verify purpose, access and retention before action"}
                            for o in present if o.pii_counts_json and o.processing_status == "completed"
                            and sum(json.loads(o.pii_counts_json).values()) > 0]
    by_id = {obj.id: obj for obj in objects}
    performed = [{"object_id": event.object_id, "action": event.action,
                  "occurred_at": event.occurred_at.isoformat(), "sha256": event.detail}
                 for event in (operations or []) if event.object_id in by_id]
    deleted_ids = {item["object_id"] for item in performed if item["action"] == "file_delete"}
    reclaimed = sum(by_id[identifier].size_bytes or 0 for identifier in deleted_ids)
    return {"as_of": now.isoformat(), "scope": "present directory objects", "object_count": n,
            "assumptions": {"dark_days": dark_days, "retention_days_by_category": retention,
                            "cost_per_gib_month": cost, "kwh_per_gib_month": energy,
                            "archive_cost_fraction": archive_cost,
                            "archive_energy_fraction": archive_energy},
            "volume": {"known_bytes": total_bytes, "unknown_size_count": n - len(known),
                       "archive_known_bytes": archive_total_bytes,
                       "archive_unknown_size_count": len(archived) - len(archive_known),
                       "deletion_pending_count": len(pending_deletion),
                       "total_managed_known_bytes": total_bytes + archive_total_bytes,
                       "by_format_bytes": dict(sorted(format_bytes.items())),
                       "by_category_bytes": dict(sorted(category_bytes.items())),
                       "registered_snapshots": history},
            "redundancy": {"duplicate_count": len(duplicate_ids), "duplicate_share": _share(len(duplicate_ids), n),
                           "duplicate_bytes": duplicate_bytes, "groups": duplicate_groups, "probable_versions": versions,
                           "possible_reclaim_bytes_upper_bound": possible_bytes if not unknown_candidate_sizes else None,
                           "candidate_unknown_size_count": unknown_candidate_sizes,
                           "actual_reclaimed_bytes": None if deleted_ids else 0,
                           "recorded_archive_file_bytes_removed": reclaimed},
            "currency": {"age_days_mean": mean(ages) if ages else None, "age_days_max": max(ages, default=None),
                         "age_known_count": len(ages), "dark_count": len(dark_ids),
                         "dark_share_of_known_access": _share(len(dark_ids), access_known),
                         "access_unknown_count": len(unknown_access), "retention_exceeded_count": len(expired_ids),
                         "retention_evaluable_count": retention_known},
            "quality_and_pii": {"missing_metadata_share": _share(missing_metadata, n),
                                "pii_assessed_count": assessed_pii, "pii_detected_document_count": pii_documents,
                                "pii_detected_share_of_assessed": _share(pii_documents, assessed_pii),
                                "detected_entities_by_class": dict(sorted(pii_counts.items())),
                                "storage_rule_violation_share": _share(len(expired_ids), retention_known),
                                "storage_rule_status": "internal category retention only; security rules unassessed; not a legal finding"},
            "index": {"value": index, "indicators": indicators, "weights": weights,
                      "available_weight": weight_sum, "status": "complete" if len(available) == len(weights) else "partial"},
            "candidates": candidates, "recommendations": recommendations,
            "pii_risk_recommendations": risk_recommendations,
            "economics": {"currency": policy.get("currency", "RUB"),
                          "baseline_cost_per_month": (total_bytes + archive_total_bytes * archive_cost) / gib * cost
                          if cost is not None and len(known) == n and len(archive_known) == len(archived) else None,
                          "baseline_kwh_per_month": (total_bytes + archive_total_bytes * archive_energy) / gib * energy
                          if energy is not None and len(known) == n and len(archive_known) == len(archived) else None,
                          "estimated_deleted_bytes": delete_bytes if not delete_unknown else None,
                          "estimated_volume_reduction_bytes": delete_bytes if not delete_unknown else None,
                          "estimated_archive_moved_bytes": archive_bytes if not archive_unknown else None,
                          "estimated_cost_saving_per_month": cost_saving_bytes / gib * cost
                          if cost is not None and not (delete_unknown or archive_unknown) else None,
                          "estimated_kwh_saving_per_month": energy_saving_bytes / gib * energy
                          if energy is not None and not (delete_unknown or archive_unknown) else None,
                          "status": "estimate; assumes independent file storage and approved actions"},
            "performed_operations": performed}
