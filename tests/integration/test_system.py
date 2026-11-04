"""Verify the authenticated catalog and PII endpoint through HTTP."""

import os
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.main import create_app
from app.storage.catalog import (
    AuditEvent,
    InventorySnapshot,
    User,
    hash_password,
    initialize_database,
    verify_password,
)


@pytest.fixture
def no_semantic(monkeypatch):
    from app.api import main
    from app.detection.semantic import ModelUnavailableError

    def unavailable():
        raise ModelUnavailableError("snapshot unavailable")

    monkeypatch.setattr(main, "_person_pipeline", unavailable)


def test_catalog_and_detection_scenario(tmp_path, no_semantic):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "sample.docx"
    from docx import Document

    document = Document()
    document.add_paragraph("Почта: ivan@example.ru")
    document.save(file)
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    app = create_app(database_url, source, tmp_path / "cache")
    client = TestClient(app)

    assert client.get("/api/objects").status_code == 401
    assert client.post("/api/sources/scan", json={}).status_code == 401
    assert client.get("/api/objects", auth=("admin", "wrong")).status_code == 401

    auth = ("admin", "secret-password")
    assert client.get("/", auth=auth).status_code == 200
    assert client.post("/api/sources/scan", json={"relative_directory": ".."}, auth=auth).status_code == 400
    assert client.post("/api/sources/scan", json={}, auth=auth).json() == {"inventoried": 1}
    item = client.get("/api/objects", auth=auth).json()[0]
    assert item["name"] == "sample.docx"
    assert item["sha256"] == sha256(file.read_bytes()).hexdigest()
    assert item["size_bytes"] == file.stat().st_size
    assert item["owner"]
    ecology = client.get("/api/ecology", auth=auth)
    assert ecology.status_code == 200
    assert ecology.json()["volume"]["known_bytes"] == file.stat().st_size
    assert ecology.json()["performed_operations"] == []

    data = file.read_bytes()
    first = client.post("/api/analyze", auth=auth, files={"document": ("sample.docx", data)}).json()
    second = client.post("/api/analyze", auth=auth, files={"document": ("sample.docx", data)}).json()
    assert first["status"] == "completed"
    assert first["findings_by_type"].get("EMAIL") == 1
    assert first["cached"] is False and second["cached"] is False
    assert first["mode"] == "deterministic_only"
    assert first["model_error"]
    assert first["entities"][0]["label"] == "EMAIL"
    assert first["entities"][0]["text"] == "ivan@example.ru"
    assert first["segments"][0]["text"][first["entities"][0]["start"]:first["entities"][0]["end"]] == "ivan@example.ru"
    assert first["entities"] == second["entities"]
    with Session(create_engine(database_url)) as session:
        user = session.scalar(select(User))
        events = session.scalars(select(AuditEvent)).all()
        assert user is not None and user.password_hash != "secret-password"
        assert verify_password("secret-password", user.password_hash)
        assert [event.action for event in events] == ["inventory_create", "analyze", "analyze"]
        assert "ivan@example.ru" not in str(events)
    analyzed = client.post(f"/api/objects/{item['id']}/analyze", auth=auth)
    assert analyzed.status_code == 200
    assert client.get("/api/ecology", auth=auth).json()["quality_and_pii"]["detected_entities_by_class"]["EMAIL"] == 1


def test_archive_backup_restore_and_confirmed_delete(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_text("first", encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    client = TestClient(create_app(database_url, source, tmp_path / "runtime" / "cache"))
    auth = ("admin", "secret-password")
    assert client.post("/api/sources/scan", json={}, auth=auth).status_code == 200
    first = client.get("/api/objects", auth=auth).json()[0]
    full = client.post("/api/backups", json={"kind": "full"}, auth=auth)
    assert full.status_code == 200, full.text
    assert client.get("/api/backups", auth=auth).json()[0]["id"] == full.json()["id"]
    (source / "second.txt").write_text("second", encoding="utf-8")
    assert client.post("/api/sources/scan", json={}, auth=auth).status_code == 200
    archived = client.post(f"/api/objects/{first['id']}/archive", auth=auth)
    assert archived.status_code == 200, archived.text
    assert not (source / "first.txt").exists()
    incremental = client.post("/api/backups", json={"kind": "incremental", "parent_id": full.json()["id"]}, auth=auth)
    assert incremental.status_code == 200, incremental.text
    restored = client.post(f"/api/backups/{incremental.json()['id']}/restore", auth=auth)
    assert restored.status_code == 200, restored.text
    destination = tmp_path / "runtime" / "restores" / incremental.json()["id"]
    assert (destination / "source" / "second.txt").read_text(encoding="utf-8") == "second"
    assert (destination / "archive" / f"{first['id']}" / first["sha256"]).read_text(encoding="utf-8") == "first"
    with sqlite3.connect(destination / "catalog.sqlite3") as restored_db:
        assert restored_db.execute("SELECT availability FROM source_objects WHERE id=?", (first["id"],)).fetchone() == ("archived",)
    from app.storage.backup import restore_backup

    backup_root = tmp_path / "runtime" / "backups"
    blob = backup_root / incremental.json()["id"] / "files" / "source" / "second.txt"
    blob.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore_backup(backup_root, incremental.json()["id"], tmp_path / "damaged-restore")
    assert not (tmp_path / "damaged-restore").exists()
    bad = client.post(f"/api/objects/{first['id']}/delete-file", auth=auth,
                      json={"expected_sha256": first["sha256"], "confirmation": "yes"})
    assert bad.status_code == 409
    deleted = client.post(f"/api/objects/{first['id']}/delete-file", auth=auth,
                          json={"expected_sha256": first["sha256"],
                                "confirmation": f"DELETE {first['id']} {first['sha256']}"})
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/objects/{first['id']}", auth=auth).json()["availability"] == "deleted"
    assert not (tmp_path / "runtime" / "archive" / str(first["id"]) / first["sha256"]).exists()
    assert {item["action"] for item in client.get("/api/ecology", auth=auth).json()["performed_operations"]} == {
        "file_archive", "file_delete_pending", "file_delete"}


def test_roles_catalog_crud_and_upload(tmp_path, no_semantic):
    from docx import Document

    source = tmp_path / "source"
    subdir = source / "department"
    subdir.mkdir(parents=True)
    file = subdir / "record.docx"
    document = Document()
    document.add_paragraph("Контакт: team@example.ru")
    document.save(file)
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    client = TestClient(create_app(database_url, source, tmp_path / "cache"))
    admin = ("admin", "secret-password")

    assert client.get("/register").status_code == 200
    assert client.post("/api/register", json={"login": "worker", "password": "short"}).status_code == 422
    registration = client.post("/api/register", json={"login": "worker", "password": "long-password-1"})
    assert registration.status_code == 201 and registration.json()["role"] == "operator"
    assert client.post("/api/register", json={"login": "worker", "password": "long-password-1"}).status_code == 409
    worker = ("worker", "long-password-1")
    assert client.get("/api/users", auth=worker).status_code == 403
    assert client.patch(f"/api/users/{registration.json()['id']}/role", auth=worker,
                        json={"role": "admin"}).status_code == 403
    assert client.patch("/api/users/1/role", auth=admin, json={"role": "auditor"}).status_code == 400

    registered = client.post("/api/sources", auth=worker, json={"relative_path": "department"})
    assert registered.status_code == 201
    assert client.post("/api/sources", auth=worker, json={"relative_path": ".."}).status_code == 400
    source_id = registered.json()["id"]
    assert client.post(f"/api/sources/{source_id}/scan", auth=worker).json() == {"inventoried": 1}
    scanned = client.get("/api/objects", auth=worker).json()[0]
    assert scanned["sha256"] == sha256(file.read_bytes()).hexdigest()
    assert scanned["category"] == "unclassified" and scanned["processing_status"] == "not_processed"

    created = client.post("/api/objects", auth=worker,
                          json={"name": "manual.txt", "file_type": "txt", "category": "HR", "size_bytes": 20})
    assert created.status_code == 201
    manual = created.json()
    assert manual["created_at"] is None and manual["accessed_at"] is None
    assert manual["sha256"] is None and manual["origin"] == "manual"
    assert [x["name"] for x in client.get("/api/objects", auth=worker,
                                         params={"sort_by": "size", "descending": "true"}).json()] == [
                                             "record.docx", "manual.txt"]
    assert [x["name"] for x in client.get("/api/objects", auth=worker,
                                         params={"file_type": "txt", "min_size": 10}).json()] == ["manual.txt"]
    assert client.get("/api/objects", auth=worker, params={"sort_by": "invalid"}).status_code == 400
    edited = client.patch(f"/api/objects/{manual['id']}", auth=worker,
                          json={"name": "renamed.txt", "category": "Finance"})
    assert edited.json()["name"] == "renamed.txt" and edited.json()["category"] == "Finance"
    assert client.patch(f"/api/objects/{manual['id']}", auth=worker,
                        json={"name": None}).status_code == 400
    deleted = client.delete(f"/api/objects/{scanned['id']}", auth=worker)
    assert deleted.json()["source_file_removed"] is False and file.exists()
    assert client.get(f"/api/objects/{scanned['id']}", auth=worker).status_code == 404

    uploaded = client.post("/api/documents", auth=worker, data={"category": "HR"},
                           files={"document": ("record.docx", file.read_bytes())})
    assert uploaded.status_code == 201 and uploaded.json()["source_file_stored"] is False
    assert uploaded.json()["object"]["accessed_at"] is None
    assert uploaded.json()["analysis"]["findings_by_type"]["EMAIL"] == 1
    docx_preview = client.post("/api/preview-page", auth=worker, data={"page_number": 1},
                               files={"document": ("record.docx", file.read_bytes())})
    assert docx_preview.status_code == 200
    assert docx_preview.json()["kind"] == "docx"
    assert docx_preview.json()["blocks"][0]["paragraph"]["segment_id"] == "paragraph-1"

    changed = client.patch(f"/api/users/{registration.json()['id']}/role", auth=admin,
                           json={"role": "auditor"})
    assert changed.json()["role"] == "auditor"
    assert client.post("/api/objects", auth=worker,
                       json={"name": "x", "file_type": "txt", "category": "HR"}).status_code == 403
    assert client.post(f"/api/sources/{source_id}/scan", auth=worker).status_code == 403
    assert client.post("/api/documents", auth=worker, data={"category": "HR"},
                       files={"document": ("record.docx", file.read_bytes())}).status_code == 403
    audit = client.get("/api/audit", auth=worker)
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()}
    assert {"register", "source_register", "inventory_create", "record_create", "record_update",
            "record_delete", "document_upload", "role_change"} <= actions


def test_existing_sqlite_catalog_is_migrated_without_losing_rows(tmp_path):
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY, login VARCHAR(80) NOT NULL UNIQUE,
            password_hash VARCHAR(200) NOT NULL, role VARCHAR(30) NOT NULL)""")
        connection.execute("INSERT INTO users VALUES (5, 'admin', ?, 'admin')", (hash_password("secret-password"),))
        connection.execute("""CREATE TABLE source_objects (
            id INTEGER PRIMARY KEY, relative_path VARCHAR(1000) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL, file_type VARCHAR(30) NOT NULL,
            size_bytes INTEGER NOT NULL, owner VARCHAR(255), created_at DATETIME NOT NULL,
            modified_at DATETIME NOT NULL, accessed_at DATETIME NOT NULL,
            sha256 VARCHAR(64) NOT NULL, inventoried_at DATETIME NOT NULL)""")
        connection.execute("""INSERT INTO source_objects VALUES
            (7, 'old.docx', 'old.docx', 'docx', 42, NULL,
             '2026-01-01', '2026-01-02', '2026-01-03', ?, '2026-01-04')""", ("a" * 64,))
        connection.execute("""CREATE TABLE audit_events (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            occurred_at DATETIME NOT NULL, action VARCHAR(60) NOT NULL, object_id INTEGER,
            detail VARCHAR(1000) NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(object_id) REFERENCES source_objects(id))""")
        connection.execute("""INSERT INTO audit_events VALUES
            (9, 5, '2026-01-04', 'inventory_create', 7, 'old.docx')""")
    client = TestClient(create_app(f"sqlite:///{database}", tmp_path, tmp_path / "cache"))
    initialize_database(f"sqlite:///{database}", "admin", "secret-password")
    initialize_database(f"sqlite:///{database}", "another-admin", "another-secret-1")
    items = client.get("/api/objects", auth=("admin", "secret-password")).json()
    assert items[0]["id"] == 7 and items[0]["sha256"] == "a" * 64
    assert items[0]["origin"] == "directory"
    assert len(client.get("/api/users", auth=("admin", "secret-password")).json()) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT object_id FROM audit_events WHERE id=9").fetchone() == (7,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert database.with_name("old.sqlite3.pre-stage3.bak").exists()


def test_inventory_tracks_all_files_and_missing_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "log.txt"
    file.write_text("repeated " * 200, encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    client = TestClient(create_app(database_url, source, tmp_path / "cache"))
    auth = ("admin", "secret-password")

    assert client.post("/api/sources/scan", auth=auth, json={}).json() == {"inventoried": 1}
    item = client.get("/api/objects", auth=auth).json()[0]
    assert item["file_type"] == "txt" and item["availability"] == "present"
    assert item["compressed_bytes"] < item["size_bytes"]
    file.unlink()
    assert client.post("/api/sources/scan", auth=auth, json={}).json() == {"inventoried": 0}
    assert client.get("/api/objects", auth=auth).json()[0]["availability"] == "missing"
    with Session(create_engine(database_url)) as session:
        snapshots = session.scalars(select(InventorySnapshot).order_by(InventorySnapshot.id)).all()
        assert [(s.object_count, s.total_bytes) for s in snapshots] == [(1, item["size_bytes"]), (0, 0)]


def test_txt_analysis_returns_highlightable_spans(tmp_path, no_semantic):
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    source_root = tmp_path / "source"
    source_root.mkdir()
    client = TestClient(create_app(database_url, source_root, tmp_path / "cache"))
    auth = ("admin", "secret-password")
    text = "Почта: ivan@example.ru; телефон: +7 999 123-45-67"
    response = client.post("/api/analyze", auth=auth, files={"document": ("sample.txt", text.encode())})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "deterministic_only"
    assert {item["label"] for item in result["entities"]} == {"EMAIL", "PHONE"}
    for item in result["entities"]:
        assert result["segments"][0]["text"][item["start"]:item["end"]] == item["text"]
        assert item["source"] == "deterministic"
    html = client.get("/", auth=auth).text
    assert "renderAnalysis" in html and "Фрагмент / страница" in html

    source = source_root / "source.txt"
    source.write_text(text, encoding="utf-8")
    assert client.post("/api/sources/scan", auth=auth, json={}).status_code == 200
    object_id = client.get("/api/objects", auth=auth).json()[0]["id"]
    catalog_result = client.post(f"/api/objects/{object_id}/analyze", auth=auth)
    assert catalog_result.status_code == 200
    assert catalog_result.json()["object_id"] == object_id
    assert catalog_result.json()["entities"] == result["entities"]
    assert client.get("/api/objects", auth=auth).json()[0]["processing_status"] == "completed"
    source.write_text(text + " changed", encoding="utf-8")
    assert client.post(f"/api/objects/{object_id}/analyze", auth=auth).status_code == 409


def test_http_pdf_scan_clean_document_and_invalid_files(tmp_path, no_semantic):
    import fitz

    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    source = tmp_path / "source"
    source.mkdir()
    client = TestClient(create_app(database_url, source, tmp_path / "cache"))
    auth = ("admin", "secret-password")

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((50, 80), "Email: test@example.com", fontsize=20)
    page.set_rotation(90)
    pdf_bytes = pdf.tobytes()
    pdf.close()
    digital = client.post("/api/analyze", auth=auth, files={"document": ("synthetic.pdf", pdf_bytes)})
    assert digital.status_code == 200
    assert digital.json()["status"] == "completed"
    assert digital.json()["findings_by_type"]["EMAIL"] == 1
    assert digital.json()["entities"][0]["coordinate_system"] == "pdf_points"
    preview = client.post("/api/preview-page", auth=auth, data={"page_number": 1},
                          files={"document": ("synthetic.pdf", pdf_bytes)})
    assert preview.status_code == 200
    assert preview.json()["image"].startswith("data:image/jpeg;base64,")
    assert preview.json()["page_count"] == 1
    assert digital.json()["entities"][0]["bounding_box"][2] < preview.json()["width"]
    assert digital.json()["entities"][0]["bounding_box"][3] < preview.json()["height"]
    assert client.post("/api/preview-page", data={"page_number": 1},
                       files={"document": ("synthetic.pdf", pdf_bytes)}).status_code == 401
    assert client.post("/api/preview-page", auth=auth, data={"page_number": 2},
                       files={"document": ("synthetic.pdf", pdf_bytes)}).status_code == 400

    fixture = Path(__file__).parents[1] / "unit" / "fixtures" / "ocr" / "clean.png"
    scanned = client.post("/api/analyze", auth=auth,
                          files={"document": ("synthetic.png", fixture.read_bytes())})
    assert scanned.status_code == 200
    assert scanned.json()["status"] == "completed"
    assert scanned.json()["findings_by_type"]["EMAIL"] == 1
    assert scanned.json()["segments"][0]["extraction"] == "tesseract"
    assert scanned.json()["entities"][0]["coordinate_system"] == "image_pixels"
    image_preview = client.post("/api/preview-page", auth=auth, data={"page_number": 1},
                                files={"document": ("synthetic.png", fixture.read_bytes())})
    assert image_preview.status_code == 200
    assert scanned.json()["entities"][0]["bounding_box"][2] <= image_preview.json()["width"]

    clean = client.post("/api/analyze", auth=auth,
                        files={"document": ("clean.txt", b"This document contains no contact data.")})
    assert clean.status_code == 200
    assert clean.json()["status"] == "completed"
    assert clean.json()["findings_by_type"] == {} and clean.json()["entities"] == []

    assert client.post("/api/analyze", auth=auth,
                       files={"document": ("empty.txt", b"")}).status_code == 400
    assert client.post("/api/analyze", auth=auth,
                       files={"document": ("unsupported.bin", b"content")}).status_code == 400
    damaged = client.post("/api/analyze", auth=auth,
                          files={"document": ("damaged.pdf", b"not a pdf")})
    assert damaged.status_code == 200
    assert damaged.json()["status"] == "failed"
    assert damaged.json()["errors"][0]["stage"] == "source_open"


@pytest.mark.skipif(os.getenv("PII_LIVE_MODEL_TEST") != "1", reason="requires local PERSON snapshot and executable ML dependencies")
def test_live_person_and_deterministic_http_inference(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'catalog.sqlite3'}"
    initialize_database(database_url, "admin", "secret-password")
    source = tmp_path / "source"
    source.mkdir()
    client = TestClient(create_app(database_url, source, tmp_path / "cache"))
    response = client.post("/api/analyze", auth=("admin", "secret-password"),
                           files={"document": ("synthetic.txt", "Иван Петров, почта test@example.com".encode())})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "hybrid" and result["model_error"] is None
    assert {item["label"] for item in result["entities"]} == {"PERSON", "EMAIL"}
    assert all(result["segments"][0]["text"][item["start"]:item["end"]] == item["text"]
               for item in result["entities"])
