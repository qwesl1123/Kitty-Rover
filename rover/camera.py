import time
import threading
import cv2
from config import (
    CAMERA_DEVICE,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    JPEG_QUALITY,
    BACK_CAMERA_DEVICE,
    BACK_CAMERA_WIDTH,
    BACK_CAMERA_HEIGHT,
    BACK_CAMERA_FPS,
    BACK_JPEG_QUALITY,
)


class LazyCamera:
    def __init__(self, device, width, height, fps, jpeg_quality, label="camera"):
        self.cap = None
        self.lock = threading.Lock()
        self.active_clients = 0
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.jpeg_quality = jpeg_quality
        self.label = label

    def open(self):
        if self.cap is None:
            print(f"[{self.label}] opening camera on {self.device}")
            self.cap = cv2.VideoCapture(self.device)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)

            if not self.cap.isOpened():
                self.cap.release()
                self.cap = None
                raise RuntimeError("Could not open camera")

    def close(self):
        if self.cap is not None:
            print(f"[{self.label}] releasing camera")
            self.cap.release()
            self.cap = None

    def stream(self):
        with self.lock:
            self.active_clients += 1
            self.open()

        try:
            delay = 1.0 / self.fps if self.fps > 0 else 0

            while True:
                with self.lock:
                    if self.cap is None:
                        break

                    ok, frame = self.cap.read()

                if not ok:
                    time.sleep(0.1)
                    continue

                ok, jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                )

                if not ok:
                    continue

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + jpeg.tobytes()
                    + b"\r\n"
                )

                time.sleep(delay)

        finally:
            with self.lock:
                self.active_clients -= 1
                if self.active_clients <= 0:
                    self.active_clients = 0
                    self.close()


camera = LazyCamera(
    CAMERA_DEVICE,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    JPEG_QUALITY,
    label="front-camera",
)

back_camera = LazyCamera(
    BACK_CAMERA_DEVICE,
    BACK_CAMERA_WIDTH,
    BACK_CAMERA_HEIGHT,
    BACK_CAMERA_FPS,
    BACK_JPEG_QUALITY,
    label="back-camera",
)
