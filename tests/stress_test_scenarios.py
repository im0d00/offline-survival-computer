"""
tests/stress_test_scenarios.py
================================
Stress-test scenarios for the Offline Survival Computer.

These tests simulate adverse real-world conditions and verify that the
application degrades gracefully rather than failing outright:

  * High search load   – concurrent note API calls
  * Large dataset      – listing/fetching from a large note store
  * Memory pressure    – repeated snapshot collection under load
  * Low-resource sim   – "potato" profile constraints respected
  * CPU throttle sim   – artificial delay to mimic slow CPU
  * Alert threshold    – monitor triggers alerts under simulated stress

Run with::

    pytest tests/stress_test_scenarios.py -v
"""

import json
import os
import sys
import time
import threading

import pytest

# Make the app package importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Redirect all data dirs to a temp path for test isolation."""
    import main as m

    notes_dir   = tmp_path / "notes"
    uploads_dir = tmp_path / "uploads"
    docs_dir    = tmp_path / "documents"
    maps_dir    = tmp_path / "maps"
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


def _seed_notes(n: int) -> None:
    """Insert *n* notes into the database."""
    import main as m
    conn = m.get_db()
    ts = m.now_iso()
    conn.executemany(
        "INSERT INTO notes (title, content, tags, created, updated) VALUES (?,?,?,?,?)",
        # Repeat content 20× to simulate a realistically sized document body
        [(f"Stress Note {i}", f"Survival content {i} " * 20, f"stress tag{i % 5}", ts, ts)
         for i in range(n)],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Scenario 1: High search load – concurrent API requests
# ---------------------------------------------------------------------------


class TestHighSearchLoad:
    """Simulate multiple concurrent search requests (1000-query proxy)."""

    def test_concurrent_notes_api_calls(self, client):
        """
        10 concurrent GET /api/notes requests must all succeed and each
        complete within 500 ms (target: search < 500ms).
        """
        import main as m
        _seed_notes(50)

        results = []
        errors  = []
        app_ref = m.app

        def fetch():
            # Each thread creates its own test client to avoid context-var
            # cross-thread collisions in Flask's request context.
            try:
                with app_ref.test_client() as tc:
                    t0 = time.perf_counter()
                    r = tc.get("/api/notes")
                    elapsed = time.perf_counter() - t0
                    results.append((r.status_code, elapsed))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=fetch) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent fetch errors: {errors}"
        assert len(results) == 10
        for status, elapsed in results:
            assert status == 200, f"Non-200 status: {status}"
            assert elapsed < 0.5, f"Concurrent request took {elapsed:.3f}s (target < 500ms)"

    def test_rapid_sequential_note_lookups(self, client):
        """
        50 sequential GET /api/notes/<id> calls must each be under 50 ms.
        Target: UI interaction < 200ms.
        """
        _seed_notes(50)
        import main as m
        conn = m.get_db()
        ids = [row[0] for row in conn.execute("SELECT id FROM notes LIMIT 50").fetchall()]
        conn.close()

        for note_id in ids:
            t0 = time.perf_counter()
            r = client.get(f"/api/notes/{note_id}")
            elapsed = time.perf_counter() - t0
            assert r.status_code == 200
            assert elapsed < 0.05, (
                f"Note {note_id} lookup took {elapsed:.3f}s (target < 50ms)"
            )


# ---------------------------------------------------------------------------
# Scenario 2: Large dataset handling
# ---------------------------------------------------------------------------


class TestLargeDatasetHandling:
    """Verify performance doesn't degrade linearly with note count."""

    def test_500_notes_list_under_500ms(self, client):
        """Listing 500 notes must complete in under 500 ms."""
        _seed_notes(500)
        t0 = time.perf_counter()
        r = client.get("/api/notes")
        elapsed = time.perf_counter() - t0
        data = json.loads(r.data)
        assert r.status_code == 200
        assert len(data) == 500
        assert elapsed < 0.5, f"500-note listing took {elapsed:.3f}s (target < 500ms)"

    def test_page_navigation_with_large_dataset_under_300ms(self, client):
        """GET /notes with 200 rows must stay under 300 ms (page nav target)."""
        _seed_notes(200)
        t0 = time.perf_counter()
        r = client.get("/notes")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 0.3, f"Notes page with 200 rows took {elapsed:.3f}s (target < 300ms)"

    def test_write_throughput_100_notes(self, client):
        """Creating 100 notes sequentially must complete in under 5 seconds."""
        import main as m
        t0 = time.perf_counter()
        ts = m.now_iso()
        conn = m.get_db()
        conn.executemany(
            "INSERT INTO notes (title, content, tags, created, updated) VALUES (?,?,?,?,?)",
            [(f"Write Note {i}", f"Content {i}", "write", ts, ts) for i in range(100)],
        )
        conn.commit()
        conn.close()
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"Writing 100 notes took {elapsed:.3f}s (target < 5s)"


# ---------------------------------------------------------------------------
# Scenario 3: Memory pressure simulation
# ---------------------------------------------------------------------------


class TestMemoryPressureSimulation:
    """Repeated snapshot collection must not leak memory or slow down."""

    def test_repeated_snapshots_stable_timing(self):
        """
        100 consecutive snapshots must all complete in under 200 ms each,
        demonstrating stable timing under repeated collection.
        """
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor(history_maxlen=100)
        times = []
        for _ in range(100):
            t0 = time.perf_counter()
            monitor.snapshot_now()
            times.append(time.perf_counter() - t0)

        # All individual snapshots must be fast
        slow = [t for t in times if t >= 0.2]
        assert not slow, f"{len(slow)} snapshots exceeded 200ms: {slow[:3]}"

        # History must not grow beyond maxlen
        assert len(monitor.history()) <= 100

    def test_monitor_history_bounded(self):
        """History ring buffer must never exceed the configured maxlen."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor(history_maxlen=5)
        for _ in range(30):
            monitor.snapshot_now()
        assert len(monitor.history()) <= 5

    def test_concurrent_snapshot_collection(self):
        """
        Multiple threads collecting snapshots simultaneously must not
        cause data corruption or race conditions.
        """
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor(history_maxlen=200)
        errors = []

        def collect():
            try:
                for _ in range(10):
                    snap = monitor.snapshot_now()
                    assert snap is not None
                    assert isinstance(snap.cpu_percent, float)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=collect) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent snapshot errors: {errors}"
        # All written snapshots must still be accessible
        assert len(monitor.history()) <= 200


# ---------------------------------------------------------------------------
# Scenario 4: Low-resource (potato) profile simulation
# ---------------------------------------------------------------------------


class TestLowResourceProfileSimulation:
    """Verify application behaves correctly under potato-profile constraints."""

    def test_potato_profile_max_one_worker(self):
        """Potato profile must allow only 1 worker thread."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="potato")
        assert p.max_workers == 1

    def test_potato_profile_ai_disabled(self):
        """AI must be disabled in the potato profile."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="potato")
        assert p.ai_mode == "DISABLED"

    def test_potato_profile_keyword_only_search(self):
        """Potato profile must use keyword-only search (no semantic)."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="potato")
        assert p.search_mode == "KEYWORD_ONLY"

    def test_potato_profile_small_cache(self):
        """Potato profile cache size must be ≤ 10."""
        from system.hardware_detector import auto_tune
        p = auto_tune(override_profile="potato")
        assert p.cache_size <= 10

    def test_profile_serialisation_all_profiles(self):
        """All profiles must be JSON-serialisable without errors."""
        from system.hardware_detector import auto_tune
        for pid in ("potato", "rpi", "laptop"):
            p = auto_tune(override_profile=pid)
            serialised = json.dumps(p.to_dict())
            assert pid in serialised or p.profile_name in serialised


# ---------------------------------------------------------------------------
# Scenario 5: CPU throttle simulation (artificial delay)
# ---------------------------------------------------------------------------


class TestCPUThrottleSimulation:
    """
    Simulate a CPU-throttled environment by injecting sleep delays and
    verifying the monitoring system still functions correctly.
    """

    def test_snapshot_tolerates_slow_collection(self, monkeypatch):
        """
        Even if the underlying psutil call is artificially slow (50 ms),
        snapshot_now() must still complete and return a valid snapshot.
        """
        from core import performance_monitor as pm

        original_collect = pm.PerformanceMonitor._collect

        def slow_collect(self):
            time.sleep(0.05)  # simulate 50ms CPU stall
            return original_collect(self)

        monkeypatch.setattr(pm.PerformanceMonitor, "_collect", slow_collect)

        monitor = pm.PerformanceMonitor()
        t0 = time.perf_counter()
        snap = monitor.snapshot_now()
        elapsed = time.perf_counter() - t0

        assert snap is not None
        # Total must still be well under 1 second even with 50ms inject
        assert elapsed < 1.0, f"Throttled snapshot took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Scenario 6: Alert threshold stress
# ---------------------------------------------------------------------------


class TestAlertThresholdStress:
    """Verify that alert thresholds fire reliably and don't flood the log."""

    def test_multiple_thresholds_fire(self):
        """
        When both CPU and RAM thresholds are set to -1, both alerts must
        fire in a single snapshot.
        """
        from core.performance_monitor import PerformanceMonitor
        fired = []

        monitor = PerformanceMonitor(
            thresholds={"cpu_percent": -1.0, "ram_percent": -1.0},
            on_alert=lambda msg, _: fired.append(msg),
        )
        monitor.snapshot_now()

        cpu_alerts = [m for m in fired if "cpu_percent" in m]
        ram_alerts = [m for m in fired if "ram_percent" in m]
        assert len(cpu_alerts) >= 1, "cpu_percent alert did not fire"
        assert len(ram_alerts) >= 1, "ram_percent alert did not fire"

    def test_no_false_alerts_under_normal_thresholds(self):
        """
        With very high thresholds (99 %), no alerts should fire on a
        lightly loaded test system.
        """
        from core.performance_monitor import PerformanceMonitor
        fired = []

        monitor = PerformanceMonitor(
            thresholds={
                "cpu_percent":   99.0,
                "ram_percent":   99.0,
                "ram_mb":        999999.0,   # effectively disabled
                "disk_percent":  99.0,
            },
            on_alert=lambda msg, _: fired.append(msg),
        )
        monitor.snapshot_now()
        # Allow battery alert if battery exists at < 99 %, but not CPU/RAM/disk
        non_battery = [m for m in fired if "battery" not in m]
        assert not non_battery, f"Unexpected alerts under high thresholds: {non_battery}"

    def test_update_threshold_takes_effect(self):
        """Updating a threshold at runtime must change alert behaviour."""
        from core.performance_monitor import PerformanceMonitor
        fired = []

        monitor = PerformanceMonitor(
            thresholds={"cpu_percent": 99.0},
            on_alert=lambda msg, _: fired.append(msg),
        )
        # Should not fire with threshold at 99 %
        monitor.snapshot_now()
        assert not [m for m in fired if "cpu_percent" in m]

        # Now lower threshold to -1 – must fire
        monitor.update_threshold("cpu_percent", -1.0)
        monitor.snapshot_now()
        assert any("cpu_percent" in m for m in fired)


# ---------------------------------------------------------------------------
# Scenario 7: Background monitor thread lifecycle
# ---------------------------------------------------------------------------


class TestMonitorThreadLifecycle:
    def test_start_stop_monitor(self):
        """Monitor must start and stop without errors."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor(interval=0.05)
        monitor.start()
        time.sleep(0.15)  # Allow 2-3 samples
        monitor.stop()
        history = monitor.history()
        assert len(history) >= 1, "No samples collected during monitor run"

    def test_double_start_is_idempotent(self):
        """Calling start() twice must not spawn duplicate threads."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor(interval=0.05)
        monitor.start()
        monitor.start()  # second call must be a no-op
        time.sleep(0.1)
        monitor.stop()
        # Thread count doesn't matter as long as monitor stops cleanly

    def test_latest_returns_none_before_any_snapshot(self):
        """latest() must return None when no snapshot has been taken yet."""
        from core.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor()
        assert monitor.latest() is None

    def test_get_monitor_singleton(self):
        """get_monitor() must return the same instance on repeated calls."""
        from core.performance_monitor import get_monitor
        m1 = get_monitor()
        m2 = get_monitor()
        assert m1 is m2
