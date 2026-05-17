from flask import Flask, Response, render_template, request, jsonify
from flask_socketio import SocketIO

from rover.camera import camera
from rover.audio import mic_stream, play_audio_file
from rover.drive import drive, stop

app = Flask(__name__)
app.config["SECRET_KEY"] = "rover-dev-secret"

socketio = SocketIO(app, cors_allowed_origins="*")


@app.route("/")
def home():
    return render_template("index.html")


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


@app.route("/play_audio", methods=["POST"])
def play_audio():
    audio = request.get_data()

    if not audio:
        return jsonify({"ok": False, "error": "no audio received"}), 400

    play_audio_file(audio)
    return jsonify({"ok": True})


@socketio.on("drive")
def handle_drive(data):
    left = int(data.get("left", 0))
    right = int(data.get("right", 0))
    drive(left, right)


@socketio.on("stop")
def handle_stop():
    stop()


@socketio.on("disconnect")
def handle_disconnect():
    stop()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
