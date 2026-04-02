"""
core/performance_monitor.py
============================
Real-time performance monitoring for the Offline Survival Computer.

Collects CPU, RAM, disk and (optionally) battery metrics, maintains a
rolling history, fires threshold-based alerts, and logs all readings so
that degradation can be diagnosed after the fact.

Design goals
------------
* Zero external dependencies beyond ``psutil`` (already in requirements).
* Thread-safe: a background daemon thread samples metrics on a configurable
  interval so the Flask request thread is never blocked.
* Lightweight: only the most-recent N samples are kept in memory; older
  entries are pruned automatically.
* Alert thresholds are aligned with the performance targets in the issue:
    - idle CPU   < 2 %
    - idle RAM   < 100 MB
    - active RAM < 300 MB (FAST mode) / 500 MB (SMART mode)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable, Deque, Dict, List, Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert thresholds (can be overridden at runtime)
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, float] = {
    "cpu_percent":      80.0,   # warn when CPU > 80 %
    "ram_percent":      85.0,   # warn when RAM > 85 %
    "ram_mb":          300.0,   # warn when used RAM > 300 MB (FAST-mode target)
    "disk_percent":    90.0,    # warn when disk > 90 % full
    "battery_percent": 20.0,    # warn when battery < 20 %
}

# Number of samples retained in the rolling history
HISTORY_MAXLEN = 120  # ~2 min at 1-second interval


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PerformanceSnapshot:
    """A single point-in-time reading of all monitored metrics."""

    timestamp: float = field(default_factory=time.time)

    # CPU
    cpu_percent: float = 0.0          # overall utilisation %
    cpu_freq_mhz: Optional[float] = None  # current clock speed

    # Memory
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_available_mb: float = 0.0
    ram_percent: float = 0.0

    # Disk (root partition)
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0

    # Battery (None when not applicable)
    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None

    # Alerts triggered at this snapshot
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Monitor class
# ---------------------------------------------------------------------------

class PerformanceMonitor:
    """
    Collects system metrics at a regular interval and maintains a rolling
    history of ``PerformanceSnapshot`` objects.

    Usage::

        monitor = PerformanceMonitor(interval=1.0)
        monitor.start()
        ...
        snapshot = monitor.latest()
        history  = monitor.history()
        monitor.stop()
    """

    def __init__(
        self,
        interval: float = 1.0,
        history_maxlen: int = HISTORY_MAXLEN,
        thresholds: Optional[Dict[str, float]] = None,
        on_alert: Optional[Callable[[str, PerformanceSnapshot], None]] = None,
    ) -> None:
        self._interval = interval
        self._history: Deque[PerformanceSnapshot] = deque(maxlen=history_maxlen)
        self._thresholds = dict(THRESHOLDS)
        if thresholds:
            self._thresholds.update(thresholds)
        self._on_alert = on_alert or self._default_alert_handler
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background sampling thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="perf-monitor", daemon=True
        )
        self._thread.start()
        logger.info("PerformanceMonitor started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        """Stop the background sampling thread gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
        logger.info("PerformanceMonitor stopped")

    def latest(self) -> Optional[PerformanceSnapshot]:
        """Return the most-recent snapshot, or ``None`` if no data yet."""
        with self._lock:
            return self._history[-1] if self._history else None

    def history(self, n: Optional[int] = None) -> List[PerformanceSnapshot]:
        """Return up to *n* most-recent snapshots (all if *n* is None)."""
        with self._lock:
            items = list(self._history)
        return items[-n:] if n else items

    def snapshot_now(self) -> PerformanceSnapshot:
        """Take an *immediate* one-off snapshot without waiting for the loop."""
        snap = self._collect()
        self._check_thresholds(snap)
        with self._lock:
            self._history.append(snap)
        return snap

    def update_threshold(self, metric: str, value: float) -> None:
        """Update a single alert threshold at runtime."""
        self._thresholds[metric] = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Background loop: collect → check → store → sleep."""
        while self._running:
            try:
                snap = self._collect()
                self._check_thresholds(snap)
                with self._lock:
                    self._history.append(snap)
                self._log_metrics(snap)
            except Exception as exc:  # pragma: no cover – defensive
                logger.warning("PerformanceMonitor collection error: %s", exc)
            time.sleep(self._interval)

    def _collect(self) -> PerformanceSnapshot:
        """Read current system metrics and return a ``PerformanceSnapshot``."""
        snap = PerformanceSnapshot()

        if not _PSUTIL_AVAILABLE:
            return snap

        # CPU
        snap.cpu_percent = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        if freq:
            snap.cpu_freq_mhz = freq.current

        # Memory
        mem = psutil.virtual_memory()
        snap.ram_total_mb = mem.total / (1024 ** 2)
        snap.ram_used_mb = mem.used / (1024 ** 2)
        snap.ram_available_mb = mem.available / (1024 ** 2)
        snap.ram_percent = mem.percent

        # Disk (root partition "/" – works on Linux)
        try:
            disk = psutil.disk_usage("/")
            snap.disk_total_gb = disk.total / (1024 ** 3)
            snap.disk_used_gb = disk.used / (1024 ** 3)
            snap.disk_free_gb = disk.free / (1024 ** 3)
            snap.disk_percent = disk.percent
        except Exception:
            pass

        # Battery (optional – not present on all devices)
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                snap.battery_percent = bat.percent
                snap.battery_plugged = bat.power_plugged
        except Exception:
            pass

        return snap

    def _check_thresholds(self, snap: PerformanceSnapshot) -> None:
        """Populate ``snap.alerts`` and invoke ``on_alert`` for each breach."""
        checks = [
            ("cpu_percent",    snap.cpu_percent,    ">"),
            ("ram_percent",    snap.ram_percent,     ">"),
            ("ram_mb",         snap.ram_used_mb,     ">"),
            ("disk_percent",   snap.disk_percent,    ">"),
        ]
        if snap.battery_percent is not None:
            checks.append(("battery_percent", snap.battery_percent, "<"))

        for metric, value, direction in checks:
            threshold = self._thresholds.get(metric)
            if threshold is None:
                continue
            breached = (value > threshold) if direction == ">" else (value < threshold)
            if breached:
                msg = (
                    f"ALERT: {metric}={value:.1f} "
                    f"{'exceeds' if direction == '>' else 'below'} "
                    f"threshold {threshold}"
                )
                snap.alerts.append(msg)
                self._on_alert(msg, snap)

    @staticmethod
    def _default_alert_handler(message: str, _snap: PerformanceSnapshot) -> None:
        logger.warning(message)

    @staticmethod
    def _log_metrics(snap: PerformanceSnapshot) -> None:
        logger.debug(
            "cpu=%.1f%% ram=%.0fMB(%.1f%%) disk=%.1f%% bat=%s",
            snap.cpu_percent,
            snap.ram_used_mb,
            snap.ram_percent,
            snap.disk_percent,
            f"{snap.battery_percent:.0f}%" if snap.battery_percent is not None else "N/A",
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_monitor: Optional[PerformanceMonitor] = None


def get_monitor() -> PerformanceMonitor:
    """Return (and lazily create) the module-level singleton monitor."""
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor
