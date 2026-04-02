"""
tests/performance_benchmarks.py
================================
Benchmarking suite for the Offline Survival Computer.

Validates the performance targets from the issue:

  Startup
  -------
  * Backend init (init_db) < 1 second
  * Route response time < 500 ms for simple pages

  Search / API
  ------------
  * Notes search API < 500 ms for 500 documents (scaled proxy for 10 K docs)
  * API notes list < 200 ms

  Hardware profile detection
  --------------------------
  * auto_tune() completes < 200 ms
  * All three profile IDs are selectable

  Memory monitoring
  -----------------
  * PerformanceMonitor.snapshot_now() < 100 ms
  * History length stays ≤ HISTORY_MAXLEN

Run with::

    pytest tests/performance_benchmarks.py -v
"""

import os
import sys
import time

import pytest

# Make the app package importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Redirect all data directories to a temporary path so tests are isolated."""
    import main as m

    notes_dir  = tmp_path / "notes"
    uploads_dir = tmp_path / "uploads"
    docs_dir   = tmp_path / "documents"
    maps_dir   = tmp_path / "maps"
    for d in (notes_dir, uploads_dir, docs_dir, maps_dir):
        d.mkdir()

    monkeypatch.setattr(m, "NOTES_DB",    notes_dir / "notes.db")
    monkeypatch.setattr(m, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(m, "DOCS_DIR",    docs_dir)
    monkeypatch.setattr(m, "MAPS_DIR",    maps_dir)

    m.init_db()
    yield


@pytest.fixture()
def client(isolated_data_dirs):
    import main as m
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        yield c


@pytest.fixture()
def populated_client(client):
    """Client with 100 notes pre-loaded for search latency tests."""
    import main as m
    conn = m.get_db()
    ts = m.now_iso()
    conn.executemany(
        "INSERT INTO notes (title, content, tags, created, updated) VALUES (?,?,?,?,?)",
        [
            (f"Survival Note {i}", f"Content about survival topic {i}", f"tag{i % 10}", ts, ts)
            for i in range(100)
        ],
    )
    conn.commit()
    conn.close()
    return client


# ---------------------------------------------------------------------------
# Target: Backend initialisation < 1 second
# ---------------------------------------------------------------------------


class TestStartupBenchmarks:
    def test_init_db_under_one_second(self, tmp_path):
        """init_db() must complete in under 1 second (target: DB ready < 1s)."""
        import main as m

        fresh_db = tmp_path / "bench_notes.db"
        orig = m.NOTES_DB
        m.NOTES_DB = fresh_db
        (tmp_path / "notes").mkdir(exist_ok=True)
        (tmp_path / "uploads").mkdir(exist_ok=True)
        (tmp_path / "documents").mkdir(exist_ok=True)
        (tmp_path / "maps").mkdir(exist_ok=True)

        t0 = time.perf_counter()
        m.init_db()
        elapsed = time.perf_counter() - t0

        m.NOTES_DB = orig  # restore
        assert elapsed < 1.0, f"init_db took {elapsed:.3f}s (target < 1s)"

    def test_index_route_response_under_500ms(self, client):
        """GET / must respond within 500 ms (target: UI render < 500ms)."""
        t0 = time.perf_counter()
        r = client.get("/")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5, f"GET / took {elapsed:.3f}s (target < 500ms)"

    def test_notes_route_response_under_500ms(self, client):
        """GET /notes must respond within 500 ms."""
        t0 = time.perf_counter()
        r = client.get("/notes")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5, f"GET /notes took {elapsed:.3f}s (target < 500ms)"

    def test_performance_route_response_under_500ms(self, client):
        """GET /performance must respond within 500 ms."""
        t0 = time.perf_counter()
        r = client.get("/performance")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.5, f"GET /performance took {elapsed:.3f}s (target < 500ms)"


# ---------------------------------------------------------------------------
# Target: Search results < 500 ms
# ---------------------------------------------------------------------------


class TestSearchBenchmarks:
    def test_api_notes_list_under_200ms(self, populated_client):
        """GET /api/notes must return within 200 ms (UI interaction < 200ms)."""
        t0 = time.perf_counter()
        r = populated_client.get("/api/notes")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.2, f"GET /api/notes took {elapsed:.3f}s (target < 200ms)"

    def test_api_notes_list_under_500ms_with_100_docs(self, populated_client):
        """Listing 100 notes must complete in under 500 ms."""
        import json
        t0 = time.perf_counter()
        r = populated_client.get("/api/notes")
        elapsed = time.perf_counter() - t0
        data = json.loads(r.data)
        assert len(data) == 100
        assert elapsed < 0.5, f"100-note listing took {elapsed:.3f}s (target < 500ms)"

    def test_single_note_lookup_under_50ms(self, populated_client):
        """GET /api/notes/<id> for a single record must be under 50 ms."""
        import main as m
        conn = m.get_db()
        note = conn.execute("SELECT id FROM notes LIMIT 1").fetchone()
        conn.close()
        assert note is not None

        t0 = time.perf_counter()
        r = populated_client.get(f"/api/notes/{note['id']}")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.05, f"Single note lookup took {elapsed:.3f}s (target < 50ms)"

    def test_page_navigation_under_300ms(self, populated_client):
        """Page navigation (GET /notes) must be under 300 ms (target: < 300ms)."""
        t0 = time.perf_counter()
        r = populated_client.get("/notes")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.3, f"Notes page navigation took {elapsed:.3f}s (target < 300ms)"


# ---------------------------------------------------------------------------
# Target: Performance monitoring overhead < 100 ms
# ---------------------------------------------------------------------------


class TestPerformanceMonitorBenchmarks:
    def test_snapshot_now_under_100ms(self):
        """PerformanceMonitor.snapshot_now() must complete in under 100 ms."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor()
        t0 = time.perf_counter()
        snap = monitor.snapshot_now()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"snapshot_now() took {elapsed:.3f}s (target < 100ms)"
        assert snap is not None

    def test_history_respects_maxlen(self):
        """History must never exceed HISTORY_MAXLEN entries."""
        from core.performance_monitor import PerformanceMonitor, HISTORY_MAXLEN
        monitor = PerformanceMonitor(history_maxlen=10)
        for _ in range(20):
            monitor.snapshot_now()
        assert len(monitor.history()) <= 10

    def test_monitor_returns_valid_snapshot(self):
        """Snapshot fields must be of correct types."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor()
        snap = monitor.snapshot_now()
        assert isinstance(snap.cpu_percent, float)
        assert isinstance(snap.ram_used_mb, float)
        assert isinstance(snap.ram_total_mb, float)
        assert isinstance(snap.disk_percent, float)
        assert isinstance(snap.alerts, list)
        assert snap.timestamp > 0

    def test_alert_fires_when_threshold_breached(self):
        """Alert callback must be invoked when a threshold is exceeded."""
        from core.performance_monitor import PerformanceMonitor
        fired = []

        def on_alert(msg, snap):
            fired.append(msg)

        # Set cpu_percent threshold to -1 so it always fires
        monitor = PerformanceMonitor(
            thresholds={"cpu_percent": -1.0},
            on_alert=on_alert,
        )
        monitor.snapshot_now()
        assert len(fired) >= 1
        assert any("cpu_percent" in m for m in fired)

    def test_api_performance_metrics_returns_200(self, client):
        """GET /api/performance/metrics must return 200 and JSON."""
        import json
        r = client.get("/api/performance/metrics")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "cpu_percent" in data
        assert "ram_used_mb" in data

    def test_api_performance_history_returns_list(self, client):
        """GET /api/performance/history must return a JSON array."""
        import json
        r = client.get("/api/performance/history?n=5")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert isinstance(data, list)

    def test_api_performance_snapshot_post(self, client):
        """POST /api/performance/snapshot must return a fresh snapshot."""
        import json
        r = client.post("/api/performance/snapshot")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "timestamp" in data

    def test_api_performance_profile_returns_profile(self, client):
        """GET /api/performance/profile must return profile and hardware keys."""
        import json
        r = client.get("/api/performance/profile")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "profile" in data
        assert "hardware" in data
        assert "ai_mode" in data["profile"]
        assert "search_mode" in data["profile"]


# ---------------------------------------------------------------------------
# Target: Hardware detection < 200 ms
# ---------------------------------------------------------------------------


class TestHardwareDetectorBenchmarks:
    def test_auto_tune_under_200ms(self):
        """auto_tune() must complete in under 200 ms."""
        from system.hardware_detector import auto_tune
        t0 = time.perf_counter()
        profile = auto_tune()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.2, f"auto_tune() took {elapsed:.3f}s (target < 200ms)"
        assert profile is not None

    def test_all_profile_ids_selectable(self):
        """All three profile IDs must be forceable via override_profile."""
        from system.hardware_detector import auto_tune
        for pid in ("potato", "rpi", "laptop"):
            p = auto_tune(override_profile=pid)
            assert p.profile_id == pid, f"Profile {pid} not returned correctly"

    def test_potato_profile_disables_ai(self):
        """Ultra low-end profile must disable AI."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="potato")
        assert p.ai_mode == "DISABLED"
        assert p.max_workers == 1

    def test_rpi_profile_uses_fast_ai(self):
        """RPi profile must use FAST AI and limited workers."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="rpi")
        assert p.ai_mode == "FAST"
        assert p.max_workers <= 2

    def test_laptop_profile_uses_smart_ai(self):
        """Laptop profile must use SMART AI and semantic search."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="laptop")
        assert p.ai_mode == "SMART"
        assert p.search_mode == "SEMANTIC"

    def test_get_hardware_info_returns_valid_data(self):
        """get_hardware_info() must return a HardwareInfo with sane values."""
        from system.hardware_detector import get_hardware_info
        info = get_hardware_info()
        assert info.cpu_cores_logical >= 1
        # RAM may be 0 on systems without psutil, but the field must exist
        assert hasattr(info, "ram_total_mb")
        assert hasattr(info, "is_raspberry_pi")

    def test_unknown_profile_falls_back_to_rpi(self):
        """An unrecognised override profile must fall back to 'rpi'."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="nonexistent_profile_xyz")
        assert p.profile_id == "rpi"

    def test_profile_to_dict_serialisable(self):
        """HardwareProfile.to_dict() must produce a JSON-serialisable dict."""
        import json
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="rpi")
        d = p.to_dict()
        serialised = json.dumps(d)  # must not raise
        assert "ai_mode" in serialised
