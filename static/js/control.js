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

startMic.addEventListener("click", async () => {
  micStatus.textContent = "Starting...";
  micAudio.src = "/mic_feed?t=" + Date.now();

  try {
    await micAudio.play();
  } catch (err) {
    micStatus.textContent = "Mic playback error: " + err.message;
  }
});

stopMic.addEventListener("click", () => {
  micAudio.pause();
  micAudio.removeAttribute("src");
  micAudio.load();
  micStatus.textContent = "Stopped";
});

micAudio.onerror = () => {
  micStatus.textContent = "Rover mic stream error";
};

micAudio.onplaying = () => {
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

function cleanupVoiceClip() {
  if (recordTimer !== null) {
    clearTimeout(recordTimer);
    recordTimer = null;
  }

  if (recordStream) {
    recordStream.getTracks().forEach((track) => track.stop());
  }

  recorder = null;
  recordStream = null;
  recordChunks = [];
  shouldUploadRecording = false;
  isRecording = false;
  currentMimeType = "";
  setTalkButtonEnabled(true);
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
    setTalkButtonEnabled(false);
    talkStatus.textContent = "Recording...";

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!isRecording || !shouldUploadRecording) {
      stream.getTracks().forEach((track) => track.stop());
      cleanupVoiceClip();
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
        cleanupVoiceClip();
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
        talkStatus.textContent = res.ok ? "Played on rover speaker" : "Failed to play audio";
      } catch (err) {
        talkStatus.textContent = "Failed to play audio: " + err.message;
      } finally {
        cleanupVoiceClip();
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
    cleanupVoiceClip();
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
    cleanupVoiceClip();
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
      cleanupVoiceClip();
    }
  } else {
    cleanupVoiceClip();
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
