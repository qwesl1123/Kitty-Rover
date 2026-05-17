import time
import threading
import cv2
from config import CAMERA_DEVICE, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS, JPEG_QUALITY


class LazyCamera:
    def __init__(self):
        self.cap = None
        self.lock = threading.Lock()
        self.active_clients = 0

    def open(self):
        if self.cap is None:
            print("[camera] opening camera")
            self.cap = cv2.VideoCapture(CAMERA_DEVICE)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

            if not self.cap.isOpened():
                self.cap.release()
                self.cap = None
                raise RuntimeError("Could not open camera")

    def close(self):
        if self.cap is not None:
            print("[camera] releasing camera")
            self.cap.release()
            self.cap = None

    def stream(self):
        with self.lock:
            self.active_clients += 1
            self.open()

        try:
            delay = 1.0 / CAMERA_FPS

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
                    [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
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


camera = LazyCamera()