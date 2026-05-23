from flask import Flask, Response, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

from rover.camera import camera, back_camera
from rover.audio import mic_stream, play_audio_file
from rover.webrtc_audio import close_audio_peer_sync, get_audio_status, handle_audio_offer_sync
from rover.webrtc_face_video import (
    close_face_video_publisher_sync,
    close_face_video_viewer_sync,
    get_face_video_status,
    handle_face_video_publish_offer_sync,
    handle_face_video_view_offer_sync,
    set_publisher_offline_callback,
)
from rover.drive import drive, get_drive_status, set_status_callback, start_watchdog, stop
from rover.system_control import (
    face_screen_start,
    face_screen_stop,
    get_system_status,
    screen_off,
    screen_on,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "rover-dev-secret"
MAX_AUDIO_UPLOAD_BYTES = 5 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

FACE_SCREEN_ROOM = "face_screen"
face_screen_connected = False
face_screen_sid = None


def emit_face_screen_status(message=None):
    payload = {"connected": face_screen_connected}
    if message:
        payload["message"] = message
    socketio.emit("face_screen_status", payload)


def emit_drive_status(status=None):
    socketio.emit("drive_status", status or get_drive_status())


def emit_face_video_publisher_offline():
    """Fired by webrtc_face_video whenever the publisher is fully torn down.

    Tells every face-screen kiosk to drop its relay viewer immediately,
    so the last video frame doesn't linger while we wait for ICE
    consent-freshness to time out (which can take 10-30 seconds).
    """
    socketio.emit("face_video_publisher_offline", room=FACE_SCREEN_ROOM)


set_status_callback(emit_drive_status)
set_publisher_offline_callback(emit_face_video_publisher_offline)
start_watchdog(socketio.start_background_task, socketio.sleep)


@app.route("/")
def home():
    return render_template("index.html", system_status=get_system_status())


@app.route("/face-screen")
def face_screen():
    return render_template("face_screen.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        camera.stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/back_video_feed")
def back_video_feed():
    return Response(
        back_camera.stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/mic_feed")
def mic_feed():
    return Response(
        mic_stream(),
        mimetype="audio/mpeg",
    )


@app.route("/webrtc/audio/offer", methods=["POST"])
def webrtc_audio_offer():
    offer_json = request.get_json(silent=True)
    if not offer_json:
        return jsonify({"ok": False, "error": "missing JSON SDP offer"}), 400

    try:
        answer = handle_audio_offer_sync(offer_json)
    except Exception as err:
        app.logger.exception("[webrtc-audio] failed to handle SDP offer")
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify(answer)


@app.route("/webrtc/audio/close", methods=["POST"])
def webrtc_audio_close():
    data = request.get_json(silent=True) or {}
    peer_id = data.get("peer_id")
    if not peer_id:
        return jsonify({"ok": False, "error": "missing peer_id"}), 400

    try:
        result = close_audio_peer_sync(peer_id)
    except Exception as err:
        app.logger.exception("[webrtc-audio] failed to close peer")
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify(result)


@app.route("/webrtc/audio/status")
def webrtc_audio_status():
    return jsonify(get_audio_status())


@app.route("/webrtc/face_video/publish_offer", methods=["POST"])
def webrtc_face_video_publish_offer():
    offer_json = request.get_json(silent=True)
    if not offer_json:
        return jsonify({"ok": False, "error": "missing JSON SDP offer"}), 400

    try:
        result = handle_face_video_publish_offer_sync(offer_json)
    except ValueError as err:
        return jsonify({"ok": False, "error": str(err)}), 400
    except Exception as err:
        app.logger.exception("[face-video] failed to handle publisher SDP offer")
        return jsonify({"ok": False, "error": str(err)}), 500

    if not result.get("ok") and result.get("code") == "publisher_exists":
        return jsonify(result), 409

    return jsonify(result), 200


@app.route("/webrtc/face_video/publish_close", methods=["POST"])
def webrtc_face_video_publish_close():
    data = request.get_json(silent=True) or {}
    publisher_id = data.get("publisher_id")

    try:
        result = close_face_video_publisher_sync(publisher_id)
    except Exception as err:
        app.logger.exception("[face-video] failed to close publisher")
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify(result)


@app.route("/webrtc/face_video/view_offer", methods=["POST"])
def webrtc_face_video_view_offer():
    offer_json = request.get_json(silent=True)
    if not offer_json:
        return jsonify({"ok": False, "error": "missing JSON SDP offer"}), 400

    try:
        result = handle_face_video_view_offer_sync(offer_json)
    except ValueError as err:
        return jsonify({"ok": False, "error": str(err)}), 400
    except Exception as err:
        app.logger.exception("[face-video] failed to handle viewer SDP offer")
        return jsonify({"ok": False, "error": str(err)}), 500

    if not result.get("ok") and result.get("code") == "no_publisher":
        return jsonify(result), 409

    return jsonify(result), 200


@app.route("/webrtc/face_video/view_close", methods=["POST"])
def webrtc_face_video_view_close():
    data = request.get_json(silent=True) or {}
    viewer_id = data.get("viewer_id")
    if not viewer_id:
        return jsonify({"ok": False, "error": "missing viewer_id"}), 400

    try:
        result = close_face_video_viewer_sync(viewer_id)
    except Exception as err:
        app.logger.exception("[face-video] failed to close viewer")
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify(result)


@app.route("/webrtc/face_video/status")
def webrtc_face_video_status():
    return jsonify(get_face_video_status())


@app.route("/status")
def status():
    return jsonify({"ok": True, "drive": get_drive_status()})


@app.route("/system/status")
def system_status():
    return jsonify(get_system_status())


@app.route("/face_screen/status")
def face_screen_status():
    return jsonify(
        {
            "ok": True,
            "connected": face_screen_connected,
        }
    )


@app.route("/system/face_screen_start", methods=["POST"])
def system_face_screen_start():
    result = face_screen_start()
    return jsonify(result), 200 if result.get("ok") else 500


@app.route("/system/face_screen_stop", methods=["POST"])
def system_face_screen_stop():
    result = face_screen_stop()
    return jsonify(result), 200 if result.get("ok") else 500


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


@socketio.on("face_screen_join")
def handle_face_screen_join():
    global face_screen_connected, face_screen_sid

    join_room(FACE_SCREEN_ROOM)
    face_screen_connected = True
    face_screen_sid = request.sid
    emit_face_screen_status("Face screen connected")


@socketio.on("face_screen_leave")
def handle_face_screen_leave():
    global face_screen_connected, face_screen_sid

    leave_room(FACE_SCREEN_ROOM)

    if request.sid == face_screen_sid:
        face_screen_connected = False
        face_screen_sid = None
        emit_face_screen_status("Face screen disconnected")


@socketio.on("disconnect")
def handle_disconnect():
    global face_screen_connected, face_screen_sid

    if request.sid == face_screen_sid:
        face_screen_connected = False
        face_screen_sid = None
        emit_face_screen_status("Face screen disconnected")

    stop()


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        allow_unsafe_werkzeug=True,
    )
