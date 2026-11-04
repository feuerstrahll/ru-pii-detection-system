"""Create and restore checked SQLite and managed-file backup chains."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from app.storage.lifecycle import digest_file, managed_path


def _manifest(root: Path, backup_id: str) -> dict:
    if not backup_id or not all(c in "0123456789abcdef" for c in backup_id) or len(backup_id) != 32:
        raise ValueError("invalid backup id")
    folder = root / backup_id
    raw = (folder / "manifest.json").read_bytes()
    if sha256(raw).hexdigest() != (folder / "manifest.sha256").read_text(encoding="ascii"):
        raise ValueError("backup manifest checksum mismatch")
    manifest = json.loads(raw)
    if manifest.get("id") != backup_id:
        raise ValueError("backup manifest id mismatch")
    return manifest


def list_backups(root: Path) -> list[dict]:
    """List backup metadata without exposing document contents."""
    if not root.exists():
        return []
    result = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
            continue
        try:
            manifest = _manifest(root, folder.name)
            result.append({"id": manifest["id"], "kind": manifest["kind"],
                           "parent_id": manifest["parent_id"], "file_count": len(manifest["files"]),
                           "status": "manifest_valid"})
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            result.append({"id": folder.name, "kind": None, "parent_id": None,
                           "file_count": None, "status": "manifest_invalid"})
    return result


def _files(snapshot: Path) -> dict[str, str]:
    with sqlite3.connect(snapshot) as db:
        rows = db.execute("SELECT relative_path, archived_path, sha256, availability, origin FROM source_objects").fetchall()
    result = {}
    for relative, archived, digest, availability, origin in rows:
        if origin == "directory" and availability in {"present", "archived", "deletion_pending"}:
            if not digest or len(digest) != 64:
                raise ValueError("managed file lacks SHA-256")
            if availability in {"archived", "deletion_pending"} and not archived:
                raise ValueError("archived file lacks managed path")
            key = f"archive/{archived}" if availability in {"archived", "deletion_pending"} else f"source/{relative}"
            if key in result:
                raise ValueError("duplicate managed path")
            result[key] = digest
    return result


def create_backup(database: Path, source_root: Path, archive_root: Path,
                  backup_root: Path, kind: str, parent_id: str | None = None) -> dict:
    if kind not in {"full", "incremental"} or (kind == "full") != (parent_id is None):
        raise ValueError("full needs no parent; incremental needs a parent")
    if backup_root.resolve().is_relative_to(source_root.resolve()) or backup_root.resolve().is_relative_to(archive_root.resolve()):
        raise ValueError("backup root must be outside managed roots")
    parent = _manifest(backup_root, parent_id) if parent_id else None
    backup_id = uuid4().hex
    folder = backup_root / backup_id
    folder.mkdir(parents=True, exist_ok=False)
    os.chmod(folder, 0o700)
    try:
        snapshot = folder / "catalog.sqlite3"
        with sqlite3.connect(database) as live, sqlite3.connect(snapshot) as copy:
            live.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("catalog snapshot is corrupt")
        os.chmod(snapshot, 0o600)
        current = _files(snapshot)
        entries = {}
        for key, digest in sorted(current.items()):
            root, relative = key.split("/", 1)
            actual_root = source_root if root == "source" else archive_root
            path = managed_path(actual_root, relative)
            if not path.is_file() or digest_file(path) != digest:
                raise ValueError(f"managed file changed: {key}")
            if parent and key in parent["files"] and parent["files"][key]["sha256"] == digest:
                prior = parent["files"][key]
                if not re.fullmatch(r"[0-9a-f]{32}", prior["stored_in"]):
                    raise ValueError("invalid parent blob reference")
                old_blob = managed_path(backup_root / prior["stored_in"] / "files" / root, relative)
                if not old_blob.is_file() or digest_file(old_blob) != digest:
                    raise ValueError(f"parent backup blob checksum mismatch: {key}")
                entries[key] = prior
                continue
            destination = managed_path(folder / "files" / root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            os.chmod(destination, 0o600)
            if digest_file(destination) != digest or digest_file(path) != digest:
                raise ValueError(f"file changed during backup: {key}")
            entries[key] = {"sha256": digest, "stored_in": backup_id}
        manifest = {"id": backup_id, "kind": kind, "parent_id": parent_id,
                    "database_sha256": digest_file(snapshot), "files": entries}
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (folder / "manifest.json").write_bytes(raw)
        (folder / "manifest.sha256").write_text(sha256(raw).hexdigest(), encoding="ascii")
        os.chmod(folder / "manifest.json", 0o600)
        os.chmod(folder / "manifest.sha256", 0o600)
        return manifest
    except Exception:
        shutil.rmtree(folder)
        raise


def restore_backup(backup_root: Path, backup_id: str, destination: Path) -> dict:
    """Restore into a new empty directory; never overwrite a live catalog."""
    if destination.exists() or destination.resolve().is_relative_to(backup_root.resolve()):
        raise ValueError("destination must be new and outside backup root")
    manifest = _manifest(backup_root, backup_id)
    chain = {}
    current = manifest
    tail = None
    while current:
        if current["id"] in chain:
            raise ValueError("backup parent cycle")
        if (current["kind"] == "full") != (current["parent_id"] is None):
            raise ValueError("invalid backup chain type")
        chain[current["id"]] = current
        tail = current
        current = _manifest(backup_root, current["parent_id"]) if current["parent_id"] else None
    if tail is None or tail["kind"] != "full" or tail["parent_id"] is not None:
        raise ValueError("backup chain lacks full base")
    snapshot = backup_root / backup_id / "catalog.sqlite3"
    if digest_file(snapshot) != manifest["database_sha256"] or _files(snapshot) != {
        key: value["sha256"] for key, value in manifest["files"].items()
    }:
        raise ValueError("catalog or manifest mismatch")
    # Verify every referenced blob before installing either metadata or files.
    for key, entry in manifest["files"].items():
        root, relative = key.split("/", 1)
        if root not in {"source", "archive"} or entry["stored_in"] not in chain:
            raise ValueError("invalid backup file reference")
        blob = managed_path(backup_root / entry["stored_in"] / "files" / root, relative)
        if not blob.is_file() or digest_file(blob) != entry["sha256"]:
            raise ValueError(f"backup blob checksum mismatch: {key}")
    stage = destination.with_name(destination.name + ".staging-" + uuid4().hex)
    try:
        (stage / "source").mkdir(parents=True)
        (stage / "archive").mkdir()
        shutil.copy2(snapshot, stage / "catalog.sqlite3")
        for key, entry in manifest["files"].items():
            root, relative = key.split("/", 1)
            blob = managed_path(backup_root / entry["stored_in"] / "files" / root, relative)
            output = managed_path(stage / root, relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob, output)
            if digest_file(output) != entry["sha256"]:
                raise ValueError("restored blob checksum mismatch")
        os.replace(stage, destination)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return {"backup_id": backup_id, "destination": str(destination),
            "restored_file_count": len(manifest["files"]), "status": "restored_isolated"}
