#!/usr/bin/env python3
"""
Web-Based Robot Controller with Hotspot Support
Serves a web interface for direct robot control without Google Sheets
"""

import time
import logging
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import threading
from queue import Queue
import json
import RPi.GPIO as GPIO

# Import robot components from main.py
from main import RobotController, PortraitGifPlayer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('web_controller.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'robot-control-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
robot = None
action_queue = None
command_queue = Queue()
robot_status = {
    'connected': False,
    'last_command': None,
    'executing': False,
    'error': None
}

# GPIO Pin configurations (BCM numbering)
MOTOR_A_PINS = [17, 27, 22]
MOTOR_B_PINS = [18, 23, 12]
FRONT_ULTRASONIC_PINS = [5, 6]
REAR_ULTRASONIC_PINS = [13, 19]

class WebCommandExecutor:
    """Handles command execution from web interface"""
    
    def __init__(self, robot_controller, action_queue):
        self.robot = robot_controller
        self.action_queue = action_queue
        self.executing = False
        
    def execute_command(self, command_type, value=1.0):
        """Execute a robot command"""
        global robot_status
        
        if self.executing:
            return {'success': False, 'message': 'Robot is currently executing a command'}
        
        self.executing = True
        robot_status['executing'] = True
        robot_status['last_command'] = command_type
        
        try:
            success = False
            
            if command_type == 'forward':
                self.action_queue.put(('forward', value / self.robot.speed))
                success = self.robot.move_forward_with_obstacle_detection(value)
                if self.robot.robot_stopped:
                    self.action_queue.put(('obstacle', 2.0))
                    
            elif command_type == 'backward':
                self.action_queue.put(('backward', value / self.robot.speed))
                success = self.robot.move_backward(value)
                if self.robot.robot_stopped:
                    self.action_queue.put(('obstacle', 2.0))
                    
            elif command_type == 'left':
                self.action_queue.put(('left', abs(value) / self.robot.rot_speed))
                success = self.robot.turn(-value)
                
            elif command_type == 'right':
                self.action_queue.put(('right', abs(value) / self.robot.rot_speed))
                success = self.robot.turn(value)
                
            elif command_type == 'dance':
                self.action_queue.put(('dance', 3.0))
                success = self.robot.move_forward_with_obstacle_detection(0.5)
                if success:
                    time.sleep(0.2)
                    success = self.robot.move_backward(0.5)
                if success:
                    time.sleep(0.2)
                    self.robot.turn(-45)
                    time.sleep(0.2)
                    self.robot.turn(90)
                    time.sleep(0.2)
                    self.robot.turn(-45)
                if not success:
                    self.action_queue.put(('obstacle', 2.0))
                    
            elif command_type == 'hi':
                self.action_queue.put(('hi', 1.5))
                self.robot.turn(-30)
                time.sleep(0.3)
                self.robot.turn(60)
                time.sleep(0.3)
                self.robot.turn(-30)
                success = True
                
            elif command_type == 'stop':
                self.action_queue.put(('obstacle', 2.0))
                self.robot.emergency_stop_forward()
                success = True
            
            robot_status['executing'] = False
            robot_status['error'] = None if success else 'Command failed or obstacle detected'
            self.executing = False
            
            # Emit status update to all connected clients
            socketio.emit('status_update', robot_status)
            
            return {
                'success': success,
                'message': 'Command executed successfully' if success else 'Command failed or obstacle detected'
            }
            
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            robot_status['executing'] = False
            robot_status['error'] = str(e)
            self.executing = False
            socketio.emit('status_update', robot_status)
            return {'success': False, 'message': str(e)}

# Command executor instance (initialized later)
command_executor = None

@app.route('/')
def index():
    """Serve the main control page"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """Get current robot status"""
    return jsonify(robot_status)

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    emit('status_update', robot_status)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')

@socketio.on('command')
def handle_command(data):
    """Handle command from web interface"""
    logger.info(f"Received command: {data}")
    
    command_type = data.get('type')
    value = float(data.get('value', 1.0))
    
    if not command_executor:
        emit('command_result', {'success': False, 'message': 'Robot not initialized'})
        return
    
    # Execute command in separate thread to avoid blocking
    def execute():
        result = command_executor.execute_command(command_type, value)
        socketio.emit('command_result', result)
    
    thread = threading.Thread(target=execute)
    thread.daemon = True
    thread.start()

@socketio.on('get_distance')
def handle_get_distance(data):
    """Get distance from ultrasonic sensor"""
    sensor_type = data.get('sensor', 'front')
    
    if not robot:
        emit('distance_update', {'error': 'Robot not initialized'})
        return
    
    if sensor_type == 'front':
        distance = robot.measure_distance(robot.FRONT_USENSE)
    else:
        distance = robot.measure_distance(robot.REAR_USENSE)
    
    emit('distance_update', {'sensor': sensor_type, 'distance': distance})

def command_processor():
    """Background thread to process commands from queue"""
    while True:
        try:
            if not command_queue.empty():
                command_data = command_queue.get()
                # Process command if needed
                pass
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in command processor: {e}")

def initialize_robot():
    """Initialize robot and display"""
    global robot, action_queue, command_executor, robot_status
    
    try:
        # Set GPIO mode
        GPIO.setmode(GPIO.BCM)
        
        # Create action queue for GIF player
        action_queue = Queue()
        
        # Initialize robot controller
        robot = RobotController(
            MOTOR_A_PINS,
            MOTOR_B_PINS,
            FRONT_ULTRASONIC_PINS,
            REAR_ULTRASONIC_PINS
        )
        
        # Initialize command executor
        command_executor = WebCommandExecutor(robot, action_queue)
        
        # Initialize GIF player
        player = PortraitGifPlayer(action_queue, "gifs")
        gif_thread = threading.Thread(target=player.run)
        gif_thread.daemon = True
        gif_thread.start()
        
        robot_status['connected'] = True
        logger.info("Robot initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize robot: {e}")
        robot_status['connected'] = False
        robot_status['error'] = str(e)

def main():
    """Main function"""
    print("=" * 60)
    print("Web-Based Robot Controller")
    print("Starting server on http://0.0.0.0:5000")
    print("Connect to the Raspberry Pi hotspot and navigate to:")
    print("http://raspberrypi.local:5000 or http://192.168.4.1:5000")
    print("=" * 60)
    
    # Initialize robot
    initialize_robot()
    
    # Start command processor thread
    processor_thread = threading.Thread(target=command_processor)
    processor_thread.daemon = True
    processor_thread.start()
    
    # Start Flask server
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if robot:
            robot.cleanup_gpio()
        GPIO.cleanup()

if __name__ == '__main__':
    main()