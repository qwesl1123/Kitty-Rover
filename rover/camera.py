"""Single-reader, multi-subscriber camera frame broker.

Background
----------
The previous LazyCamera let multiple clients share one cv2.VideoCapture but
had each client call cap.read() independently under a shared lock. That
produced two compounding bugs:

  1. FPS splitting: every subscriber (browser, future detector, etc.) ran
     its own cap.read() loop, so frames were divided among them instead of
     replicated -- two viewers each got ~half the camera's real frame rate.

  2. Stuck active_clients / camera LED on: per-client generators blocked
     inside cap.read() under the lock couldn't observe a stop signal
     quickly, so the active-clients counter drifted upward and the camera
     never released.

Design
------
Each FrameBroker owns one cv2.VideoCapture and runs a single background
reader thread. The reader grabs frames at the configured FPS, JPEG-encodes
each once, and publishes it under a Condition variable. Subscribers wait
on the Condition for new frames; when the last subscriber leaves the reader
exits and the camera is released. A subsequent subscriber transparently
restarts the reader.

This keeps the public API identical -- app.py still calls camera.stream()
and back_camera.stream() unchanged -- and trivially scales to a third
subscriber (e.g. the future on-laptop cat detector) without further work.

Lock layering (read this before editing)
----------------------------------------
There are two locks:

  _state_lock (== _frame_cond's lock)
      Short critical sections only. Protects: subscriber count, should_run
      flag, latest_jpeg, frame_seq, and the reader_thread reference. Never
      held across cap.read(), JPEG encode, sleep, or network IO.

  _start_lock
      Serializes _ensure_reader. Held while joining an outgoing reader
      thread and starting its replacement -- the only situation where we
      genuinely need to block for non-trivial time.

Acquisition order is always _start_lock first, then _state_lock. The reader
thread never acquires _start_lock, so the two locks form a DAG (no cycle,
no deadlock).
"""

import threading
import time
from typing import Optional

import cv2

from config import (
    BACK_CAMERA_DEVICE,
    BACK_CAMERA_FPS,
    BACK_CAMERA_HEIGHT,
    BACK_CAMERA_WIDTH,
    BACK_JPEG_QUALITY,
    CAMERA_DEVICE,
    CAMERA_FPS,
    CAMERA_HEIGHT,
    CAMERA_WIDTH,
    JPEG_QUALITY,
)


# How long a subscriber may block waiting for a new frame before re-checking
# state. Defends against a dead reader (driver hang, unplug) leaving a
# subscriber waiting forever.
_SUBSCRIBER_WAIT_TIMEOUT_S = 1.0

# How long _ensure_reader waits for a previous reader to fully exit before
# starting a replacement. cap.release() is usually instant but can take a
# beat on some USB cameras after a fault.
_READER_JOIN_TIMEOUT_S = 2.0

# How long the reader sleeps after a transient cap.read() failure before
# trying again. Keeps the reader from busy-looping on a flaky camera.
_READ_FAILURE_RETRY_S = 0.1

# Number of frames to read and discard after opening the camera to let the
# sensor auto-exposure settle.  USB cameras commonly produce 1-5 black frames
# immediately after open.
_WARMUP_FRAMES = 5

# Minimum mean pixel brightness (0-255) for a frame to count as non-black
# during the warmup phase.
_WARMUP_MIN_BRIGHTNESS = 10


class FrameBroker:
    """One-producer, many-consumer broker for an OpenCV capture device.

    Subscribers obtain MJPEG byte streams via .stream(). The broker starts
    its reader thread on first subscription and stops it on last
    unsubscription; restarting is transparent.
    """

    def __init__(self, device, width, height, fps, jpeg_quality, label="camera"):
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._jpeg_quality = jpeg_quality
        self._label = label

        self._state_lock = threading.Lock()
        self._frame_cond = threading.Condition(self._state_lock)
        self._start_lock = threading.Lock()

        self._subscribers = 0
        self._should_run = False
        self._explicitly_stopped = False
        self._reader_thread: Optional[threading.Thread] = None
        self._latest_jpeg: Optional[bytes] = None
        self._frame_seq = 0
        self._warmup_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stream(self):
        """Yield multipart MJPEG chunks for a Flask streaming Response.

        Each subscriber sees the latest frame the reader has produced; slow
        clients silently skip frames rather than building a backlog. The
        generator's finally block deregisters the subscriber even when the
        client closes the connection mid-yield (BrokenPipeError).
        """
        self._subscribe()
        self._warmup_event.wait(timeout=2.0)
        last_seen = 0
        try:
            while True:
                jpeg_bytes: Optional[bytes] = None

                with self._frame_cond:
                    self._frame_cond.wait_for(
                        lambda: self._frame_seq != last_seen or not self._should_run,
                        timeout=_SUBSCRIBER_WAIT_TIMEOUT_S,
                    )
                    if not self._should_run and self._frame_seq == last_seen:
                        # Reader stopped and there is no fresh frame to send.
                        break
                    if self._frame_seq != last_seen and self._latest_jpeg is not None:
                        jpeg_bytes = self._latest_jpeg
                        last_seen = self._frame_seq
                    # else: spurious wakeup or timeout with no new frame --
                    # loop and wait again. Bounded by _SUBSCRIBER_WAIT_TIMEOUT_S.

                if jpeg_bytes is not None:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + jpeg_bytes
                        + b"\r\n"
                    )
        finally:
            self._unsubscribe()

    def stop(self):
        """Explicitly stop the reader and release the camera.

        Idempotent.  Forces _should_run False and wakes all waiters
        regardless of subscriber count so the reader exits and calls
        cap.release() in its finally block.  Only acquires _state_lock
        (never _start_lock) so it completes instantly and cannot deadlock
        with _ensure_reader.
        """
        with self._frame_cond:
            if not self._should_run:
                return
            self._explicitly_stopped = True
            self._should_run = False
            self._subscribers = 0
            self._frame_cond.notify_all()
        print(f"[{self._label}] explicit stop() called; reader will exit", flush=True)

    def start(self):
        """Explicitly start the reader, clearing any previous stop.

        Idempotent if the reader is already healthy.  This does NOT add a
        subscriber — the reader runs but produces no MJPEG output until a
        subscriber connects via stream().  Use it to pre-warm the camera
        so the first stream() subscriber gets frames immediately.
        """
        with self._state_lock:
            self._explicitly_stopped = False
        self._warmup_event.clear()
        self._ensure_reader()

    @property
    def is_active(self):
        """Whether the reader is running."""
        with self._state_lock:
            return (
                self._should_run
                and self._reader_thread is not None
                and self._reader_thread.is_alive()
            )

    # ------------------------------------------------------------------
    # Subscription bookkeeping
    # ------------------------------------------------------------------

    def _subscribe(self):
        with self._state_lock:
            self._explicitly_stopped = False
            self._subscribers += 1
            count = self._subscribers
        print(f"[{self._label}] subscriber added (now {count})", flush=True)
        self._ensure_reader()

    def _unsubscribe(self):
        last = False
        with self._state_lock:
            self._subscribers -= 1
            if self._subscribers <= 0:
                self._subscribers = 0
                self._should_run = False
                last = True
            count = self._subscribers
            # Wake any subscriber still waiting so they re-check state and
            # exit cleanly when the reader winds down.
            self._frame_cond.notify_all()
        if last:
            print(f"[{self._label}] last subscriber left; reader will exit", flush=True)
        else:
            print(f"[{self._label}] subscriber removed (now {count})", flush=True)

    # ------------------------------------------------------------------
    # Reader thread lifecycle
    # ------------------------------------------------------------------

    def _ensure_reader(self):
        """Make sure a healthy reader thread is running. Idempotent.

        If the reader is alive AND should_run is True, this is a no-op. If
        the reader is missing, dead, or currently shutting down, this joins
        the old thread (briefly) and starts a fresh one.
        """
        with self._start_lock:
            with self._state_lock:
                healthy = (
                    self._reader_thread is not None
                    and self._reader_thread.is_alive()
                    and self._should_run
                )
                if healthy:
                    return
                old_thread = self._reader_thread
                self._reader_thread = None

            if old_thread is not None and old_thread.is_alive():
                old_thread.join(timeout=_READER_JOIN_TIMEOUT_S)
                if old_thread.is_alive():
                    print(
                        f"[{self._label}] previous reader did not exit within "
                        f"{_READER_JOIN_TIMEOUT_S}s; starting new one anyway",
                        flush=True,
                    )

            self._warmup_event.clear()
            with self._state_lock:
                self._should_run = True
                self._latest_jpeg = None
                self._frame_seq = 0
                thread = threading.Thread(
                    target=self._reader_loop,
                    name=f"{self._label}-reader",
                    daemon=True,
                )
                self._reader_thread = thread
            thread.start()
            print(f"[{self._label}] reader thread started", flush=True)

    def _reader_loop(self):
        """Read, encode, publish; loop until should_run is False.

        On any exit path (clean stop, cap-open failure, exception) the
        finally block releases the camera, marks should_run False, and wakes
        any subscribers still waiting on the condition variable so they can
        observe the stopped state.
        """
        cap = self._open_capture()
        if cap is None:
            with self._frame_cond:
                self._should_run = False
                self._frame_cond.notify_all()
            self._warmup_event.set()
            return

        delay = 1.0 / self._fps if self._fps > 0 else 0.0

        # --- Warmup: discard initial black frames from USB cameras ---
        warmup_done = False
        for _ in range(_WARMUP_FRAMES):
            with self._state_lock:
                if not self._should_run:
                    break
            ok, frame = cap.read()
            if not ok:
                time.sleep(_READ_FAILURE_RETRY_S)
                continue
            if frame.mean() >= _WARMUP_MIN_BRIGHTNESS:
                ok_enc, jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
                )
                if ok_enc:
                    with self._frame_cond:
                        self._latest_jpeg = jpeg.tobytes()
                        self._frame_seq += 1
                        self._frame_cond.notify_all()
                warmup_done = True
                break

        self._warmup_event.set()
        if not warmup_done:
            print(
                f"[{self._label}] warmup: no bright frame in "
                f"{_WARMUP_FRAMES} reads",
                flush=True,
            )

        try:
            while True:
                with self._state_lock:
                    if not self._should_run:
                        break

                ok, frame = cap.read()
                if not ok:
                    time.sleep(_READ_FAILURE_RETRY_S)
                    continue

                ok, jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
                )
                if not ok:
                    continue

                with self._frame_cond:
                    self._latest_jpeg = jpeg.tobytes()
                    self._frame_seq += 1
                    self._frame_cond.notify_all()

                if delay > 0:
                    time.sleep(delay)
        finally:
            cap.release()
            with self._frame_cond:
                self._should_run = False
                self._frame_cond.notify_all()
            self._warmup_event.clear()
            print(f"[{self._label}] reader thread exited; camera released", flush=True)

    def _open_capture(self):
        print(f"[{self._label}] opening camera on {self._device}", flush=True)
        cap = cv2.VideoCapture(self._device)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        if not cap.isOpened():
            cap.release()
            print(f"[{self._label}] failed to open camera on {self._device}", flush=True)
            return None
        return cap


camera = FrameBroker(
    CAMERA_DEVICE,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    JPEG_QUALITY,
    label="front-camera",
)

back_camera = FrameBroker(
    BACK_CAMERA_DEVICE,
    BACK_CAMERA_WIDTH,
    BACK_CAMERA_HEIGHT,
    BACK_CAMERA_FPS,
    BACK_JPEG_QUALITY,
    label="back-camera",
)
