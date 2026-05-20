const socket = typeof io === "function" ? io() : null;

if (!socket) {
  console.warn("Socket.IO client is unavailable; drive controls are disabled, but system controls will keep working.");
}

// Camera
const video = document.getElementById("video");
const camToggle = document.getElementById("camToggle");
const cameraSection = document.querySelector(".cameraSection");

camToggle.addEventListener("click", () => {
  if (video.hasAttribute("src")) {
    video.removeAttribute("src");
  } else {
    video.src = "/video_feed?t=" + Date.now();
  }
});

// Audio
const startMic = document.getElementById("startMic");
const stopMic = document.getElementById("stopMic");
const micAudio = document.getElementById("micAudio");
const micStatus = document.getElementById("micStatus");

let isListening = false;
let listenWasActiveBeforeTalk = false;
let micStopReason = "manual";

async function startRoverMic() {
  micStopReason = "start";
  micStatus.textContent = "Starting...";
  micAudio.src = "/mic_feed?t=" + Date.now();

  try {
    await micAudio.play();
    isListening = true;
    micStatus.textContent = "Listening";
  } catch (err) {
    isListening = false;
    micAudio.pause();
    micAudio.removeAttribute("src");
    micAudio.load();
    micStatus.textContent = "Mic playback error: " + err.message;
  }
}

function stopRoverMic(reason = "manual") {
  micStopReason = reason;
  micAudio.pause();
  micAudio.removeAttribute("src");
  micAudio.load();
  isListening = false;
  micStatus.textContent = reason === "talk" ? "Paused while talking" : "Stopped";
}

async function resumeRoverMicIfNeeded() {
  if (!listenWasActiveBeforeTalk) {
    return;
  }

  listenWasActiveBeforeTalk = false;
  micStatus.textContent = "Resuming listen...";
  await startRoverMic();
}

startMic.addEventListener("click", startRoverMic);

stopMic.addEventListener("click", () => {
  listenWasActiveBeforeTalk = false;
  stopRoverMic("manual");
});

micAudio.onerror = () => {
  if (micStopReason === "talk" || micStopReason === "manual") {
    return;
  }
  isListening = false;
  micStatus.textContent = "Rover mic stream error";
};

micAudio.onplaying = () => {
  isListening = true;
  micStatus.textContent = "Listening";
};

const talkBtn = document.getElementById("talkBtn");
const talkStatus = document.getElementById("talkStatus");

let recorder = null;
let recordStream = null;
let recordChunks = [];
let recordTimer = null;
let shouldUploadRecording = false;
let isRecording = false;
let currentMimeType = "";

function setTalkButtonEnabled(enabled) {
  talkBtn.disabled = !enabled;
}

function selectVoiceClipMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];

  if (!window.MediaRecorder) {
    return "";
  }

  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function stopPhoneMicTracks() {
  if (recordStream) {
    recordStream.getTracks().forEach((track) => track.stop());
  }
}

function resetVoiceClipState() {
  recorder = null;
  recordStream = null;
  recordChunks = [];
  shouldUploadRecording = false;
  isRecording = false;
  currentMimeType = "";
  setTalkButtonEnabled(true);
}

async function cleanupVoiceClip({ resumeListen = false } = {}) {
  if (recordTimer !== null) {
    clearTimeout(recordTimer);
    recordTimer = null;
  }

  stopPhoneMicTracks();
  resetVoiceClipState();

  if (resumeListen) {
    await resumeRoverMicIfNeeded();
  } else {
    listenWasActiveBeforeTalk = false;
  }
}

async function startVoiceClip() {
  if (isRecording) {
    console.log("Recording already in progress");
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    talkStatus.textContent = "Mic error: browser recording is not supported";
    return;
  }

  try {
    isRecording = true;
    shouldUploadRecording = true;
    recordChunks = [];
    listenWasActiveBeforeTalk = isListening;
    setTalkButtonEnabled(false);

    if (isListening) {
      stopRoverMic("talk");
    }

    talkStatus.textContent = "Recording...";

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!isRecording || !shouldUploadRecording) {
      stream.getTracks().forEach((track) => track.stop());
      await cleanupVoiceClip({ resumeListen: true });
      return;
    }

    recordStream = stream;
    currentMimeType = selectVoiceClipMimeType();
    const options = currentMimeType ? { mimeType: currentMimeType } : undefined;
    recorder = new MediaRecorder(recordStream, options);

    const activeRecorder = recorder;
    const uploadMimeType = currentMimeType || activeRecorder.mimeType || "application/octet-stream";

    activeRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        recordChunks.push(event.data);
      }
    };

    activeRecorder.onerror = (event) => {
      const message = event.error?.message || "unknown recorder error";
      talkStatus.textContent = "Mic error: " + message;
      cancelVoiceClip("recorder error");
    };

    activeRecorder.onstop = async () => {
      console.log("recorder stopped");
      const uploadAllowed = shouldUploadRecording;
      const chunksToUpload = recordChunks.slice();

      if (!uploadAllowed) {
        console.log("upload skipped because canceled");
        talkStatus.textContent = "Recording canceled";
        await cleanupVoiceClip({ resumeListen: true });
        return;
      }

      try {
        if (chunksToUpload.length === 0) {
          throw new Error("no recorded audio data");
        }

        const blob = new Blob(chunksToUpload, { type: uploadMimeType });
        const buffer = await blob.arrayBuffer();

        talkStatus.textContent = "Sending...";
        console.log("upload starting");

        const res = await fetch("/play_audio", {
          method: "POST",
          headers: { "Content-Type": uploadMimeType },
          body: buffer,
        });

        console.log("upload complete");
        if (res.ok) {
          talkStatus.textContent = "Played on rover speaker";
        } else {
          let errorMessage = "Failed to play audio";
          try {
            const data = await res.json();
            if (data.error) {
              errorMessage += ": " + data.error;
            }
          } catch (err) {
            console.log("failed to parse play_audio error response", err);
          }
          talkStatus.textContent = errorMessage;
        }
      } catch (err) {
        talkStatus.textContent = "Failed to play audio: " + err.message;
      } finally {
        await cleanupVoiceClip({ resumeListen: true });
      }
    };

    activeRecorder.start(250);
    console.log("recording started");

    recordTimer = setTimeout(() => {
      console.log("timer finished");
      finishVoiceClip();
    }, 3000);
  } catch (err) {
    talkStatus.textContent = "Mic error: " + err.message;
    await cleanupVoiceClip({ resumeListen: true });
  }
}

function finishVoiceClip() {
  if (!recorder || recorder.state !== "recording") {
    return;
  }

  try {
    if (typeof recorder.requestData === "function") {
      recorder.requestData();
    }
    recorder.stop();
  } catch (err) {
    talkStatus.textContent = "Failed to stop recording: " + err.message;
    cleanupVoiceClip({ resumeListen: true });
  }
}

function cancelVoiceClip(reason) {
  if (!isRecording && !recorder) {
    return;
  }

  console.log("recording canceled", reason);
  talkStatus.textContent = "Recording canceled";
  shouldUploadRecording = false;

  if (recorder && recorder.state === "recording") {
    try {
      recorder.stop();
    } catch (err) {
      console.log("recorder stop failed during cancel", err);
      cleanupVoiceClip({ resumeListen: true });
    }
  } else {
    cleanupVoiceClip({ resumeListen: true });
  }
}

talkBtn.addEventListener("click", startVoiceClip);

window.addEventListener("pagehide", () => cancelVoiceClip("pagehide"));
window.addEventListener("beforeunload", () => cancelVoiceClip("beforeunload"));


// Realtime WebRTC audio
const webrtcToggle = document.getElementById("webrtcToggle");
const webrtcListenToggle = document.getElementById("webrtcListenToggle");
const webrtcTalkToggle = document.getElementById("webrtcTalkToggle");
const webrtcStatus = document.getElementById("webrtcStatus");
const webrtcRemoteAudio = document.getElementById("webrtcRemoteAudio");

let webrtcPc = null;
let webrtcLocalStream = null;
let webrtcRemoteStream = null;
let webrtcPeerId = null;
let webrtcTalkEnabled = false;
let webrtcListenEnabled = false;
let webrtcConnecting = false;
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

function setWebrtcStatus(message, isError = false) {
  webrtcStatus.textContent = message;
  webrtcStatus.classList.toggle("error", isError);
}

function updateWebrtcButtons() {
  const connected = Boolean(webrtcPc) && !webrtcConnecting;
  const hasLocalAudioTrack = Boolean(webrtcLocalStream?.getAudioTracks()?.length);
  webrtcListenToggle.disabled = !connected;
  webrtcTalkToggle.disabled = !connected || (!hasLocalAudioTrack && !isIOS);

  if (cameraSection) {
    let audioState = "disconnected";
    if (webrtcConnecting) {
      audioState = "connecting";
    } else if (connected) {
      audioState = "connected";
    }
    cameraSection.dataset.audioState = audioState;
    cameraSection.dataset.audioListen = webrtcListenEnabled ? "on" : "off";
    cameraSection.dataset.audioTalk = webrtcTalkEnabled ? "on" : "off";
  }
}

function stopWebrtcLocalTracks() {
  if (webrtcLocalStream) {
    webrtcLocalStream.getTracks().forEach((track) => track.stop());
  }
  webrtcLocalStream = null;
}

function waitForIceGatheringComplete(pc) {
  if (pc.iceGatheringState === "complete") {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      pc.removeEventListener("icegatheringstatechange", onStateChange);
      resolve();
    }, 3000);

    const onStateChange = () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timeout);
        pc.removeEventListener("icegatheringstatechange", onStateChange);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", onStateChange);
  });
}

async function closeWebrtcAudio({ notifyServer = true, statusMessage = "Disconnected" } = {}) {
  const peerIdToClose = webrtcPeerId;
  const pcToClose = webrtcPc;

  webrtcPc = null;
  webrtcPeerId = null;
  webrtcConnecting = false;
  webrtcTalkEnabled = false;
  webrtcListenEnabled = false;

  stopWebrtcLocalTracks();

  if (pcToClose) {
    pcToClose.ontrack = null;
    pcToClose.onconnectionstatechange = null;
    pcToClose.oniceconnectionstatechange = null;
    pcToClose.close();
  }

  if (webrtcRemoteAudio) {
    webrtcRemoteAudio.pause();
    webrtcRemoteAudio.srcObject = null;
    webrtcRemoteAudio.muted = false;
  }
  webrtcRemoteStream = null;

  updateWebrtcButtons();
  setWebrtcStatus(statusMessage);

  if (notifyServer && peerIdToClose) {
    try {
      await fetch("/webrtc/audio/close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ peer_id: peerIdToClose }),
        keepalive: true,
      });
    } catch (err) {
      console.log("WebRTC server close notification failed", err);
    }
  }
}

async function sendWebrtcOffer(pc) {
  const response = await fetch("/webrtc/audio/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pc.localDescription),
  });

  let answer = null;
  try {
    answer = await response.json();
  } catch (err) {
    throw new Error("invalid JSON answer from rover");
  }

  if (!response.ok || answer.ok === false) {
    throw new Error(answer.error || "rover rejected WebRTC offer");
  }

  webrtcPeerId = answer.peer_id || webrtcPeerId || null;
  await pc.setRemoteDescription(answer);
}

async function createWebrtcPeer({ includeMic = false, micStream = null, iosListenOnly = false } = {}) {
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  webrtcPc = pc;
  webrtcRemoteStream = new MediaStream();
  webrtcRemoteAudio.srcObject = webrtcRemoteStream;
  webrtcRemoteAudio.muted = false;

  pc.ontrack = (event) => {
    event.streams[0]?.getAudioTracks().forEach((track) => webrtcRemoteStream.addTrack(track));
    if (!event.streams[0]) {
      webrtcRemoteStream.addTrack(event.track);
    }
    if (webrtcListenEnabled) {
      webrtcRemoteAudio.play().catch((err) => {
        setWebrtcStatus("WebRTC error: " + err.message, true);
      });
    }
  };

  pc.onconnectionstatechange = () => {
    console.log("WebRTC connection state", pc.connectionState);
    if (pc.connectionState === "connected") {
      setWebrtcStatus("Connected");
    } else if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
      closeWebrtcAudio({ notifyServer: false, statusMessage: "Disconnected" });
    }
  };

  pc.oniceconnectionstatechange = () => {
    console.log("WebRTC ICE state", pc.iceConnectionState);
  };

  if (iosListenOnly) {
    pc.addTransceiver("audio", { direction: "recvonly" });
    webrtcTalkEnabled = false;
  }

  if (includeMic) {
    const stream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
    const micTrack = stream.getAudioTracks()[0];
    if (!micTrack) {
      throw new Error("microphone did not provide an audio track");
    }
    webrtcLocalStream = stream;
    if (!isIOS) {
      micTrack.enabled = false;
    }
    pc.addTrack(micTrack, stream);
  }

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitForIceGatheringComplete(pc);
  await sendWebrtcOffer(pc);

  return pc;
}

async function enableWebrtcTalkIOS() {
  setWebrtcStatus("Requesting iPhone microphone...");
  const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const micTrack = micStream.getAudioTracks()[0];
  if (!micTrack) {
    throw new Error("iPhone microphone did not provide an audio track");
  }

  await closeWebrtcAudio({ notifyServer: true, statusMessage: "Reconnecting with iPhone microphone..." });

  webrtcConnecting = true;
  updateWebrtcButtons();
  webrtcListenEnabled = true;
  await createWebrtcPeer({ includeMic: true, micStream });
  webrtcConnecting = false;
  webrtcTalkEnabled = true;
  updateWebrtcButtons();
  await setWebrtcListen(true);
  setWebrtcStatus("iPhone microphone active");
}

async function disableWebrtcTalkIOS() {
  await closeWebrtcAudio({ notifyServer: true, statusMessage: "iPhone microphone stopped" });

  webrtcConnecting = true;
  updateWebrtcButtons();
  setWebrtcStatus("Reconnecting listen-only...");
  await createWebrtcPeer({ iosListenOnly: true });
  webrtcConnecting = false;
  webrtcListenEnabled = true;
  webrtcTalkEnabled = false;
  updateWebrtcButtons();
  await setWebrtcListen(true);
  setWebrtcStatus("iPhone microphone stopped");
}

async function setWebrtcTalk(enabled) {
  if (!webrtcPc) {
    return;
  }

  if (isIOS) {
    try {
      if (enabled) {
        await enableWebrtcTalkIOS();
      } else {
        await disableWebrtcTalkIOS();
      }
    } catch (err) {
      webrtcTalkEnabled = false;
      updateWebrtcButtons();
      setWebrtcStatus("iPhone microphone error: " + err.message, true);
    }
    return;
  }

  webrtcTalkEnabled = enabled;
  if (webrtcLocalStream) {
    webrtcLocalStream.getAudioTracks().forEach((track) => {
      track.enabled = enabled;
    });
  }
  updateWebrtcButtons();
  setWebrtcStatus(enabled ? "Talk ON" : "Talk OFF");
}

async function setWebrtcListen(enabled) {
  webrtcListenEnabled = enabled;
  if (webrtcRemoteAudio) {
    webrtcRemoteAudio.muted = !enabled;
    if (enabled) {
      try {
        await webrtcRemoteAudio.play();
      } catch (err) {
        setWebrtcStatus("WebRTC error: " + err.message, true);
        webrtcListenEnabled = false;
      }
    } else {
      webrtcRemoteAudio.pause();
    }
  }
  updateWebrtcButtons();
  if (!webrtcStatus.classList.contains("error")) {
    setWebrtcStatus(enabled ? "Listen ON" : "Listen OFF");
  }
}

async function connectWebrtcAudio() {
  if (webrtcPc || webrtcConnecting) {
    return;
  }

  if (!window.RTCPeerConnection || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setWebrtcStatus("WebRTC error: browser realtime audio is not supported", true);
    return;
  }

  webrtcConnecting = true;
  setWebrtcStatus("Connecting...");
  updateWebrtcButtons();

  try {
    if (isIOS) {
      await createWebrtcPeer({ iosListenOnly: true });
      webrtcTalkEnabled = false;
    } else {
      await createWebrtcPeer({ includeMic: true });
      webrtcTalkEnabled = false;
    }

    webrtcConnecting = false;
    webrtcListenEnabled = true;
    updateWebrtcButtons();
    setWebrtcStatus(isIOS ? "Connected (iOS mode: tap Talk ON to start microphone)" : "Connected");
    await setWebrtcListen(true);
    if (!isIOS) {
      await setWebrtcTalk(false);
    }
  } catch (err) {
    await closeWebrtcAudio({ notifyServer: Boolean(webrtcPeerId), statusMessage: "WebRTC error: " + err.message });
    webrtcStatus.classList.add("error");
  }
}

webrtcToggle.addEventListener("click", () => {
  if (webrtcPc || webrtcConnecting) {
    closeWebrtcAudio({ statusMessage: "Disconnected" });
  } else {
    connectWebrtcAudio();
  }
});
webrtcTalkToggle.addEventListener("click", async () => setWebrtcTalk(!webrtcTalkEnabled));
webrtcListenToggle.addEventListener("click", () => setWebrtcListen(!webrtcListenEnabled));

window.addEventListener("pagehide", () => {
  closeWebrtcAudio({ statusMessage: "Disconnected" });
});
window.addEventListener("beforeunload", () => {
  closeWebrtcAudio({ statusMessage: "Disconnected" });
});

updateWebrtcButtons();

// Drive controls
const driveLeft = document.getElementById("driveLeft");
const driveRight = document.getElementById("driveRight");
const emergencyStop = document.getElementById("emergencyStop");
const DRIVE_REPEAT_MS = 200;

let driveRepeatTimer = null;

function updateDriveStatus(status) {
  if (!status) return;

  driveLeft.textContent = status.left;
  driveRight.textContent = status.right;
}

if (socket) {
  socket.on("drive_status", updateDriveStatus);
}

function sendDrive(left, right) {
  if (!socket) return;
  socket.emit("drive", { left, right });
}

function sendStop() {
  if (!socket) return;
  socket.emit("stop");
}

function clearDriveRepeat() {
  if (driveRepeatTimer !== null) {
    clearInterval(driveRepeatTimer);
    driveRepeatTimer = null;
  }
}

function stopAllDriveButtons() {
  clearDriveRepeat();
  document.querySelectorAll(".driveBtn.active").forEach((btn) => {
    btn.classList.remove("active");
  });
}

document.querySelectorAll(".driveBtn").forEach((btn) => {
  const left = parseInt(btn.dataset.left, 10);
  const right = parseInt(btn.dataset.right, 10);

  btn.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    stopAllDriveButtons();
    btn.setPointerCapture(event.pointerId);
    sendDrive(left, right);
    driveRepeatTimer = setInterval(() => sendDrive(left, right), DRIVE_REPEAT_MS);
    btn.classList.add("active");
  });

  const stopHandler = () => {
    stopAllDriveButtons();
    sendStop();
  };

  btn.addEventListener("pointerup", stopHandler);
  btn.addEventListener("pointercancel", stopHandler);
  btn.addEventListener("pointerleave", stopHandler);
});

emergencyStop.addEventListener("click", () => {
  stopAllDriveButtons();
  if (socket) {
    socket.emit("emergency_stop");
  }
});

window.addEventListener("blur", () => {
  stopAllDriveButtons();
  sendStop();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    cancelVoiceClip("visibility hidden");
    stopAllDriveButtons();
    sendStop();
  }
});

// System appliance controls
const batteryPercent = document.getElementById("batteryPercent");
const batteryStatus = document.getElementById("batteryStatus");
const cpuTemp = document.getElementById("cpuTemp");
const cpuUsage = document.getElementById("cpuUsage");
const ramUsage = document.getElementById("ramUsage");
const diskUsage = document.getElementById("diskUsage");
const uptime = document.getElementById("uptime");
const hostnameValue = document.getElementById("hostnameValue");
const lanIpValue = document.getElementById("lanIpValue");
const tailscaleIpValue = document.getElementById("tailscaleIpValue");
const interfacesValue = document.getElementById("interfacesValue");
const systemStatusMessage = document.getElementById("systemStatusMessage");
const refreshSystemStatus = document.getElementById("refreshSystemStatus");
const screenOff = document.getElementById("screenOff");
const screenOn = document.getElementById("screenOn");
const SYSTEM_REFRESH_MS = 10000;

function setSystemMessage(message, isError = false) {
  systemStatusMessage.textContent = message;
  systemStatusMessage.classList.toggle("error", isError);
}

function formatBattery(battery) {
  if (!battery || battery.capacity_percent === null || battery.capacity_percent === undefined) {
    batteryPercent.textContent = "Unknown";
  } else {
    batteryPercent.textContent = battery.capacity_percent + "%";
  }

  batteryStatus.textContent = battery && battery.status ? "(" + battery.status + ")" : "(unknown)";
}

function formatCpuTemp(temp) {
  if (!temp || temp.celsius === null || temp.celsius === undefined) {
    cpuTemp.textContent = "Unknown";
    return;
  }

  cpuTemp.textContent = temp.celsius.toFixed(1) + " °C";
}

function formatPercentValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "Unknown";
  }

  return Number(value).toFixed(1) + "%";
}

function formatResources(resources) {
  if (!resources) {
    cpuUsage.textContent = "Unknown";
    ramUsage.textContent = "Unknown";
    diskUsage.textContent = "Unknown";
    uptime.textContent = "Unknown";
    return;
  }

  cpuUsage.textContent = formatPercentValue(resources.cpu_percent);
  ramUsage.textContent = formatPercentValue(resources.ram_percent);
  diskUsage.textContent = formatPercentValue(resources.disk_percent);
  uptime.textContent = resources.uptime_human || "Unknown";
}

function formatNetwork(network) {
  if (!network) {
    hostnameValue.textContent = "Unknown";
    lanIpValue.textContent = "Unknown";
    tailscaleIpValue.textContent = "Unknown";
    interfacesValue.textContent = "Unknown";
    return;
  }

  hostnameValue.textContent = network.hostname || "Unknown";
  lanIpValue.textContent = Array.isArray(network.lan_ipv4) && network.lan_ipv4.length > 0
    ? network.lan_ipv4.join(", ")
    : "Unknown";
  tailscaleIpValue.textContent = network.tailscale_available && network.tailscale_ipv4
    ? network.tailscale_ipv4
    : "Unavailable";

  const compactInterfaces = Array.isArray(network.interfaces)
    ? network.interfaces
      .filter((iface) => iface && iface.is_up)
      .map((iface) => {
        const name = iface.name || "unknown";
        const ipv4 = Array.isArray(iface.ipv4) && iface.ipv4.length > 0 ? iface.ipv4.join(", ") : "no IPv4";
        return `${name}: ${ipv4}`;
      })
    : [];

  interfacesValue.textContent = compactInterfaces.length > 0 ? compactInterfaces.join("; ") : "Unknown";
}

async function fetchJsonOrThrow(url, options = {}) {
  const response = await fetch(url, options);
  let data = null;

  try {
    data = await response.json();
  } catch (err) {
    throw new Error("Invalid JSON response from " + url);
  }

  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Request failed with HTTP " + response.status);
  }

  return data;
}

async function refreshSystemStatusNow() {
  try {
    setSystemMessage("Refreshing system status...");
    const data = await fetchJsonOrThrow("/system/status");
    formatBattery(data.battery);
    formatCpuTemp(data.cpu_temp);
    formatResources(data.resources);
    formatNetwork(data.network);
    setSystemMessage("System status updated " + new Date().toLocaleTimeString());
  } catch (err) {
    setSystemMessage("System status error: " + err.message, true);
  }
}

async function postScreenControl(url, actionName) {
  try {
    setSystemMessage(actionName + "...");
    await fetchJsonOrThrow(url, { method: "POST" });
    setSystemMessage(actionName + " succeeded");
    await refreshSystemStatusNow();
  } catch (err) {
    setSystemMessage(actionName + " failed: " + err.message, true);
  }
}

refreshSystemStatus.addEventListener("click", refreshSystemStatusNow);
screenOff.addEventListener("click", () => postScreenControl("/system/screen_off", "Screen off"));
screenOn.addEventListener("click", () => postScreenControl("/system/screen_on", "Screen on"));

refreshSystemStatusNow();
setInterval(refreshSystemStatusNow, SYSTEM_REFRESH_MS);
