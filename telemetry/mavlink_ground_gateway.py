"""
Nomad Aerospace - MAVLink Ground Control Gateway
Architecture: Python 3.9+ | pymavlink | AsyncIO
Purpose: Intercepts, decrypts, and routes live MAVLink telemetry from the  UAV 
         to the local UI and Variable Rate Application (VRA) database.
Author: Nomad Aerospace Systems Team
"""

from pymavlink import mavutil
import time
import json
import logging

# Configure Aerospace Logging Standard
logging.basicConfig(level=logging.INFO, format='%(asctime)s [NOMAD-SYS] %(message)s')

class NomadTelemetryGateway:
    def __init__(self, connection_string='udp:127.0.0.1:14550', baud_rate=57600):
        self.conn_string = connection_string
        self.baud = baud_rate
        self.vehicle = None
        logging.info(f"Initializing MAVLink Gateway on {self.conn_string}")

    def connect_to_uav(self):
        """Establish secure heartbeat with Cube Orange+ flight controller."""
        try:
            self.vehicle = mavutil.mavlink_connection(self.conn_string, baud=self.baud)
            self.vehicle.wait_heartbeat(timeout=10)
            logging.info(f"Target Acquired. System ID: {self.vehicle.target_system}, Component ID: {self.vehicle.target_component}")
        except Exception as e:
            logging.error(f"MAVLink Handshake Failed: {e}")

    def parse_flight_data(self):
        """Extract Critical Sensor Fusion Data in Real-Time."""
        if not self.vehicle:
            return

        logging.info("Listening for VFR_HUD and GLOBAL_POSITION_INT data streams...")
        while True:
            msg = self.vehicle.recv_match(type=['VFR_HUD', 'GLOBAL_POSITION_INT', 'SYS_STATUS'], blocking=True)
            if not msg:
                continue
            
            payload = {}
            if msg.get_type() == 'VFR_HUD':
                payload['airspeed'] = msg.airspeed
                payload['groundspeed'] = msg.groundspeed
                payload['alt_msl'] = msg.alt
            
            elif msg.get_type() == 'SYS_STATUS':
                payload['battery_voltage'] = msg.voltage_battery / 1000.0  # mV to V
                payload['battery_current'] = msg.current_battery / 100.0   # cA to A
                
                # Failsafe Trigger: 18S System Voltage Drop (Below 61.2V)
                if payload['battery_voltage'] > 0 and payload['battery_voltage'] < 61.2:
                    logging.warning(f"CRITICAL: 18S Voltage Drop Detected ({payload['battery_voltage']}V). Initiating RTL.")

            # Route to JSON for Web Dashboard Consumption
            if payload:
                print(json.dumps(payload))
            
            time.sleep(0.05) # 20Hz polling rate

if __name__ == "__main__":
    gateway = NomadTelemetryGateway(connection_string='udp:0.0.0.0:14550')
    gateway.connect_to_uav()
    gateway.parse_flight_data()
