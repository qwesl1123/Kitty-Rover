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

startMic.addEventListener("click", () => {
  micAudio.src = "/mic_feed?t=" + Date.now();
  micAudio.play();
});

stopMic.addEventListener("click", () => {
  micAudio.pause();
  micAudio.removeAttribute("src");
  micAudio.load();
});

const talkBtn = document.getElementById("talkBtn");
const talkStatus = document.getElementById("talkStatus");

let recorder = null;
let chunks = [];

talkBtn.addEventListener("click", async () => {
  try {
    talkStatus.textContent = "Recording 3 seconds...";

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    recorder = new MediaRecorder(stream);
    chunks = [];

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };

    recorder.onstop = async () => {
      const blob = new Blob(chunks, { type: recorder.mimeType });
      const buffer = await blob.arrayBuffer();

      talkStatus.textContent = "Sending...";

      const res = await fetch("/play_audio", {
        method: "POST",
        headers: { "Content-Type": recorder.mimeType },
        body: buffer
      });

      stream.getTracks().forEach(track => track.stop());
      talkStatus.textContent = res.ok ? "Played on rover speaker" : "Failed to play audio";
    };

    recorder.start();
    setTimeout(() => recorder.stop(), 3000);
  } catch (err) {
    talkStatus.textContent = "Mic error: " + err.message;
  }
});

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
    stopAllDriveButtons();
    sendStop();
  }
});
