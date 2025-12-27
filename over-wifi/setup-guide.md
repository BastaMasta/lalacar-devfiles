# Raspberry Pi Robot - Hotspot Setup Guide

## Overview
This guide will help you set up your Raspberry Pi as a WiFi hotspot, allowing direct control of your robot without internet or Google Sheets.

## Prerequisites
- Raspberry Pi with WiFi capability (Pi 3, 4, or Zero W)
- Raspbian/Raspberry Pi OS installed
- Your existing robot setup working

## Installation Steps

### 1. Install Required Python Packages

```bash
pip3 install flask flask-socketio python-socketio eventlet
```

### 2. Create Directory Structure

```bash
# In your robot project directory
mkdir templates
cd templates
# Place index.html here
cd ..
# Place web_controller.py in main directory
```

### 3. Set Up WiFi Hotspot

#### Method A: Using NetworkManager (Recommended for newer OS)

```bash
# Install NetworkManager if not present
sudo apt-get update
sudo apt-get install network-manager

# Create hotspot
sudo nmcli device wifi hotspot ssid RobotControl password robot1234

# Make it persistent (auto-start on boot)
sudo nmcli connection modify Hotspot connection.autoconnect yes
```

#### Method B: Using hostapd (Traditional Method)

```bash
# Install required packages
sudo apt-get update
sudo apt-get install hostapd dnsmasq

# Stop services while configuring
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq

# Configure dhcpcd
sudo nano /etc/dhcpcd.conf
```

Add at the end:
```
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
```

```bash
# Configure dnsmasq
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
sudo nano /etc/dnsmasq.conf
```

Add:
```
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
```

```bash
# Configure hostapd
sudo nano /etc/hostapd/hostapd.conf
```

Add:
```
interface=wlan0
driver=nl80211
ssid=RobotControl
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=robot1234
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
```

```bash
# Tell system where config is
sudo nano /etc/default/hostapd
```

Find and update:
```
DAEMON_CONF="/etc/hostapd/hostapd.conf"
```

```bash
# Enable and start services
sudo systemctl unmask hostapd
sudo systemctl enable hostapd
sudo systemctl start hostapd
sudo systemctl enable dnsmasq
sudo systemctl start dnsmasq

# Reboot
sudo reboot
```

### 4. Auto-Start Web Controller

Create systemd service:

```bash
sudo nano /etc/systemd/system/robot-controller.service
```

Add:
```ini
[Unit]
Description=Robot Web Controller
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/robot
ExecStart=/usr/bin/python3 /home/pi/robot/web_controller.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable robot-controller
sudo systemctl start robot-controller
```

## Usage

### 1. Connect to Robot
1. Power on your Raspberry Pi
2. Wait 30-60 seconds for it to boot
3. On your phone/laptop, look for WiFi network: **RobotControl**
4. Connect using password: **robot1234**

### 2. Access Control Interface
Open a web browser and navigate to:
- `http://192.168.4.1:5000` (IP address)
- OR `http://raspberrypi.local:5000` (hostname, may not work on all devices)

### 3. Control Your Robot
- Use arrow buttons for movement
- Adjust distance/angle with slider
- Press action buttons for special moves
- View sensor data in real-time
- Use keyboard shortcuts:
  - Arrow keys or WASD for movement
  - Spacebar for emergency stop

## Troubleshooting

### Hotspot Not Visible
```bash
# Check hostapd status
sudo systemctl status hostapd

# Check if wlan0 is up
ip addr show wlan0

# Restart services
sudo systemctl restart hostapd
sudo systemctl restart dnsmasq
```

### Can't Access Web Interface
```bash
# Check if service is running
sudo systemctl status robot-controller

# Check logs
sudo journalctl -u robot-controller -f

# Test manually
cd /home/pi/robot
python3 web_controller.py
```

### GPIO Permissions
```bash
# Add user to gpio group
sudo usermod -a -G gpio pi
sudo reboot
```

### Port Already in Use
```bash
# Check what's using port 5000
sudo lsof -i :5000

# Kill process if needed
sudo kill -9 <PID>
```

## Network Diagrams

### Old System (Google Sheets)
```
[Your Phone] -> Internet -> Google Sheets <- [Raspberry Pi polls every 1s]
                                            [Motor Controllers]
                                            [Display]
```

### New System (Direct Hotspot)
```
[Your Phone] -> WiFi Hotspot (RobotControl) -> [Raspberry Pi Web Server]
                                               [Real-time WebSocket]
                                               [Motor Controllers]
                                               [Display]
```

## Features Comparison

| Feature | Google Sheets | Web Hotspot |
|---------|--------------|-------------|
| Internet Required | Yes | No |
| Response Time | ~1-2 seconds | <100ms |
| Real-time Feedback | No | Yes |
| Sensor Display | No | Yes |
| Multiple Users | Via shared sheet | Multiple browsers |
| Keyboard Control | No | Yes |
| Works Anywhere | No (needs internet) | Yes |

## Security Notes

- Change default password in hostapd.conf
- For production, use stronger encryption
- Consider MAC address filtering for additional security
- The web interface has no authentication - add if needed

## Performance Tips

1. **Faster Response**: WebSockets provide instant communication
2. **Battery Life**: Hotspot uses more power than WiFi client mode
3. **Range**: Typical range is 30-50 feet indoors
4. **Multiple Devices**: Up to 10 devices can connect simultaneously

## Advanced: Dual Mode Setup

To switch between hotspot and normal WiFi:

```bash
# Create script: toggle_mode.sh
#!/bin/bash
if [ "$1" == "hotspot" ]; then
    sudo systemctl start hostapd
    sudo systemctl start dnsmasq
    echo "Hotspot mode enabled"
elif [ "$1" == "wifi" ]; then
    sudo systemctl stop hostapd
    sudo systemctl stop dnsmasq
    sudo systemctl restart dhcpcd
    echo "WiFi client mode enabled"
fi
```

## Next Steps

- Add authentication to web interface
- Implement video streaming from Pi camera
- Add autonomous navigation modes
- Create mobile app wrapper using WebView
- Add emergency stop button on physical robot

## Support

If you encounter issues:
1. Check system logs: `sudo journalctl -xe`
2. Verify GPIO connections match pin definitions
3. Test individual components separately
4. Check power supply is adequate (2.5A+ recommended)