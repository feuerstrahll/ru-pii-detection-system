"""Verify a failed archive deletion remains recoverable and auditable."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.storage.catalog import AuditEvent, Base, SourceObject, User
from app.storage.lifecycle import delete_archived


def test_pending_delete_can_be_retried_after_unlink_error(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    archive_root = tmp_path / "archive"
    content = b"sensitive test fixture"
    digest = sha256(content).hexdigest()
    target = archive_root / "1" / digest
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    with Session(engine) as session:
        session.add(User(id=1, login="admin", password_hash="unused", role="admin"))
        session.add(SourceObject(id=1, relative_path="file.txt", name="file.txt", file_type="txt",
                                 size_bytes=len(content), sha256=digest, inventoried_at=datetime.now(timezone.utc),
                                 availability="archived", archived_path=f"1/{digest}", origin="directory"))
        session.commit()
        obj = session.get(SourceObject, 1)
        original_unlink = Path.unlink

        def fail_once(path: Path, *args, **kwargs):
            monkeypatch.setattr(Path, "unlink", original_unlink)
            raise OSError("simulated unlink failure")

        monkeypatch.setattr(Path, "unlink", fail_once)
        with pytest.raises(OSError, match="simulated"):
            delete_archived(session, obj, archive_root, 1, digest, f"DELETE 1 {digest}")
        session.refresh(obj)
        assert obj.availability == "deletion_pending"
        assert (archive_root / obj.archived_path).read_bytes() == content
        result = delete_archived(session, obj, archive_root, 1, digest, f"DELETE 1 {digest}")
        assert result["state"] == "deleted"
        assert [event.action for event in session.scalars(select(AuditEvent).order_by(AuditEvent.id))] == [
            "file_delete_pending", "file_delete"]
