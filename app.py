from flask import Flask, Response, render_template, request, jsonify
from flask_socketio import SocketIO

from rover.camera import camera
from rover.audio import mic_stream, play_audio_file
from rover.drive import drive, get_drive_status, set_status_callback, start_watchdog, stop
from rover.system_control import get_system_status, screen_off, screen_on

app = Flask(__name__)
app.config["SECRET_KEY"] = "rover-dev-secret"
MAX_AUDIO_UPLOAD_BYTES = 5 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


def emit_drive_status(status=None):
    socketio.emit("drive_status", status or get_drive_status())


set_status_callback(emit_drive_status)
start_watchdog(socketio.start_background_task, socketio.sleep)


@app.route("/")
def home():
    return render_template("index.html", system_status=get_system_status())


@app.route("/video_feed")
def video_feed():
    return Response(
        camera.stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/mic_feed")
def mic_feed():
    return Response(
        mic_stream(),
        mimetype="audio/mpeg",
    )


@app.route("/status")
def status():
    return jsonify({"ok": True, "drive": get_drive_status()})


@app.route("/system/status")
def system_status():
    return jsonify(get_system_status())


@app.route("/system/screen_off", methods=["POST"])
def system_screen_off():
    result = screen_off()
    return jsonify(result), 200 if result.get("ok") else 500


@app.route("/system/screen_on", methods=["POST"])
def system_screen_on():
    result = screen_on()
    return jsonify(result), 200 if result.get("ok") else 500


@app.route("/play_audio", methods=["POST"])
def play_audio():
    if request.content_length and request.content_length > MAX_AUDIO_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "audio upload too large"}), 413

    audio = request.get_data()

    if not audio:
        return jsonify({"ok": False, "error": "no audio received"}), 400

    if len(audio) > MAX_AUDIO_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "audio upload too large"}), 413

    try:
        play_audio_file(audio)
    except Exception as err:
        app.logger.exception("[audio] playback failed to start")
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify({"ok": True})


@socketio.on("drive")
def handle_drive(data):
    left = int(data.get("left", 0))
    right = int(data.get("right", 0))
    drive(left, right)


@socketio.on("stop")
def handle_stop():
    stop()


@socketio.on("emergency_stop")
def handle_emergency_stop():
    stop()


@socketio.on("connect")
def handle_connect():
    emit_drive_status()


@socketio.on("disconnect")
def handle_disconnect():
    stop()


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        allow_unsafe_werkzeug=True,
    )
