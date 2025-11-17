#!/usr/bin/env python3
"""
RC Car Multi-Port Server with Authentication
Each port offers different interface with its own password:
- 5000: Full Control
- 5001: Video Only
- 5002: GPS Tracking
- 5003: Media Gallery
- 5004: API Only
"""

from flask import Flask, render_template, Response, jsonify, send_file, request, abort
from flask_socketio import SocketIO
from functools import wraps
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

# ==========================================
# 🔐 PASSWORD SETTINGS - EDIT HERE!
# ==========================================
PASSWORDS = {
    'full_control': 'admin123',      # Port 5000 - Full Control
    'video_only': 'video123',        # Port 5001 - Video Only
    'gps_tracking': 'gps123',        # Port 5002 - GPS Only
    'media_gallery': 'media123',     # Port 5003 - Gallery
    'api_access': 'api123'           # Port 5004 - API
}
# ==========================================

# Authentication decorator
def require_password(password_key):
    """Decorator to require password authentication"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth = request.authorization
            expected_password = PASSWORDS.get(password_key)
            
            if not auth or auth.password != expected_password:
                return Response(
                    'Authentication Required!\n'
                    'Username: any name\n'
                    f'Password: {password_key}',
                    401,
                    {'WWW-Authenticate': 'Basic realm="RC Car Login Required"'}
                )
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ========== Shared Resources ==========
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Pin definitions
SERVO_LEFT = 17
SERVO_RIGHT = 27
ULTRASONIC_TRIG = 23
ULTRASONIC_ECHO = 24
LED_FRONT = 22

# Setup GPIO pins
for pin in [SERVO_LEFT, SERVO_RIGHT, ULTRASONIC_TRIG, ULTRASONIC_ECHO, LED_FRONT]:
    if pin in [SERVO_LEFT, SERVO_RIGHT, LED_FRONT]:
        GPIO.setup(pin, GPIO.OUT)
    else:
        GPIO.setup(pin, GPIO.OUT if pin == ULTRASONIC_TRIG else GPIO.IN)

# PWM for servos
pwm_left = GPIO.PWM(SERVO_LEFT, 50)
pwm_right = GPIO.PWM(SERVO_RIGHT, 50)
pwm_left.start(7.5)
pwm_right.start(7.5)

# Shared state variables
shared_state = {
    'distance': 0,
    'obstacle_warning': False,
    'lights_on': False,
    'auto_avoid': False,
    'is_recording': False,
    'gps': {
        'latitude': None,
        'longitude': None,
        'altitude': None,
        'speed': None,
        'satellites': 0
    },
    'home_position': None
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
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# ========== PORT 5000: Full Control 🔐 ==========
app_full = Flask(__name__, template_folder='templates')
sio_full = SocketIO(app_full, cors_allowed_origins="*")

@app_full.route('/')
@require_password('full_control')
def full_index():
    return render_template('control_complete.html')

@app_full.route('/video_feed')
@require_password('full_control')
def full_video():
    return Response(generate_video_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app_full.route('/status')
@require_password('full_control')
def full_status():
    return jsonify(shared_state)

@sio_full.on('command')
def full_command(data):
    # Socket.IO doesn't support HTTP Basic auth, so we rely on page being protected
    cmd = data.get('command')
    speed = data.get('speed', 70)
    
    if cmd == 'forward':
        servo_forward(speed)
    elif cmd == 'backward':
        servo_backward(speed)
    elif cmd == 'left':
        servo_turn_left(speed)
    elif cmd == 'right':
        servo_turn_right(speed)
    elif cmd == 'stop':
        servo_stop()

@sio_full.on('lights')
def full_lights(data):
    shared_state['lights_on'] = not shared_state['lights_on']
    GPIO.output(LED_FRONT, GPIO.HIGH if shared_state['lights_on'] else GPIO.LOW)

@sio_full.on('take_photo')
def full_take_photo(data):
    print("📸 Taking photo...")

@sio_full.on('start_recording')
def full_start_recording(data):
    print("🎥 Starting recording...")

@sio_full.on('stop_recording')
def full_stop_recording(data):
    print("⏹️ Stopping recording...")

# ========== PORT 5001: Video Only 🔐 ==========
app_video = Flask(__name__)

@app_video.route('/')
@require_password('video_only')
def video_index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🔐 RC Car - Video Only</title>
        <style>
            body {
                margin: 0;
                padding: 0;
                background: #000;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                flex-direction: column;
            }
            .header {
                text-align: center;
                color: white;
                margin-bottom: 20px;
            }
            h1 {
                font-family: Arial, sans-serif;
                margin: 10px 0;
                font-size: 28px;
            }
            .badge {
                display: inline-block;
                background: #f44336;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                margin-top: 10px;
            }
            img {
                max-width: 95vw;
                max-height: 85vh;
                border: 3px solid #2196F3;
                border-radius: 10px;
                box-shadow: 0 10px 40px rgba(33, 150, 243, 0.5);
            }
            .info {
                color: #aaa;
                font-family: monospace;
                margin-top: 20px;
                text-align: center;
                background: rgba(255,255,255,0.1);
                padding: 15px;
                border-radius: 8px;
            }
            .fullscreen-btn {
                position: fixed;
                bottom: 20px;
                right: 20px;
                padding: 15px 25px;
                background: #2196F3;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                cursor: pointer;
                font-size: 16px;
                box-shadow: 0 4px 15px rgba(33, 150, 243, 0.5);
                transition: all 0.3s;
            }
            .fullscreen-btn:hover {
                background: #1976D2;
                transform: translateY(-2px);
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.6; }
            }
            .live-indicator {
                display: inline-block;
                width: 10px;
                height: 10px;
                background: #f44336;
                border-radius: 50%;
                margin-right: 8px;
                animation: pulse 2s infinite;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📹 RC Car - Video Stream</h1>
            <div class="badge">🔒 Protected View</div>
        </div>
        
        <img id="video" src="/video_feed" alt="Live Video">
        
        <div class="info">
            <p>
                <span class="live-indicator"></span>
                <strong>Live Stream</strong> | Read-Only Mode
            </p>
            <p style="font-size: 12px; margin-top: 10px;">
                Port 5001 - Video Access Only | No Control Allowed
            </p>
        </div>
        
        <button class="fullscreen-btn" onclick="goFullscreen()">⛶ Fullscreen</button>
        
        <script>
            function goFullscreen() {
                const video = document.getElementById('video');
                if (video.requestFullscreen) {
                    video.requestFullscreen();
                } else if (video.webkitRequestFullscreen) {
                    video.webkitRequestFullscreen();
                } else if (video.mozRequestFullScreen) {
                    video.mozRequestFullScreen();
                }
            }
            
            // Auto refresh on error
            document.getElementById('video').onerror = function() {
                console.log('Video error, retrying...');
                setTimeout(() => {
                    this.src = '/video_feed?' + new Date().getTime();
                }, 1000);
            };
            
            // Keyboard shortcut for fullscreen
            document.addEventListener('keydown', function(e) {
                if (e.key === 'f' || e.key === 'F') {
                    goFullscreen();
                }
            });
        </script>
    </body>
    </html>
    '''

@app_video.route('/video_feed')
@require_password('video_only')
def video_feed():
    return Response(generate_video_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ========== PORT 5002: GPS & Tracking Only 🔐 ==========
app_gps = Flask(__name__)
sio_gps = SocketIO(app_gps, cors_allowed_origins="*")

@app_gps.route('/')
@require_password('gps_tracking')
def gps_index():
    return '''
    <!DOCTYPE html>
    <html dir="rtl" lang="he">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🔐 RC Car - GPS Tracking</title>
        <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                font-family: Arial, sans-serif;
                color: white;
                min-height: 100vh;
            }
            .container {
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 15px;
                padding: 30px;
                color: #333;
                box-shadow: 0 10px 40px rgba(0,0,0,0.3);
            }
            .header {
                text-align: center;
                margin-bottom: 30px;
            }
            h1 {
                color: #667eea;
                margin-bottom: 10px;
            }
            .badge {
                display: inline-block;
                background: #f44336;
                color: white;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: bold;
            }
            .gps-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }
            .gps-card {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 25px;
                border-radius: 10px;
                text-align: center;
                color: white;
                box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                transition: transform 0.3s;
            }
            .gps-card:hover {
                transform: translateY(-5px);
            }
            .gps-label {
                font-size: 13px;
                opacity: 0.9;
                margin-bottom: 10px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            .gps-value {
                font-size: 32px;
                font-weight: bold;
                text-shadow: 0 2px 10px rgba(0,0,0,0.3);
            }
            .gps-unit {
                font-size: 14px;
                opacity: 0.8;
                margin-top: 5px;
            }
            #map {
                width: 100%;
                height: 400px;
                border-radius: 10px;
                margin-top: 20px;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 18px;
                color: #666;
                border: 2px solid #e0e0e0;
            }
            .status-bar {
                background: #f5f5f5;
                padding: 15px;
                border-radius: 8px;
                margin-top: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .status-indicator {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .indicator-dot {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: #4CAF50;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📍 GPS Tracking - RC Car</h1>
                <span class="badge">🔒 Tracking Only</span>
            </div>
            
            <div class="gps-grid">
                <div class="gps-card">
                    <div class="gps-label">Latitude</div>
                    <div class="gps-value" id="lat">--</div>
                    <div class="gps-unit">°N/S</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">Longitude</div>
                    <div class="gps-value" id="lon">--</div>
                    <div class="gps-unit">°E/W</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">Altitude</div>
                    <div class="gps-value" id="alt">--</div>
                    <div class="gps-unit">meters</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">Speed</div>
                    <div class="gps-value" id="speed">--</div>
                    <div class="gps-unit">km/h</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">Satellites</div>
                    <div class="gps-value" id="sats">0</div>
                    <div class="gps-unit">connected</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">Accuracy</div>
                    <div class="gps-value" style="font-size: 24px;" id="accuracy">--</div>
                </div>
            </div>
            
            <div id="map">
                🗺️ Map (requires Google Maps API or Leaflet)
            </div>
            
            <div class="status-bar">
                <div class="status-indicator">
                    <div class="indicator-dot"></div>
                    <span><strong>Status:</strong> <span id="status">Connected</span></span>
                </div>
                <div>
                    <strong>Last Update:</strong> <span id="lastUpdate">--</span>
                </div>
            </div>
        </div>
        
        <script>
            function updateGPS() {
                fetch('/gps_data')
                    .then(r => r.json())
                    .then(data => {
                        if (data.latitude) {
                            document.getElementById('lat').textContent = data.latitude.toFixed(6);
                            document.getElementById('lon').textContent = data.longitude.toFixed(6);
                            document.getElementById('alt').textContent = (data.altitude || 0).toFixed(1);
                            document.getElementById('speed').textContent = (data.speed || 0).toFixed(1);
                            document.getElementById('sats').textContent = data.satellites;
                            
                            // Accuracy based on satellite count
                            const accuracy = data.satellites >= 8 ? 'Excellent' : 
                                           data.satellites >= 6 ? 'Good' : 
                                           data.satellites >= 4 ? 'Fair' : 'Poor';
                            document.getElementById('accuracy').textContent = accuracy;
                            
                            document.getElementById('status').textContent = 'Receiving Data';
                        } else {
                            document.getElementById('status').textContent = 'Waiting for GPS';
                        }
                        
                        const now = new Date();
                        document.getElementById('lastUpdate').textContent = 
                            now.toLocaleTimeString('en-US');
                    })
                    .catch(err => {
                        document.getElementById('status').textContent = 'Connection Error';
                    });
            }
            
            // Update every half second
            setInterval(updateGPS, 500);
            updateGPS();
        </script>
    </body>
    </html>
    '''

@app_gps.route('/gps_data')
@require_password('gps_tracking')
def gps_data():
    return jsonify(shared_state['gps'])

# ========== PORT 5003: Media Gallery 🔐 ==========
app_media = Flask(__name__)

MEDIA_DIR = '/home/pi/rc_car_media'
os.makedirs(f'{MEDIA_DIR}/photos', exist_ok=True)
os.makedirs(f'{MEDIA_DIR}/videos', exist_ok=True)

@app_media.route('/')
@require_password('media_gallery')
def media_index():
    return '''
    <!DOCTYPE html>
    <html dir="rtl" lang="he">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🔐 RC Car - Media Gallery</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                padding: 20px;
                background: #1a1a1a;
                font-family: Arial, sans-serif;
                color: white;
                min-height: 100vh;
            }
            .header {
                text-align: center;
                margin-bottom: 30px;
            }
            h1 {
                color: #2196F3;
                margin-bottom: 10px;
            }
            .badge {
                display: inline-block;
                background: #f44336;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: bold;
            }
            .tabs {
                display: flex;
                justify-content: center;
                gap: 20px;
                margin: 30px 0;
            }
            .tab {
                padding: 15px 30px;
                background: #333;
                border: none;
                border-radius: 8px;
                color: white;
                cursor: pointer;
                font-size: 16px;
                font-weight: bold;
                transition: all 0.3s;
            }
            .tab:hover {
                background: #444;
                transform: translateY(-2px);
            }
            .tab.active {
                background: #2196F3;
                box-shadow: 0 4px 15px rgba(33, 150, 243, 0.5);
            }
            .stats {
                display: flex;
                justify-content: center;
                gap: 30px;
                margin: 20px 0;
            }
            .stat-card {
                background: #2a2a2a;
                padding: 20px 30px;
                border-radius: 10px;
                text-align: center;
            }
            .stat-number {
                font-size: 32px;
                font-weight: bold;
                color: #2196F3;
            }
            .stat-label {
                font-size: 14px;
                color: #888;
                margin-top: 5px;
            }
            .gallery {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 20px;
                max-width: 1400px;
                margin: 0 auto;
            }
            .media-item {
                background: #2a2a2a;
                border-radius: 10px;
                overflow: hidden;
                cursor: pointer;
                transition: transform 0.3s;
                box-shadow: 0 4px 15px rgba(0,0,0,0.5);
            }
            .media-item:hover {
                transform: scale(1.05);
                box-shadow: 0 8px 25px rgba(33, 150, 243, 0.5);
            }
            .media-item img, .media-item video {
                width: 100%;
                height: 200px;
                object-fit: cover;
                display: block;
            }
            .media-info {
                padding: 15px;
            }
            .media-name {
                font-weight: bold;
                margin-bottom: 5px;
                font-size: 14px;
            }
            .media-date {
                font-size: 12px;
                color: #888;
            }
            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: #666;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📁 Media Gallery - RC Car</h1>
            <span class="badge">🔒 Protected Gallery</span>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="photoCount">0</div>
                <div class="stat-label">Photos</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="videoCount">0</div>
                <div class="stat-label">Videos</div>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" onclick="showTab('photos')">📷 Photos</button>
            <button class="tab" onclick="showTab('videos')">🎥 Videos</button>
        </div>
        
        <div class="gallery" id="gallery"></div>
        
        <script>
            let currentTab = 'photos';
            
            function showTab(tab) {
                currentTab = tab;
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                event.target.classList.add('active');
                loadMedia();
            }
            
            function loadMedia() {
                fetch(`/list_${currentTab}`)
                    .then(r => r.json())
                    .then(data => {
                        const gallery = document.getElementById('gallery');
                        gallery.innerHTML = '';
                        
                        const items = data[currentTab] || [];
                        
                        // Update counts
                        if (currentTab === 'photos') {
                            document.getElementById('photoCount').textContent = items.length;
                        } else {
                            document.getElementById('videoCount').textContent = items.length;
                        }
                        
                        if (items.length === 0) {
                            gallery.innerHTML = `
                                <div class="empty-state" style="grid-column: 1/-1;">
                                    <p style="font-size: 48px;">📭</p>
                                    <p style="font-size: 18px;">No ${currentTab} yet</p>
                                </div>
                            `;
                            return;
                        }
                        
                        items.forEach(item => {
                            const div = document.createElement('div');
                            div.className = 'media-item';
                            
                            if (currentTab === 'photos') {
                                div.innerHTML = `
                                    <img src="/media/photo/${item}" alt="${item}">
                                    <div class="media-info">
                                        <div class="media-name">📷 ${item}</div>
                                        <div class="media-date">${formatFilename(item)}</div>
                                    </div>
                                `;
                                div.onclick = () => window.open(`/media/photo/${item}`, '_blank');
                            } else {
                                div.innerHTML = `
                                    <video src="/media/video/${item}"></video>
                                    <div class="media-info">
                                        <div class="media-name">🎥 ${item}</div>
                                        <div class="media-date">${formatFilename(item)}</div>
                                    </div>
                                `;
                                div.onclick = () => window.open(`/media/video/${item}`, '_blank');
                            }
                            
                            gallery.appendChild(div);
                        });
                    });
            }
            
            function formatFilename(filename) {
                // photo_20231225_143022.jpg -> 25/12/2023 14:30:22
                const match = filename.match(/(\d{8})_(\d{6})/);
                if (match) {
                    const date = match[1];
                    const time = match[2];
                    const year = date.substring(0, 4);
                    const month = date.substring(4, 6);
                    const day = date.substring(6, 8);
                    const hour = time.substring(0, 2);
                    const minute = time.substring(2, 4);
                    const second = time.substring(4, 6);
                    return `${month}/${day}/${year} ${hour}:${minute}:${second}`;
                }
                return filename;
            }
            
            // Load media on page load
            loadMedia();
            
            // Auto refresh every 5 seconds
            setInterval(loadMedia, 5000);
            
            // Update both counts
            fetch('/list_photos')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('photoCount').textContent = data.photos.length;
                });
            
            fetch('/list_videos')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('videoCount').textContent = data.videos.length;
                });
        </script>
    </body>
    </html>
    '''

@app_media.route('/list_photos')
@require_password('media_gallery')
def list_photos():
    photos = sorted(os.listdir(f'{MEDIA_DIR}/photos'), reverse=True) if os.path.exists(f'{MEDIA_DIR
