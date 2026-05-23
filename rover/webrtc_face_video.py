"""Server-relayed WebRTC face video bridge for the rover.

This module keeps aiortc on one long-lived asyncio loop running in a
background thread so synchronous Flask routes can safely hand SDP offers to it.
It intentionally does not use asyncio.run() per request.
"""

import asyncio
import sys
import threading
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay


_LOOP = asyncio.new_event_loop()
_LOOP_READY = threading.Event()
_STATE_LOCK = threading.Lock()


@dataclass
class FaceVideoState:
    publisher_id: Optional[str] = None
    publisher_pc: Optional[RTCPeerConnection] = None
    publisher_video_track: Optional[object] = None
    viewers: Dict[str, RTCPeerConnection] = None


_STATE = FaceVideoState(viewers={})
_RELAY = MediaRelay()


def _log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(f"[face-video] {message}", file=stream, flush=True)


def _run_loop() -> None:
    asyncio.set_event_loop(_LOOP)
    _LOOP_READY.set()
    _log("asyncio loop started")
    _LOOP.run_forever()


_THREAD = threading.Thread(target=_run_loop, name="face-video-loop", daemon=True)
_THREAD.start()
_LOOP_READY.wait(timeout=5)


async def _close_viewers_locked(reason: str) -> int:
    viewers = list(_STATE.viewers.items())
    _STATE.viewers = {}

    for viewer_id, viewer_pc in viewers:
        try:
            await viewer_pc.close()
        except Exception as err:
            _log(f"viewer {viewer_id}: close failed: {err}", error=True)
    if viewers:
        _log(f"closed {len(viewers)} viewer(s): {reason}")
    return len(viewers)


async def _close_publisher_locked(reason: str) -> bool:
    publisher_id = _STATE.publisher_id
    publisher_pc = _STATE.publisher_pc
    _STATE.publisher_id = None
    _STATE.publisher_pc = None
    _STATE.publisher_video_track = None

    if publisher_pc is None:
        return False

    try:
        await publisher_pc.close()
    except Exception as err:
        _log(f"publisher {publisher_id}: close failed: {err}", error=True)

    _log(f"publisher {publisher_id}: closed ({reason})")
    return True


async def _cleanup_publisher_and_viewers(reason: str) -> None:
    with _STATE_LOCK:
        await _close_viewers_locked(reason)
        await _close_publisher_locked(reason)


async def _handle_publish_offer(offer_json: dict) -> dict:
    if not isinstance(offer_json, dict):
        raise ValueError("offer must be a JSON object")

    sdp = offer_json.get("sdp")
    offer_type = offer_json.get("type")
    if not sdp or offer_type != "offer":
        raise ValueError('expected JSON body with SDP offer: {"sdp": "...", "type": "offer"}')

    with _STATE_LOCK:
        if _STATE.publisher_pc is not None:
            return {"ok": False, "error": "face video publisher already active", "code": "publisher_exists"}

    pc = RTCPeerConnection()
    publisher_id = uuid.uuid4().hex[:12]
    video_track_ready = asyncio.get_running_loop().create_future()

    @pc.on("track")
    def on_track(track):
        _log(f"publisher {publisher_id}: received {track.kind} track")
        if track.kind == "video" and not video_track_ready.done():
            video_track_ready.set_result(track)

        @track.on("ended")
        async def on_ended():
            _log(f"publisher {publisher_id}: {track.kind} track ended")
            await _cleanup_publisher_and_viewers(f"publisher {track.kind} track ended")

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        _log(f"publisher {publisher_id}: connection state {pc.connectionState}")
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            await _cleanup_publisher_and_viewers(f"publisher connection {pc.connectionState}")

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=offer_type))

        try:
            video_track = await asyncio.wait_for(video_track_ready, timeout=10.0)
        except asyncio.TimeoutError as err:
            raise RuntimeError("publisher offer did not provide a video track") from err

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        with _STATE_LOCK:
            if _STATE.publisher_pc is not None:
                await pc.close()
                return {"ok": False, "error": "face video publisher already active", "code": "publisher_exists"}
            _STATE.publisher_id = publisher_id
            _STATE.publisher_pc = pc
            _STATE.publisher_video_track = video_track

        _log(f"publisher {publisher_id}: accepted and active")
        return {
            "ok": True,
            "publisher_id": publisher_id,
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        }
    except Exception:
        try:
            await pc.close()
        except Exception:
            pass
        raise


async def _handle_view_offer(offer_json: dict) -> dict:
    if not isinstance(offer_json, dict):
        raise ValueError("offer must be a JSON object")

    sdp = offer_json.get("sdp")
    offer_type = offer_json.get("type")
    if not sdp or offer_type != "offer":
        raise ValueError('expected JSON body with SDP offer: {"sdp": "...", "type": "offer"}')

    with _STATE_LOCK:
        video_track = _STATE.publisher_video_track

    if video_track is None:
        return {"ok": False, "error": "no active face video publisher", "code": "no_publisher"}

    viewer_id = uuid.uuid4().hex[:12]
    viewer_pc = RTCPeerConnection()

    @viewer_pc.on("connectionstatechange")
    async def on_connectionstatechange():
        _log(f"viewer {viewer_id}: connection state {viewer_pc.connectionState}")
        if viewer_pc.connectionState in {"failed", "closed", "disconnected"}:
            with _STATE_LOCK:
                existing = _STATE.viewers.pop(viewer_id, None)
            if existing is not None:
                try:
                    await existing.close()
                except Exception:
                    pass
            _log(f"viewer {viewer_id}: removed after state {viewer_pc.connectionState}")

    try:
        relayed_track = _RELAY.subscribe(video_track)
        viewer_pc.addTrack(relayed_track)
        await viewer_pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=offer_type))
        answer = await viewer_pc.createAnswer()
        await viewer_pc.setLocalDescription(answer)

        with _STATE_LOCK:
            if _STATE.publisher_video_track is None:
                await viewer_pc.close()
                return {"ok": False, "error": "no active face video publisher", "code": "no_publisher"}
            _STATE.viewers[viewer_id] = viewer_pc

        _log(f"viewer {viewer_id}: connected")
        return {
            "ok": True,
            "viewer_id": viewer_id,
            "sdp": viewer_pc.localDescription.sdp,
            "type": viewer_pc.localDescription.type,
        }
    except Exception:
        try:
            await viewer_pc.close()
        except Exception:
            pass
        raise


async def _close_publisher(publisher_id: Optional[str]) -> dict:
    with _STATE_LOCK:
        if _STATE.publisher_pc is None:
            return {"ok": True, "closed": False, "reason": "publisher not found"}
        if publisher_id and _STATE.publisher_id and publisher_id != _STATE.publisher_id:
            return {"ok": True, "closed": False, "reason": "publisher id mismatch"}

        await _close_viewers_locked("publisher close requested")
        closed = await _close_publisher_locked("publisher close requested")

    return {"ok": True, "closed": closed}


async def _close_viewer(viewer_id: str) -> dict:
    with _STATE_LOCK:
        viewer_pc = _STATE.viewers.pop(viewer_id, None)

    if viewer_pc is None:
        return {"ok": True, "closed": False, "reason": "viewer not found"}

    try:
        await viewer_pc.close()
    except Exception as err:
        _log(f"viewer {viewer_id}: close failed: {err}", error=True)

    _log(f"viewer {viewer_id}: closed by request")
    return {"ok": True, "closed": True}


async def _close_all() -> dict:
    with _STATE_LOCK:
        closed_viewers = await _close_viewers_locked("close all requested")
        closed_publisher = await _close_publisher_locked("close all requested")

    return {"ok": True, "closed_publisher": closed_publisher, "closed_viewers": closed_viewers}


def handle_face_video_publish_offer_sync(offer_json: dict, timeout: float = 25.0) -> dict:
    future = asyncio.run_coroutine_threadsafe(_handle_publish_offer(offer_json), _LOOP)
    return future.result(timeout=timeout)


def handle_face_video_view_offer_sync(offer_json: dict, timeout: float = 25.0) -> dict:
    future = asyncio.run_coroutine_threadsafe(_handle_view_offer(offer_json), _LOOP)
    return future.result(timeout=timeout)


def close_face_video_publisher_sync(publisher_id: Optional[str], timeout: float = 10.0) -> dict:
    future = asyncio.run_coroutine_threadsafe(_close_publisher(publisher_id), _LOOP)
    return future.result(timeout=timeout)


def close_face_video_viewer_sync(viewer_id: str, timeout: float = 10.0) -> dict:
    future = asyncio.run_coroutine_threadsafe(_close_viewer(viewer_id), _LOOP)
    return future.result(timeout=timeout)


def close_all_face_video_sync(timeout: float = 15.0) -> dict:
    future = asyncio.run_coroutine_threadsafe(_close_all(), _LOOP)
    return future.result(timeout=timeout)


def get_face_video_status() -> dict:
    with _STATE_LOCK:
        publisher_id = _STATE.publisher_id
        viewer_ids = sorted(_STATE.viewers.keys())

    return {
        "ok": True,
        "publisher_active": publisher_id is not None,
        "publisher_id": publisher_id,
        "viewer_count": len(viewer_ids),
        "viewer_ids": viewer_ids,
    }
