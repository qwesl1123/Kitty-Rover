import os
import subprocess
import sys
import tempfile
import threading


MIC_DEVICE = "hw:0,0"
SPEAKER_DEVICE = "hw:0,0"

_playback_lock = threading.Lock()
_playback_process = None


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

    print("[audio] mic stream started", flush=True)
    print(f"[audio] starting ffmpeg for microphone {MIC_DEVICE}", flush=True)
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
        print("[audio] stopping ffmpeg mic stream", flush=True)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[audio] ffmpeg mic stream did not terminate; killing", flush=True)
                proc.kill()
                proc.wait()
        stderr_thread.join(timeout=1)
        print("[audio] mic stream stopped", flush=True)


def _log_ffmpeg_stderr_text(proc):
    if proc.stderr is None:
        return

    for raw_line in iter(proc.stderr.readline, b""):
        if not raw_line:
            break
        line = raw_line.decode(errors="replace").rstrip()
        print(f"[audio] ffmpeg: {line}", file=sys.stderr, flush=True)


def play_audio_file(file_bytes: bytes, suffix=".webm"):
    """Start rover speaker playback in the background and return promptly."""
    global _playback_process

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(file_bytes)
        path = f.name

    with _playback_lock:
        previous_proc = _playback_process
        if previous_proc is not None and previous_proc.poll() is None:
            print("[audio] terminating previous playback", flush=True)
            previous_proc.terminate()
            _playback_process = None

        try:
            proc = subprocess.Popen(
                [
                    "ffplay",
                    "-nodisp",
                    "-autoexit",
                    "-loglevel", "error",
                    "-af", "volume=5.0",
                    path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception:
            _remove_temp_file(path)
            raise

        _playback_process = proc

    print("[audio] playback started", flush=True)
    threading.Thread(
        target=_finish_playback,
        args=(proc, path),
        daemon=True,
    ).start()


def _finish_playback(proc, path):
    global _playback_process

    try:
        _, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        print("[audio] ffplay timed out; killing playback", flush=True)
        proc.kill()
        _, stderr = proc.communicate()

    if stderr:
        for line in stderr.splitlines():
            print(f"[audio] ffplay: {line}", file=sys.stderr, flush=True)

    print("[audio] playback finished", flush=True)
    _remove_temp_file(path)

    with _playback_lock:
        if _playback_process is proc:
            _playback_process = None


def _remove_temp_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as err:
        print(f"[audio] failed to remove temp audio file {path}: {err}", file=sys.stderr, flush=True)
