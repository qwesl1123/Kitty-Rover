# serial_link.py — owns the USB serial link to the ESP32 motor controller.
#
# Place this inside the rover/ package (next to drive.py). It is transport-only:
# it reads the *current* drive state from drive.py at a fixed rate and streams
# "M <left> <right>\n" to the firmware. It never sets drive state itself, so
# drive.py stays serial-agnostic.
#
# Three independent safety layers stay intact:
#   1. ESP32 firmware deadman (500 ms)  -> stops if the serial stream dies.
#   2. drive.py watchdog (500 ms)       -> zeroes logical state if the web/UI
#                                          stops sending; we then stream zeros.
#   3. This loop streams continuously    -> keeps layer 1 fed while driving.

import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:                       # pyserial not installed
    serial = None
    list_ports = None

# Works whether this file sits in the rover/ package or beside drive.py at root.
try:
    from rover.drive import get_drive_status
except ImportError:
    from drive import get_drive_status

# ---------------------------------------------------------------- config ----
BAUD = 115200
SEND_HZ = 20                              # 50 ms; well under the 500 ms deadman

# Fixed port -- set to None to auto-detect, or a string to pin it:
#   "/dev/ttyACM0"  (this rover laptop)   |   "COM3" (Windows)
PREFERRED_PORT = "/dev/ttyACM0"

# Substrings used to recognise USB-UART bridges during auto-detect (fallback).
_USB_HINTS = ("CP210", "CH340", "CH910", "USB-SERIAL", "USB SERIAL",
              "SINGLE SERIAL", "SILICON LABS", "WCHUSB", "WCH")

# --------------------------------------------------------------- internals ---
_ser = None
_thread = None
_running = False
_lock = threading.Lock()
_connected = False


def is_connected() -> bool:
    with _lock:
        return _connected


def _find_port():
    if PREFERRED_PORT:
        return PREFERRED_PORT
    if not list_ports:
        return None
    ports = list(list_ports.comports())
    for p in ports:
        blob = " ".join(filter(None, [p.device, p.description,
                                      getattr(p, "manufacturer", ""), p.hwid])).upper()
        if any(h in blob for h in _USB_HINTS):
            return p.device
    if len(ports) == 1:                   # unambiguous single port
        return ports[0].device
    return None


def _open() -> bool:
    global _ser, _connected
    if serial is None:
        print("[serial_link] pyserial not installed -- motor output disabled")
        return False
    port = _find_port()
    if not port:
        print("[serial_link] no ESP32 serial port found (set PREFERRED_PORT)")
        return False
    try:
        _ser = serial.Serial(port, BAUD, timeout=0.1)
        time.sleep(2)                     # ESP32 auto-resets when the port opens
        with _lock:
            _connected = True
        print(f"[serial_link] connected on {port}")
        return True
    except Exception as e:
        print(f"[serial_link] open failed on {port}: {e}")
        _ser = None
        return False


def _send_loop():
    global _ser, _connected
    period = 1.0 / SEND_HZ
    backoff = 1.0
    while _running:
        if _ser is None:
            if not _open():
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            backoff = 1.0
        status = get_drive_status()
        left = int(status["left"])
        right = int(status["right"])
        try:
            _ser.write(f"M {left} {right}\n".encode())
        except Exception as e:
            print(f"[serial_link] write failed: {e} -- will reconnect")
            try:
                _ser.close()
            except Exception:
                pass
            _ser = None
            with _lock:
                _connected = False
            continue
        time.sleep(period)


# ------------------------------------------------------------------ public ---
def start(start_background_task=None) -> None:
    """Start streaming drive state to the ESP32. A daemon thread is fine in
    Socket.IO threading mode (your async_mode); pass start_background_task only
    if you later switch to eventlet/gevent."""
    global _thread, _running
    if _running:
        return
    _running = True
    if start_background_task is not None:
        start_background_task(_send_loop)
    else:
        _thread = threading.Thread(target=_send_loop, daemon=True)
        _thread.start()


def stop() -> None:
    """Stop the loop and leave the motors commanded to zero."""
    global _running, _ser
    _running = False
    time.sleep(0.1)
    if _ser is not None:
        try:
            _ser.write(b"M 0 0\n")
            _ser.close()
        except Exception:
            pass
        _ser = None
