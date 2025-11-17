#!/usr/bin/env python3
"""
RC Car Server with Multiple Ports
Each port offers a different interface:

- 5000: Full Control
- 5001: Video Only (RTSP-style)
- 5002: GPS and Tracking Only
- 5003: Media Management (photos/video)
- 5004: API Only (JSON)
"""

from flask import Flask, render_template, Response, jsonify, send_file, request
from flask_socketio import SocketIO
import RPi.GPIO as GPIO
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder, H264Encoder
from picamera2.outputs import FileOutput
import io
import threading
import time
import os
from datetime import datetime
import json

# ========== Shared Resources ==========

# GPIO, Camera, GPS etc. shared for all servers

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Pins

SERVO_LEFT = 17
SERVO_RIGHT = 27
ULTRASONIC_TRIG = 23
ULTRASONIC_ECHO = 24
LED_FRONT = 22

# GPIO Setup

for pin in [SERVO_LEFT, SERVO_RIGHT, ULTRASONIC_TRIG, ULTRASONIC_ECHO, LED_FRONT]:
    if pin in [SERVO_LEFT, SERVO_RIGHT, LED_FRONT]:
        GPIO.setup(pin, GPIO.OUT)
    else:
        GPIO.setup(pin, GPIO.OUT if pin == ULTRASONIC_TRIG else GPIO.IN)

pwm_left = GPIO.PWM(SERVO_LEFT, 50)
pwm_right = GPIO.PWM(SERVO_RIGHT, 50)
pwm_left.start(7.5)
pwm_right.start(7.5)

# Shared variables

shared_state = {
    "distance": 0,
    "obstacle_warning": False,
    "lights_on": False,
    "auto_avoid": False,
    "is_recording": False,
    "gps": {
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "speed": None,
        "satellites": 0
    },
    "home_position": None
}

# Shared camera

picam2 = Picamera2()
config = picam2.create_video_configuration(main={"size": (640, 480)})
picam2.configure(config)


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


output = StreamingOutput()
picam2.start_recording(JpegEncoder(), FileOutput(output))

# Shared control functions


def servo_stop():
    pwm_left.ChangeDutyCycle(7.5)
    pwm_right.ChangeDutyCycle(7.5)


def servo_forward(speed=100):
    left_duty = 7.5 + (speed / 100 * 2.5)
    right_duty = 7.5 - (speed / 100 * 2.5)
    pwm_left.ChangeDutyCycle(left_duty)
    pwm_right.ChangeDutyCycle(right_duty)


def servo_backward(speed=100):
    left_duty = 7.5 - (speed / 100 * 2.5)
    right_duty = 7.5 + (speed / 100 * 2.5)
    pwm_left.ChangeDutyCycle(left_duty)
    pwm_right.ChangeDutyCycle(right_duty)


def servo_turn_left(speed=100):
    left_duty = 7.5 - (speed / 100 * 2.5)
    right_duty = 7.5 - (speed / 100 * 2.5)
    pwm_left.ChangeDutyCycle(left_duty)
    pwm_right.ChangeDutyCycle(right_duty)


def servo_turn_right(speed=100):
    left_duty = 7.5 + (speed / 100 * 2.5)
    right_duty = 7.5 + (speed / 100 * 2.5)
    pwm_left.ChangeDutyCycle(left_duty)
    pwm_right.ChangeDutyCycle(right_duty)


def generate_video_stream():
    """Shared video stream generator"""
    while True:
        with output.condition:
            output.condition.wait()
            frame = output.frame
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

# ========== PORT 5000: Full Control ==========


app_full = Flask(__name__, template_folder="templates")
sio_full = SocketIO(app_full, cors_allowed_origins="*")


@app_full.route("/")
def full_index():
    return render_template("control_complete.html")


@app_full.route("/video_feed")
def full_video():
    return Response(generate_video_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app_full.route("/status")
def full_status():
    return jsonify(shared_state)


@sio_full.on("command")
def full_command(data):
    cmd = data.get("command")
    speed = data.get("speed", 70)

    if cmd == "forward":
        servo_forward(speed)
    elif cmd == "backward":
        servo_backward(speed)
    elif cmd == "left":
        servo_turn_left(speed)
    elif cmd == "right":
        servo_turn_right(speed)
    elif cmd == "stop":
        servo_stop()


@sio_full.on("lights")
def full_lights(data):
    shared_state["lights_on"] = not shared_state["lights_on"]
    GPIO.output(LED_FRONT, GPIO.HIGH if shared_state["lights_on"] else GPIO.LOW)

# ========== PORT 5001: Video Only ==========


app_video = Flask(__name__)


@app_video.route("/")
def video_index():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RC Car - Video Only</title>
<style>
body { margin: 0; padding: 0; background: #000; display: flex; justify-content: center; align-items: center; min-height: 100vh; flex-direction: column; }
h1 { color: white; font-family: Arial, sans-serif; margin-bottom: 20px; }
img { max-width: 100%; max-height: 90vh; border: 3px solid #2196F3; border-radius: 10px; }
.info { color: #aaa; font-family: monospace; margin-top: 20px; text-align: center; }
.fullscreen-btn { position: fixed; bottom: 20px; right: 20px; padding: 15px 25px; background: #2196F3; color: white; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 16px; }
.fullscreen-btn:hover { background: #1976D2; }
</style>
</head>
<body>
<h1>Video Stream - RC Car</h1>
<img id="video" src="/video_feed" alt="Live Video">
<div class="info">
<p>Live Stream | Read-Only Mode</p>
<p>Port 5001 - Video Access Only</p>
</div>
<button class="fullscreen-btn" onclick="goFullscreen()">Fullscreen</button>
<script>
function goFullscreen() {
    const video = document.getElementById("video");
    if (video.requestFullscreen) {
        video.requestFullscreen();
    } else if (video.webkitRequestFullscreen) {
        video.webkitRequestFullscreen();
    }
}
document.getElementById("video").onerror = function() {
    setTimeout(() => {
        this.src = "/video_feed?" + new Date().getTime();
    }, 1000);
};
</script>
</body>
</html>
"""


@app_video.route("/video_feed")
def video_feed():
    return Response(generate_video_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

# ========== PORT 5002: GPS & Tracking Only ==========


app_gps = Flask(__name__)
sio_gps = SocketIO(app_gps, cors_allowed_origins="*")


@app_gps.route("/")
def gps_index():
    return """
<!DOCTYPE html>
<html dir="ltr" lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RC Car - GPS Tracking</title>
<style>
body { margin: 0; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); font-family: Arial, sans-serif; color: white; }
.container { max-width: 800px; margin: 0 auto; background: white; border-radius: 15px; padding: 30px; color: #333; box-shadow: 0 10px 40px rgba(0,0,0,0.3); }
h1 { text-align: center; color: #667eea; margin-bottom: 30px; }
.gps-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }
.gps-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; text-align: center; color: white; }
.gps-label { font-size: 14px; opacity: 0.9; margin-bottom: 10px; }
.gps-value { font-size: 32px; font-weight: bold; }
#map { width: 100%; height: 400px; border-radius: 10px; margin-top: 20px; background: #f0f0f0; display: flex; align-items: center; justify-content: center; font-size: 18px; color: #666; }
</style>
</head>
<body>
<div class="container">
<h1>GPS Tracking - RC Car</h1>
<div class="gps-grid">
    <div class="gps-card"><div class="gps-label">Latitude</div><div class="gps-value" id="lat">--</div></div>
    <div class="gps-card"><div class="gps-label">Longitude</div><div class="gps-value" id="lon">--</div></div>
    <div class="gps-card"><div class="gps-label">Altitude</div><div class="gps-value" id="alt">--</div></div>
    <div class="gps-card"><div class="gps-label">Speed</div><div class="gps-value" id="speed">--</div></div>
    <div class="gps-card"><div class="gps-label">Satellites</div><div class="gps-value" id="sats">0</div></div>
    <div class="gps-card"><div class="gps-label">Accuracy</div><div class="gps-value" id="accuracy">--</div></div>
</div>
<div id="map">Map (requires Google Maps API)</div>
</div>
<script>
    setInterval(() => {
        fetch("/gps_data").then(r => r.json()).then(data => {
            if (data.latitude) {
                document.getElementById("lat").textContent = data.latitude.toFixed(6);
                document.getElementById("lon").textContent = data.longitude.toFixed(6);
                document.getElementById("alt").textContent = (data.altitude || "--") + "m";
                document.getElementById("speed").textContent = (data.speed || 0).toFixed(1) + " km/h";
                document.getElementById("sats").textContent = data.satellites;
                document.getElementById("accuracy").textContent = 
                    data.satellites >= 6 ? "High" : data.satellites >= 4 ? "Medium" : "Low";
            }
        });
    }, 500);
</script>
</body>
</html>
"""


@app_gps.route("/gps_data")
def gps_data():
    return jsonify(shared_state["gps"])

# ========== PORT 5003: Media Gallery ==========


app_media = Flask(__name__)

MEDIA_DIR = "/home/pi/rc_car_media"


@app_media.route("/")
def media_index():
    return """
<!DOCTYPE html>
<html dir="ltr" lang="en">
<head>
<meta charset="UTF-8">
<title>RC Car - Media Gallery</title>
<style>
body { margin: 0; padding: 20px; background: #1a1a1a; font-family: Arial, sans-serif; color: white; }
h1 { text-align: center; color: #2196F3; }
.tabs { display: flex; justify-content: center; gap: 20px; margin: 30px 0; }
.tab { padding: 15px 30px; background: #333; border: none; border-radius: 8px; color: white; cursor: pointer; font-size: 16px; font-weight: bold; }
.tab.active { background: #2196F3; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; max-width: 1400px; margin: 0 auto; }
.media-item { background: #2a2a2a; border-radius: 10px; overflow: hidden; cursor: pointer; transition: transform 0.2s; }
.media-item:hover { transform: scale(1.05); }
.media-item img, .media-item video { width: 100%; height: 200px; object-fit: cover; }
.media-info { padding: 15px; }
.media-name { font-weight: bold; margin-bottom: 5px; }
</style>
</head>
<body>
<h1>Media Gallery - RC Car</h1>
<div class="tabs">
    <button class="tab active" onclick="showTab('photos')">Photos</button>
    <button class="tab" onclick="showTab('videos')">Videos</button>
</div>
<div class="gallery" id="gallery"></div>
<script>
    let currentTab = "photos";
    function showTab(tab) {
        currentTab = tab;
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        event.target.classList.add("active");
        loadMedia();
    }
    function loadMedia() {
        fetch(`/list_${currentTab}`).then(r => r.json()).then(data => {
            const gallery = document.getElementById("gallery");
            gallery.innerHTML = "";
            const items = data[currentTab] || [];
            items.forEach(item => {
                const div = document.createElement("div");
                div.className = "media-item";
                if (currentTab === "photos") {
                    div.innerHTML = `<img src="/media/photo/${item}" alt="${item}"><div class="media-info"><div class="media-name">${item}</div></div>`;
                    div.onclick = () => window.open(`/media/photo/${item}`);
                } else {
                    div.innerHTML = `<video src="/media/video/${item}" controls></video><div class="media-info"><div class="media-name">${item}</div></div>`;
                }
                gallery.appendChild(div);
            });
        });
    }
    loadMedia();
    setInterval(loadMedia, 5000);
</script>
</body>
</html>
"""


@app_media.route("/list_photos")
def list_photos():
    photos = sorted(os.listdir(f"{MEDIA_DIR}/photos"), reverse=True) if os.path.exists(f"{MEDIA_DIR}/photos") else []
    return jsonify({"photos": photos})


@app_media.route("/list_videos")
def list_videos():
    videos = sorted(os.listdir(f"{MEDIA_DIR}/videos"), reverse=True) if os.path.exists(f"{MEDIA_DIR}/videos") else []
    return jsonify({"videos": videos})


@app_media.route("/media/photo/<filename>")
def get_photo(filename):
    return send_file(f"{MEDIA_DIR}/photos/{filename}")


@app_media.route("/media/video/<filename>")
def get_video(filename):
    return send_file(f"{MEDIA_DIR}/videos/{filename}")

# ========== PORT 5004: API Only (JSON) ==========


app_api = Flask(__name__)


@app_api.route("/")
def api_index():
    return jsonify({
        "service": "RC Car API",
        "version": "1.0",
        "endpoints": {
            "/status": "GET - Full system status",
            "/control": "POST - Send control commands",
            "/gps": "GET - GPS data only",
            "/sensors": "GET - Sensor readings",
            "/media/count": "GET - Media file counts"
        }
    })


@app_api.route("/status")
def api_status():
    return jsonify(shared_state)


@app_api.route("/control", methods=["POST"])
def api_control():
    data = request.get_json()
    cmd = data.get("command")
    speed = data.get("speed", 70)

    if cmd == "forward":
        servo_forward(speed)
    elif cmd == "backward":
        servo_backward(speed)
    elif cmd == "left":
        servo_turn_left(speed)
    elif cmd == "right":
        servo_turn_right(speed)
    elif cmd == "stop":
        servo_stop()
    else:
        return jsonify({"error": "Unknown command"}), 400

    return jsonify({"success": True, "command": cmd, "speed": speed})


@app_api.route("/gps")
def api_gps():
    return jsonify(shared_state["gps"])


@app_api.route("/sensors")
def api_sensors():
    return jsonify({
        "distance": shared_state["distance"],
        "obstacle_warning": shared_state["obstacle_warning"]
    })


@app_api.route("/media/count")
def api_media_count():
    photo_count = len(os.listdir(f"{MEDIA_DIR}/photos")) if os.path.exists(f"{MEDIA_DIR}/photos") else 0
    video_count = len(os.listdir(f"{MEDIA_DIR}/videos")) if os.path.exists(f"{MEDIA_DIR}/videos") else 0
    return jsonify({
        "photos": photo_count,
        "videos": video_count
    })

# ========== Run All Servers ==========


def run_server(app, port, name):
    """Run server on specific port"""
    print(f"Starting {name} on port {port}")
    if hasattr(app, "run"):
        if name == "Full Control":
            sio_full.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
        elif name == "GPS Tracking":
            sio_gps.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
        else:
            app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    print("=" * 60)
    print("RC Car Multi-Port Server")
    print("=" * 60)
    print("Available interfaces:")
    print("  - http://<IP>:5000 - Full Control")
    print("  - http://<IP>:5001 - Video Only")
    print("  - http://<IP>:5002 - GPS Tracking")
    print("  - http://<IP>:5003 - Media Gallery")
    print("  - http://<IP>:5004 - API Only")
    print("=" * 60)

    # Create threads for each server
    threads = [
        threading.Thread(target=run_server, args=(app_full, 5000, "Full Control"), daemon=True),
        threading.Thread(target=run_server, args=(app_video, 5001, "Video Only"), daemon=True),
        threading.Thread(target=run_server, args=(app_gps, 5002, "GPS Tracking"), daemon=True),
        threading.Thread(target=run_server, args=(app_media, 5003, "Media Gallery"), daemon=True),
        threading.Thread(target=run_server, args=(app_api, 5004, "API Only"), daemon=True)
    ]

    try:
        # Start all servers
        for thread in threads:
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
            
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        servo_stop()
        GPIO.cleanup()
        picam2.stop_recording()
        print("Shutdown complete")
