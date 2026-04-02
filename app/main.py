"""
Offline Survival Computer - Main Application Entry Point
"""
import os
import json
import sqlite3
import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

from core.performance_monitor import get_monitor, PerformanceMonitor
from system.hardware_detector import auto_tune, get_hardware_info

# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
NOTES_DB = DATA_DIR / "notes" / "notes.db"
UPLOADS_DIR = DATA_DIR / "uploads"
DOCS_DIR = DATA_DIR / "documents"
MAPS_DIR = DATA_DIR / "maps"

ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "docx", "png", "jpg", "jpeg"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "offline-survival-computer-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB max upload

# Service URLs (configurable via environment variables)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
KIWIX_URL = os.environ.get("KIWIX_URL", "http://kiwix:8080")
MAPS_TILE_URL = os.environ.get(
    "MAPS_TILE_URL", "/tiles/{z}/{x}/{y}.png"
)

# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

def init_db():
    """Create notes database if it doesn't exist."""
    NOTES_DB.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    MAPS_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(NOTES_DB)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title    TEXT    NOT NULL,
            content  TEXT    NOT NULL DEFAULT '',
            tags     TEXT    NOT NULL DEFAULT '',
            created  TEXT    NOT NULL,
            updated  TEXT    NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filename  TEXT    NOT NULL,
            filepath  TEXT    NOT NULL,
            size      INTEGER NOT NULL DEFAULT 0,
            uploaded  TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_db():
    """Return a database connection."""
    conn = sqlite3.connect(NOTES_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_service(url: str, timeout: int = 2) -> bool:
    """Return True if the given URL responds with a 2xx status."""
    import requests as req
    try:
        r = req.get(url, timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Command Center (Dashboard)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    statuses = {
        "kiwix": check_service(KIWIX_URL),
        "ollama": check_service(f"{OLLAMA_URL}/api/tags"),
    }
    conn = get_db()
    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    conn.close()
    return render_template(
        "index.html",
        statuses=statuses,
        note_count=note_count,
        doc_count=doc_count,
        kiwix_url=KIWIX_URL,
    )


# ---------------------------------------------------------------------------
# Information Library
# ---------------------------------------------------------------------------

@app.route("/library")
def library():
    return render_template("library.html", kiwix_url=KIWIX_URL)


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

@app.route("/ai")
def ai_assistant():
    # Fetch available models from Ollama
    import requests as req
    models = []
    try:
        r = req.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.ok:
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    conn = get_db()
    documents = conn.execute(
        "SELECT id, filename, size, uploaded FROM documents ORDER BY uploaded DESC"
    ).fetchall()
    conn.close()
    return render_template("ai_assistant.html", models=models, documents=documents)


@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    """Proxy a chat request to Ollama."""
    import requests as req
    data = request.get_json(force=True, silent=True) or {}
    model = data.get("model", "llama2")
    messages = data.get("messages", [])

    try:
        r = req.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=120,
        )
        if r.ok:
            return jsonify(r.json())
        return jsonify({"error": f"Ollama returned {r.status_code}"}), 502
    except Exception:
        return jsonify({"error": "Could not reach the AI service. Is Ollama running?"}), 503


@app.route("/ai/upload", methods=["POST"])
def ai_upload_document():
    """Accept a document file for use in the AI knowledge base."""
    if "file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("ai_assistant"))

    file = request.files["file"]
    if file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("ai_assistant"))

    if not allowed_file(file.filename):
        flash("Unsupported file type.", "danger")
        return redirect(url_for("ai_assistant"))

    filename = secure_filename(file.filename)
    dest = DOCS_DIR / filename
    file.save(dest)

    conn = get_db()
    conn.execute(
        "INSERT INTO documents (filename, filepath, size, uploaded) VALUES (?, ?, ?, ?)",
        (filename, str(dest), dest.stat().st_size, now_iso()),
    )
    conn.commit()
    conn.close()

    flash(f"'{filename}' uploaded successfully.", "success")
    return redirect(url_for("ai_assistant"))


@app.route("/ai/document/<int:doc_id>", methods=["DELETE"])
def ai_delete_document(doc_id: int):
    """Remove a document from the knowledge base."""
    conn = get_db()
    row = conn.execute(
        "SELECT filepath FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    filepath = Path(row["filepath"])
    if filepath.exists():
        filepath.unlink()
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@app.route("/ai/models")
def ai_models():
    """Return available Ollama models as JSON."""
    import requests as req
    try:
        r = req.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.ok:
            return jsonify(r.json())
        return jsonify({"models": []}), 502
    except Exception:
        return jsonify({"error": "Could not reach the AI service. Is Ollama running?"}), 503


# ---------------------------------------------------------------------------
# Education Platform
# ---------------------------------------------------------------------------

@app.route("/education")
def education():
    return render_template("education.html", kiwix_url=KIWIX_URL)


# ---------------------------------------------------------------------------
# Offline Maps
# ---------------------------------------------------------------------------

@app.route("/maps")
def maps():
    return render_template("maps.html", tile_url=MAPS_TILE_URL)


# ---------------------------------------------------------------------------
# Notes App
# ---------------------------------------------------------------------------

@app.route("/notes")
def notes():
    conn = get_db()
    all_notes = conn.execute(
        "SELECT id, title, tags, created, updated FROM notes ORDER BY updated DESC"
    ).fetchall()
    conn.close()
    return render_template("notes.html", notes=all_notes)


@app.route("/notes/new", methods=["GET", "POST"])
def notes_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        tags = request.form.get("tags", "").strip()
        if not title:
            flash("Title is required.", "danger")
            return render_template("notes_edit.html", note=None)
        ts = now_iso()
        conn = get_db()
        cursor = conn.execute(
            "INSERT INTO notes (title, content, tags, created, updated) VALUES (?, ?, ?, ?, ?)",
            (title, content, tags, ts, ts),
        )
        note_id = cursor.lastrowid
        conn.commit()
        conn.close()
        flash("Note created.", "success")
        return redirect(url_for("notes_view", note_id=note_id))
    return render_template("notes_edit.html", note=None)


@app.route("/notes/<int:note_id>")
def notes_view(note_id: int):
    conn = get_db()
    note = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    if note is None:
        flash("Note not found.", "danger")
        return redirect(url_for("notes"))
    return render_template("notes_view.html", note=note)


@app.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
def notes_edit(note_id: int):
    conn = get_db()
    note = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if note is None:
        conn.close()
        flash("Note not found.", "danger")
        return redirect(url_for("notes"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        tags = request.form.get("tags", "").strip()
        if not title:
            flash("Title is required.", "danger")
            return render_template("notes_edit.html", note=note)
        conn.execute(
            "UPDATE notes SET title = ?, content = ?, tags = ?, updated = ? WHERE id = ?",
            (title, content, tags, now_iso(), note_id),
        )
        conn.commit()
        conn.close()
        flash("Note updated.", "success")
        return redirect(url_for("notes_view", note_id=note_id))

    conn.close()
    return render_template("notes_edit.html", note=note)


@app.route("/notes/<int:note_id>/delete", methods=["POST"])
def notes_delete(note_id: int):
    conn = get_db()
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()
    flash("Note deleted.", "info")
    return redirect(url_for("notes"))


# API endpoints for notes (JSON)
@app.route("/api/notes")
def api_notes():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, tags, created, updated FROM notes ORDER BY updated DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notes/<int:note_id>")
def api_note(note_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# Performance Dashboard
# ---------------------------------------------------------------------------

@app.route("/performance")
def performance():
    """Render the real-time performance dashboard."""
    profile = auto_tune()
    hw_info = get_hardware_info()
    return render_template(
        "performance.html",
        profile=profile.to_dict(),
        hw_info=hw_info.to_dict(),
    )


# ---------------------------------------------------------------------------
# Performance API
# ---------------------------------------------------------------------------

@app.route("/api/performance/metrics")
def api_performance_metrics():
    """Return the latest performance snapshot as JSON."""
    monitor = get_monitor()
    snap = monitor.latest()
    if snap is None:
        snap = monitor.snapshot_now()
    return jsonify(snap.to_dict())


@app.route("/api/performance/history")
def api_performance_history():
    """Return recent performance history (last N snapshots)."""
    monitor = get_monitor()
    n = request.args.get("n", 60, type=int)
    n = max(1, min(n, 120))
    history = monitor.history(n=n)
    return jsonify([s.to_dict() for s in history])


@app.route("/api/performance/snapshot", methods=["POST"])
def api_performance_snapshot():
    """Force an immediate metric collection and return it."""
    monitor = get_monitor()
    snap = monitor.snapshot_now()
    return jsonify(snap.to_dict())


@app.route("/api/performance/profile")
def api_performance_profile():
    """Return the auto-detected hardware profile and raw hardware info."""
    profile = auto_tune()
    hw_info = get_hardware_info()
    return jsonify({"profile": profile.to_dict(), "hardware": hw_info.to_dict()})


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    # Start background performance monitor
    get_monitor().start()
    app.run(host="0.0.0.0", port=5000, debug=False)
