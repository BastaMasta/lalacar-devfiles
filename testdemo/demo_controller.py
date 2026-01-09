#!/usr/bin/env python3
"""
Demo Robot Controller - No Hardware Required
Tests the web interface and wireless communication without GPIO
"""

import time
import logging
import random
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import threading
from queue import Queue
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('demo_controller.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'robot-demo-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Robot status tracking
robot_status = {
    'connected': True,
    'last_command': None,
    'executing': False,
    'error': None,
    'position': {'x': 0.0, 'y': 0.0, 'angle': 0.0},  # Virtual position
    'total_distance': 0.0,
    'total_rotations': 0.0,
    'command_count': 0
}

class SimulatedRobotController:
    """Simulates robot behavior without hardware"""
    
    def __init__(self):
        self.speed = 0.7  # meters per second
        self.rot_speed = 270  # degrees per second
        self.position = {'x': 0.0, 'y': 0.0, 'angle': 0.0}
        self.obstacle_threshold = 20.0
        self.total_distance = 0.0
        self.total_rotations = 0.0
        
    def measure_distance(self, sensor='front'):
        """Simulate distance measurement (random values)"""
        # Simulate realistic distance readings
        base_distance = random.uniform(25, 200)
        # Occasionally simulate obstacles
        if random.random() < 0.05:  # 5% chance of obstacle
            return random.uniform(5, 15)
        return base_distance
    
    def check_obstacle(self, sensor='front'):
        """Simulate obstacle detection"""
        distance = self.measure_distance(sensor)
        return distance < self.obstacle_threshold
    
    def move_forward(self, distance=1.0):
        """Simulate forward movement"""
        logger.info(f"🚗 Moving forward {distance}m")
        duration = distance / self.speed
        
        # Simulate movement with time delay
        steps = 10
        for i in range(steps):
            time.sleep(duration / steps)
            
            # Check for simulated obstacles
            if i > 2 and self.check_obstacle('front'):
                logger.warning("⚠️ Obstacle detected! Stopping.")
                socketio.emit('obstacle_detected', {'sensor': 'front', 'distance': self.measure_distance('front')})
                return False
            
            # Update virtual position
            step_distance = distance / steps
            import math
            angle_rad = math.radians(self.position['angle'])
            self.position['x'] += step_distance * math.cos(angle_rad)
            self.position['y'] += step_distance * math.sin(angle_rad)
            self.total_distance += step_distance
            
            # Send position update
            socketio.emit('position_update', self.position)
        
        return True
    
    def move_backward(self, distance=1.0):
        """Simulate backward movement"""
        logger.info(f"🔙 Moving backward {distance}m")
        duration = distance / self.speed
        
        steps = 10
        for i in range(steps):
            time.sleep(duration / steps)
            
            if i > 2 and self.check_obstacle('rear'):
                logger.warning("⚠️ Rear obstacle detected! Stopping.")
                socketio.emit('obstacle_detected', {'sensor': 'rear', 'distance': self.measure_distance('rear')})
                return False
            
            # Update virtual position (moving backward)
            step_distance = distance / steps
            import math
            angle_rad = math.radians(self.position['angle'])
            self.position['x'] -= step_distance * math.cos(angle_rad)
            self.position['y'] -= step_distance * math.sin(angle_rad)
            self.total_distance += step_distance
            
            socketio.emit('position_update', self.position)
        
        return True
    
    def turn(self, angle):
        """Simulate turning"""
        direction = "left" if angle < 0 else "right"
        logger.info(f"🔄 Turning {direction} {abs(angle)}°")
        
        duration = abs(angle) / self.rot_speed
        
        steps = 10
        for i in range(steps):
            time.sleep(duration / steps)
            
            # Update virtual angle
            self.position['angle'] += angle / steps
            self.position['angle'] = self.position['angle'] % 360
            self.total_rotations += abs(angle / steps)
            
            socketio.emit('position_update', self.position)
        
        return True
    
    def dance(self):
        """Simulate dance routine"""
        logger.info("💃 Dancing!")
        success = self.move_forward(0.5)
        if not success:
            return False
        time.sleep(0.2)
        
        success = self.move_backward(0.5)
        if not success:
            return False
        time.sleep(0.2)
        
        self.turn(-45)
        time.sleep(0.2)
        self.turn(90)
        time.sleep(0.2)
        self.turn(-45)
        return True
    
    def say_hi(self):
        """Simulate greeting routine"""
        logger.info("👋 Saying hi!")
        self.turn(-30)
        time.sleep(0.3)
        self.turn(60)
        time.sleep(0.3)
        self.turn(-30)
        return True
    
    def emergency_stop(self):
        """Simulate emergency stop"""
        logger.warning("🛑 Emergency stop!")
        return True

class DemoCommandExecutor:
    """Handles command execution for demo"""
    
    def __init__(self, robot_controller):
        self.robot = robot_controller
        self.executing = False
        
    def execute_command(self, command_type, value=1.0):
        """Execute a robot command"""
        global robot_status
        
        if self.executing:
            return {'success': False, 'message': 'Robot is currently executing a command'}
        
        self.executing = True
        robot_status['executing'] = True
        robot_status['last_command'] = command_type
        robot_status['command_count'] += 1
        
        try:
            success = False
            
            if command_type == 'forward':
                success = self.robot.move_forward(value)
                    
            elif command_type == 'backward':
                success = self.robot.move_backward(value)
                    
            elif command_type == 'left':
                success = self.robot.turn(-value)
                
            elif command_type == 'right':
                success = self.robot.turn(value)
                
            elif command_type == 'dance':
                success = self.robot.dance()
                    
            elif command_type == 'hi':
                success = self.robot.say_hi()
                
            elif command_type == 'stop':
                success = self.robot.emergency_stop()
            
            # Update status
            robot_status['executing'] = False
            robot_status['error'] = None if success else 'Command failed or obstacle detected'
            robot_status['position'] = self.robot.position
            robot_status['total_distance'] = round(self.robot.total_distance, 2)
            robot_status['total_rotations'] = round(self.robot.total_rotations, 1)
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

# Initialize robot and executor
robot = SimulatedRobotController()
command_executor = DemoCommandExecutor(robot)

@app.route('/')
def index():
    """Serve the main control page"""
    return render_template('demo.html')

@app.route('/api/status')
def get_status():
    """Get current robot status"""
    return jsonify(robot_status)

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('✅ Client connected')
    emit('status_update', robot_status)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('❌ Client disconnected')

@socketio.on('command')
def handle_command(data):
    """Handle command from web interface"""
    logger.info(f"📨 Received command: {data}")
    
    command_type = data.get('type')
    value = float(data.get('value', 1.0))
    
    # Execute command in separate thread to avoid blocking
    def execute():
        result = command_executor.execute_command(command_type, value)
        socketio.emit('command_result', result)
    
    thread = threading.Thread(target=execute)
    thread.daemon = True
    thread.start()

@socketio.on('get_distance')
def handle_get_distance(data):
    """Get distance from simulated ultrasonic sensor"""
    sensor_type = data.get('sensor', 'front')
    distance = robot.measure_distance(sensor_type)
    emit('distance_update', {'sensor': sensor_type, 'distance': distance})

@socketio.on('reset_position')
def handle_reset_position():
    """Reset virtual position"""
    robot.position = {'x': 0.0, 'y': 0.0, 'angle': 0.0}
    robot.total_distance = 0.0
    robot.total_rotations = 0.0
    robot_status['position'] = robot.position
    robot_status['total_distance'] = 0.0
    robot_status['total_rotations'] = 0.0
    emit('status_update', robot_status)
    logger.info("🔄 Position reset")

def main():
    """Main function"""
    print("=" * 70)
    print("🤖 DEMO Robot Controller - No Hardware Required")
    print("=" * 70)
    print()
    print("This is a DEMO version that simulates robot behavior")
    print("No GPIO connections needed!")
    print()
    print("🌐 Starting server...")
    print()
    print("Access the control interface at:")
    print("  • http://localhost:5000 (on this computer)")
    print("  • http://YOUR_PI_IP:5000 (from other devices)")
    print()
    print("Features:")
    print("  ✓ Simulated movement with realistic timing")
    print("  ✓ Random obstacle detection")
    print("  ✓ Virtual position tracking")
    print("  ✓ Real-time sensor readings")
    print("  ✓ Statistics tracking")
    print()
    print("Press Ctrl+C to exit")
    print("=" * 70)
    print()
    
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("\n👋 Shutting down demo...")
    except Exception as e:
        logger.error(f"❌ Error: {e}")

if __name__ == '__main__':
    main()
