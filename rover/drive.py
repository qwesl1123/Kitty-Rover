import threading
import time
from typing import Callable, Optional

WATCHDOG_TIMEOUT_SECONDS = 0.5

_lock = threading.Lock()
_state = {
    "left": 0,
    "right": 0,
    "time": time.monotonic(),
}
_status_callback: Optional[Callable[[dict], None]] = None
_watchdog_started = False


def _clamp_speed(value: int) -> int:
    return max(-255, min(255, int(value)))


def _status_from_state(now: Optional[float] = None) -> dict:
    if now is None:
        now = time.monotonic()

    return {
        "left": _state["left"],
        "right": _state["right"],
        "last_command_age": max(0.0, now - _state["time"]),
    }


def get_drive_status() -> dict:
    with _lock:
        return _status_from_state()


def get_last_drive() -> dict:
    status = get_drive_status()
    return {
        "left": status["left"],
        "right": status["right"],
        "time": time.time() - status["last_command_age"],
    }


def set_status_callback(callback: Callable[[dict], None]) -> None:
    global _status_callback
    _status_callback = callback


def _emit_status(status: dict) -> None:
    if _status_callback is not None:
        _status_callback(status)


def _set_drive(left: int, right: int, *, log_when_unchanged: bool = False) -> dict:
    left = _clamp_speed(left)
    right = _clamp_speed(right)

    with _lock:
        changed = _state["left"] != left or _state["right"] != right
        _state["left"] = left
        _state["right"] = right
        _state["time"] = time.monotonic()
        status = _status_from_state(_state["time"])

    if changed or log_when_unchanged:
        print(f"[drive] DRIVE {left} {right}")

    _emit_status(status)
    return status


def drive(left: int, right: int) -> dict:
    return _set_drive(left, right)


def stop() -> dict:
    return _set_drive(0, 0)


def start_watchdog(start_background_task=None, sleep_func=None) -> None:
    global _watchdog_started

    if sleep_func is None:
        sleep_func = time.sleep

    with _lock:
        if _watchdog_started:
            return
        _watchdog_started = True

    def watchdog_loop():
        while True:
            sleep_func(WATCHDOG_TIMEOUT_SECONDS / 2)
            status_to_emit = None

            with _lock:
                moving = _state["left"] != 0 or _state["right"] != 0
                expired = time.monotonic() - _state["time"] > WATCHDOG_TIMEOUT_SECONDS

                if moving and expired:
                    _state["left"] = 0
                    _state["right"] = 0
                    _state["time"] = time.monotonic()
                    status_to_emit = _status_from_state(_state["time"])

            if status_to_emit is not None:
                print("[drive] DRIVE 0 0")
                _emit_status(status_to_emit)

    if start_background_task is not None:
        start_background_task(watchdog_loop)
    else:
        thread = threading.Thread(target=watchdog_loop, daemon=True)
        thread.start()
