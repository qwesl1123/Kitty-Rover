import time

_last_drive = {"left": 0, "right": 0, "time": 0}


def drive(left: int, right: int):
    global _last_drive

    left = max(-255, min(255, int(left)))
    right = max(-255, min(255, int(right)))

    _last_drive = {
        "left": left,
        "right": right,
        "time": time.time(),
    }

    print(f"[drive] DRIVE {left} {right}")


def stop():
    drive(0, 0)


def get_last_drive():
    return _last_drive
