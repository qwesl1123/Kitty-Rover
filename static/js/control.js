const socket = io();

// Camera
const video = document.getElementById("video");
const startCam = document.getElementById("startCam");
const stopCam = document.getElementById("stopCam");

startCam.addEventListener("click", () => {
  video.src = "/video_feed?t=" + Date.now();
});

stopCam.addEventListener("click", () => {
  video.removeAttribute("src");
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

socket.on("drive_status", updateDriveStatus);

function sendDrive(left, right) {
  socket.emit("drive", { left, right });
}

function sendStop() {
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
  socket.emit("emergency_stop");
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
