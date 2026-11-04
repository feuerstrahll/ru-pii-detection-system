"""Move cataloged files into managed archive and delete confirmed archived files."""

from hashlib import sha256
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.storage.catalog import AuditEvent, SourceObject, utc_now


def digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def managed_path(root: Path, relative: str) -> Path:
    base = root.resolve()
    candidate = root / relative
    path = candidate.resolve()
    if not path.is_relative_to(base):
        raise ValueError("path outside managed root or symbolic link")
    cursor = candidate
    while cursor != root and cursor.is_relative_to(root):
        if cursor.is_symlink():
            raise ValueError("symbolic links are not managed files")
        cursor = cursor.parent
    return path


def archive(session: Session, obj: SourceObject, source_root: Path, archive_root: Path, user_id: int) -> dict:
    if archive_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("archive root must be outside scanned source root")
    if obj.origin != "directory" or obj.availability != "present" or not obj.sha256:
        raise ValueError("only present inventoried files can be archived")
    source = managed_path(source_root, obj.relative_path)
    if not source.is_file() or digest_file(source) != obj.sha256:
        raise ValueError("source changed or unavailable; rescan first")
    archive_root.mkdir(parents=True, exist_ok=True)
    relative = f"{obj.id}/{obj.sha256}"
    target = managed_path(archive_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("archive target already exists")
    try:
        os.replace(source, target)
        if digest_file(target) != obj.sha256:
            raise ValueError("archive digest mismatch")
        obj.archived_path = relative
        obj.availability = "archived"
        session.add(AuditEvent(user_id=user_id, occurred_at=utc_now(), action="file_archive",
                               object_id=obj.id, detail=obj.sha256))
        session.commit()
    except Exception:
        session.rollback()
        if target.exists() and not source.exists():
            os.replace(target, source)
        raise
    return {"object_id": obj.id, "state": "archived", "sha256": obj.sha256,
            "actual_source_bytes_moved": obj.size_bytes}


def delete_archived(session: Session, obj: SourceObject, archive_root: Path,
                    user_id: int, expected_sha256: str, confirmation: str) -> dict:
    if obj.availability not in {"archived", "deletion_pending"} or not obj.archived_path:
        raise ValueError("only archived files can be deleted")
    if confirmation != f"DELETE {obj.id} {expected_sha256}" or expected_sha256 != obj.sha256:
        raise ValueError("explicit object and digest confirmation required")
    target = managed_path(archive_root, obj.archived_path)
    if obj.availability == "archived" and (not target.is_file() or digest_file(target) != expected_sha256):
        raise ValueError("archived file changed or unavailable")
    if obj.availability == "archived":
        staged = target.with_name(target.name + ".pending-delete")
        if staged.exists():
            raise ValueError("pending deletion already exists")
        try:
            os.replace(target, staged)
            obj.availability = "deletion_pending"
            obj.archived_path += ".pending-delete"
            session.add(AuditEvent(user_id=user_id, occurred_at=utc_now(), action="file_delete_pending",
                                   object_id=obj.id, detail=expected_sha256))
            session.commit()
        except Exception:
            session.rollback()
            if staged.exists() and not target.exists():
                os.replace(staged, target)
            raise
    else:
        staged = target
    if staged.exists():
        if not staged.is_file() or digest_file(staged) != expected_sha256:
            raise ValueError("pending deletion file changed")
        staged.unlink()
    obj.availability = "deleted"
    obj.archived_path = None
    session.add(AuditEvent(user_id=user_id, occurred_at=utc_now(), action="file_delete",
                           object_id=obj.id, detail=expected_sha256))
    session.commit()
    return {"object_id": obj.id, "state": "deleted", "sha256": expected_sha256,
            "actual_archive_bytes_removed": obj.size_bytes}
