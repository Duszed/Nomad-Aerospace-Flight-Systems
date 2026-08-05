# Nomad Aerospace — Flight Systems & Telemetry Repository

![License](https://img.shields.io/badge/License-Proprietary--Open%20Architecture-blue)
![Platform](https://img.shields.io/badge/Platform-ArduPilot%20%7C%20Cube%20Orange%2B-orange)
![Hardware](https://img.shields.io/badge/Hardware-30L%20Heavy--Lift%20UAV-green)
![Language](https://img.shields.io/badge/Language-C%2B%2B%20%7C%20Python%20%7C%20Param-brightgreen)

Welcome to the official core flight system documentation and custom telemetry firmware for **Nomad Aerospace** — Central Asia's premier Deep Tech agricultural UAV platform.

---

## 🚀 System Overview

Nomad Aerospace engineers 30-liter / 60kg heavy-lift autonomous UAVs designed specifically for high-efficiency precision agriculture, crop dusting, and localized environmental telemetry across Central Asia.

### Key Hardware & Avionics Specifications
* **Flight Controller:** Hex Cube Orange+ (Triple-Redundant IMU, Vibration Isolated)
* **Firmware Base:** ArduPilot Copter (Custom Parameter Architecture)
* **Propulsion:** 4x Hobbywing X11 G2 FOC Integrated Motors (14S DC)
* **Primary Navigation:** Dual-Antenna Here4 RTK GNSS 
* **Sensor Fusion Safety Shield:**
  * **Altimetry:** Benewake TF03 Long-Range Industrial LiDAR (UART/CAN)
  * **Omnidirectional Shield:** 360° Millimeter-Wave Radar Ring (77GHz)
* **Field Edge-Nodes:** ESP32-C3 (RISC-V) with Sensirion SHT4x I2C Industrial Sensors

---

## 🏗️ Hardware Ecosystem Architecture

```mermaid
graph TD
    A[Cube Orange+ Flight Controller] -->|MAVLink via 915MHz| B[Nomad Ground Control Station]
    C[Benewake TF03 LiDAR] -->|CAN Bus| A
    D[77GHz 360 Radar] -->|CAN Bus| A
    E[ESP32-C3 Sensirion Field Node] -->|LoRa / WiFi| B
    B -->|Encrypted JSON| F[Nomad Cloud Analytics]
```

📏 Hardware Blueprints & Airframe CAD
Airframe Chassis: EFT K30 (30-Liter Payload Capacity)
Motor-to-motor diagonal span: 1781mm | Operational footprint: 1.3 x 1.3 meters
![alt text](assets/k30_blueprint.png)
⚡ Propulsion Performance & Empirical Thrust Data
Hobbywing X11 G2 (14S DC) paired with 43x14 Folding Carbon-Polymer Propellers.
1. Empirical Dynamometer Thrust Curves (54V Nominal)
At our target hover weight of 15.5 kg/axis (60kg Total All-Up Weight), the propulsion system draws ~51 Amps at an efficiency of 7.8 g/W, leaving over 30kg/axis of peak thrust headroom for safety maneuvers.
![alt text](assets/thrust_graph_54v.png)
2. Integrated FOC Motor Mount & ESC Architecture
45.1mm carbon-tube clamp, integrated FOC ESC cooling housing, and 12AWG heavy-current power routing.
![alt text](assets/x11_motor_mount_cad.png)
3. 43-Inch Aerodynamic Propeller Geometry
Blade length: 1082mm | Pitch: 14 inches | Dual-bolt carbon hub mount
![alt text](assets/propeller_43inch_cad.png)
📂 Repository Structure
/config - Production ArduPilot parameter stacks for 30L heavy-lift airframes.
/telemetry - Ground gateway Python scripts and custom C++ edge-node firmware.
/missions - Autonomous MAVLink GPS waypoint grids for precision agriculture spraying.
🛠 Advanced Features & Implementations
1. Sensor Fusion & Failsafe Protocols
GPS-Loss Failsafe: Automatic switch to AltHold with active LiDAR terrain-following and Radar boundary hold.
Smart Battery Failsafe: Dual-stage voltage monitoring on high-voltage 14S systems.
Variable Rate Application (VRA): Pump speed modulation tied directly to ground speed and microclimate data.
2. Edge Telemetry Node (/telemetry)
Custom C++ firmware developed for ultra-low-power ESP32-C3 RISC-V microcontrollers. Utilizing Sensirion SHT4x industrial I2C sensors with onboard micro-heaters, the node burns off morning dew to ensure 100% accurate field climate data. This guarantees spraying operations only occur during optimal agronomic windows.
📄 Intellectual Property
Copyright © 2026 Nomad Aerospace. All rights reserved. Hardware parameter profiles and custom telemetry firmware are licensed strictly for Nomad Aerospace deployment and partners.
