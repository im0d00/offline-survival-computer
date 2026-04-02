"""
system/hardware_detector.py
============================
Hardware detection and automatic performance-profile selection for the
Offline Survival Computer.

Detects available RAM, CPU cores and architecture, then selects the most
appropriate performance profile so the application runs optimally on
everything from a Raspberry Pi 3B+ to a modern laptop.

Performance profiles
--------------------
``potato``  – 512 MB RAM, AI disabled, keyword-only search
``rpi``     – 1 GB RAM (Raspberry Pi 3B+), FAST AI, keyword search
``laptop``  – 4 GB+ RAM, SMART AI, semantic search

The ``auto_tune()`` function returns a ``HardwareProfile`` that contains
ready-to-use configuration values for the rest of the application.
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass, asdict
from typing import Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Profile definitions  (mirrors the PROFILES dict from the issue)
# ---------------------------------------------------------------------------

PROFILES = {
    "potato": {
        "name": "Ultra Low-End (512 MB RAM)",
        "ram_threshold_mb": 512,
        "ai_mode": "DISABLED",
        "search_mode": "KEYWORD_ONLY",
        "max_workers": 1,
        "cache_size": 10,
        "db_cache_kb": 512,
        "model_size": "none",
    },
    "rpi": {
        "name": "Raspberry Pi (1 GB RAM)",
        "ram_threshold_mb": 1024,
        "ai_mode": "FAST",
        "search_mode": "KEYWORD",
        "max_workers": 2,
        "cache_size": 50,
        "db_cache_kb": 2048,
        "model_size": "small",   # e.g. 3B quantised GGUF
    },
    "laptop": {
        "name": "Modern Laptop (4 GB+ RAM)",
        "ram_threshold_mb": 4096,
        "ai_mode": "SMART",
        "search_mode": "SEMANTIC",
        "max_workers": 4,
        "cache_size": 200,
        "db_cache_kb": 8192,
        "model_size": "large",   # e.g. 7B or 13B quantised GGUF
    },
}

# Minimum supported hardware
MIN_RAM_MB = 512
MIN_DISK_MB = 500

# RAM thresholds used to select a performance profile
_POTATO_THRESHOLD_MB = 768    # below this → ultra low-end (potato)
_RPI_THRESHOLD_MB    = 2048   # below this → RPi class


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HardwareInfo:
    """Raw hardware information collected from the host."""

    # CPU
    cpu_cores_physical: int = 1
    cpu_cores_logical: int = 1
    cpu_arch: str = "unknown"
    cpu_freq_max_mhz: Optional[float] = None

    # Memory
    ram_total_mb: float = 0.0
    ram_available_mb: float = 0.0

    # Disk (root partition)
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0

    # Platform
    os_name: str = ""
    os_version: str = ""
    is_raspberry_pi: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HardwareProfile:
    """
    Selected performance profile plus derived configuration values that the
    application should use at runtime.
    """

    profile_id: str = "rpi"
    profile_name: str = ""

    # AI configuration
    ai_mode: str = "FAST"           # DISABLED | FAST | SMART
    model_size: str = "small"

    # Search configuration
    search_mode: str = "KEYWORD"    # KEYWORD_ONLY | KEYWORD | SEMANTIC

    # Concurrency
    max_workers: int = 2

    # Caching
    cache_size: int = 50            # number of cached query results
    db_cache_kb: int = 2048         # SQLite page-cache size in KB

    # Resource limits
    ram_total_mb: float = 0.0
    meets_minimum_requirements: bool = True
    warnings: list = None           # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _detect_raspberry_pi() -> bool:
    """Return True when running on a Raspberry Pi."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            content = f.read().lower()
        return "raspberry pi" in content or "bcm2" in content
    except OSError:
        pass
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        pass
    return False


def _get_hardware_info() -> HardwareInfo:
    """Collect raw hardware information from the host."""
    info = HardwareInfo()

    # CPU
    info.cpu_arch = platform.machine()
    if _PSUTIL_AVAILABLE:
        info.cpu_cores_physical = psutil.cpu_count(logical=False) or 1
        info.cpu_cores_logical = psutil.cpu_count(logical=True) or 1
        freq = psutil.cpu_freq()
        if freq:
            info.cpu_freq_max_mhz = freq.max or freq.current
        # Memory
        mem = psutil.virtual_memory()
        info.ram_total_mb = mem.total / (1024 ** 2)
        info.ram_available_mb = mem.available / (1024 ** 2)
        # Disk
        try:
            disk = psutil.disk_usage("/")
            info.disk_total_gb = disk.total / (1024 ** 3)
            info.disk_free_gb = disk.free / (1024 ** 3)
        except Exception:
            pass
    else:
        # Fallback: read /proc/meminfo on Linux
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        info.ram_total_mb = int(line.split()[1]) / 1024
                    elif line.startswith("MemAvailable:"):
                        info.ram_available_mb = int(line.split()[1]) / 1024
        except OSError:
            pass
        try:
            info.cpu_cores_logical = os.cpu_count() or 1
            info.cpu_cores_physical = info.cpu_cores_logical
        except Exception:
            pass

    # Platform
    info.os_name = platform.system()
    info.os_version = platform.version()
    info.is_raspberry_pi = _detect_raspberry_pi()

    return info


# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------

def _select_profile(info: HardwareInfo) -> str:
    """Choose the most appropriate profile ID for the given hardware."""
    ram = info.ram_total_mb

    if ram < _POTATO_THRESHOLD_MB:   # less than 768 MB → ultra low-end
        return "potato"
    if ram < _RPI_THRESHOLD_MB:      # 768 MB – 2 GB → RPi class
        return "rpi"
    return "laptop"                  # 2 GB+ → modern laptop


def auto_tune(override_profile: Optional[str] = None) -> HardwareProfile:
    """
    Detect host hardware and return the best ``HardwareProfile``.

    Parameters
    ----------
    override_profile:
        Force a specific profile ID (``"potato"``, ``"rpi"``, ``"laptop"``)
        instead of auto-detecting.  Useful for testing or explicit user choice.
    """
    info = _get_hardware_info()

    profile_id = override_profile or _select_profile(info)
    if profile_id not in PROFILES:
        logger.warning(
            "Unknown profile '%s', falling back to 'rpi'", profile_id
        )
        profile_id = "rpi"

    raw = PROFILES[profile_id]
    warnings: list = []

    # Check minimum requirements
    meets_min = True
    if info.ram_total_mb > 0 and info.ram_total_mb < MIN_RAM_MB:
        meets_min = False
        warnings.append(
            f"RAM {info.ram_total_mb:.0f} MB is below the minimum {MIN_RAM_MB} MB"
        )
    disk_free_mb = info.disk_free_gb * 1024
    if disk_free_mb > 0 and disk_free_mb < MIN_DISK_MB:
        warnings.append(
            f"Free disk {disk_free_mb:.0f} MB is below the recommended {MIN_DISK_MB} MB"
        )

    # Clamp max_workers to the number of logical CPUs available
    max_workers = min(raw["max_workers"], info.cpu_cores_logical)

    profile = HardwareProfile(
        profile_id=profile_id,
        profile_name=raw["name"],
        ai_mode=raw["ai_mode"],
        model_size=raw["model_size"],
        search_mode=raw["search_mode"],
        max_workers=max_workers,
        cache_size=raw["cache_size"],
        db_cache_kb=raw["db_cache_kb"],
        ram_total_mb=info.ram_total_mb,
        meets_minimum_requirements=meets_min,
        warnings=warnings,
    )

    logger.info(
        "Hardware profile selected: %s (RAM=%.0f MB, cores=%d, AI=%s)",
        profile_id,
        info.ram_total_mb,
        info.cpu_cores_logical,
        profile.ai_mode,
    )
    for w in warnings:
        logger.warning("Hardware warning: %s", w)

    return profile


def get_hardware_info() -> HardwareInfo:
    """Public accessor for raw hardware information."""
    return _get_hardware_info()
