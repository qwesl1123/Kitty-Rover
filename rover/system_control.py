"""System appliance controls for the rover laptop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

BATTERY_PATH = Path("/sys/class/power_supply/BAT0")
CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREEN_OFF_SCRIPT = PROJECT_ROOT / "scripts" / "screen_off.sh"
SCREEN_ON_SCRIPT = PROJECT_ROOT / "scripts" / "screen_on.sh"
SCRIPT_TIMEOUT_SECONDS = 10


def _read_text(path: Path) -> str | None:
    """Read a sysfs text value, returning None when it is unavailable."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def get_battery_status() -> dict[str, Any]:
    """Return battery capacity and charging status without crashing on missing sysfs files."""
    if not BATTERY_PATH.exists():
        return {
            "available": False,
            "capacity_percent": None,
            "status": "unknown",
            "error": "BAT0 not found",
        }

    raw_capacity = _read_text(BATTERY_PATH / "capacity")
    raw_status = _read_text(BATTERY_PATH / "status")

    capacity_percent: int | None = None
    if raw_capacity is not None:
        try:
            capacity_percent = int(raw_capacity)
        except ValueError:
            capacity_percent = None

    return {
        "available": capacity_percent is not None or raw_status is not None,
        "capacity_percent": capacity_percent,
        "status": raw_status or "unknown",
    }


def get_cpu_temp() -> dict[str, Any]:
    """Return CPU temperature in Celsius without crashing when thermal data is unavailable."""
    raw_temp = _read_text(CPU_TEMP_PATH)
    if raw_temp is None:
        return {
            "available": False,
            "celsius": None,
            "error": "thermal_zone0 temp not found",
        }

    try:
        celsius = int(raw_temp) / 1000.0
    except ValueError:
        return {
            "available": False,
            "celsius": None,
            "error": "thermal_zone0 temp is invalid",
        }

    return {
        "available": True,
        "celsius": round(celsius, 1),
    }


def _run_screen_script(script_path: Path) -> dict[str, Any]:
    """Run a screen control script and return a JSON-safe result."""
    if not script_path.exists():
        return {
            "ok": False,
            "error": f"script not found: {script_path}",
        }

    if not script_path.is_file():
        return {
            "ok": False,
            "error": f"screen control path is not a file: {script_path}",
        }

    try:
        completed = subprocess.run(
            ["sudo", "-n", str(script_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"script timed out after {SCRIPT_TIMEOUT_SECONDS}s",
        }
    except OSError as err:
        return {
            "ok": False,
            "error": str(err),
        }

    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip() or completed.stdout.strip() or "screen script failed",
            "returncode": completed.returncode,
        }

    return {
        "ok": True,
        "returncode": completed.returncode,
    }


def screen_off() -> dict[str, Any]:
    """Blank the CLI laptop display via the rover screen-off script."""
    return _run_screen_script(SCREEN_OFF_SCRIPT)


def screen_on() -> dict[str, Any]:
    """Wake the CLI laptop display via the rover screen-on script."""
    return _run_screen_script(SCREEN_ON_SCRIPT)


def get_system_status() -> dict[str, Any]:
    """Return the appliance status shown in the web UI."""
    return {
        "ok": True,
        "battery": get_battery_status(),
        "cpu_temp": get_cpu_temp(),
    }
