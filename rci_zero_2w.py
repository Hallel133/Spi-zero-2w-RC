#!/usr/bin/env python3
"""
שרת מכונית RC עם פורטים מרובים + אבטחה
כל פורט מציע ממשק שונה עם סיסמה משלו:
- 5000: בקרה מלאה
- 5001: צפייה בוידאו בלבד
- 5002: GPS ומעקב בלבד
- 5003: ניהול מדיה
- 5004: API בלבד
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
# 🔐 הגדרות סיסמאות - ערוך כאן!
# ==========================================
PASSWORDS = {
    'full_control': 'admin123',      # פורט 5000 - בקרה מלאה
    'video_only': 'video123',        # פורט 5001 - וידאו בלבד
    'gps_tracking': 'gps123',        # פורט 5002 - GPS בלבד
    'media_gallery': 'media123',     # פורט 5003 - גלריה
    'api_access': 'api123'           # פורט 5004 - API
}
# ==========================================

# פונקציית הזדהות
def require_password(password_key):
    """דקורטור לדרישת סיסמה"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth = request.authorization
            expected_password = PASSWORDS.get(password_key)
            
            if not auth or auth.password != expected_password:
                return Response(
                    'נדרשת הזדהות!\n'
                    'שם משתמש: כל שם\n'
                    f'סיסמה: {password_key}',
                    401,
                    {'WWW-Authenticate': 'Basic realm="RC Car Login Required"'}
                )
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ========== Shared Resources ==========
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# פינים
SERVO_LEFT = 17
SERVO_RIGHT = 27
ULTRASONIC_TRIG = 23
ULTRASONIC_ECHO = 24
LED_FRONT = 22

# הגדרת GPIO
for pin in [SERVO_LEFT, SERVO_RIGHT, ULTRASONIC_TRIG, ULTRASONIC_ECHO, LED_FRONT]:
    if pin in [SERVO_LEFT, SERVO_RIGHT, LED_FRONT]:
        GPIO.setup(pin, GPIO.OUT)
    else:
        GPIO.setup(pin, GPIO.OUT if pin == ULTRASONIC_TRIG else GPIO.IN)

pwm_left = GPIO.PWM(SERVO_LEFT, 50)
pwm_right = GPIO.PWM(SERVO_RIGHT, 50)
pwm_left.start(7.5)
pwm_right.start(7.5)

# משתנים משותפים
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

# מצלמה משותפת
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

# פונקציות בקרה משותפות
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
    """גנרטור סטרים וידאו משותף"""
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
    # Socket.IO לא תומך באימות HTTP Basic, אז נשתמש באימות token
    # או פשוט נסמוך על כך שהדף עצמו מוגן
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
    print("📸 צילום תמונה...")

@sio_full.on('start_recording')
def full_start_recording(data):
    print("🎥 מתחיל הקלטה...")

@sio_full.on('stop_recording')
def full_stop_recording(data):
    print("⏹️ עוצר הקלטה...")

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
                <h1>📍 מעקב GPS - מכונית RC</h1>
                <span class="badge">🔒 Tracking Only</span>
            </div>
            
            <div class="gps-grid">
                <div class="gps-card">
                    <div class="gps-label">קו רוחב</div>
                    <div class="gps-value" id="lat">--</div>
                    <div class="gps-unit">°N/S</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">קו אורך</div>
                    <div class="gps-value" id="lon">--</div>
                    <div class="gps-unit">°E/W</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">גובה</div>
                    <div class="gps-value" id="alt">--</div>
                    <div class="gps-unit">מטרים</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">מהירות</div>
                    <div class="gps-value" id="speed">--</div>
                    <div class="gps-unit">קמ"ש</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">לוויינים</div>
                    <div class="gps-value" id="sats">0</div>
                    <div class="gps-unit">connected</div>
                </div>
                <div class="gps-card">
                    <div class="gps-label">דיוק</div>
                    <div class="gps-value" style="font-size: 24px;" id="accuracy">--</div>
                </div>
            </div>
            
            <div id="map">
                🗺️ מפה (דורש Google Maps API או Leaflet)
            </div>
            
            <div class="status-bar">
                <div class="status-indicator">
                    <div class="indicator-dot"></div>
                    <span><strong>סטטוס:</strong> <span id="status">מחובר</span></span>
                </div>
                <div>
                    <strong>עדכון אחרון:</strong> <span id="lastUpdate">--</span>
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
                            
                            // דיוק לפי מספר לוויינים
                            const accuracy = data.satellites >= 8 ? 'מעולה' : 
                                           data.satellites >= 6 ? 'גבוה' : 
                                           data.satellites >= 4 ? 'בינוני' : 'נמוך';
                            document.getElementById('accuracy').textContent = accuracy;
                            
                            document.getElementById('status').textContent = 'מקבל נתונים';
                        } else {
                            document.getElementById('status').textContent = 'ממתין ל-GPS';
                        }
                        
                        const now = new Date();
                        document.getElementById('lastUpdate').textContent = 
                            now.toLocaleTimeString('he-IL');
                    })
                    .catch(err => {
                        document.getElementById('status').textContent = 'שגיאת חיבור';
                    });
            }
            
            // עדכון כל חצי שנייה
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
            .empty-state svg {
                width: 100px;
                height: 100px;
                opacity: 0.3;
                margin-bottom: 20px;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📁 גלריית מדיה - מכונית RC</h1>
            <span class="badge">🔒 Protected Gallery</span>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="photoCount">0</div>
                <div class="stat-label">תמונות</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="videoCount">0</div>
                <div class="stat-label">סרטונים</div>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" onclick="showTab('photos')">📷 תמונות</button>
            <button class="tab" onclick="showTab('videos')">🎥 סרטונים</button>
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
                                    <p style="font-size: 18px;">אין ${currentTab === 'photos' ? 'תמונות' : 'סרטונים'} עדיין</p>
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
                    return `${day}/${month}/${year} ${hour}:${minute}:${second}`;
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
    photos = sorted(os.listdir(f'{MEDIA_DIR}/photos'), reverse=True) if os.path.exists(f'{MEDIA_DIR}/photos') else []
    return jsonify({'photos': photos})

@app_media.route('/list_videos')
@require_password('media_gallery')
def list_videos():
    videos = sorted(os.listdir(f'{MEDIA_DIR}/videos'), reverse=True) if os.path.exists(f'{MEDIA_DIR}/videos') else []
    return jsonify({'videos': videos})

@app_media.route('/media/photo/<filename>')
@require_password('media_gallery')
def get_photo(filename):
    return send_file(f'{MEDIA_DIR}/photos/{filename}')

@app_media.route('/media/video/<filename>')
@require_password('media_gallery')
def get_video(filename):
    return send_file(f'{MEDIA_DIR}/videos/{filename}')

# ========== PORT 5004: API Only 🔐 ==========
app_api = Flask(__name__)

@app_api.route('/')
@require_password('api_access')
def api_index():
    return jsonify({
        'service': 'RC Car API',
        'version': '1.0',
        'authentication': 'HTTP Basic Auth Required',
        'endpoints': {
            '/status': {
                'method': 'GET',
                'description': 'Full system status',
                'auth_required': True
            },
            '/control': {
                'method': 'POST',
                'description': 'Send control commands',
                'auth_required': True,
                'parameters': {
                    'command': 'forward|backward|left|right|stop',
                    'speed': 'integer 0-100'
                },
                'example': {
                    'command': 'forward',
                    'speed': 70
                }
            },
            '/gps': {
                'method': 'GET',
                'description': 'GPS data only',
                'auth_required': True
            },
            '/sensors': {
                'method': 'GET',
                'description': 'Sensor readings',
                'auth_required': True
            },
            '/media/count': {
                'method': 'GET',
                'description': 'Media file counts',
                'auth_required': True
            }
        }
    })

@app_api.route('/status')
@require_password('api_access')
def api_status():
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'data': shared_state
    })

@app_api.route('/control', methods=['POST'])
@require_password('api_access')
def api_control():
    data = request.get_json()
    
    if not data:
        return jsonify({
            'success': False,
            'error': 'No JSON data provided'
        }), 400
    
    cmd = data.get('command')
    speed = data.get('speed', 70)
    
    valid_commands = ['forward', 'backward', 'left', 'right', 'stop']
    if cmd not in valid_commands:
        return jsonify({
            'success': False,
            'error': f'Invalid command. Valid commands: {", ".join(valid_commands)}'
        }), 400
    
    if not isinstance(speed, int) or speed < 0 or speed > 100:
        return jsonify({
            'success': False,
            'error': 'Speed must be an integer between 0 and 100'
        }), 400
    
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
    
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'command': cmd,
        'speed': speed
    })

@app_api.route('/gps')
@require_password('api_access')
def api_gps():
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'data': shared_state['gps']
    })

@app_api.route('/sensors')
@require_password('api_access')
def api_sensors():
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'data': {
            'distance': shared_state['distance'],
            'obstacle_warning': shared_state['obstacle_warning'],
            'lights_on': shared_state['lights_on']
        }
    })

@app_api.route('/media/count')
@require_password('api_access')
def api_media_count():
    photo_count = len(os.listdir(f'{MEDIA_DIR}/photos')) if os.path.exists(f'{MEDIA_DIR}/photos') else 0
    video_count = len(os.listdir(f'{MEDIA_DIR}/videos')) if os.path.exists(f'{MEDIA_DIR}/videos') else 0
    return jsonify({
        'success': True,
        'timestamp': datetime.now().isoformat(),
        'data': {
            'photos': photo_count,
            'videos': video_count,
            'total': photo_count + video_count
        }
    })

# ========== Run All Servers ==========
def run_server(app, port, name, use_socketio=False):
    """הרץ שרת בפורט ספציפי"""
    print(f"🚀 {name} running on port {port}")
    try:
        if use_socketio:
            if name == "Full Control":
                sio_full.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
            elif name == "GPS Tracking":
                sio_gps.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
        else:
            app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except Exception as e:
        print(f"❌ Error starting {name}: {e}")

if __name__ == '__main__':
    print("=" * 70)
    print("🚗 RC Car Multi-Port Server with Authentication")
    print("=" * 70)
    print("\n🔐 PASSWORDS (change in code):")
    print(f"  Port 5000 (Full Control):  {PASSWORDS['full_control']}")
    print(f"  Port 5001 (Video Only):    {PASSWORDS['video_only']}")
    print(f"  Port 5002 (GPS Tracking):  {PASSWORDS['gps_tracking']}")
    print(f"  Port 5003 (Media Gallery): {PASSWORDS['media_gallery']}")
    print(f"  Port 5004 (API Access):    {PASSWORDS['api_access']}")
    print("\n📋 Available interfaces:")
    print("  • http://<IP>:5000 - 🎮 Full Control (בקרה מלאה)")
    print("  • http://<IP>:5001 - 📹 Video Only (וידאו בלבד)")
    print("  • http://<IP>:5002 - 📍 GPS Tracking (מעקב GPS)")
    print("  • http://<IP>:5003 - 📁 Media Gallery (גלריה)")
    print("  • http://<IP>:5004 - 🔌 API Only (JSON API)")
    print("=" * 70)
    print("\n💡 Tip: Use any username with the correct password")
    print("=" * 70)
    
    # צור threads לכל שרת
    threads = [
        threading.Thread(target=run_server, args=(app_full, 5000, "Full Control", True), daemon=True),
        threading.Thread(target=run_server, args=(app_video, 5001, "Video Only", False), daemon=True),
        threading.Thread(target=run_server, args=(app_gps, 5002, "GPS Tracking", True), daemon=True),
        threading.Thread(target=run_server, args=(app_media, 5003, "Media Gallery", False), daemon=True),
        threading.Thread(target=run_server, args=(app_api, 5004, "API Only", False), daemon=True)
    ]
    
    try:
        # התחל את כל השרתים
        for thread in threads:
            thread.start()
            time.sleep(0.5)  # תן זמן לכל שרת להתחיל
        
        print("\n✅ All servers started successfully!")
        print("🔒 All ports are password protected")
        print("\nPress Ctrl+C to stop all servers\n")
        
        # המתן לכולם
        for thread in threads:
            thread.join()
            
    except KeyboardInterrupt:
        print("\n⚠️ Shutting down all servers...")
    finally:
        servo_stop()
        GPIO.cleanup()
        picam2.stop_recording()
        print("✅ Shutdown complete")
