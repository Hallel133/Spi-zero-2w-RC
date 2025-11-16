#!/usr/bin/env python3
"""
אפליקציית בקרת מכונית RC עם שלט DualSense (PS5)
מחבר בין שלט PS5 למכונית דרך WiFi/LTE
"""

import socketio
from pydualsense import pydualsense, TriggerModes
import time
import sys
from threading import Thread
import argparse

class DualSenseCarController:
    def __init__(self, car_ip, car_port=5000):
        """
        אתחול בקר
        :param car_ip: כתובת IP של המכונית
        :param car_port: פורט השרת (ברירת מחדל 5000)
        """
        self.car_ip = car_ip
        self.car_port = car_port
        
        # חיבור לשרת המכונית
        self.sio = socketio.Client()
        self.connected_to_car = False
        
        # אתחול שלט DualSense
        self.ds = pydualsense()
        self.controller_connected = False
        
        # מצב נוכחי
        self.current_command = 'stop'
        self.current_speed = 70
        self.lights_on = False
        self.auto_avoid = False
        
        # deadzone לג'ויסטיקים
        self.deadzone = 20
        
        print("🎮 מאתחל בקר DualSense...")
        
    def connect_controller(self):
        """חיבור לשלט DualSense"""
        try:
            self.ds.init()
            self.controller_connected = True
            print("✅ שלט DualSense מחובר!")
            
            # הגדר צבע LED בשלט (כחול = מוכן)
            self.ds.light.setColorI(0, 0, 255)
            
            # הגדר טריגרים רגועים
            self.ds.triggerL.setMode(TriggerModes.Off)
            self.ds.triggerR.setMode(TriggerModes.Off)
            
            return True
        except Exception as e:
            print(f"❌ שגיאה בחיבור לשלט: {e}")
            print("ודא ששלט DualSense מחובר ב-USB או Bluetooth")
            return False
    
    def connect_to_car(self):
        """חיבור לשרת המכונית"""
        try:
            @self.sio.event
            def connect():
                print(f"✅ מחובר למכונית ב-{self.car_ip}:{self.car_port}")
                self.connected_to_car = True
                # שנה LED לירוק = מחובר
                self.ds.light.setColorI(0, 255, 0)
            
            @self.sio.event
            def disconnect():
                print("⚠️ נותק מהמכונית")
                self.connected_to_car = False
                # שנה LED לאדום = מנותק
                self.ds.light.setColorI(255, 0, 0)
            
            @self.sio.on('obstacle_detected')
            def on_obstacle(data):
                # רטט בשלט כשמזוהה מכשול
                print(f"⚠️ מכשול מזוהה! מרחק: {data['distance']}ס\"מ")
                self.ds.triggerL.setMode(TriggerModes.Pulse_A)
                self.ds.triggerR.setMode(TriggerModes.Pulse_A)
                time.sleep(0.3)
                self.ds.triggerL.setMode(TriggerModes.Off)
                self.ds.triggerR.setMode(TriggerModes.Off)
            
            print(f"🔌 מתחבר למכונית ב-{self.car_ip}:{self.car_port}...")
            self.sio.connect(f'http://{self.car_ip}:{self.car_port}')
            return True
            
        except Exception as e:
            print(f"❌ שגיאה בחיבור למכונית: {e}")
            return False
    
    def send_command(self, command, speed=None):
        """שלח פקודה למכונית"""
        if not self.connected_to_car:
            return
        
        if speed is None:
            speed = self.current_speed
        
        if command != self.current_command or speed != self.current_speed:
            self.sio.emit('command', {
                'command': command,
                'speed': speed
            })
            self.current_command = command
            self.current_speed = speed
    
    def toggle_lights(self):
        """הדלק/כבה אורות"""
        if not self.connected_to_car:
            return
        
        self.lights_on = not self.lights_on
        self.sio.emit('lights', {})
        print(f"💡 אורות: {'דלוקים' if self.lights_on else 'כבויים'}")
        
        # רטט קצר
        self.ds.triggerR.setMode(TriggerModes.Pulse_B)
        time.sleep(0.1)
        self.ds.triggerR.setMode(TriggerModes.Off)
    
    def toggle_auto_avoid(self):
        """הפעל/כבה הימנעות אוטומטית"""
        if not self.connected_to_car:
            return
        
        self.auto_avoid = not self.auto_avoid
        self.sio.emit('auto_avoid', {'enabled': self.auto_avoid})
        print(f"🛡️ הימנעות אוטומטית: {'פעילה' if self.auto_avoid else 'כבויה'}")
        
        # רטט קצר
        self.ds.triggerL.setMode(TriggerModes.Pulse_B)
        time.sleep(0.1)
        self.ds.triggerL.setMode(TriggerModes.Off)
    
    def process_joystick(self, x, y):
        """עיבוד קלט מג'ויסטיק ימני"""
        # בדוק deadzone
        if abs(x - 127) < self.deadzone and abs(y - 127) < self.deadzone:
            self.send_command('stop')
            return
        
        # המר ערכים (0-255) למרכוז על 127
        x_centered = x - 127
        y_centered = -(y - 127)  # הפוך Y
        
        # חשב מהירות מהמרחק מהמרכז
        distance = (x_centered**2 + y_centered**2) ** 0.5
        speed = min(100, int((distance / 127) * 100))
        
        # קבע כיוון
        if abs(y_centered) > abs(x_centered):
            # תנועה קדימה/אחורה
            if y_centered > self.deadzone:
                self.send_command('forward', speed)
            elif y_centered < -self.deadzone:
                self.send_command('backward', speed)
        else:
            # פנייה
            if x_centered > self.deadzone:
                self.send_command('right', speed)
            elif x_centered < -self.deadzone:
                self.send_command('left', speed)
    
    def process_dpad(self):
        """עיבוד D-Pad"""
        state = self.ds.state
        
        if state.DpadUp:
            self.send_command('forward', 80)
        elif state.DpadDown:
            self.send_command('backward', 80)
        elif state.DpadLeft:
            self.send_command('left', 80)
        elif state.DpadRight:
            self.send_command('right', 80)
        else:
            # אם לא לוחצים על D-Pad, בדוק ג'ויסטיק
            self.process_joystick(state.RX, state.RY)
    
    def control_loop(self):
        """לולאת בקרה ראשית"""
        print("\n🎮 מיפוי כפתורים:")
        print("├─ ג'ויסטיק ימני: תנועה חופשית")
        print("├─ D-Pad: תנועה בכיוונים")
        print("├─ R2: מהירות (טריגר ימני)")
        print("├─ L1: הדלק/כבה אורות")
        print("├─ R1: הימנעות אוטומטית")
        print("├─ ✕ (X): עצירה חירום")
        print("├─ □ (Square): צלם תמונה")
        print("├─ ○ (Circle): הקלטת וידאו")
        print("├─ △ (Triangle): קבע נקודת בית")
        print("├─ L2: חזור לבית")
        print("└─ OPTIONS: ניתוק\n")
        
        last_r2 = 0
        last_l1 = False
        last_r1 = False
        last_square = False
        last_circle = False
        last_triangle = False
        last_l2 = False
        
        try:
            while self.controller_connected and self.connected_to_car:
                # קרא מצב שלט
                state = self.ds.state
                
                # כפתור X - עצירה חירום
                if state.cross:
                    self.send_command('stop')
                    self.ds.light.setColorI(255, 0, 0)  # אדום
                    time.sleep(0.1)
                    self.ds.light.setColorI(0, 255, 0)  # חזרה לירוק
                    continue
                
                # כפתור OPTIONS - ניתוק
                if state.options:
                    print("👋 מנתק...")
                    break
                
                # L1 - אורות
                if state.L1 and not last_l1:
                    self.toggle_lights()
                last_l1 = state.L1
                
                # R1 - הימנעות אוטומטית
                if state.R1 and not last_r1:
                    self.toggle_auto_avoid()
                last_r1 = state.R1
                
                # R2 - שליטה במהירות (0-255)
                r2_value = state.R2
                if abs(r2_value - last_r2) > 10:
                    self.current_speed = int((r2_value / 255) * 100)
                    print(f"🏎️ מהירות: {self.current_speed}%")
                    last_r2 = r2_value
                
                # עיבוד תנועה
                self.process_dpad()
                
                # עדכון תדיר
                time.sleep(0.05)  # 20Hz
                
        except KeyboardInterrupt:
            print("\n⚠️ נעצר על ידי המשתמש")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """ניקוי וניתוק"""
        print("🧹 מנקה...")
        
        # עצור את המכונית
        if self.connected_to_car:
            self.send_command('stop')
            time.sleep(0.1)
            self.sio.disconnect()
        
        # סגור שלט
        if self.controller_connected:
            self.ds.light.setColorI(0, 0, 0)
            self.ds.close()
        
        print("✅ נסגר בהצלחה")
    
    def run(self):
        """הרץ את הבקר"""
        # חבר שלט
        if not self.connect_controller():
            return False
        
        # חבר למכונית
        if not self.connect_to_car():
            self.ds.close()
            return False
        
        # התחל לולאת בקרה
        self.control_loop()
        
        return True


def main():
    parser = argparse.ArgumentParser(description='בקרת מכונית RC עם שלט DualSense')
    parser.add_argument('car_ip', help='כתובת IP של המכונית')
    parser.add_argument('--port', type=int, default=5000, help='פורט השרת (ברירת מחדל: 5000)')
    
    args = parser.parse_args()
    
    print("=" * 50)
    print("🚗 בקר מכונית RC עם DualSense")
    print("=" * 50)
    
    controller = DualSenseCarController(args.car_ip, args.port)
    
    try:
        controller.run()
    except Exception as e:
        print(f"❌ שגיאה: {e}")
        controller.cleanup()
        sys.exit(1)


if __name__ == '__main__':
    main()
