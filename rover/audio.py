import os
import subprocess
import sys
import tempfile
import threading


MIC_DEVICE = "hw:0,0"
SPEAKER_DEVICE = "hw:0,0"


def mic_stream():
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "alsa",
        "-i", MIC_DEVICE,
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        "-f", "mp3",
        "pipe:1",
    ]

    print(f"[mic_stream] starting ffmpeg for microphone {MIC_DEVICE}", flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )

    stderr_thread = threading.Thread(
        target=_log_ffmpeg_stderr_text,
        args=(proc,),
        daemon=True,
    )
    stderr_thread.start()

    try:
        if proc.stdout is None:
            return

        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        print("[mic_stream] stopping ffmpeg", flush=True)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[mic_stream] ffmpeg did not terminate; killing", flush=True)
                proc.kill()
                proc.wait()
        stderr_thread.join(timeout=1)
        print("[mic_stream] stopped", flush=True)


def _log_ffmpeg_stderr_text(proc):
    if proc.stderr is None:
        return

    for raw_line in iter(proc.stderr.readline, b""):
        if not raw_line:
            break
        line = raw_line.decode(errors="replace").rstrip()
        print(f"[mic_stream ffmpeg] {line}", file=sys.stderr, flush=True)


def play_audio_file(file_bytes: bytes, suffix=".webm"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(file_bytes)
        path = f.name

    try:
        subprocess.run(
            [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel", "error",
                "-af", "volume=5.0",
                path,
            ],
            timeout=20,
        )
    finally:
        os.remove(path)
