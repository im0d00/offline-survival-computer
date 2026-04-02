"""
Offline Survival Computer - Main Application Entry Point
"""
import os
import json
import sqlite3
import datetime
import tarfile
import io
import socket
from pathlib import Path

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
import psutil

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
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max upload

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"

# Admin credentials from environment
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    if user_id == "1":
        return User("1", ADMIN_USERNAME)
    return None

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


def check_port(host: str, port: int, timeout: float = 2) -> bool:
    """Check if a port is open using socket."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def get_directory_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except Exception:
        pass
    return total


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and ADMIN_PASSWORD_HASH:
            if check_password_hash(ADMIN_PASSWORD_HASH, password):
                user = User("1", ADMIN_USERNAME)
                login_user(user)
                flash("Login successful!", "success")
                next_page = request.args.get("next")
                return redirect(next_page) if next_page else redirect(url_for("index"))

        flash("Invalid username or password.", "danger")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
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
    """Display file manager page with uploads and documents."""
    uploads = []
    documents_list = []

    # List uploads
    if UPLOADS_DIR.exists():
        for file_path in UPLOADS_DIR.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                uploads.append({
                    "name": file_path.name,
                    "size": stat.st_size,
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                })

    # List documents (AI knowledge base)
    if DOCS_DIR.exists():
        for file_path in DOCS_DIR.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                documents_list.append({
                    "name": file_path.name,
                    "size": stat.st_size,
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                })

    return render_template("files.html", uploads=uploads, documents=documents_list)


@app.route("/files/upload", methods=["POST"])
@login_required
def files_upload():
    """Handle file upload to uploads directory."""
    if "file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("files"))

    file = request.files["file"]
    if file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("files"))

    filename = secure_filename(file.filename)
    dest = UPLOADS_DIR / filename
    file.save(dest)

    flash(f"'{filename}' uploaded successfully.", "success")
    return redirect(url_for("files"))


@app.route("/files/download/<path:filename>")
@login_required
def files_download(filename: str):
    """Download a file from uploads or documents."""
    from flask import send_from_directory

    filename = secure_filename(filename)

    # Check uploads first
    if (UPLOADS_DIR / filename).exists():
        return send_from_directory(UPLOADS_DIR, filename, as_attachment=True)

    # Then check documents
    if (DOCS_DIR / filename).exists():
        return send_from_directory(DOCS_DIR, filename, as_attachment=True)

    flash("File not found.", "danger")
    return redirect(url_for("files"))


@app.route("/files/delete/<path:filename>", methods=["POST"])
@login_required
def files_delete(filename: str):
    """Delete a file from uploads or documents."""
    filename = secure_filename(filename)

    # Check uploads first
    upload_path = UPLOADS_DIR / filename
    if upload_path.exists():
        upload_path.unlink()
        flash(f"'{filename}' deleted from uploads.", "info")
        return redirect(url_for("files"))

    # Then check documents
    doc_path = DOCS_DIR / filename
    if doc_path.exists():
        doc_path.unlink()
        flash(f"'{filename}' deleted from documents.", "info")
        return redirect(url_for("files"))

    flash("File not found.", "danger")
    return redirect(url_for("files"))


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------

@app.route("/backup/download", methods=["POST"])
@login_required
def backup_download():
    """Create and download a backup tarball of critical data."""
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"survival_backup_{timestamp}.tar.gz"

    # Create in-memory tarball
    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        # Add notes directory
        if NOTES_DB.parent.exists():
            tar.add(NOTES_DB.parent, arcname="notes")

        # Add documents directory
        if DOCS_DIR.exists():
            tar.add(DOCS_DIR, arcname="documents")

        # Add uploads directory
        if UPLOADS_DIR.exists():
            tar.add(UPLOADS_DIR, arcname="uploads")

        # Add .env if it exists (in parent directory)
        env_file = BASE_DIR.parent / ".env"
        if env_file.exists():
            tar.add(env_file, arcname=".env")

    tar_buffer.seek(0)

    return send_file(
        tar_buffer,
        as_attachment=True,
        download_name=backup_filename,
        mimetype="application/gzip"
    )


@app.route("/backup/restore", methods=["POST"])
@login_required
def backup_restore():
    """Restore data from an uploaded backup tarball."""
    if "backup_file" not in request.files:
        flash("No backup file selected.", "danger")
        return redirect(url_for("index"))

    file = request.files["backup_file"]
    if file.filename == "":
        flash("No backup file selected.", "danger")
        return redirect(url_for("index"))

    if not file.filename.endswith(".tar.gz"):
        flash("Invalid backup file. Must be a .tar.gz file.", "danger")
        return redirect(url_for("index"))

    try:
        # Read tarball into memory
        tar_buffer = io.BytesIO(file.read())
        tar_buffer.seek(0)

        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            # Validate tarball contents
            members = tar.getmembers()
            valid_prefixes = ("notes/", "documents/", "uploads/", ".env")

            for member in members:
                # Prevent path traversal attacks
                if member.name.startswith("/") or ".." in member.name:
                    flash("Invalid backup file: contains unsafe paths.", "danger")
                    return redirect(url_for("index"))

                # Check if member starts with valid prefix
                if not any(member.name.startswith(prefix) for prefix in valid_prefixes):
                    if member.name not in valid_prefixes:  # Allow exact matches
                        flash(f"Invalid backup file: unexpected file '{member.name}'.", "danger")
                        return redirect(url_for("index"))

            # Extract to data directory
            for member in members:
                if member.name == ".env":
                    # Extract .env to parent directory
                    tar.extract(member, path=BASE_DIR.parent)
                else:
                    # Extract data files to data directory
                    tar.extract(member, path=DATA_DIR.parent / "data")

        flash("Backup restored successfully!", "success")

    except tarfile.TarError as e:
        flash(f"Failed to restore backup: {str(e)}", "danger")
    except Exception as e:
        flash(f"An error occurred during restoration: {str(e)}", "danger")

    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# System Monitor
# ---------------------------------------------------------------------------

@app.route("/system")
@login_required
def system_api():
    """Return system metrics as JSON."""
    # Get CPU and memory info
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # Check service ports
    services = {
        "kiwix": check_port("kiwix", 8080, timeout=1),
        "ollama": check_port("ollama", 11434, timeout=1),
    }

    # Get uptime
    boot_time = psutil.boot_time()
    uptime_seconds = datetime.datetime.now().timestamp() - boot_time

    return jsonify({
        "cpu_percent": cpu_percent,
        "ram_used_mb": memory.used / 1024 / 1024,
        "ram_total_mb": memory.total / 1024 / 1024,
        "ram_percent": memory.percent,
        "disk_used_gb": disk.used / 1024 / 1024 / 1024,
        "disk_total_gb": disk.total / 1024 / 1024 / 1024,
        "disk_percent": disk.percent,
        "services": services,
        "uptime_seconds": uptime_seconds,
    })


@app.route("/system/page")
@login_required
def system_page():
    """Render system monitor page."""
    # Get data volume sizes
    zim_size = get_directory_size(DATA_DIR / "zim") if (DATA_DIR / "zim").exists() else 0
    docs_size = get_directory_size(DOCS_DIR) if DOCS_DIR.exists() else 0
    uploads_size = get_directory_size(UPLOADS_DIR) if UPLOADS_DIR.exists() else 0

    volumes = {
        "zim_mb": zim_size / 1024 / 1024,
        "docs_mb": docs_size / 1024 / 1024,
        "uploads_mb": uploads_size / 1024 / 1024,
    }

    return render_template("system.html", volumes=volumes)


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
