"""System appliance controls for the rover laptop."""

from __future__ import annotations

import subprocess
import socket
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


def get_network_status() -> dict[str, Any]:
    """Return hostname and network interface details for rover connectivity checks."""
    hostname = socket.gethostname()
    interfaces: list[dict[str, Any]] = []
    lan_ipv4: list[str] = []
    tailscale_ipv4: str | None = None
    tailscale_available = False
    errors: list[str] = []

    try:
        net_addrs = psutil.net_if_addrs()
        net_stats = psutil.net_if_stats()
    except (psutil.Error, OSError, ValueError, TypeError) as err:
        return {
            "hostname": hostname,
            "interfaces": [],
            "lan_ipv4": [],
            "tailscale_ipv4": None,
            "tailscale_available": False,
            "error": f"network introspection failed: {err}",
        }

    for interface_name, interface_addresses in net_addrs.items():
        ipv4_addresses: list[str] = []
        for addr in interface_addresses:
            if addr.family == socket.AF_INET and addr.address:
                ipv4_addresses.append(addr.address)

        is_up = bool(net_stats.get(interface_name).isup) if interface_name in net_stats else False
        interfaces.append(
            {
                "name": interface_name,
                "ipv4": ipv4_addresses,
                "is_up": is_up,
            }
        )

        if interface_name == "tailscale0" and ipv4_addresses:
            tailscale_ipv4 = ipv4_addresses[0]
            tailscale_available = True

        for ip in ipv4_addresses:
            if ip != "127.0.0.1":
                lan_ipv4.append(ip)

    if tailscale_ipv4 and tailscale_ipv4 in lan_ipv4:
        lan_ipv4 = [ip for ip in lan_ipv4 if ip != tailscale_ipv4]

    if not tailscale_ipv4:
        try:
            completed = subprocess.run(
                ["tailscale", "ip", "-4"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if completed.returncode == 0:
                parsed_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
                if parsed_lines:
                    tailscale_ipv4 = parsed_lines[0]
                    tailscale_available = True
                    if tailscale_ipv4 in lan_ipv4:
                        lan_ipv4 = [ip for ip in lan_ipv4 if ip != tailscale_ipv4]
            else:
                tailscale_available = False
        except (subprocess.TimeoutExpired, OSError) as err:
            errors.append(f"tailscale command unavailable: {err}")

    network_status: dict[str, Any] = {
        "hostname": hostname,
        "interfaces": interfaces,
        "lan_ipv4": lan_ipv4,
        "tailscale_ipv4": tailscale_ipv4,
        "tailscale_available": tailscale_available,
        "error": "; ".join(errors) if errors else None,
    }
    return network_status


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




def get_face_screen_status() -> dict[str, Any]:
    """Return rover-face-screen.service activity state in a JSON-safe shape."""
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", "rover-face-screen.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"running": False, "state": "unknown", "error": "systemctl is-active timed out"}
    except OSError as err:
        return {"running": False, "state": "unknown", "error": str(err)}

    state = (completed.stdout or completed.stderr or "unknown").strip().lower() or "unknown"
    running = state == "active"
    if state not in {"active", "inactive", "failed"}:
        state = "unknown"

    return {"running": running, "state": state}


def _run_face_screen_action(action: str) -> dict[str, Any]:
    """Start or stop rover-face-screen.service and return a JSON-safe result."""
    try:
        completed = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", action, "rover-face-screen.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"face screen {action} timed out after 10s"}
    except OSError as err:
        return {"ok": False, "error": str(err)}

    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip() or completed.stdout.strip() or f"face screen {action} failed",
            "returncode": completed.returncode,
        }

    return {
        "ok": True,
        "returncode": completed.returncode,
        "face_screen": get_face_screen_status(),
    }


def face_screen_start() -> dict[str, Any]:
    """Start rover-face-screen.service."""
    return _run_face_screen_action("start")


def face_screen_stop() -> dict[str, Any]:
    """Stop rover-face-screen.service."""
    return _run_face_screen_action("stop")
def get_system_status() -> dict[str, Any]:
    """Return the appliance status shown in the web UI."""
    return {
        "ok": True,
        "battery": get_battery_status(),
        "cpu_temp": get_cpu_temp(),
        "resources": get_resource_status(),
        "network": get_network_status(),
        "face_screen": get_face_screen_status(),
    }
