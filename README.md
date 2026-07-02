# 🐱 Kitty-Rover

A browser-driven, teleoperated rover you can drive from anywhere. Point a phone or
laptop at the rover's web page and you get a live view from two cameras, two-way
audio, and a D-pad to drive it around. The rover can even show an animated **cat
face** on an onboard screen and relay your face to it over video.

Everything the operator sees and touches runs in a normal web browser — no app to
install.

---

## How it works (architecture)

The rover carries a small **laptop** as its brain. That laptop runs the software in
this repo (a Python web server). It talks to an **ESP32** microcontroller over USB,
which in turn drives the motors:

```
   Browser (phone / laptop)
        │   WiFi · LAN · Tailscale
        │   • HTTP  :5000  — camera video (MJPEG), mic audio (MP3), uploads
        │   • Socket.IO    — drive commands + live status
        │   • WebRTC       — two-way audio + relayed face video
        ▼
   Rover onboard laptop  ──  Flask + Socket.IO server  (this repo)
        │   USB serial  /dev/ttyACM0 @ 115200 baud, 20 Hz
        │   sends text lines:  "M <left> <right>\n"   (values -255..255)
        ▼
   ESP32  ──  parses serial · outputs PWM · 500 ms deadman safety
        │   RPWM / LPWM per side
        ▼
   2 × IBT-2 (BTS7960) H-bridge  ──►  2 × MG540 gearmotor
        (differential / "tank" drive — steer by spinning the sides at different speeds)
```

> **Note:** This repo contains the **laptop-side software** plus the **ESP32
> firmware** (in [`firmware/`](firmware/)). The wiring from the ESP32 to the IBT-2
> boards and MG540 motors is your own hardware build — see [Hardware](#hardware).

---

## Features

- **Differential (tank) drive** with two gears (HIGH / LOW) and a minimum-PWM floor
  so slow turns still overcome motor stall.
- **Dual cameras** — front and back, streamed as MJPEG to the browser.
- **Two-way realtime audio** over WebRTC (listen to the rover's mic and talk back
  through its speaker), plus a lightweight MP3 mic stream and one-tap **voice-clip
  playback**.
- **Animated face screen** — an onboard kiosk display shows a cat animation, and a
  phone can publish its camera so the rover "wears" your face (relayed via WebRTC).
- **System telemetry panel** — battery, CPU temperature, CPU / RAM / disk usage,
  network interfaces, and Tailscale address.
- **Single-controller lock** — only one browser can drive at a time; others can
  press **Take Control** to grab the wheel.
- **Three-layer motor safety watchdog** — the rover stops itself if the connection
  drops (see [Safety](#safety)).

---

## Repository layout

```
Kitty-Rover
├── app.py                     # Flask + Socket.IO server (entry point)
├── config.py                  # Camera device / resolution constants
├── requirements.txt           # Pinned Python dependencies
├── rover/                     # Backend Python package
│   ├── audio.py               # Mic streaming + speaker playback (ffmpeg/ffplay)
│   ├── camera.py              # OpenCV MJPEG frame broker (front + back cameras)
│   ├── drive.py               # Drive state + safety watchdog (transport-agnostic)
│   ├── serial_link.py         # USB serial link to the ESP32 (streams motor commands)
│   ├── system_control.py      # Battery / CPU / network status, screen + kiosk control
│   ├── webrtc_audio.py        # Realtime two-way WebRTC audio (aiortc)
│   └── webrtc_face_video.py   # Server-relayed WebRTC face video (aiortc MediaRelay)
├── firmware/
│   └── kitty_rover_esp32/     # ESP32 Arduino sketch (motor driver firmware)
├── scripts/                   # Kiosk launch + screen on/off helpers (bash)
├── static/                    # style.css, control.js, oiia_cat.webm
└── templates/
    ├── index.html             # Operator control page
    └── face_screen.html       # Kiosk "face" display page
```

---

## Hardware

### Parts

| Part | Role |
|------|------|
| Laptop (Linux) | The rover's brain — runs this software |
| ESP32 dev board | Reads serial commands, generates motor PWM |
| 2 × IBT-2 (BTS7960) H-bridge | High-current motor drivers (one per side) |
| 2 × MG540 gearmotor | Left and right drive motors |
| Battery pack | Powers the motors / IBT-2 boards |
| 2 × USB cameras | Front and back views |
| USB mic + speaker | Two-way audio |
| Small HDMI screen *(optional)* | The animated "face" kiosk |

### Signal chain

```
laptop ──USB serial──► ESP32 ──PWM──► IBT-2 ×2 ──► MG540 ×2
```

### ESP32 pin map

These match [`firmware/kitty_rover_esp32/kitty_rover_esp32.ino`](firmware/kitty_rover_esp32/kitty_rover_esp32.ino):

| Signal | ESP32 GPIO | Connects to |
|--------|-----------|-------------|
| LEFT RPWM / LPWM  | 25 / 26 | Left IBT-2 RPWM / LPWM |
| RIGHT RPWM / LPWM | 32 / 33 | Right IBT-2 RPWM / LPWM |
| EN (shared enable) | 27 | `R_EN` **and** `L_EN` on **both** IBT-2 boards |

Wiring notes:

- **Enable line is shared.** GPIO 27 drives `R_EN` and `L_EN` on both IBT-2 boards;
  the firmware sets it HIGH on boot to enable the H-bridges.
- **Right motor is mirrored.** In the firmware `R_INVERT = true` (and
  `L_INVERT = false`) so that a positive command drives both tracks *forward* even
  though the right motor is mounted facing the other way. Flip these if your build
  drives backwards on one side.
- Give the ESP32 and the motor supply a **common ground**.

---

## Serial protocol (laptop ↔ ESP32)

Dead simple ASCII, one command per line at **115200 baud**:

| Command | Meaning |
|---------|---------|
| `M <left> <right>\n` | Set left/right motor power. Each value is a signed integer **−255..255** (sign = direction, magnitude = 8-bit PWM). |
| `stop` or `s` | Stop both motors. |

The laptop streams `M <left> <right>` at **20 Hz** whenever it's driving (see
`rover/serial_link.py`). The ESP32 enforces a **500 ms deadman**: if no command
arrives for half a second, it stops the motors on its own.

---

## Software setup (the laptop)

Requires **Python 3** and a few system tools.

```bash
# 1. Clone
git clone https://github.com/qwesl1123/kitty-rover.git
cd kitty-rover

# 2. Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. System binaries (Debian/Ubuntu example)
sudo apt install ffmpeg            # ffmpeg + ffplay: mic stream & clip playback
# optional extras:
#   tailscale          — remote access (shown in the status panel)
#   chromium + cage    — the face-screen kiosk

# 4. Run
python app.py                      # serves on http://0.0.0.0:5000
```

Then open **`http://<rover-ip>:5000/`** in a browser to drive.

The ESP32 should be connected on `/dev/ttyACM0`. If it isn't present, the server
still runs — it just logs that motor output is disabled and keeps retrying the
connection.

---

## Flashing the ESP32

1. Install the **Arduino IDE** and add ESP32 board support (Boards Manager →
   *esp32 by Espressif*).
2. Open [`firmware/kitty_rover_esp32/kitty_rover_esp32.ino`](firmware/kitty_rover_esp32/kitty_rover_esp32.ino).
3. Select your ESP32 board and its serial port, then **Upload**.
4. Wire it per the [pin map](#esp32-pin-map). Once plugged into the laptop it
   enumerates as `/dev/ttyACM0` (the port the software expects).

You can test it without the full stack using the Arduino Serial Monitor at 115200
baud — send e.g. `M 150 150` to drive forward, or `s` to stop.

---

## Using the rover

Open `http://<rover-ip>:5000/` (the **operator page**):

- **Drive** with the on-screen D-pad. Switch between **HIGH** and **LOW** gears —
  LOW is the default and best for tight spaces.
- **Take Control** — if someone else is driving, click this to take over.
- **Cameras** — toggle the front and back video feeds on/off.
- **Audio** — *Listen* to the rover's mic, *Talk* back through its speaker, or send
  a short recorded **voice clip**.
- **Face screen** — start/stop the onboard kiosk and publish your phone's camera to
  it.
- **System panel** — battery, temperature, resource usage, and network info.

`http://<rover-ip>:5000/face-screen` is the **kiosk page** shown on the rover's own
display (a cat animation by default; your face when a phone is publishing).

---

## Configuration & host assumptions

The code was written for one specific rover, so a few things are hardcoded. Adjust
them for your build:

| What | Where | Default |
|------|-------|---------|
| Camera devices / resolution | `config.py` | `/dev/video0` (front), `/dev/video2` (back) |
| ESP32 serial port | `rover/serial_link.py` → `PREFERRED_PORT` | `/dev/ttyACM0` (set `None` to auto-detect) |
| Mic / speaker (ALSA) | `rover/audio.py`, `rover/webrtc_audio.py` | `hw:0,0` |
| Kiosk Linux user | `scripts/face_screen_start.sh` | `franklin` |
| Kiosk service | `rover/system_control.py` | `rover-face-screen.service` (systemd) |

The screen and kiosk controls run helper scripts via `sudo -n` (passwordless), and
the face screen expects a `rover-face-screen.service` systemd unit that launches
`scripts/face_screen_start.sh`. These are optional — the driving and camera/audio
features work without them.

---

## Safety

The motors will not run away if something breaks. Three independent layers, each
with a **500 ms** timeout:

1. **ESP32 deadman** — the firmware stops the motors if the serial stream goes
   quiet.
2. **`drive.py` watchdog** — the laptop zeroes the drive state if the browser stops
   sending commands.
3. **Continuous serial stream** — `serial_link.py` keeps sending the current state
   at 20 Hz, so layer 1 stays fed while driving.

On top of that, a browser disconnect immediately releases control and issues a stop.

---

## Security notes

The server ships with a development `SECRET_KEY` and `cors_allowed_origins="*"` —
fine on a trusted home LAN or over Tailscale, but tighten both before exposing the
rover to a wider or untrusted network.

---

## Remote access (optional)

Install [Tailscale](https://tailscale.com/) on the rover laptop and your control
device to drive it from anywhere on your tailnet. The rover's Tailscale address is
surfaced in the system status panel.
