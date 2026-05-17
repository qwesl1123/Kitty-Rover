import os
import tempfile
import subprocess


MIC_DEVICE = "hw:0,0"
SPEAKER_DEVICE = "hw:0,0"


def mic_stream():
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "alsa",
        "-i", MIC_DEVICE,
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        "-f", "mp3",
        "pipe:1",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        proc.kill()
        proc.wait()


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