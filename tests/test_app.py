"""
Tests for the Offline Survival Computer Flask application.
Run with:  pytest tests/
"""
import io
import json
import os
import sys
import tarfile

import pytest

# Ensure the app module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Redirect all data directories to a temporary path so tests are isolated."""
    import main as m

    data_dir = tmp_path / "data"
    notes_dir = data_dir / "notes"
    uploads_dir = data_dir / "uploads"
    docs_dir = data_dir / "documents"
    maps_dir = data_dir / "maps"
    for d in (notes_dir, uploads_dir, docs_dir, maps_dir):
        d.mkdir(parents=True)

    monkeypatch.setattr(m, "DATA_DIR", data_dir)
    monkeypatch.setattr(m, "NOTES_DB", notes_dir / "notes.db")
    monkeypatch.setattr(m, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(m, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(m, "MAPS_DIR", maps_dir)
    monkeypatch.setattr(m, "check_service", lambda *args, **kwargs: False)
    m.app.config["UPLOAD_FOLDER"] = str(uploads_dir)

    m.init_db()
    yield


@pytest.fixture()
def client(isolated_data_dirs):
    import main as m

    m.app.config["TESTING"] = True
    m.app.config["WTF_CSRF_ENABLED"] = False
    with m.app.test_client() as client:
        yield client


def login(client):
    import main as m

    return client.post(
        "/login",
        data={"username": m.DEFAULT_USERNAME, "password": m.DEFAULT_PASSWORD},
        follow_redirects=True,
    )


@pytest.fixture()
def logged_in_client(client):
    login(client)
    return client


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_page_returns_200(self, client):
        r = client.get("/login")
        assert r.status_code == 200

    def test_login_success_redirects_home(self, client):
        import main as m
        r = client.post(
            "/login",
            data={"username": m.DEFAULT_USERNAME, "password": m.DEFAULT_PASSWORD},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/")

    def test_login_failure_shows_error(self, client):
        r = client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Invalid username or password" in r.data

    def test_home_requires_login(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_logout_redirects_to_login(self, logged_in_client):
        r = logged_in_client.get("/logout")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]


# ---------------------------------------------------------------------------
# Command Center and content pages
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, logged_in_client):
        r = logged_in_client.get("/")
        assert r.status_code == 200
        assert b"Command Center" in r.data

    def test_contains_module_links(self, logged_in_client):
        r = logged_in_client.get("/")
        for text in (b"Library", b"AI Assistant", b"Education", b"Maps", b"Notes"):
            assert text in r.data


class TestLibrary:
    def test_returns_200(self, logged_in_client):
        r = logged_in_client.get("/library")
        assert r.status_code == 200
        assert b"Kiwix" in r.data


class TestEducation:
    def test_returns_200(self, logged_in_client):
        r = logged_in_client.get("/education")
        assert r.status_code == 200
        assert b"Khan Academy" in r.data


class TestMaps:
    def test_returns_200(self, logged_in_client):
        r = logged_in_client.get("/maps")
        assert r.status_code == 200
        assert b"leaflet" in r.data.lower()


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

class TestAIAssistant:
    def test_returns_200(self, logged_in_client):
        r = logged_in_client.get("/ai")
        assert r.status_code == 200

    def test_upload_valid_file(self, logged_in_client):
        data = {"file": (io.BytesIO(b"Hello world"), "test_doc.txt")}
        r = logged_in_client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"uploaded successfully" in r.data

    def test_upload_disallowed_extension(self, logged_in_client):
        data = {"file": (io.BytesIO(b"#!/bin/bash"), "evil.sh")}
        r = logged_in_client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Unsupported" in r.data

    def test_delete_nonexistent_document(self, logged_in_client):
        r = logged_in_client.delete("/ai/document/9999")
        assert r.status_code == 404

    def test_delete_existing_document(self, logged_in_client):
        data = {"file": (io.BytesIO(b"Some content"), "deleteme.txt")}
        logged_in_client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
        )
        import main as m

        conn = m.get_db()
        doc = conn.execute("SELECT id FROM documents ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert doc is not None

        r = logged_in_client.delete(f"/ai/document/{doc['id']}")
        assert r.status_code == 200
        assert json.loads(r.data)["status"] == "deleted"

    def test_models_endpoint_on_ollama_down(self, logged_in_client):
        r = logged_in_client.get("/ai/models")
        assert r.status_code in (502, 503)

    def test_chat_endpoint_on_ollama_down(self, logged_in_client):
        r = logged_in_client.post(
            "/ai/chat",
            data=json.dumps(
                {"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]}
            ),
            content_type="application/json",
        )
        assert r.status_code in (502, 503)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class TestNotes:
    def test_list_empty(self, logged_in_client):
        r = logged_in_client.get("/notes")
        assert r.status_code == 200
        assert b"No notes yet" in r.data

    def test_new_note_form(self, logged_in_client):
        r = logged_in_client.get("/notes/new")
        assert r.status_code == 200
        assert b"form" in r.data.lower()

    def test_create_and_view_note(self, logged_in_client):
        r = logged_in_client.post(
            "/notes/new",
            data={"title": "Test Note", "content": "Hello, survival!", "tags": "test"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Test Note" in r.data
        assert b"Hello, survival!" in r.data

    def test_create_note_missing_title(self, logged_in_client):
        r = logged_in_client.post(
            "/notes/new",
            data={"title": "", "content": "No title"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Title is required" in r.data

    def test_edit_note(self, logged_in_client):
        logged_in_client.post(
            "/notes/new",
            data={"title": "Edit Me", "content": "Original", "tags": ""},
            follow_redirects=True,
        )
        import main as m

        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = logged_in_client.post(
            f"/notes/{note['id']}/edit",
            data={"title": "Edited Title", "content": "Updated content", "tags": "edited"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Edited Title" in r.data

    def test_delete_note(self, logged_in_client):
        logged_in_client.post(
            "/notes/new",
            data={"title": "Delete Me", "content": "Bye", "tags": ""},
            follow_redirects=True,
        )
        import main as m

        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = logged_in_client.post(f"/notes/{note['id']}/delete", follow_redirects=True)
        assert r.status_code == 200
        assert b"deleted" in r.data.lower()

    def test_view_nonexistent_note(self, logged_in_client):
        r = logged_in_client.get("/notes/99999", follow_redirects=True)
        assert r.status_code == 200
        assert b"not found" in r.data.lower()

    def test_api_notes_empty(self, logged_in_client):
        r = logged_in_client.get("/api/notes")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_notes_with_data(self, logged_in_client):
        logged_in_client.post(
            "/notes/new",
            data={"title": "API Note", "content": "API content", "tags": "api"},
            follow_redirects=True,
        )
        r = logged_in_client.get("/api/notes")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert len(data) == 1
        assert data[0]["title"] == "API Note"

    def test_api_single_note(self, logged_in_client):
        logged_in_client.post(
            "/notes/new",
            data={"title": "Single", "content": "Solo", "tags": ""},
            follow_redirects=True,
        )
        import main as m

        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = logged_in_client.get(f"/api/notes/{note['id']}")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["title"] == "Single"

    def test_api_note_not_found(self, logged_in_client):
        r = logged_in_client.get("/api/notes/99999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# File Manager
# ---------------------------------------------------------------------------

class TestFileManager:
    def test_files_page(self, logged_in_client):
        r = logged_in_client.get("/files")
        assert r.status_code == 200

    def test_upload_and_download(self, logged_in_client):
        data = {"file": (io.BytesIO(b"sample"), "file.txt")}
        r = logged_in_client.post(
            "/files/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert r.status_code == 302
        download = logged_in_client.get("/files/download/file.txt")
        assert download.status_code == 200
        assert download.data == b"sample"

    def test_upload_missing_file_returns_400(self, logged_in_client):
        r = logged_in_client.post("/files/upload", data={}, follow_redirects=True)
        assert r.status_code == 400

    def test_delete_file(self, logged_in_client, tmp_path):
        import main as m

        target = m.UPLOADS_DIR / "delete.txt"
        target.write_text("delete me")
        r = logged_in_client.post("/files/delete/delete.txt", follow_redirects=True)
        assert r.status_code == 200
        assert not target.exists()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

class TestBackup:
    def test_backup_download_returns_tar_gz(self, logged_in_client):
        r = logged_in_client.post("/backup/download")
        assert r.status_code == 200
        assert "backup.tar.gz" in r.headers.get("Content-Disposition", "")

    def test_backup_restore_valid_tar(self, logged_in_client, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="notes/notes.db")
            data = b"content"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        r = logged_in_client.post(
            "/backup/restore",
            data={"backup": (buf, "backup.tar.gz")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        import main as m

        assert m.NOTES_DB.exists()

    def test_backup_restore_with_traversal_rejected(self, logged_in_client):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="../evil.txt")
            data = b"evil"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        r = logged_in_client.post(
            "/backup/restore",
            data={"backup": (buf, "bad.tar.gz")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# System Monitor
# ---------------------------------------------------------------------------

class TestSystem:
    def test_system_json_has_expected_keys(self, logged_in_client):
        r = logged_in_client.get("/system")
        assert r.status_code == 200
        data = r.get_json()
        expected = {
            "cpu_percent",
            "ram_used_mb",
            "ram_total_mb",
            "ram_percent",
            "disk_used_gb",
            "disk_total_gb",
            "disk_percent",
            "services",
            "uptime_seconds",
        }
        assert expected.issubset(data.keys())

    def test_system_page_returns_200(self, logged_in_client):
        r = logged_in_client.get("/system/page")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Survival Tools
# ---------------------------------------------------------------------------

class TestSurvivalTools:
    def test_water_tool_returns_keys(self, logged_in_client):
        r = logged_in_client.post("/tools/water", json={"liters": 2})
        data = r.get_json()
        assert r.status_code == 200
        assert {"instructions", "treatment_amount", "wait_time_minutes"} <= data.keys()

    def test_calories_tool_returns_keys(self, logged_in_client):
        r = logged_in_client.post("/tools/calories", json={"weight_kg": 70})
        data = r.get_json()
        assert r.status_code == 200
        assert {"total_calories", "carbs_g", "protein_g", "fat_g"} <= data.keys()

    def test_dose_tool_returns_keys(self, logged_in_client):
        r = logged_in_client.post(
            "/tools/dose", json={"medication": "ibuprofen", "weight_kg": 70, "age": 30}
        )
        data = r.get_json()
        assert r.status_code == 200
        assert {"dose_mg", "frequency", "max_daily_mg", "warnings"} <= data.keys()

    def test_dose_tool_warns_on_aspirin_and_child(self, logged_in_client):
        r = logged_in_client.post(
            "/tools/dose", json={"medication": "aspirin", "weight_kg": 40, "age": 12}
        )
        data = r.get_json()
        assert any("aspirin" in w.lower() for w in data["warnings"])

    def test_fire_tool_returns_keys(self, logged_in_client):
        r = logged_in_client.post("/tools/fire", json={"fuel": "wood"})
        data = r.get_json()
        assert r.status_code == 200
        assert {"method", "steps", "tinder_options"} <= data.keys()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class TestInventory:
    def test_inventory_page(self, logged_in_client):
        r = logged_in_client.get("/inventory")
        assert r.status_code == 200

    def test_add_item(self, logged_in_client):
        r = logged_in_client.post(
            "/inventory/add",
            data={"name": "Water", "quantity": 2, "category": "Supplies", "location": "Bin A"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        import main as m

        conn = m.get_db()
        count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        conn.close()
        assert count == 1

    def test_edit_item(self, logged_in_client):
        import main as m

        conn = m.get_db()
        conn.execute(
            "INSERT INTO inventory (name, quantity, category, location, notes, updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Radio", 1, "Comms", "Shelf", "", m.now_iso()),
        )
        item_id = conn.execute("SELECT id FROM inventory LIMIT 1").fetchone()["id"]
        conn.commit()
        conn.close()

        r = logged_in_client.post(
            f"/inventory/edit/{item_id}",
            data={"name": "Radio", "quantity": 3, "category": "Comms", "location": "Bag", "notes": ""},
            follow_redirects=False,
        )
        assert r.status_code == 302
        conn = m.get_db()
        qty = conn.execute("SELECT quantity FROM inventory WHERE id = ?", (item_id,)).fetchone()[0]
        conn.close()
        assert qty == 3

    def test_delete_item(self, logged_in_client):
        import main as m

        conn = m.get_db()
        conn.execute(
            "INSERT INTO inventory (name, quantity, category, location, notes, updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Food", 5, "Supplies", "Pantry", "", m.now_iso()),
        )
        item_id = conn.execute("SELECT id FROM inventory LIMIT 1").fetchone()["id"]
        conn.commit()
        conn.close()

        r = logged_in_client.post(f"/inventory/delete/{item_id}", follow_redirects=False)
        assert r.status_code == 302
        conn = m.get_db()
        count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        conn.close()
        assert count == 0


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_allowed_file_pdf(self):
        import main as m
        assert m.allowed_file("document.pdf") is True

    def test_allowed_file_txt(self):
        import main as m
        assert m.allowed_file("notes.txt") is True

    def test_allowed_file_sh_rejected(self):
        import main as m
        assert m.allowed_file("hack.sh") is False

    def test_allowed_file_exe_rejected(self):
        import main as m
        assert m.allowed_file("virus.exe") is False

    def test_now_iso_format(self):
        import main as m
        ts = m.now_iso()
        assert len(ts) == 20
        assert ts.endswith("Z")
        assert "T" in ts
