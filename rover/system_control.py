"""System appliance controls for the rover laptop."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

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


def _format_uptime(seconds: float | int | None) -> str:
    """Format uptime like '4m', '2h 13m', or '1d 3h 20m'."""
    if seconds is None:
        return "Unknown"

    total_seconds = max(0, int(seconds))
    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24

    if days > 0:
        remaining_hours = hours % 24
        remaining_minutes = minutes % 60
        return f"{days}d {remaining_hours}h {remaining_minutes}m"

    if hours > 0:
        remaining_minutes = minutes % 60
        return f"{hours}h {remaining_minutes}m"

    return f"{minutes}m"


def get_resource_status() -> dict[str, Any]:
    """Return CPU/RAM/disk/uptime safely for the system status panel."""
    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    uptime_seconds: float | None = None
    errors: list[str] = []

    try:
        cpu_percent = round(float(psutil.cpu_percent(interval=None)), 1)
    except (psutil.Error, OSError, ValueError, TypeError) as err:
        errors.append(f"cpu: {err}")

    try:
        ram_percent = round(float(psutil.virtual_memory().percent), 1)
    except (psutil.Error, OSError, ValueError, TypeError) as err:
        errors.append(f"ram: {err}")

    try:
        disk_percent = round(float(psutil.disk_usage('/').percent), 1)
    except (psutil.Error, OSError, ValueError, TypeError) as err:
        errors.append(f"disk: {err}")

    try:
        uptime_seconds = round(float(time.time() - psutil.boot_time()), 1)
    except (psutil.Error, OSError, ValueError, TypeError) as err:
        errors.append(f"uptime: {err}")

    resource_status: dict[str, Any] = {
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "disk_percent": disk_percent,
        "uptime_seconds": uptime_seconds,
        "uptime_human": _format_uptime(uptime_seconds),
    }
    if errors:
        resource_status["error"] = "; ".join(errors)

    return resource_status


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
        "resources": get_resource_status(),
    }
