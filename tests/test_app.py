"""
Tests for the Offline Survival Computer Flask application.
Run with:  pytest tests/
"""
import json
import os
import sys
import tempfile
import pytest

# Ensure the app module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Redirect all data directories to a temporary path so tests are isolated."""
    import main as m

    notes_dir = tmp_path / "notes"
    uploads_dir = tmp_path / "uploads"
    docs_dir = tmp_path / "documents"
    maps_dir = tmp_path / "maps"
    notes_dir.mkdir()
    uploads_dir.mkdir()
    docs_dir.mkdir()
    maps_dir.mkdir()

    monkeypatch.setattr(m, "NOTES_DB", notes_dir / "notes.db")
    monkeypatch.setattr(m, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(m, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(m, "MAPS_DIR", maps_dir)

    m.init_db()
    yield


@pytest.fixture()
def client(isolated_data_dirs):
    import main as m
    m.app.config["TESTING"] = True
    m.app.config["WTF_CSRF_ENABLED"] = False
    with m.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Command Center
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_contains_title(self, client):
        r = client.get("/")
        assert b"Command Center" in r.data

    def test_contains_module_links(self, client):
        r = client.get("/")
        assert b"Library" in r.data
        assert b"AI Assistant" in r.data
        assert b"Education" in r.data
        assert b"Maps" in r.data
        assert b"Notes" in r.data


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

class TestLibrary:
    def test_returns_200(self, client):
        r = client.get("/library")
        assert r.status_code == 200

    def test_contains_kiwix_references(self, client):
        r = client.get("/library")
        assert b"Kiwix" in r.data
        assert b"Wikipedia" in r.data


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------

class TestEducation:
    def test_returns_200(self, client):
        r = client.get("/education")
        assert r.status_code == 200

    def test_contains_khan_academy(self, client):
        r = client.get("/education")
        assert b"Khan Academy" in r.data


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------

class TestMaps:
    def test_returns_200(self, client):
        r = client.get("/maps")
        assert r.status_code == 200

    def test_contains_leaflet(self, client):
        r = client.get("/maps")
        assert b"leaflet" in r.data.lower()


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

class TestAIAssistant:
    def test_returns_200(self, client):
        r = client.get("/ai")
        assert r.status_code == 200

    def test_upload_no_file_redirects(self, client):
        r = client.post("/ai/upload", data={}, follow_redirects=True)
        assert r.status_code == 200

    def test_upload_valid_file(self, client):
        data = {
            "file": (
                __import__("io").BytesIO(b"Hello world"),
                "test_doc.txt",
            )
        }
        r = client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"uploaded successfully" in r.data

    def test_upload_disallowed_extension(self, client):
        data = {
            "file": (
                __import__("io").BytesIO(b"#!/bin/bash"),
                "evil.sh",
            )
        }
        r = client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Unsupported" in r.data

    def test_delete_nonexistent_document(self, client):
        r = client.delete("/ai/document/9999")
        assert r.status_code == 404

    def test_delete_existing_document(self, client):
        # Upload first
        data = {
            "file": (
                __import__("io").BytesIO(b"Some content"),
                "deleteme.txt",
            )
        }
        client.post(
            "/ai/upload",
            data=data,
            content_type="multipart/form-data",
        )
        import main as m
        conn = m.get_db()
        doc = conn.execute("SELECT id FROM documents ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert doc is not None

        r = client.delete(f"/ai/document/{doc['id']}")
        assert r.status_code == 200
        assert json.loads(r.data)["status"] == "deleted"

    def test_models_endpoint_on_ollama_down(self, client):
        # Ollama is not running in tests; should return a graceful error response
        r = client.get("/ai/models")
        # Accept either 503 (connection refused) or 502
        assert r.status_code in (502, 503)

    def test_chat_endpoint_on_ollama_down(self, client):
        r = client.post(
            "/ai/chat",
            data=json.dumps({"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]}),
            content_type="application/json",
        )
        assert r.status_code in (502, 503)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class TestNotes:
    def test_list_empty(self, client):
        r = client.get("/notes")
        assert r.status_code == 200
        assert b"No notes yet" in r.data

    def test_new_note_form(self, client):
        r = client.get("/notes/new")
        assert r.status_code == 200
        assert b"form" in r.data.lower()

    def test_create_and_view_note(self, client):
        r = client.post(
            "/notes/new",
            data={"title": "Test Note", "content": "Hello, survival!", "tags": "test"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Test Note" in r.data
        assert b"Hello, survival!" in r.data

    def test_create_note_missing_title(self, client):
        r = client.post(
            "/notes/new",
            data={"title": "", "content": "No title"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Title is required" in r.data

    def test_edit_note(self, client):
        # Create
        client.post(
            "/notes/new",
            data={"title": "Edit Me", "content": "Original", "tags": ""},
            follow_redirects=True,
        )
        import main as m
        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = client.post(
            f"/notes/{note['id']}/edit",
            data={"title": "Edited Title", "content": "Updated content", "tags": "edited"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"Edited Title" in r.data

    def test_delete_note(self, client):
        client.post(
            "/notes/new",
            data={"title": "Delete Me", "content": "Bye", "tags": ""},
            follow_redirects=True,
        )
        import main as m
        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = client.post(f"/notes/{note['id']}/delete", follow_redirects=True)
        assert r.status_code == 200
        assert b"deleted" in r.data.lower()

    def test_view_nonexistent_note(self, client):
        r = client.get("/notes/99999", follow_redirects=True)
        assert r.status_code == 200
        assert b"not found" in r.data.lower()

    def test_api_notes_empty(self, client):
        r = client.get("/api/notes")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_notes_with_data(self, client):
        client.post(
            "/notes/new",
            data={"title": "API Note", "content": "API content", "tags": "api"},
            follow_redirects=True,
        )
        r = client.get("/api/notes")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert len(data) == 1
        assert data[0]["title"] == "API Note"

    def test_api_single_note(self, client):
        client.post(
            "/notes/new",
            data={"title": "Single", "content": "Solo", "tags": ""},
            follow_redirects=True,
        )
        import main as m
        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        r = client.get(f"/api/notes/{note['id']}")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["title"] == "Single"

    def test_api_note_not_found(self, client):
        r = client.get("/api/notes/99999")
        assert r.status_code == 404


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
