"""Persist users and source metadata without retaining document contents or PII values."""

from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from pathlib import Path
from secrets import token_bytes
import sqlite3

from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30), default="operator")


class SourceDirectory(Base):
    __tablename__ = "source_directories"

    id: Mapped[int] = mapped_column(primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(1000), unique=True)
    registered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceObject(Base):
    __tablename__ = "source_objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(1000), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    inventoried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(30), default="not_processed")
    record_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    origin: Mapped[str] = mapped_column(String(30), default="directory")
    availability: Mapped[str] = mapped_column(String(30), default="present")
    compressed_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    copy_kind: Mapped[str] = mapped_column(String(30), default="unknown")
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    anomaly_status: Mapped[str] = mapped_column(String(30), default="unassessed")
    pii_counts_json: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    archived_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scanned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(1000))
    object_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(60))
    object_id: Mapped[int | None] = mapped_column(ForeignKey("source_objects.id"), nullable=True)
    detail: Mapped[str] = mapped_column(String(1000), default="")


def hash_password(password: str, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("Пароль не может быть пустым")
    actual_salt = salt or token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, 600_000)
    return f"pbkdf2_sha256$600000${actual_salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        method, rounds, salt, expected = encoded.split("$")
        if method != "pbkdf2_sha256":
            return False
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def initialize_database(url: str, login: str, password: str) -> None:
    engine = create_engine(url)
    ensure_schema(engine)
    with Session(engine) as session:
        if session.query(User).first() is None:
            if not login.strip() or len(password) < 12:
                raise ValueError("Укажите логин и пароль администратора длиной не менее 12 символов")
            session.add(User(login=login, password_hash=hash_password(password), role="admin"))
            session.commit()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema(engine: Engine) -> None:
    """Create tables and upgrade the first SQLite catalog without losing its rows."""
    Base.metadata.create_all(engine)
    if engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        return
    database_path = Path(engine.url.database)
    with sqlite3.connect(database_path, isolation_level=None) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source_objects)")}
        if {"category", "processing_status", "record_owner_id", "origin"} <= columns:
            _ensure_inventory_columns(connection, columns)
            return
        backup_path = database_path.with_name(database_path.name + ".pre-stage3.bak")
        if not backup_path.exists():
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE source_objects_next (
                id INTEGER PRIMARY KEY, relative_path VARCHAR(1000) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL, file_type VARCHAR(30) NOT NULL,
                size_bytes INTEGER, owner VARCHAR(255), created_at DATETIME,
                modified_at DATETIME, accessed_at DATETIME, sha256 VARCHAR(64),
                inventoried_at DATETIME NOT NULL, category VARCHAR(100),
                processing_status VARCHAR(30) NOT NULL, record_owner_id INTEGER,
                origin VARCHAR(30) NOT NULL,
                FOREIGN KEY(record_owner_id) REFERENCES users(id))""")
            connection.execute("""INSERT INTO source_objects_next
                (id, relative_path, name, file_type, size_bytes, owner, created_at,
                 modified_at, accessed_at, sha256, inventoried_at, processing_status, origin)
                SELECT id, relative_path, name, file_type, size_bytes, owner, created_at,
                       modified_at, accessed_at, sha256, inventoried_at, 'not_processed', 'directory'
                FROM source_objects""")
            connection.execute("DROP TABLE source_objects")
            connection.execute("ALTER TABLE source_objects_next RENAME TO source_objects")
            connection.execute("CREATE INDEX ix_source_objects_sha256 ON source_objects (sha256)")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("Catalog migration failed foreign key check")
            connection.execute("COMMIT")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        _ensure_inventory_columns(connection, {row[1] for row in connection.execute("PRAGMA table_info(source_objects)")})


def _ensure_inventory_columns(connection: sqlite3.Connection, columns: set[str]) -> None:
    additions = {
        "availability": "VARCHAR(30) NOT NULL DEFAULT 'present'",
        "compressed_bytes": "INTEGER",
        "department": "VARCHAR(100)",
        "copy_kind": "VARCHAR(30) NOT NULL DEFAULT 'unknown'",
        "verification_status": "VARCHAR(30) NOT NULL DEFAULT 'unverified'",
        "anomaly_status": "VARCHAR(30) NOT NULL DEFAULT 'unassessed'",
        "pii_counts_json": "VARCHAR(1000)",
        "archived_path": "VARCHAR(1000)",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE source_objects ADD COLUMN {name} {declaration}")
    connection.execute("UPDATE source_objects SET availability='metadata_only' WHERE origin IN ('manual', 'upload')")
