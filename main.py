#!/usr/bin/env python3
"""
Integrated Raspberry Pi Robot Controller with Action-Specific GIF Player
Monitors Google Sheets for commands, executes robot movements with adjusted timing, and plays action-specific GIFs
"""

import time
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import threading
import os
import sys
import hashlib
import pickle
from queue import Queue
from PIL import Image, ImageSequence

try:
    import gspread
    from google.oauth2.service_account import Credentials
    import RPi.GPIO as GPIO
    from luma.core.interface.serial import spi
    from luma.core.render import canvas
    from luma.lcd.device import ili9486
except ImportError:
    print("Required packages not installed. Run:")
    print("pip install gspread google-auth google-auth-oauthlib google-auth-httplib2 luma.lcd pillow")
    print("sudo apt install python3-pil python3-numpy")
    exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('integrated.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Robot speed configurations
LINEAR_SPEED = 0.7  # Meters per second (e.g., 0.7m takes 1s; increase for faster linear movement)
ROTATION_SPEED = 270  # Degrees per second (e.g., 270° takes 1s; increase for faster rotation)

class RobotController:
    """Handles all robot movement and sensor operations using RPi.GPIO only"""

    def __init__(self, motor_a_pins, motor_b_pins, front_ultrasonic_pins, rear_ultrasonic_pins):
        """
        Initialize robot controller

        Args:
            motor_a_pins: [forward_pin, backward_pin, pwm_pin] for motor A (BCM)
            motor_b_pins: [forward_pin, backward_pin, pwm_pin] for motor B (BCM)
            front_ultrasonic_pins: [trigger_pin, echo_pin] for front ultrasonic sensor (BCM)
            rear_ultrasonic_pins: [trigger_pin, echo_pin] for rear ultrasonic sensor (BCM)
        """
        self.MOTOR_A = motor_a_pins
        self.MOTOR_B = motor_b_pins
        self.FRONT_USENSE = front_ultrasonic_pins
        self.REAR_USENSE = rear_ultrasonic_pins

        # Robot parameters (configurable via global constants)
        self.speed = LINEAR_SPEED  # Meters per second
        self.rot_speed = ROTATION_SPEED  # Degrees per second
        self.obstacle_threshold = 20.0  # 20cm obstacle detection threshold

        # State tracking
        self.robot_stopped = False
        self.pwm_a = None
        self.pwm_b = None

        self.setup_gpio()

    def setup_gpio(self):
        """Initialize GPIO settings"""
        try:
            GPIO.setwarnings(False)
            all_motor_pins = [
                self.MOTOR_A[0], self.MOTOR_A[1], self.MOTOR_A[2],
                self.MOTOR_B[0], self.MOTOR_B[1], self.MOTOR_B[2]
            ]
            GPIO.setup(all_motor_pins, GPIO.OUT)
            GPIO.setup(self.FRONT_USENSE[0], GPIO.OUT)
            GPIO.setup(self.FRONT_USENSE[1], GPIO.IN)
            GPIO.setup(self.REAR_USENSE[0], GPIO.OUT)
            GPIO.setup(self.REAR_USENSE[1], GPIO.IN)
            GPIO.output(all_motor_pins, GPIO.LOW)
            logger.info("GPIO setup completed successfully")
        except Exception as e:
            logger.error(f"GPIO setup failed: {e}")
            raise

    def measure_distance(self, sensor):
        """Measure distance using specified ultrasonic sensor (in cm)"""
        try:
            GPIO.output(sensor[0], True)
            time.sleep(0.00001)
            GPIO.output(sensor[0], False)
            pulse_start = time.time()
            timeout = pulse_start + 0.1
            while GPIO.input(sensor[1]) == 0 and time.time() < timeout:
                pulse_start = time.time()
            pulse_end = time.time()
            timeout = pulse_end + 0.1
            while GPIO.input(sensor[1]) == 1 and time.time() < timeout:
                pulse_end = time.time()
            pulse_duration = pulse_end - pulse_start
            distance = pulse_duration * 17150
            return round(distance, 2)
        except Exception as e:
            logger.error(f"Error measuring distance: {e}")
            return 999

    def check_obstacle(self, sensor):
        """Check if there's an obstacle using specified sensor"""
        distance = self.measure_distance(sensor)
        logger.debug(f"Distance measured: {distance}cm")
        return distance < self.obstacle_threshold

    def cleanup_gpio(self):
        """Clean up GPIO resources"""
        try:
            if self.pwm_a:
                self.pwm_a.stop()
            if self.pwm_b:
                self.pwm_b.stop()
            GPIO.cleanup()
            logger.info("GPIO cleanup completed")
        except Exception as e:
            logger.error(f"GPIO cleanup error: {e}")

    def emergency_stop_forward(self):
        """Emergency stop due to front obstacle detection"""
        logger.warning("FRONT OBSTACLE DETECTED! Emergency stop activated")
        self.robot_stopped = True
        if self.pwm_a and self.pwm_b:
            GPIO.output([self.MOTOR_A[0], self.MOTOR_A[1], self.MOTOR_B[0], self.MOTOR_B[1]], GPIO.LOW)
            self.pwm_a.ChangeDutyCycle(0)
            self.pwm_b.ChangeDutyCycle(0)
        self.handle_obstacle_detected_backward()  # Move backward to avoid front obstacle

    def emergency_stop_backward(self):
        """Emergency stop due to rear obstacle detection"""
        logger.warning("REAR OBSTACLE DETECTED! Emergency stop activated")
        self.robot_stopped = True
        if self.pwm_a and self.pwm_b:
            GPIO.output([self.MOTOR_A[0], self.MOTOR_A[1], self.MOTOR_B[0], self.MOTOR_B[1]], GPIO.LOW)
            self.pwm_a.ChangeDutyCycle(0)
            self.pwm_b.ChangeDutyCycle(0)
        self.handle_obstacle_detected_forward()  # Move forward to avoid rear obstacle

    def handle_obstacle_detected_backward(self):
        """Handle front obstacle by moving backward briefly"""
        logger.info("Moving backward to avoid front obstacle")
        try:
            GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.HIGH)
            GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.LOW)
            if self.pwm_a and self.pwm_b:
                self.pwm_a.ChangeDutyCycle(30)
                self.pwm_b.ChangeDutyCycle(30)
            time.sleep(0.5)
            GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.LOW)
            if self.pwm_a and self.pwm_b:
                self.pwm_a.ChangeDutyCycle(0)
                self.pwm_b.ChangeDutyCycle(0)
            logger.info("Backward movement completed")
        except Exception as e:
            logger.error(f"Error during front obstacle handling: {e}")

    def handle_obstacle_detected_forward(self):
        """Handle rear obstacle by moving forward briefly"""
        logger.info("Moving forward to avoid rear obstacle")
        try:
            GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.HIGH)
            GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.LOW)
            if self.pwm_a and self.pwm_b:
                self.pwm_a.ChangeDutyCycle(30)
                self.pwm_b.ChangeDutyCycle(30)
            time.sleep(0.5)
            GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.LOW)
            if self.pwm_a and self.pwm_b:
                self.pwm_a.ChangeDutyCycle(0)
                self.pwm_b.ChangeDutyCycle(0)
            logger.info("Forward movement completed")
        except Exception as e:
            logger.error(f"Error during rear obstacle handling: {e}")

    def move_forward_with_obstacle_detection(self, distance: float = 1.0):
        """Move forward with front obstacle detection"""
        logger.info(f"Moving forward {distance} meters with front obstacle detection")
        try:
            self.robot_stopped = False
            self.pwm_a = GPIO.PWM(self.MOTOR_A[2], 200)
            self.pwm_b = GPIO.PWM(self.MOTOR_B[2], 200)
            self.pwm_a.start(0)
            self.pwm_b.start(0)
            GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.HIGH)
            GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.LOW)
            self.pwm_a.ChangeDutyCycle(50)
            self.pwm_b.ChangeDutyCycle(50)
            target_duration = distance / self.speed
            start_time = time.time()
            while (time.time() - start_time) < target_duration and not self.robot_stopped:
                if self.check_obstacle(self.FRONT_USENSE):
                    self.emergency_stop_forward()
                    break
                time.sleep(0.1)
            if not self.robot_stopped:
                GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.LOW)
                self.pwm_a.ChangeDutyCycle(0)
                self.pwm_b.ChangeDutyCycle(0)
            self.pwm_a.stop()
            self.pwm_b.stop()
            self.pwm_a = None
            self.pwm_b = None
            return not self.robot_stopped
        except Exception as e:
            logger.error(f"Error in forward movement: {e}")
            return False

    def move_backward(self, distance: float = 1.0):
        """Move backward with rear obstacle detection"""
        logger.info(f"Moving backward {distance} meters with rear obstacle detection")
        try:
            self.robot_stopped = False
            self.pwm_a = GPIO.PWM(self.MOTOR_A[2], 200)
            self.pwm_b = GPIO.PWM(self.MOTOR_B[2], 200)
            self.pwm_a.start(0)
            self.pwm_b.start(0)
            GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.HIGH)
            GPIO.output([self.MOTOR_A[0], self.MOTOR_B[0]], GPIO.LOW)
            self.pwm_a.ChangeDutyCycle(50)
            self.pwm_b.ChangeDutyCycle(50)
            target_duration = distance / self.speed
            start_time = time.time()
            while (time.time() - start_time) < target_duration and not self.robot_stopped:
                if self.check_obstacle(self.REAR_USENSE):
                    self.emergency_stop_backward()
                    break
                time.sleep(0.1)
            if not self.robot_stopped:
                GPIO.output([self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.LOW)
                self.pwm_a.ChangeDutyCycle(0)
                self.pwm_b.ChangeDutyCycle(0)
            self.pwm_a.stop()
            self.pwm_b.stop()
            self.pwm_a = None
            self.pwm_b = None
            return not self.robot_stopped
        except Exception as e:
            logger.error(f"Error in backward movement: {e}")
            return False

    def turn(self, angle: float):
        """Turn the robot by specified angle (negative = left, positive = right)"""
        logger.info(f"Turning {angle} degrees")
        if angle == 0:
            logger.warning("Turn angle is 0 degrees, skipping")
            return True
        try:
            pwm_a = GPIO.PWM(self.MOTOR_A[2], 90)
            pwm_b = GPIO.PWM(self.MOTOR_B[2], 90)
            pwm_a.start(0)
            pwm_b.start(0)
            GPIO.setup([self.MOTOR_A[0], self.MOTOR_A[1], self.MOTOR_B[0], self.MOTOR_B[1]], GPIO.OUT)
            if angle < 0:  # Turn left
                GPIO.output(self.MOTOR_A[0], GPIO.HIGH)
                GPIO.output([self.MOTOR_A[1], self.MOTOR_B[0], self.MOTOR_B[1]], GPIO.LOW)
                pwm_a.ChangeDutyCycle(50)
                pwm_b.ChangeDutyCycle(50)
                time.sleep(abs(angle) / self.rot_speed)
                GPIO.output(self.MOTOR_A[0], GPIO.LOW)
            elif angle > 0:  # Turn right
                GPIO.output(self.MOTOR_B[0], GPIO.HIGH)
                GPIO.output([self.MOTOR_A[0], self.MOTOR_A[1], self.MOTOR_B[1]], GPIO.LOW)
                pwm_a.ChangeDutyCycle(50)
                pwm_b.ChangeDutyCycle(50)
                time.sleep(abs(angle) / self.rot_speed)
                GPIO.output(self.MOTOR_B[0], GPIO.LOW)
            pwm_a.stop()
            pwm_b.stop()
            return True
        except Exception as e:
            logger.error(f"Error in turn movement: {e}")
            return False

class CommandExecutor:
    def __init__(self, credentials_file: str, spreadsheet_name: str, worksheet_name: str = "Sheet1",
                 motor_a_pins=[17, 27, 22], motor_b_pins=[18, 23, 12], front_ultrasonic_pins=[5, 6],
                 rear_ultrasonic_pins=[13, 19], action_queue=None):
        """
        Initialize the CommandExecutor with robot control

        Args:
            credentials_file: Path to Google service account credentials JSON file
            spreadsheet_name: Name of the Google Spreadsheet
            worksheet_name: Name of the worksheet (default: "Sheet1")
            motor_a_pins: [forward, backward, pwm] pins for motor A (BCM)
            motor_b_pins: [forward, backward, pwm] pins for motor B (BCM)
            front_ultrasonic_pins: [trigger, echo] pins for front ultrasonic sensor (BCM)
            rear_ultrasonic_pins: [trigger, echo] pins for rear ultrasonic sensor (BCM)
            action_queue: Queue to communicate actions to GIF player
        """
        self.credentials_file = credentials_file
        self.spreadsheet_name = spreadsheet_name
        self.worksheet_name = worksheet_name
        self.last_command_id = None
        self.client = None
        self.worksheet = None
        self.action_queue = action_queue
        self.robot = RobotController(motor_a_pins, motor_b_pins, front_ultrasonic_pins, rear_ultrasonic_pins)
        self.command_map = {
            'forward': self.move_forward,
            'backward': self.move_backward,
            'left turn': self.turn_left,
            'right turn': self.turn_right,
            'dance': self.dance,
            'hi': self.say_hi,
            'stop': self.stop_robot
        }
        self.setup_google_sheets()

    def __del__(self):
        """Cleanup when object is destroyed"""
        if hasattr(self, 'robot'):
            self.robot.cleanup_gpio()

    def setup_google_sheets(self):
        """Setup Google Sheets API connection"""
        try:
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(self.credentials_file, scopes=scopes)
            self.client = gspread.authorize(creds)
            spreadsheet = self.client.open(self.spreadsheet_name)
            self.worksheet = spreadsheet.worksheet(self.worksheet_name)
            logger.info(f"Successfully connected to Google Sheet: {self.spreadsheet_name}")
        except Exception as e:
            logger.error(f"Failed to setup Google Sheets: {e}")
            raise

    def parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse timestamp from various formats"""
        formats = [
            "%d/%m/%Y, %H:%M:%S",
            "%d/%m/%Y, %I:%M:%S %p",
            "%d/%m/%Y, %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(timestamp_str.strip(), fmt)
            except ValueError:
                continue
        logger.warning(f"Could not parse timestamp: {timestamp_str}")
        return None

    def is_within_time_range(self, timestamp_str: str, max_age_seconds: int = 20) -> bool:
        """Check if the timestamp is within acceptable range"""
        command_time = self.parse_timestamp(timestamp_str)
        if not command_time:
            return False
        current_time = datetime.now()
        time_diff = abs((current_time - command_time).total_seconds())
        logger.debug(f"Time difference: {time_diff} seconds")
        return time_diff <= max_age_seconds

    def get_row2_command(self) -> Optional[Dict[str, Any]]:
        """Get command data from row 2 only"""
        try:
            row_data = self.worksheet.row_values(2)
            if len(row_data) < 3:
                logger.debug("Row 2 doesn't have enough data columns")
                return None
            return {
                'row_index': 2,
                'timestamp': row_data[0].strip() if row_data[0] else '',
                'command': row_data[1].lower().strip() if row_data[1] else '',
                'distance': row_data[2].strip() if row_data[2] else '1',
                'status': row_data[3].strip() if len(row_data) > 3 and row_data[3] else ''
            }
        except Exception as e:
            logger.error(f"Error getting row 2 command: {e}")
            return None

    def update_status(self, row_index: int, status: str):
        """Update the status column for the given row"""
        try:
            self.worksheet.update_cell(row_index, 4, status)
            logger.info(f"Updated row {row_index} status to: {status}")
        except Exception as e:
            logger.error(f"Error updating status: {e}")

    def move_forward(self, distance: str = "1"):
        """Execute forward movement with obstacle detection"""
        try:
            dist_value = float(distance) if distance.replace('.', '').isdigit() else 1.0
            self.action_queue.put(('forward', dist_value / self.robot.speed))
            success = self.robot.move_forward_with_obstacle_detection(dist_value)
            if self.robot.robot_stopped:
                self.action_queue.put(('obstacle', 2.0))
            return success
        except Exception as e:
            logger.error(f"Error in move_forward: {e}")
            return False

    def move_backward(self, distance: str = "1"):
        """Execute backward movement with rear obstacle detection"""
        try:
            dist_value = float(distance) if distance.replace('.', '').isdigit() else 1.0
            self.action_queue.put(('backward', dist_value / self.robot.speed))
            success = self.robot.move_backward(dist_value)
            if self.robot.robot_stopped:
                self.action_queue.put(('obstacle', 2.0))
            return success
        except Exception as e:
            logger.error(f"Error in move_backward: {e}")
            return False

    def turn_left(self, angle: str = "90"):
        """Execute left turn"""
        try:
            angle_value = float(angle) if angle.replace('.', '').isdigit() else 90.0
            self.action_queue.put(('left', abs(angle_value) / self.robot.rot_speed))
            return self.robot.turn(-angle_value)
        except Exception as e:
            logger.error(f"Error in turn_left: {e}")
            return False

    def turn_right(self, angle: str = "90"):
        """Execute right turn"""
        try:
            angle_value = float(angle) if angle.replace('.', '').isdigit() else 90.0
            self.action_queue.put(('right', abs(angle_value) / self.robot.rot_speed))
            return self.robot.turn(angle_value)
        except Exception as e:
            logger.error(f"Error in turn_right: {e}")
            return False

    def dance(self, duration: str = "1"):
        """Execute dance routine"""
        logger.info("Executing dance routine")
        try:
            self.action_queue.put(('dance', 3.0))  # Adjusted for longer sequence
            success = self.robot.move_forward_with_obstacle_detection(0.5)
            if not success:
                self.action_queue.put(('obstacle', 2.0))
                return False
            time.sleep(0.2)
            success = self.robot.move_backward(0.5)
            if not success:
                self.action_queue.put(('obstacle', 2.0))
                return False
            time.sleep(0.2)
            self.robot.turn(-45)
            time.sleep(0.2)
            self.robot.turn(90)
            time.sleep(0.2)
            self.robot.turn(-45)
            return True
        except Exception as e:
            logger.error(f"Error in dance: {e}")
            return False

    def say_hi(self, duration: str = "1"):
        """Execute greeting routine (rotate left and right)"""
        logger.info("Saying hi with rotation")
        try:
            self.action_queue.put(('hi', 1.5))  # Adjusted for sequence
            self.robot.turn(-30)
            time.sleep(0.3)
            self.robot.turn(60)
            time.sleep(0.3)
            self.robot.turn(-30)
            return True
        except Exception as e:
            logger.error(f"Error in say_hi: {e}")
            return False

    def stop_robot(self, duration: str = "1"):
        """Emergency stop command"""
        logger.info("Emergency stop command received")
        try:
            self.action_queue.put(('obstacle', 2.0))
            self.robot.emergency_stop_forward()  # Or backward, depending on context; default to front
            return True
        except Exception as e:
            logger.error(f"Error in stop_robot: {e}")
            return False

    def execute_command(self, command_data: Dict[str, Any]) -> bool:
        """Execute the given command"""
        command = command_data['command']
        distance = command_data.get('distance', '1')
        if command in self.command_map:
            try:
                return self.command_map[command](distance)
            except Exception as e:
                logger.error(f"Error executing command '{command}': {e}")
                return False
        else:
            logger.warning(f"Unknown command: {command}")
            return False

    def run(self, check_interval: float = 1.0):
        """Main monitoring loop"""
        logger.info("Starting command monitoring with robot control...")
        try:
            while True:
                try:
                    command_data = self.get_row2_command()
                    if not command_data:
                        self.action_queue.put(('idle', 0))
                        time.sleep(check_interval)
                        continue
                    current_status = command_data['status']
                    current_timestamp = command_data['timestamp']
                    current_command = command_data['command']
                    current_command_id = f"{current_timestamp}:{current_command}"
                    if (hasattr(self, 'last_command_id') and
                        current_command_id == self.last_command_id):
                        self.action_queue.put(('idle', 0))
                        time.sleep(check_interval)
                        continue
                    logger.info(f"New command detected: {current_command} at {current_timestamp}")
                    if current_status.upper() not in ['OK', 'ERROR', 'EXPIRED']:
                        if self.is_within_time_range(current_timestamp):
                            logger.info(f"Executing command: {current_command} with parameter: {command_data['distance']}")
                            success = self.execute_command(command_data)
                            if success:
                                self.update_status(2, 'OK')
                                logger.info(f"Command '{current_command}' executed successfully")
                            else:
                                self.update_status(2, 'ERROR')
                                logger.error(f"Command '{current_command}' execution failed")
                        else:
                            logger.warning(f"Command timestamp too old: {current_timestamp}")
                            self.update_status(2, 'EXPIRED')
                    else:
                        logger.debug(f"Command already processed (status: {current_status})")
                    self.last_command_id = current_command_id
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                time.sleep(check_interval)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.robot.cleanup_gpio()

class PortraitGifPlayer:
    def __init__(self, action_queue, gif_dir="gifs"):
        """
        Initialize the LCD display for action-specific GIF playback

        Args:
            action_queue: Queue to receive actions from CommandExecutor
            gif_dir: Directory containing action-specific GIFs
        """
        self.device = None
        self.display_width = 320
        self.display_height = 480
        self.gif_dir = gif_dir
        self.action_queue = action_queue
        self.cache_dir = "gif_cache"
        self.current_action = 'idle'
        self.stop_event = threading.Event()
        self.setup_cache_directory()
        self.setup_display()
        self.gif_map = {
            'forward': 'forward.gif',
            'backward': 'backwards.gif',
            'left': 'left.gif',
            'right': 'right.gif',
            'dance': 'dance.gif',
            'hi': 'hi.gif',
            'obstacle': 'obstacle.gif',
            'idle': 'idle.gif'
        }

    def setup_cache_directory(self):
        """Create cache directory for storing processed GIF frames"""
        try:
            if not os.path.exists(self.cache_dir):
                os.makedirs(self.cache_dir)
                logger.info(f"Created cache directory: {self.cache_dir}")
            else:
                logger.info(f"Using cache directory: {self.cache_dir}")
        except Exception as e:
            logger.error(f"Failed to create cache directory: {e}")
            self.cache_dir = None

    def get_gif_cache_path(self, gif_path):
        """Generate cache file path for a GIF"""
        if not self.cache_dir:
            return None
        try:
            stat = os.stat(gif_path)
            file_size = stat.st_size
            mod_time = stat.st_mtime
            hash_input = f"{os.path.basename(gif_path)}_{file_size}_{mod_time}_{self.display_width}x{self.display_height}"
            file_hash = hashlib.md5(hash_input.encode()).hexdigest()
            cache_filename = f"gif_{file_hash}.cache"
            return os.path.join(self.cache_dir, cache_filename)
        except Exception as e:
            logger.error(f"Error generating cache path: {e}")
            return None

    def save_processed_frames(self, cache_path, processed_frames, durations, is_landscape):
        """Save processed frames to cache file"""
        try:
            cache_data = {
                'frames': processed_frames,
                'durations': durations,
                'is_landscape': is_landscape,
                'display_size': (self.display_width, self.display_height),
                'version': '1.0'
            }
            with open(cache_path, 'wb') as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(f"Cached processed frames to: {os.path.basename(cache_path)}")
        except Exception as e:
            logger.error(f"Error saving cache: {e}")

    def load_processed_frames(self, cache_path):
        """Load processed frames from cache file"""
        try:
            if not os.path.exists(cache_path):
                return None, None, None
            with open(cache_path, 'rb') as f:
                cache_data = pickle.load(f)
            if (cache_data.get('display_size') != (self.display_width, self.display_height) or
                cache_data.get('version') != '1.0'):
                logger.info("Cache incompatible with current settings, will regenerate")
                return None, None, None
            logger.info(f"Loaded cached frames from: {os.path.basename(cache_path)}")
            return cache_data['frames'], cache_data['durations'], cache_data['is_landscape']
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
            return None, None, None

    def clean_old_cache_files(self, max_age_days=7):
        """Clean up old cache files"""
        if not self.cache_dir or not os.path.exists(self.cache_dir):
            return
        try:
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 60 * 60
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.cache'):
                    file_path = os.path.join(self.cache_dir, filename)
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        logger.info(f"Removed old cache file: {filename}")
        except Exception as e:
            logger.error(f"Error cleaning cache: {e}")

    def setup_display(self):
        """Setup the Waveshare 3.5" LCD display in portrait mode"""
        try:
            serial = spi(
                device=0,
                port=0,
                bus_speed_hz=32000000,
                reset_pin=25,
                dc_pin=24,
                cs_pin=8,
                rst_active_low=False
            )
            self.device = ili9486(
                serial,
                width=320,
                height=480,
                rotate=0,
                bgr=False
            )
            logger.info(f"Display initialized in portrait mode: {self.device.width}x{self.device.height}")
            with canvas(self.device) as draw:
                draw.rectangle(self.device.bounding_box, outline="black", fill="black")
        except Exception as e:
            logger.error(f"Failed to initialize display: {e}")
            raise

    def invert_colors_fast(self, image):
        """Fast color inversion using numpy"""
        import numpy as np
        if image.mode != 'RGB':
            image = image.convert('RGB')
        img_array = np.array(image)
        inverted_array = 255 - img_array
        return Image.fromarray(inverted_array.astype('uint8'))

    def prepare_image_for_portrait(self, image):
        """Prepare an image for portrait display"""
        orig_width, orig_height = image.size
        is_landscape = orig_width > orig_height
        if is_landscape:
            # Rotate landscape image 90 degrees counterclockwise to fit portrait display
            # To change direction, swap to -90 for clockwise
            image = image.rotate(90, expand=True)
            logger.debug(f"Rotated landscape image: {orig_width}x{orig_height} -> {image.size[0]}x{image.size[1]}")
        img_width, img_height = image.size
        scale_w = self.display_width / img_width
        scale_h = self.display_height / img_height
        scale = min(scale_w, scale_h)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)
        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        centered = Image.new('RGB', (self.display_width, self.display_height), 'black')
        paste_x = (self.display_width - new_width) // 2
        paste_y = (self.display_height - new_height) // 2
        centered.paste(resized, (paste_x, paste_y))
        return self.invert_colors_fast(centered)

    def preprocess_gif_frames(self, gif_path):
        """Pre-process all GIF frames with caching support"""
        cache_path = self.get_gif_cache_path(gif_path)
        if cache_path:
            cached_frames, cached_durations, cached_is_landscape = self.load_processed_frames(cache_path)
            if cached_frames is not None:
                logger.info(f"Using cached frames ({len(cached_frames)} frames)")
                return cached_frames, cached_durations, cached_is_landscape
        logger.info("Cache miss - processing GIF frames...")
        try:
            gif = Image.open(gif_path)
            frame_count = gif.n_frames if hasattr(gif, 'n_frames') else 1
            is_landscape = gif.size[0] > gif.size[1]
            logger.info(f"Processing {frame_count} frames...")
            processed_frames = []
            durations = []
            for i, frame in enumerate(ImageSequence.Iterator(gif)):
                if frame.mode != 'RGB':
                    frame = frame.convert('RGB')
                processed_frame = self.prepare_image_for_portrait(frame)
                processed_frames.append(processed_frame)
                duration = frame.info.get('duration', 100) / 1000.0
                durations.append(duration)
                if frame_count > 10 and (i + 1) % 5 == 0:
                    logger.info(f"Processed {i + 1}/{frame_count} frames...")
            logger.info(f"Processing complete! ({len(processed_frames)} frames)")
            if cache_path:
                self.save_processed_frames(cache_path, processed_frames, durations, is_landscape)
            return processed_frames, durations, is_landscape
        except Exception as e:
            logger.error(f"Error pre-processing GIF: {e}")
            return [], [], False

    def play_gif_with_time_limit(self, gif_path, time_limit):
        """Play GIF for a specific time limit"""
        if not os.path.exists(gif_path):
            logger.error(f"GIF file not found: {gif_path}")
            return
        processed_frames, durations, is_landscape = self.preprocess_gif_frames(gif_path)
        if not processed_frames:
            return
        logger.info(f"Playing GIF: {os.path.basename(gif_path)} for {time_limit}s")
        if is_landscape:
            logger.info("Landscape GIF rotated for portrait display")
        start_time = time.time()
        frame_index = 0
        try:
            while (time.time() - start_time) < time_limit and not self.stop_event.is_set():
                frame_start = time.time()
                self.device.display(processed_frames[frame_index])
                elapsed = time.time() - frame_start
                sleep_time = max(0, durations[frame_index] - elapsed)
                time.sleep(sleep_time)
                frame_index = (frame_index + 1) % len(processed_frames)
        except Exception as e:
            logger.error(f"Error in time-limited playback: {e}")

    def run(self):
        """Main GIF player loop, reacting to actions"""
        self.show_text("Robot GIF Player\nReady!", duration=3)
        self.clean_old_cache_files(max_age_days=7)
        while not self.stop_event.is_set():
            try:
                action, duration = self.action_queue.get(timeout=1.0)
                self.stop_event.clear()
                gif_filename = self.gif_map.get(action, 'idle.gif')
                gif_path = os.path.join(self.gif_dir, gif_filename)
                if os.path.exists(gif_path):
                    self.play_gif_with_time_limit(gif_path, duration if duration > 0 else 3600)
                else:
                    logger.warning(f"GIF not found: {gif_path}, playing idle.gif")
                    idle_path = os.path.join(self.gif_dir, self.gif_map['idle'])
                    self.play_gif_with_time_limit(idle_path, duration if duration > 0 else 3600)
            except Queue.Empty:
                idle_path = os.path.join(self.gif_dir, self.gif_map['idle'])
                if os.path.exists(idle_path):
                    self.play_gif_with_time_limit(idle_path, 3600)
                else:
                    self.show_text("No idle.gif found!", duration=2)
            except Exception as e:
                logger.error(f"Error in GIF player loop: {e}")

    def show_text(self, text, font_size=18, duration=3.0):
        """Display text message on screen"""
        try:
            with canvas(self.device) as draw:
                draw.rectangle(self.device.bounding_box, outline="black", fill="black")
                lines = text.split('\n')
                line_height = font_size + 5
                total_height = len(lines) * line_height
                start_y = (self.display_height - total_height) // 2
                for i, line in enumerate(lines):
                    bbox = draw.textbbox((0, 0), line)
                    text_width = bbox[2] - bbox[0]
                    x = (self.display_width - text_width) // 2
                    y = start_y + (i * line_height)
                    draw.text((x, y), line, fill="white")
            time.sleep(duration)
        except Exception as e:
            logger.error(f"Error displaying text: {e}")

    def cleanup(self):
        """Clean up resources"""
        try:
            self.stop_event.set()
            if self.device:
                with canvas(self.device) as draw:
                    draw.rectangle(self.device.bounding_box, outline="black", fill="black")
            logger.info("Display cleaned up")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

def main():
    # Configuration
    CREDENTIALS_FILE = "credentials.json"
    SPREADSHEET_NAME = "lalacar"
    WORKSHEET_NAME = "Sheet1"
    GIF_DIR = "gifs"

    # GPIO Pin configurations (BCM numbering)
    MOTOR_A_PINS = [17, 27, 22]  # Left motor (BOARD 11, 13, 15)
    MOTOR_B_PINS = [18, 23, 12]  # Right motor (BOARD 12, 16, 32)
    FRONT_ULTRASONIC_PINS = [5, 6]  # Front Trigger=5, Echo=6 (BOARD 29, 31)
    REAR_ULTRASONIC_PINS = [13, 19]  # Rear Trigger=13, Echo=19 (BOARD 33, 35)

    # Set GPIO mode to BCM
    GPIO.setmode(GPIO.BCM)

    executor = None
    player = None
    robot_thread = None
    gif_thread = None
    action_queue = Queue()

    try:
        executor = CommandExecutor(
            CREDENTIALS_FILE,
            SPREADSHEET_NAME,
            WORKSHEET_NAME,
            MOTOR_A_PINS,
            MOTOR_B_PINS,
            FRONT_ULTRASONIC_PINS,
            REAR_ULTRASONIC_PINS,
            action_queue
        )
        player = PortraitGifPlayer(action_queue, GIF_DIR)

        # Start robot monitoring in a separate thread
        robot_thread = threading.Thread(target=executor.run, args=(1.0,))
        robot_thread.daemon = True
        robot_thread.start()

        # Start GIF player in a separate thread
        gif_thread = threading.Thread(target=player.run)
        gif_thread.daemon = True
        gif_thread.start()

        # Keep main thread alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        if player:
            player.cleanup()
        if executor:
            executor.robot.cleanup_gpio()
        GPIO.setwarnings(True)
        GPIO.cleanup()

if __name__ == "__main__":
    print("=== Integrated Robot Controller with Action-Specific GIF Player ===")
    print("Monitors Google Sheets for robot commands")
    print("Plays action-specific GIFs on Waveshare 3.5\" LCD from gifs/ directory")
    print("Press Ctrl+C to exit")
    print("=" * 50)
    main()
