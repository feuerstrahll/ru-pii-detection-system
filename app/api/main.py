"""Run the authenticated catalog and PII review service with `python -m app.api.main`."""

import argparse
from base64 import b64encode
import dbm
import json
import os
import re
import sqlite3
from io import BytesIO
from collections import Counter
from datetime import datetime, timezone
from getpass import getpass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4
from zlib import compressobj

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import load_config, load_ocr_config
from app.core.parsers import ParserRegistry
from app.core.pipeline import DeterministicPipeline, HybridPipeline
from app.ingestion.docx_adapter import DocxDocumentParser, preview_blocks
from app.ingestion.ocr_adapter import OCRDocumentParser
from app.ingestion.txt_adapter import TextDocumentParser
from app.detection.semantic import GLiNER2SemanticAdapter, ModelUnavailableError, snapshot_digest
from app.api.ui import dashboard, registration_page
from app.storage.catalog import (
    AuditEvent, InventorySnapshot, SourceDirectory, SourceObject, User, ensure_schema, hash_password, initialize_database, utc_now,
    verify_password,
)
from app.storage.ecology import report as ecology_report
from app.storage.lifecycle import archive as archive_file, delete_archived
from app.storage.backup import create_backup, list_backups, restore_backup


ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = DocxDocumentParser.supported_suffixes | OCRDocumentParser.supported_suffixes | TextDocumentParser.supported_suffixes
MAX_UPLOAD = 10 * 1024 * 1024
security = HTTPBasic(auto_error=False)


class ScanRequest(BaseModel):
    relative_directory: str = "."


class Registration(BaseModel):
    login: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class RoleChange(BaseModel):
    role: str = Field(pattern=r"^(admin|operator|auditor)$")


class SourceRegistration(BaseModel):
    relative_path: str = "."


class ObjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    file_type: str = Field(min_length=1, max_length=30, pattern=r"^[a-zA-Z0-9]+$")
    category: str = Field(min_length=1, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)
    owner: str | None = Field(default=None, max_length=255)
    created_at: datetime | None = None
    modified_at: datetime | None = None
    accessed_at: datetime | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ObjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    file_type: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[a-zA-Z0-9]+$")
    category: str | None = Field(default=None, min_length=1, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)
    owner: str | None = Field(default=None, max_length=255)
    created_at: datetime | None = None
    modified_at: datetime | None = None
    accessed_at: datetime | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    processing_status: str | None = Field(default=None, pattern=r"^(not_processed|completed|partial|failed)$")


class DeleteConfirmation(BaseModel):
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str


class BackupRequest(BaseModel):
    kind: str = Field(pattern=r"^(full|incremental)$")
    parent_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def object_view(obj: SourceObject) -> dict:
    return dict(id=obj.id, relative_path=obj.relative_path, name=obj.name, file_type=obj.file_type,
                size_bytes=obj.size_bytes, owner=obj.owner,
                created_at=obj.created_at.isoformat() if obj.created_at else None,
                modified_at=obj.modified_at.isoformat() if obj.modified_at else None,
                accessed_at=obj.accessed_at.isoformat() if obj.accessed_at else None,
                sha256=obj.sha256, category=obj.category, processing_status=obj.processing_status,
                origin=obj.origin, record_owner_id=obj.record_owner_id, availability=obj.availability,
                compressed_bytes=obj.compressed_bytes, department=obj.department, copy_kind=obj.copy_kind,
                verification_status=obj.verification_status, anomaly_status=obj.anomaly_status,
                pii_counts=json.loads(obj.pii_counts_json) if obj.pii_counts_json else None,
                archived_path=obj.archived_path)


def file_owner(path: Path) -> str | None:
    if os.name == "nt":
        try:
            import win32security

            descriptor = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
            sid = descriptor.GetSecurityDescriptorOwner()
            name, domain, _ = win32security.LookupAccountSid(None, sid)
            return f"{domain}\\{name}" if domain else name
        except (ImportError, OSError):
            return None
    try:
        return path.owner()
    except (OSError, NotImplementedError):
        return None


def _person_pipeline() -> HybridPipeline:
    frozen = json.loads((ROOT / "configs" / "semantic_person_frozen_v2.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "models.lock").read_text(encoding="utf-8"))
    model = next(item for item in lock["candidates"] if item["id"] == frozen["candidate_id"])
    if (model["model_name"] != frozen["model_ref"] or model["revision"] != frozen["revision"]
            or model["tokenizer_revision"] != frozen["revision"]):
        raise ModelUnavailableError("frozen PERSON model and tokenizer revisions do not match models.lock")
    path = ROOT / model["local_snapshot"]
    if snapshot_digest(path) != frozen["snapshot_sha256"]:
        raise ModelUnavailableError("local PERSON snapshot hash differs from the completed experiment")
    adapter = GLiNER2SemanticAdapter(frozen["candidate_id"], path, frozen["threshold"])
    return HybridPipeline(load_config(ROOT / "configs" / "deterministic_v3.json"), frozen, adapter)


def _entity_view(entity, segment) -> dict:
    source = next((e.detector_id for e in entity.evidence if e.signal.value == "ner"), "deterministic")
    box = None
    words = segment.source_locator.get("words", [])
    aligned = [word for word in words if word["start"] < entity.span.end and entity.span.start < word["end"]]
    covered = {position for word in aligned
               for position in range(max(word["start"], entity.span.start), min(word["end"], entity.span.end))}
    boxes = [word["bounding_box"] for word in aligned]
    coordinate_system = None
    if boxes and all(position in covered or segment.original_text[position].isspace()
                     for position in range(entity.span.start, entity.span.end)):
        box = [min(item[0] for item in boxes), min(item[1] for item in boxes),
               max(item[2] for item in boxes), max(item[3] for item in boxes)]
        pdf = str(segment.source_locator.get("source", "")).casefold().endswith(".pdf")
        if pdf and segment.source_locator.get("extraction") == "tesseract":
            box = [value * 72 / segment.source_locator["ocr_dpi"] for value in box]
        coordinate_system = "pdf_points" if pdf else "image_pixels"
    return {"label": entity.entity_type.value, "text": entity.span.text,
            "start": entity.span.start, "end": entity.span.end, "source": source,
            "score": entity.confidence, "decision": entity.decision,
            "segment_id": segment.segment_id, "page_number": segment.page_number,
            "bounding_box": box, "coordinate_system": coordinate_system}


def create_app(database_url: str, source_root: Path, cache_path: Path) -> FastAPI:
    source_root = source_root.resolve()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})
    ensure_schema(engine)
    app = FastAPI(title="Экология данных: каталог и обнаружение ПДн")
    cache_lock = Lock()
    semantic_lock = Lock()
    semantic_pipeline: HybridPipeline | None = None

    def current_user(credentials: HTTPBasicCredentials | None = Depends(security)) -> User:
        if credentials is None:
            raise HTTPException(401, "Требуется вход", headers={"WWW-Authenticate": "Basic"})
        with Session(engine) as session:
            user = session.scalar(select(User).where(User.login == credentials.username))
            if user is None or not verify_password(credentials.password, user.password_hash):
                raise HTTPException(401, "Неверные учетные данные", headers={"WWW-Authenticate": "Basic"})
            if user.role not in {"admin", "operator", "auditor"}:
                raise HTTPException(403, "Неизвестная роль пользователя")
            session.expunge(user)
            return user

    def require_editor(user: User = Depends(current_user)) -> User:
        if user.role not in {"admin", "operator"}:
            raise HTTPException(403, "Недостаточно прав")
        return user

    def require_admin(user: User = Depends(current_user)) -> User:
        if user.role != "admin":
            raise HTTPException(403, "Требуются права администратора")
        return user

    @app.post("/api/register", status_code=201)
    def register(request: Registration) -> dict:
        with Session(engine) as session:
            user = User(login=request.login, password_hash=hash_password(request.password), role="operator")
            session.add(user)
            try:
                session.flush()
                session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="register", detail=request.login))
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(409, "Логин уже занят") from error
            return {"id": user.id, "login": user.login, "role": user.role}

    @app.get("/api/users")
    def users(admin: User = Depends(require_admin)) -> list[dict]:
        with Session(engine) as session:
            return [dict(id=user.id, login=user.login, role=user.role)
                    for user in session.scalars(select(User).order_by(User.id))]

    @app.patch("/api/users/{user_id}/role")
    def change_role(user_id: int, request: RoleChange, admin: User = Depends(require_admin)) -> dict:
        with Session(engine) as session:
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(404, "Пользователь не найден")
            if user.id == admin.id and request.role != "admin":
                raise HTTPException(400, "Нельзя снять собственные права администратора")
            previous = user.role
            user.role = request.role
            session.add(AuditEvent(user_id=admin.id, occurred_at=utc_now(), action="role_change",
                                   detail=f"{user.login}: {previous} -> {request.role}"))
            session.commit()
            return {"id": user.id, "login": user.login, "role": user.role}

    @app.get("/api/audit")
    def audit(user: User = Depends(current_user)) -> list[dict]:
        if user.role not in {"admin", "auditor"}:
            raise HTTPException(403, "Недостаточно прав")
        with Session(engine) as session:
            events = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(500)).all()
            return [dict(id=event.id, user_id=event.user_id, occurred_at=event.occurred_at.isoformat(),
                         action=event.action, object_id=event.object_id, detail=event.detail) for event in events]

    @app.post("/api/sources", status_code=201)
    def register_source(request: SourceRegistration, user: User = Depends(require_editor)) -> dict:
        target = (source_root / request.relative_path).resolve()
        if not target.is_relative_to(source_root):
            raise HTTPException(400, "Каталог вне разрешенного источника")
        if not target.is_dir():
            raise HTTPException(404, "Каталог источника недоступен")
        relative = str(target.relative_to(source_root)) or "."
        with Session(engine) as session:
            source = SourceDirectory(relative_path=relative, registered_by=user.id, registered_at=utc_now())
            session.add(source)
            try:
                session.flush()
                session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="source_register",
                                       detail=relative))
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(409, "Источник уже зарегистрирован") from error
            return {"id": source.id, "relative_path": source.relative_path}

    @app.get("/api/sources")
    def list_sources(user: User = Depends(current_user)) -> list[dict]:
        with Session(engine) as session:
            query = select(SourceDirectory).order_by(SourceDirectory.id)
            if user.role == "operator":
                query = query.where(SourceDirectory.registered_by == user.id)
            return [dict(id=source.id, relative_path=source.relative_path, registered_by=source.registered_by)
                    for source in session.scalars(query)]

    @app.get("/", response_class=HTMLResponse)
    def index(user: User = Depends(current_user)) -> str:
        return dashboard(user.login, user.role)

    @app.get("/register", response_class=HTMLResponse)
    def registration_form() -> str:
        return registration_page()

    @app.get("/api/objects")
    def list_objects(q: str | None = None, file_type: str | None = None, category: str | None = None,
                     min_size: int | None = None, max_size: int | None = None,
                     accessed_before: datetime | None = None, sort_by: str = "name", descending: bool = False,
                     user: User = Depends(current_user)) -> list[dict]:
        sort_fields = {"name": SourceObject.name, "size": SourceObject.size_bytes,
                       "modified": SourceObject.modified_at, "accessed": SourceObject.accessed_at,
                       "category": SourceObject.category}
        if sort_by not in sort_fields:
            raise HTTPException(400, "Недопустимое поле сортировки")
        if (min_size is not None and min_size < 0) or (max_size is not None and max_size < 0):
            raise HTTPException(400, "Размер не может быть отрицательным")
        with Session(engine) as session:
            query = select(SourceObject)
            if user.role == "operator":
                query = query.where(SourceObject.record_owner_id == user.id)
            if q:
                query = query.where(SourceObject.name.ilike(f"%{q}%"))
            if file_type:
                query = query.where(SourceObject.file_type == file_type.lower().lstrip("."))
            if category:
                query = query.where(SourceObject.category == category)
            if min_size is not None:
                query = query.where(SourceObject.size_bytes >= min_size)
            if max_size is not None:
                query = query.where(SourceObject.size_bytes <= max_size)
            if accessed_before is not None:
                query = query.where(SourceObject.accessed_at < accessed_before)
            column = sort_fields[sort_by]
            query = query.order_by(column.desc() if descending else column.asc(), SourceObject.id)
            return [object_view(obj) for obj in session.scalars(query)]

    @app.get("/api/ecology")
    def ecology(user: User = Depends(current_user)) -> dict:
        policy = json.loads((ROOT / "configs" / "ecology_v1.json").read_text(encoding="utf-8"))
        with Session(engine) as session:
            query = select(SourceObject)
            if user.role == "operator":
                query = query.where(SourceObject.record_owner_id == user.id)
            objects = session.scalars(query).all()
            snapshots = session.scalars(select(InventorySnapshot)).all() if user.role != "operator" else []
            object_ids = {obj.id for obj in objects}
            operations = [event for event in session.scalars(select(AuditEvent).where(
                AuditEvent.action.in_(["file_archive", "file_delete_pending", "file_delete"])).order_by(AuditEvent.id))
                if event.object_id in object_ids]
            return ecology_report(objects, snapshots, policy, utc_now(), operations)

    @app.post("/api/objects/{object_id}/archive")
    def archive_object(object_id: int, admin: User = Depends(require_admin)) -> dict:
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None:
                raise HTTPException(404, "Object not found")
            try:
                return archive_file(session, obj, source_root, cache_path.parent / "archive", admin.id)
            except (ValueError, OSError) as error:
                raise HTTPException(409, str(error)) from error

    @app.post("/api/objects/{object_id}/delete-file")
    def delete_file(object_id: int, request: DeleteConfirmation,
                    admin: User = Depends(require_admin)) -> dict:
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None:
                raise HTTPException(404, "Object not found")
            try:
                return delete_archived(session, obj, cache_path.parent / "archive", admin.id,
                                       request.expected_sha256, request.confirmation)
            except (ValueError, OSError) as error:
                raise HTTPException(409, str(error)) from error

    @app.post("/api/backups")
    def backup(request: BackupRequest, admin: User = Depends(require_admin)) -> dict:
        database = engine.url.database
        if engine.dialect.name != "sqlite" or not database or database == ":memory:":
            raise HTTPException(400, "Backup requires an on-disk SQLite catalog")
        try:
            result = create_backup(Path(database), source_root, cache_path.parent / "archive",
                                   cache_path.parent / "backups", request.kind, request.parent_id)
        except (ValueError, OSError, sqlite3.Error) as error:
            with Session(engine) as session:
                session.add(AuditEvent(user_id=admin.id, occurred_at=utc_now(), action="backup_failed",
                                       detail=request.kind))
                session.commit()
            raise HTTPException(409, str(error)) from error
        with Session(engine) as session:
            session.add(AuditEvent(user_id=admin.id, occurred_at=utc_now(), action="backup_create",
                                   detail=f"{request.kind}:{result['id']}"))
            session.commit()
        return result

    @app.get("/api/backups")
    def backups(admin: User = Depends(require_admin)) -> list[dict]:
        return list_backups(cache_path.parent / "backups")

    @app.post("/api/backups/{backup_id}/restore")
    def restore(backup_id: str, admin: User = Depends(require_admin)) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", backup_id):
            raise HTTPException(400, "Invalid backup id")
        destination = cache_path.parent / "restores" / backup_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = restore_backup(cache_path.parent / "backups", backup_id, destination)
        except (ValueError, OSError, KeyError, sqlite3.Error) as error:
            with Session(engine) as session:
                session.add(AuditEvent(user_id=admin.id, occurred_at=utc_now(), action="restore_failed",
                                       detail=backup_id))
                session.commit()
            raise HTTPException(409, str(error)) from error
        with Session(engine) as session:
            session.add(AuditEvent(user_id=admin.id, occurred_at=utc_now(), action="backup_restore",
                                   detail=backup_id))
            session.commit()
        return result

    @app.post("/api/objects", status_code=201)
    def create_object(request: ObjectCreate, user: User = Depends(require_editor)) -> dict:
        with Session(engine) as session:
            obj = SourceObject(relative_path=f"manual:{uuid4().hex}", name=request.name,
                               file_type=request.file_type.lower().lstrip("."), size_bytes=request.size_bytes,
                               owner=request.owner, created_at=request.created_at, modified_at=request.modified_at,
                               accessed_at=request.accessed_at, sha256=request.sha256, category=request.category,
                               processing_status="not_processed", origin="manual", availability="metadata_only",
                               record_owner_id=user.id,
                               inventoried_at=utc_now())
            session.add(obj)
            session.flush()
            session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="record_create",
                                   object_id=obj.id, detail=obj.relative_path))
            session.commit()
            return object_view(obj)

    @app.get("/api/objects/{object_id}")
    def get_object(object_id: int, user: User = Depends(current_user)) -> dict:
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None or (user.role == "operator" and obj.record_owner_id != user.id):
                raise HTTPException(404, "Запись не найдена")
            return object_view(obj)

    @app.patch("/api/objects/{object_id}")
    def edit_object(object_id: int, request: ObjectUpdate, user: User = Depends(require_editor)) -> dict:
        changes = request.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(400, "Нет изменений")
        if any(changes.get(field) is None for field in ("name", "file_type", "category", "processing_status")
               if field in changes):
            raise HTTPException(400, "Обязательное поле не может быть пустым")
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None or (user.role == "operator" and obj.record_owner_id != user.id):
                raise HTTPException(404, "Запись не найдена")
            for field, value in changes.items():
                setattr(obj, field, value.lower() if field == "file_type" else value)
            session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="record_update",
                                   object_id=obj.id, detail=",".join(sorted(changes))))
            session.commit()
            return object_view(obj)

    @app.delete("/api/objects/{object_id}")
    def delete_object(object_id: int, user: User = Depends(require_editor)) -> dict:
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None or (user.role == "operator" and obj.record_owner_id != user.id):
                raise HTTPException(404, "Запись не найдена")
            if obj.availability in {"archived", "deletion_pending"}:
                raise HTTPException(409, "Archived file must be handled through lifecycle operations")
            identifier = obj.relative_path
            session.execute(update(AuditEvent).where(AuditEvent.object_id == obj.id).values(object_id=None))
            session.delete(obj)
            session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="record_delete",
                                   detail=identifier))
            session.commit()
            return {"deleted_record_id": object_id, "source_file_removed": False}

    def scan_directory(relative_directory: str, user: User) -> dict:
        target = (source_root / relative_directory).resolve()
        if not target.is_relative_to(source_root):
            raise HTTPException(400, "Каталог вне разрешенного источника")
        if not target.is_dir():
            raise HTTPException(404, "Каталог источника недоступен")
        count = 0
        seen: set[str] = set()
        with Session(engine) as session:
            for path in sorted(target.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                    digest = sha256()
                    compressor = compressobj()
                    compressed_bytes = 0
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            compressed_bytes += len(compressor.compress(chunk))
                    compressed_bytes += len(compressor.flush())
                    relative = str(path.relative_to(source_root))
                    seen.add(relative)
                    obj = session.scalar(select(SourceObject).where(SourceObject.relative_path == relative))
                    if obj is not None and obj.availability in {"archived", "deletion_pending", "deleted"}:
                        raise HTTPException(409, f"Previously managed path reused: {relative}")
                    action = "inventory_update" if obj else "inventory_create"
                    if obj is None:
                        obj = SourceObject(relative_path=relative)
                        session.add(obj)
                    owner = file_owner(path)
                    obj.name = path.name
                    obj.file_type = path.suffix.casefold().lstrip(".")
                    obj.size_bytes = stat.st_size
                    obj.compressed_bytes = compressed_bytes
                    if owner is not None or obj.owner is None:
                        obj.owner = owner
                    birth_time = stat.st_ctime if os.name == "nt" else getattr(stat, "st_birthtime", None)
                    obj.created_at = datetime.fromtimestamp(birth_time, timezone.utc) if birth_time is not None else None
                    obj.modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                    obj.accessed_at = datetime.fromtimestamp(stat.st_atime, timezone.utc)
                    new_digest = digest.hexdigest()
                    if obj.sha256 != new_digest:
                        obj.processing_status = "not_processed"
                    obj.sha256 = new_digest
                    obj.inventoried_at = utc_now()
                    obj.category = obj.category or "unclassified"
                    obj.record_owner_id = obj.record_owner_id or user.id
                    obj.origin = "directory"
                    obj.availability = "present"
                    session.flush()
                    session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action=action, object_id=obj.id,
                                           detail=relative))
                    count += 1
                except OSError as error:
                    session.rollback()
                    raise HTTPException(503, f"Не удалось прочитать источник: {path.name}") from error
            objects = session.scalars(select(SourceObject).where(SourceObject.origin == "directory")).all()
            for obj in objects:
                location = (source_root / obj.relative_path).resolve()
                if location.is_relative_to(target) and obj.relative_path not in seen and obj.availability == "present":
                    obj.availability = "missing"
                    session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="inventory_missing",
                                           object_id=obj.id, detail=obj.relative_path))
            present = [obj for obj in objects if obj.availability == "present"]
            session.add(InventorySnapshot(scanned_at=utc_now(), scanned_by=user.id, scope=relative_directory,
                                          object_count=len(present),
                                          total_bytes=sum(obj.size_bytes or 0 for obj in present)))
            session.commit()
        return {"inventoried": count}

    @app.post("/api/sources/scan")
    def scan(request: ScanRequest, admin: User = Depends(require_admin)) -> dict:
        return scan_directory(request.relative_directory, admin)

    @app.post("/api/sources/{source_id}/scan")
    def scan_registered(source_id: int, user: User = Depends(require_editor)) -> dict:
        with Session(engine) as session:
            source = session.get(SourceDirectory, source_id)
            if source is None or (user.role == "operator" and source.registered_by != user.id):
                raise HTTPException(404, "Источник не найден")
            return scan_directory(source.relative_path, user)

    @app.post("/api/analyze")
    async def analyze(document: UploadFile = File(...), user: User = Depends(require_editor)) -> dict:
        nonlocal semantic_pipeline
        name = Path(document.filename or "").name
        suffix = Path(name).suffix.casefold()
        if suffix not in SUPPORTED:
            raise HTTPException(400, "Поддерживаются DOCX, PDF, TXT и изображения")
        content = await document.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            raise HTTPException(400, "Файл пуст или превышает 10 МБ")
        digest = sha256(content).hexdigest()
        mode = "hybrid"
        model_error = None
        if semantic_pipeline is None:
            with semantic_lock:
                if semantic_pipeline is None:
                    try:
                        semantic_pipeline = _person_pipeline()
                    except (ImportError, OSError, RuntimeError, MemoryError) as error:
                        mode = "deterministic_only"
                        model_error = str(error)
        pipeline = semantic_pipeline if semantic_pipeline is not None else DeterministicPipeline(
            load_config(ROOT / "configs" / "deterministic_v3.json"))
        with TemporaryDirectory(prefix="pii-system-") as directory:
            source = Path(directory) / f"upload{suffix}"
            source.write_bytes(content)
            registry = ParserRegistry()
            registry.register(DocxDocumentParser())
            registry.register(OCRDocumentParser(load_ocr_config(ROOT / "configs" / "ocr_v1.json")))
            registry.register(TextDocumentParser())
            try:
                parsed = registry.parse(digest[:16], source)
                result = pipeline.detect_parsed(parsed)
            except (ValueError, OSError) as error:
                raise HTTPException(422, f"Не удалось обработать документ: {error}") from error
        segments = {segment.segment_id: segment for segment in parsed.segments}
        entities = [_entity_view(entity, segments[entity.span.segment_id]) for entity in result.entities
                    if entity.decision in {"accept", "review"}]
        counts = Counter(entity["label"] for entity in entities)
        summary = {"document_sha256": digest, "status": result.processing_status.value,
                   "config_version": result.config_version, "findings_by_type": dict(counts),
                   "candidate_count": result.candidate_count}
        if result.processing_status.value == "completed":
            with cache_lock:
                with dbm.open(str(cache_path), "c") as cache:
                    cache[f"{result.config_version}:{digest}".encode("ascii")] = json.dumps(summary).encode("utf-8")
        with Session(engine) as session:
            session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="analyze", detail=digest))
            session.commit()
        return {**summary, "mode": mode, "model_error": model_error, "errors": result.errors,
                "segments": [{"segment_id": segment.segment_id, "text": segment.original_text,
                              "page_number": segment.page_number,
                              "extraction": segment.source_locator.get("extraction", "docx_text"),
                              "ocr_confidence": segment.ocr_confidence,
                              "source_locator": {key: value for key, value in segment.source_locator.items()
                                                 if key not in {"source", "words"}}}
                             for segment in parsed.segments],
                "entities": entities, "cached": False}

    @app.post("/api/preview-page")
    async def preview_page(document: UploadFile = File(...), page_number: int = Form(...),
                           user: User = Depends(require_editor)) -> dict:
        suffix = Path(document.filename or "").suffix.casefold()
        if suffix not in OCRDocumentParser.supported_suffixes | {".docx"} or page_number < 1:
            raise HTTPException(400, "Укажите DOCX/PDF/изображение и номер страницы от 1")
        content = await document.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            raise HTTPException(400, "Файл пуст или превышает 10 МБ")
        if suffix == ".docx":
            if page_number != 1:
                raise HTTPException(400, "Страница вне документа")
            try:
                return {"kind": "docx", "blocks": preview_blocks(content), "page_count": 1}
            except (ValueError, OSError, KeyError) as error:
                raise HTTPException(422, "Не удалось показать DOCX") from error
        try:
            if suffix == ".pdf":
                import fitz

                with fitz.open(stream=content, filetype="pdf") as source:
                    page_count = len(source)
                    if page_number > page_count:
                        raise HTTPException(400, "Страница вне документа")
                    page = source[page_number - 1]
                    width, height = page.rect.width, page.rect.height
                    scale = min(1.6, 1800 / width, 2400 / height)
                    image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("jpeg")
            else:
                from PIL import Image

                with Image.open(BytesIO(content)) as source:
                    page_count = getattr(source, "n_frames", 1)
                    if page_number > page_count:
                        raise HTTPException(400, "Страница вне документа")
                    source.seek(page_number - 1)
                    width, height = source.size
                    frame = source.convert("RGB")
                    frame.thumbnail((1800, 2400))
                    output = BytesIO()
                    frame.save(output, format="JPEG", quality=85)
                    image = output.getvalue()
        except (OSError, RuntimeError, ValueError, ZeroDivisionError) as error:
            raise HTTPException(422, "Не удалось показать страницу документа") from error
        return {"image": f"data:image/jpeg;base64,{b64encode(image).decode('ascii')}",
                "width": width, "height": height, "page_count": page_count}

    @app.post("/api/objects/{object_id}/analyze")
    async def analyze_catalog_object(object_id: int, user: User = Depends(require_editor)) -> dict:
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is None or (user.role == "operator" and obj.record_owner_id != user.id):
                raise HTTPException(404, "Объект не найден")
            if obj.origin != "directory":
                raise HTTPException(400, "У записи нет доступного исходного файла")
            relative_path, expected_hash = obj.relative_path, obj.sha256
        source = (source_root / relative_path).resolve()
        if not source.is_relative_to(source_root):
            raise HTTPException(400, "Файл вне разрешенного источника")
        try:
            with source.open("rb") as handle:
                content = handle.read(MAX_UPLOAD + 1)
        except OSError as error:
            raise HTTPException(409, "Исходный файл недоступен; пересканируйте каталог") from error
        if len(content) > MAX_UPLOAD:
            raise HTTPException(400, "Файл превышает 10 МБ")
        if sha256(content).hexdigest() != expected_hash:
            raise HTTPException(409, "Файл изменился после инвентаризации; пересканируйте каталог")
        upload = UploadFile(file=BytesIO(content), filename=source.name)
        try:
            result = await analyze(upload, user)
        finally:
            await upload.close()
        with Session(engine) as session:
            obj = session.get(SourceObject, object_id)
            if obj is not None:
                obj.processing_status = result["status"]
                obj.pii_counts_json = json.dumps(dict(Counter(entity["label"] for entity in result["entities"]))) if result["status"] == "completed" else None
                session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="source_analyze",
                                       object_id=object_id, detail=result["document_sha256"]))
                session.commit()
        return {**result, "object_id": object_id}

    @app.post("/api/documents", status_code=201)
    async def upload_document(category: str = Form(...), document: UploadFile = File(...),
                              user: User = Depends(require_editor)) -> dict:
        category = category.strip()
        if not 1 <= len(category) <= 100:
            raise HTTPException(400, "Укажите категорию длиной до 100 символов")
        content = await document.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            raise HTTPException(400, "Файл пуст или превышает 10 МБ")
        await document.seek(0)
        summary = await analyze(document, user)
        with Session(engine) as session:
            obj = SourceObject(relative_path=f"upload:{uuid4().hex}",
                               name=Path(document.filename or "document").name,
                               file_type=Path(document.filename or "").suffix.casefold().lstrip("."),
                               size_bytes=len(content), owner=None, created_at=None, modified_at=None,
                               accessed_at=None, sha256=summary["document_sha256"], category=category,
                               processing_status=summary["status"], origin="upload", availability="metadata_only",
                               pii_counts_json=json.dumps(dict(Counter(entity["label"] for entity in summary["entities"]))) if summary["status"] == "completed" else None,
                               record_owner_id=user.id,
                               inventoried_at=utc_now())
            session.add(obj)
            session.flush()
            session.add(AuditEvent(user_id=user.id, occurred_at=utc_now(), action="document_upload",
                                   object_id=obj.id, detail=obj.relative_path))
            session.commit()
            return {"object": object_view(obj), "analysis": summary, "source_file_stored": False}

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Local data ecology service")
    parser.add_argument("command", choices=["init-db", "serve"])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    runtime = ROOT / "var"
    runtime.mkdir(exist_ok=True)
    database_url = os.getenv("PII_DATABASE_URL", f"sqlite:///{runtime / 'catalog.sqlite3'}")
    source_root = Path(os.getenv("PII_SOURCE_ROOT", str(ROOT / "data" / "source")))
    cache_path = runtime / "analysis_cache"
    if args.command == "init-db":
        login = input("Логин администратора: ").strip()
        password = getpass("Пароль администратора: ")
        initialize_database(database_url, login, password)
        print("База данных и учетная запись готовы")
    else:
        uvicorn.run(create_app(database_url, source_root, cache_path), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
