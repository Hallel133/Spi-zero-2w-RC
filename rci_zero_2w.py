from flask import Flask, render_template, Response, jsonify, send_file
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
import math

# GPS (אם קיים)
try:
    import serial
    import pynmea2
    GPS_AVAILABLE = True
except:
    GPS_AVAILABLE = False
    print("⚠️ GPS לא זמין - התקן: pip install pyserial pynmea2")

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# הגדרות GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# ========== פינים ==========
SERVO_LEFT = 17
SERVO_RIGHT = 27
ULTRASONIC_TRIG = 23
ULTRASONIC_ECHO = 24
LED_FRONT = 22

# הגדרת פינים
GPIO.setup(SERVO_LEFT, GPIO.OUT)
GPIO.setup(SERVO_RIGHT, GPIO.OUT)
GPIO.setup(ULTRASONIC_TRIG, GPIO.OUT)
GPIO.setup(ULTRASONIC_ECHO, GPIO.IN)
GPIO.setup(LED_FRONT, GPIO.OUT)

# PWM למנועי סרוו
pwm_left = GPIO.PWM(SERVO_LEFT, 50)
pwm_right = GPIO.PWM(SERVO_RIGHT, 50)
pwm_left.start(0)
pwm_right.start(0)

# משתנים גלובליים
current_distance = 0
obstacle_warning = False
lights_on = False
auto_avoid = False
is_recording = False
recording_thread = None

# GPS
gps_data = {
    'latitude': None,
    'longitude': None,
    'altitude': None,
    'speed': None,
    'timestamp': None,
    'satellites': 0
}
home_position = None
auto_return_active = False

# תיקיות לשמירת קבצים
MEDIA_DIR = '/home/pi/rc_car_media'
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(f'{MEDIA_DIR}/photos', exist_ok=True)
os.makedirs(f'{MEDIA_DIR}/videos', exist_ok=True)

# ========== GPS Functions ==========
class GPSReader:
    def __init__(self, port='/dev/serial0', baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.running = False
        
    def start(self):
        """התחל קריאת GPS"""
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            thread = threading.Thread(target=self._read_loop, daemon=True)
            thread.start()
            print("✅ GPS מופעל")
            return True
        except Exception as e:
            print(f"❌ שגיאה בהפעלת GPS: {e}")
            return False
    
    def _read_loop(self):
        """לולאת קריאה מ-GPS"""
        global gps_data
        while self.running:
            try:
                line = self.serial.readline().decode('ascii', errors='ignore')
                if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                    msg = pynmea2.parse(line)
                    gps_data['latitude'] = msg.latitude
                    gps_data['longitude'] = msg.longitude
                    gps_data['altitude'] = msg.altitude
                    gps_data['satellites'] = msg.num_sats
                    gps_data['timestamp'] = datetime.now().isoformat()
                elif line.startswith('$GPVTG') or line.startswith('$GNVTG'):
                    msg = pynmea2.parse(line)
                    gps_data['speed'] = msg.spd_over_grnd_kmph if msg.spd_over_grnd_kmph else 0
            except Exception as e:
                pass
            time.sleep(0.1)
    
    def stop(self):
        self.running = False
        if self.serial:
            self.serial.close()

# אתחול GPS
gps_reader = None
if GPS_AVAILABLE:
    gps_reader = GPSReader()
    gps_reader.start()

# ========== Navigation Functions ==========
def calculate_distance(lat1, lon1, lat2, lon2):
    """חישוב מרחק בין שתי נקודות GPS (בקילומטרים)"""
    R = 6371  # רדיוס כדור הארץ בק"מ
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    return R * c

def calculate_bearing(lat1, lon1, lat2, lon2):
    """חישוב כיוון (בדרגות) בין שתי נקודות"""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    
    x = math.sin(delta_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
    
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360

def auto_return_home():
    """חזור אוטומטית לנקודת הבית"""
    global auto_return_active
    
    if not home_position or not gps_data['latitude']:
        socketio.emit('navigation_error', {'message': 'אין נתוני GPS או נקודת בית'})
        return
    
    auto_return_active = True
    socketio.emit('navigation_started', {'message': 'מתחיל ניווט לבית...'})
    
    while auto_return_active:
        current_lat = gps_data['latitude']
        current_lon = gps_data['longitude']
        home_lat = home_position['latitude']
        home_lon = home_position['longitude']
        
        # חשב מרחק וכיוון
        distance = calculate_distance(current_lat, current_lon, home_lat, home_lon) * 1000  # במטרים
        bearing = calculate_bearing(current_lat, current_lon, home_lat, home_lon)
        
        socketio.emit('navigation_update', {
            'distance': distance,
            'bearing': bearing
        })
        
        # בדוק אם הגענו (דיוק של 3 מטרים)
        if distance < 3:
            servo_stop()
            auto_return_active = False
            socketio.emit('navigation_complete', {'message': 'הגעת לנקודת הבית!'})
            break
        
        # בדוק מכשולים
        if obstacle_warning and auto_avoid:
            servo_stop()
            time.sleep(0.5)
            # נסה לעקוף
            servo_turn_right(60)
            time.sleep(1)
            servo_forward(50)
            time.sleep(1)
        else:
            # נווט לכיוון הבית
            # כאן צריך להוסיף לוגיקה של compass/IMU לזיהוי כיוון הרכב
            # לעת עתה פשוט נסע קדימה
            servo_forward(50)
        
        time.sleep(0.5)

# ========== Servo Functions ==========
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

# ========== Ultrasonic ==========
def get_distance():
    try:
        GPIO.output(ULTRASONIC_TRIG, GPIO.LOW)
        time.sleep(0.00001)
        GPIO.output(ULTRASONIC_TRIG, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(ULTRASONIC_TRIG, GPIO.LOW)
        
        timeout = time.time() + 0.1
        
        while GPIO.input(ULTRASONIC_ECHO) == GPIO.LOW:
            pulse_start = time.time()
            if pulse_start > timeout:
                return -1
        
        while GPIO.input(ULTRASONIC_ECHO) == GPIO.HIGH:
            pulse_end = time.time()
            if pulse_end > timeout:
                return -1
        
        pulse_duration = pulse_end - pulse_start
        distance = pulse_duration * 17150
        distance = round(distance, 2)
        
        return distance if distance < 400 else 400
    except:
        return -1

def distance_monitor():
    global current_distance, obstacle_warning
    while True:
        dist = get_distance()
        if dist > 0:
            current_distance = dist
            obstacle_warning = dist < 20
            
            if obstacle_warning and auto_avoid:
                servo_stop()
                socketio.emit('obstacle_detected', {'distance': dist})
        
        time.sleep(0.1)

# ========== Camera & Recording ==========
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

def take_photo():
    """צלם תמונה בודדת"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{MEDIA_DIR}/photos/photo_{timestamp}.jpg'
        
        # צלם תמונה באיכות גבוהה
        picam2.stop_recording()
        photo_config = picam2.create_still_configuration(main={"size": (1920, 1080)})
        picam2.configure(photo_config)
        picam2.start()
        picam2.capture_file(filename)
        picam2.stop()
        
        # חזור למצב סטרימינג
        picam2.configure(config)
        picam2.start_recording(JpegEncoder(), FileOutput(output))
        
        print(f"📸 תמונה נשמרה: {filename}")
        return filename
    except Exception as e:
        print(f"❌ שגיאה בצילום: {e}")
        return None

def start_video_recording():
    """התחל הקלטת וידאו"""
    global is_recording, recording_thread
    
    if is_recording:
        return False
    
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{MEDIA_DIR}/videos/video_{timestamp}.h264'
        
        # עצור סטרימינג והתחל הקלטה
        picam2.stop_recording()
        video_config = picam2.create_video_configuration()
        picam2.configure(video_config)
        
        encoder = H264Encoder(bitrate=10000000)
        picam2.start_recording(encoder, filename)
        
        is_recording = True
        print(f"🎥 מתחיל הקלטה: {filename}")
        return filename
    except Exception as e:
        print(f"❌ שגיאה בהקלטה: {e}")
        return None

def stop_video_recording():
    """עצור הקלטת וידאו"""
    global is_recording
    
    if not is_recording:
        return None
    
    try:
        picam2.stop_recording()
        
        # חזור למצב סטרימינג
        picam2.configure(config)
        picam2.start_recording(JpegEncoder(), FileOutput(output))
        
        is_recording = False
        print("⏹️ הקלטה נעצרה")
        return True
    except Exception as e:
        print(f"❌ שגיאה בעצירת הקלטה: {e}")
        return False

# ========== Flask Routes ==========
@app.route('/')
def index():
    return render_template('control_with_gamepad.html')

def generate():
    while True:
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status')
def status():
    return jsonify({
        'distance': current_distance,
        'obstacle_warning': obstacle_warning,
        'lights_on': lights_on,
        'auto_avoid': auto_avoid,
        'is_recording': is_recording,
        'gps': gps_data,
        'has_home': home_position is not None,
        'auto_return_active': auto_return_active
    })

@app.route('/media/photos')
def list_photos():
    """רשימת תמונות"""
    photos = sorted(os.listdir(f'{MEDIA_DIR}/photos'), reverse=True)
    return jsonify({'photos': photos})

@app.route('/media/videos')
def list_videos():
    """רשימת סרטונים"""
    videos = sorted(os.listdir(f'{MEDIA_DIR}/videos'), reverse=True)
    return jsonify({'videos': videos})

@app.route('/media/photo/<filename>')
def get_photo(filename):
    """הורד תמונה"""
    return send_file(f'{MEDIA_DIR}/photos/{filename}')

@app.route('/media/video/<filename>')
def get_video(filename):
    """הורד וידאו"""
    return send_file(f'{MEDIA_DIR}/videos/{filename}')

# ========== Socket Events ==========
@socketio.on('command')
def handle_command(data):
    if auto_return_active:
        return  # אל תקבל פקודות בזמן ניווט אוטומטי
    
    cmd = data.get('command')
    speed = data.get('speed', 70)
    
    if cmd == 'forward' and obstacle_warning and auto_avoid:
        socketio.emit('blocked', {'message': 'מכשול מזוהה!'})
        return
    
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

@socketio.on('lights')
def handle_lights(data):
    global lights_on
    lights_on = not lights_on
    GPIO.output(LED_FRONT, GPIO.HIGH if lights_on else GPIO.LOW)
    socketio.emit('lights_status', {'on': lights_on})

@socketio.on('auto_avoid')
def handle_auto_avoid(data):
    global auto_avoid
    auto_avoid = data.get('enabled', False)
    socketio.emit('auto_avoid_status', {'enabled': auto_avoid})

@socketio.on('take_photo')
def handle_take_photo(data):
    """צלם תמונה"""
    filename = take_photo()
    if filename:
        socketio.emit('photo_taken', {'filename': os.path.basename(filename)})
    else:
        socketio.emit('photo_error', {'message': 'שגיאה בצילום'})

@socketio.on('start_recording')
def handle_start_recording(data):
    """התחל הקלטה"""
    filename = start_video_recording()
    if filename:
        socketio.emit('recording_started', {'filename': os.path.basename(filename)})
    else:
        socketio.emit('recording_error', {'message': 'שגיאה בהקלטה'})

@socketio.on('stop_recording')
def handle_stop_recording(data):
    """עצור הקלטה"""
    if stop_video_recording():
        socketio.emit('recording_stopped', {})
    else:
        socketio.emit('recording_error', {'message': 'שגיאה בעצירת הקלטה'})

@socketio.on('set_home')
def handle_set_home(data):
    """קבע נקודת בית"""
    global home_position
    if gps_data['latitude']:
        home_position = {
            'latitude': gps_data['latitude'],
            'longitude': gps_data['longitude'],
            'timestamp': datetime.now().isoformat()
        }
        socketio.emit('home_set', {'position': home_position})
        print(f"🏠 נקודת בית נקבעה: {home_position}")
    else:
        socketio.emit('gps_error', {'message': 'אין אות GPS'})

@socketio.on('return_home')
def handle_return_home(data):
    """חזור לבית"""
    thread = threading.Thread(target=auto_return_home, daemon=True)
    thread.start()

@socketio.on('cancel_return')
def handle_cancel_return(data):
    """בטל חזרה לבית"""
    global auto_return_active
    auto_return_active = False
    servo_stop()
    socketio.emit('navigation_cancelled', {})

# ========== Startup ==========
if __name__ == '__main__':
    # התחל מעקב מרחק
    distance_thread = threading.Thread(target=distance_monitor, daemon=True)
    distance_thread.start()
    
    try:
        print("=" * 50)
        print("🚗 שרת מכונית RC פועל!")
        print(f"📍 GPS: {'זמין ✅' if GPS_AVAILABLE else 'לא זמין ❌'}")
        print(f"📁 מדיה נשמרת ב: {MEDIA_DIR}")
        print("=" * 50)
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    finally:
        servo_stop()
        GPIO.output(LED_FRONT, GPIO.LOW)
        GPIO.cleanup()
        picam2.stop_recording()
        if gps_reader:
            gps_reader.stop()