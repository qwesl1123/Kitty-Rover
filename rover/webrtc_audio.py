"""Realtime WebRTC audio bridge for the rover.

This module keeps aiortc on one long-lived asyncio loop running in a
background thread so synchronous Flask routes can safely hand SDP offers to it.
It intentionally does not use asyncio.run() per request.
"""

import os

import asyncio
import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRecorder


os.environ.setdefault("ALSA_CONFIG_PATH", "/usr/share/alsa/alsa.conf")

MIC_DEVICE = "plughw:0,0"
SPEAKER_DEVICE = "plughw:0,0"

_LOOP = asyncio.new_event_loop()
_PEERS: Dict[str, "AudioPeer"] = {}
_PEERS_LOCK = threading.Lock()
_LOOP_READY = threading.Event()


@dataclass
class AudioPeer:
    peer_id: str
    pc: RTCPeerConnection
    player: Optional[MediaPlayer] = None
    recorder: Optional[MediaRecorder] = None
    recorder_started: bool = False
    cleanup_started: bool = False
    cleanup_tasks: set = field(default_factory=set)


def _log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(f"[webrtc-audio] {message}", file=stream, flush=True)


def _run_loop() -> None:
    asyncio.set_event_loop(_LOOP)
    _LOOP_READY.set()
    _log("asyncio loop started")
    _LOOP.run_forever()


_THREAD = threading.Thread(
    target=_run_loop, name="webrtc-audio-loop", daemon=True)
_THREAD.start()
_LOOP_READY.wait(timeout=5)


def _schedule_cleanup(peer: AudioPeer, reason: str) -> None:
    """Schedule peer cleanup from any callback running on the aiortc loop."""
    if peer.cleanup_started:
        return

    task = asyncio.create_task(_cleanup_peer(peer, reason))
    peer.cleanup_tasks.add(task)
    task.add_done_callback(peer.cleanup_tasks.discard)


async def _cleanup_peer(peer: AudioPeer, reason: str) -> None:
    """Release ALSA, aiortc, and peer bookkeeping resources."""
    if peer.cleanup_started:
        return

    peer.cleanup_started = True
    _log(f"peer {peer.peer_id}: cleanup started ({reason})")

    if peer.recorder is not None and peer.recorder_started:
        try:
            await peer.recorder.stop()
            _log(f"peer {peer.peer_id}: speaker recorder stopped")
        except Exception as err:  # best-effort shutdown
            _log(
                f"peer {peer.peer_id}: speaker recorder stop failed: {err}", error=True)

    if peer.player is not None and peer.player.audio is not None:
        try:
            peer.player.audio.stop()
            _log(f"peer {peer.peer_id}: microphone player stopped")
        except Exception as err:  # best-effort shutdown
            _log(
                f"peer {peer.peer_id}: microphone player stop failed: {err}", error=True)

    try:
        await peer.pc.close()
    except Exception as err:  # best-effort shutdown
        _log(
            f"peer {peer.peer_id}: peer connection close failed: {err}", error=True)

    with _PEERS_LOCK:
        _PEERS.pop(peer.peer_id, None)

    _log(f"peer {peer.peer_id}: cleanup complete")


async def _start_speaker_playback(peer: AudioPeer, track) -> None:
    """Send browser microphone RTP audio to the rover laptop speaker.

    MediaRecorder can write decoded audio frames directly through PyAV/FFmpeg's
    ALSA output. If this ALSA sink is unreliable on a specific Debian laptop,
    the practical fallback is to replace this recorder with an ffmpeg/ffplay
    subprocess that reads raw PCM from an aiortc MediaBlackhole-style consumer
    and writes to `ffplay -nodisp -f s16le -ar 48000 -ac 1 -i pipe:0` or
    `ffmpeg -f s16le ... -f alsa {SPEAKER_DEVICE}`. Keeping the fallback in
    comments avoids spawning orphan subprocesses unless this hardware needs it.
    """
    if peer.cleanup_started:
        return

    try:
        _log(
            f"peer {peer.peer_id}: starting speaker playback on ALSA {SPEAKER_DEVICE}")
        recorder = MediaRecorder(
            SPEAKER_DEVICE,
            format="alsa",
            options={"channels": "1", "sample_rate": "48000"},
        )
        recorder.addTrack(track)
        peer.recorder = recorder
        await recorder.start()
        peer.recorder_started = True
        _log(f"peer {peer.peer_id}: speaker playback started")
    except Exception as err:
        _log(f"peer {peer.peer_id}: speaker playback failed: {err}", error=True)
        _schedule_cleanup(peer, "speaker playback failed")


async def _handle_audio_offer(offer_json: dict) -> dict:
    if not isinstance(offer_json, dict):
        raise ValueError("offer must be a JSON object")

    sdp = offer_json.get("sdp")
    offer_type = offer_json.get("type")
    if not sdp or offer_type != "offer":
        raise ValueError(
            'expected JSON body with SDP offer: {"sdp": "...", "type": "offer"}')

    peer_id = uuid.uuid4().hex[:12]
    # STUN helps browser ICE state even on HTTPS/Tailscale/NPM paths, while host
    # candidates remain available for direct LAN/Tailscale connectivity.
    pc = RTCPeerConnection(
        RTCConfiguration(iceServers=[RTCIceServer(
            urls=["stun:stun.l.google.com:19302"])])
    )
    peer = AudioPeer(peer_id=peer_id, pc=pc)

    with _PEERS_LOCK:
        _PEERS[peer_id] = peer

    _log(f"peer {peer_id}: created")

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        _log(f"peer {peer_id}: connection state {pc.connectionState}")
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            _schedule_cleanup(peer, f"connection state {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        _log(f"peer {peer_id}: ICE connection state {pc.iceConnectionState}")
        if pc.iceConnectionState in {"failed", "closed", "disconnected"}:
            _schedule_cleanup(peer, f"ICE state {pc.iceConnectionState}")

    @pc.on("track")
    def on_track(track):
        _log(f"peer {peer_id}: received browser {track.kind} track")
        if track.kind == "audio":
            asyncio.create_task(_start_speaker_playback(peer, track))

        @track.on("ended")
        async def on_ended():
            _log(f"peer {peer_id}: browser {track.kind} track ended")
            _schedule_cleanup(peer, f"browser {track.kind} track ended")

    try:
        _log(f"peer {peer_id}: opening rover microphone ALSA {MIC_DEVICE}")
        player = MediaPlayer(
            MIC_DEVICE,
            format="alsa",
            options={"channels": "1", "sample_rate": "48000"},
        )
        peer.player = player
        if player.audio is None:
            raise RuntimeError(
                f"no audio track from ALSA microphone {MIC_DEVICE}")
        pc.addTrack(player.audio)
        _log(f"peer {peer_id}: rover microphone track added")

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        _log(f"peer {peer_id}: SDP answer created")

        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "peer_id": peer_id,
        }
    except Exception:
        _schedule_cleanup(peer, "offer handling failed")
        raise


def handle_audio_offer_sync(offer_json: dict, timeout: float = 20.0) -> dict:
    """Synchronous Flask-safe wrapper for creating a WebRTC SDP answer."""
    future = asyncio.run_coroutine_threadsafe(
        _handle_audio_offer(offer_json), _LOOP)
    return future.result(timeout=timeout)


async def _close_peer(peer_id: str, reason: str) -> dict:
    with _PEERS_LOCK:
        peer = _PEERS.get(peer_id)

    if peer is None:
        return {"ok": True, "closed": False, "reason": "peer not found"}

    await _cleanup_peer(peer, reason)
    return {"ok": True, "closed": True}


def close_audio_peer_sync(peer_id: str, reason: str = "client requested disconnect", timeout: float = 10.0) -> dict:
    """Synchronous Flask-safe wrapper for explicit browser disconnect cleanup."""
    future = asyncio.run_coroutine_threadsafe(
        _close_peer(peer_id, reason), _LOOP)
    return future.result(timeout=timeout)


def get_audio_status() -> dict:
    with _PEERS_LOCK:
        active_peers = len(_PEERS)

    return {"ok": True, "active_peers": active_peers}
