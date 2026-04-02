"""
Offline Survival Computer - Main Application Entry Point
"""
from __future__ import annotations

import datetime
import io
import json
import os
import sqlite3
import tarfile
import time
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - defensive fallback
    psutil = None
    _PSUTIL_AVAILABLE = False

from core.performance_monitor import PerformanceMonitor, get_monitor
from survival_tools import (
    plan_calories,
    plan_fire_starting,
    plan_medication_dose,
    plan_water_treatment,
)
from system.hardware_detector import auto_tune, get_hardware_info

# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR.parent / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
NOTES_DB = DATA_DIR / "notes" / "notes.db"
UPLOADS_DIR = DATA_DIR / "uploads"
DOCS_DIR = DATA_DIR / "documents"
MAPS_DIR = DATA_DIR / "maps"

DEFAULT_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "offline")

ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "docx", "png", "jpg", "jpeg"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "offline-survival-computer-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB max upload
app.config["UPLOAD_FOLDER"] = str(UPLOADS_DIR)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message_category = "warning"
login_manager.init_app(app)

# Service URLs (configurable via environment variables)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
KIWIX_URL = os.environ.get("KIWIX_URL", "http://kiwix:8080")
MAPS_TILE_URL = os.environ.get(
    "MAPS_TILE_URL", "/tiles/{z}/{x}/{y}.png"
)

# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, username: str, password_hash: str) -> None:
        self.id = username
        self.password_hash = password_hash


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return get_user(user_id)


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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT    NOT NULL,
            quantity  INTEGER NOT NULL DEFAULT 0,
            category  TEXT    NOT NULL DEFAULT '',
            location  TEXT    NOT NULL DEFAULT '',
            notes     TEXT    NOT NULL DEFAULT '',
            updated   TEXT    NOT NULL
        )
        """
    )
    _ensure_default_user(cursor)
    conn.commit()
    conn.close()


def get_db():
    """Return a database connection."""
    conn = sqlite3.connect(NOTES_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_default_user(cursor: sqlite3.Cursor) -> None:
    """Insert or update the default admin user."""
    password_hash = os.environ.get("ADMIN_PASSWORD_HASH") or generate_password_hash(
        DEFAULT_PASSWORD
    )
    row = cursor.execute(
        "SELECT password_hash FROM users WHERE username = ?", (DEFAULT_USERNAME,)
    ).fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (DEFAULT_USERNAME, password_hash),
        )
    elif row[0] != password_hash:
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash, DEFAULT_USERNAME),
        )


def get_user(username: str) -> User | None:
    conn = get_db()
    row = conn.execute(
        "SELECT username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row:
        return User(row["username"], row["password_hash"])
    return None


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


def list_uploaded_files() -> List[dict]:
    """Return metadata for files stored in the uploads directory."""
    files: List[dict] = []
    if not UPLOADS_DIR.exists():
        return files
    for path in sorted(UPLOADS_DIR.glob("*")):
        if path.is_file():
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime),
                }
            )
    return files


def _is_safe_tar_member(member: tarfile.TarInfo, base: Path) -> bool:
    """Prevent path traversal when extracting tar archives."""
    target_path = (base / member.name).resolve()
    try:
        target_path.relative_to(base.resolve())
    except ValueError:
        return False
    return not (member.name.startswith("/") or ".." in Path(member.name).parts)


def _extract_backup(tar_stream: io.BytesIO) -> bool:
    """Safely extract a backup archive into DATA_DIR."""
    tar_stream.seek(0)
    with tarfile.open(fileobj=tar_stream, mode="r:gz") as tar:
        members = tar.getmembers()
        if not all(_is_safe_tar_member(m, DATA_DIR) for m in members):
            return False
        tar.extractall(path=DATA_DIR)
    try:
        init_db()
    except sqlite3.DatabaseError:
        if NOTES_DB.exists():
            NOTES_DB.unlink()
        init_db()
    return True


def _system_metrics() -> Dict[str, object]:
    """Collect system metrics for the dashboard."""
    services = {
        "kiwix": check_service(KIWIX_URL),
        "ollama": check_service(f"{OLLAMA_URL}/api/tags"),
    }

    if not _PSUTIL_AVAILABLE:
        return {
            "cpu_percent": 0.0,
            "ram_used_mb": 0.0,
            "ram_total_mb": 0.0,
            "ram_percent": 0.0,
            "disk_used_gb": 0.0,
            "disk_total_gb": 0.0,
            "disk_percent": 0.0,
            "services": services,
            "uptime_seconds": 0,
        }

    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(DATA_DIR))
    uptime_seconds = max(0, int(time.time() - psutil.boot_time()))

    return {
        "cpu_percent": cpu_percent,
        "ram_used_mb": mem.used / (1024 ** 2),
        "ram_total_mb": mem.total / (1024 ** 2),
        "ram_percent": mem.percent,
        "disk_used_gb": disk.used / (1024 ** 3),
        "disk_total_gb": disk.total / (1024 ** 3),
        "disk_percent": disk.percent,
        "services": services,
        "uptime_seconds": uptime_seconds,
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user(username)
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("index"))
        error = "Invalid username or password."
        flash(error, "danger")
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Command Center (Dashboard)
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
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
@login_required
def library():
    return render_template("library.html", kiwix_url=KIWIX_URL)


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

@app.route("/ai")
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def education():
    return render_template("education.html", kiwix_url=KIWIX_URL)


# ---------------------------------------------------------------------------
# Offline Maps
# ---------------------------------------------------------------------------

@app.route("/maps")
@login_required
def maps():
    return render_template("maps.html", tile_url=MAPS_TILE_URL)


# ---------------------------------------------------------------------------
# Notes App
# ---------------------------------------------------------------------------

@app.route("/notes")
@login_required
def notes():
    conn = get_db()
    all_notes = conn.execute(
        "SELECT id, title, tags, created, updated FROM notes ORDER BY updated DESC"
    ).fetchall()
    conn.close()
    return render_template("notes.html", notes=all_notes)


@app.route("/notes/new", methods=["GET", "POST"])
@login_required
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
@login_required
def notes_view(note_id: int):
    conn = get_db()
    note = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    if note is None:
        flash("Note not found.", "danger")
        return redirect(url_for("notes"))
    return render_template("notes_view.html", note=note)


@app.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
@login_required
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
@login_required
def notes_delete(note_id: int):
    conn = get_db()
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()
    flash("Note deleted.", "info")
    return redirect(url_for("notes"))


# API endpoints for notes (JSON)
@app.route("/api/notes")
@login_required
def api_notes():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, tags, created, updated FROM notes ORDER BY updated DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notes/<int:note_id>")
@login_required
def api_note(note_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# File Manager
# ---------------------------------------------------------------------------

@app.route("/files")
@login_required
def files():
    return render_template("files.html", files=list_uploaded_files())


@app.route("/files/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files or request.files["file"].filename == "":
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    filename = secure_filename(file.filename)
    dest = UPLOADS_DIR / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    file.save(dest)
    flash(f"Uploaded {filename}", "success")
    return redirect(url_for("files"))


@app.route("/files/download/<path:filename>")
@login_required
def download_file(filename: str):
    safe_name = secure_filename(filename)
    file_path = UPLOADS_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        abort(404)
    return send_from_directory(UPLOADS_DIR, safe_name, as_attachment=True)


@app.route("/files/delete/<path:filename>", methods=["POST"])
@login_required
def delete_file(filename: str):
    safe_name = secure_filename(filename)
    file_path = UPLOADS_DIR / safe_name
    if file_path.exists():
        file_path.unlink()
    flash(f"Deleted {safe_name}", "info")
    return redirect(url_for("files"))


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------

@app.route("/backup/download", methods=["POST"])
@login_required
def backup_download():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if NOTES_DB.exists():
            tar.add(NOTES_DB, arcname="notes/notes.db")
        if UPLOADS_DIR.exists():
            tar.add(UPLOADS_DIR, arcname="uploads")
        if DOCS_DIR.exists():
            tar.add(DOCS_DIR, arcname="documents")
        if MAPS_DIR.exists():
            tar.add(MAPS_DIR, arcname="maps")
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/gzip",
        as_attachment=True,
        download_name="backup.tar.gz",
    )


@app.route("/backup/restore", methods=["POST"])
@login_required
def backup_restore():
    uploaded = request.files.get("backup")
    if uploaded is None or uploaded.filename == "":
        return jsonify({"error": "No backup uploaded"}), 400
    buffer = io.BytesIO(uploaded.read())
    if not _extract_backup(buffer):
        return jsonify({"error": "Invalid archive"}), 400
    flash("Backup restored.", "success")
    return jsonify({"status": "restored"})


# ---------------------------------------------------------------------------
# System Monitor
# ---------------------------------------------------------------------------

@app.route("/system")
@login_required
def system_info():
    return jsonify(_system_metrics())


@app.route("/system/page")
@login_required
def system_page():
    return render_template("system.html")


# ---------------------------------------------------------------------------
# Survival Tools
# ---------------------------------------------------------------------------

@app.route("/tools/water", methods=["POST"])
@login_required
def tools_water():
    data = request.get_json(force=True, silent=True) or {}
    liters = float(data.get("liters", 1.0))
    method = data.get("method", "boil")
    altitude = float(data.get("altitude_m", 0))
    return jsonify(plan_water_treatment(liters, method=method, altitude_m=altitude))


@app.route("/tools/calories", methods=["POST"])
@login_required
def tools_calories():
    data = request.get_json(force=True, silent=True) or {}
    weight = float(data.get("weight_kg", 70))
    activity = data.get("activity", "moderate")
    return jsonify(plan_calories(weight_kg=weight, activity_level=activity))


@app.route("/tools/dose", methods=["POST"])
@login_required
def tools_dose():
    data = request.get_json(force=True, silent=True) or {}
    medication = data.get("medication", "")
    weight = float(data.get("weight_kg", 70))
    age = int(data.get("age", 30))
    return jsonify(plan_medication_dose(medication, weight, age))


@app.route("/tools/fire", methods=["POST"])
@login_required
def tools_fire():
    data = request.get_json(force=True, silent=True) or {}
    fuel = data.get("fuel", "wood")
    weather = data.get("weather", "dry")
    return jsonify(plan_fire_starting(fuel=fuel, weather=weather))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@app.route("/inventory")
@login_required
def inventory():
    conn = get_db()
    items = conn.execute(
        "SELECT id, name, quantity, category, location, notes, updated "
        "FROM inventory ORDER BY updated DESC"
    ).fetchall()
    conn.close()
    return render_template("inventory.html", items=items)


@app.route("/inventory/add", methods=["POST"])
@login_required
def inventory_add():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    quantity = request.form.get("quantity", "0")
    category = request.form.get("category", "").strip()
    location = request.form.get("location", "").strip()
    notes = request.form.get("notes", "").strip()
    ts = now_iso()

    conn = get_db()
    conn.execute(
        "INSERT INTO inventory (name, quantity, category, location, notes, updated) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, int(quantity or 0), category, location, notes, ts),
    )
    conn.commit()
    conn.close()
    flash("Item added.", "success")
    return redirect(url_for("inventory"))


@app.route("/inventory/edit/<int:item_id>", methods=["POST"])
@login_required
def inventory_edit(item_id: int):
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    quantity = request.form.get("quantity", "0")
    category = request.form.get("category", "").strip()
    location = request.form.get("location", "").strip()
    notes = request.form.get("notes", "").strip()

    conn = get_db()
    row = conn.execute("SELECT id FROM inventory WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    conn.execute(
        "UPDATE inventory SET name = ?, quantity = ?, category = ?, location = ?, notes = ?, updated = ? "
        "WHERE id = ?",
        (name, int(quantity or 0), category, location, notes, now_iso(), item_id),
    )
    conn.commit()
    conn.close()
    flash("Item updated.", "success")
    return redirect(url_for("inventory"))


@app.route("/inventory/delete/<int:item_id>", methods=["POST"])
@login_required
def inventory_delete(item_id: int):
    conn = get_db()
    conn.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Item deleted.", "info")
    return redirect(url_for("inventory"))


# ---------------------------------------------------------------------------
# Performance Dashboard
# ---------------------------------------------------------------------------

@app.route("/performance")
@login_required
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
@login_required
def api_performance_metrics():
    """Return the latest performance snapshot as JSON."""
    monitor = get_monitor()
    snap = monitor.latest()
    if snap is None:
        snap = monitor.snapshot_now()
    return jsonify(snap.to_dict())


@app.route("/api/performance/history")
@login_required
def api_performance_history():
    """Return recent performance history (last N snapshots)."""
    monitor = get_monitor()
    n = request.args.get("n", 60, type=int)
    n = max(1, min(n, 120))
    history = monitor.history(n=n)
    return jsonify([s.to_dict() for s in history])


@app.route("/api/performance/snapshot", methods=["POST"])
@login_required
def api_performance_snapshot():
    """Force an immediate metric collection and return it."""
    monitor = get_monitor()
    snap = monitor.snapshot_now()
    return jsonify(snap.to_dict())


@app.route("/api/performance/profile")
@login_required
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
